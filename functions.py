"""Shared helpers for kiro-iso-builder: repo discovery, runners, config bridge.

Imported everywhere as ``fn``. Runs as the normal user; privileged work is
elevated on demand with ``pkexec`` (discrete fixes) or by answering the build's
own ``sudo`` prompt inside a PTY (the full build). Nothing here assumes a
specific user, home path, or distro — it works on any Arch-based system.
"""

import fcntl
import html
import os
import pty
import re
import shlex
import shutil
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

# ── Paths / repo discovery ──────────────────────────────────────────
# This is the NEMESIS (beta) builder, so it ALWAYS targets the beta repo,
# kiro-iso-next (and uses its own config dir below).
REPO_NAME = "kiro-iso-next"
REPO_URL = f"https://github.com/kirodubes/{REPO_NAME}"

APP_DIR = Path(__file__).resolve().parent
# Own config dir (not the production builder's "kiro-iso-builder") so this beta
# builder never inherits a saved repo path pointing at the production kiro-iso —
# that would silently make it read the wrong repo (no edition blocks).
CONFIG_DIR = Path.home() / ".config" / "kiro-iso-builder-next"


def default_repo_dir():
    """Where the kiro-iso repo is cloned when the user doesn't pick a spot."""
    return Path.home() / REPO_NAME


def repo_path_file():
    return CONFIG_DIR / "repo_path"


def saved_repo_path():
    """The user's chosen kiro-iso repo dir (persisted), or None."""
    f = repo_path_file()
    if f.is_file():
        text = f.read_text().strip()
        if text:
            return Path(text)
    return None


def save_repo_path(path):
    """Persist the kiro-iso repo dir so discovery finds it on the next launch."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    repo_path_file().write_text(str(path) + "\n")


def resolve_repo_dir(picked):
    """Map a Browse selection to the repo dir: the folder itself if it already
    holds the repo, otherwise a kiro-iso clone created under it."""
    picked = Path(picked)
    if (picked / "build-scripts" / "build-the-iso.sh").is_file():
        return picked
    return picked / REPO_NAME


def find_build_scripts():
    """Locate the kiro-iso repo's build-scripts dir on any host, or None."""
    candidates = []
    env = os.environ.get("KIRO_ISO_DIR")
    if env:
        candidates += [Path(env) / "build-scripts", Path(env)]
    saved = saved_repo_path()
    if saved:
        candidates += [saved / "build-scripts", saved]
    # Deliberately NOT APP_DIR.parent/<repo> (the app's sibling): on the dev box
    # that's a Kiro-HQ source folder, and the builder must never adopt, build in,
    # or move it. Default to ~/<repo> — a dedicated clone location outside HQ.
    candidates.append(Path.home() / REPO_NAME / "build-scripts")
    candidates.append(Path("/usr/share") / REPO_NAME / "build-scripts")
    for c in candidates:
        if (c / "build-the-iso.sh").is_file():
            return c
    return None


BUILD_SCRIPTS = find_build_scripts()


def refresh_paths():
    """Re-run discovery after a clone/locate; update module globals."""
    global BUILD_SCRIPTS
    BUILD_SCRIPTS = find_build_scripts()
    ensure_build_conf()
    normalize_build_location()
    return BUILD_SCRIPTS


def build_script():
    return BUILD_SCRIPTS / "build-the-iso.sh" if BUILD_SCRIPTS else None


def host_prep_run():
    return BUILD_SCRIPTS / "host-prep-run.sh" if BUILD_SCRIPTS else None


def unmount_build_script():
    return BUILD_SCRIPTS / "unmount-build.sh" if BUILD_SCRIPTS else None


def build_conf_path():
    return BUILD_SCRIPTS / "build.conf" if BUILD_SCRIPTS else None


def build_conf_defaults_path():
    return BUILD_SCRIPTS / "build.conf.defaults" if BUILD_SCRIPTS else None


def ensure_build_conf():
    """Seed the gitignored live build.conf from build.conf.defaults when missing.

    The live build.conf is the GUI's working copy (gitignored so local tweaks
    never leak into the repo); the tracked defaults carry the canonical values.
    """
    path = build_conf_path()
    defaults = build_conf_defaults_path()
    if path and defaults and defaults.is_file() and not path.is_file():
        shutil.copy(defaults, path)


def normalize_build_location():
    """Force build_location=local in the live build.conf.

    KISS: the build/out folders always sit under the one folder the user picks on
    Pre-flight (next to the clone). We settle that here — where the location is
    established — not on the Configure screen, which the StackSidebar lets a user
    skip on the way to Build."""
    if build_conf_path() and read_conf().get("build_location") != "local":
        set_conf("build_location", "local")


def packages_file():
    """archiso/packages.x86_64 in the kiro-iso repo, or None."""
    return BUILD_SCRIPTS.parent / "archiso" / "packages.x86_64" if BUILD_SCRIPTS else None


def package_selection_path():
    return BUILD_SCRIPTS / "package-selection.conf" if BUILD_SCRIPTS else None


# ── Repo freshness / update (git) ───────────────────────────────────
def repo_dir():
    """The kiro-iso repo root (parent of build-scripts), or None."""
    return BUILD_SCRIPTS.parent if BUILD_SCRIPTS else None


def build_base_dir():
    """The single folder that holds the clone, kiro-build and kiro-Out.

    KISS: everything lives under one folder the user picks on Pre-flight. It's the
    clone's parent once a clone exists; before that, the parent of the chosen (or
    default) clone location, so the disk check has a real target to measure."""
    repo = repo_dir()
    if repo:
        return repo.parent
    return (saved_repo_path() or default_repo_dir()).parent


def build_folder():
    """The mkarchiso work dir, under the chosen build folder."""
    return build_base_dir() / "kiro-build"


