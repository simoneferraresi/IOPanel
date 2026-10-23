"""Conservative AST reference inventory for the complete Python source tree.

This is an investigation aid, not a dead-code remover. It deliberately counts
attribute and unqualified-name references by spelling across all files;
therefore a match does NOT establish that a specific definition is reachable.
Qt meta-object dispatch, callbacks, reflection and external consumers cannot
be exhaustively resolved by AST inspection.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path

SKIP_DIRECTORIES = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist"}
SKIP_FILES = {"resources/resources_rc.py"}
DYNAMIC_CALLS = {"getattr", "setattr", "hasattr", "delattr", "eval", "exec"}
QT_CALLS = {"connect", "disconnect", "invokeMethod", "singleShot", "connectSlotsByName"}
MAX_SITES = 4


def python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not set(path.relative_to(root).parts).intersection(SKIP_DIRECTORIES)
        and path.relative_to(root).as_posix() not in SKIP_FILES
    )


def module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _decorator_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


class SourceInventory(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[tuple[str, str]] = []
        self.symbols: list[dict[str, object]] = []
        self.references: list[tuple[str, str, int, str]] = []
        self.imports: list[str] = []
        self.connections: list[dict[str, object]] = []
        self.reflection: list[dict[str, object]] = []
        self.string_lookups: list[tuple[str, str, int, str]] = []

    def _define(self, node: ast.AST, name: str, kind: str, decorators: list[str] | None = None) -> None:
        self.symbols.append({
            "path": self.path,
            "line": node.lineno,
            "name": name,
            "qualified_name": ".".join([value for value, _ in self.scope] + [name]),
            "kind": kind,
            "decorators": decorators or [],
        })

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._define(node, node.name, "class", [_decorator_text(d) for d in node.decorator_list])
        for base in node.bases:
            self.visit(base)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.scope.append((node.name, "class"))
        for member in node.body:
            self.visit(member)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent_kind = self.scope[-1][1] if self.scope else "module"
        kind = "method" if parent_kind == "class" else ("function" if parent_kind == "module" else "nested")
        self._define(node, node.name, kind, [_decorator_text(d) for d in node.decorator_list])
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self.scope.append((node.name, "function"))
        for member in node.body:
            self.visit(member)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.references.append((node.id, self.path, node.lineno, "name"))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.references.append((node.attr, self.path, node.lineno, "attribute"))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append("." * node.level + node.module)
        elif node.level:
            self.imports.append("." * node.level)
        for alias in node.names:
            if alias.name != "*":
                self.references.append((alias.name, self.path, node.lineno, "import"))

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.scope and self.scope[-1][1] == "class" and isinstance(node.value, ast.Call):
            function = node.value.func
            if isinstance(function, ast.Name) and function.id in {"Signal", "pyqtSignal"}:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._define(target, target.id, "qt-signal")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        if name in QT_CALLS:
            try:
                expression = ast.unparse(node)[:180]
            except Exception:
                expression = name
            self.connections.append({"path": self.path, "line": node.lineno, "call": name, "expression": expression})
        if name in DYNAMIC_CALLS | {"invokeMethod", "findChild", "findChildren"}:
            self.reflection.append({"path": self.path, "line": node.lineno, "call": name})
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self.string_lookups.append((arg.value, self.path, node.lineno, "literal-lookup"))
        self.generic_visit(node)


def audit(root: Path) -> dict[str, object]:
    root = root.resolve()
    files = python_files(root)
    if not files:
        raise ValueError(f"No Python files found under {root}")
    readers: list[SourceInventory] = []
    errors: list[dict[str, str]] = []
    modules = {module_name(p.relative_to(root)): p.relative_to(root).as_posix() for p in files}
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"path": relative, "error": str(exc)})
            continue
        reader = SourceInventory(relative)
        reader.visit(tree)
        readers.append(reader)

    symbols = [symbol for reader in readers for symbol in reader.symbols]
    sites: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for reader in readers:
        for name, path, line, kind in reader.references + reader.string_lookups:
            sites[name].append((path, line, kind))

    connections = [item for reader in readers for item in reader.connections]
    reflection = [item for reader in readers for item in reader.reflection]
    import_edges: set[tuple[str, str]] = set()
    for reader in readers:
        source_module = module_name(Path(reader.path))
        for imported in reader.imports:
            if imported.startswith("."):
                # Retain relative imports as unresolved rather than inventing edges.
                continue
            for candidate in (imported, imported.rsplit(".", 1)[0] if "." in imported else imported):
                if candidate in modules:
                    import_edges.add((source_module, candidate))
                    break

    candidates: list[dict[str, object]] = []
    production = ("hardware/", "logic/", "ui/")
    for symbol in symbols:
        path = str(symbol["path"])
        name = str(symbol["name"])
        kind = str(symbol["kind"])
        if not (path.startswith(production) or "/" not in path):
            continue
        if kind == "nested" or (name.startswith("__") and name.endswith("__")):
            continue
        refs = sorted(set(sites.get(name, [])))
        if refs:
            continue
        decorators = symbol["decorators"]
        flags = []
        if kind == "qt-signal":
            flags.append("qt-metaobject")
        if decorators:
            flags.append("decorated-or-registered")
        if kind == "method":
            flags.append("possible-override-or-framework-hook")
        if not name.startswith("_"):
            flags.append("public-api-or-external-consumer")
        if reflection:
            flags.append("repository-uses-reflection")
        if connections:
            flags.append("repository-uses-qt-connect")
        candidates.append({**symbol, "reference_count": 0, "cautions": flags})

    candidates.sort(key=lambda item: (
        0 if item["kind"] == "class" else 1 if item["kind"] == "function" else 2,
        str(item["path"]), int(item["line"]),
    ))
    counts = Counter(str(s["kind"]) for s in symbols)
    return {
        "method": "Conservative spelling-based AST inventory, not proof of dead code",
        "source_files": len(files),
        "parsed_files": len(readers),
        "parse_errors": errors,
        "definitions_by_kind": dict(sorted(counts.items())),
        "local_import_edges": [{"source": source, "target": target} for source, target in sorted(import_edges)],
        "qt_connection_sites": connections,
        "dynamic_lookup_sites": reflection,
        "zero_spelling_reference_candidates": candidates,
        "limitations": [
            "References are matched by spelling, not exact object identity; shadowed names cause false negatives.",
            "Dynamically constructed attribute names and external plugins cannot be resolved completely.",
            "Qt slots, inherited overrides, decorators, string dispatch and test fixtures may be invoked without visible direct calls.",
            "Unreferenced definitions are review candidates only; no symbol is automatically safe to remove.",
            "Import edges are coarse; relative imports and runtime imports are not resolved into a complete dependency graph.",
        ],
    }


def markdown_report(report: dict[str, object]) -> str:
    lines = [
        "# IOPanel Python reference inventory",
        "",
        f"Python files: {report['source_files']}; parsed: {report['parsed_files']}; parse errors: {len(report['parse_errors'])}.",
        f"Definitions: {json.dumps(report['definitions_by_kind'], sort_keys=True)}.",
        f"Local import edges: {len(report['local_import_edges'])}.",
        f"Qt connection/dispatch sites: {len(report['qt_connection_sites'])}.",
        f"Reflection/dynamic lookup sites: {len(report['dynamic_lookup_sites'])}.",
        f"Zero-spelling-reference candidates: {len(report['zero_spelling_reference_candidates'])}.",
        "",
        "## Candidates requiring human review",
        "",
        "| Symbol | Kind | Location | Cautions |",
        "|---|---|---|---|",
    ]
    for item in report["zero_spelling_reference_candidates"]:
        lines.append(
            f"| {item['qualified_name']} | {item['kind']} | "
            f"{item['path']}:{item['line']} | {', '.join(item['cautions']) or 'none identified'} |"
        )
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {line}" for line in report["limitations"])
    if report["parse_errors"]:
        lines.extend(["", "## Parse errors", ""])
        lines.extend(f"- {item['path']}: {item['error']}" for item in report["parse_errors"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--show", type=int, default=30, help="Candidate lines shown in CI logs")
    args = parser.parse_args()
    result = audit(args.root)
    print(
        f"REFERENCE_AUDIT files={result['source_files']} parsed={result['parsed_files']} "
        f"parse_errors={len(result['parse_errors'])} "
        f"definitions={sum(result['definitions_by_kind'].values())} "
        f"import_edges={len(result['local_import_edges'])} "
        f"qt_connections={len(result['qt_connection_sites'])} "
        f"dynamic_lookups={len(result['dynamic_lookup_sites'])} "
        f"review_candidates={len(result['zero_spelling_reference_candidates'])}",
        flush=True,
    )
    for item in result["zero_spelling_reference_candidates"][:args.show]:
        print(f"  {item['kind']} {item['path']}:{item['line']} {item['qualified_name']}", flush=True)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "reference-inventory.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (args.output_dir / "reference-inventory.md").write_text(markdown_report(result), encoding="utf-8")
    if result["parse_errors"]:
        raise SystemExit("AST parse errors make this audit incomplete")


if __name__ == "__main__":
    main()
