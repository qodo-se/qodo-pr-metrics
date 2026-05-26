"""user_impact.py — supplementary per-user impact report.

Generated alongside the org-wide report from the same per-PR row data.
Adds an interactive date-range slider that recomputes the headline,
at-a-glance panel, and per-developer table on the client.

Public API:
    generate_user_html(rows, org, since, until, logo_path=None) -> str

Integration (in github.py, right after writing the org report HTML):

    from user_impact import generate_user_html

    user_html_path = output_path.with_name(output_path.stem + "_user.html")
    user_html_path.write_text(
        generate_user_html(rows, org, since, until, logo_path=logo_path),
        encoding="utf-8",
    )
    print(f"  Wrote {user_html_path}", file=sys.stderr)

SCOPE NOTE — Total PRs vs. Qodo-reviewed PRs:

    Only PRs present in `rows` are counted. In the current github.py
    pipeline, `rows` contains PRs that had a Qodo review comment, so
    "Total PRs" in this report = "Qodo-reviewed PRs by this user".

    To report TRUE total PRs (Qodo or not) per author, augment github.py
    to also fetch unreviewed PRs (drop the
    `'Code Review by Qodo' in:comments` qualifier from the search) and
    set `Has Qodo Review` False on those rows. This module already
    reads `Has Qodo Review` correctly; the only change needed is in the
    row producer.

The HTML is self-contained: inlined CSS + JS, embedded JSON data,
no external runtime dependencies beyond Google Fonts (which falls
back to system fonts if offline).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape as _h
from pathlib import Path
from typing import Optional


# ─── data layer ────────────────────────────────────────────────────────

@dataclass
class UserImpactData:
    users: list = field(default_factory=list)
    pr_records: list = field(default_factory=list)
    window: dict = field(default_factory=dict)


def _parse_iso_date(value: str) -> Optional[date]:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' or 'YYYY-MM-DD'. Returns None on failure."""
    if not value:
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def aggregate_user_impact(rows: list, since: str, until: str) -> UserImpactData:
    """Aggregate per-user metrics + per-PR records.

    Args:
        rows: list of row dicts (same format report.aggregate consumes).
        since: 'YYYY-MM-DD' window start, inclusive.
        until: 'YYYY-MM-DD' window end, inclusive.

    Returns UserImpactData with:
        users      — one entry per author, totalled over the full window.
                     Sorted by AR-acceptance-rate desc, then PRs desc.
        pr_records — one entry per PR, used by the slider to recompute
                     on the client. Each carries the day offset within
                     the window plus the columns needed for aggregation.
        window     — {start, end, total_days}.
    """
    start = _parse_iso_date(since)
    end = _parse_iso_date(until)
    if not start or not end:
        return UserImpactData(window={"start": since, "end": until, "total_days": 1})
    total_days = (end - start).days + 1

    user_acc: dict = defaultdict(lambda: {
        "login": "", "name": "",
        "prs": 0, "reviewed": 0,
        "locTotal": 0, "locReviewed": 0,
        "arSugg": 0, "arAcc": 0,
    })
    pr_records = []

    for r in rows:
        user = r.get("PR Creator")
        if not user:
            continue
        created = _parse_iso_date(r.get("PR Creation Date", ""))
        if not created:
            continue
        day_offset = (created - start).days
        if day_offset < 0 or day_offset >= total_days:
            continue

        reviewed = bool(r.get("Has Qodo Review", True))
        loc = int(r.get("Lines Added", 0) or 0)
        ar_sug = int(r.get("Action Required Suggestions", 0) or 0)
        ar_imp = int(r.get("Action Required Implemented", 0) or 0)

        u = user_acc[user]
        u["login"] = user
        u["name"] = _humanize_login(user)
        u["prs"] += 1
        u["locTotal"] += loc
        if reviewed:
            u["reviewed"] += 1
            u["locReviewed"] += loc
            u["arSugg"] += ar_sug
            u["arAcc"] += ar_imp

        pr_records.append({
            "user": user,
            "dayOffset": day_offset,
            "reviewed": 1 if reviewed else 0,
            "locTotal": loc,
            "locReviewed": loc if reviewed else 0,
            "arSugg": ar_sug if reviewed else 0,
            "arAcc": ar_imp if reviewed else 0,
        })

    def _rank(u: dict) -> tuple:
        rate = u["arAcc"] / u["arSugg"] if u["arSugg"] else -1
        return (rate, u["prs"])

    users_sorted = sorted(user_acc.values(), key=_rank, reverse=True)

    return UserImpactData(
        users=users_sorted,
        pr_records=pr_records,
        window={"start": since, "end": until, "total_days": total_days},
    )


def _humanize_login(login: str) -> str:
    """'ofer-sabo' -> 'Ofer Sabo'. Falls back to the login on weird input."""
    if not login:
        return ""
    parts = [p for p in re.split(r"[-_.]+", login) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or login


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"[-_\s.]+", name.strip()) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name[:2] or "—").upper()


