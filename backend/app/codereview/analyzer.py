"""Deterministic Python static analysis (AST). Findings here are facts about the code's structure;
AI suggestions are produced separately and labelled as such."""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass

BLOCKING_CALLS = {
    "time.sleep": "use `await asyncio.sleep()`",
    "requests.get": "use an async client (httpx.AsyncClient)", "requests.post": "use an async client (httpx.AsyncClient)",
    "requests.put": "use an async client", "requests.delete": "use an async client", "requests.request": "use an async client",
    "urllib.request.urlopen": "use httpx.AsyncClient",
    "subprocess.run": "use `asyncio.create_subprocess_exec`", "subprocess.call": "use asyncio subprocess",
    "subprocess.check_output": "use asyncio subprocess", "os.system": "use asyncio subprocess",
    "socket.create_connection": "use asyncio streams", "psycopg2.connect": "use asyncpg / async SQLAlchemy",
    "sqlite3.connect": "use aiosqlite", "open": "use `aiofiles` or `asyncio.to_thread` for file I/O",
}
SECRET_RE = re.compile(r"(?i)(password|passwd|secret|api_?key|token|private_?key)\s*=\s*['\"][^'\"]{6,}['\"]")
SQL_RE = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\b(from|into|set|where)\b")


@dataclass
class Finding:
    rule: str
    category: str  # concurrency | async | resource | security | correctness | performance | maintainability | api
    severity: str  # high | medium | low
    line: int
    message: str
    suggestion: str
    source: str = "static-analysis"


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


