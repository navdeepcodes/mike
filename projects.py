"""Projects: find the code folders on this Mac, pick the one being worked on, and summarise it for the model and the Code view."""
import difflib
import json
import os
import re
import subprocess
from pathlib import Path

import config

HOME = Path.home()
SETTINGS = config.DATA / "settings.json"
ROOTS = [HOME, HOME / "Documents", HOME / "Developer", HOME / "code", HOME / "projects", HOME / "src", HOME / "Desktop"]
MARKERS = (".git", "package.json", "pyproject.toml", "requirements.txt", "Package.swift", "Cargo.toml", "go.mod", "Makefile", "pom.xml")
SKIP = {"node_modules", ".venv", "venv", "__pycache__", "Library", "Applications", "Movies", "Music", "Pictures", "Public", ".Trash"}


def _is_project(p: Path) -> bool:
    return p.is_dir() and any((p / m).exists() for m in MARKERS)


def scan() -> list[Path]:
    """Project folders under the usual places (two levels deep)."""
    found: dict[str, Path] = {}
    for root in ROOTS:
        if not root.is_dir():
            continue
        try:
            level1 = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP]
        except OSError:
            continue
        for p in level1:
            if _is_project(p):
                found[str(p)] = p
            elif root != HOME:
                try:
                    for q in p.iterdir():
                        if q.is_dir() and not q.name.startswith(".") and q.name not in SKIP and _is_project(q):
                            found[str(q)] = q
                except OSError:
                    pass
    return sorted(found.values(), key=lambda p: -p.stat().st_mtime)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find(query: str, limit: int = 6) -> list[Path]:
    """Best matches for a spoken name ("mike", "the tv assistant"), most likely first."""
    projects = scan()
    q = _norm(query)
    if not q:
        return projects[:limit]
    scored = []
    for p in projects:
        n = _norm(p.name)
        score = 1.0 if n == q else 0.9 if q in n or n in q else difflib.SequenceMatcher(None, q, n).ratio()
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for s, p in scored if s >= 0.5][:limit]


def _git(path: Path, *args: str, strip: bool = True) -> str:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return ""
        return r.stdout.strip() if strip else r.stdout
    except Exception:
        return ""


def current() -> Path | None:
    try:
        p = Path(json.loads(SETTINGS.read_text()).get("project", ""))
        return p if p.is_dir() else None
    except Exception:
        return None


def use(path: str) -> str:
    p = Path(os.path.expanduser(path)).resolve()
    if not p.is_dir():
        return f"{path} isn't a folder."
    try:
        data = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    except Exception:
        data = {}
    data["project"] = str(p)
    SETTINGS.write_text(json.dumps(data, indent=2))
    return f"Working on {p.name} ({p})."


def stack_and_commands(p: Path) -> tuple[list[str], dict[str, str]]:
    stack, cmds = [], {}
    if (p / "package.json").exists():
        stack.append("Node")
        try:
            pkg = json.loads((p / "package.json").read_text())
            for k in ("dev", "start", "build", "test", "lint"):
                if k in (pkg.get("scripts") or {}):
                    cmds[k] = f"npm run {k}" if k not in ("start", "test") else f"npm {k}"
            deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
            stack += [n for n in ("react", "next", "vue", "svelte", "vite", "express", "electron") if n in deps]
        except Exception:
            pass
    if (p / "pyproject.toml").exists() or (p / "requirements.txt").exists() or any(p.glob("*.py")):
        stack.append("Python")
        if (p / "tests").is_dir() or any(p.glob("test_*.py")):
            cmds.setdefault("test", "python3 -m pytest -q")
        if (p / "server.py").exists():
            cmds.setdefault("run", "python3 server.py")
        elif (p / "main.py").exists():
            cmds.setdefault("run", "python3 main.py")
    if (p / "index.html").exists() and not (p / "package.json").exists():
        stack.append("Web"); cmds.setdefault("run", "python3 -m http.server 8000")
    if (p / "Package.swift").exists():
        stack.append("Swift"); cmds.setdefault("build", "swift build"); cmds.setdefault("test", "swift test")
    if (p / "Cargo.toml").exists():
        stack.append("Rust"); cmds.setdefault("build", "cargo build"); cmds.setdefault("test", "cargo test")
    if (p / "go.mod").exists():
        stack.append("Go"); cmds.setdefault("build", "go build ./..."); cmds.setdefault("test", "go test ./...")
    if (p / "Makefile").exists():
        stack.append("Make")
    return stack, cmds


def info(path: str = "") -> dict:
    p = Path(path) if path else current()
    if not p or not p.is_dir():
        return {}
    stack, cmds = stack_and_commands(p)
    status = _git(p, "status", "--porcelain", strip=False)
    dirty = [l[3:] for l in status.splitlines()][:12]
    return {
        "name": p.name, "path": str(p), "stack": stack, "commands": cmds,
        "branch": _git(p, "rev-parse", "--abbrev-ref", "HEAD"), "dirty": len(status.splitlines()), "dirty_files": dirty,
        "last_commit": _git(p, "log", "-1", "--pretty=%h %s (%cr)"),
    }


def describe(path: str = "") -> str:
    i = info(path)
    if not i:
        return "No project is selected. Use project_find then project_use."
    readme = ""
    for name in ("README.md", "README.txt", "README"):
        f = Path(i["path"]) / name
        if f.exists():
            readme = " ".join(f.read_text(errors="replace").split())[:400]
            break
    lines = [f"Project {i['name']} at {i['path']}", f"Stack: {', '.join(i['stack']) or 'unknown'}"]
    if i["branch"]:
        lines.append(f"Git: branch {i['branch']}, {i['dirty']} changed file(s); last commit {i['last_commit'] or 'none'}")
        if i["dirty_files"]:
            lines.append("Changed: " + ", ".join(i["dirty_files"]))
    if i["commands"]:
        lines.append("Commands: " + "; ".join(f"{k}: {v}" for k, v in i["commands"].items()))
    if readme:
        lines.append("README: " + readme)
    return "\n".join(lines)


