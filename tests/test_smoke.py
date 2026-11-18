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


def test_browser_safety_rules():
    import browser
    assert browser.RISKY_CLICK.search("Place your order") and browser.RISKY_CLICK.search("Buy now")
    assert not browser.RISKY_CLICK.search("Reviews")
    assert browser.SECRET_FIELD.search("password pw") and browser.SECRET_FIELD.search("cc-number")


def test_claude_api_message_conversion():
    import anthropic_provider as ap
    chat = [
        {"role": "user", "content": "open notes"},
        {"role": "assistant", "content": "On it.", "tool_calls": [
            {"id": "call:1", "type": "function", "function": {"name": "run_command", "arguments": '{"command": "ls"}'}},
            {"id": "call_2", "type": "function", "function": {"name": "read_file", "arguments": "{bad"}}]},
        {"role": "tool", "tool_call_id": "call:1", "content": "a.txt"},
        {"role": "tool", "tool_call_id": "call_2", "content": "Failed: nope"},
        {"role": "assistant", "content": "Done."},
    ]
    out = ap.messages(chat)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant"]
    uses = [b for b in out[1]["content"] if b["type"] == "tool_use"]
    assert uses[0]["id"] == "call_1" and uses[0]["input"] == {"command": "ls"} and uses[1]["input"] == {}
    results = out[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["call_1", "call_2"] and results[1]["is_error"] is True
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "toolu_1", "name": "x", "input": {}}]
    replay = ap.messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "tool_calls": [
        {"id": "toolu_1", "type": "function", "function": {"name": "x", "arguments": "{}"}, "extra_content": {"anthropic": raw}}]}])
    assert replay[1]["content"] == raw                      # Claude's own blocks come back unchanged
    t = ap.tools([{"type": "function", "function": {"name": "x", "description": "d", "parameters": {"type": "object"}}}])
    assert t[0]["eager_input_streaming"] is True and t[0]["input_schema"] == {"type": "object"}


def test_provider_helpers(monkeypatch):
    import fallback
    cf = next(p for p in fallback.PROVIDERS if p["id"] == "cloudflare")
    oa = next(p for p in fallback.PROVIDERS if p["id"] == "openai")
    assert fallback._base(cf, "acc123:tok") == "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1"
    assert fallback._auth(cf, "acc123:tok") == {"Authorization": "Bearer tok"}
    assert fallback._body(oa, {"max_tokens": 5, "temperature": 0.4, "model": "m"}) == {"max_completion_tokens": 5, "model": "m"}
    lanes = [(oa, "a"), (cf, "b")]
    monkeypatch.setattr(config, "PREFERRED_PROVIDER", "cloudflare")
    assert fallback.preferred_first(lanes)[0][0]["id"] == "cloudflare"
    monkeypatch.setattr(config, "PREFERRED_PROVIDER", "")
    assert fallback.preferred_first(lanes) == lanes
    gem = fallback._for_provider([{"role": "assistant", "tool_calls": [{"id": "1", "extra_content": {"anthropic": []}}]}], "gemini")
    assert gem[0]["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "skip_thought_signature_validator"}}