def _embed_logo(logo_path: Optional[str], height: int = 32) -> str:
    """Inline an SVG or raster logo into HTML. Empty string on failure.

    Kept local (not imported from report.py) so this module is fully standalone.
    """
    if not logo_path:
        return ""
    p = Path(logo_path)
    if not p.exists():
        return ""
    if p.suffix.lower() == ".svg":
        text = p.read_text(encoding="utf-8")
        text = re.sub(r"<\?xml[^?]*\?>", "", text).strip()
        text = re.sub(r'\s+width="[^"]*"', "", text, count=1)
        text = re.sub(r'\s+height="[^"]*"', "", text, count=1)
        text = re.sub(r"(<svg\b)", rf'\1 height="{height}"', text, count=1)
        return text
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<img src="data:{mime};base64,{b64}" alt="Qodo" height="{height}">'


def _human_date(iso: str) -> str:
    d = _parse_iso_date(iso)
    return d.strftime("%b %d") if d else iso


# ─── CSS ───────────────────────────────────────────────────────────────

_CSS = r"""<style>
:root {
  --pm-100:#F4F4F4; --pm-200:#DFDFDF; --pm-400:#6E6E6E; --pm-500:#3D3D3D;
  --pm-700:#1C1C1C; --pm-750:#171518; --pm-800:#141414;
  --p-200:#A8A1FD; --p-300:#9084FC; --p-400:#7968FA; --p-500:#634FD1;
  --p-tint-10:rgba(174,161,241,0.10); --p-tint-20:rgba(174,161,241,0.20);
  --success:#57E3C0; --danger:#E5484D; --warning:#F5B544; --g-mint-bright:#06E4AE;
  --bg-canvas:var(--pm-750); --bg-surface:var(--pm-700); --bg-inset:var(--pm-800);
  --border-default:#2C2C2C; --border-subtle:rgba(255,255,255,0.06);
  --fg-default:var(--pm-200); --fg-strong:var(--pm-100); --fg-muted:#A09CB6; --fg-subtle:var(--pm-400);
  --brand-gradient:linear-gradient(135deg,#684BFE 0%,#06E4AE 100%);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: var(--bg-canvas); color: var(--fg-default);
             font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
             font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
.mono, .lb-mono { font-family: 'IBM Plex Mono', ui-monospace, Menlo, monospace; }
.page-wrap { width: 100%; min-height: 100vh; display: flex; justify-content: center; padding: 32px 16px; }
.lb { width: 1200px; max-width: 100%; background: var(--bg-canvas);
      border-radius: 14px; overflow: clip;
      box-shadow: 0 1px 0 var(--border-default), 0 24px 64px rgba(0,0,0,.4); }

/* Header bar */
.lb-bar { display: grid; grid-template-columns: auto 1fr auto; align-items: center;
          padding: 22px 36px; gap: 28px; border-bottom: 1px solid var(--border-default);
          background: var(--bg-inset); }
.lb-bar .brand { display: flex; align-items: center; gap: 14px; }
.lb-bar .brand svg, .lb-bar .brand img { height: 32px; width: auto; display: block; }
.lb-bar .brand .sep { width: 1px; height: 20px; background: var(--border-default); margin: 0 2px; }
.lb-bar .brand .label { font-size: 13px; color: var(--fg-muted); }
.lb-bar .crumbs { font-size: 12px; color: var(--fg-muted); display: flex; align-items: center; gap: 10px; justify-content: center; }
.lb-bar .crumbs .pill { background: var(--p-tint-10); border: 1px solid var(--p-tint-20);
                        color: var(--p-300); padding: 4px 10px; border-radius: 999px;
                        font-size: 11px; font-weight: 600; letter-spacing: .04em; }
.lb-bar .rng { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--fg-default);
               padding: 6px 12px; background: var(--bg-surface); border: 1px solid var(--border-default);
               border-radius: 6px; }

/* Hero */
.lb-hero { padding: 48px 36px 44px; border-bottom: 1px solid var(--border-default);
           display: grid; grid-template-columns: 1.4fr 1fr; gap: 48px; align-items: start; }
.lb-hero h1 { font-size: 30px; line-height: 1.22; font-weight: 600; color: var(--fg-strong);
              letter-spacing: -.01em; text-wrap: balance; }
.lb-hero h1 .accent { color: var(--p-300); font-weight: 700; }
.lb-hero h1 .accent.success { color: var(--success); }
.lb-hero h1 .accent.zero { color: var(--fg-muted); font-weight: 500; }
.lb-hero .tldr { padding: 20px 22px; background: var(--bg-surface); border: 1px solid var(--p-tint-20);
                 border-radius: 12px; border-left: 3px solid var(--p-400); }
.lb-hero .tldr-label { font-size: 10.5px; font-weight: 700; letter-spacing: .12em;
                       text-transform: uppercase; color: var(--p-300); margin-bottom: 12px;
                       display: flex; justify-content: space-between; align-items: baseline; }
.lb-hero .tldr-label .lowsamp { font-size: 9.5px; color: var(--warning); letter-spacing: .08em;
                                background: rgba(245,181,68,.12); padding: 2px 6px; border-radius: 4px; }
.lb-hero .tldr-label .lowsamp[hidden] { display: none !important; }
.lb-hero .tldr-row { display: flex; justify-content: space-between; align-items: baseline; padding: 7px 0;
                     border-bottom: 1px dashed rgba(255,255,255,.06); font-size: 12.5px; }
.lb-hero .tldr-row:last-child { border-bottom: none; }
.lb-hero .tldr-row .l { color: var(--fg-muted); }
.lb-hero .tldr-row .v { color: var(--fg-strong); font-weight: 500;
                        font-family: 'IBM Plex Mono', monospace; white-space: nowrap; }
.lb-hero .tldr-row.headline { padding-bottom: 10px; }
.lb-hero .tldr-row.headline .v { font-size: 15px; color: var(--success); font-weight: 600; }
.lb-hero .tldr-row.headline.zero .v { color: var(--fg-muted); }

/* Section */
.lb-section { padding: 0 0 40px; border-bottom: 1px solid var(--border-default); }
.lb-section > .lb-sec-head, .lb-section > .lb-board { margin-left: 36px; margin-right: 36px; }
.lb-section:last-child { border-bottom: none; }
.lb-sec-head { display: flex; align-items: end; justify-content: space-between;
               margin-top: 36px; margin-bottom: 24px; gap: 24px; }
.lb-sec-title { font-size: 20px; font-weight: 600; color: var(--fg-strong); letter-spacing: -.005em; }

/* Sticky control bar */
.lb-ctrl { padding: 16px 36px 20px; border-top: 1px solid var(--border-default);
           border-bottom: 1px solid var(--border-default);
           background: rgba(20, 20, 20, .92); backdrop-filter: blur(8px);
           -webkit-backdrop-filter: blur(8px);
           display: flex; align-items: center; gap: 24px;
           position: sticky; top: 0; z-index: 20; }
.lb-ctrl-label { font-size: 10.5px; font-weight: 700; letter-spacing: .14em;
                 text-transform: uppercase; color: var(--p-300); white-space: nowrap; }
.lb-ctrl-actions { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.lb-reset { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--fg-muted);
            background: var(--bg-surface); border: 1px solid var(--border-default);
            border-radius: 6px; padding: 6px 10px; cursor: pointer; transition: all 120ms ease-out; }
.lb-reset:hover { color: var(--fg-strong); border-color: var(--p-tint-20); background: var(--p-tint-10); }
.lb-reset:disabled { opacity: .4; cursor: not-allowed; }
.lb-reset:disabled:hover { color: var(--fg-muted); border-color: var(--border-default); background: var(--bg-surface); }

/* Date range slider */
.dr { flex: 1; min-width: 0; }
.dr-row { display: flex; align-items: center; gap: 14px; }
.dr-end { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--fg-subtle);
          white-space: nowrap; min-width: 56px; }
.dr-end:last-of-type { text-align: right; }
.dr-track-wrap { position: relative; flex: 1; height: 32px; }
.dr-track { position: absolute; top: 50%; transform: translateY(-50%); left: 0; right: 0;
            height: 4px; background: var(--bg-inset); border-radius: 999px; }
.dr-tick { position: absolute; top: 50%; transform: translate(-50%, -50%);
           width: 2px; height: 8px; background: var(--border-default);
           border-radius: 1px; pointer-events: none; }
.dr-fill { position: absolute; top: 50%; transform: translateY(-50%);
           height: 4px; background: var(--p-400); border-radius: 999px;
           pointer-events: none; box-shadow: 0 0 12px rgba(121,104,250,.5); }
.dr-input { position: absolute; top: 0; left: 0; width: 100%; height: 32px;
            -webkit-appearance: none; appearance: none;
            background: transparent; pointer-events: none; margin: 0; padding: 0; }
.dr-input:focus { outline: none; }
.dr-input::-webkit-slider-runnable-track { background: transparent; border: 0; }
.dr-input::-moz-range-track { background: transparent; border: 0; }
.dr-input::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 18px; height: 18px; border-radius: 50%;
  background: #fff; border: 2px solid var(--p-400);
  box-shadow: 0 1px 4px rgba(0,0,0,.4);
  cursor: grab; pointer-events: auto;
}
.dr-input::-moz-range-thumb {
  width: 18px; height: 18px; border-radius: 50%; box-sizing: border-box;
  background: #fff; border: 2px solid var(--p-400);
  box-shadow: 0 1px 4px rgba(0,0,0,.4);
  cursor: grab; pointer-events: auto;
}
.dr-bubble { position: absolute; top: -2px; transform: translate(-50%, -100%);
             font-family: 'IBM Plex Mono', monospace; font-size: 11px;
             font-weight: 500; color: var(--fg-strong); background: var(--bg-surface);
             border: 1px solid var(--p-tint-20); border-radius: 6px;
             padding: 3px 8px; white-space: nowrap; pointer-events: none;
             box-shadow: 0 2px 8px rgba(0,0,0,.3); }
.dr-meta { margin-top: 8px; font-size: 11px; color: var(--fg-muted); text-align: center; }
.dr-days { font-family: 'IBM Plex Mono', monospace; }

/* Leaderboard */
.lb-board { background: var(--bg-surface); border: 1px solid var(--border-default);
            border-radius: 12px; overflow: hidden; }
.lb-board table { width: 100%; border-collapse: collapse; font-size: 13px; }
.lb-board th { text-align: left; padding: 12px 16px; font-size: 10.5px; font-weight: 700;
               letter-spacing: .1em; text-transform: uppercase; color: var(--fg-muted);
               background: var(--bg-inset); border-bottom: 1px solid var(--border-default);
               white-space: nowrap; }
.lb-board th.num { text-align: right; }
.lb-board td { padding: 14px 16px; border-bottom: 1px solid var(--border-subtle);
               transition: opacity 80ms ease-out; }
.lb-board tr:last-child td { border-bottom: none; }
.lb-board td.num { text-align: right; font-family: 'IBM Plex Mono', monospace; color: var(--fg-default); }
.lb-board td.num.strong { color: var(--fg-strong); font-weight: 500; }
.lb-board .dev { display: flex; align-items: center; gap: 10px; }
.lb-board .avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--brand-gradient);
                    font-size: 10.5px; font-weight: 600; color: #fff;
                    display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.lb-board .dev-name { color: var(--fg-strong); font-weight: 500; font-size: 13px; line-height: 1.2; }
.lb-board .dev-handle { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--fg-muted); }
.lb-board .meter { display: inline-flex; align-items: center; gap: 8px; justify-content: flex-end; }
.lb-board .meter .bar { position: relative; width: 64px; height: 5px;
                        background: var(--bg-inset); border-radius: 999px; overflow: hidden; }
.lb-board .meter .bar i { display: block; height: 100%; background: var(--success);
                          border-radius: 999px; transition: width 120ms ease-out; }
.lb-board .meter .pct { color: var(--fg-strong); font-weight: 500; min-width: 36px;
                        text-align: right; font-family: 'IBM Plex Mono', monospace; }
.lb-board .ar-frac { font-size: 13px; color: var(--fg-muted); font-family: 'IBM Plex Mono', monospace; }
.lb-board .ar-frac .acc { color: var(--success); font-weight: 500; }

/* Grouped column headers (total vs Qodo-reviewed) */
.lb-board th.grp { background: var(--bg-inset); color: var(--p-300); text-align: center;
                   padding-top: 10px; padding-bottom: 6px; font-size: 10px;
                   letter-spacing: .14em; border-bottom: 1px solid var(--p-tint-20); }
.lb-board th.grp.qodo { background: rgba(121,104,250,.10); }
.lb-board th.sub { padding-top: 6px; padding-bottom: 12px; font-size: 9.5px; }
.lb-board td.col-q { background: rgba(121,104,250,.04); }
.lb-board td.col-q.left { box-shadow: inset 1px 0 0 var(--p-tint-20); }
.lb-board td.col-q.right { box-shadow: inset -1px 0 0 var(--p-tint-20); }

/* Inactive row */
.lb-board tr.inactive td { opacity: .42; }
.lb-board tr.inactive .dev-name, .lb-board tr.inactive .dev-handle { color: var(--fg-subtle); }
.lb-board tr.inactive .avatar { filter: saturate(.2); }
.inactive-tag { display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: 10px;
                color: var(--fg-subtle); background: var(--bg-inset);
                border: 1px solid var(--border-default);
                padding: 1px 6px; border-radius: 4px; margin-left: 8px;
                letter-spacing: .04em; text-transform: uppercase; font-weight: 600; }

/* Footer */
.lb-foot { padding: 18px 36px; background: var(--bg-inset);
           display: flex; align-items: center; justify-content: space-between;
           font-size: 11px; color: var(--fg-muted); }
.lb-foot .l { display: flex; align-items: center; gap: 12px; }
.lb-foot .l svg, .lb-foot .l img { height: 18px; width: auto; display: block; }
</style>"""


