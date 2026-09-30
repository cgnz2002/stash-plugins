"""Locate the plugin under test, relative to this file.

The tests live at the repository root rather than inside the plugin directory
on purpose: build_site.sh packages a plugin by running `zip -r` over that whole
directory, so anything kept beside the source ships to every user's Stash.
"""

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "of-stash-sync")


def plugin_file(name):
    """Absolute path to one of the plugin's files.

    Several tests read the source rather than import it -- to assert that a
    GraphQL query still selects a field, or that a guard is present at every
    call site it needs to be at. Those are the checks that would otherwise
    depend on a running Stash.
    """
    return os.path.join(PLUGIN, name)
