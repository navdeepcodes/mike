"""Rules for the Code tab that don't need the running server: when to ask before acting, and which pasted
images to accept. Kept separate so they can be tested without starting Mike."""
import base64

# What "Ask first" covers: anything that changes files or runs a command (Claude Code's default).
CODE_ASK_TOOLS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "terminal_start", "terminal_send",
                  # the self-coding tools Claude Code can call through Mike's tool server
                  "code_write", "code_edit", "code_bash", "code_test", "code_lint", "code_type_check", "code_build",
                  "code_git_checkpoint", "code_subagent_spawn"}
MODES = ("ask", "risky")
MAX_IMAGES, MAX_IMAGE_B64 = 4, 7_000_000          # 4 images, each under ~5 MB


def tool_kind(tool: str) -> str:
    return tool.replace("mcp__mike__", "")


def should_ask(tool: str, sensitive: bool, code_live: bool, mode: str, allowed: set[str]) -> bool:
    """Ask the user first? Risky actions always; in the Code tab with "Ask first" on, every edit and command
    too, unless they chose "Allow for this session" for that kind of step."""
    if sensitive:
        return True
    kind = tool_kind(tool)
    return code_live and mode == "ask" and kind in CODE_ASK_TOOLS and kind not in allowed


def clean_images(raw) -> list[str]:
    """Pasted images: base64 JPEG only (the Code tab converts every paste to JPEG), at most MAX_IMAGES."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        b64 = str(item or "").split(",", 1)[-1].strip()
        if not b64 or len(b64) > MAX_IMAGE_B64:
            continue
        try:
            head = base64.b64decode(b64[:24] + "=" * (-len(b64[:24]) % 4))
        except Exception:
            continue
        if head.startswith(b"\xff\xd8"):
            out.append(b64)
        if len(out) >= MAX_IMAGES:
            break
    return out


LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


def request_ok(origin: str | None, host: str, port: int, bind_host: str = "127.0.0.1") -> bool:
    """Only Mike's own page (or a client that sends no Origin: the native app, his tools, curl) may talk to him.
    Any other website open in your browser could otherwise drive Mike, who runs commands (cross-site requests),
    and a Host other than loopback is a DNS-rebinding attempt."""
    if bind_host in LOOPBACK:
        name = (host or "").rsplit(":", 1)[0] if not (host or "").startswith("[") else (host or "").split("]")[0] + "]"
        if name not in LOOPBACK:
            return False
    if origin is None:
        return True
    return origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
