"""Pre-flight host checks for kiro-iso-builder.

Each check detects a condition and, where possible, names a one-click fix that
maps to an existing host-prep.sh function — so a host fixed in the GUI is
identical to one prepared by the CLI build. Works on any Arch-based host.
"""

import os
import shutil

import functions as fn

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
        return OK, f"Found at {fn.BUILD_SCRIPTS}", None
    return FAIL, f"{fn.REPO_NAME} clone not found — everything is built from it", ("clone",)


def check_repo_uptodate():
    # Tell the user when their kiro-iso clone is behind origin so they don't
    # build from stale scripts/package lists. Fetches; offline degrades to a WARN.
    repo = fn.repo_dir()
    if repo is None:
        return OK, f"set once the {fn.REPO_NAME} clone is present", None
    if not fn.have("git"):
        return WARN, "git unavailable — can't check for updates", None
    if not fn.git_fetch(repo):
        return WARN, "couldn't check for updates (offline?)", None
    n = fn.commits_behind(repo)
    if n == 0:
        return OK, "up to date with origin", None
    return WARN, f"{n} update(s) available — Update for the latest build scripts/packages", ("update",)


def check_not_root():
    if os.geteuid() != 0:
        return OK, "Running as a normal user", None
    return FAIL, "Do NOT run the builder as root — relaunch as your user", None


def check_arch():
    osr = _os_release()
    idlike = f"{osr.get('ID', '')} {osr.get('ID_LIKE', '')}".lower()
    if "arch" in idlike:
        return OK, f"{osr.get('PRETTY_NAME', 'Arch-based')}", None
    return FAIL, f"{osr.get('PRETTY_NAME', 'Unknown')} is not Arch-based", None


def check_polkit():
    # pkexec needs an authentication agent running in the session.
    if not fn.have("pkexec"):
        return FAIL, "pkexec not installed — fixes cannot elevate", None
    agents = ("polkit-gnome-au", "polkit-kde-auth", "lxpolkit",
              "polkit-mate-aut", "xfce-polkit", "polkitd")
    running = fn.cmd_out(["pgrep", "-l", "-f", "polkit"])
    if any(a in running for a in agents) or running:
        return OK, "polkit agent present", None
    return WARN, "No polkit agent detected — fix prompts may not appear", None


def check_archiso():
    return (OK, "archiso installed", None) if _pkg("archiso") else \
        (FAIL, "archiso missing", ("hostprep", ["ensure_package", "archiso"]))


def check_grub():
    return (OK, "grub installed", None) if _pkg("grub") else \
        (FAIL, "grub missing", ("hostprep", ["ensure_package", "grub"]))


def check_chaotic():
    have = _pkg("chaotic-keyring") and _pkg("chaotic-mirrorlist")
    return (OK, "chaotic keyring + mirrorlist present", None) if have else \
        (FAIL, "chaotic keyring/mirrorlist missing", ("hostprep", ["setup_chaotic"]))


def check_cachyos():
    have = _pkg("cachyos-keyring") and _pkg("cachyos-mirrorlist")
    return (OK, "cachyos keyring + mirrorlist present", None) if have else \
        (FAIL, "cachyos keyring/mirrorlist missing (linux-cachyos lives there)",
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
        return OK, f"{free:.0f} GB free in {base}", None
    return WARN, f"only {free:.0f} GB free in {base} (~15 GB recommended)", None


def check_kernels():
    if fn.build_conf_path() is None:
        return OK, f"set once the {fn.REPO_NAME} clone is present", None
    raw = fn.read_conf().get("kernel", "").strip()
    if raw == "ask":
        return OK, "kernel picker set to 'ask' (chosen at build time)", None
    tokens = [t for t in raw.split() if t and t != "ask"]
    if not tokens:
        return WARN, "no kernel set in build.conf", None
    if not fn.have("pacman"):
        return WARN, "pacman unavailable", None
    available = set(fn.cmd_out(["pacman", "-Slq"]).split())
    missing = [t for t in tokens if t not in available]
    if missing:
        return WARN, f"kernel(s) not in synced repos: {', '.join(missing)}", None
    return OK, f"kernel(s) resolve: {', '.join(tokens)}", None


def check_stale_mounts():
    # Leftover mkarchiso bind-mounts from an earlier stopped/crashed build break
    # the next build and can wedge the host. Detect them read-only (no root).
    if fn.unmount_build_script() is None:
        return OK, f"set once the {fn.REPO_NAME} clone is present", None
    if fn.stale_mounts_present():
        return WARN, "stale build mounts under kiro-build — clean before building", ("unmount",)
    return OK, "no stale build mounts", None


def check_leftover_build_folder():
    # A leftover kiro-build from a prior run is root-owned (mkarchiso ran as root)
    # and can't be deleted from a file manager. Offer a confirmed removal.
    if fn.repo_dir() is None:
        return OK, f"set once the {fn.REPO_NAME} clone is present", None
    if fn.build_folder_present():
        return WARN, f"leftover build folder {fn.build_folder()} (root-owned) — remove it", ("removebuild",)
    return OK, "no leftover build folder", None


def check_nvidia():
    conf = fn.read_conf()
    choice = conf.get("nvidia_driver", "open")
    lspci = fn.cmd_out(["lspci"]) if fn.have("lspci") else ""
    has_nv = "nvidia" in lspci.lower()
    if has_nv:
        return OK, f"NVIDIA GPU detected; driver set to '{choice}'", None
    return OK, f"no NVIDIA GPU detected; driver set to '{choice}' (unused)", None


# Grouped into sections that render as headers on the Pre-flight screen.
# Within a section order is cosmetic — every check runs regardless of order.
CHECKS = [
    ("repo", f"{fn.REPO_NAME} (git clone)", check_repo, "Build sources"),
    ("uptodate", f"{fn.REPO_NAME} up to date", check_repo_uptodate, "Build sources"),
    ("polkit", "polkit auth agent", check_polkit, "Dependencies"),
    ("archiso", "archiso package", check_archiso, "Dependencies"),
    ("grub", "grub package", check_grub, "Dependencies"),
    ("chaotic", "Chaotic-AUR repo", check_chaotic, "Dependencies"),
    ("cachyos", "CachyOS repo", check_cachyos, "Dependencies"),
    ("kernels", "Kernel package(s)", check_kernels, "Dependencies"),
    ("root", "Not running as root", check_not_root, "Host & workspace"),
    ("arch", "Arch-based host", check_arch, "Host & workspace"),
    ("disk", "Free disk space", check_disk, "Host & workspace"),
    ("stale_mounts", "Stale build mounts", check_stale_mounts, "Host & workspace"),
    ("leftover_build", "Leftover build folder", check_leftover_build_folder, "Host & workspace"),
    ("nvidia", "NVIDIA driver choice", check_nvidia, "Host & workspace"),
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
