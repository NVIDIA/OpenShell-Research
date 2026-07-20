import reachy_mini_conversation_app


def test_package_exports_version() -> None:
    """The package root should expose a stable version string."""
    assert isinstance(reachy_mini_conversation_app.__version__, str)
    assert reachy_mini_conversation_app.__version__
