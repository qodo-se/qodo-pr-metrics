"""The git-provider collector contract."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Collector(Protocol):
    def search_merged_prs(self, org, since, until=None, chunk_days=None,
                          repos=None, total_prs=None, qodo_only=True): ...
    def fetch_pr_data(self, owner, repo, number, comments_limit=20): ...
    def fetch_pr_data_batch(self, prs, batch_size=50, raise_on_5xx=False,
                            comments_first=20): ...
    def get_org_pr_count(self, org, since, repos=None): ...
    def get_org_author_count(self, org, since, repos=None, chunk_days=None,
                             total_prs=None): ...
    def get_org_repo_count(self, org): ...
    def get_qodo_pr_count(self, org, since, repos=None): ...
    def get_all_pr_loc(self, org, since, repos=None, chunk_days=None,
                       total_prs=None, page_size=None): ...
    def get_revert_pr_count(self, org, since, repos=None): ...
    def get_hotfix_pr_count(self, org, since, repos=None): ...
    def get_weekly_pr_counts(self, org, since, repos=None): ...


def get_collector(name: str, **config) -> "Collector":
    """Return a collector for the named git provider.

    `config` carries provider-specific construction args (Bitbucket needs
    base_url/token/scope); the GitHub collector ignores it.
    """
    if name == "github":
        from collectors.github import GitHubCollector
        return GitHubCollector()
    if name == "bitbucket-dc":
        from collectors.bitbucket import BitbucketCollector
        return BitbucketCollector(**config)
    raise ValueError(f"Unknown git provider: {name!r}")