def out_folder():
    """The ISO output dir, under the chosen build folder."""
    return build_base_dir() / "kiro-Out"


# ── Per-build logs ──────────────────────────────────────────────────
def logs_dir():
    """~/.config/kiro-iso-builder/logs — one timestamped folder per build."""
    d = CONFIG_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_build_log_dir():
    """Create and return a fresh timestamped log folder for a build."""
    d = logs_dir() / time.strftime("%Y-%m-%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_log_dir():
    """The most recent per-build log folder, or None."""
    subdirs = [p for p in logs_dir().iterdir() if p.is_dir()]
    return max(subdirs, key=lambda p: p.stat().st_mtime) if subdirs else None


def write_build_summary(log_dir, lines):
    """Write summary.txt from a list of 'Key: value' strings."""
    (Path(log_dir) / "summary.txt").write_text("\n".join(lines) + "\n")


def git_fetch(repo, timeout=20):
    """True if `git fetch` succeeds — False on error/offline/timeout (so a
    freshness check degrades gracefully instead of hanging pre-flight)."""
    if not have("git"):
        return False
    try:
        r = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"],
                           capture_output=True, timeout=timeout)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def commits_behind(repo):
    """How many commits the local branch is behind its upstream (0 on any error)."""
    out = cmd_out(["git", "-C", str(repo), "rev-list", "--count", "HEAD..@{u}"])
    return int(out) if out.isdigit() else 0


def repo_is_dirty(repo):
    """True if the working tree has tracked changes (untracked/ignored ignored)."""
    return bool(cmd_out(["git", "-C", str(repo), "status", "--porcelain",
                         "--untracked-files=no"]))


def git_pull_ff_argv(repo):
    return ["git", "-C", str(repo), "pull", "--ff-only"]


def git_force_update_argv(repo, keep_paths):
    """Reset the clone to its upstream, preserving the user's package choices.

    Used when the tree is dirty (build byproducts block a fast-forward). build.conf
    is gitignored so it survives reset untouched; package-selection.conf and
    package-additions.conf are tracked, so each is backed up and restored around the reset.
    """
    repo_q = shlex.quote(str(repo))
    keep = " ".join(shlex.quote(str(p)) for p in keep_paths)
    script = (
        f'bak="$(mktemp -d)"; i=0; for f in {keep}; do i=$((i+1)); '
        f'[ -f "$f" ] && cp "$f" "$bak/$i"; done; '
        f'git -C {repo_q} fetch origin && git -C {repo_q} reset --hard @{{u}} || {{ rm -rf "$bak"; exit 1; }}; '
        f'i=0; for f in {keep}; do i=$((i+1)); [ -f "$bak/$i" ] && cp "$bak/$i" "$f"; done; rm -rf "$bak"; '
        f'echo "Updated to the latest; your package selection and add-ons were preserved."'
    )
    return ["bash", "-c", script]


PROFILES_DIR = CONFIG_DIR / "profiles"


def profiles_dir():
    """~/.config/kiro-iso-builder/profiles — saved package profiles (created on demand)."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILES_DIR


def ensure_app_dirs():
    """Create the app's config/profiles dirs on startup, the way ATT does."""
    profiles_dir()


# ── Small shell helpers ─────────────────────────────────────────────
def have(binary):
    return shutil.which(binary) is not None


