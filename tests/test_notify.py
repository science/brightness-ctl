"""Tests for notify.py — command construction and replace-id persistence."""

from notify import build_notify_cmd, load_notify_id, save_notify_id, _NOTIFY_ID_FILE


class TestBuildNotifyCmd:
    """Verify notify-send command construction."""

    def test_basic_message(self):
        cmd = build_notify_cmd("Warmer: 2600K", replace_id=None)
        assert cmd[0] == "notify-send"
        assert "-a" in cmd
        assert "brightness-ctl" in cmd
        assert "Brightness" in cmd
        assert "Warmer: 2600K" in cmd

    def test_with_replace_id(self):
        cmd = build_notify_cmd("Warmer: 2600K", replace_id=42)
        assert "--replace-id" in cmd
        idx = cmd.index("--replace-id")
        assert cmd[idx + 1] == "42"

    def test_without_replace_id(self):
        cmd = build_notify_cmd("test", replace_id=None)
        assert "--replace-id" not in cmd

    def test_has_print_id(self):
        cmd = build_notify_cmd("test", replace_id=None)
        assert "--print-id" in cmd

    def test_has_timeout(self):
        cmd = build_notify_cmd("test", replace_id=None)
        assert "-t" in cmd
        idx = cmd.index("-t")
        assert cmd[idx + 1] == "1500"

    def test_transient_flag(self):
        cmd = build_notify_cmd("test", replace_id=None)
        assert "-e" in cmd


class TestNotifyIdPersistence:
    """Replace-id survives daemon restarts via file persistence."""

    def test_save_and_load(self, tmp_path, monkeypatch):
        id_file = tmp_path / "notify-id"
        monkeypatch.setattr("notify._NOTIFY_ID_FILE", id_file)
        save_notify_id(42)
        assert load_notify_id() == 42

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("notify._NOTIFY_ID_FILE", tmp_path / "missing")
        assert load_notify_id() is None

    def test_load_empty_file(self, tmp_path, monkeypatch):
        id_file = tmp_path / "notify-id"
        id_file.write_text("")
        monkeypatch.setattr("notify._NOTIFY_ID_FILE", id_file)
        assert load_notify_id() is None

    def test_load_corrupt_file(self, tmp_path, monkeypatch):
        id_file = tmp_path / "notify-id"
        id_file.write_text("not-a-number")
        monkeypatch.setattr("notify._NOTIFY_ID_FILE", id_file)
        assert load_notify_id() is None
