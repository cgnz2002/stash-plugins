"""Catch names a function uses but nothing defines.

A cleanup run reached its last line and died on `NameError: name 'dry_run' is
not defined` -- after doing all its work. In a dry run that only lost the
summary; in a real run the writes would have landed and the task would still
have reported failure.

Nothing here runs the plugin (it needs a live Stash), and `ast.parse` is happy
with an undefined name, so the mistake had no way of being caught before a
user hit it. `symtable` is stdlib and knows which names a scope resolves
globally, so a global that the module never defines is findable statically --
which is the whole of that bug.

This is not a general linter: it only asks "could this name possibly resolve?"
"""

import sys
import os
import builtins
import symtable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                 "__package__", "__loader__", "__builtins__"}


def module_names(table):
    """Every name the module level binds: assignments, defs, classes, imports."""
    names = set()
    for sym in table.get_symbols():
        # A module-level symbol that is assigned, imported or declared is
        # resolvable from any nested scope.
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
            names.add(sym.get_name())
    return names


def undefined_in(table, defined, path, trail=()):
    """Global names referenced in this scope that `defined` does not cover."""
    bad = []
    here = trail + (table.get_name(),)
    for sym in table.get_symbols():
        name = sym.get_name()
        if not sym.is_referenced():
            continue
        # is_global() means this scope resolves the name at module level.
        # Anything local, a parameter, or closed over from an enclosing
        # function is someone else's problem and resolves fine.
        if sym.is_global() and name not in defined and name not in BUILTINS:
            bad.append((" > ".join(here[1:]) or "<module>", name))
    for child in table.get_children():
        bad.extend(undefined_in(child, defined, path, here))
    return bad


def check(path):
    with open(path, encoding="utf-8") as f:
        source = f.read()
    top = symtable.symtable(source, os.path.basename(path), "exec")
    return undefined_in(top, module_names(top), path)


# --- the real plugin files ---------------------------------------------------
files = sorted(f for f in os.listdir(PLUGIN) if f.endswith(".py"))
assert files, "no plugin sources found"

problems = []
for name in files:
    for scope, missing in check(os.path.join(PLUGIN, name)):
        problems.append("{}: {} uses undefined '{}'".format(name, scope, missing))

assert not problems, "\n".join(problems)


# --- and the check itself actually catches the bug it was written for -------
import tempfile

BROKEN = '''
def main():
    client = object()
    summary = "done"
    if dry_run:                 # never defined -- the real bug
        summary += " (dry run)"
    return summary
'''

FIXED = '''
def main():
    client = object()
    summary = "done"
    if client.dry_run:
        summary += " (dry run)"
    return summary
'''

with tempfile.TemporaryDirectory() as d:
    bad_path = os.path.join(d, "broken.py")
    with open(bad_path, "w", encoding="utf-8") as f:
        f.write(BROKEN)
    found = check(bad_path)
    assert found == [("main", "dry_run")], found

    ok_path = os.path.join(d, "fixed.py")
    with open(ok_path, "w", encoding="utf-8") as f:
        f.write(FIXED)
    assert check(ok_path) == [], check(ok_path)

    # ...without crying wolf over names that DO resolve
    fine = os.path.join(d, "fine.py")
    with open(fine, "w", encoding="utf-8") as f:
        f.write('''
import os

CONST = 1


def outer(arg):
    local = arg + CONST
    def inner():
        return local + CONST + os.sep
    return inner


class C:
    attr = CONST

    def method(self):
        return self.attr + len("x")
''')
    assert check(fine) == [], check(fine)

print("ALL OK")
