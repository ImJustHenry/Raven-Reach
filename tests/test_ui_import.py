def test_ui_module_imports():
    """Catches syntax/import errors in the glasses UI (the other tests don't import it)."""
    import reach.ui  # noqa: F401
