# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Project

`kiro-iso-builder` — a GTK4 Python front-end that builds the Kiro ISO by driving
`kiro-iso/build-scripts/build-the-iso.sh`. It is a **front-end, not a reimplementation**:
the build pipeline and all host-prep logic stay in the `kiro-iso` repo; this app shells out
to them. Design spec: `Kiro-HQ/ISO-BUILDER-GUI-SPEC.md`. Ships via `nemesis_repo`.

## Conventions (follow ATT)

- Plain **GTK4** (`Gtk 4.0`), no libadwaita. ATT visual style.
- Runs as the **normal user**, never root. Elevate on demand: pre-flight fixes via `pkexec`,
  the build via its own `sudo` answered inside a PTY.
- GTK4 callbacks name unused widget params `_widget`/`_w`.
- Never `subprocess.call()` from a GUI callback — always `Popen`/PTY in a daemon thread,
  streaming output via `GLib.idle_add`.
- Must work on **all Arch-based systems** and on **Wayland** — no hardcoded paths/users,
  no X11-only calls.
- Python: max line 120; run `ruff check` before considering work done.

## Architecture

- `kiro-iso-builder.py` — `Gtk.Application` + `BuilderWindow` (HeaderBar + StackSidebar +
  Stack of four screens); loads `style.css`; `navigate(name)` drives the wizard.
- `functions.py` (`fn`) — repo discovery (`KIRO_ISO_DIR` / sibling / `~/kiro-iso` /
  `/usr/share`), `build.conf` read/write bridge, two runners (`run_pipe` for pkexec fixes,
  `run_in_pty` for the build), and a thread-safe `ask_password` dialog.
- `host_checks.py` — the 11 pre-flight checks and their fix descriptors.
- `*_gui.py` — one screen class each (`PreflightScreen`, `ConfigureScreen`, `PackagesScreen`,
  `ExtrasScreen`, `BuildScreen`, `DoneScreen`), each exposing `.widget` and `on_show()`.
  `PackagesScreen` removes TIER-3 apps (ticked = ships); `ExtrasScreen` adds opt-in apps not on
  the base image (ticked = added), auto-discovered from the `EXTRA-APP` blocks in `packages.x86_64`.

## The kiro-iso contract

This app depends on three things in `kiro-iso/build-scripts`:
- `build-the-iso.sh` — the build entry point (runs non-root).
  Its `check_archiso_version` holds the archiso minimum (`local required="…"`), which the
  pre-flight `check_archiso` reads — keep that line's shape.
- `build.conf` — the shared user-config file the Configure screen reads/writes.
- `host-prep-run.sh` — a dispatcher that runs one host-prep function (`ensure_package`,
  `setup_chaotic`, `setup_cachyos`) in isolation under `pkexec`.

Changing those scripts can affect this app — keep them in sync.

## Translations

- `kib_i18n.py` uses the `kiro-iso-builder` gettext domain (also in the nemesis twin, which installs unprefixed).
  Catalogs: `po/<lang>.po` for `nl de es fr pt_BR ru hu it tr pl ja zh_CN ro`. Run from git it reads the repo's
  `locale/`; installed it reads `/usr/share/locale` (the PKGBUILD copies `locale/` there).
- Follows the system language until the user picks one in the header dropdown (before ♥ Support); the choice is
  saved in `~/.config/kiro-iso-builder/prefs.json` (shared by both builders) and applies after **Restart now**,
  which refuses while a build runs. `KIB_LANGUAGE` pins a catalog; the tests pin `en`.
- Whole sentences in `_()`, counts in `ngettext()`, named placeholders. Never translate: `build.conf` keys/values,
  package, kernel and driver names, check ids, anything passed to `host-prep-run.sh`/`build-the-iso.sh`,
  `SUDO_SENTINEL` and any string matched against command output, log files, the build-summary HTML.
- After changing English UI text: `bash po/make-mo.sh`, translate the new entries in all 13 catalogs (0 fuzzy,
  0 untranslated), then commit `po/` and `locale/` together. Edit `po/kiro-iso-builder.desktop.in`, never the
  generated `kiro-iso-builder.desktop`.

## Tests

`bash tests/run-tests.sh` runs everything; it is not shipped (the PKGBUILD installs only the top-level files).
- `tests/test_functions.py` — `unittest` (no pytest dependency) for the package logic: tiers, edition optional
  sections, kept reasons, the resolver, the summary data and HTML. Runs on `tests/fixture.py`, a fake kiro-iso repo
  and a fake sync db, so it never calls pacman.
- `tests/test_build_script.sh` — contract test for the `build-the-iso.sh` functions KIB relies on
  (`apply_editions`, `apply_optional_edition_excludes`, `check_group_drift`), lifted from the real script in
  `$KIRO_ISO_DIR` (default `~/KIRO-ISO-CALAMARES/kiro-iso-next`) with `pacman` stubbed.
- `tests/gui_smoke.py [shot-dir]` — opens every screen on the fake repo and fails on any Python exception;
  needs a desktop session, saves screenshots on X11.

Add a test when you change the package logic or the kiro-iso contract.

