"""
Mike's Self-Coding Engine - Autonomous development loop with verification.
Replaces the basic self_check with a full coding agent capability.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import config

log = logging.getLogger("mike.self_coding")

# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool: str
    args: Dict[str, Any]
    output: str
    success: bool
    duration_ms: float
    error: Optional[str] = None


@dataclass
class TestResult:
    """Test execution results."""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total: int = 0
    output: str = ""
    returncode: int = 0
    framework: str = ""


@dataclass
class CodeChange:
    """A single file change."""
    file: str
    old_content: str
    new_content: str
    description: str


@dataclass
class ProjectContext:
    """Indexed project context for semantic retrieval."""
    language: str
    imports: Dict[str, List[str]]  # module -> list of imported modules
    exports: Dict[str, List[str]]  # module -> what imports it
    call_graph: Dict[str, List[str]]  # function -> functions it calls
    reverse_call_graph: Dict[str, List[str]]  # function -> functions that call it
    class_hierarchy: Dict[str, str]  # class -> parent class
    file_deps: Dict[str, Set[str]]  # file -> set of dependent files
    test_deps: Dict[str, Set[str]]  # test file -> dependent source files
    keywords: Dict[str, Set[str]]  # module -> set of key terms


@dataclass
class DevSession:
    """A development session with full context."""
    id: str
    goal: str
    project_path: Path
    start_time: float = field(default_factory=time.time)
    changes: List[CodeChange] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    test_results: List[TestResult] = field(default_factory=list)
    current_plan: List[str] = field(default_factory=list)
    step: int = 0
    max_steps: int = 50
    blocked: bool = False
    blocker_reason: str = ""
    project_context: Optional[ProjectContext] = None


# ============================================================================
# Project Detection & Tool Discovery
# ============================================================================

class ProjectDetector:
    """Detects project type, test framework, linters, build tools."""

    def __init__(self, project_path: Path):
        self.project_path = project_path

    def detect_all(self) -> Dict[str, Any]:
        """Detect everything about the project."""
        return {
            "language": self._detect_language(),
            "test_framework": self._detect_test_framework(),
            "linter": self._detect_linter(),
            "type_checker": self._detect_type_checker(),
            "build_tool": self._detect_build_tool(),
            "package_manager": self._detect_package_manager(),
            "test_command": self._get_test_command(),
            "lint_command": self._get_lint_command(),
            "type_check_command": self._get_type_check_command(),
            "build_command": self._get_build_command(),
            "dev_dependencies": self._get_dev_dependencies(),
        }

    def _detect_language(self) -> str:
        """Primary language of the project."""
        if (self.project_path / "pyproject.toml").exists() or (self.project_path / "requirements.txt").exists() or list(self.project_path.glob("*.py")):
            return "python"
        if (self.project_path / "package.json").exists():
            return "javascript"
        if (self.project_path / "Cargo.toml").exists():
            return "rust"
        if (self.project_path / "go.mod").exists():
            return "go"
        if (self.project_path / "Package.swift").exists():
            return "swift"
        return "unknown"

    def _pytest_available(self) -> bool:
        """pytest on PATH, in the project's own venv, or among Mike's packages (which a project venv Mike made
        can import). Only checking PATH picked unittest on machines where pytest lives in Mike's venv alone."""
        import importlib.util
        if shutil.which("pytest"):
            return True
        if any((self.project_path / v / config.VENV_BIN / "pytest").exists() for v in (".venv", "venv", "env")):
            return True
        return importlib.util.find_spec("pytest") is not None

    def _detect_test_framework(self) -> str:
        """Detect test framework."""
        lang = self._detect_language()
        if lang == "python":
            # pytest is a superset for ordinary unittest tests and also discovers
            # function-style test_* tests, which unittest discovery silently skips.
            has_test_files = bool(list(self.project_path.glob("test_*.py")) or list(self.project_path.glob("*_test.py")))
            if has_test_files and self._pytest_available():
                return "pytest"
            if (self.project_path / "pytest.ini").exists() or (self.project_path / "pyproject.toml").exists():
                content = (self.project_path / "pyproject.toml").read_text() if (self.project_path / "pyproject.toml").exists() else ""
                if "pytest" in content or (self.project_path / "pytest.ini").exists():
                    return "pytest"
            if (self.project_path / "unittest").exists() or list(self.project_path.glob("test_*.py")) or list(self.project_path.glob("*_test.py")):
                return "unittest"
            return "pytest"  # default
        if lang == "javascript":
            pkg = self._read_package_json()
            if pkg:
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "jest" in deps:
                    return "jest"
                if "vitest" in deps:
                    return "vitest"
                if "mocha" in deps:
                    return "mocha"
                if "@playwright/test" in deps:
                    return "playwright"
            return "jest"
        if lang == "rust":
            return "cargo"
        if lang == "go":
            return "go test"
        return "unknown"

    def _detect_linter(self) -> str:
        """Detect linter."""
        lang = self._detect_language()
        if lang == "python":
            if (self.project_path / ".ruff.toml").exists() or (self.project_path / "ruff.toml").exists() or "ruff" in self._read_pyproject():
                return "ruff"
            if (self.project_path / ".flake8").exists() or (self.project_path / "setup.cfg").exists():
                return "flake8"
            if (self.project_path / "pylintrc").exists():
                return "pylint"
            return "ruff"  # default modern
        if lang == "javascript":
            pkg = self._read_package_json()
            if pkg:
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "eslint" in deps:
                    return "eslint"
                if "biome" in deps:
                    return "biome"
            return "eslint"
        if lang == "rust":
            return "clippy"
        if lang == "go":
            return "golangci-lint"
        return "unknown"

    def _detect_type_checker(self) -> str:
        """Detect type checker."""
        lang = self._detect_language()
        if lang == "python":
            if (self.project_path / "pyrightconfig.json").exists() or "pyright" in self._read_pyproject():
                return "pyright"
            if (self.project_path / "mypy.ini").exists() or (self.project_path / "pyproject.toml").exists() and "mypy" in self._read_pyproject():
                return "mypy"
            return "pyright"  # default
        if lang == "javascript":
            pkg = self._read_package_json()
            if pkg:
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "typescript" in deps:
                    return "tsc"
            return "none"
        if lang == "rust":
            return "cargo check"
        return "unknown"

    def _detect_build_tool(self) -> str:
        """Detect build tool."""
        lang = self._detect_language()
        if lang == "python":
            if (self.project_path / "pyproject.toml").exists():
                content = self._read_pyproject()
                if "poetry" in content:
                    return "poetry"
                if "flit" in content:
                    return "flit"
                if "hatch" in content:
                    return "hatch"
            if (self.project_path / "setup.py").exists():
                return "setuptools"
            return "pip"
        if lang == "javascript":
            if (self.project_path / "pnpm-lock.yaml").exists():
                return "pnpm"
            if (self.project_path / "yarn.lock").exists():
                return "yarn"
            if (self.project_path / "package-lock.json").exists():
                return "npm"
            return "npm"
        if lang == "rust":
            return "cargo"
        if lang == "go":
            return "go build"
        return "unknown"

    def _detect_package_manager(self) -> str:
        return self._detect_build_tool()

    def _get_test_command(self) -> str:
        """Get the test command for this project."""
        fw = self._detect_test_framework()
        lang = self._detect_language()
        if lang == "python":
            if fw == "pytest":
                return "python -m pytest -v --tb=short"
            if fw == "unittest":
                return "python -m unittest discover -v"
        if lang == "javascript":
            if fw == "jest":
                return "npx jest --verbose"
            if fw == "vitest":
                return "npx vitest run --reporter=verbose"
            if fw == "mocha":
                return "npx mocha"
            if fw == "playwright":
                return "npx playwright test"
            return "npm test"
        if lang == "rust":
            return "cargo test"
        if lang == "go":
            return "go test ./... -v"
        return ""

    def _get_lint_command(self) -> str:
        """Get the lint command."""
        linter = self._detect_linter()
        lang = self._detect_language()
        if lang == "python":
            if linter == "ruff":
                return "ruff check ."
            if linter == "flake8":
                return "flake8 ."
            if linter == "pylint":
                return "pylint **/*.py"
        if lang == "javascript":
            if linter == "eslint":
                return "npx eslint ."
            if linter == "biome":
                return "npx biome check ."
        if lang == "rust":
            return "cargo clippy"
        if lang == "go":
            return "golangci-lint run"
        return ""

    def _get_type_check_command(self) -> str:
        """Get the type check command."""
        checker = self._detect_type_checker()
        lang = self._detect_language()
        if lang == "python":
            if checker == "pyright":
                return "pyright"
            if checker == "mypy":
                return "mypy ."
        if lang == "javascript":
            if checker == "tsc":
                return "npx tsc --noEmit"
        if lang == "rust":
            return "cargo check"
        return ""

    def _get_build_command(self) -> str:
        """Get the build command."""
        tool = self._detect_build_tool()
        lang = self._detect_language()
        if lang == "python":
            if tool == "poetry":
                return "poetry build"
            return "pip install -e ."
        if lang == "javascript":
            if tool in ("npm", "yarn", "pnpm"):
                return f"{tool} run build"
        if lang == "rust":
            return "cargo build --release"
        if lang == "go":
            return "go build ./..."
        return ""

    def _read_pyproject(self) -> str:
        try:
            return (self.project_path / "pyproject.toml").read_text()
        except Exception:
            return ""

    def _read_package_json(self) -> Optional[Dict]:
        try:
            return json.loads((self.project_path / "package.json").read_text())
        except Exception:
            return None

    def _get_dev_dependencies(self) -> List[str]:
        """Get dev dependencies."""
        lang = self._detect_language()
        if lang == "python":
            deps = []
            content = self._read_pyproject()
            for dep in ["pytest", "ruff", "mypy", "pyright", "black", "isort", "coverage"]:
                if dep in content:
                    deps.append(dep)
            return deps
        if lang == "javascript":
            pkg = self._read_package_json()
            if pkg:
                return list({**pkg.get("devDependencies", {})}.keys())
        return []


