"""Every plugin manifest must parse, or Stash silently drops the plugin.

3.1.0 shipped a task description written as a plain (unquoted) YAML scalar
that contained "additions: 'sponsored' ...". A colon followed by a space
inside a plain scalar reads as a second mapping key, so the whole manifest
failed to load -- and Stash's response to an unloadable plugin config is to
leave the plugin out entirely: it vanished from both the Tasks page and the
plugin settings, with nothing pointing at the cause.

Two checks. The stdlib one always runs and catches exactly that class of
mistake (": " or " #" inside an unquoted value). When PyYAML happens to be
installed the manifests are also parsed for real -- an optional extra, like
the Node half of test_comic_reader.py, since the tests are stdlib only.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import PLUGIN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(PLUGIN)))
MANIFESTS = sorted(glob.glob(os.path.join(ROOT, "plugins", "**", "*.yml"),
                             recursive=True))
assert MANIFESTS, "no manifests found under plugins/"

# `key: value` or `- key: value`, where value is a plain (unquoted) scalar.
PAIR = re.compile(r"^\s*(?:-\s+)?[\w.-]+:\s+(?P<value>\S.*)$")
QUOTED_OR_BLOCK = ('"', "'", "|", ">", "[", "{", "&", "*", "!")


def plain_scalar_problems(text):
    """Lines whose unquoted value YAML would mis-read."""
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = PAIR.match(line)
        if not m:
            continue
        value = m.group("value")
        if value.startswith(QUOTED_OR_BLOCK):
            continue
        if ": " in value or value.endswith(":"):
            problems.append((n, "':' inside an unquoted value"))
        elif " #" in value:
            problems.append((n, "' #' starts a comment, truncating the value"))
    return problems


# --- the check catches the 3.1.0 line itself ---------------------------------
BROKEN = ("    description: Re-apply only the crew and sponsor logic (the only "
          "tag changes are additions: 'sponsored' and content-house tags).\n")
assert plain_scalar_problems(BROKEN), "must flag a colon in a plain scalar"
assert not plain_scalar_problems('    description: "a: b"\n'), "quoted is fine"
assert not plain_scalar_problems("    description: no colon here\n")
assert not plain_scalar_problems("  - python\n")

# --- every real manifest ------------------------------------------------------
for path in MANIFESTS:
    text = open(path, encoding="utf-8").read()
    bad = plain_scalar_problems(text)
    assert not bad, "{}: {}".format(
        os.path.relpath(path, ROOT),
        "; ".join("line {}: {}".format(n, why) for n, why in bad))

try:
    import yaml
except ImportError:
    yaml = None
    print("PyYAML not installed -- full YAML parse skipped")

if yaml is not None:
    for path in MANIFESTS:
        try:
            data = yaml.safe_load(open(path, encoding="utf-8"))
        except yaml.YAMLError as e:
            raise AssertionError("{} does not parse: {}".format(
                os.path.relpath(path, ROOT), e))
        assert isinstance(data, dict) and data.get("name"), path

print("ALL OK")
