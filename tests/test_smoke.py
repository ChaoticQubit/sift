"""Offline smoke tests only (CLAUDE.md): no network, no paid APIs."""


def test_import():
    import sift

    assert sift.__version__


def test_models_roundtrip():
    from sift.models import IngestVia, Item, Medium

    it = Item(
        id="abc",
        permalink="https://instagram.com/reel/x/",
        medium=Medium.reel,
        ingest_via=IngestVia.dyi_export,
    )
    assert it.medium == "reel"


def test_action_item_defaults_pending():
    from sift.models import ActionItem, Interpretation

    interp = Interpretation(action_items=[ActionItem(text="apply to X")])
    assert interp.action_items[0].status == "pending"
    assert 0.0 <= interp.action_items[0].confidence <= 1.0