# ---- creating projects, browsing files, diffs and commits (for the Code view) ---------------------------
PROJECTS_HOME = HOME / "Documents" / "Projects"
TEMPLATES = {
    "empty": ("Empty", {"README.md": "# {name}\n"}),
    "python": ("Python", {"README.md": "# {name}\n", "main.py": "def main():\n    print(\"Hello from {name}\")\n\n\nif __name__ == \"__main__\":\n    main()\n",
                          ".gitignore": ".venv/\n__pycache__/\n*.pyc\n.env\n"}),
    "node": ("Node", {"README.md": "# {name}\n", "index.js": "console.log(\"Hello from {name}\");\n",
                      "package.json": "{\n  \"name\": \"{slug}\",\n  \"version\": \"0.1.0\",\n  \"scripts\": {\n    \"start\": \"node index.js\"\n  }\n}\n",
                      ".gitignore": "node_modules/\n.env\n"}),
    "website": ("Website", {"README.md": "# {name}\n", "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>{name}</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n  <h1>{name}</h1>\n  <script src=\"script.js\"></script>\n</body>\n</html>\n",
                         "style.css": "body { font-family: system-ui, sans-serif; margin: 2rem; }\n", "script.js": "",
                         ".gitignore": ".DS_Store\n"}),
}


def create(name: str, template: str = "empty", parent: str = "") -> tuple[bool, str]:
    """Makes a new project folder (with a few starter files and git), and returns (ok, path or the problem)."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")
    if not slug:
        return False, "Give the project a name."
    base = Path(os.path.expanduser(parent)) if parent else PROJECTS_HOME
    folder = base / slug
    if folder.exists():
        return False, f"{folder} already exists."
    label, files = TEMPLATES.get(template, TEMPLATES["empty"])
    try:
        folder.mkdir(parents=True)
        for fname, body in files.items():
            (folder / fname).write_text(body.replace("{name}", name).replace("{slug}", slug))   # not str.format: CSS and JSON are full of braces
        subprocess.run(["git", "-C", str(folder), "init", "-q", "-b", "main"], capture_output=True, timeout=10)
        subprocess.run(["git", "-C", str(folder), "add", "-A"], capture_output=True, timeout=10)
        subprocess.run(["git", "-C", str(folder), "commit", "-q", "-m", "Start the project"], capture_output=True, timeout=10)
    except Exception as e:
        return False, f"Couldn't create it: {e}"
    use(str(folder))
    return True, str(folder)


def files(path: str = "", limit: int = 1500) -> list[str]:
    """Files in the project (git's list when it's a repo, else a walk), relative paths, shallow ones first."""
    p = Path(path) if path else current()
    if not p or not p.is_dir():
        return []
    out = _git(p, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    if not out:
        for root, dirs, names in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
            out += [os.path.relpath(os.path.join(root, n), p) for n in names if not n.startswith(".")]
            if len(out) > limit:
                break
    return sorted(out, key=lambda f: (f.count("/"), f))[:limit]


def changes(path: str = "") -> list[dict]:
    """Changed files with added/removed line counts."""
    p = Path(path) if path else current()
    if not p or not p.is_dir():
        return []
    status = {}
    for line in _git(p, "status", "--porcelain", strip=False).splitlines():
        status[line[3:].strip().strip('"')] = line[:2].strip() or "M"
    stats = {}
    for line in _git(p, "diff", "HEAD", "--numstat").splitlines():
        a, b, f = (line.split("\t") + ["", "", ""])[:3]
        stats[f] = (int(a) if a.isdigit() else 0, int(b) if b.isdigit() else 0)
    out = []
    for f, s in status.items():
        add, rem = stats.get(f, (0, 0))
        if s in ("??", "A") and f not in stats:
            try:
                add = len((p / f).read_text(errors="replace").splitlines())
            except Exception:
                pass
        out.append({"file": f, "status": "A" if s in ("??", "A") else "D" if s == "D" else "M", "add": add, "del": rem})
    return sorted(out, key=lambda c: c["file"])


def diff(file: str, path: str = "") -> str:
    p = Path(path) if path else current()
    if not p:
        return ""
    text = _git(p, "diff", "HEAD", "--", file)
    if not text and (p / file).is_file():          # untracked: show it as all added
        body = (p / file).read_text(errors="replace").splitlines()[:400]
        text = f"--- /dev/null\n+++ b/{file}\n@@ -0,0 +1,{len(body)} @@\n" + "\n".join("+" + l for l in body)
    return text[:60000]


def read(file: str, path: str = "") -> str:
    p = Path(path) if path else current()
    target = (p / file).resolve() if p else None
    if not target or not str(target).startswith(str(p.resolve())) or not target.is_file():
        return ""
    try:
        return target.read_text(errors="replace")[:60000]
    except Exception:
        return ""


def commit(message: str, path: str = "") -> str:
    p = Path(path) if path else current()
    if not p:
        return "No project."
    message = (message or "").strip()
    if not message:
        return "Write a commit message."
    r = subprocess.run(["git", "-C", str(p), "add", "-A"], capture_output=True, text=True, timeout=15)
    r = subprocess.run(["git", "-C", str(p), "commit", "-m", message], capture_output=True, text=True, timeout=20)
    return "Committed." if r.returncode == 0 else (r.stdout + r.stderr).strip()[:300] or "Nothing to commit."