# ─── JS (vanilla, no React) ────────────────────────────────────────────

_JS = r"""<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var RECORDS  = JSON.parse($('ui-records').textContent);
  var BASELINE = JSON.parse($('ui-baseline').textContent);
  var WINDOW   = JSON.parse($('ui-window').textContent);
  var TOTAL_DAYS = WINDOW.total_days;
  // Parse window start as local date (avoid UTC offset shifting day 0).
  var parts = WINDOW.start.split('-').map(Number);
  var START_DATE = new Date(parts[0], parts[1] - 1, parts[2]);
  var DAY_MS = 86400000;

  function dayToDate(off) { return new Date(START_DATE.getTime() + off * DAY_MS); }
  function fmtShort(d) {
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
  function initials(name) {
    var p = (name || '').split(/[-_\s.]+/).filter(Boolean);
    if (p.length >= 2) return (p[0][0] + p[1][0]).toUpperCase();
    return (name || '').slice(0, 2).toUpperCase() || '—';
  }
  function fmtInt(n) { return n.toLocaleString('en-US'); }
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  // Aggregate PR records in the day-range [lo, hi].
  function aggregate(lo, hi) {
    var userMap = Object.create(null);
    BASELINE.forEach(function (b) {
      userMap[b.login] = {
        login: b.login, name: b.name,
        prs: 0, reviewed: 0,
        locTotal: 0, locReviewed: 0,
        arSugg: 0, arAcc: 0,
      };
    });
    for (var i = 0; i < RECORDS.length; i++) {
      var r = RECORDS[i];
      if (r.dayOffset < lo || r.dayOffset > hi) continue;
      var u = userMap[r.user];
      if (!u) continue;
      u.prs += 1;
      u.reviewed += r.reviewed;
      u.locTotal += r.locTotal;
      u.locReviewed += r.locReviewed;
      u.arSugg += r.arSugg;
      u.arAcc += r.arAcc;
    }
    var users = Object.keys(userMap).map(function (k) { return userMap[k]; });
    var totals = {
      userCount: users.length,
      activeUsers: users.filter(function (u) { return u.prs > 0; }).length,
      prsRaised: users.reduce(function (a, u) { return a + u.prs; }, 0),
      prsReviewedByQodo: users.reduce(function (a, u) { return a + u.reviewed; }, 0),
      locTotal: users.reduce(function (a, u) { return a + u.locTotal; }, 0),
      locReviewed: users.reduce(function (a, u) { return a + u.locReviewed; }, 0),
      actionRequiredSugg: users.reduce(function (a, u) { return a + u.arSugg; }, 0),
      actionRequiredAccepted: users.reduce(function (a, u) { return a + u.arAcc; }, 0),
    };
    return { users: users, totals: totals };
  }

  function renderRows(users) {
    var tbody = $('ui-tbody');
    var sorted = users.slice().sort(function (a, b) {
      var aA = a.prs > 0 ? 1 : 0, bA = b.prs > 0 ? 1 : 0;
      if (aA !== bA) return bA - aA;
      var aR = a.arSugg > 0 ? a.arAcc / a.arSugg : -1;
      var bR = b.arSugg > 0 ? b.arAcc / b.arSugg : -1;
      if (aR !== bR) return bR - aR;
      return b.prs - a.prs;
    });
    var html = sorted.map(function (u) {
      var inactive = u.prs === 0;
      var arRate = u.arSugg > 0 ? Math.round(u.arAcc * 100 / u.arSugg) : 0;
      var arCell = u.arSugg > 0
        ? '<span class="ar-frac"><span class="acc">' + u.arAcc + '</span> / ' + u.arSugg + '</span>'
        : '<span class="ar-frac">—</span>';
      var rateCell = u.arSugg > 0
        ? '<span class="meter"><span class="bar"><i style="width:' + arRate + '%"></i></span>'
          + '<span class="pct">' + arRate + '%</span></span>'
        : '<span class="ar-frac">—</span>';
      var inactiveTag = inactive
        ? ' <span class="inactive-tag">No activity</span>'
        : '';
      var displayName = u.name || u.login;
      return '<tr class="' + (inactive ? 'inactive' : '') + '">'
        + '<td><div class="dev"><div class="avatar">' + escapeHtml(initials(displayName)) + '</div>'
        + '<div><div class="dev-name">' + escapeHtml(displayName) + inactiveTag + '</div>'
        + '<div class="dev-handle">@' + escapeHtml(u.login) + '</div></div></div></td>'
        + '<td class="num strong">' + u.prs + '</td>'
        + '<td class="num">' + fmtInt(u.locTotal) + '</td>'
        + '<td class="num col-q left strong">' + u.reviewed + '</td>'
        + '<td class="num col-q right">' + fmtInt(u.locReviewed) + '</td>'
        + '<td class="num">' + arCell + '</td>'
        + '<td class="num">' + rateCell + '</td>'
        + '</tr>';
    }).join('');
    tbody.innerHTML = html;
  }

  function renderHero(totals, days) {
    var reviewedPct = totals.prsRaised > 0
      ? Math.round(totals.prsReviewedByQodo * 100 / totals.prsRaised) : 0;
    var arPct = totals.actionRequiredSugg > 0
      ? Math.round(totals.actionRequiredAccepted * 100 / totals.actionRequiredSugg) : 0;
    var h1 = $('ui-headline');
    if (totals.prsRaised === 0) {
      h1.innerHTML = '<span class="accent zero">No PRs</span> from these '
        + totals.userCount + ' developers in the selected ' + days + '-day window.';
    } else if (totals.actionRequiredSugg > 0) {
      h1.innerHTML = 'Across ' + totals.activeUsers + ' of ' + totals.userCount
        + ' developers and ' + days + ' days, Qodo reviewed '
        + '<span class="accent">' + reviewedPct + '% of PRs</span> '
        + 'and the team accepted '
        + '<span class="accent success">' + arPct + '%</span> '
        + 'of high-priority suggestions.';
    } else {
      h1.innerHTML = 'Across ' + totals.activeUsers + ' of ' + totals.userCount
        + ' developers and ' + days + ' days, Qodo reviewed '
        + '<span class="accent">' + reviewedPct + '% of PRs</span>.';
    }
  }

  function renderTldr(totals) {
    var reviewedPct = totals.prsRaised > 0
      ? Math.round(totals.prsReviewedByQodo * 100 / totals.prsRaised) : 0;
    var arPct = totals.actionRequiredSugg > 0
      ? Math.round(totals.actionRequiredAccepted * 100 / totals.actionRequiredSugg) : 0;
    var lowSample = totals.actionRequiredSugg > 0 && totals.actionRequiredSugg < 10;
    $('ui-tldr-headline').textContent =
      totals.actionRequiredSugg > 0 ? (arPct + '%') : '—';
    $('ui-tldr-headline-row').classList.toggle('zero', totals.actionRequiredSugg === 0);
    var lowEl = $('ui-lowsamp');
    if (lowSample) {
      lowEl.textContent = 'low sample · ' + totals.actionRequiredSugg + ' AR';
      lowEl.hidden = false;
    } else {
      lowEl.hidden = true;
    }
    $('ui-tldr-prs').textContent = totals.prsRaised;
    $('ui-tldr-reviewed').textContent =
      totals.prsRaised > 0 ? (totals.prsReviewedByQodo + ' (' + reviewedPct + '%)') : '0';
    $('ui-tldr-loctotal').textContent = fmtInt(totals.locTotal);
    $('ui-tldr-locreviewed').textContent = fmtInt(totals.locReviewed);
    $('ui-tldr-ar').textContent = totals.actionRequiredSugg;
    $('ui-tldr-aracc').textContent = totals.actionRequiredAccepted;
  }

  // Slider state
  var lo = 0, hi = TOTAL_DAYS - 1;
  var loInput = $('dr-lo');
  var hiInput = $('dr-hi');
  var fill    = $('dr-fill');
  var bubLo   = $('dr-bubble-lo');
  var bubHi   = $('dr-bubble-hi');
  var daysOut = $('dr-days');
  var resetBtn = $('dr-reset');

  function update() {
    var pctLo = (lo / (TOTAL_DAYS - 1)) * 100;
    var pctHi = (hi / (TOTAL_DAYS - 1)) * 100;
    fill.style.left  = pctLo + '%';
    fill.style.right = (100 - pctHi) + '%';
    bubLo.style.left = pctLo + '%';
    bubHi.style.left = pctHi + '%';
    bubLo.textContent = fmtShort(dayToDate(lo));
    bubHi.textContent = fmtShort(dayToDate(hi));
    var days = hi - lo + 1;
    daysOut.textContent = days + ' day' + (days === 1 ? '' : 's') + ' selected';
    resetBtn.disabled = (lo === 0 && hi === TOTAL_DAYS - 1);

    var r = aggregate(lo, hi);
    renderHero(r.totals, days);
    renderTldr(r.totals);
    renderRows(r.users);
  }

  loInput.addEventListener('input', function () {
    var v = parseInt(loInput.value, 10);
    lo = Math.min(v, hi - 1);
    loInput.value = lo;
    update();
  });
  hiInput.addEventListener('input', function () {
    var v = parseInt(hiInput.value, 10);
    hi = Math.max(v, lo + 1);
    hiInput.value = hi;
    update();
  });
  resetBtn.addEventListener('click', function () {
    lo = 0; hi = TOTAL_DAYS - 1;
    loInput.value = lo; hiInput.value = hi;
    update();
  });

  update();
})();
</script>"""


