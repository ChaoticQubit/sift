"""Offline smoke tests only (CLAUDE.md): no network, no paid APIs."""


def test_import():
    import sift

    assert sift.__version__


def test_models_roundtrip():
    from sift.models import Item, Medium

    it = Item(id="abc", permalink="https://instagram.com/reel/x/", medium=Medium.reel)
    assert it.medium == "reel"
