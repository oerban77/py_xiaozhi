# -*- coding: utf-8 -*-
"""Categorize CJK string literals by AST context to find DATA strings.

Strings in risky positions (dict keys, subscripts, comparisons, assignments,
call args to data-ish functions) are dumped for manual review -> SKIP list.
"""
import ast
import io
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
cjk_re = re.compile(r"[一-鿿]")

LOG_METHODS = {
    "debug", "info", "warning", "warn", "error", "critical", "exception", "log",
    "print", "add", "set", "append", "extend", "insert", "write", "format",
}

risk = Counter()   # risky context -> text
safe = Counter()   # prose context -> text


def classify(text, ctx):
    if cjk_re.search(text):
        (risk if ctx else safe)[text] += 1


def walk(node, src, out):
    for field, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            walk(value, src, out)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    walk(item, src, out)


def is_docstring(node):
    if not isinstance(node, ast.Expr):
        return False
    v = node.value
    return isinstance(v, ast.Constant) and isinstance(v.value, str)


PARENT_CTX = {}


def analyze(path):
    raw = io.open(str(path), encoding="utf-8").read()
    tree = ast.parse(raw)
    # map node -> parent
    parent = {}
    stack = [tree]
    while stack:
        n = stack.pop()
        for _, v in ast.iter_fields(n):
            if isinstance(v, ast.AST):
                parent[v] = n
                stack.append(v)
            elif isinstance(v, list):
                for it in v:
                    if isinstance(it, ast.AST):
                        parent[it] = n
                        stack.append(it)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and cjk_re.search(node.value):
            p = parent.get(node)
            ctx = None
            if p is None:
                ctx = "module?"
            elif isinstance(p, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and is_docstring(p):
                ctx = "docstring"
            elif isinstance(p, ast.keyword):
                ctx = f"kwarg:{p.arg}"
            elif isinstance(p, ast.Call):
                f = p.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "?")
                ctx = f"call:{name}"
            elif isinstance(p, ast.Dict) and p.keys and node in p.keys:
                ctx = "dictkey"
            elif isinstance(p, ast.Subscript):
                ctx = "subscript"
            elif isinstance(p, (ast.Compare,)):
                ctx = "compare"
            elif isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                ctx = "assign"
            elif isinstance(p, (ast.Return, ast.Yield, ast.YieldFrom)):
                ctx = "return"
            elif isinstance(p, (ast.List, ast.Tuple, ast.Set)):
                ctx = "literal"
            elif isinstance(p, ast.BinOp):
                ctx = "binop"
            elif isinstance(p, ast.If) or isinstance(p, ast.BoolOp):
                ctx = "condition"
            else:
                ctx = f"other:{type(p).__name__}"
            for ln in node.value.split("\n"):
                ln = ln.strip()
                if ln and cjk_re.search(ln):
                    PARENT_CTX.setdefault(ctx, Counter())[ln] += 1


for f in sorted(SRC.rglob("*.py")):
    try:
        analyze(f)
    except SyntaxError:
        pass

with open("_ctx.txt", "w", encoding="utf-8") as fh:
    for ctx, counter in sorted(PARENT_CTX.items(), key=lambda x: -sum(x[1].values())):
        fh.write(f"########## {ctx}  (total {sum(counter.values())}) ##########\n")
        for text, n in counter.most_common():
            fh.write(f"  [{n}] {text}\n")
        fh.write("\n")
print({k: sum(v.values()) for k, v in sorted(PARENT_CTX.items(), key=lambda x: -sum(x[1].values()))})