# ─── HTML rendering ────────────────────────────────────────────────────

def _render_row_html(u: dict) -> str:
    """Server-side render of one initial table row (must match the client renderRows)."""
    inactive = u["prs"] == 0
    ar_rate = round(u["arAcc"] * 100 / u["arSugg"]) if u["arSugg"] else 0
    name = u.get("name") or u["login"]
    ar_cell = (
        f'<span class="ar-frac"><span class="acc">{u["arAcc"]}</span> / {u["arSugg"]}</span>'
        if u["arSugg"] else '<span class="ar-frac">—</span>'
    )
    rate_cell = (
        f'<span class="meter"><span class="bar"><i style="width:{ar_rate}%"></i></span>'
        f'<span class="pct">{ar_rate}%</span></span>'
        if u["arSugg"] else '<span class="ar-frac">—</span>'
    )
    inactive_tag = ' <span class="inactive-tag">No activity</span>' if inactive else ''
    cls = ' class="inactive"' if inactive else ''
    return (
        f'<tr{cls}>'
        f'<td><div class="dev"><div class="avatar">{_h(_initials(name))}</div>'
        f'<div><div class="dev-name">{_h(name)}{inactive_tag}</div>'
        f'<div class="dev-handle">@{_h(u["login"])}</div></div></div></td>'
        f'<td class="num strong">{u["prs"]}</td>'
        f'<td class="num">{u["locTotal"]:,}</td>'
        f'<td class="num col-q left strong">{u["reviewed"]}</td>'
        f'<td class="num col-q right">{u["locReviewed"]:,}</td>'
        f'<td class="num">{ar_cell}</td>'
        f'<td class="num">{rate_cell}</td>'
        f'</tr>'
    )


