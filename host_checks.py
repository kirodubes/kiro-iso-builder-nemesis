"""Pre-flight host checks for kiro-iso-builder.

Each check detects a condition and, where possible, names a one-click fix that
maps to an existing host-prep.sh function — so a host fixed in the GUI is
identical to one prepared by the CLI build. Works on any Arch-based host.
"""

import os
import re
import shutil

import functions as fn
from kib_i18n import _, N_, ngettext

OK, WARN, FAIL = "ok", "warn", "fail"

# Fix descriptors the pre-flight screen knows how to run:
#   ("hostprep", [args])  -> pkexec bash host-prep-run.sh <args>
#   ("clone",)            -> git clone the kiro-iso repo (no root)
#   None                  -> not auto-fixable


def _pkg(name):
    return fn.cmd_ok(["pacman", "-Qi", name])


def _os_release():
    data = {}
    try:
        for line in open("/etc/os-release", encoding="utf-8"):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


def _free_gb(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1_000_000_000
    except OSError:
        return 0.0


# ── Individual checks → (status, detail, fix) ───────────────────────
def check_repo():
    if fn.BUILD_SCRIPTS:
        return OK, _("Found at {path}").format(path=fn.BUILD_SCRIPTS), None
    return FAIL, _("{repo} clone not found; everything is built from it.").format(repo=fn.REPO_NAME), ("clone",)


def check_repo_uptodate():
    # Tell the user when their kiro-iso clone is behind origin so they don't
    # build from stale scripts/package lists. Fetches; offline degrades to a WARN.
    repo = fn.repo_dir()
    if repo is None:
        return OK, _("Set once the {repo} clone is present.").format(repo=fn.REPO_NAME), None
    if not fn.have("git"):
        return WARN, _("git is unavailable; cannot check for updates."), None
    if not fn.git_fetch(repo):
        return WARN, _("Could not check for updates (offline?)."), None
    n = fn.commits_behind(repo)
    if n == 0:
        return OK, _("Up to date with origin."), None
    return WARN, ngettext(
        "{count} update available; update for the latest build scripts and packages.",
        "{count} updates available; update for the latest build scripts and packages.", n).format(count=n), (
            "update",)


def check_not_root():
    if os.geteuid() != 0:
        return OK, _("Running as a normal user"), None
    return FAIL, _("Do NOT run the builder as root; relaunch as your user."), None


def check_arch():
    osr = _os_release()
    idlike = f"{osr.get('ID', '')} {osr.get('ID_LIKE', '')}".lower()
    if "arch" in idlike:
        return OK, f"{osr.get('PRETTY_NAME', 'Arch-based')}", None
    return FAIL, _("{name} is not Arch-based.").format(name=osr.get("PRETTY_NAME", "Unknown")), None


def check_polkit():
    # pkexec needs an authentication agent running in the session.
    if not fn.have("pkexec"):
        return FAIL, _("pkexec is not installed; fixes cannot elevate."), None
    agents = ("polkit-gnome-au", "polkit-kde-auth", "lxpolkit",
              "polkit-mate-aut", "xfce-polkit", "polkitd")
    running = fn.cmd_out(["pgrep", "-l", "-f", "polkit"])
    if any(a in running for a in agents) or running:
        return OK, _("polkit agent present."), None
    return WARN, _("No polkit agent detected; fix prompts may not appear."), None


def _archiso_required():
    # build-the-iso.sh's check_archiso_version owns the minimum; read it from the
    # clone so the GUI and the CLI build can never disagree. None = no minimum.
    script = fn.build_script()
    if script is None:
        return None
    try:
        match = re.search(r'local required="([^"]+)"', script.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def _hostprep_has(func):
    script = fn.BUILD_SCRIPTS / "host-prep.sh" if fn.BUILD_SCRIPTS else None
    try:
        return script is not None and f"\n{func}() {{" in script.read_text(encoding="utf-8")
    except OSError:
        return False


def check_archiso():
    if not _pkg("archiso"):
        return FAIL, _("archiso is missing."), ("hostprep", ["ensure_package", "archiso"])
    installed = fn.cmd_out(["pacman", "-Q", "archiso"]).split()[-1]
    required = _archiso_required()
    if required and int(fn.cmd_out(["vercmp", installed, required])) < 0:
        # The fix is host-prep's upgrade_system; an older clone lacks it, and Fix all never
        # updates the clone, so say so instead of failing silently.
        if not _hostprep_has("upgrade_system"):
            return FAIL, _("archiso {installed} is too old; needs {required}+; update the "
                           "{repo} clone first (row above).").format(
                               installed=installed, required=required, repo=fn.REPO_NAME), None
        # The fix is a full system upgrade: upgrading archiso alone would be a partial upgrade.
        return FAIL, _("archiso {installed} is too old; needs {required}+; Fix runs a full "
                       "system upgrade.").format(installed=installed, required=required), (
                           "hostprep", ["upgrade_system"])
    return OK, _("archiso {version} installed.").format(version=installed), None


def check_grub():
    return (OK, _("grub installed."), None) if _pkg("grub") else \
        (FAIL, _("grub is missing."), ("hostprep", ["ensure_package", "grub"]))


def check_chaotic():
    have = _pkg("chaotic-keyring") and _pkg("chaotic-mirrorlist")
    return (OK, _("Chaotic-AUR keyring and mirrorlist present."), None) if have else \
        (FAIL, _("Chaotic-AUR keyring or mirrorlist is missing."), ("hostprep", ["setup_chaotic"]))


def check_cachyos():
    have = _pkg("cachyos-keyring") and _pkg("cachyos-mirrorlist")
    return (OK, _("CachyOS keyring and mirrorlist present."), None) if have else \
        (FAIL, _("CachyOS keyring or mirrorlist is missing (linux-cachyos is hosted there)."),
         ("hostprep", ["setup_cachyos"]))


def check_disk():
    # Measure the filesystem that actually holds the build folder the user chose,
    # not always $HOME — they may build on a separate disk (e.g. /DATA).
    base = fn.build_base_dir()
    probe = base
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = _free_gb(str(probe))
    if free >= 15:
        return OK, _("{free:.0f} GB free in {path}.").format(free=free, path=base), None
    return WARN, _("Only {free:.0f} GB free in {path} (about 15 GB recommended).").format(
        free=free, path=base), None


def check_kernels():
    if fn.build_conf_path() is None:
        return OK, _("Set once the {repo} clone is present.").format(repo=fn.REPO_NAME), None
    raw = fn.read_conf().get("kernel", "").strip()
    if raw == "ask":
        return OK, _("Kernel picker set to 'ask' (chosen at build time)."), None
    tokens = [t for t in raw.split() if t and t != "ask"]
    if not tokens:
        return WARN, _("No kernel set in build.conf."), None
    if not fn.have("pacman"):
        return WARN, _("pacman is unavailable."), None
    available = set(fn.cmd_out(["pacman", "-Slq"]).split())
    missing = [t for t in tokens if t not in available]
    if missing:
        # A cachyos-only kernel missing because [cachyos] is disabled (Kiro's
        # default) is fixable: the keyring/mirrorlist are present, the repo is
        # just commented out. Offer to enable it — only in that exact case, so a
        # normal build is never nagged about the intentional default.
        cachyos_missing = [t for t in missing if t.startswith("linux-cachyos")]
        cachyos_ready = _pkg("cachyos-keyring") and _pkg("cachyos-mirrorlist")
        cachyos_off = not fn.cmd_ok(["pacman", "-Sl", "cachyos"])
        if cachyos_missing and cachyos_ready and cachyos_off:
            return (WARN,
                    _("{kernels} need the [cachyos] repo; it is disabled in pacman.conf "
                      "(Kiro's default). Enable it to build with this kernel.").format(
                          kernels=", ".join(cachyos_missing)),
                    ("hostprep", ["enable_cachyos"]))
        return WARN, _("Kernel(s) not in synced repos: {kernels}").format(kernels=", ".join(missing)), None
    return OK, _("Kernel(s) resolve: {kernels}").format(kernels=", ".join(tokens)), None


def check_stale_mounts():
    # Leftover mkarchiso bind-mounts from an earlier stopped/crashed build break
    # the next build and can wedge the host. Detect them read-only (no root).
    if fn.unmount_build_script() is None:
        return OK, _("Set once the {repo} clone is present.").format(repo=fn.REPO_NAME), None
    if fn.stale_mounts_present():
        return WARN, _("Stale build mounts under kiro-build; clean them before building."), ("unmount",)
    return OK, _("No stale build mounts."), None


def check_leftover_build_folder():
    # A leftover kiro-build from a prior run is root-owned (mkarchiso ran as root)
    # and can't be deleted from a file manager. Offer a confirmed removal.
    if fn.repo_dir() is None:
        return OK, _("Set once the {repo} clone is present.").format(repo=fn.REPO_NAME), None
    if fn.build_folder_present():
        return WARN, _("Leftover build folder {path} (owned by root); remove it.").format(
            path=fn.build_folder()), ("removebuild",)
    return OK, _("No leftover build folder."), None


def check_nvidia():
    conf = fn.read_conf()
    choice = conf.get("nvidia_driver", "open")
    lspci = fn.cmd_out(["lspci"]) if fn.have("lspci") else ""
    has_nv = "nvidia" in lspci.lower()
    if has_nv:
        return OK, _("NVIDIA GPU detected; driver set to '{driver}'.").format(driver=choice), None
    return OK, _("No NVIDIA GPU detected; driver set to '{driver}' (unused).").format(driver=choice), None


# Grouped into sections that render as headers on the Pre-flight screen.
# Within a section order is cosmetic — every check runs regardless of order.
CHECKS = [
    ("repo", _("{repo} (git clone)").format(repo=fn.REPO_NAME), check_repo, N_("Build sources")),
    ("uptodate", _("{repo} up to date").format(repo=fn.REPO_NAME), check_repo_uptodate, N_("Build sources")),
    ("polkit", _("polkit auth agent"), check_polkit, N_("Dependencies")),
    ("archiso", _("archiso package"), check_archiso, N_("Dependencies")),
    ("grub", _("grub package"), check_grub, N_("Dependencies")),
    ("chaotic", _("Chaotic-AUR repo"), check_chaotic, N_("Dependencies")),
    ("cachyos", _("CachyOS repo"), check_cachyos, N_("Dependencies")),
    ("kernels", _("Kernel package(s)"), check_kernels, N_("Dependencies")),
    ("root", _("Not running as root"), check_not_root, N_("Host and workspace")),
    ("arch", _("Arch-based host"), check_arch, N_("Host and workspace")),
    ("disk", _("Free disk space"), check_disk, N_("Host and workspace")),
    ("stale_mounts", _("Stale build mounts"), check_stale_mounts, N_("Host and workspace")),
    ("leftover_build", _("Leftover build folder"), check_leftover_build_folder, N_("Host and workspace")),
    ("nvidia", _("NVIDIA driver choice"), check_nvidia, N_("Host and workspace")),
]


def run_all():
    """Return a list of dicts: key, title, status, detail, fix, section."""
    results = []
    for key, title, detect, section in CHECKS:
        status, detail, fix = detect()
        results.append({"key": key, "title": title, "status": status,
                        "detail": detail, "fix": fix, "section": section})
    return results


def clone_cmd(dest=None):
    """git clone argv for the repo fix. None if git absent.

    Clones into the user-chosen repo path (or the default ~/kiro-iso).
    """
    if not shutil.which("git"):
        return None
    dest = dest or str(fn.saved_repo_path() or fn.default_repo_dir())
    return ["git", "clone", fn.REPO_URL, dest]
