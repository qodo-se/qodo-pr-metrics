import inspect
import pytest

from collectors.base import Collector, get_collector
from collectors.github import GitHubCollector


def _bare_signature(fn):
    """Return a signature with all annotations stripped, for structural comparison."""
    sig = inspect.signature(fn)
    bare_params = [
        p.replace(annotation=inspect.Parameter.empty)
        for p in sig.parameters.values()
    ]
    return sig.replace(parameters=bare_params, return_annotation=inspect.Parameter.empty)


def test_github_collector_satisfies_protocol():
    # runtime_checkable verifies method NAMES are present.
    assert isinstance(GitHubCollector(), Collector)


def test_github_collector_has_every_protocol_method():
    # Derive names from the Protocol itself so this test can't drift out of sync.
    # __protocol_attrs__ lists only the declared Protocol members (Python 3.12+).
    protocol_methods = (
        Collector.__protocol_attrs__
        if hasattr(Collector, "__protocol_attrs__")
        else {n for n in vars(Collector) if not n.startswith("_") and callable(getattr(Collector, n, None))}
    )
    for name in protocol_methods:
        assert callable(getattr(GitHubCollector, name, None)), f"missing method: {name}"
        proto_sig = _bare_signature(getattr(Collector, name))
        impl_sig = _bare_signature(getattr(GitHubCollector, name))
        assert impl_sig == proto_sig, (
            f"signature mismatch for {name}: "
            f"GitHubCollector has {inspect.signature(getattr(GitHubCollector, name))}, "
            f"Collector Protocol has {inspect.signature(getattr(Collector, name))}"
        )


def test_get_collector_returns_github():
    assert isinstance(get_collector("github"), GitHubCollector)


def test_get_collector_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_collector("bitbucket")
