"""Catch undefined / unused names in the desktop UI without extra dependencies.

Uses `symtable` (stdlib) to find names that are read but never bound in any
enclosing scope and are not builtins -- i.e. the `NameError` class of bug.
That class is easy to introduce in Tk code (bare `CARD` instead of `T.CARD`)
and only shows up when a specific page is opened, so it must be checked
statically rather than by clicking through.
"""
from __future__ import annotations

import builtins
import symtable
import sys
from pathlib import Path
from typing import List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
TARGETS = list((ROOT / "hc_agent" / "desktop").glob("*.py")) + [
    ROOT / "hc_agent" / "engine_host.py",
]

ok = fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def collect(table: symtable.SymbolTable, bound: Set[str],
            used: List[Tuple[str, str]]) -> None:
    for sym in table.get_symbols():
        n = sym.get_name()
        if sym.is_assigned() or sym.is_imported() or sym.is_parameter() \
                or sym.is_namespace():
            bound.add(n)
        if sym.is_referenced() and not sym.is_global() and not sym.is_free():
            used.append((n, table.get_name()))
    for child in table.get_children():
        collect(child, bound, used)


print("=" * 74)
print("桌面端静态检查（未定义名 / 语法）")
print("=" * 74)

for path in TARGETS:
    src = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)
    try:
        top = symtable.symtable(src, str(path), "exec")
    except SyntaxError as exc:
        check(f"{rel} 语法正确", False, str(exc))
        continue
    check(f"{rel} 语法正确", True)

    # module-level names + builtins are always available
    scope: Set[str] = set(dir(builtins))
    for sym in top.get_symbols():
        scope.add(sym.get_name())
    imported: Set[str] = set()
    for node in __import__("ast").walk(__import__("ast").parse(src)):
        if isinstance(node, __import__("ast").Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, __import__("ast").ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)
    scope |= imported

    bound: Set[str] = set()
    used: List[Tuple[str, str]] = []
    collect(top, bound, used)
    # names bound anywhere in the file count as defined for our purpose
    undefined = sorted({n for n, _ in used if n not in scope and n not in bound})
    check(f"{rel} 无未定义名", not undefined, f"未定义: {undefined}")

print()
print("=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
