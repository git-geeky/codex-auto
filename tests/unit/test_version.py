from codex_auto import __version__


def test_package_exposes_project_version() -> None:
    assert __version__ == "0.1.0"
