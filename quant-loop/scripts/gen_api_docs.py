#!/usr/bin/env python3
"""Generate API docs for ``_shared/`` modules (J13).

Walks ``_shared/**/*.py`` (excluding tests and ``__init__.py``), and for
each module writes ``docs/api/<subpackage>/<module>.md`` containing:

  * module docstring (full first section),
  * public classes: signature, docstring first paragraph, public methods,
  * public functions: signature, parameter table (name / annotation /
    default), docstring first paragraph.

Extraction uses ``inspect`` on the imported module; when a module cannot
be imported (missing optional dependency, import-time side effect) the
script falls back to static ``ast`` parsing and marks the page as such.

An index of all generated pages is written to ``docs/api/README.md``.

Usage::

    python3 scripts/gen_api_docs.py
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHARED = REPO / "_shared"
OUT = REPO / "docs" / "api"

SKIP_FILES = {"__init__.py", "__main__.py"}


@dataclass
class FuncDoc:
    name: str
    signature: str
    doc: str
    params: list[tuple] = field(default_factory=list)  # (name, annotation, default)


@dataclass
class ClassDoc:
    name: str
    signature: str
    doc: str
    methods: list[FuncDoc] = field(default_factory=list)


@dataclass
class ModuleDoc:
    dotted: str
    doc: str
    functions: list[FuncDoc] = field(default_factory=list)
    classes: list[ClassDoc] = field(default_factory=list)
    fallback_ast: bool = False
    error: str = ""


def _first_paragraph(docstring: str | None) -> str:
    if not docstring:
        return ""
    lines = inspect.cleandoc(docstring).splitlines()
    para = []
    for line in lines:
        if not line.strip():
            break
        para.append(line.strip())
    return " ".join(para)


def _is_public(name: str) -> bool:
    return not name.startswith("_")


# ------------------------------------------------------------- inspect path
def _params_of(sig: inspect.Signature) -> list[tuple]:
    out = []
    for p in sig.parameters.values():
        if p.name in ("self", "cls"):
            continue
        ann = (
            ""
            if p.annotation is inspect.Parameter.empty
            else inspect.formatannotation(p.annotation)
        )
        default = "" if p.default is inspect.Parameter.empty else repr(p.default)
        out.append((p.name, ann, default))
    return out


def _func_doc_inspect(name: str, fn) -> FuncDoc:
    try:
        sig = inspect.signature(fn)
        sig_str = f"{name}{sig}"
        params = _params_of(sig)
    except (TypeError, ValueError):
        sig_str, params = f"{name}(...)", []
    return FuncDoc(name=name, signature=sig_str, doc=_first_paragraph(fn.__doc__), params=params)


def _via_inspect(dotted: str) -> ModuleDoc:
    mod = importlib.import_module(dotted)
    page = ModuleDoc(dotted=dotted, doc=_first_paragraph(mod.__doc__))
    exported = set(getattr(mod, "__all__", []) or [])

    def wanted(name, obj):
        if not _is_public(name):
            return False
        if exported:
            return name in exported
        return getattr(obj, "__module__", None) == dotted

    for name, obj in sorted(vars(mod).items()):
        if inspect.isclass(obj) and wanted(name, obj):
            try:
                sig = f"{name}{inspect.signature(obj)}"
            except (TypeError, ValueError):
                sig = name
            cls = ClassDoc(name=name, signature=sig, doc=_first_paragraph(obj.__doc__))
            for mname, mobj in sorted(vars(obj).items()):
                if _is_public(mname) and inspect.isfunction(mobj):
                    cls.methods.append(_func_doc_inspect(mname, mobj))
            page.classes.append(cls)
        elif inspect.isfunction(obj) and wanted(name, obj):
            page.functions.append(_func_doc_inspect(name, obj))
    return page


# ----------------------------------------------------------------- ast path
def _ann(node) -> str:
    return ast.unparse(node) if node is not None else ""


def _func_doc_ast(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FuncDoc:
    args = node.args
    parts = []
    params = []
    pos = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(pos) - len(args.defaults)) + list(args.defaults)
    def _add_arg(a, d):
        default = ast.unparse(d) if d is not None else ""
        ann = _ann(a.annotation)
        if default:
            rendered = f"{a.arg}: {ann} = {default}"
        elif ann:
            rendered = f"{a.arg}: {ann}"
        else:
            rendered = a.arg
        parts.append(rendered)
        params.append((a.arg, ann, default))

    for a, d in zip(pos, defaults):
        if a.arg in ("self", "cls"):
            continue
        _add_arg(a, d)
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        _add_arg(a, d)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    ret = f" -> {_ann(node.returns)}" if node.returns else ""
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else ""
    _ = prefix  # signature kept uniform; async noted in doc
    return FuncDoc(
        name=node.name,
        signature=f"{node.name}({', '.join(parts)}){ret}",
        doc=_first_paragraph(ast.get_docstring(node)),
        params=params,
    )


def _via_ast(dotted: str, path: Path, error: str) -> ModuleDoc:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    page = ModuleDoc(
        dotted=dotted,
        doc=_first_paragraph(ast.get_docstring(tree)),
        fallback_ast=True,
        error=error,
    )
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
            page.functions.append(_func_doc_ast(node))
        elif isinstance(node, ast.ClassDef) and _is_public(node.name):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            cls = ClassDoc(
                name=node.name,
                signature=f"{node.name}({bases})" if bases else node.name,
                doc=_first_paragraph(ast.get_docstring(node)),
            )
            func_types = (ast.FunctionDef, ast.AsyncFunctionDef)
            for sub in node.body:
                if isinstance(sub, func_types) and _is_public(sub.name):
                    cls.methods.append(_func_doc_ast(sub))
            page.classes.append(cls)
    return page


# --------------------------------------------------------------- rendering
def _render_params(params: list[tuple]) -> list[str]:
    if not params:
        return []
    lines = ["| Parameter | Type | Default |", "|---|---|---|"]
    for name, ann, default in params:
        lines.append(f"| `{name}` | {ann or '—'} | {default or '—'} |")
    return lines


def render(page: ModuleDoc, rel_src: str) -> str:
    lines = [f"# `{page.dotted}`", ""]
    if page.fallback_ast:
        lines += [
            "> ⚠ Generated via static AST parsing — the module could not be "
            f"imported (`{page.error.strip().splitlines()[-1] if page.error else 'unknown'}`).",
            "",
        ]
    lines += [f"Source: `{rel_src}`", ""]
    if page.doc:
        lines += [page.doc, ""]
    for cls in page.classes:
        lines += [f"## class `{cls.signature}`", ""]
        if cls.doc:
            lines += [cls.doc, ""]
        for m in cls.methods:
            lines += [f"### `{m.signature}`", ""]
            if m.doc:
                lines += [m.doc, ""]
            lines += _render_params(m.params)
            if m.params:
                lines.append("")
    for fn in page.functions:
        lines += [f"## `{fn.signature}`", ""]
        if fn.doc:
            lines += [fn.doc, ""]
        lines += _render_params(fn.params)
        if fn.params:
            lines.append("")
    if not page.classes and not page.functions:
        lines += ["_(no public classes or functions)_", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    OUT.mkdir(parents=True, exist_ok=True)

    pages = []  # (dotted, out_path, first_paragraph, fallback)
    failures = 0
    for src in sorted(SHARED.rglob("*.py")):
        if src.name in SKIP_FILES or src.name.startswith("test_"):
            continue
        if "__pycache__" in src.parts:
            continue
        rel = src.relative_to(SHARED).with_suffix("")
        dotted = "_shared." + ".".join(rel.parts)
        try:
            page = _via_inspect(dotted)
        except Exception as exc:  # noqa: BLE001 — fall back to static parse
            try:
                page = _via_ast(dotted, src, str(exc))
            except Exception as exc2:  # noqa: BLE001
                failures += 1
                print(f"SKIP {dotted}: import failed ({exc}); ast failed ({exc2})")
                continue
        out_path = OUT / rel.parent / f"{rel.name}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render(page, str(src.relative_to(REPO))), encoding="utf-8")
        pages.append((dotted, out_path, page.doc, page.fallback_ast))

    # Index.
    idx = ["# `_shared/` API 索引 (J13)", ""]
    idx.append("由 `python3 scripts/gen_api_docs.py` 生成；请勿手改。")
    idx.append("")
    current_group = None
    for dotted, out_path, doc, fallback in sorted(pages):
        group = ".".join(dotted.split(".")[:2])  # _shared or _shared.<subpkg>
        if group != current_group:
            current_group = group
            idx += ["", f"## `{group}`", ""]
        rel_link = out_path.relative_to(OUT)
        note = " ⚠ast" if fallback else ""
        summary = f" — {doc}" if doc else ""
        idx.append(f"- [`{dotted}`]({rel_link}){note}{summary}")
    idx.append("")
    (OUT / "README.md").write_text("\n".join(idx), encoding="utf-8")

    n_fallback = sum(1 for p in pages if p[3])
    print(f"generated {len(pages)} module pages under {OUT.relative_to(REPO)}/ "
          f"({n_fallback} via AST fallback, {failures} skipped)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
