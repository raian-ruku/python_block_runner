"""
resolver.py - AST Reference and Dependency Resolver for PyBlockRunner

Finds variable, function, class, and import references declared outside a block
in the full script, resolving and synthesizing dependency code to execute before
running the block. This prevents NameError exceptions when blocks are executed
independently, out of order, or in isolated namespaces.
"""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass
from typing import Optional, Set, List, Tuple

BUILTIN_NAMES = set(dir(builtins)) | {
    "__name__", "__doc__", "__file__", "__annotations__", "__builtins__",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Definition:
    node: ast.AST
    lineno: int
    end_lineno: int
    source_code: str
    defines: set[str]          # All symbols defined by this statement
    depends_on: set[str]       # Symbols required to execute this statement
    is_star_import: bool = False


# ---------------------------------------------------------------------------
# Block AST Analyzer
# ---------------------------------------------------------------------------

class _BlockAnalyzer(ast.NodeVisitor):
    """
    Walks AST nodes in a block to find external references (loaded names that
    are not assigned locally within the block, not function parameters, and
    not Python builtins).
    """

    def __init__(self):
        self.assigned_names: set[str] = set()
        self.external_refs: set[str] = set()
        self.scope_stack: list[set[str]] = []

    def _is_local(self, name: str) -> bool:
        if name in self.assigned_names or name in BUILTIN_NAMES:
            return True
        for scope in self.scope_stack:
            if name in scope:
                return True
        return False

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            if not self._is_local(node.id):
                self.external_refs.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            if self.scope_stack:
                self.scope_stack[-1].add(node.id)
            else:
                self.assigned_names.add(node.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if self.scope_stack:
            self.scope_stack[-1].add(node.name)
        else:
            self.assigned_names.add(node.name)

        func_locals = set()
        for arg in node.args.args:
            func_locals.add(arg.arg)
        for arg in node.args.kwonlyargs:
            func_locals.add(arg.arg)
        if getattr(node.args, "posonlyargs", None):
            for arg in node.args.posonlyargs:
                func_locals.add(arg.arg)
        if node.args.vararg:
            func_locals.add(node.args.vararg.arg)
        if node.args.kwarg:
            func_locals.add(node.args.kwarg.arg)

        # Default argument values are evaluated in the enclosing scope
        for default in node.args.defaults:
            self.visit(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        for dec in node.decorator_list:
            self.visit(dec)

        self.scope_stack.append(func_locals)
        for stmt in node.body:
            self.visit(stmt)
        self.scope_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef):
        if self.scope_stack:
            self.scope_stack[-1].add(node.name)
        else:
            self.assigned_names.add(node.name)

        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        for dec in node.decorator_list:
            self.visit(dec)

        class_locals = set()
        self.scope_stack.append(class_locals)
        for stmt in node.body:
            self.visit(stmt)
        self.scope_stack.pop()

    def visit_ListComp(self, node: ast.ListComp):
        self._visit_comp(node)

    def visit_SetComp(self, node: ast.SetComp):
        self._visit_comp(node)

    def visit_DictComp(self, node: ast.DictComp):
        self._visit_comp(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        self._visit_comp(node)

    def _visit_comp(self, node):
        comp_locals = set()
        for gen in node.generators:
            self.visit(gen.iter)
            for name_node in ast.walk(gen.target):
                if isinstance(name_node, ast.Name) and isinstance(name_node.ctx, ast.Store):
                    comp_locals.add(name_node.id)
            self.scope_stack.append(comp_locals)
            for if_expr in gen.ifs:
                self.visit(if_expr)
            self.scope_stack.pop()

        self.scope_stack.append(comp_locals)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scope_stack.pop()


# ---------------------------------------------------------------------------
# Public Reference Extraction
# ---------------------------------------------------------------------------

def find_external_references(block_code: str) -> set[str]:
    """
    Return all variable/function/import names referenced (loaded) in *block_code*
    that are not defined locally within *block_code* and not in builtins.
    """
    try:
        tree = ast.parse(block_code)
    except SyntaxError:
        return set()

    analyzer = _BlockAnalyzer()
    analyzer.visit(tree)
    return analyzer.external_refs


# ---------------------------------------------------------------------------
# Statement Analysis & Definition Extraction
# ---------------------------------------------------------------------------

def _extract_target_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
    return names


def _extract_load_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in BUILTIN_NAMES:
                names.add(n.id)
    return names


def _analyze_statement(stmt: ast.AST) -> tuple[set[str], set[str], bool]:
    """
    Analyze a top-level AST statement.
    Returns (defines_set, depends_on_set, is_star_import).
    """
    defs: set[str] = set()
    deps: set[str] = set()
    is_star = False

    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            defs.update(_extract_target_names(target))
        deps.update(_extract_load_names(stmt.value))

    elif isinstance(stmt, ast.AnnAssign):
        defs.update(_extract_target_names(stmt.target))
        if stmt.value:
            deps.update(_extract_load_names(stmt.value))

    elif isinstance(stmt, ast.AugAssign):
        defs.update(_extract_target_names(stmt.target))
        deps.update(_extract_target_names(stmt.target))
        deps.update(_extract_load_names(stmt.value))

    elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        defs.add(stmt.name)
        analyzer = _BlockAnalyzer()
        analyzer.visit(stmt)
        deps.update(analyzer.external_refs)

    elif isinstance(stmt, ast.ClassDef):
        defs.add(stmt.name)
        analyzer = _BlockAnalyzer()
        analyzer.visit(stmt)
        deps.update(analyzer.external_refs)

    elif isinstance(stmt, ast.Import):
        for alias in stmt.names:
            name = alias.asname or alias.name.split('.')[0]
            defs.add(name)

    elif isinstance(stmt, ast.ImportFrom):
        for alias in stmt.names:
            if alias.name == "*":
                is_star = True
            else:
                name = alias.asname or alias.name
                defs.add(name)

    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        defs.update(_extract_target_names(stmt.target))
        deps.update(_extract_load_names(stmt.iter))
        for s in stmt.body:
            s_defs, s_deps, _ = _analyze_statement(s)
            defs.update(s_defs)
            deps.update(s_deps)

    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        for item in stmt.items:
            if item.optional_vars:
                defs.update(_extract_target_names(item.optional_vars))
        for s in stmt.body:
            s_defs, s_deps, _ = _analyze_statement(s)
            defs.update(s_defs)
            deps.update(s_deps)

    elif isinstance(stmt, ast.If):
        deps.update(_extract_load_names(stmt.test))
        for s in stmt.body:
            s_defs, s_deps, _ = _analyze_statement(s)
            defs.update(s_defs)
            deps.update(s_deps)
        for s in stmt.orelse:
            s_defs, s_deps, _ = _analyze_statement(s)
            defs.update(s_defs)
            deps.update(s_deps)

    elif isinstance(stmt, ast.Try):
        for s in stmt.body + stmt.orelse + stmt.finalbody:
            s_defs, s_deps, _ = _analyze_statement(s)
            defs.update(s_defs)
            deps.update(s_deps)
        for h in stmt.handlers:
            if h.name:
                defs.add(h.name)
            for s in h.body:
                s_defs, s_deps, _ = _analyze_statement(s)
                defs.update(s_defs)
                deps.update(s_deps)

    if not isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        deps.difference_update(defs)
    deps.difference_update(BUILTIN_NAMES)

    return defs, deps, is_star


def extract_definitions(full_source: str) -> list[Definition]:
    """
    Parse *full_source* into ordered Definition records for all top-level statements.
    """
    try:
        tree = ast.parse(full_source)
    except SyntaxError:
        return []

    lines = full_source.splitlines(keepends=True)
    definitions: list[Definition] = []

    for stmt in tree.body:
        defs, deps, is_star = _analyze_statement(stmt)
        if defs or is_star:
            start = stmt.lineno - 1
            end = getattr(stmt, "end_lineno", stmt.lineno)
            code = "".join(lines[start:end])
            definitions.append(Definition(
                node=stmt,
                lineno=stmt.lineno,
                end_lineno=end,
                source_code=code,
                defines=defs,
                depends_on=deps,
                is_star_import=is_star,
            ))

    return definitions


# ---------------------------------------------------------------------------
# Dependency Graph Resolution
# ---------------------------------------------------------------------------

def _find_best_definition(
    symbol: str,
    all_definitions: list[Definition],
    before_lineno: int,
    allow_fallback: bool = True,
) -> Optional[Definition]:
    candidates = [d for d in all_definitions if symbol in d.defines]
    if not candidates:
        star_candidates = [
            d for d in all_definitions
            if d.is_star_import and d.lineno < before_lineno
        ]
        if star_candidates:
            return star_candidates[-1]
        return None

    # Prefer definition that occurs before before_lineno, closest to it
    before = [d for d in candidates if d.lineno < before_lineno]
    if before:
        return max(before, key=lambda d: d.lineno)

    # Fallback to first definition found only when allowed (e.g. initial block queries)
    if allow_fallback:
        return candidates[0]
    return None


def _resolve_symbol_set(
    missing_symbols: list[str],
    all_definitions: list[Definition],
    current_ns: dict,
    block_start_line: int,
) -> list[Definition]:
    needed_definitions: list[Definition] = []
    seen_nodes: set[int] = set()
    visited_queries: set[tuple[str, int]] = set()
    queue: list[tuple[str, int, bool]] = [
        (sym, block_start_line, True) for sym in missing_symbols
    ]

    while queue:
        sym, before_line, allow_fallback = queue.pop(0)
        if sym in BUILTIN_NAMES:
            continue
        if before_line == block_start_line and sym in current_ns:
            continue

        defn = _find_best_definition(
            sym, all_definitions, before_line, allow_fallback=allow_fallback
        )
        if defn is None:
            continue

        node_id = id(defn.node)
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            needed_definitions.append(defn)

            for dep in defn.depends_on:
                query_key = (dep, defn.lineno)
                if query_key not in visited_queries:
                    visited_queries.add(query_key)
                    # For dependencies required by defn at defn.lineno, look strictly before defn.lineno
                    queue.append((dep, defn.lineno, False))

    return needed_definitions


# ---------------------------------------------------------------------------
# Public Resolver API
# ---------------------------------------------------------------------------

def resolve_dependencies(
    full_source: str,
    block_code: str,
    current_ns: dict,
    block_start_line: int = 1,
) -> str:
    """
    Find external references in *block_code* missing from *current_ns*,
    resolve their definitions from *full_source*, and return executable
    Python code that initializes them in correct line order.
    """
    if not full_source or not full_source.strip():
        return ""

    external_refs = find_external_references(block_code)
    missing = [name for name in external_refs if name not in current_ns]
    if not missing:
        return ""

    all_definitions = extract_definitions(full_source)
    if not all_definitions:
        return ""

    needed = _resolve_symbol_set(missing, all_definitions, current_ns, block_start_line)
    if not needed:
        return ""

    needed.sort(key=lambda d: d.lineno)
    return "\n".join(d.source_code.rstrip() for d in needed)


def resolve_single_symbol(
    full_source: str,
    symbol: str,
    current_ns: dict,
    block_start_line: int = 1,
) -> str:
    """
    Resolve and synthesize executable code for a single missing symbol name,
    including any transitive dependencies.
    """
    if not full_source or not full_source.strip() or not symbol:
        return ""

    all_definitions = extract_definitions(full_source)
    if not all_definitions:
        return ""

    needed = _resolve_symbol_set([symbol], all_definitions, current_ns, block_start_line)
    if not needed:
        return ""

    needed.sort(key=lambda d: d.lineno)
    return "\n".join(d.source_code.rstrip() for d in needed)
