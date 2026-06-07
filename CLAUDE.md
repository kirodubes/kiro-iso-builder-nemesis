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
- `*_gui.py` — one screen class each (`PreflightScreen`, `ConfigureScreen`, `BuildScreen`,
  `DoneScreen`), each exposing `.widget` and `on_show()`.

## The kiro-iso contract

This app depends on three things in `kiro-iso/build-scripts`:
- `build-the-iso.sh` — the build entry point (runs non-root).
- `build.conf` — the shared user-config file the Configure screen reads/writes.
- `host-prep-run.sh` — a dispatcher that runs one host-prep function (`ensure_package`,
  `setup_chaotic`, `setup_cachyos`) in isolation under `pkexec`.

Changing those scripts can affect this app — keep them in sync.