class _Visitor(ast.NodeVisitor):
    def __init__(self, src: str) -> None:
        self.f: list[Finding] = []
        self.async_depth = 0
        self.async_defs: set[str] = set()
        self.with_depth = 0
        self.src = src
        self.lock_names: set[str] = set()

    def add(self, *a) -> None:
        self.f.append(Finding(*a))

    def visit_Module(self, node: ast.Module) -> None:
        for n in ast.walk(node):
            if isinstance(n, ast.AsyncFunctionDef):
                self.async_defs.add(n.name)
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and _name(n.value).endswith(("threading.Lock", "Lock")):
                for t in n.targets:
                    self.lock_names.add(_name(t))
        self.generic_visit(node)

    def _func_common(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for d in node.args.defaults + node.args.kw_defaults:
            if isinstance(d, ast.List | ast.Dict | ast.Set):
                self.add("mutable-default-arg", "correctness", "medium", d.lineno,
                         f"Mutable default argument in `{node.name}`", "Use `None` and create the object inside the function.")
        length = (node.end_lineno or node.lineno) - node.lineno
        if length > 60:
            self.add("long-function", "maintainability", "low", node.lineno, f"`{node.name}` is {length} lines long",
                     "Split into smaller functions with single responsibilities.")
        branches = sum(isinstance(n, ast.If | ast.For | ast.While | ast.Try | ast.BoolOp | ast.ExceptHandler | ast.With)
                       for n in ast.walk(node))
        if branches > 12:
            self.add("high-complexity", "maintainability", "medium", node.lineno,
                     f"`{node.name}` has cyclomatic complexity ~{branches + 1}", "Reduce branching; extract helpers.")
        if not node.name.startswith("_") and node.returns is None and node.name != "__init__":
            self.add("missing-return-annotation", "api", "low", node.lineno, f"Public function `{node.name}` lacks a return type",
                     "Add type annotations to define the API contract.")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_common(node)
        saved = self.async_depth
        self.async_depth = 0
        self.generic_visit(node)
        self.async_depth = saved

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_common(node)
        self.async_depth += 1
        self.generic_visit(node)
        self.async_depth -= 1

    def visit_With(self, node: ast.With) -> None:
        self.with_depth += 1
        if self.async_depth:
            for item in node.items:
                if _name(item.context_expr) in self.lock_names or _name(item.context_expr).endswith("Lock"):
                    self.add("threading-lock-in-async", "concurrency", "high", node.lineno,
                             "threading.Lock held inside async code blocks the event loop",
                             "Use `asyncio.Lock` with `async with`.")
        self.generic_visit(node)
        self.with_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        name = _name(node.func)
        if self.async_depth and name in BLOCKING_CALLS:
            self.add("blocking-io-in-async", "async", "high", node.lineno, f"Blocking call `{name}()` inside `async def`",
                     BLOCKING_CALLS[name])
        if name == "open" and not self.with_depth:
            self.add("resource-leak-open", "resource", "medium", node.lineno, "`open()` outside a context manager",
                     "Use `with open(...) as f:` so the file is always closed.")
        if name in ("eval", "exec"):
            self.add("dangerous-eval", "security", "high", node.lineno, f"Use of `{name}()`",
                     "Avoid dynamic code execution; use ast.literal_eval or a safe parser.")
        if name in ("pickle.loads", "pickle.load", "yaml.load"):
            safe_yaml = name == "yaml.load" and any(k.arg == "Loader" and "Safe" in _name(k.value) for k in node.keywords)
            if not safe_yaml:
                self.add("unsafe-deserialization", "security", "high", node.lineno, f"Unsafe deserialization `{name}`",
                         "Use json or yaml.safe_load; never unpickle untrusted data.")
        if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
            self.add("shell-injection", "security", "high", node.lineno, "subprocess with shell=True",
                     "Pass an argument list and shell=False.")
        if name.endswith(".execute") and node.args:
            a = node.args[0]
            if isinstance(a, ast.JoinedStr) or (isinstance(a, ast.BinOp) and isinstance(a.op, ast.Mod | ast.Add)) or \
                    (isinstance(a, ast.Call) and _name(a.func).endswith(".format")):
                self.add("sql-injection", "security", "high", node.lineno, "SQL built with string formatting",
                         "Use parameterised queries / bound parameters.")
        if name in ("requests.get", "requests.post", "httpx.get", "httpx.post") and not any(k.arg == "timeout" for k in node.keywords):
            self.add("missing-timeout", "performance", "medium", node.lineno, f"`{name}` without a timeout",
                     "Always set a timeout on network calls.")
        if name == "hashlib.md5" or name == "hashlib.sha1":
            self.add("weak-hash", "security", "medium", node.lineno, f"Weak hash `{name}`", "Use SHA-256+ (or argon2 for passwords).")
        if name in ("random.random", "random.randint", "random.choice") and re.search(r"(?i)token|secret|password|key",
                                                                                     self.src.splitlines()[node.lineno - 1]):
            self.add("insecure-random", "security", "high", node.lineno, "Non-cryptographic RNG used for secrets",
                     "Use the `secrets` module.")
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call) and _name(node.value.func) in ("asyncio.create_task", "loop.create_task",
                                                                            "asyncio.ensure_future"):
            self.add("fire-and-forget-task", "concurrency", "medium", node.lineno, "Task created without keeping a reference",
                     "Store the task (e.g. in a set) and handle exceptions; otherwise it may be garbage-collected.")
        if isinstance(node.value, ast.Call) and self.async_depth:
            n = _name(node.value.func).split(".")[-1]
            if n in self.async_defs:
                self.add("missing-await", "async", "high", node.lineno, f"Coroutine `{n}()` called without await",
                         "Add `await` (the coroutine never runs otherwise).")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.add("bare-except", "correctness", "medium", node.lineno, "Bare `except:` swallows all errors incl. CancelledError",
                     "Catch specific exceptions; re-raise asyncio.CancelledError.")
        elif _name(node.type) in ("Exception", "BaseException") and all(isinstance(b, ast.Pass) for b in node.body):
            self.add("swallowed-exception", "correctness", "medium", node.lineno, "Exception caught and silently ignored",
                     "Log the error or handle it explicitly.")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        if self.async_depth:
            for n in ast.walk(node):
                if isinstance(n, ast.Await):
                    self.add("sequential-await-in-loop", "performance", "low", node.lineno,
                             "Awaiting inside a loop serialises independent I/O",
                             "If iterations are independent, use asyncio.gather / TaskGroup with a semaphore.")
                    break
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.add("global-mutable-state", "concurrency", "low", node.lineno, f"`global {', '.join(node.names)}`",
                 "Shared mutable state is unsafe under concurrency; use dependency injection or locks.")
        self.generic_visit(node)


def analyze(code: str) -> dict:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"findings": [asdict(Finding("syntax-error", "correctness", "high", exc.lineno or 1, f"SyntaxError: {exc.msg}",
                                            "Fix the syntax error before further analysis."))],
                "metrics": {"parsed": False}}
    v = _Visitor(code)
    v.visit(tree)
    for i, line in enumerate(code.splitlines(), 1):
        if SECRET_RE.search(line) and "os.environ" not in line and "getenv" not in line:
            v.add("hardcoded-secret", "security", "high", i, "Possible hard-coded credential",
                  "Load secrets from environment / secret manager.")
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    sev = {"high": 0, "medium": 1, "low": 2}
    findings = sorted((asdict(f) for f in v.f), key=lambda f: (sev[f["severity"]], f["line"]))
    # de-duplicate identical rule+line
    seen, uniq = set(), []
    for f in findings:
        k = (f["rule"], f["line"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return {"findings": uniq, "metrics": {
        "parsed": True, "lines": len(code.splitlines()), "functions": len(funcs),
        "async_functions": sum(isinstance(f, ast.AsyncFunctionDef) for f in funcs),
        "classes": sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree)),
        "by_severity": {s: sum(f["severity"] == s for f in uniq) for s in ("high", "medium", "low")},
    }}


def function_signatures(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            args = ", ".join(a.arg for a in n.args.args)
            out.append(f"{'async ' if isinstance(n, ast.AsyncFunctionDef) else ''}def {n.name}({args})")
    return out
