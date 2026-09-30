"""The generated index.yml must parse, whatever a manifest's wording is.

build_site.sh interpolates a plugin's name and description straight into the
index. Unquoted, a value starting with a YAML indicator ("[DEPRECATED] ..."
parses as a flow sequence) or containing ": " yields an index.yml that does not
parse AT ALL -- so one plugin's wording breaks the plugin SOURCE for every user,
including the plugins that were fine. That is the failure this guards.
"""

import sys
import os
import shutil
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin import REPO

BUILD = os.path.join(REPO, "build_site.sh")

if shutil.which("bash") is None or shutil.which("zip") is None:
    print("bash/zip unavailable, skipping")
    print("ALL OK")
    raise SystemExit(0)


def build(repo_root):
    out = tempfile.mkdtemp()
    proc = subprocess.run(["bash", os.path.join(repo_root, "build_site.sh"), out],
                          cwd=repo_root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    with open(os.path.join(out, "index.yml"), encoding="utf-8") as f:
        text = f.read()
    shutil.rmtree(out, ignore_errors=True)
    return text


def parse(text):
    """Parse without pyyaml, which the plugin's stdlib-only rule keeps out.

    Only the shape build_site.sh emits is handled: a list of plugins, each a
    block mapping. That is enough to catch a value that escapes its line, which
    is the whole failure mode -- a name parsed as a sequence, or a stray ": "
    splitting one field into two.
    """
    entries, current = [], None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("- id: "):
            current = {"id": raw[len("- id: "):]}
            entries.append(current)
            continue
        assert current is not None, "content before the first plugin: " + raw
        assert raw.startswith("  "), "unindented line inside a plugin: " + raw
        stripped = raw.strip()
        # "metadata:" opens a nested mapping and "requires:" a list; both are
        # structure, not a value, and their children are flattened onto the
        # plugin since no key collides.
        if stripped in ("metadata:", "requires:") or stripped.startswith("- "):
            continue
        key, sep, value = stripped.partition(": ")
        assert sep, "line is not key: value -- " + raw
        if value.startswith('"'):
            assert value.endswith('"') and len(value) >= 2, \
                "quoted value not closed on its own line: " + raw
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        else:
            # An unquoted value must not look like a sequence, a mapping, or
            # anything else YAML would reinterpret.
            assert not value.startswith(("[", "{", "&", "*", "!", "|", ">", "@", "`")), \
                "unquoted value starts with a YAML indicator: " + raw
            assert ": " not in value, "unquoted value contains ': ' -- " + raw
        current[key] = value
    return entries


# --- the real repo builds a parseable index --------------------------------
text = build(REPO)
entries = parse(text)
ids = [e["id"] for e in entries]
assert "of-stash-sync" in ids, ids

by_id = {e["id"]: e for e in entries}
for e in entries:
    for field in ("name", "version", "path", "sha256"):
        assert e.get(field), (e["id"], field)
    assert e["path"] == e["id"] + ".zip", e

# --- the deprecated plugin stays published, and says so --------------------
# Removing it from the index would break "Check for Updates" for anyone who
# still has it installed, so it is marked rather than dropped.
if "patreon-stash-sync" in by_id:
    dep = by_id["patreon-stash-sync"]
    assert dep["name"].startswith("DEPRECATED"), dep["name"]
    # ...and not with a YAML indicator, which is what broke the index once
    assert not dep["name"].startswith("["), dep["name"]

# --- wording that WOULD have broken it is now handled ----------------------
fixture = tempfile.mkdtemp()
try:
    shutil.copy(BUILD, os.path.join(fixture, "build_site.sh"))
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    pdir = os.path.join(fixture, "plugins", "nasty")
    os.makedirs(pdir)
    with open(os.path.join(pdir, "nasty.yml"), "w", encoding="utf-8") as f:
        f.write('name: "[DEPRECATED] Thing"\n'
                'description: "Note: it has a colon, a \\"quote\\", and a - dash"\n'
                "version: 1.0.0\n")
    subprocess.run(["git", "add", "-A"], cwd=fixture, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], cwd=fixture, check=True)

    nasty = parse(build(fixture))
    assert len(nasty) == 1, nasty
    # The two that used to produce an unparseable index.
    assert nasty[0]["name"] == "[DEPRECATED] Thing", nasty[0]["name"]
    assert nasty[0]["description"].startswith("Note: it has a colon"), nasty[0]
    assert nasty[0]["description"].endswith("and a - dash"), nasty[0]

    # Pre-existing build_site.sh quirk, asserted so it is known rather than
    # discovered: it extracts name/description with grep + sed, not a YAML
    # parser, so it strips the surrounding quotes but leaves a manifest's own
    # \" escapes as literal backslash-quote. The index is still valid -- the
    # value round-trips exactly as extracted -- but the text carries a stray
    # backslash. Don't write escaped quotes into a manifest name or
    # description; use plain ones, as every manifest here does.
    assert '\\"quote\\"' in nasty[0]["description"], nasty[0]["description"]
finally:
    shutil.rmtree(fixture, ignore_errors=True)

print("ALL OK")
