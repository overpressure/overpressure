#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Write docs/api.md, the headset interface's reference, from the code; --check fails when it is out of date.

    python scripts/api-reference.py            # write docs/api.md
    python scripts/api-reference.py --check    # exit 1 if docs/api.md differs from the code

Each module of overpressure.api.v1 is an overpressure module as it is, so the reference lists that module's own
top-level names (no imported ones, none with a leading underscore): functions and classes with their signatures as
written, their docstrings' first paragraph, dataclass fields and constants with their `#:` comments. It reads the
source with ast and never imports it, so every Python and every host writes the same file.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = REPO / "overpressure/api/v1"
OUTPUT = REPO / "docs/api.md"
#: A constant's value is shown when its source is one line no longer than this.
SHORT_VALUE = 60


def interface_version() -> str:
    """Return the newest interface, MAJOR.MINOR, as overpressure/headset.py's SUPPORTED has it."""
    match = re.search(
        r"^SUPPORTED: dict\[int, int\] = \{(\d+): (\d+)\}",
        (REPO / "overpressure/headset.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        msg = "overpressure/headset.py: no SUPPORTED with one major"
        raise SystemExit(msg)
    return f"{match.group(1)}.{match.group(2)}"


def flat(text: str) -> str:
    """Return TEXT on one line: whitespace collapsed, no space inside brackets, no trailing comma before one."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([(\[{]) ", r"\1", text)
    return re.sub(r",? ([)\]}])", r"\1", text)


def without_comments(source: str) -> str:
    """Return SOURCE with its comments blanked out, every other character where it was (the AST's offsets hold)."""
    lines = source.splitlines(keepends=True)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            (row, start), (_, end) = token.start, token.end
            line = lines[row - 1]
            lines[row - 1] = line[:start] + " " * (end - start) + line[end:]
    return "".join(lines)


def first_paragraph(node: ast.AST) -> str:
    """Return NODE's docstring's first paragraph on one line, "" without one."""
    if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    doc = ast.get_docstring(node)
    return flat(doc.split("\n\n", 1)[0]) if doc else ""


def comment(lines: list[str], node: ast.stmt) -> str:
    """Return the `#:` comment of NODE: on the lines right above it, or at the end of its own line."""
    found: list[str] = []
    row = node.lineno - 2
    while row >= 0 and lines[row].strip().startswith("#:"):
        found.insert(0, lines[row].strip()[2:].strip())
        row -= 1
    end = lines[(node.end_lineno or node.lineno) - 1]
    if not found and "#: " in end:
        found.append(end.split("#: ", 1)[1].strip())
    return flat(" ".join(found))


def header(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    """Return a def's or a class's line as written, up to its colon, on one line."""
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    body = node.body[0]
    text = source[starts[node.lineno - 1] + node.col_offset : starts[body.lineno - 1] + body.col_offset]
    return flat(text).removesuffix(":").strip()


def function(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str = "") -> str:
    """Return a function's line of the reference."""
    head = header(source, node)
    head = re.sub(r"^(async )?def ", "", head)
    kind = "property " if any(isinstance(d, ast.Name) and d.id == "property" for d in node.decorator_list) else ""
    static = any(isinstance(d, ast.Name) and d.id in {"staticmethod", "classmethod"} for d in node.decorator_list)
    if static:
        kind = "static " if any(d.id == "staticmethod" for d in node.decorator_list if isinstance(d, ast.Name)) else ""
        kind = kind or "classmethod "
    doc = first_paragraph(node)
    return f"{prefix}- {kind}`{head}`" + (f": {doc}" if doc else "")


def assignment(source: str, lines: list[str], node: ast.Assign | ast.AnnAssign, prefix: str = "") -> str | None:
    """Return a constant's or a field's line of the reference, None for a private or unusual one."""
    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
    if not isinstance(target, ast.Name) or target.id.startswith("_"):
        return None
    name = target.id
    if isinstance(node, ast.AnnAssign):
        annotation = ast.get_source_segment(source, node.annotation) or ""
        text = f"{name}: {flat(annotation)}"
    else:
        text = name
    value = ast.get_source_segment(source, node.value) if node.value is not None else None
    if value is not None and "\n" not in value and len(value) <= SHORT_VALUE:
        text += f" = {value}"
    doc = comment(lines, node)
    return f"{prefix}- `{text}`" + (f": {doc}" if doc else "")


def is_type_alias(node: ast.stmt) -> bool:
    """Return whether NODE is a TypeVar or a typing alias, which a headset does not use by name."""
    value = getattr(node, "value", None)
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "TypeVar"


def class_lines(source: str, lines: list[str], node: ast.ClassDef) -> list[str]:
    """Return a class's lines of the reference: itself, its fields and its public methods."""
    head = header(source, node).removeprefix("class ")
    decorators = [flat(ast.get_source_segment(source, d) or "") for d in node.decorator_list]
    dataclass = [d for d in decorators if d.startswith("dataclass")]
    doc = first_paragraph(node)
    out = [f"- class `{head}`" + (f" ({'@' + dataclass[0]})" if dataclass else "") + (f": {doc}" if doc else "")]
    for item in node.body:
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            line = assignment(source, lines, item, "  ")
            if line:
                out.append(line)
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
            out.append(function(source, item, "  "))
    return out


def module_lines(name: str) -> tuple[str, list[str]]:
    """Return a module's summary (from its interface module) and its reference lines."""
    shim = ast.parse((API / f"{name}.py").read_text(encoding="utf-8"))
    summary = first_paragraph(shim)
    summary = re.sub(r"^Headset interface 1: ", "", summary)
    summary = re.sub(r" \(overpressure\.\w+\)", "", summary)
    path = REPO / "overpressure" / f"{name}.py"
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()  # the #: comments
    source = without_comments(original)  # signatures and values as written, without their comments
    tree = ast.parse(source)
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            out.append(function(source, node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out.extend(class_lines(source, lines, node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not is_type_alias(node):
            line = assignment(source, lines, node)
            if line:
                out.append(line)
    return summary.rstrip("."), out


def anchor(title: str) -> str:
    """Return GitHub's anchor for a heading, as scripts/check-docs.py computes it."""
    title = re.sub(r"[`*_]|\[|\]\([^)]*\)", "", title).strip().lower()
    return re.sub(r"[^\w\- ]", "", title).replace(" ", "-")


def reference() -> str:
    """Return docs/api.md."""
    version = interface_version()
    names = sorted(p.stem for p in API.glob("*.py") if p.stem != "__init__")
    modules = {name: module_lines(name) for name in names}
    root = ast.parse((API / "__init__.py").read_text(encoding="utf-8"))
    out = [
        "# Headset interface reference",
        "",
        f"**TL;DR.** Every name a headset may use in headset interface {version}, `overpressure.api.v1`: its",
        "modules, and in each its functions and classes with their signatures, dataclass fields and constants, each",
        "with the first paragraph of its documentation. A headset imports only these",
        "(`from overpressure.api.v1 import REPO, qualcomm`, `from overpressure.api.v1.headset import Headset, Step`).",
        "The rules (a minor only adds, a break makes a new major) are in",
        "[adding-a-headset.md](adding-a-headset.md#the-headset-interface), what each minor added in",
        "[what a headset may use](adding-a-headset.md#what-a-headset-may-use): a headset made for an older minor",
        "uses only what that minor had. The source links lead to the code, which says more than its first paragraph.",
        "",
        "`python scripts/api-reference.py` writes this file from the code; a test fails when it is out of date.",
        "",
        "## Modules",
        "",
        "| Module | What it is |",
        "| --- | --- |",
        f"| [`overpressure.api.v1`](#{anchor('overpressure.api.v1')}) | {first_paragraph(root)} |",
    ]
    out.extend(f"| [`{name}`](#{anchor(name)}) | {summary} |" for name, (summary, _) in modules.items())
    out += [
        "",
        "## overpressure.api.v1",
        "",
        "The interface's package itself ([source](../overpressure/api/v1/__init__.py)).",
        "",
    ]
    package = (REPO / "overpressure/__init__.py").read_text(encoding="utf-8")
    for node in ast.parse(package).body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if any(isinstance(t, ast.Name) and t.id == "REPO" for t in targets):
            out.append(f"- `REPO`: {comment(package.splitlines(), node)}")
    for name, (summary, lines) in modules.items():
        out += [
            "",
            f"## {name}",
            "",
            f"`overpressure.api.v1.{name}`: {summary} ([source](../overpressure/{name}.py)).",
            "",
        ]
        out.extend(lines or ["- no names of its own"])
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    """Write the reference, or with --check compare it."""
    text = reference()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(
                f"{OUTPUT.relative_to(REPO).as_posix()} is out of date: python scripts/api-reference.py",
                file=sys.stderr,
            )
            return 1
        return 0
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(f"wrote {OUTPUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