def cmd_ok(argv):
    """True if argv exits 0 (stdout/stderr discarded)."""
    try:
        return subprocess.run(argv, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def cmd_out(argv):
    """Stripped stdout of argv, or '' on any failure."""
    try:
        return subprocess.run(argv, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        return ""


# ── build.conf bridge (read / write, comments preserved) ────────────
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_conf():
    """Return build.conf as a {key: value} dict (quotes/comments stripped)."""
    conf = {}
    path = build_conf_path()
    if not path or not path.is_file():
        return conf
    for line in path.read_text().splitlines():
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        val = raw.split("#", 1)[0].strip().strip('"').strip("'")
        conf[key] = val
    return conf


def set_conf(key, value):
    """Set ``key`` in build.conf: rewrite its value in place (keeping any comment),
    or append the key if it isn't present yet (so newly-added knobs persist even on
    an older live build.conf)."""
    path = build_conf_path()
    if not path or not path.is_file():
        return False
    out, changed = [], False
    keyline = re.compile(rf"^(\s*{re.escape(key)}=)(.*)$")
    quoted = " " in value or value == ""
    rendered = f'"{value}"' if quoted else value
    for line in path.read_text().splitlines():
        m = keyline.match(line)
        if m and not changed:
            comment = ""
            rest = m.group(2)
            if "#" in rest:
                comment = "   " + rest[rest.index("#"):]
            out.append(f"{m.group(1)}{rendered}{comment}")
            changed = True
        else:
            out.append(line)
    if not changed:
        out.append(f"{key}={rendered}")
    path.write_text("\n".join(out) + "\n")
    return True


# ── TIER 3 package list (the optional packages, grouped by category) ─
_TIER3_MARKER = "TIER 3"
_END_MARKER = "PERSONAL_REPO"
_CATEGORY_RE = re.compile(r"^###\s+(\S.*)$")
_RULE_RE = re.compile(r"^#{5,}\s*$")


def read_tier3():
    """[(category, [pkg, ...]), ...] of UNCOMMENTED TIER 3 packages, source order.

    Mirrors the kiro-iso gen-streamline-list.py parser: the TIER 3 section is the
    'TIER 3' line directly under a full-width '#' rule, ending at PERSONAL_REPO.
    TIER 1/2 are never returned, so the page can't offer a build-breaking package.
    """
    pf = packages_file()
    if not pf or not pf.is_file():
        return []
    lines = pf.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if _TIER3_MARKER in line and i > 0 and _RULE_RE.match(lines[i - 1]):
            start = i + 1
            break
    if start is None:
        return []
    categories, current = [], None
    for line in lines[start:]:
        if _END_MARKER in line:
            break
        m = _CATEGORY_RE.match(line)
        if m:
            current = (m.group(1).strip(), [])
            categories.append(current)
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current is not None:
            current[1].append(stripped)
    return [(name, pkgs) for name, pkgs in categories if pkgs]


# ── EXTRA APPS (opt-in apps the ISO does NOT ship by default) ────────
_EXTRA_CAT_RE = re.compile(r"^###\s*CATEGORY:\s*(.+?)\s*$")
# Optional 4th field = space-separated edition scope (e.g. "plasma"). Absent = global
# (shown for every edition). The build ignores it — it only matches on the key.
_EXTRA_START_RE = re.compile(
    r"^###\s*>>> EXTRA-APP (\S+)\s*\|\s*(.+?)\s*\|\s*(\S+?)\s*(?:\|\s*(.+?)\s*)?>>>\s*$")
_EXTRA_END_RE = re.compile(r"^###\s*<<< EXTRA-APP (\S+) <<<")
_EXTRA_PKG_RE = re.compile(r"^#([^#\s]\S*)\s*$")


def read_extra_apps():
    """[(category, [{key,label,repo,editions,packages}, ...]), ...] of opt-in EXTRA APPS.

    Parses the commented EXTRA-APP blocks in packages.x86_64 — the build's source of
    truth, the same blocks apply_package_additions() uncomments. Read-only, no root.
    """
    pf = packages_file()
    if pf is None or not pf.is_file():
        return []
    categories, by_name, current_cat, current_app = [], {}, None, None
    for line in pf.read_text().splitlines():
        m = _EXTRA_CAT_RE.match(line)
        if m:
            current_cat = m.group(1)
            if current_cat not in by_name:
                by_name[current_cat] = []
                categories.append((current_cat, by_name[current_cat]))
            continue
        m = _EXTRA_START_RE.match(line)
        if m:
            current_app = {"key": m.group(1), "label": m.group(2), "repo": m.group(3),
                           "editions": m.group(4).split() if m.group(4) else [], "packages": []}
            continue
        if _EXTRA_END_RE.match(line):
            if current_app is not None and current_cat is not None:
                by_name[current_cat].append(current_app)
            current_app = None
            continue
        if current_app is not None:
            mp = _EXTRA_PKG_RE.match(line)
            if mp:
                current_app["packages"].append(mp.group(1))
    return [(cat, apps) for cat, apps in categories if apps]


def extra_app_keys(edition=None):
    """Set of EXTRA-APP keys scoped to `edition` — None = global (unscoped) apps."""
    keys = set()
    for _cat, apps in read_extra_apps():
        for a in apps:
            scoped = (a["editions"] and edition in a["editions"]) if edition else (not a["editions"])
            if scoped:
                keys.add(a["key"])
    return keys


def edition_extras():
    """Sorted list of edition names that have at least one scoped EXTRA-APP block —
    drives which conditional per-edition pages the builder offers."""
    eds = set()
    for _cat, apps in read_extra_apps():
        for a in apps:
            eds.update(a["editions"])
    return sorted(eds)


_EXCLUDE_HEADER = (
    "# Optional packages to EXCLUDE from the ISO (kiro-iso-builder package profile).\n"
    "#\n"
    "# build-the-iso.sh comments each one out in the build-tree copy of\n"
    "# packages.x86_64: TIER 3 lines, and the lines of an edition block's\n"
    "# \"## group: NAME optional\" section (xfce4-goodies). One package name per\n"
    "# line; lines starting with # are ignored. An empty list ships everything.\n"
    "# This same format is build-scripts/package-selection.conf and an importable\n"
    "# saved profile.\n"
    "#\n"
    "# TIER 1 (frozen), TIER 2 (core) and fixed edition packages must never be excluded.\n\n"
)


def read_exclude_file(path):
    """Set of package names in an exclude file (overlay or saved profile)."""
    out = set()
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.add(s)
    return out


def write_exclude_file(path, excludes):
    """Write an exclude file (sorted, with header) to an arbitrary path."""
    body = "\n".join(sorted(excludes))
    Path(path).write_text(_EXCLUDE_HEADER + body + ("\n" if body else ""))


# ── Build profiles (settings + package selection, shareable) ────────
PROFILE_EXT = ".kiroprofile"
# Only ISO-identity settings travel in a shared profile — not host/workflow
# knobs (build_location, clean_pacman_cache, remove_build_folder, bump_version).
PROFILE_SETTINGS_KEYS = ("desktop", "kernel", "nvidia_driver")

_PROFILE_HEADER = (
    "# Kiro ISO build profile — kiro-iso-builder\n"
    "# Reproduces the build recipe (settings + removed packages), not a\n"
    "# byte-identical image (Kiro is rolling — package versions move).\n\n"
)


def write_build_profile(path, settings, excludes):
    """Write a *.kiroprofile: [settings] (ISO knobs) + [packages.excluded]."""
    lines = [_PROFILE_HEADER, "[settings]\n"]
    for key in PROFILE_SETTINGS_KEYS:
        if key in settings:
            val = settings[key]
            rendered = f'"{val}"' if " " in val else val
            lines.append(f"{key}={rendered}\n")
    lines.append("\n[packages.excluded]\n")
    lines += [f"{pkg}\n" for pkg in sorted(excludes)]
    Path(path).write_text("".join(lines))


def read_build_profile(path):
    """Parse a *.kiroprofile into ({settings}, {excludes}); empty on failure."""
    settings, excludes, section = {}, set(), None
    try:
        text = Path(path).read_text()
    except OSError:
        return settings, excludes
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        if section == "settings":
            m = _ASSIGN_RE.match(line)
            if m:
                settings[m.group(1)] = m.group(2).split("#", 1)[0].strip().strip('"').strip("'")
        elif section == "packages.excluded":
            excludes.add(s)
    return settings, excludes


def read_excludes():
    """Set of package names currently excluded in the overlay file."""
    p = package_selection_path()
    return read_exclude_file(p) if p and p.is_file() else set()


def write_excludes(excludes):
    """Write the overlay file (build-scripts/package-selection.conf)."""
    p = package_selection_path()
    if not p:
        return False
    write_exclude_file(p, excludes)
    return True


# ── EXTRA APPS additions overlay (the "Add apps" page) ──────────────
_ADDITIONS_HEADER = (
    "# EXTRA APPS to ADD to the ISO (kiro-iso-builder \"Add apps\" page).\n"
    "#\n"
    "# build-the-iso.sh's apply_package_additions() uncomments the matching EXTRA-APP\n"
    "# block in packages.x86_64. One app KEY per line (e.g. wps, ollama); lines starting\n"
    "# with # are ignored. An empty list adds nothing — the standard ISO.\n\n"
)


def additions_path():
    return BUILD_SCRIPTS / "package-additions.conf" if BUILD_SCRIPTS else None


def read_additions_file(path):
    """Set of app keys in an additions file (overlay or saved list)."""
    out = set()
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.add(s)
    return out


def write_additions_file(path, keys):
    """Write an additions file (sorted, with header) to an arbitrary path."""
    body = "\n".join(sorted(keys))
    Path(path).write_text(_ADDITIONS_HEADER + body + ("\n" if body else ""))


def read_additions():
    """Set of app keys currently in the additions overlay file."""
    p = additions_path()
    return read_additions_file(p) if p and p.is_file() else set()


def write_additions(keys):
    """Write the additions overlay file (build-scripts/package-additions.conf)."""
    p = additions_path()
    if not p:
        return False
    write_additions_file(p, keys)
    return True


def reconcile_additions(catalog_keys, ticked_keys):
    """Merge one Extras page's selection into the shared additions file without clobbering
    the others. Replaces only the keys this page owns (catalog_keys) with its ticked set,
    leaving every other page's keys untouched. Catalogs are disjoint (global vs per-edition),
    so each page reconciles independently."""
    kept = read_additions() - set(catalog_keys)
    return write_additions(kept | set(ticked_keys))


# ── Packages that come back after being unticked ────────────────────
# Unticking only comments out the TIER 3 line. A package still lands on the ISO when
# a ticked edition block lists it, another shipped package depends on it, or it is a
# member of a shipped package group (xfce4 → mousepad, ristretto, parole).
def read_tiers():
    """(tier1, tier2): uncommented FROZEN and KIRO CORE package names, source order."""
    pf = packages_file()
    if not pf or not pf.is_file():
        return [], []
    lines = pf.read_text().splitlines()
    tiers = ([], [])
    current = tiers[0]
    for i, line in enumerate(lines):
        header = i > 0 and _RULE_RE.match(lines[i - 1])
        if header and _TIER3_MARKER in line:
            break
        if header and "TIER 2" in line:
            current = tiers[1]
        s = line.strip()
        if s and not s.startswith("#"):
            current.append(s)
    return tiers


def read_core():
    """Uncommented TIER 1 + TIER 2 package names — the core that always ships."""
    tier1, tier2 = read_tiers()
    return tier1 + tier2


# An edition block can list a package group member by member; "optional" sections can be unticked.
_GROUP_RE = re.compile(r"^##\s+group:\s+(\S+)(\s+optional)?\s*$")


def read_edition_optional(editions):
    """[(category, [pkg, ...])] for the optional group sections of the ticked editions."""
    pf = packages_file()
    if not pf or not pf.is_file():
        return []
    out, block, current = [], None, None
    for line in pf.read_text().splitlines():
        m = re.match(r"^###\s*>>> EDITION-BLOCK (\S+) >>>", line)
        if m:
            block, current = m.group(1), None
            continue
        if line.startswith("###"):
            block = current = None
            continue
        m = _GROUP_RE.match(line)
        if m:
            current = None
            if block in editions and m.group(2):
                current = (f"{block.upper()} extras ({m.group(1)})", [])
                out.append(current)
            continue
        mp = _EXTRA_PKG_RE.match(line.strip())
        if current is not None and mp:
            current[1].append(mp.group(1))
    return out


def read_edition_fixed(editions):
    """{edition: [pkg, ...]} for the lines of the ticked editions that always ship (not in an optional section)."""
    pf = packages_file()
    if not pf or not pf.is_file():
        return {}
    out, block, optional = {}, None, False
    for line in pf.read_text().splitlines():
        m = re.match(r"^###\s*>>> EDITION-BLOCK (\S+) >>>", line)
        if m:
            block, optional = m.group(1), False
            continue
        if line.startswith("###"):
            block = None
            continue
        m = _GROUP_RE.match(line)
        if m:
            optional = bool(m.group(2))
            continue
        mp = _EXTRA_PKG_RE.match(line.strip())
        if block in editions and mp and not optional:
            out.setdefault(block, []).append(mp.group(1))
    return out


def _shipped_names(excludes, editions):
    """{name: source} for every line that will be uncommented in the build's packages.x86_64."""
    pf = packages_file()
    if not pf or not pf.is_file():
        return {}
    ticked_extras = read_additions()
    names, block, block_kind, optional = {}, None, None, False
    for line in pf.read_text().splitlines():
        m = re.match(r"^###\s*>>> EDITION-BLOCK (\S+) >>>", line)
        if m:
            block, block_kind, optional = m.group(1), "edition", False
            continue
        m = _GROUP_RE.match(line)
        if m:
            optional = bool(m.group(2))
            continue
        m = _EXTRA_START_RE.match(line)
        if m:
            block, block_kind = m.group(1), "extra"
            continue
        if re.match(r"^###\s*<<< (EDITION-BLOCK|EXTRA-APP) ", line):
            block = block_kind = None
            continue
        s = line.strip()
        if block is not None:
            mp = _EXTRA_PKG_RE.match(s)
            if not mp:
                continue
            if block_kind == "edition" and block in editions:
                if optional and mp.group(1) in excludes:
                    continue
                names.setdefault(mp.group(1), f"listed by the {block} edition")
            elif block_kind == "extra" and block in ticked_extras:
                names.setdefault(mp.group(1), f"part of the {block} add-on")
        elif s and not s.startswith("#") and s not in excludes:
            names.setdefault(s, "")
    return names


_SYNC_DB = {}
_CACHE_LOCK = threading.Lock()
_DEP_NAME_RE = re.compile(r"[<>=:].*$")


def _sync_db():
    """{name: (depends, provides)} for the sync db, first repo wins; cached."""
    if not _SYNC_DB:
        name, field = None, None
        for line in cmd_out(["env", "LC_ALL=C", "pacman", "-Si"]).splitlines():
            key, sep, val = line.partition(" : ")
            if sep and not key.startswith(" "):
                field = key.strip()
                if field == "Name":
                    name = val.strip()
                    if name in _SYNC_DB:
                        name = None
                    else:
                        _SYNC_DB[name] = ([], [])
                    continue
            elif line.startswith(" "):
                val = line
            else:
                continue
            if name and field in ("Depends On", "Provides"):
                words = [_DEP_NAME_RE.sub("", w) for w in val.split() if w != "None"]
                _SYNC_DB[name][0 if field == "Depends On" else 1].extend(words)
    return _SYNC_DB


_GROUPS = {}


def group_members():
    """{member: group} for every sync-db package group; cached."""
    if _GROUPS:
        return _GROUPS
    members = _GROUPS
    names = cmd_out(["pacman", "-Sg"]).split()
    for ln in cmd_out(["pacman", "-Sg", *names]).splitlines() if names else []:
        group, _, pkg = ln.partition(" ")
        if pkg:
            members.setdefault(pkg, group)
    return members


def kept_packages(excludes, editions):
    """{pkg: reason} for the excluded packages the ISO will still ship (or ship a variant of).

    Resolves dependencies like pacman: an already-selected package satisfies a virtual
    dependency before any other provider is pulled in. The first call is slow (two pacman
    queries, then cached) — make it from a thread."""
    with _CACHE_LOCK:
        db = _sync_db()
        groups = group_members()
    parent = _resolve(_shipped_names(excludes, editions), excludes, db, groups)
    kept = {}
    for pkg in sorted(excludes):
        if pkg in parent:
            kept[pkg] = parent[pkg] or "listed elsewhere in packages.x86_64"
            continue
        names = {pkg, *db.get(pkg, ((), ()))[1]}
        variant = next((q for q in parent if q in names or pkg in db.get(q, ((), ()))[1]), None)
        if variant:
            kept[pkg] = f"{variant} ships instead ({parent[variant]})"
    return kept


def always_shipped(row_pkgs, editions):
    """{pkg: reason} for the optional rows that ship even with every row unticked.

    Their reason is core, a ticked edition or a ticked add-on — nothing on the Packages page
    can remove them, so the page hides them instead of offering a checkbox that does nothing."""
    return kept_packages(set(row_pkgs), editions)


def package_rows(editions):
    """[(category, [pkg, ...])] for the Packages page: optional edition sections, then TIER 3.

    One row per package: a name that is both an edition extra and a TIER 3 line (baobab in gnome)
    keeps its TIER 3 row — one exclusion drops it from both places in the build."""
    tier3 = read_tier3()
    in_tier3 = {p for _c, pkgs in tier3 for p in pkgs}
    extras = [(c, [p for p in pkgs if p not in in_tier3]) for c, pkgs in read_edition_optional(editions)]
    return [(c, pkgs) for c, pkgs in extras if pkgs] + tier3


def optional_rows(editions):
    """Every package name the Packages page can show for these editions."""
    return [p for _c, pkgs in package_rows(editions) for p in pkgs]


def reported_kept(excludes, editions):
    """kept_packages() minus the always-shipped rows the Packages page hides.

    A selection saved before a row was hidden (or an imported profile) can still exclude it;
    the Build page and the summary don't warn about a package the user can't see."""
    hidden = always_shipped(optional_rows(editions), editions)
    return {p: r for p, r in kept_packages(excludes, editions).items() if p not in hidden}


def _resolve(shipped, excludes, db, groups):
    """{pkg: reason} for every package the selection installs, dependencies included."""
    by_group = {}
    for m, g in groups.items():
        by_group.setdefault(g, []).append(m)

    parent = {}
    for name, why in shipped.items():
        # pacman installs a real package before a group of the same name.
        if name in db or name not in by_group:
            parent.setdefault(name, why)
            continue
        for pkg in by_group[name]:
            parent.setdefault(pkg, f"part of the {name} group")
    provided = {}
    for pkg in parent:
        for v in db.get(pkg, ((), ()))[1]:
            provided.setdefault(v, pkg)
    providers = {}
    for pkg, (_deps, provs) in db.items():
        for v in provs:
            providers.setdefault(v, []).append(pkg)

    queue = list(parent)
    while queue:
        pkg = queue.pop()
        for dep in db.get(pkg, ((), ()))[0]:
            if dep in parent or dep in provided:
                continue
            if dep in db:
                pick = dep
            else:
                cands = providers.get(dep, [])
                pick = next((c for c in cands if c not in excludes), cands[0] if cands else None)
            if pick is None or pick in parent:
                continue
            parent[pick] = f"required by {pkg}"
            for v in db[pick][1]:
                provided.setdefault(v, pick)
            queue.append(pick)
    return parent


# ── Build summary page ──────────────────────────────────────────────
def build_summary(excludes, editions):
    """Everything the summary page shows, as one dict. Slow on the first call — use a thread."""
    with _CACHE_LOCK:
        db = _sync_db()
        groups = group_members()
    conf = read_conf()
    tier1, tier2 = read_tiers()
    shipped = _shipped_names(excludes, editions)
    all_kept = kept_packages(excludes, editions)
    kept = reported_kept(excludes, editions)

    by_block = {}
    for name, why in shipped.items():
        if why:
            label = why.removeprefix("listed by the ").removeprefix("part of the ")
            by_block.setdefault(label, []).append(name)

    # Only what the user's choices add: the core's own dependencies (glibc, systemd…) are noise here.
    full = _resolve(shipped, excludes, db, groups)
    core_only = _resolve(dict.fromkeys(tier1 + tier2, ""), excludes, db, groups)
    pulled_in = {p: why for p, why in sorted(full.items())
                 if p not in core_only and p not in shipped and p not in excludes}

    return {
        "settings": {
            "Editions": " ".join(editions) or "-",
            "Default session": conf.get("default_session", "-"),
            "Kernel": conf.get("kernel", "-"),
            "NVIDIA driver": conf.get("nvidia_driver", "-"),
        },
        "frozen": tier1,
        "core": tier2,
        "blocks": by_block,
        "removed": sorted(p for p in excludes if p not in all_kept),
        "kept": kept,
        "pulled_in": pulled_in,
        "source": str(packages_file() or "?"),
    }


_SUMMARY_CSS = """
:root { --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --dim:#667085; --line:#e3e6ec;
        --green:#1a7f4b; --orange:#b45309; --blue:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --card:#1d2128; --fg:#e6e9ef; --dim:#98a2b3; --line:#2c323c;
          --green:#4ade80; --orange:#fbbf24; --blue:#60a5fa; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { margin: 0 0 4px; font-size: 26px; }
h2 { margin: 0 0 10px; font-size: 18px; }
.sub, footer { color: var(--dim); }
section, details { background:var(--card); border:1px solid var(--line); border-radius:10px;
                   padding:16px 18px; margin-top:16px; }
summary { cursor:pointer; font-weight:600; font-size:17px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; margin-top:12px; }
.tile { border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.tile b { display:block; font-size:24px; }
table.settings { width:auto; }
.settings td:first-child { color:var(--dim); padding-right:28px; }
footer { overflow-wrap:anywhere; }
table { border-collapse: collapse; width:100%; }
.rows td { padding:5px 8px; border-top:1px solid var(--line); vertical-align:top; }
.rows td:first-child { font-family: ui-monospace, monospace; white-space:nowrap; width:1%; padding-right:24px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chips span { font-family: ui-monospace, monospace; font-size:13px; border:1px solid var(--line);
              border-radius:6px; padding:2px 8px; }
.removed h2 { color: var(--green); }
.kept h2 { color: var(--orange); }
.kept { border-left: 4px solid var(--orange); }
.note { color: var(--dim); margin: 0 0 10px; }
#q { width:100%; padding:9px 12px; border-radius:8px; border:1px solid var(--line);
     background:var(--card); color:var(--fg); font-size:15px; margin-top:16px; }
.hide { display:none !important; }
"""

_SUMMARY_JS = """
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('[data-text]').forEach(el =>
    el.classList.toggle('hide', q !== '' && !el.dataset.text.includes(q)));
  if (q) document.querySelectorAll('details').forEach(d => d.open = true);
});
"""


def _summary_html(s):
    esc = html.escape

    def chips(pkgs):
        return '<div class="chips">' + "".join(
            f'<span data-text="{esc(p.lower())}">{esc(p)}</span>' for p in pkgs) + "</div>"

    def rows(mapping):
        return '<table class="rows">' + "".join(
            f'<tr data-text="{esc((p + " " + why).lower())}"><td>{esc(p)}</td><td>{esc(why)}</td></tr>'
            for p, why in mapping.items()) + "</table>"

    def section(cls, title, note, body):
        note_html = f'<p class="note">{note}</p>' if note else ""
        return f'<section class="{cls}"><h2>{title}</h2>{note_html}{body}</section>'

    def folded(title, note, body):
        return f'<details><summary>{title}</summary><p class="note">{note}</p>{body}</details>'

    tiles = [("Frozen", len(s["frozen"])), ("Core", len(s["core"])), ("Removed", len(s["removed"])),
             ("Comes back", len(s["kept"])), ("Pulled in", len(s["pulled_in"]))]
    settings = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in s["settings"].items())
    parts = [
        "<h1>Kiro ISO build summary</h1>",
        '<p class="sub">What this build puts on the ISO, and why.</p>',
        f'<section><h2>This build</h2><table class="settings">{settings}</table>'
        '<div class="tiles">' + "".join(f"<div class=\"tile\"><b>{n}</b>{t}</div>" for t, n in tiles)
        + "</div></section>",
        '<input id="q" type="search" placeholder="Search packages or reasons...">',
    ]
    parts.append(section(
        "removed", f"Removed ({len(s['removed'])})",
        "You unticked these and nothing else needs them, so they are not on the ISO.",
        chips(s["removed"]) if s["removed"] else "<p>Nothing removed.</p>"))
    parts.append(section(
        "kept", f"Comes back anyway ({len(s['kept'])})",
        "You unticked these, but they still ship. The reason is on the right.",
        rows(s["kept"]) if s["kept"] else "<p>Every unticked package is really gone.</p>"))
    for label, pkgs in s["blocks"].items():
        parts.append(section("", f"{esc(label[0].upper() + label[1:])} ({len(pkgs)})", "", chips(pkgs)))
    parts.append(folded(
        f"Pulled in by your choices ({len(s['pulled_in'])})",
        "Dependencies of your editions, add-ons and optional apps that the core alone would not bring.",
        rows(s["pulled_in"])))
    parts.append(folded(
        f"Frozen, TIER 1 ({len(s['frozen'])})",
        "Always ship. Removing any of these breaks the build, the live boot or the installer.",
        chips(s["frozen"])))
    parts.append(folded(
        f"Core, TIER 2 ({len(s['core'])})",
        "Always ship. The base of every Kiro system.",
        chips(s["core"])))
    parts.append(f"<footer><p>Generated {esc(time.strftime('%Y-%m-%d %H:%M'))} from {esc(s['source'])}</p></footer>")

    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Kiro ISO build summary</title><style>{_SUMMARY_CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main><script>{_SUMMARY_JS}</script></body></html>\n")


def write_summary_html(summary, path=None):
    """Write the summary page (default: the config dir) and return its path."""
    path = Path(path) if path else CONFIG_DIR / "build-summary.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_summary_html(summary))
    return path


# ── Runner 1: pipe (pkexec fixes, clone) — non-tty is fine ──────────
def run_pipe(argv, on_line, on_done, cwd=None):
    """Run argv in a daemon thread, streaming stdout lines to the GTK loop."""
    def worker():
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except FileNotFoundError as exc:
            GLib.idle_add(on_line, f"[error] {exc}")
            GLib.idle_add(on_done, 127)
            return
        for line in proc.stdout:
            GLib.idle_add(on_line, line.rstrip("\n"))
        proc.wait()
        GLib.idle_add(on_done, proc.returncode)

    threading.Thread(target=worker, daemon=True).start()


def run_hostprep_fix(args, on_line, on_done):
    """pkexec the host-prep dispatcher: one polkit prompt, runs as root."""
    runner = host_prep_run()
    if runner is None:
        GLib.idle_add(on_line, f"[error] {REPO_NAME} clone not found")
        GLib.idle_add(on_done, 1)
        return
    run_pipe(["pkexec", "bash", str(runner), *args], on_line, on_done)


def stale_mounts_present():
    """True if mkarchiso bind-mounts are still live under the work dir.

    Runs unmount-build.sh in read-only ``check`` mode as the user (no elevation),
    so it can gate the privileged cleanup without ever popping a polkit prompt.
    """
    script = unmount_build_script()
    if script is None or not script.is_file():
        return False
    return not cmd_ok(["bash", str(script), "check"])


def run_cleanup_mounts(on_line, on_done):
    """pkexec the build-mount cleanup: one polkit prompt, unmounts as root."""
    script = unmount_build_script()
    if script is None:
        GLib.idle_add(on_line, "[error] unmount-build.sh not found")
        GLib.idle_add(on_done, 1)
        return
    run_pipe(["pkexec", "bash", str(script), "clean"], on_line, on_done)


def build_folder_present():
    """True if the kiro-build work dir exists (a leftover to clean up)."""
    bf = build_folder()
    return bool(bf and bf.exists())


def build_folder_human_size():
    """Best-effort human size of the build folder ('' if unknown).

    Run as the user, so root-owned subdirs may make this approximate — it's only
    for the confirmation dialog, never for any decision.
    """
    bf = build_folder()
    if not bf or not bf.exists():
        return ""
    out = cmd_out(["du", "-sh", str(bf)])
    return out.split("\t")[0].split()[0] if out else ""


def run_remove_build_folder(on_line, on_done):
    """pkexec: unmount any stale mounts (via unmount-build.sh clean) then rm -rf
    the build folder. DESTRUCTIVE — the GUI must confirm with the user first."""
    bf = build_folder()
    script = unmount_build_script()
    if bf is None or script is None:
        GLib.idle_add(on_line, "[error] build folder / unmount-build.sh path unknown")
        GLib.idle_add(on_done, 1)
        return
    bf_q = shlex.quote(str(bf))
    sc_q = shlex.quote(str(script))
    # Unmount first (reuses the proven loop), then remove the now-quiet folder.
    cmd = (f'bash {sc_q} clean; '
           f'[ -d {bf_q} ] && rm -rf {bf_q} && echo "Removed {bf}" || echo "Nothing to remove: {bf}"')
    run_pipe(["pkexec", "bash", "-c", cmd], on_line, on_done)


# ── Runner 2: PTY (the build) — gives sudo a tty, smooth streaming ──
# We force the build's sudo to use this sentinel as its prompt (SUDO_PROMPT),
# so detection is locale-proof. Fall back to the default English prompt too.
SUDO_SENTINEL = "kiro-iso-builder: password required"
_PROMPT_RE = re.compile(re.escape(SUDO_SENTINEL).encode() + rb"|\[sudo\] password")


def _child_make_ctty(slave_fd):
    """preexec: new session + make the PTY slave our controlling terminal.

    start_new_session alone (setsid) detaches the controlling tty, which would
    leave sudo with 'no tty present'. TIOCSCTTY re-attaches the slave as the
    ctty so sudo can prompt, while the new session still lets us killpg on Stop.
    """
    os.setsid()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    except OSError:
        pass


def run_in_pty(argv, cwd, on_line, on_done, ask_password, on_start=None):
    """Run argv under a PTY so the build's internal ``sudo`` can prompt once.

    ``ask_password(prompt)`` is called from this worker thread when sudo asks;
    it must block and return the password string, or None to cancel.
    ``on_start(proc)`` (optional) receives the Popen so the caller can stop it.
    """
    def worker():
        master, slave = pty.openpty()
        # Give the PTY a real window size, else pacman/mkarchiso can't size their
        # progress bars and spam the same line hundreds of times.
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        except OSError:
            pass
        env = dict(os.environ, SUDO_PROMPT=SUDO_SENTINEL)
        # The PTY makes the build's `[[ -t 1 ]]` true, so it runs tput. Desktop
        # sessions (e.g. MATE) launch GUI apps with TERM unset OR set to junk
        # ("unknown"/"dumb"), which makes tput abort the build under `set -e`.
        # We own this PTY, so force a known-good terminal type.
        env["TERM"] = "xterm-256color"
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, env=env,
                preexec_fn=lambda: _child_make_ctty(slave),
            )
        except FileNotFoundError as exc:
            os.close(master)
            os.close(slave)
            GLib.idle_add(on_line, f"[error] {exc}")
            GLib.idle_add(on_done, 127)
            return
        def send(text):
            try:
                os.write(master, text.encode())
            except OSError:
                pass

        if on_start:
            on_start(proc, send)
        os.close(slave)
        buf = b""
        try:
            while True:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                # prompt detected: matched text with no trailing newline = awaiting input
                if _PROMPT_RE.search(buf) and not buf.endswith(b"\n"):
                    pw = ask_password("Enter your password to continue the build (sudo).")
                    if pw is None:
                        proc.terminate()
                        buf = b""
                        continue
                    os.write(master, (pw + "\n").encode())
                    buf = b""
                    continue
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    # Drop the trailing CR the PTY's ONLCR adds (\n -> \r\n), then
                    # collapse an in-place progress bar (redraws with '\r') to its
                    # final state so one package = one line, not hundreds.
                    text = line.decode(errors="replace").rstrip("\r")
                    if "\r" in text:
                        text = text.rsplit("\r", 1)[-1]
                    GLib.idle_add(on_line, text)
        finally:
            os.close(master)
            proc.wait()
            if buf.strip():
                GLib.idle_add(on_line, buf.decode(errors="replace"))
            GLib.idle_add(on_done, proc.returncode)

    threading.Thread(target=worker, daemon=True).start()


# ── GTK password dialog, callable synchronously from a worker thread ─
def ask_password(parent, prompt):
    """Show a modal password entry; block the caller until answered.

    Safe to call off the GTK thread — it marshals the dialog onto the main
    loop and waits on an Event. Returns the password, or None if cancelled.
    """
    result = {"pw": None}
    done = threading.Event()

    def show():
        dialog = Gtk.Dialog(title="Authentication required", transient_for=parent, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Authenticate", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_spacing(8)
        box.append(Gtk.Label(label=prompt or "Enter your password", xalign=0))
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        box.append(entry)

        def on_response(dlg, resp):
            result["pw"] = entry.get_text() if resp == Gtk.ResponseType.OK else None
            dlg.destroy()
            done.set()

        dialog.connect("response", on_response)
        dialog.present()
        return False

    GLib.idle_add(show)
    done.wait()
    return result["pw"]


def list_kernels():
    """Kernels the repos offer that have a matching -headers (via list-kernels.sh).

    Same filter the build uses, so the GUI never offers a kernel the build would
    reject. Read-only, no root. Returns [] if the repo/script isn't found.
    """
    if BUILD_SCRIPTS is None:
        return []
    script = BUILD_SCRIPTS / "list-kernels.sh"
    if not script.is_file():
        return []
    out = cmd_out(["bash", str(script)])
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def list_kernels_with_repo():
    """Same kernels as list_kernels(), tagged with their source repo.

    Runs list-kernels.sh --with-repo and returns [(repo, kernel), …]. The GUI
    uses the repo tag to group kernels by source. Returns [] if the repo/script
    isn't found.
    """
    if BUILD_SCRIPTS is None:
        return []
    script = BUILD_SCRIPTS / "list-kernels.sh"
    if not script.is_file():
        return []
    out = cmd_out(["bash", str(script), "--with-repo"])
    pairs = []
    for ln in out.splitlines():
        repo, _, name = ln.partition("\t")
        if name:
            pairs.append((repo.strip(), name.strip()))
    return pairs


def list_editions():
    """WM/desktop editions the ISO offers — the EDITION-BLOCK names in
    packages.x86_64 (the build's source of truth, same blocks the build toggles).
    Read-only, no root. Returns [] if the repo/file isn't found.
    """
    pf = packages_file()
    if pf is None or not pf.is_file():
        return []
    return re.findall(r"^###\s*>>> EDITION-BLOCK (\S+) >>>", pf.read_text(), re.M)


def max_build_phase(default=12):
    """Highest 'Phase N' number in build-the-iso.sh, so progress never overflows."""
    script = build_script()
    if not script or not script.is_file():
        return default
    nums = [int(n) for n in re.findall(r"Phase (\d+)", script.read_text())]
    return max(nums) if nums else default


# OSC strings (\x1b]…) terminated by either BEL (\x07) or ST (\x1b\\) — sudo's
# command-tracking marker (]3008;start=…;comm=sudo;…) uses ST, so a BEL-only
# match left it leaking into the log as literal text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][AB0-2]")


def strip_ansi(text):
    """Remove terminal colour/control escapes so the GTK log reads cleanly."""
    return _ANSI_RE.sub("", text)


def open_path(path):
    """Open a folder in the file manager (Wayland-safe).

    Goes through the freedesktop ``org.freedesktop.FileManager1`` D-Bus interface
    so it always lands in a real file manager — not whatever ``inode/directory`` is
    bound to (some systems map it to a disk-usage analyzer, which is wrong here).
    Falls back to a known file-manager binary, then ``xdg-open``.
    """
    p = Path(path)
    uri = p.resolve().as_uri()
    if have("gdbus"):
        rc = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowFolders",
             f"['{uri}']", ""],
            capture_output=True).returncode
        if rc == 0:
            return
    for fm in ("thunar", "nemo", "nautilus", "dolphin", "pcmanfm-qt", "pcmanfm", "caja"):
        if have(fm):
            subprocess.Popen([fm, str(p)])
            return
    if have("xdg-open"):
        subprocess.Popen(["xdg-open", str(p)])
