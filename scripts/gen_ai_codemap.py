#!/usr/bin/env python3
"""Generate and verify `docs/ai/CODEMAP.md`.

The codemap only reads static source structure (paths, line counts, top-level
declarations). It never imports application modules, so it stays cheap and safe
to run in CI.

Usage:
    python scripts/gen_ai_codemap.py            # rewrite docs/ai/CODEMAP.md
    python scripts/gen_ai_codemap.py --check    # fail if the file is stale
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "ai" / "CODEMAP.md"

EXCLUDED_PARTS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "dist",
    "build",
    "local",
    "static",
    ".mypy_cache",
    ".pytest_cache",
}

# Directory responsibilities are the only hand-maintained part of this file.
# Add a row here when a new top-level area appears.
DIRECTORY_ROLES: tuple[tuple[str, str], ...] = (
    ("src/core", "主流程编排：pipeline、配置装配、调度入口"),
    ("src/services", "业务服务层：分析、筛选、通知、系统配置、任务与诊断"),
    ("src/repositories", "数据访问层：数据库读写封装"),
    ("src/reports", "报告生成与渲染"),
    ("src/schemas", "API / 报告数据结构定义"),
    ("src/agent", "问股 Agent：意图解析、编排、工具与流式事件"),
    ("src/llm", "LLM 调用封装与本地 CLI 生成后端"),
    ("data_provider", "多数据源适配、优先级与 fallback"),
    ("api", "FastAPI 应用与 v1 路由"),
    ("bot", "IM / 机器人接入"),
    ("apps/dsa-web/src", "Web 前端（React + Vite）"),
    ("apps/dsa-desktop", "Electron 桌面端壳与打包配置"),
    ("scripts", "本地与 CI 脚本"),
    (".github/scripts", "GitHub Actions 专用脚本"),
    ("tests", "pytest 测试"),
)

# Python areas scanned for size / declaration tables.
PYTHON_AREA_ROOTS: tuple[str, ...] = (
    "src",
    "data_provider",
    "api",
    "bot",
    "scripts",
    ".github/scripts",
    "tests",
    "main.py",
    "server.py",
)

FRONTEND_AREA_ROOTS: tuple[str, ...] = ("apps/dsa-web/src",)

COUNTED_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".scss",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".sh",
    ".ps1",
    ".html",
    ".sql",
    ".toml",
    ".txt",
}

TEST_PATH_MARKERS = ("/__tests__/", "/tests/", ".test.", ".spec.")

BIG_FILE_THRESHOLD = 400
BIG_FILE_LIMIT = 30
FRONTEND_FILE_LIMIT = 12
MAX_SYMBOLS_PER_FILE = 6


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def python_files() -> list[Path]:
    files: list[Path] = []
    for area in PYTHON_AREA_ROOTS:
        root = ROOT / area
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*.py")
        for path in candidates:
            if path.is_file() and path.suffix == ".py" and not is_excluded(path):
                files.append(path)
    return sorted(set(files))


def frontend_files() -> list[Path]:
    files: list[Path] = []
    for area in FRONTEND_AREA_ROOTS:
        root = ROOT / area
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix in {".ts", ".tsx"} and path.is_file() and not is_excluded(path):
                files.append(path)
    return sorted(set(files))


def is_test_path(path: Path) -> bool:
    rel_path = "/" + rel(path)
    return any(marker in rel_path for marker in TEST_PATH_MARKERS) or rel_path.startswith("/tests/")


def line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def module_import_aliases(path: Path) -> list[str]:
    """Names other modules could use to import this file."""
    rel_path = rel(path)
    dotted = rel_path[: -len(".py")].replace("/", ".")
    aliases = [dotted]
    if dotted.endswith(".__init__"):
        aliases.append(dotted[: -len(".__init__")])
    if dotted.startswith("src."):
        aliases.append(dotted[len("src.") :])
    return aliases


def parse_trees(files: list[Path]) -> dict[str, ast.Module]:
    """Parse each file once and reuse the AST for every table."""
    trees: dict[str, ast.Module] = {}
    for path in files:
        try:
            trees[rel(path)] = ast.parse(
                path.read_text(encoding="utf-8", errors="ignore")
            )
        except (SyntaxError, UnicodeError):
            continue
    return trees


def build_import_index(
    files: list[Path], trees: dict[str, ast.Module]
) -> tuple[dict[str, int], dict[str, int]]:
    """Count cross-file references to each module path and top-level symbol."""
    module_hits: dict[str, int] = {rel(path): 0 for path in files}
    symbol_hits: dict[str, int] = {}
    per_file_symbols: dict[str, set[str]] = {
        owner: {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for owner, tree in trees.items()
    }
    alias_to_file = {
        alias: rel(path) for path in files for alias in module_import_aliases(path)
    }
    symbol_to_file = {
        name: path for path, names in per_file_symbols.items() for name in names
    }
    for owner, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias_to_file.get(alias.name)
                    if target and target != owner:
                        module_hits[target] += 1
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    target = alias_to_file.get(node.module)
                    if target and target != owner:
                        module_hits[target] += 1
                for alias in node.names:
                    if alias.name == "*" or not node.module:
                        continue
                    target = alias_to_file.get(f"{node.module}.{alias.name}")
                    if target and target != owner:
                        module_hits[target] += 1
                    symbol_owner = symbol_to_file.get(alias.name)
                    if symbol_owner and symbol_owner != owner and node.module in alias_to_file:
                        symbol_hits[symbol_owner] = symbol_hits.get(symbol_owner, 0) + 1
    return module_hits, symbol_hits


def top_level_symbols(tree: ast.Module | None) -> list[tuple[str, int]]:
    if tree is None:
        return []
    symbols: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append((node.name, node.lineno))
    return symbols


def directory_table() -> list[str]:
    lines = [
        "## 目录职责",
        "",
        "行数按 py/ts/tsx/js/css/md/json/yml/sh/sql 等文本文件统计，不含 `node_modules`、`dist`、`local`、`static`。",
        "`docs/` 不在此表统计：文档会随着每次改动增减，纳入统计会让本文件自身永远「过期」（本文件也在 `docs/` 下）。",
        "",
        "| 目录 | 职责 | 文件数 | 行数 |",
        "| --- | --- | --- | --- |",
    ]
    for directory, role in DIRECTORY_ROLES:
        root = ROOT / directory
        if not root.exists():
            continue
        members = [
            path
            for path in root.rglob("*")
            if path.is_file() and not is_excluded(path) and path.suffix in COUNTED_SUFFIXES
        ]
        total_lines = sum(line_count(path) for path in members)
        lines.append(f"| `{directory}/` | {role} | {len(members)} | {total_lines} |")
    return lines


def generated_lines() -> list[str]:
    py_files = python_files()
    web_files = frontend_files()
    counts = {rel(path): line_count(path) for path in py_files + web_files}
    trees = parse_trees(py_files)
    module_hits, symbol_hits = build_import_index(py_files, trees)

    lines: list[str] = [
        "# CODEMAP · 代码地图",
        "",
        "> 本文件由 `python scripts/gen_ai_codemap.py` 生成，请勿手改。",
        "> 重新生成：`python scripts/gen_ai_codemap.py`；校验：`python scripts/gen_ai_codemap.py --check`（CI 已接入）。",
        "> 只统计静态结构（路径 / 行数 / 顶层声明 / import 引用次数），不执行任何模块。",
        "",
    ]
    lines += directory_table()
    lines.append("")

    lines += [
        "## 大文件（Python，≥{} 行）".format(BIG_FILE_THRESHOLD),
        "",
        "`引用` 指其他 Python 文件通过 `import` 引用该模块或其顶层符号的次数；0 表示只被入口脚本、反射或测试间接使用，改动前先自行搜索。",
        "",
        "| 文件 | 行数 | 关键顶层声明（行号） | 引用 |",
        "| --- | --- | --- | --- |",
    ]
    big_python = sorted(
        (
            path
            for path in py_files
            if counts[rel(path)] >= BIG_FILE_THRESHOLD and not is_test_path(path)
        ),
        key=lambda p: (-counts[rel(p)], rel(p)),
    )
    for path in big_python[:BIG_FILE_LIMIT]:
        symbols = top_level_symbols(trees.get(rel(path)))[:MAX_SYMBOLS_PER_FILE]
        rendered = ", ".join(f"`{name}`:{line}" for name, line in symbols) or "—"
        references = module_hits[rel(path)] + symbol_hits.get(rel(path), 0)
        lines.append(f"| `{rel(path)}` | {counts[rel(path)]} | {rendered} | {references} |")
    if len(big_python) > BIG_FILE_LIMIT:
        lines.append(
            f"| …另外 {len(big_python) - BIG_FILE_LIMIT} 个 ≥{BIG_FILE_THRESHOLD} 行的文件 | — | — | — |"
        )
    lines.append("")

    lines += [
        "## 大文件（前端，≥{} 行）".format(BIG_FILE_THRESHOLD),
        "",
        "| 文件 | 行数 |",
        "| --- | --- |",
    ]
    big_web = sorted(
        (
            path
            for path in web_files
            if counts[rel(path)] >= BIG_FILE_THRESHOLD and not is_test_path(path)
        ),
        key=lambda p: (-counts[rel(p)], rel(p)),
    )
    for path in big_web[:FRONTEND_FILE_LIMIT]:
        lines.append(f"| `{rel(path)}` | {counts[rel(path)]} |")
    if not big_web:
        lines.append("| 无 | — |")
    lines.append("")

    source_files = [path for path in py_files if not is_test_path(path)]
    test_files = [path for path in py_files if is_test_path(path)]
    total_lines = sum(counts[rel(path)] for path in source_files)
    lines += [
        "## 规模快照",
        "",
        f"- Python 源码（不含测试）：{len(source_files)} 个文件，{total_lines} 行",
        f"- Python 测试：{len(test_files)} 个文件，{sum(counts[rel(path)] for path in test_files)} 行",
        f"- 前端 TS/TSX：{len(web_files)} 个文件，{sum(counts[rel(p)] for p in web_files)} 行",
        f"- ≥{BIG_FILE_THRESHOLD} 行的 Python 文件：{len(big_python)} 个",
        "",
        "规模数字只用于判断改动影响面，不是质量指标。",
        "",
    ]
    return lines


def render() -> str:
    return "\n".join(generated_lines())


def main(argv: list[str]) -> int:
    content = render()
    if argv == ["--check"]:
        if not OUTPUT.exists():
            print(f"[codemap] missing {rel(OUTPUT)}; run python scripts/gen_ai_codemap.py")
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current == content:
            print("[codemap] OK")
            return 0
        current_lines = current.splitlines()
        fresh_lines = content.splitlines()
        for index, (before, after) in enumerate(zip(current_lines, fresh_lines), start=1):
            if before != after:
                print(f"[codemap] first difference at line {index}:")
                print(f"  file: {before}")
                print(f"  new : {after}")
                break
        else:
            print("[codemap] line counts differ", len(current_lines), "->", len(fresh_lines))
        print("[codemap] docs/ai/CODEMAP.md is stale; run python scripts/gen_ai_codemap.py")
        return 1
    if argv:
        print("usage: python scripts/gen_ai_codemap.py [--check]", file=sys.stderr)
        return 2
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"[codemap] wrote {rel(OUTPUT)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
