"""Code tab: approvals, pasted images, saved sessions, file containment, and the backup brain's handling of
provider trouble (rate limits, timeouts, empty replies, models that can't see images), driven end to end
against a fake OpenAI-style provider. No network, no keys, no quota."""
import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
import pytest

import code_guard
import code_sessions
import fallback
import model_stats
import projects

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 64).decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


# ---------------------------------------------------------------- approvals and images
def test_code_tab_asks_before_edits_and_commands_unless_allowed_for_the_session():
    ask = code_guard.should_ask
    assert ask("Bash", False, True, "ask", set())                       # Code tab, Ask first: every command
    assert ask("Write", False, True, "ask", set())
    assert ask("mcp__mike__terminal_start", False, True, "ask", set())  # Claude Code's tool name for the same thing
    assert ask("mcp__mike__code_bash", False, True, "ask", set())       # the v2 self-coding tools too
    assert not ask("mcp__mike__code_read", False, True, "ask", set())
    assert not ask("Read", False, True, "ask", set())                   # reading never asks
    assert not ask("Bash", False, True, "ask", {"Bash"})                # "Allow for this session"
    assert not ask("Bash", False, True, "risky", set())                 # only-risky mode
    assert not ask("Bash", False, False, "ask", set())                  # voice / chat: only risky actions ask
    assert ask("Bash", True, False, "risky", {"Bash"})                  # risky always asks, whatever was allowed


def test_only_jpeg_images_are_accepted_and_at_most_four():
    assert code_guard.clean_images([JPEG]) == [JPEG]
    assert code_guard.clean_images(["data:image/jpeg;base64," + JPEG]) == [JPEG]
    assert code_guard.clean_images([PNG, "not base64 !!", None, 42]) == []
    assert len(code_guard.clean_images([JPEG] * 9)) == 4
    assert code_guard.clean_images("not a list") == []
    assert code_guard.clean_images(["/9j/" + "A" * (code_guard.MAX_IMAGE_B64 + 10)]) == []


# ---------------------------------------------------------------- saved sessions
def test_sessions_are_saved_listed_and_reopened(tmp_path, monkeypatch):
    monkeypatch.setattr(code_sessions, "DIR", tmp_path / "s")
    sid = code_sessions.new({"name": "app", "path": "/x/app"})
    for e in ({"type": "user", "text": "fix the tests"}, {"type": "tool", "id": "a", "name": "Bash", "args": {"command": "pytest"}},
              {"type": "tool_result", "id": "a", "output": "1 failed", "error": True},
              {"type": "assistant_delta", "text": "Fixed "}, {"type": "assistant_delta", "text": "it."},
              {"type": "assistant_done", "duration_ms": 900}, {"type": "audio", "data": "x"}):
        code_sessions.append(sid, e)
    code_sessions.snapshot(sid, [{"file": "a.py", "status": "M", "add": 1, "del": 0}], {"a.py": "+x"})
    ev = code_sessions.load(sid)
    kinds = [e["type"] for e in ev]
    assert kinds == ["session", "user", "tool", "tool_result", "assistant_delta", "assistant_done", "snapshot"]
    assert ev[4]["text"] == "Fixed it."                                   # streamed text joined back up
    assert "audio" not in kinds                                           # only Code tab events are kept
    (row,) = code_sessions.listing("/x/app")
    assert row["first"] == "fix the tests" and row["turns"] == 1 and row["changed"] == 1
    assert code_sessions.listing("/other") == []


def test_session_ids_cannot_reach_other_files(tmp_path, monkeypatch):
    monkeypatch.setattr(code_sessions, "DIR", tmp_path / "s")
    (tmp_path / "secret.jsonl").write_text('{"type": "session"}\n')
    assert code_sessions.load("../secret") == []
    code_sessions.append("../secret", {"type": "user", "text": "x"})
    assert (tmp_path / "secret.jsonl").read_text() == '{"type": "session"}\n'


def test_project_file_reads_stay_inside_the_project(tmp_path):
    proj, sib = tmp_path / "app", tmp_path / "app-secrets"
    proj.mkdir(); sib.mkdir()
    (proj / "ok.txt").write_text("fine")
    (sib / "key.txt").write_text("SECRET")
    (tmp_path / "outside.txt").write_text("SECRET")
    assert projects.read("ok.txt", str(proj)) == "fine"
    assert projects.read("../outside.txt", str(proj)) == ""
    assert projects.read("../app-secrets/key.txt", str(proj)) == ""      # a look-alike sibling folder
    assert "SECRET" not in projects.diff("../outside.txt", str(proj))