def _render_ticks(total_days: int) -> str:
    if total_days <= 1:
        return ''
    ticks = []
    i = 0
    while i <= total_days - 1:
        pct = i / (total_days - 1) * 100
        ticks.append(f'<div class="dr-tick" style="left:{pct:.3f}%"></div>')
        i += 7
    return ''.join(ticks)


def generate_user_html(rows: list, org: str, since: str, until: str,
                       logo_path: Optional[str] = None) -> str:
    """Render the full per-user impact HTML report.

    Args:
        rows: per-PR row dicts (the same list passed to report.aggregate).
        org: GitHub org slug, e.g. 'acme-corp'.
        since: 'YYYY-MM-DD' window start.
        until: 'YYYY-MM-DD' window end.
        logo_path: Optional path to a logo SVG/PNG to inline in the header.

    Returns the complete HTML document as a string.
    """
    data = aggregate_user_impact(rows, since, until)
    total_days = data.window.get("total_days", 1)

    # Server-rendered initial state for the full window. The JS recomputes
    # everything on first paint anyway, but rendering it server-side keeps
    # the report meaningful before JS runs (and supports view-source / no-JS).
    total_prs = sum(u["prs"] for u in data.users)
    total_reviewed = sum(u["reviewed"] for u in data.users)
    total_loc = sum(u["locTotal"] for u in data.users)
    total_loc_reviewed = sum(u["locReviewed"] for u in data.users)
    total_ar_sug = sum(u["arSugg"] for u in data.users)
    total_ar_acc = sum(u["arAcc"] for u in data.users)
    active_users = sum(1 for u in data.users if u["prs"] > 0)
    user_count = len(data.users)
    reviewed_pct = round(total_reviewed * 100 / total_prs) if total_prs else 0
    ar_pct = round(total_ar_acc * 100 / total_ar_sug) if total_ar_sug else 0
    low_sample = 0 < total_ar_sug < 10

    # Initial headline (must match the JS in renderHero())
    if total_prs == 0:
        headline = (
            f'<span class="accent zero">No PRs</span> from these {user_count} developers '
            f'in the selected {total_days}-day window.'
        )
    elif total_ar_sug > 0:
        headline = (
            f'Across {active_users} of {user_count} developers and {total_days} days, '
            f'Qodo reviewed <span class="accent">{reviewed_pct}% of PRs</span> and the '
            f'team accepted <span class="accent success">{ar_pct}%</span> of high-priority '
            f'suggestions.'
        )
    else:
        headline = (
            f'Across {active_users} of {user_count} developers and {total_days} days, '
            f'Qodo reviewed <span class="accent">{reviewed_pct}% of PRs</span>.'
        )

    # JSON blobs — small enough to inline (typical ~140 bytes per PR row).
    baseline_json = json.dumps(
        [{"login": u["login"], "name": u.get("name") or u["login"]} for u in data.users],
        separators=(",", ":"),
    )
    records_json = json.dumps(data.pr_records, separators=(",", ":"))
    window_json = json.dumps(data.window, separators=(",", ":"))

    rows_html = "\n".join(_render_row_html(u) for u in data.users) if data.users else (
        '<tr><td colspan="7" style="text-align:center;padding:48px 16px;color:var(--fg-muted)">'
        'No developer activity found in this window.</td></tr>'
    )
    logo_html = _embed_logo(logo_path, height=32)
    logo_html_small = _embed_logo(logo_path, height=18)
    ticks_html = _render_ticks(total_days)
    fallback_brand = '<span style="font-size:18px;font-weight:700;color:var(--fg-strong)">qodo</span>'
    fallback_brand_small = '<span style="font-weight:600;color:var(--fg-default)">qodo</span>'
    lowsamp_hidden = '' if low_sample else ' hidden'
    headline_zero_class = ' zero' if total_ar_sug == 0 else ''
    headline_val = f'{ar_pct}%' if total_ar_sug else '—'
    reviewed_val = f'{total_reviewed} ({reviewed_pct}%)' if total_prs else '0'
    days_word = 's' if total_days != 1 else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qodo — user impact report · {_h(org)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
{_CSS}
</head>
<body>
<div class="page-wrap"><div class="lb">

  <div class="lb-bar">
    <div class="brand">{logo_html or fallback_brand}<span class="sep"></span><span class="label">User impact report</span></div>
    <div class="crumbs">
      <span class="pill">{_h(org)}</span>
      <span>·</span>
      <span>{user_count} developers</span>
      <span>·</span>
      <span>Merged PRs only</span>
    </div>
    <div class="rng">{_h(since)} → {_h(until)}</div>
  </div>

  <div class="lb-hero">
    <div>
      <h1 id="ui-headline">{headline}</h1>
    </div>
    <div class="tldr">
      <div class="tldr-label">
        <span>At a glance</span>
        <span id="ui-lowsamp" class="lowsamp"{lowsamp_hidden}>low sample · {total_ar_sug} AR</span>
      </div>
      <div id="ui-tldr-headline-row" class="tldr-row headline{headline_zero_class}">
        <span class="l">Action Required acceptance</span>
        <span class="v" id="ui-tldr-headline">{headline_val}</span>
      </div>
      <div class="tldr-row"><span class="l">Total PRs</span><span class="v" id="ui-tldr-prs">{total_prs}</span></div>
      <div class="tldr-row"><span class="l">Reviewed by Qodo</span><span class="v" id="ui-tldr-reviewed">{reviewed_val}</span></div>
      <div class="tldr-row"><span class="l">LOC (total)</span><span class="v" id="ui-tldr-loctotal">{total_loc:,}</span></div>
      <div class="tldr-row"><span class="l">LOC reviewed</span><span class="v" id="ui-tldr-locreviewed">{total_loc_reviewed:,}</span></div>
      <div class="tldr-row"><span class="l">Action Required</span><span class="v" id="ui-tldr-ar">{total_ar_sug}</span></div>
      <div class="tldr-row"><span class="l">…of which accepted</span><span class="v" id="ui-tldr-aracc">{total_ar_acc}</span></div>
    </div>
  </div>

  <div class="lb-section">

    <div class="lb-ctrl">
      <div class="lb-ctrl-label">Time range</div>
      <div class="dr">
        <div class="dr-row">
          <span class="dr-end">{_h(_human_date(since))}</span>
          <div class="dr-track-wrap">
            <div class="dr-track"></div>
            {ticks_html}
            <div class="dr-fill" id="dr-fill" style="left:0%;right:0%"></div>
            <input type="range" id="dr-lo" min="0" max="{total_days - 1}" value="0" class="dr-input dr-lo" aria-label="Start date">
            <input type="range" id="dr-hi" min="0" max="{total_days - 1}" value="{total_days - 1}" class="dr-input dr-hi" aria-label="End date">
            <div class="dr-bubble" id="dr-bubble-lo" style="left:0%"></div>
            <div class="dr-bubble" id="dr-bubble-hi" style="left:100%"></div>
          </div>
          <span class="dr-end">{_h(_human_date(until))}</span>
        </div>
        <div class="dr-meta">
          <span class="dr-days" id="dr-days">{total_days} day{days_word} selected</span>
        </div>
      </div>
      <div class="lb-ctrl-actions">
        <button type="button" class="lb-reset" id="dr-reset" disabled>Reset</button>
      </div>
    </div>

    <div class="lb-sec-head">
      <div class="lb-sec-title">Per-developer breakdown</div>
    </div>
    <div class="lb-board">
      <table>
        <thead>
          <tr>
            <th rowspan="2">Developer</th>
            <th colspan="2" class="grp num">Total</th>
            <th colspan="2" class="grp qodo num">Qodo reviewed</th>
            <th rowspan="2" class="num">Implemented Qodo Findings</th>
            <th rowspan="2" class="num">Acceptance Rate</th>
          </tr>
          <tr>
            <th class="num sub">PRs</th>
            <th class="num sub">LOC</th>
            <th class="num sub">PRs</th>
            <th class="num sub">LOC</th>
          </tr>
        </thead>
        <tbody id="ui-tbody">
{rows_html}
        </tbody>
      </table>
    </div>
  </div>

  <div class="lb-foot">
    <div class="l">{logo_html_small or fallback_brand_small}<span>user impact report</span></div>
    <div class="lb-mono">{_h(org)} · {user_count} users · report scope: {_h(since)} → {_h(until)}</div>
  </div>

</div></div>

<script type="application/json" id="ui-records">{records_json}</script>
<script type="application/json" id="ui-baseline">{baseline_json}</script>
<script type="application/json" id="ui-window">{window_json}</script>

{_JS}

</body>
</html>
"""
