"""Fast checks that need no models, microphone or API keys. Run: python -m pytest -q"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def test_time_format_works_on_this_os():
    assert time.strftime(config.tf("%-I:%M %p"))[0] != "0"


def test_persona_matches_the_os():
    assert config.MACHINE in config.PERSONA
    assert ("osascript" in config.PERSONA) == config.IS_MAC
    assert "{" not in config.APPS_GUIDE.replace("{}", "")


def test_reminders_parse_and_describe(tmp_path, monkeypatch):
    import reminders
    monkeypatch.setattr(config, "DATA", tmp_path)
    due = reminders.parse_when(10, "")
    assert 590 < due - time.time() < 610
    assert "at" in reminders.describe({"id": "x", "kind": "reminder", "text": "stretch", "due": due, "repeat": ""})


def test_shell_runs_a_command():
    import fallback
    out = fallback.run_command("echo hello-mike")
    assert "hello-mike" in out


def test_terminal_session_round_trip():
    import terminals
    cmd = "echo terminal-ok"
    assert "Started" in terminals.start("t1", cmd, str(ROOT))
    out = ""
    for _ in range(50):
        out = terminals.read("t1")
        if "terminal-ok" in out:
            break
        time.sleep(0.1)
    assert "terminal-ok" in out
    terminals.stop_all()


def test_desktop_handles_unknown_and_mac_only_ops():
    import desktop
    assert "error" in desktop.handle("no_such_op", {})
    if not config.IS_MAC:
        assert "Mac" in desktop.handle("ui", {})["error"]


def test_model_list_is_complete():
    import models
    assert set(models.FILES) == {"kokoro-v1.0.int8.onnx", "voices-v1.0.bin", "wespeaker_en_resnet34.onnx"}


def test_no_personal_paths_in_code():
    home = str(Path.home())
    for f in ROOT.glob("*.py"):
        assert home not in f.read_text(encoding="utf-8"), f
