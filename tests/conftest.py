import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Never read or write the real user's settings during tests."""
    home = tmp_path / "settings-home"
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(home))
    return home
