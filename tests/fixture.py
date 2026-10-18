"""A small fake kiro-iso repo plus a fake pacman sync db, so the tests never touch the real system."""

import tempfile
from pathlib import Path

PACKAGES = """\
#############################################################################
#  TIER 1 — FROZEN — DO NOT REMOVE                                          #
#############################################################################
base
linux
### >>> EDITION-BLOCK xfce >>>
## group: xfce4
#xfwm4
#thunar
## group: xfce4-goodies optional
#mousepad
#ristretto
### <<< EDITION-BLOCK xfce <<<
### >>> EDITION-BLOCK qtile >>>
#kiro-qtile
#thunar
### <<< EDITION-BLOCK qtile <<<

#############################################################################
#  TIER 2 — KIRO CORE / SYSTEM                                              #
#############################################################################
archlinux-tweak-tool
font-y

#############################################################################
#  TIER 3 — USER-CHANGEABLE / OPTIONAL                                      #
#############################################################################
### Utilities
bat
flameshot-git
gimp
### Fonts and groups
foo
mygroup
### >>> EXTRA-APP krita | Krita | extra >>>
#krita
### <<< EXTRA-APP krita <<<
### PERSONAL_REPO
"""

BUILD_CONF = 'editions="xfce qtile"\ndefault_session="xfce"\nkernel="linux"\nnvidia_driver="open"\n'

# {name: (depends, provides)}, the shape functions._sync_db() returns.
SYNC_DB = {
    "base": ([], []),
    "linux": ([], []),
    "xfwm4": ([], []),
    "thunar": ([], []),
    "mousepad": ([], []),
    "ristretto": ([], []),
    "kiro-qtile": (["flameshot", "qtile"], []),
    "qtile": (["python-x"], []),
    "python-x": ([], []),
    "archlinux-tweak-tool": (["bat"], []),
    "bat": ([], []),
    "font-x": ([], ["ttf-font"]),
    "font-y": ([], ["ttf-font"]),
    "flameshot": ([], []),
    "flameshot-git": ([], ["flameshot"]),
    "gimp": (["babl"], []),
    "babl": ([], []),
    "foo": (["ttf-font"], []),
    "tool-a": ([], []),
    "tool-b": ([], []),
    "krita": ([], []),
}

GROUPS = {"tool-a": "mygroup", "tool-b": "mygroup"}


def make_repo(build_conf=BUILD_CONF):
    """Create the fake repo; returns (TemporaryDirectory, build_scripts_path)."""
    tmp = tempfile.TemporaryDirectory(prefix="kib-test-")
    root = Path(tmp.name) / "kiro-iso-next"
    (root / "archiso").mkdir(parents=True)
    (root / "archiso" / "packages.x86_64").write_text(PACKAGES)
    bs = root / "build-scripts"
    bs.mkdir()
    (bs / "build-the-iso.sh").write_text("#!/bin/bash\n")
    (bs / "build.conf").write_text(build_conf)
    return tmp, bs


def install(fn, build_scripts):
    """Point functions.py at the fake repo and the fake sync db."""
    fn.BUILD_SCRIPTS = build_scripts
    fn._SYNC_DB.clear()
    fn._SYNC_DB.update(SYNC_DB)
    fn._GROUPS.clear()
    fn._GROUPS.update(GROUPS)