# ---------------------------------------------------------------- the backup brain against a fake provider
def sse(*chunks) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def text_reply(t):
    return sse({"choices": [{"delta": {"content": t}}]})


def tool_reply(name, args, cid="c1"):
    return sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": cid, "function": {"name": name, "arguments": json.dumps(args)}}]}}]})


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A one-provider world with two models, m1 and m2. Each test scripts what the provider answers."""
    monkeypatch.setattr(model_stats, "PATH", tmp_path / "stats.json")
    monkeypatch.setattr(model_stats, "_stats", {})
    prov = {"id": "fake", "name": "Fake", "base": "http://fake.test/v1", "prefer": ["m1", "m2"]}
    monkeypatch.setattr(fallback, "PROVIDERS", [prov])
    monkeypatch.setattr(fallback, "load_keys", lambda: {"fake": "k"})
    monkeypatch.setattr(fallback.skills, "index", lambda: {})
    world = {"script": [], "seen": []}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        world["seen"].append(body)
        step = world["script"].pop(0) if world["script"] else ("text", "done")
        kind, val = step[0], step[1]
        if kind == "raise":
            raise val
        if kind == "status":
            return httpx.Response(val, text=step[2])
        if kind == "for":                                   # answer depends on the model asked
            kind, val = val[body["model"]]
            if kind == "status":
                return httpx.Response(val[0], text=val[1])
        if kind == "text" and not body.get("stream"):           # a plain request (the vision probe)
            return httpx.Response(200, json={"choices": [{"message": {"content": val}}]})
        return httpx.Response(200, content=text_reply(val) if kind == "text" else tool_reply(*val),
                              headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(fallback.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    events = {"tools": [], "results": [], "done": None, "text": ""}

    async def on_delta(t): events["text"] += t
    async def on_tool(n, a): events["tools"].append(n)
    async def on_done(i): events["done"] = i
    async def allow(*a): return True
    brain = fallback.FallbackBrain(on_delta, on_tool, on_done, allow)
    brain.models = {"fake": ["m1", "m2"]}
    brain.dev_until = time.time() + 600                     # a coding turn
    async def on_result(tid, name, out, err): events["results"].append((tid, name, out, err))
    brain.on_tool_result = on_result
    return brain, world, events


def run(brain, text, images=None):
    async def go():
        await brain.send(text, images)
        await brain.task
    asyncio.run(go())


def test_each_step_result_reaches_the_code_tab(fake, tmp_path):
    brain, world, ev = fake
    f = tmp_path / "notes.txt"; f.write_text("hello from the file")
    world["script"] = [("tool", ("read_file", {"path": str(f)})), ("text", "It says hello.")]
    run(brain, "what does notes.txt say")
    assert ev["done"].get("error") is None and "hello" in ev["text"]
    (tid, name, out, err), = ev["results"]
    assert tid == "c1" and name == "read_file" and "hello from the file" in out and err is False


def test_a_failed_step_is_reported_as_an_error(fake, tmp_path):
    brain, world, ev = fake
    world["script"] = [("tool", ("read_file", {"path": str(tmp_path / "missing.txt")})), ("text", "It isn't there.")]
    run(brain, "read missing.txt")
    assert ev["results"][0][3] is True                         # "Couldn't read it: …" is an error in the UI


def test_rate_limit_then_the_model_frees_up(fake):
    brain, world, ev = fake
    msg = 'HTTP 429 {"error": {"message": "Rate limit reached. Please try again in 0.2s."}}'
    world["script"] = [("status", 429, msg), ("status", 429, msg), ("text", "ok after waiting")]
    t0 = time.time()
    run(brain, "do it")
    assert ev["done"].get("error") is None and "ok after waiting" in ev["text"]
    assert time.time() - t0 < 15


def test_an_empty_reply_sidelines_only_that_model(fake):
    brain, world, ev = fake
    world["script"] = [("text", ""), ("text", ""), ("text", "from the other model")]
    run(brain, "say something")
    assert "from the other model" in ev["text"]
    assert "fake" not in brain.failed_until                      # the provider itself is still fine
    assert world["seen"][-1]["model"] == "m2"


def test_a_timeout_sidelines_only_that_model(fake):
    brain, world, ev = fake
    world["script"] = [("raise", httpx.ReadTimeout("slow")), ("text", "the fast one answered")]
    run(brain, "hello there friend")
    assert "the fast one answered" in ev["text"]
    assert "fake" not in brain.failed_until and "fake/m1" in brain.failed_until


def test_pasted_images_go_to_a_model_that_can_see_them(fake):
    brain, world, ev = fake
    # m1 takes the probe but guesses (it can't see); m2 says red. Only m2 gets the screenshot.
    world["script"] = [("text", "Blue."), ("text", "Red"), ("text", "I see a red square")]
    run(brain, "what is in this screenshot", [JPEG])
    assert "red square" in ev["text"]
    assert model_stats.flag("fake/m1", "vision") is False and model_stats.flag("fake/m2", "vision") is True
    final = world["seen"][-1]
    assert final["model"] == "m2"
    last_user = [m for m in final["messages"] if m["role"] == "user"][-1]
    assert last_user["content"][1]["image_url"]["url"] == f"data:image/jpeg;base64,{JPEG}"
    assert not any(b.get("model") == "m1" and b.get("stream") for b in world["seen"])   # m1 never got the real request


def test_a_model_that_rejects_images_is_skipped_too(fake):
    brain, world, ev = fake
    world["script"] = [("status", 400, "HTTP 400 image input is not supported"), ("text", "red"), ("text", "a red square")]
    run(brain, "what is this", [JPEG])
    assert "red square" in ev["text"] and model_stats.flag("fake/m1", "vision") is False


def test_images_survive_the_context_line_the_server_adds(fake):
    brain, world, ev = fake
    world["script"] = [("text", "red"), ("text", "red")]
    run(brain, "[context: front app: Mike]\nwhat color is this", [JPEG])
    last_user = [m for m in world["seen"][-1]["messages"] if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], list) and last_user["content"][0]["text"] == "what color is this"
    assert last_user["content"][1]["type"] == "image_url"


def test_when_no_model_can_see_images_mike_says_so_instead_of_guessing(fake):
    brain, world, ev = fake
    world["script"] = [("text", "blue"), ("text", "green"), ("text", "I can't see images here; can you describe it?")]
    run(brain, "what is in this screenshot", [JPEG])
    assert ev["done"].get("error") is None and "describe" in ev["text"]
    last_user = [m for m in world["seen"][-1]["messages"] if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], str) and "can see images" in last_user["content"]


def test_old_images_are_not_resent_every_turn(fake):
    brain, world, ev = fake
    world["script"] = [("text", "red"), ("text", "a cat"), ("text", "sure")]
    run(brain, "what is this", [JPEG])
    run(brain, "thanks, now rename it")
    users = [m for m in world["seen"][-1]["messages"] if m["role"] == "user"]
    assert all(isinstance(u["content"], str) for u in users)
    assert "image(s) were shared here" in users[0]["content"]


def test_only_mikes_own_page_can_drive_him():
    ok = code_guard.request_ok
    assert ok(None, "127.0.0.1:8777", 8777)                                  # native app, tools, curl
    assert ok("http://127.0.0.1:8777", "127.0.0.1:8777", 8777)               # his own window
    assert ok("http://localhost:8777", "localhost:8777", 8777)
    assert not ok("https://evil.example", "127.0.0.1:8777", 8777)            # another site in your browser
    assert not ok("null", "127.0.0.1:8777", 8777)                            # sandboxed frames send "null"
    assert not ok("http://127.0.0.1:9999", "127.0.0.1:8777", 8777)           # another local app's page
    assert not ok(None, "evil.example:8777", 8777)                           # DNS rebinding
    assert ok(None, "192.168.1.5:8777", 8777, bind_host="0.0.0.0")           # opened up on purpose: host not checked


def test_a_coding_turn_cannot_finish_without_checking_its_work(fake, tmp_path):
    brain, world, ev = fake
    f = tmp_path / "app.py"
    world["script"] = [("tool", ("write_file", {"path": str(f), "content": "print(1)\n"})), ("text", "Done, it works."),
                       ("tool", ("run_command", {"command": f"python3 {f}"}, "c2")), ("text", "Ran it: prints 1.")]
    run(brain, "make app.py print 1")
    assert "prints 1" in ev["text"]
    nudges = [m for b in world["seen"] for m in b["messages"] if m["role"] == "user" and "Before you finish" in str(m["content"])]
    assert nudges and "haven't run anything" in nudges[0]["content"]


def test_a_failing_last_command_sends_him_back_but_only_twice(fake, tmp_path):
    brain, world, ev = fake
    f = tmp_path / "bad.py"
    world["script"] = [("tool", ("write_file", {"path": str(f), "content": "raise SystemExit(3)\n"})),
                       ("tool", ("run_command", {"command": f"python3 {f}"}, "c2")),
                       ("text", "All good."), ("text", "All good."), ("text", "It still exits with code 3.")]
    run(brain, "make bad.py work")
    assert ev["done"].get("error") is None and "still exits" in ev["text"]