# ============================================================================
# Code Indexing Engine
# ============================================================================

class CodeIndexer:
    """Builds and maintains a semantic index of the codebase for intelligent retrieval."""

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.language = self._detect_language()
        self._cache: Dict[str, Any] = {}

    def _detect_language(self) -> str:
        if (self.project_path / "pyproject.toml").exists() or (self.project_path / "requirements.txt").exists() or list(self.project_path.glob("*.py")):
            return "python"
        if (self.project_path / "package.json").exists():
            return "javascript"
        if (self.project_path / "Cargo.toml").exists():
            return "rust"
        if (self.project_path / "go.mod").exists():
            return "go"
        if (self.project_path / "Package.swift").exists():
            return "swift"
        return "unknown"

    def index_project(self) -> ProjectContext:
        """Build a complete semantic index of the project."""
        if "project_context" in self._cache:
            return self._cache["project_context"]

        lang = self.language
        imports = {}
        exports = {}
        call_graph = {}
        reverse_call_graph = {}
        class_hierarchy = {}
        file_deps = {}
        test_deps = {}
        keywords = {}

        if lang == "python":
            self._index_python(imports, exports, call_graph, reverse_call_graph, class_hierarchy, file_deps, test_deps, keywords)
        elif lang == "javascript":
            self._index_javascript(imports, exports, call_graph, reverse_call_graph, class_hierarchy, file_deps, test_deps, keywords)

        context = ProjectContext(
            language=lang,
            imports=imports,
            exports=exports,
            call_graph=call_graph,
            reverse_call_graph=reverse_call_graph,
            class_hierarchy=class_hierarchy,
            file_deps=file_deps,
            test_deps=test_deps,
            keywords=keywords,
        )
        self._cache["project_context"] = context
        return context

    def _index_python(self, imports, exports, call_graph, reverse_call_graph, class_hierarchy, file_deps, test_deps, keywords):
        """Index Python files for imports, calls, classes, and keywords."""
        py_files = list(self.project_path.glob("**/*.py"))
        py_files = [f for f in py_files if ".venv" not in f.parts and "__pycache__" not in f.parts]

        import_pattern = re.compile(r"^(?:from\s+(\S+)\s+)?import\s+(.+)$", re.MULTILINE)
        call_pattern = re.compile(r"(\w+)\s*\(")
        class_pattern = re.compile(r"^class\s+(\w+)(?:\s*\(([^)]*)\))?:", re.MULTILINE)

        for py_file in py_files:
            try:
                content = py_file.read_text(errors="replace")
                rel_path = str(py_file.relative_to(self.project_path))

                # Extract imports
                file_imports = []
                for match in import_pattern.finditer(content):
                    module = match.group(1) or ""
                    names = match.group(2)
                    for name in names.split(","):
                        name = name.strip().split(" as ")[0].strip()
                        if name and name != "*":
                            file_imports.append(f"{module}.{name}" if module else name)
                imports[rel_path] = file_imports

                # Build reverse index
                for imp in file_imports:
                    if imp not in exports:
                        exports[imp] = []
                    exports[imp].append(rel_path)

                # Extract classes
                for match in class_pattern.finditer(content):
                    class_name = match.group(1)
                    parents = match.group(2).strip()
                    class_key = f"{rel_path}::{class_name}"
                    if parents:
                        for p in parents.split(","):
                            p = p.strip()
                            if p:
                                class_hierarchy[class_key] = f"{rel_path}::{p}" if not p.endswith(".") else p
                    keywords.setdefault(rel_path, set()).add(class_name)

                # Extract function calls (simplified)
                functions = re.findall(r"^def\s+(\w+)\s*\(", content, re.MULTILINE)
                for func in functions:
                    func_key = f"{rel_path}::{func}"
                    calls = call_pattern.findall(content)
                    call_graph[func_key] = [c for c in calls if c != func]

                # Build reverse call graph
                for caller, callees in call_graph.items():
                    for callee in callees:
                        reverse_call_graph.setdefault(callee, []).append(caller)

                # Keywords
                keywords.setdefault(rel_path, set()).update(functions)
                keywords[rel_path].update(re.findall(r"\b(self\.\w+)", content))
                keywords[rel_path].update(re.findall(r"\b(\w+)\s*=", content)[:20])

                # File dependencies (which other project files this imports)
                deps = set()
                for imp in file_imports:
                    for exp_file, exp_items in exports.items():
                        if imp.split(".")[-1] in exp_items or imp in exp_items:
                            deps.add(exp_file)
                file_deps[rel_path] = deps

                # Test dependencies
                if "test" in rel_path or rel_path.startswith("test_"):
                    test_deps[rel_path] = deps

            except Exception:
                continue

    def _index_javascript(self, imports, exports, call_graph, reverse_call_graph, class_hierarchy, file_deps, test_deps, keywords):
        """Index JavaScript/TypeScript files."""
        js_files = list(self.project_path.glob("**/*.{js,ts,jsx,tsx}"))
        js_files = [f for f in js_files if "node_modules" not in f.parts and ".git" not in f.parts]

        import_pattern = re.compile(r"import\s+(?:\w+\s*,\s*)?(\{.*?\}|\w+)\s+from\s+['\"]([^'\"]+)['\"]", re.MULTILINE)
        export_pattern = re.compile(r"export\s+(?:default\s+)?(?:class|function|const|let|var)\s+(\w+)", re.MULTILINE)
        call_pattern = re.compile(r"(\w+)\s*\(")
        class_pattern = re.compile(r"(?:class|interface)\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE)

        for js_file in js_files:
            try:
                content = js_file.read_text(errors="replace")
                rel_path = str(js_file.relative_to(self.project_path))

                # Extract imports
                file_imports = []
                for match in import_pattern.finditer(content):
                    names = match.group(1)
                    module = match.group(2)
                    if names:
                        for n in names.replace("{", "").replace("}", "").split(","):
                            n = n.strip()
                            if n:
                                file_imports.append(f"{module}:{n}")
                imports[rel_path] = file_imports

                # Build reverse index
                for imp in file_imports:
                    if imp not in exports:
                        exports[imp] = []
                    exports[imp].append(rel_path)

                # Extract classes/interfaces
                for match in class_pattern.finditer(content):
                    class_name = match.group(1)
                    parent = match.group(2)
                    class_key = f"{rel_path}::{class_name}"
                    if parent:
                        class_hierarchy[class_key] = f"{rel_path}::{parent}"
                    keywords.setdefault(rel_path, set()).add(class_name)

                # Extract functions
                functions = re.findall(r"(?:function|const|let|var)\s+(\w+)\s*(?:=.*?)?\(", content)
                for func in functions:
                    func_key = f"{rel_path}::{func}"
                    calls = call_pattern.findall(content)
                    call_graph[func_key] = [c for c in calls if c != func]

                # Build reverse call graph
                for caller, callees in call_graph.items():
                    for callee in callees:
                        reverse_call_graph.setdefault(callee, []).append(caller)

                # Keywords
                keywords.setdefault(rel_path, set()).update(functions)

                # File dependencies
                deps = set()
                for imp in file_imports:
                    for exp_file, exp_items in exports.items():
                        if imp.split(":")[-1] in exp_items or imp in exp_items:
                            deps.add(exp_file)
                file_deps[rel_path] = deps

                # Test dependencies
                if "test" in rel_path or rel_path.startswith("__tests__"):
                    test_deps[rel_path] = deps

            except Exception:
                continue

    def retrieve_relevant(self, query: str, max_files: int = 10) -> List[Tuple[str, float]]:
        """Retrieve most relevant files for a query using keyword matching."""
        context = self.index_project()
        scores = {}

        query_terms = set(re.findall(r"\w+", query.lower()))
        for file_path, terms in context.keywords.items():
            score = len(query_terms & {t.lower() for t in terms})
            if score > 0:
                scores[file_path] = score

        # Sort by score
        sorted_files = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_files[:max_files]

    def get_dependents(self, file_path: str) -> List[str]:
        """Get files that depend on the given file."""
        context = self.index_project()
        return context.exports.get(file_path, [])

    def get_dependencies(self, file_path: str) -> List[str]:
        """Get files the given file depends on."""
        context = self.index_project()
        return context.file_deps.get(file_path, [])

    def get_callers(self, function: str) -> List[str]:
        """Get functions that call the given function."""
        context = self.index_project()
        return context.reverse_call_graph.get(function, [])

    def get_callees(self, function: str) -> List[str]:
        """Get functions called by the given function."""
        context = self.index_project()
        return context.call_graph.get(function, [])

    def get_test_files(self, source_file: str) -> List[str]:
        """Get test files that might test the given source file."""
        context = self.index_project()
        return context.test_deps.get(source_file, [])


# ============================================================================
# Tool Execution Engine
# ============================================================================

class ToolExecutor:
    """Executes development tools safely with proper context."""

    _path_locks: Dict[str, threading.RLock] = {}
    _path_locks_guard = threading.Lock()

    def __init__(self, project_path: Path, session: DevSession):
        self.project_path = project_path
        self.session = session
        self.venv_python = self._find_venv_python()

    @classmethod
    def _lock_for_path(cls, path: Path) -> threading.RLock:
        """Return a process-wide lock shared by executors editing the same path."""
        key = str(path.resolve())
        with cls._path_locks_guard:
            return cls._path_locks.setdefault(key, threading.RLock())

    def _is_mike(self) -> bool:
        return self.project_path.resolve() == config.ROOT.resolve()

    @staticmethod
    def _path_without_mike_venv(path: str) -> str:
        mike_bin = str(config.ROOT / ".venv" / config.VENV_BIN)
        return os.pathsep.join(p for p in path.split(os.pathsep) if p and Path(p).resolve() != Path(mike_bin).resolve())

    def _project_venv_bin(self) -> Optional[Path]:
        """The project's venv bin folder, created on first use (not for Mike's own folder, which has its own)."""
        venv = self.project_path / ".venv"
        bin_dir = venv / config.VENV_BIN
        if bin_dir.exists() or self._is_mike():
            return bin_dir if bin_dir.exists() else None
        base = getattr(sys, "_base_executable", "") or shutil.which("python3") or sys.executable
        try:
            subprocess.run([base, "-m", "venv", str(venv)], capture_output=True, timeout=120, check=True)
            # Let the new venv *see* Mike's packages (pytest, fastapi, httpx...) after its own, read-only: pip
            # installs land in the project venv and never uninstall anything outside it.
            import site
            # Mike's own packages: wherever the Python running Mike keeps them (his .venv when installed normally,
            # the system's on a CI machine). The link is read-only: installs land in the project's venv.
            mine = [p for p in site.getsitepackages() if Path(p).is_dir()]
            theirs = subprocess.run([str(bin_dir / "python"), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                                    capture_output=True, text=True, timeout=30).stdout.strip()
            if mine and theirs:
                (Path(theirs) / "_mike_shared.pth").write_text("\n".join(mine) + "\n")
        except Exception as e:
            log.warning("couldn't create a venv in %s: %s", self.project_path, e)
            return None
        return bin_dir if bin_dir.exists() else None

    def _inside_project(self, path: str) -> Path:
        """Resolve a path for writing; refuse anything outside the project folder."""
        p = Path(path)
        full = (p if p.is_absolute() else self.project_path / p).resolve()
        if not full.is_relative_to(self.project_path.resolve()):
            raise PermissionError(f"{path} is outside the project folder {self.project_path}; write inside it.")
        return full

    def _find_venv_python(self) -> str:
        """Find the project's Python interpreter."""
        venv = self.project_path / ".venv" / "bin" / "python"
        if venv.exists():
            return str(venv)
        # Try parent directories
        for parent in self.project_path.parents:
            venv = parent / ".venv" / "bin" / "python"
            if venv.exists():
                return str(venv)
        return sys.executable

    def _run_command(self, cmd: str, cwd: Optional[Path] = None, timeout: int = 120) -> ToolResult:
        """Run a shell command and return structured result."""
        start = time.time()
        work_dir = cwd or self.project_path
        try:
            # Commands run in the project's own venv. For any project other than Mike himself, Mike's venv is never
            # on PATH: a `pip install` there once downgraded pydantic and broke Mike. No venv yet? Make one.
            env = os.environ.copy()
            env.pop("VIRTUAL_ENV", None)
            project_venv = self._project_venv_bin()
            if project_venv:
                env["VIRTUAL_ENV"] = str(project_venv.parent)
                env["PATH"] = f"{project_venv}{os.pathsep}{self._path_without_mike_venv(env.get('PATH', ''))}"

            env["PYTHONPATH"] = str(work_dir) + ":" + env.get("PYTHONPATH", "")

            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(work_dir), env=env
            )
            duration = (time.time() - start) * 1000
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr

            return ToolResult(
                tool="bash",
                args={"command": cmd},
                output=output[:10000],
                success=result.returncode == 0,
                duration_ms=duration,
                error=result.stderr[:2000] if result.returncode != 0 else None
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool="bash", args={"command": cmd}, output="",
                success=False, duration_ms=timeout * 1000,
                error=f"Timeout after {timeout}s"
            )
        except Exception as e:
            return ToolResult(
                tool="bash", args={"command": cmd}, output="",
                success=False, duration_ms=(time.time() - start) * 1000,
                error=str(e)
            )

    def read_file(self, path: str, start: int = 0, end: int = 0) -> ToolResult:
        """Read a file. Supports both relative (to project) and absolute paths.
        Falls back to Mike's root if not found in current project."""
        start_time = time.time()
        try:
            path_obj = Path(path)
            # If absolute path, use as-is
            if path_obj.is_absolute():
                full_path = path_obj
            else:
                # Try relative to project_path first
                full_path = self.project_path / path
                # If not found, try Mike's root (for Mike's own files)
                if not full_path.exists() and self.project_path != config.ROOT:
                    full_path = config.ROOT / path
            if not full_path.exists():
                return ToolResult("read_file", {"path": path}, "", False,
                                (time.time() - start_time) * 1000, f"File not found: {path}")
            text = full_path.read_text(errors="replace")
            if start or end:
                lines = text.splitlines()
                a, b = max(1, start or 1), min(len(lines), end or len(lines))
                text = f"{path} lines {a}-{b} of {len(lines)}:\n" + "\n".join(f"{i}: {lines[i-1]}" for i in range(a, b+1))
            return ToolResult("read_file", {"path": path}, text[:20000], True,
                            (time.time() - start_time) * 1000)
        except Exception as e:
            return ToolResult("read_file", {"path": path}, "", False,
                            (time.time() - start_time) * 1000, str(e))

    def write_file(self, path: str, content: str) -> ToolResult:
        """Write a file. Supports both relative (to project) and absolute paths."""
        start_time = time.time()
        try:
            full_path = self._inside_project(path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock_for_path(full_path):
                old_content = ""
                if full_path.exists():
                    old_content = full_path.read_text(errors="replace")
                full_path.write_text(content)
            # Track change
            self.session.changes.append(CodeChange(path, old_content, content, f"Write {path}"))
            return ToolResult("write_file", {"path": path}, f"Wrote {len(content)} chars to {path}", True,
                            (time.time() - start_time) * 1000)
        except Exception as e:
            return ToolResult("write_file", {"path": path}, "", False,
                            (time.time() - start_time) * 1000, str(e))

    def edit_file(self, path: str, old: str, new: str, replace_all: bool = False) -> ToolResult:
        """Edit a file. Supports both relative (to project) and absolute paths."""
        start_time = time.time()
        try:
            full_path = self._inside_project(path)
            if not full_path.exists():
                return ToolResult("edit_file", {"path": path}, "", False,
                                (time.time() - start_time) * 1000, f"File not found: {path}")
            with self._lock_for_path(full_path):
                text = full_path.read_text(errors="replace")
                if old not in text:
                    return ToolResult("edit_file", {"path": path}, "", False,
                                    (time.time() - start_time) * 1000, f"Text not found in {path}")
                if replace_all:
                    new_text = text.replace(old, new)
                    count = text.count(old)
                else:
                    new_text = text.replace(old, new, 1)
                    count = 1
                full_path.write_text(new_text)
                if full_path.suffix == ".py":
                    for cache_file in (full_path.parent / "__pycache__").glob(f"{full_path.stem}.*.pyc"):
                        cache_file.unlink(missing_ok=True)
            self.session.changes.append(CodeChange(path, text, new_text, f"Edit {path} ({count} occurrence{'s' if count>1 else ''})"))
            return ToolResult("edit_file", {"path": path}, f"Edited {path} ({count} change{'s' if count>1 else ''})", True,
                            (time.time() - start_time) * 1000)
        except Exception as e:
            return ToolResult("edit_file", {"path": path}, "", False,
                            (time.time() - start_time) * 1000, str(e))

    def search_code(self, pattern: str, path: str = "", include_mike_root: bool = True) -> ToolResult:
        """Search code in the project. Supports absolute paths.
        If current project is not Mike's root and search returns empty, also search Mike's root."""
        start_time = time.time()
        try:
            if path:
                path_obj = Path(path)
                if path_obj.is_absolute():
                    root = path_obj
                else:
                    root = self.project_path / path
            else:
                root = self.project_path
            cmd = ["grep", "-rnIE", "--exclude-dir=.git", "--exclude-dir=node_modules",
                   "--exclude-dir=.venv", "--exclude-dir=__pycache__", "--exclude-dir=dist",
                   "--exclude-dir=build", "-m", "20", pattern, str(root)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr

            # If current project is not Mike's root and search returned empty, also search Mike's root
            if include_mike_root and not output.strip() and root != config.ROOT:
                cmd2 = ["grep", "-rnIE", "--exclude-dir=.git", "--exclude-dir=node_modules",
                       "--exclude-dir=.venv", "--exclude-dir=__pycache__", "--exclude-dir=dist",
                       "--exclude-dir=build", "-m", "20", pattern, str(config.ROOT)]
                result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
                if result2.stdout.strip():
                    output = f"(Also searched Mike's root)\n{result2.stdout}"
                    if result2.stderr:
                        output += "\n" + result2.stderr

            return ToolResult("search_code", {"pattern": pattern, "path": path},
                            output[:10000], result.returncode == 0,
                            (time.time() - start_time) * 1000)
        except Exception as e:
            return ToolResult("search_code", {"pattern": pattern}, "", False,
                            (time.time() - start_time) * 1000, str(e))

    def run_tests(self, detector: ProjectDetector) -> TestResult:
        """Run project tests and return structured results."""
        start_time = time.time()
        cmd = detector._get_test_command()
        if not cmd:
            return TestResult(0, 0, 0, 0, "No test command detected", 1, "none")

        result = self._run_command(cmd, timeout=300)
        tr = TestResult()
        tr.output = result.output
        tr.returncode = 0 if result.success else 1
        tr.framework = detector._detect_test_framework()

        # Parse test output
        output = result.output
        if tr.framework == "pytest":
            import re
            tr.passed = int(re.search(r"(\d+) passed", output).group(1)) if re.search(r"(\d+) passed", output) else output.count(" PASSED")
            tr.failed = int(re.search(r"(\d+) failed", output).group(1)) if re.search(r"(\d+) failed", output) else output.count(" FAILED")
            tr.errors = int(re.search(r"(\d+) errors?", output).group(1)) if re.search(r"(\d+) errors?", output) else output.count(" ERROR")
            tr.total = tr.passed + tr.failed + tr.errors
        elif tr.framework in ("jest", "vitest"):
            # Parse Jest/Vitest output
            import re
            passed_match = re.search(r"(\d+) passed", output)
            failed_match = re.search(r"(\d+) failed", output)
            tr.passed = int(passed_match.group(1)) if passed_match else 0
            tr.failed = int(failed_match.group(1)) if failed_match else 0
            tr.total = tr.passed + tr.failed
        elif tr.framework == "cargo":
            # cargo test output
            tr.passed = output.count("test ") - output.count("test result: FAILED")
            tr.failed = output.count("FAILED")
            tr.total = tr.passed + tr.failed
        else:
            # Generic: count lines that look like test results
            tr.passed = output.count("PASSED") + output.count("✓") + output.count("ok ")
            tr.failed = output.count("FAILED") + output.count("✗") + output.count("FAIL")
            tr.total = max(tr.passed + tr.failed, 1)

        self.session.test_results.append(tr)
        return tr

    def run_lint(self, detector: ProjectDetector) -> ToolResult:
        """Run linter."""
        cmd = detector._get_lint_command()
        if not cmd:
            return ToolResult("lint", {}, "No linter configured", True, 0)
        return self._run_command(cmd, timeout=120)

    def run_type_check(self, detector: ProjectDetector) -> ToolResult:
        """Run type checker."""
        cmd = detector._get_type_check_command()
        if not cmd:
            return ToolResult("type_check", {}, "No type checker configured", True, 0)
        return self._run_command(cmd, timeout=180)

    def run_build(self, detector: ProjectDetector) -> ToolResult:
        """Run build."""
        cmd = detector._get_build_command()
        if not cmd:
            return ToolResult("build", {}, "No build command configured", True, 0)
        return self._run_command(cmd, timeout=300)

    def git_status(self) -> ToolResult:
        return self._run_command("git status --porcelain")

    def git_diff(self, staged: bool = False) -> ToolResult:
        flag = "--cached" if staged else ""
        return self._run_command(f"git diff {flag}")

    def git_commit(self, message: str) -> ToolResult:
        return self._run_command(f'git add -A && git commit -m "{message}"')

    def git_checkpoint(self, label: str) -> ToolResult:
        """Create a git checkpoint."""
        branch = f"checkpoint/{label}-{int(time.time())}"
        self._run_command(f"git stash push -m '{label}'")
        return self._run_command(f"git checkout -b {branch} && git stash pop")


# ============================================================================
# Task Manager (Persistent task state & resume)
# ============================================================================

class TaskManager:
    """Manages persistent task state for resume across restarts."""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = session_dir / "task_state.json"
        self._load_state()

    def _load_state(self):
        """Load saved task state from disk."""
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text())
                self.active_goal = data.get("active_goal", "")
                self.completed_steps = data.get("completed_steps", [])
                self.pending_actions = data.get("pending_actions", [])
                self.task_history = data.get("task_history", [])
                self.resume_counter = data.get("resume_counter", 0)
                self.last_save = data.get("last_save", 0)
                self.notes = data.get("notes", "")
                self.status = data.get("status", "active" if self.active_goal else "idle")
            else:
                self.active_goal = ""
                self.completed_steps = []
                self.pending_actions = []
                self.task_history = []
                self.resume_counter = 0
                self.last_save = 0
                self.notes = ""
                self.status = "idle"
        except Exception:
            self.active_goal = ""
            self.completed_steps = []
            self.pending_actions = []
            self.task_history = []
            self.resume_counter = 0
            self.last_save = 0
            self.notes = ""
            self.status = "idle"

    def save_state(self):
        """Persist current task state to disk."""
        if self.active_goal and self.status == "idle":
            self.status = "active"
        data = {
            "active_goal": self.active_goal,
            "completed_steps": self.completed_steps,
            "pending_actions": self.pending_actions,
            "task_history": self.task_history,
            "resume_counter": self.resume_counter,
            "last_save": time.time(),
            "notes": self.notes,
            "status": self.status,
        }
        self.state_file.write_text(json.dumps(data, indent=2))

    def mark_step_complete(self, step_num: int, step_description: str):
        """Mark a step as completed and save state."""
        if step_num not in self.completed_steps:
            self.completed_steps.append(step_num)
        executor = getattr(self, "executor", None)
        session = getattr(executor, "session", None)
        self.task_history.append({
            "step": step_num,
            "description": step_description,
            "timestamp": time.time(),
            "changes": len(getattr(session, "changes", [])),
        })
        self.save_state()

    def add_pending_action(self, action: str, description: str):
        """Add a pending action."""
        self.pending_actions.append({
            "action": action,
            "description": description,
            "timestamp": time.time(),
        })
        self.save_state()

    def complete_pending_action(self, action: str) -> bool:
        """Mark a pending action as complete and remove it."""
        for i, pa in enumerate(self.pending_actions):
            if pa["action"] == action:
                self.pending_actions.pop(i)
                self.save_state()
                return True
        return False

    def get_pending_actions(self) -> List[Dict]:
        """Get all pending actions."""
        return self.pending_actions

    def has_pending_action(self, action: str) -> bool:
        """Check if a pending action exists."""
        return any(pa["action"] == action for pa in self.pending_actions)


# ============================================================================
# Autonomous Subagent System
# ============================================================================

class SubagentResult:
    """Result from a subagent execution."""
    def __init__(self, success: bool, output: str, changes: List[CodeChange] = None,
                 test_results: List[TestResult] = None, error: str = None):
        self.success = success
        self.output = output
        self.changes = changes or []
        self.test_results = test_results or []
        self.error = error


class Subagent:
    """A parallel subagent that can work on independent subtasks."""

    def __init__(self, agent_id: str, task_description: str, project_path: Path,
                 on_delta: Callable[[str], Awaitable[None]],
                 on_tool: Callable[[str, Dict], Awaitable[None]],
                 on_done: Callable[[Dict], Awaitable[None]],
                 on_permission: Callable[[str, Dict], Awaitable[bool]]):
        self.agent_id = agent_id
        self.task_description = task_description
        self.project_path = project_path
        self.on_delta = on_delta
        self.on_tool = on_tool
        self.on_done = on_done
        self.on_permission = on_permission
        self.executor = ToolExecutor(project_path, DevSession("sub", "subagent", project_path))
        self.detector = ProjectDetector(project_path)
        self.agent_self = SelfCodingAgent(
            project_path, on_delta, on_tool, on_done, on_permission
        )
        self.result: Optional[SubagentResult] = None
        self.completed = False

    async def run(self, subtask_goal: str, max_steps: int = 30) -> SubagentResult:
        """Run the subagent on a specific subtask."""
        try:
            self.result = await self.agent_self.run(subtask_goal, max_steps)
            self.completed = True
            blocked = bool(self.result.get("blocked"))
            reason = self.result.get("blocker_reason", "")
            return SubagentResult(
                success=not blocked,
                output=str(reason or f"Completed in {self.result.get('duration_sec', 0):.2f}s"),
                changes=list(self.result.get("files_changed", [])),
                test_results=self._extract_test_results(),
                error=reason if blocked else None,
            )
        except Exception as e:
            self.completed = True
            return SubagentResult(
                success=False,
                output="",
                error=str(e),
            )

    def _extract_test_results(self) -> List[TestResult]:
        """Extract test results from subagent's session."""
        if self.executor and self.executor.session and self.executor.session.test_results:
            return self.executor.session.test_results
        return []


# ============================================================================
# Enhanced SelfCodingAgent
# ============================================================================

class SelfCodingAgent:
    """Autonomous coding agent with subagent support, persistence, and verification."""

    def __init__(
        self,
        project_path: Path,
        on_delta: Callable[[str], Awaitable[None]],
        on_tool: Callable[[str, Dict], Awaitable[None]],
        on_done: Callable[[Dict], Awaitable[None]],
        on_permission: Callable[[str, Dict], Awaitable[bool]],
        model_driver: Optional[Callable[[str, Callable], Awaitable[str]]] = None,
    ):
        self.project_path = project_path
        self.on_delta = on_delta
        self.on_tool = on_tool
        self.on_done = on_done
        self.on_permission = on_permission
        self.model_driver = model_driver
        self.detector = ProjectDetector(project_path)
        self.session: Optional[DevSession] = None
        self.executor: Optional[ToolExecutor] = None
        self.max_steps = 50
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5

        # Task persistence
        self.task_dir: Optional[Path] = None
        self.task_manager: Optional[TaskManager] = None

        # Subagent system
        self.subagents: Dict[str, Subagent] = {}
        self.next_subagent_id = 1

        # Planning
        self.current_plan: List[str] = []
        self.plan_step: int = 0

    async def run(self, goal: str, max_steps: int = 50, task_dir: Optional[str] = None) -> Dict[str, Any]:
        """Run a model-driven coding turn, persist tool progress, and verify the result."""
        if task_dir:
            self.task_dir = Path(task_dir).resolve()
        else:
            project_key = hashlib.sha256(str(self.project_path.resolve()).encode()).hexdigest()[:16]
            goal_key = hashlib.sha256(goal.strip().encode()).hexdigest()[:16]
            self.task_dir = config.DATA / "coding_tasks" / project_key / goal_key
        self.task_manager = TaskManager(self.task_dir)
        if self.task_manager.active_goal and self.task_manager.active_goal != goal:
            return {"blocked": True, "blocker_reason": "Task directory contains a different saved goal; choose another task directory.",
                    "steps": 0, "changes": 0, "files_changed": [], "verification": {}}
        resuming = bool(self.task_manager.active_goal and self.task_manager.status != "completed")
        if self.task_manager.status == "completed":
            self.task_manager.completed_steps = []
            self.task_manager.pending_actions = []
            self.task_manager.task_history = []
            self.task_manager.notes = ""
        if resuming:
            await self.on_delta(f"\n[Resuming task: {goal[:80]}]\n")

        self.session = DevSession(
            id=str(uuid.uuid4())[:8],
            goal=goal,
            project_path=self.project_path,
            max_steps=max_steps
        )
        self.executor = ToolExecutor(self.project_path, self.session)
        self.task_manager.executor = self.executor

        self.max_steps = max(1, max_steps)
        self.task_manager.active_goal = goal
        self.task_manager.status = "active"
        saved_notes = self.task_manager.notes if resuming else ""
        self.task_manager.save_state()
        prompt = goal
        if saved_notes:
            prompt += f"\n\nSaved progress from the previous run:\n{saved_notes}\nContinue from the current project state; do not repeat completed work unless verification requires it."
        await self.on_delta("\n[Working on the coding task]\n")
        try:
            await self._run_model_driver(prompt)
        except Exception as exc:
            self.session.blocked = True
            self.session.blocker_reason = str(exc)[:500]
            await self.on_delta(f"\n[Blocked: {self.session.blocker_reason}]\n")

        if not self.session.blocked:
            await self._final_verification()
        self.task_manager.notes = self._progress_note()
        self.task_manager.status = "blocked" if self.session.blocked else "completed"
        self.task_manager.save_state()
        result = self._compile_result()
        try:
            await self.on_done(result)
        except Exception:
            log.exception("self-coding completion callback failed")
        return result

    async def _create_plan(self):
        """Retained as a compatibility hook for callers that inspect the plan."""
        self.current_plan = ["Inspect the project", "Implement the requested change", "Verify with tests"]

    async def _execute_step(self):
        """Compatibility wrapper: execute a full model turn for the active goal."""
        if self.session:
            await self._run_model_driver(self.session.goal)

    async def _run_model_driver(self, prompt: str) -> str:
        if self.model_driver:
            return await self.model_driver(prompt, self._execute_tool_call)

        import fallback

        agent = self

        class CodingBrain(fallback.FallbackBrain):
            def _tool_schemas(self, dev: bool) -> list[dict]:
                return [schema for schema in fallback.DEV_TOOLS
                        if schema["function"]["name"].startswith("code_")]

            async def _run(self) -> str:
                self.dev_until = time.time() + 20 * 60
                providers = self.available()
                if not providers:
                    raise RuntimeError("No backup model is configured for the self-coding agent")
                return await self._answer(providers, fallback.DEV_PROMPT)

            async def _call(self, name: str, raw: str) -> str:
                if not name.startswith("code_"):
                    return "This coding session only accepts project coding tools."
                try:
                    args = json.loads(raw or "{}")
                    if not isinstance(args, dict):
                        return "Tool arguments must be an object."
                except json.JSONDecodeError:
                    return "Tool arguments were invalid JSON."
                return await agent._execute_tool_call(name, args)

        brain = CodingBrain(self.on_delta, self.on_tool, self.on_done, self.on_permission)
        brain.history = [{"role": "user", "content": f"Project: {self.project_path.resolve()}\n\n{prompt}"}]
        return await brain._run()

    def _safe_project_path(self, raw_path: str) -> tuple[Path, str]:
        path = Path(raw_path) if raw_path else Path(".")
        target = path if path.is_absolute() else self.project_path / path
        root = self.project_path.resolve()
        resolved = target.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            raise ValueError("Path is outside the selected project")
        return resolved, relative.as_posix() or "."

    async def _execute_tool_call(self, name: str, args: Dict[str, Any]) -> str:
        if not self.session or not self.executor or not self.task_manager:
            return "Coding session is not initialized."
        if not name.startswith("code_"):
            return "Unsupported tool for this coding session."
        if self.session.step >= self.max_steps:
            self.session.blocked = True
            self.session.blocker_reason = f"Tool-call limit reached ({self.max_steps})"
            return self.session.blocker_reason
        self.session.step += 1
        try:
            allowed = await self.on_permission(name, args)
        except Exception as exc:
            return f"Permission check failed: {exc}"
        if allowed is not True:
            return allowed if isinstance(allowed, str) else "The user declined this action."
        await self.on_tool(name, args)
        try:
            output = await asyncio.to_thread(self._execute_tool_sync, name, args)
        except Exception as exc:
            output = f"Failed: {exc}"
        tool_result = ToolResult(name, dict(args), output[:10000], not output.startswith("Failed:"), 0,
                                 output[7:] if output.startswith("Failed:") else None)
        self.session.tool_results.append(tool_result)
        if self.task_manager:
            self.task_manager.mark_step_complete(self.session.step, f"{name}: {output[:300]}")
            self.task_manager.notes = self._progress_note()
            self.task_manager.save_state()
        return output

    def _execute_tool_sync(self, name: str, args: Dict[str, Any]) -> str:
        path = str(args.get("path", ""))
        if name in {"code_read", "code_write", "code_edit", "code_search", "code_search_relevant",
                    "code_dependents", "code_deps"} and path:
            _, path = self._safe_project_path(path)
        if name == "code_read":
            result = self.executor.read_file(path, int(args.get("start", 0)), int(args.get("end", 0)))
            return result.output if result.success else f"Failed: {result.error}"
        if name == "code_write":
            result = self.executor.write_file(path, str(args.get("content", "")))
            return result.output if result.success else f"Failed: {result.error}"
        if name == "code_edit":
            result = self.executor.edit_file(path, str(args.get("old", "")), str(args.get("new", "")),
                                             bool(args.get("replace_all", False)))
            return result.output if result.success else f"Failed: {result.error}"
        if name == "code_search":
            result = self.executor.search_code(str(args.get("pattern", "")), path, include_mike_root=False)
            return result.output if result.success else f"Failed: {result.error}"
        if name == "code_bash":
            cwd, _ = self._safe_project_path(str(args.get("cwd", "")))
            result = self.executor._run_command(str(args.get("command", "")), cwd, min(int(args.get("timeout", 120)), 300))
            return result.output if result.success else f"Failed: {result.error or result.output}"
        if name == "code_test":
            result = self.executor.run_tests(self.detector)
            return f"Tests: {result.passed}/{result.total} passed ({result.failed} failed, {result.errors} errors)\n{result.output[-5000:]}"
        if name in {"code_lint", "code_type_check", "code_build"}:
            operation = {"code_lint": self.executor.run_lint, "code_type_check": self.executor.run_type_check,
                         "code_build": self.executor.run_build}[name]
            result = operation(self.detector)
            return result.output if result.success else f"Failed: {result.error or result.output}"
        if name == "code_git_status":
            return self.executor.git_status().output
        if name == "code_git_diff":
            return self.executor.git_diff(bool(args.get("staged", False))).output
        if name == "code_git_checkpoint":
            result = self.executor.git_checkpoint(str(args.get("label", "checkpoint")))
            return result.output if result.success else f"Failed: {result.error or result.output}"
        if name in {"code_task_save", "code_task_load"}:
            if name == "code_task_save":
                goal = str(args.get("goal") or self.session.goal)
                self.task_manager.active_goal = goal
                step = int(args.get("step", self.session.step))
                if step > 0 and step not in self.task_manager.completed_steps:
                    self.task_manager.completed_steps.append(step)
                self.task_manager.notes = str(args.get("notes", self.task_manager.notes))
                self.task_manager.status = "active"
                self.task_manager.save_state()
                return f"Task state saved: {goal} (through step {step})"
            return (f"Resumed task: {self.task_manager.active_goal}\nStatus: {self.task_manager.status}\nCompleted steps: {self.task_manager.completed_steps}\n"
                    f"Notes: {self.task_manager.notes}") if self.task_manager.active_goal else "No saved task state found."
        if name == "code_subagent_spawn":
            from subagents import spawn
            return spawn(self.project_path, str(args.get("task_description", "")))
        if name == "code_subagent_results":
            from subagents import results
            return results(str(args.get("agent_id", "")))
        if name == "code_index":
            from self_coding import CodeIndexer
            context = CodeIndexer(self.project_path).index_project()
            return (f"Language: {context.language}\nFiles indexed: {len(context.keywords)}\n"
                    f"Dependencies: {len(context.file_deps)}\nCall graph entries: {len(context.call_graph)}")
        if name in {"code_dependents", "code_deps", "code_callers", "code_callees", "code_search_relevant"}:
            from self_coding import CodeIndexer
            indexer = CodeIndexer(self.project_path)
            if name == "code_search_relevant":
                return str(indexer.retrieve_relevant(str(args.get("query", "")), int(args.get("max_files", 10))))
            context = indexer.index_project()
            key = str(args.get("path", "")) if name in {"code_dependents", "code_deps"} else str(args.get("func", ""))
            table = {"code_dependents": context.exports, "code_deps": context.file_deps,
                     "code_callers": context.reverse_call_graph, "code_callees": context.call_graph}[name]
            return str(table.get(key, []))
        if name == "code_self_check":
            from self_coding import enhanced_self_check
            return enhanced_self_check(str(self.project_path))
        return f"Unsupported tool: {name}"

    def _progress_note(self) -> str:
        recent = self.session.tool_results[-12:] if self.session else []
        lines = [f"Goal: {self.session.goal}"] if self.session else []
        lines.extend(f"{item.tool}: {item.output[:300]}" for item in recent)
        return "\n".join(lines)[-6000:]

    async def _verify_changes(self):
        """Run verification after changes."""
        if not self.executor:
            return

        # Run tests
        test_result = self.executor.run_tests(self.detector)
        await self.on_tool("test", {"command": self.detector._get_test_command()})

        if test_result.failed > 0 or test_result.errors > 0:
            self.consecutive_failures += 1
            await self.on_delta(f"\n[Tests: {test_result.passed}/{test_result.total} passed, {test_result.failed} failed]\n")
        else:
            self.consecutive_failures = 0
            await self.on_delta(f"\n[All {test_result.passed} tests passed]\n")

        # Run linter
        lint_result = self.executor.run_lint(self.detector)
        await self.on_tool("lint", {"command": self.detector._get_lint_command()})

        # Run type checker
        type_result = self.executor.run_type_check(self.detector)
        await self.on_tool("type_check", {"command": self.detector._get_type_check_command()})

        # Record in task manager if available
        if self.task_manager:
            self.task_manager.mark_step_complete(self.session.step, self.current_plan[self.session.step - 1] if self.session.step <= len(self.current_plan) else "step")

    async def _final_verification(self):
        """Final comprehensive verification."""
        if not self.executor:
            return
        self.verification = {}
        checks = [("tests", "code_test", self.executor.run_tests),
                  ("lint", "code_lint", self.executor.run_lint),
                  ("type_check", "code_type_check", self.executor.run_type_check)]
        for label, tool_name, operation in checks:
            command = {"tests": self.detector._get_test_command(), "lint": self.detector._get_lint_command(),
                       "type_check": self.detector._get_type_check_command()}[label]
            if not command:
                self.verification[label] = {"status": "not_configured"}
                continue
            executable = command.split()[0]
            local_bins = (self.project_path / ".venv" / "bin" / executable,
                          config.ROOT / ".venv" / "bin" / executable)
            if not shutil.which(executable) and not any(candidate.exists() for candidate in local_bins):
                self.verification[label] = {"status": "not_installed", "command": command}
                continue
            allowed = await self.on_permission(tool_name, {"command": command, "final_verification": True})
            if allowed is not True:
                self.verification[label] = {"status": "not_run", "reason": str(allowed or "permission declined")}
                continue
            await self.on_tool(tool_name, {"command": command, "final_verification": True})
            result = await asyncio.to_thread(operation, self.detector)
            if label == "tests":
                passed = result.returncode == 0 and result.failed == 0 and result.errors == 0 and result.total > 0
                self.verification[label] = {"status": "passed" if passed else "failed", "passed": result.passed,
                                            "failed": result.failed, "errors": result.errors,
                                            "returncode": result.returncode, "output": result.output[-3000:]}
            else:
                self.verification[label] = {"status": "passed" if result.success else "failed",
                                            "output": result.output[-1500:], "error": result.error}
            status = self.verification[label]["status"]
            await self.on_delta(f"[{label.replace('_', ' ').title()}: {status}]\n")

        tests = self.verification.get("tests", {})
        if tests.get("status") != "passed":
            self.session.blocked = True
            self.session.blocker_reason = "Final test verification did not pass; review the verification results before treating the task as complete."
        failures = [name for name in ("lint", "type_check") if self.verification.get(name, {}).get("status") == "failed"]
        if failures:
            self.session.blocked = True
            self.session.blocker_reason = "Verification failed: " + ", ".join(failures)

    async def _run_regression_check(self):
        """Run existing tests to ensure no regressions."""
        if not self.executor or not self.session:
            return
        # Get all test files and run them
        test_cmd = self.detector._get_test_command()
        if test_cmd:
            result = self.executor._run_command(test_cmd, timeout=300)
            if result.success:
                await self.on_delta("[Regression: no regressions detected]\n")
            else:
                await self.on_delta("[WARNING: Possible regressions detected]\n")

    async def _check_unintended_modifications(self):
        """Check if any files were modified unintentionally."""
        if not self.executor:
            return
        git_result = self.executor.git_status()
        if git_result.output.strip():
            await self.on_delta(f"[Git Status: {git_result.output[:500]}]\n")

    async def spawn_subagent(self, task_description: str) -> str:
        """Spawn a parallel subagent for an independent subtask."""
        agent_id = f"sub_{self.next_subagent_id}"
        self.next_subagent_id += 1

        subagent = Subagent(
            agent_id=agent_id,
            task_description=task_description,
            project_path=self.project_path,
            on_delta=self.on_delta,
            on_tool=self.on_tool,
            on_done=self.on_done,
            on_permission=self.on_permission,
        )

        self.subagents[agent_id] = subagent

        # Run the subagent
        asyncio.create_task(self._run_subagent(subagent, task_description))
        return agent_id

    async def _run_subagent(self, subagent: Subagent, task_description: str):
        """Run a subagent and capture its result."""
        try:
            subagent.result = await subagent.run(task_description, max_steps=30)
        except Exception as e:
            subagent.result = SubagentResult(success=False, error=str(e))

    async def collect_subagent_results(self) -> Dict[str, SubagentResult]:
        """Collect results from all completed subagents."""
        results = {}
        for agent_id, subagent in self.subagents.items():
            if subagent.completed and subagent.result:
                results[agent_id] = subagent.result
        return results

    def _compile_result(self) -> Dict[str, Any]:
        """Compile final result."""
        base_result = {
            "session_id": self.session.id if self.session else "",
            "goal": self.session.goal if self.session else "",
            "steps": self.session.step if self.session else 0,
            "changes": len(self.session.changes) if self.session else 0,
            "files_changed": [c.file for c in self.session.changes] if self.session else [],
            "blocked": self.session.blocked if self.session else False,
            "blocker_reason": self.session.blocker_reason if self.session else "",
            "duration_sec": time.time() - (self.session.start_time if self.session else time.time()),
            "verification": getattr(self, "verification", {}),
        }

        # Add task manager state
        if self.task_manager:
            base_result["task_persistence"] = {
                "status": self.task_manager.status,
                "completed_steps": self.task_manager.completed_steps,
                "pending_actions": [pa["action"] for pa in self.task_manager.get_pending_actions()],
                "task_history_count": len(self.task_manager.task_history),
            }

        # Add subagent results
        subagent_results = {}
        for agent_id, subagent in self.subagents.items():
            if subagent.result:
                subagent_results[agent_id] = {
                    "success": subagent.result.success,
                    "output": subagent.result.output[:200] if subagent.result.output else "",
                    "error": subagent.result.error,
                    "changes_count": len(subagent.result.changes),
                }
        base_result["subagent_results"] = subagent_results

        return base_result

    async def _explore_project(self):
        """Explore the project structure."""
        # Read key files
        for fname in ["README.md", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"]:
            if (self.project_path / fname).exists():
                result = self.executor.read_file(fname)
                await self.on_tool("read_file", {"path": fname})
                self.session.tool_results.append(result)

        # List project files
        result = self.executor._run_command("find . -type f -name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.rs' -o -name '*.go' | head -50")
        await self.on_tool("bash", {"command": result.args["command"]})
        self.session.tool_results.append(result)

        # Detect project details
        details = self.detector.detect_all()
        await self.on_delta(f"\n[Project: {details['language']}, Tests: {details['test_framework']}, Linter: {details['linter']}, Type checker: {details['type_checker']}]\n")

    async def _create_plan(self):
        """Create an initial plan using the AI."""
        # This will be filled in by the actual AI interaction
        # For now, we create a basic plan structure
        self.session.current_plan = [
            "Understand the codebase and requirements",
            "Implement the solution",
            "Run tests to verify",
            "Fix any failing tests",
            "Run linter and type checker",
            "Final verification"
        ]

    async def _execute_step(self):
        """Execute one step of the plan."""
        if self.session.step >= len(self.session.current_plan):
            # We've completed the plan - verify and finish
            await self._verify_changes()
            return

        step_desc = self.session.current_plan[self.session.step - 1] if self.session.step > 0 else self.session.current_plan[0]
        await self.on_delta(f"\n[Step {self.session.step}/{self.session.max_steps}: {step_desc}]\n")

        await self._run_model_driver(self.session.goal)

    async def _verify_changes(self):
        """Run verification after changes."""
        if not self.executor:
            return

        # Run tests
        test_result = self.executor.run_tests(self.detector)
        await self.on_tool("test", {"command": self.detector._get_test_command()})

        if test_result.failed > 0 or test_result.errors > 0:
            self.consecutive_failures += 1
            await self.on_delta(f"\n[Tests: {test_result.passed}/{test_result.total} passed, {test_result.failed} failed]\n")
            # Could trigger fix loop here
        else:
            self.consecutive_failures = 0
            await self.on_delta(f"\n[All {test_result.passed} tests passed]\n")

        # Run linter
        lint_result = self.executor.run_lint(self.detector)
        await self.on_tool("lint", {"command": self.detector._get_lint_command()})

        # Run type checker
        type_result = self.executor.run_type_check(self.detector)
        await self.on_tool("type_check", {"command": self.detector._get_type_check_command()})

    async def _final_verification(self):
        """Final comprehensive verification."""
        if not self.executor:
            return
        self.verification = {}
        checks = [("tests", "code_test", self.executor.run_tests, self.detector._get_test_command()),
                  ("lint", "code_lint", self.executor.run_lint, self.detector._get_lint_command()),
                  ("type_check", "code_type_check", self.executor.run_type_check, self.detector._get_type_check_command())]
        for label, tool_name, operation, command in checks:
            if not command:
                self.verification[label] = {"status": "not_configured"}
                continue
            executable = command.split()[0]
            local_bins = (self.project_path / ".venv" / "bin" / executable,
                          config.ROOT / ".venv" / "bin" / executable)
            if not shutil.which(executable) and not any(candidate.exists() for candidate in local_bins):
                self.verification[label] = {"status": "not_installed", "command": command}
                continue
            allowed = await self.on_permission(tool_name, {"command": command, "final_verification": True})
            if allowed is not True:
                self.verification[label] = {"status": "not_run", "reason": str(allowed or "permission declined")}
                continue
            await self.on_tool(tool_name, {"command": command, "final_verification": True})
            result = await asyncio.to_thread(operation, self.detector)
            if label == "tests":
                passed = result.returncode == 0 and result.failed == 0 and result.errors == 0 and result.total > 0
                self.verification[label] = {"status": "passed" if passed else "failed", "passed": result.passed,
                                            "failed": result.failed, "errors": result.errors,
                                            "returncode": result.returncode, "output": result.output[-3000:]}
            else:
                self.verification[label] = {"status": "passed" if result.success else "failed",
                                            "output": result.output[-1500:], "error": result.error}
            await self.on_delta(f"[{label.replace('_', ' ').title()}: {self.verification[label]['status']}]\n")
        if self.verification.get("tests", {}).get("status") != "passed":
            self.session.blocked = True
            self.session.blocker_reason = "Final test verification did not pass or was not authorized."
        failures = [name for name in ("lint", "type_check") if self.verification.get(name, {}).get("status") == "failed"]
        if failures:
            self.session.blocked = True
            self.session.blocker_reason = "Verification failed: " + ", ".join(failures)

    def _compile_result(self) -> Dict[str, Any]:
        """Compile final result."""
        return {
            "session_id": self.session.id,
            "goal": self.session.goal,
            "steps": self.session.step,
            "changes": len(self.session.changes),
            "files_changed": [c.file for c in self.session.changes],
            "test_results": [
                {"passed": tr.passed, "failed": tr.failed, "total": tr.total, "framework": tr.framework}
                for tr in self.session.test_results
            ],
            "blocked": self.session.blocked,
            "blocker_reason": self.session.blocker_reason,
            "verification": getattr(self, "verification", {}),
            "task_persistence": {"status": self.task_manager.status,
                                 "completed_steps": self.task_manager.completed_steps,
                                 "task_history_count": len(self.task_manager.task_history)} if self.task_manager else {},
            "duration_sec": time.time() - self.session.start_time,
        }

    # ========================================================================
    # Tool methods that the AI can call
    # ========================================================================

    async def tool_read(self, path: str, start: int = 0, end: int = 0) -> ToolResult:
        """Read a file."""
        result = self.executor.read_file(path, start, end)
        await self.on_tool("read_file", {"path": path, "start": start, "end": end})
        self.session.tool_results.append(result)
        return result

    async def tool_write(self, path: str, content: str) -> ToolResult:
        """Write a file."""
        result = self.executor.write_file(path, content)
        await self.on_tool("write_file", {"path": path})
        self.session.tool_results.append(result)
        return result

    async def tool_edit(self, path: str, old: str, new: str, replace_all: bool = False) -> ToolResult:
        """Edit a file."""
        result = self.executor.edit_file(path, old, new, replace_all)
        await self.on_tool("edit_file", {"path": path, "replace_all": replace_all})
        self.session.tool_results.append(result)
        return result

    async def tool_search(self, pattern: str, path: str = "") -> ToolResult:
        """Search code."""
        result = self.executor.search_code(pattern, path)
        await self.on_tool("search_code", {"pattern": pattern, "path": path})
        self.session.tool_results.append(result)
        return result

    async def tool_bash(self, command: str, cwd: str = "", timeout: int = 120) -> ToolResult:
        """Run a shell command."""
        work_dir = self.project_path / cwd if cwd else self.project_path
        result = self.executor._run_command(command, cwd=work_dir, timeout=timeout)
        await self.on_tool("bash", {"command": command, "cwd": cwd})
        self.session.tool_results.append(result)
        return result

    async def tool_test(self) -> TestResult:
        """Run tests."""
        result = self.executor.run_tests(self.detector)
        await self.on_tool("test", {"command": self.detector._get_test_command()})
        return result

    async def tool_lint(self) -> ToolResult:
        """Run linter."""
        result = self.executor.run_lint(self.detector)
        await self.on_tool("lint", {"command": self.detector._get_lint_command()})
        return result

    async def tool_type_check(self) -> ToolResult:
        """Run type checker."""
        result = self.executor.run_type_check(self.detector)
        await self.on_tool("type_check", {"command": self.detector._get_type_check_command()})
        return result

    async def tool_build(self) -> ToolResult:
        """Run build."""
        result = self.executor.run_build(self.detector)
        await self.on_tool("build", {"command": self.detector._get_build_command()})
        return result

    async def tool_git_status(self) -> ToolResult:
        result = self.executor.git_status()
        await self.on_tool("bash", {"command": "git status --porcelain"})
        return result

    async def tool_git_diff(self, staged: bool = False) -> ToolResult:
        result = self.executor.git_diff(staged)
        await self.on_tool("bash", {"command": f"git diff {'--cached' if staged else ''}"})
        return result

    async def tool_git_checkpoint(self, label: str) -> ToolResult:
        result = self.executor.git_checkpoint(label)
        await self.on_tool("bash", {"command": f"git checkpoint {label}"})
        return result


# ============================================================================
# Integration with Mike's existing system
# ============================================================================

async def run_self_coding_task(
    goal: str,
    project_path: Optional[str] = None,
    on_delta: Callable[[str], Awaitable[None]] = None,
    on_tool: Callable[[str, Dict], Awaitable[None]] = None,
    on_done: Callable[[Dict], Awaitable[None]] = None,
    on_permission: Callable[[str, Dict], Awaitable[bool]] = None,
    max_steps: int = 50,
) -> Dict[str, Any]:
    """Entry point for running a self-coding task."""
    # Default callbacks
    if on_delta is None:
        async def on_delta(t): pass
    if on_tool is None:
        async def on_tool(n, a): pass
    if on_done is None:
        async def on_done(d): pass
    if on_permission is None:
        async def on_permission(t, a): return True

    # Determine project path
    if project_path:
        path = Path(project_path).resolve()
    else:
        # Use current project from Mike's project system
        import projects
        current = projects.current()
        path = Path(current) if current else config.ROOT

    agent = SelfCodingAgent(path, on_delta, on_tool, on_done, on_permission)
    return await agent.run(goal, max_steps)


# Backward compatibility: enhanced self_check
def enhanced_self_check(path: str = "") -> str:
    """Enhanced self_check that runs full verification."""
    project_path = Path(path) if path else config.ROOT
    detector = ProjectDetector(project_path)
    executor = ToolExecutor(project_path, DevSession("check", "verify", project_path))

    results = []

    # Python compilation
    py_files = list(project_path.glob("*.py"))
    for f in py_files:
        try:
            import py_compile
            py_compile.compile(str(f), doraise=True)
        except Exception as e:
            results.append(f"❌ {f.name}: {e}")

    # JS syntax check
    node = shutil.which("node")
    if node:
        for f in project_path.glob("static/*.js"):
            result = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=20)
            if result.returncode:
                results.append(f"❌ {f.name}: {result.stderr.strip()[:200]}")

    # Run detected tests
    test_cmd = detector._get_test_command()
    if test_cmd:
        result = executor._run_command(test_cmd, timeout=120)
        if result.success:
            results.append(f"✅ Tests passed")
        else:
            results.append(f"❌ Tests failed: {result.output[-500:]}")

    # Run linter
    lint_cmd = detector._get_lint_command()
    if lint_cmd:
        result = executor._run_command(lint_cmd, timeout=60)
        if result.success:
            results.append(f"✅ Lint passed")
        else:
            results.append(f"❌ Lint failed: {result.output[-500:]}")

    # Run type checker
    type_cmd = detector._get_type_check_command()
    if type_cmd:
        result = executor._run_command(type_cmd, timeout=60)
        if result.success:
            results.append(f"✅ Type check passed")
        else:
            results.append(f"❌ Type check failed: {result.output[-500:]}")

    return "OK: everything compiles and passes checks." if not any(r.startswith("❌") for r in results) else "Broken:\n" + "\n".join(results)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(enhanced_self_check(sys.argv[1]))
    else:
        print(enhanced_self_check())
