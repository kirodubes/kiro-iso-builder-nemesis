# Kiro ISO Builder
 
A GTK4 front-end that makes building the Kiro ISO clickable — and **corrects bad
host settings before the build** instead of letting `mkarchiso` fail halfway. It is a
front-end, not a reimplementation: the build pipeline stays in
[`kiro-iso/build-scripts`](https://github.com/kirodubes/kiro-iso); this app drives it.

Works on any Arch-based host (Arch, CachyOS, EndeavourOS, Garuda, …).

## How it runs

- Launches as your **normal user** — never as root.
- Pre-flight **fixes** are elevated on demand with `pkexec` (one polkit prompt each),
  reusing `host-prep.sh`'s idempotent functions, so a GUI-fixed host is identical to a
  CLI-prepared one.
- The **build** runs `build-the-iso.sh` as you (it refuses root and `sudo`s internally).
  It runs inside a PTY, so its one `sudo` password prompt appears as a dialog and is then
  cached for the whole run.

## Screens

1. **Pre-flight** — checks the host (repo present, Arch-based, polkit agent, `archiso`/`grub`,
   Chaotic + CachyOS repos, disk space, kernel tokens, NVIDIA choice) and offers one-click fixes.
2. **Configure** — `nvidia_driver`, `kernel` (two dropdowns + a **Detect** button that lists
   the host's real kernels — only those with matching `-headers`), `bump_version` and a few
   advanced knobs, written straight to `build.conf`. Headers install automatically per kernel.
3. **Build** — live log + phase progress (auto-derived from the build script), stoppable.
4. **Done** — open `~/kiro-Out`, show checksums, boot the ISO in QEMU.

## Run from source

```bash
python3 kiro-iso-builder.py
```

The app finds the `kiro-iso` repo automatically when it sits beside this one, at
`~/kiro-iso`, or `/usr/share/kiro-iso`. Override with `KIRO_ISO_DIR=/path/to/kiro-iso`.
If it isn't found, the Pre-flight screen can clone it.

## Requirements

`python-gobject`, `gtk4`, `polkit` (+ a polkit auth agent for your session), `pacman`,
and for the optional VM test, `qemu`.
