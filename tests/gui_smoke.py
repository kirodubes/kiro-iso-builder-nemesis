"""GUI smoke test: open KIB on the fake repo, visit every screen, fail on any Python exception.

Needs a running desktop session (X11 or Wayland). Pass a folder to also save a screenshot
per screen (X11 only, needs xdotool + imagemagick's import). Writes only inside the fake repo.
"""

import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["KIB_LANGUAGE"] = "en"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fixture  # noqa: E402

tmp, build_scripts = fixture.make_repo()
os.environ["KIRO_ISO_DIR"] = str(build_scripts.parent)

import importlib.util  # noqa: E402

import functions  # noqa: E402

# Before the app module loads, so nothing it does at start-up reaches the real ~/.config.
fixture.install(functions, build_scripts)
spec = importlib.util.spec_from_file_location("kib", HERE.parent / "kiro-iso-builder.py")
kib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kib)
from gi.repository import GLib  # noqa: E402

SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
SCREENS = ["preflight", "configure", "packages", "extras", "build", "done"]
errors = []


def hook(exc_type, exc, tb):
    errors.append("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = hook
kib.APP_ID = "org.kiro.isobuilder.smoketest"
orig_present = kib.BuilderWindow.present


def shoot(name):
    if SHOTS and shutil.which("xdotool") and shutil.which("import"):
        wid = subprocess.run(["xdotool", "search", "--name", "KIB-SMOKE"], capture_output=True, text=True).stdout.split()
        if wid:
            subprocess.run(["import", "-window", wid[0], str(SHOTS / f"{name}.png")], check=False)


def check_search_note(screen):
    """Searching for a core package names it under the search box instead of showing nothing."""
    screen.search.set_text("tweak")
    screen._apply_filter("tweak")
    note = screen.search_note
    if not (note.get_visible() and "archlinux-tweak-tool" in note.get_text()):
        errors.append(f"search note missing for an always-shipped package: {note.get_text()!r}")
    screen.search.set_text("")
    screen._apply_filter("")
    if note.get_visible():
        errors.append("search note still shown after clearing the search")
    return False


def check_size_line(label, where):
    """The Packages status and the Build page both carry the ISO size estimate."""
    if "Estimated ISO size: about" not in label.get_text():
        errors.append(f"no ISO size estimate on the {where} page: {label.get_text()!r}")
    return False


def present(self):
    orig_present(self)
    self.set_title("KIB-SMOKE")
    names = [n for n in SCREENS if n in self.screens]
    missing = set(SCREENS) - set(names)
    if missing:
        errors.append(f"screens not built: {sorted(missing)}")

    def step(i=0):
        if i > 0:
            shoot(names[i - 1])
        if i == len(names):
            self.get_application().quit()
            return False
        print(f"  visiting {names[i]}")
        self.navigate(names[i])
        if names[i] == "packages":
            GLib.timeout_add(600, check_search_note, self.screens["packages"])
            GLib.timeout_add(1000, check_size_line, self.screens["packages"].status, "Packages")
        if names[i] == "build":
            GLib.timeout_add(1000, check_size_line, self.screens["build"].size, "Build")
        GLib.timeout_add(1200, step, i + 1)
        return False

    GLib.timeout_add(800, step)


kib.BuilderWindow.present = present
if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)
app = kib.BuilderApp()
GLib.timeout_add_seconds(60, app.quit)
app.run([])
tmp.cleanup()

if errors:
    print("\n".join(errors))
    print(f"GUI smoke test FAILED ({len(errors)} error(s))")
    sys.exit(1)
print("GUI smoke test passed" + (f", screenshots in {SHOTS}" if SHOTS else ""))
