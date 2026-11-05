import pytest

from image_magic import sync
from image_magic.settings import Settings


@pytest.mark.parametrize("mode", ["full", "quick", "test"])
@pytest.mark.parametrize("webui", [False, True])
def test_never_deletes_from_icloud(mode, webui):
    for edited in (True, False):
        s = Settings(apple_id="me@example.com", library_path="/lib", download_edited=edited,
                     download_videos=not edited, advanced_data_protection=edited)
        cmd = sync.build_command("icloudpd", s, mode=mode, webui=webui)
        assert not set(cmd) & sync.FORBIDDEN_FLAGS


@pytest.mark.parametrize("flag", sorted(sync.FORBIDDEN_FLAGS))
def test_guard_rejects_delete_flags(flag):
    with pytest.raises(sync.UnsafeCommandError):
        sync.assert_never_deletes(["icloudpd", flag])
    with pytest.raises(sync.UnsafeCommandError):
        sync.assert_never_deletes(["icloudpd", f"{flag}=3"])
    with pytest.raises(sync.UnsafeCommandError):
        sync.run_interactive(["icloudpd", flag])


def test_command_reflects_settings():
    s = Settings(apple_id="me@example.com", library_path="/lib",
                 download_edited=False, download_videos=False, download_live_videos=False,
                 save_xmp=False, folder_structure="year", advanced_data_protection=True,
                 quick_sync_stop_after=7)
    cmd = sync.build_command("icloudpd", s, mode="quick", webui=True)
    assert cmd[cmd.index("--folder-structure") + 1] == "{:%Y}"
    assert cmd.count("--size") == 1
    for flag in ("--skip-videos", "--skip-live-photos", "--adp-web-access"):
        assert flag in cmd
    assert "--xmp-sidecar" not in cmd
    assert cmd[cmd.index("--until-found") + 1] == "7"
    assert cmd[cmd.index("--password-provider") + 1] == "webui"


def test_default_command():
    s = Settings(apple_id="me@example.com", library_path="/lib")
    cmd = sync.build_command("icloudpd", s, mode="test")
    assert cmd[:3] == ["icloudpd", "--username", "me@example.com"]
    assert cmd[cmd.index("--folder-structure") + 1] == "{:%Y/%Y-%m}"
    assert cmd.count("--size") == 2 and "--xmp-sidecar" in cmd
    assert cmd[cmd.index("--recent") + 1] == "20"


def test_settings_roundtrip_and_validation():
    s = Settings.from_dict({
        "apple_id": " me@example.com ", "download_videos": "false", "auto_sync_hours": "999999",
        "folder_structure": "bogus", "quick_sync_stop_after": "x", "unknown": 1,
    })
    assert s.apple_id == "me@example.com"
    assert s.download_videos is False
    assert s.auto_sync_hours == 24 * 7
    assert s.folder_structure == "year_month"
    assert s.quick_sync_stop_after == 50
    s.save()
    assert Settings.load() == s


def test_default_library_not_in_pictures():
    assert "Pictures" not in Settings().library_path
