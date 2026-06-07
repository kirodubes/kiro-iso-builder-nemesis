"""Shared helpers for kiro-iso-builder: repo discovery, runners, config bridge.

Imported everywhere as ``fn``. Runs as the normal user; privileged work is
elevated on demand with ``pkexec`` (discrete fixes) or by answering the build's
own ``sudo`` prompt inside a PTY (the full build). Nothing here assumes a
specific user, home path, or distro — it works on any Arch-based system.
"""

import fcntl
import os
import pty
import re
import shlex
import shutil
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

# ── Paths / repo discovery ──────────────────────────────────────────
# Hidden dev mode: `--dev` on the launch command targets kiro-iso-next and an
# isolated config dir, so test builds never touch the production repo or state.
DEV = "--dev" in sys.argv
REPO_NAME = "kiro-iso-next" if DEV else "kiro-iso"
REPO_URL = f"https://github.com/kirodubes/{REPO_NAME}"

APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / ".config" / ("kiro-iso-builder-dev" if DEV else "kiro-iso-builder")


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
    candidates += [
        APP_DIR.parent / REPO_NAME / "build-scripts",   # sibling clone (dev layout)
        Path.home() / REPO_NAME / "build-scripts",
    ]
    if not DEV:
        candidates.append(Path("/usr/share/kiro-iso/build-scripts"))
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


def packages_file():
    """archiso/packages.x86_64 in the kiro-iso repo, or None."""
    return BUILD_SCRIPTS.parent / "archiso" / "packages.x86_64" if BUILD_SCRIPTS else None


def package_selection_path():
    return BUILD_SCRIPTS / "package-selection.conf" if BUILD_SCRIPTS else None


# ── Repo freshness / update (git) ───────────────────────────────────
def repo_dir():
    """The kiro-iso repo root (parent of build-scripts), or None."""
    return BUILD_SCRIPTS.parent if BUILD_SCRIPTS else None


def build_output_base(location):
    """Where kiro-build/kiro-Out land for a given build_location, mirroring
    build-the-iso.sh: 'local' → beside the repo (its parent), else $HOME."""
    if location == "local":
        repo = repo_dir()
        return repo.parent if repo else None
    return Path.home()


def build_folder():
    """The mkarchiso work dir for the current build_location, or None."""
    base = build_output_base(read_conf().get("build_location", "home"))
    return base / "kiro-build" if base else None


def out_folder():
    """The ISO output dir for the current build_location, or None."""
    base = build_output_base(read_conf().get("build_location", "home"))
    return base / "kiro-Out" if base else None


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


def git_force_update_argv(repo, selection_path):
    """Reset the clone to its upstream, preserving the user's package selection.

    Used when the tree is dirty (build byproducts block a fast-forward). build.conf
    is gitignored so it survives reset untouched; package-selection.conf is tracked,
    so it's backed up and restored around the reset.
    """
    repo_q = shlex.quote(str(repo))
    sel_q = shlex.quote(str(selection_path))
    script = (
        f'sel={sel_q}; bak=""; '
        f'[ -f "$sel" ] && bak="$(mktemp)" && cp "$sel" "$bak"; '
        f'git -C {repo_q} fetch origin && git -C {repo_q} reset --hard @{{u}} || exit 1; '
        f'[ -n "$bak" ] && cp "$bak" "$sel" && rm -f "$bak"; '
        f'echo "Updated to the latest; your package selection was preserved."'
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
    """Rewrite ``key``'s value in build.conf in place, keeping its comment."""
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
    if changed:
        path.write_text("\n".join(out) + "\n")
    return changed


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


_EXCLUDE_HEADER = (
    "# TIER 3 packages to EXCLUDE from the ISO (kiro-iso-builder package profile).\n"
    "#\n"
    "# build-the-iso.sh's apply_package_selection() comments each one out in the\n"
    "# build-tree copy of packages.x86_64. One package name per line; lines starting\n"
    "# with # are ignored. An empty list ships the full TIER 3 set. This same format\n"
    "# is build-scripts/package-selection.conf and an importable saved profile.\n"
    "#\n"
    "# Only TIER 3 (USER-CHANGEABLE / OPTIONAL) packages belong here — TIER 1\n"
    "# (frozen) and TIER 2 (core) must never be excluded.\n\n"
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
        GLib.idle_add(on_line, f"[error] {REPO_NAME} repo not found")
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
        # The PTY makes the build's `[[ -t 1 ]]` true, so it runs tput. When the
        # app is launched from a desktop menu, TERM is unset and tput aborts the
        # build ("No value for $TERM"). Give it a sane terminal type.
        env.setdefault("TERM", "xterm-256color")
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


def max_build_phase(default=12):
    """Highest 'Phase N' number in build-the-iso.sh, so progress never overflows."""
    script = build_script()
    if not script or not script.is_file():
        return default
    nums = [int(n) for n in re.findall(r"Phase (\d+)", script.read_text())]
    return max(nums) if nums else default


_ANSI_RE = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB0-2]")


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
