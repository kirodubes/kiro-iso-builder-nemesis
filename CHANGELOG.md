# CHANGELOG

> History of kiro-iso-builder — newest first.

---

## 2026-06-08 — Never move/adopt the HQ source repo (mirrors production)

### What Changed
- **Removed the repo-move behaviour** (`preflight_gui.py`): picking a folder no
  longer `shutil.move`s an existing clone — it just points the app at the target
  and clones fresh. Dropped `_confirm_move`/`_on_move_confirmed`/`_on_moved` + the
  `shutil` import.
- **Dropped the HQ-sibling auto-discovery** (`functions.py find_build_scripts`):
  no longer adopts `APP_DIR.parent/<repo>`; defaults to `~/<repo>`.

### Why
- On **hq** the builder discovered the sibling `KIRO-ISO-CALAMARES/kiro-iso` and
  *moved* it to the chosen/default spot — scattering the source repo into
  `~/Music`, `~/Desktop`, `~/kiro-iso`. The builder must never move, adopt, or
  build inside a Kiro-HQ source folder. Mirrors the same fix in the production
  `kiro-iso-builder`.

### Files Modified
- `preflight_gui.py`, `functions.py`

## 2026-06-08 — Pre-flight clarity: clone wording, grouped checks, one build folder

### What Changed — one build folder, surfaced up front (KISS)

The "where does my stuff go?" decision was split in two and both halves were buried:
the **clone path** sat mid-Pre-flight behind the checks, and the **build output**
(`home` vs `local`) was a dropdown on the Configure screen among nvidia/kernels. That
split confused cyberagency on Discussions #39.

Collapsed it to one decision: the user picks a **single folder** on Pre-flight and the
clone, the `kiro-build` work dir and the `kiro-Out` ISO all live under it. The separate
"Build location" choice is gone. The default case is unchanged — default clone
`~/kiro-iso` → `~/kiro-build` / `~/kiro-Out`, exactly as before; only the unusual
"non-home clone + home output" combo collapses.

- **No build-script change.** The build script's existing `build_location=local` branch
  already puts build/out next to the clone, and `resolve_repo_dir` already appends
  `kiro-iso` to the picked folder (clone-parent == chosen base). So the whole change
  stays inside the builder app; `build-the-iso.sh` / `build.conf.defaults` untouched.
- **`functions.py`** — `build_output_base()` replaced by `build_base_dir()` (always the
  clone's parent, or the chosen/default clone-parent before a clone exists);
  `build_folder()`/`out_folder()` derive from it. New `normalize_build_location()` forces
  `build_location=local` in the live `build.conf`, called from `refresh_paths()` **and**
  at app startup (`kiro-iso-builder.py`) — so a user who jumps Pre-flight → Build,
  skipping Configure, still builds into the chosen folder rather than silently into `~`.
- **`preflight_gui.py`** — the location picker is now a prominent "Where should Kiro
  build?" card at the top of the screen, above the checks, with a one-line explainer.
- **`configure_gui.py`** — removed the Build-location dropdown, its hint, the `LOCATION`
  constant, `_update_location_hint()`, and the `build_location` default.
- **`host_checks.py`** — `check_disk()` now measures free space on the filesystem that
  holds the chosen build folder (e.g. `/DATA`), not always `$HOME`.
- **`build_gui.py`** — the build summary reports the resolved **Build folder** instead of
  the now-always-`local` knob.

### What Changed — remove build folder by default

Flipped the **"Remove build folder after build"** default from `no` to `yes`. The build
already wipes and recreates the work dir at the start of every run
(`prepare_build_tree` → `remove_buildfolder yes`), so keeping it bought nothing but a
lingering **root-owned** `kiro-build` that tripped the leftover-folder pre-flight warning
and the keep/delete prompts (the loop cyberagency hit on #39). Removing it after each
build leaves a clean state with no build-speed cost. The toggle stays in Advanced for
anyone who wants to inspect the tree. Changed in the GUI `DEFAULTS` here and in the
canonical `build.conf.defaults` seed in `kiro-iso` / `kiro-iso-next`.

### What Changed — clone wording

Reworded the user-facing strings that called the `kiro-iso` build-scripts checkout a
"repo". On the Pre-flight screen it sat right next to the **Chaotic-AUR repo** and
**CachyOS repo** rows (real pacman repositories), so users read it as another pacman
repo when it's actually a git clone of the build profile. Raised by cyberagency on
Discussions #39.

- Pre-flight check row title `kiro-iso repo` → **`kiro-iso (git clone)`**, now visibly
  distinct from the pacman-repo rows.
- Detail / status / error strings across the Pre-flight, Configure, Packages and Build
  screens now say "clone" instead of "repo" when referring to the kiro-iso checkout.

### Technical Details

All labels interpolate `fn.REPO_NAME` (`kiro-iso`, or `kiro-iso-next` in dev mode), so the
wording stays correct in both modes. Internal docstrings/comments that say "kiro-iso repo"
were left as-is — it *is* a git repository; the fix is purely about the user-facing
conflation with pacman repos.

### What Changed — grouped Pre-flight checks

The Pre-flight screen was one flat list of ~14 rows mixing build sources, dependencies
and host/workspace housekeeping. Grouped them under section headers — **Build sources**
(kiro-iso clone, up-to-date), **Dependencies** (polkit, archiso, grub, Chaotic-AUR,
CachyOS, kernels), **Host & workspace** (not-root, Arch-based, disk space, stale build
mounts, leftover build folder, NVIDIA) — so the disk/mounts/leftover housekeeping reads
as its own group instead of interleaved with dependency checks. Raised by cyberagency on
Discussions #39 (point 2). Checks themselves are unchanged; only display order/headers.

- Added a `section` field to each entry in `CHECKS`; `run_all()` carries it through.
- `PreflightScreen._populate` inserts a non-selectable header row when the section
  changes; new `_build_header()` renders it with a `.section-header` CSS class.

### What Changed — Build screen states output paths

The Build screen never said where output lands; the resolved `kiro-build` / `kiro-Out`
paths were only hinted on Configure. Added an explicit, selectable two-line label under the
Build subtitle — **Work dir:** and **ISO output:** — refreshed on every show so it tracks
the `build_location` chosen on Configure. Raised by cyberagency on Discussions #39 (point 3,
paths). The clone path stays on Pre-flight where it's picked.

- New `BuildScreen._show_paths()` reads `fn.build_folder()` / `fn.out_folder()` and renders
  the two paths (falls back to a "set the clone path on Pre-flight" note when unresolved);
  called from `on_show()`.

### Files Modified

- host_checks.py, configure_gui.py, build_gui.py, packages_gui.py, kiro-iso-builder.py,
  functions.py, preflight_gui.py, style.css

## 2026-06-07 — Initial GTK4 app

First working version of the Kiro ISO Builder: a GTK4 (ATT-style, plain Gtk4 — no
libadwaita) front-end that drives `kiro-iso/build-scripts/build-the-iso.sh` rather than
reimplementing it.

### Window can be shrunk again

- **Fixed: the window couldn't be dragged narrower — the real blocker was the Done
  screen's button bar.** Eight buttons in a single non-wrapping `Gtk.Box` summed to a
  ~1086px minimum width; because the `Gtk.Stack` sizes to its widest page, that pinned
  the whole window (`set_default_size` only sets the *initial* size, never the floor).
  Replaced the `Gtk.Box` with a `Gtk.FlowBox` (`min_children_per_line=1`) so the buttons
  reflow onto multiple rows as the window narrows. Measured via a throwaway
  `widget.measure()` harness: the Done floor dropped **1086 → 189px**; the window minimum
  is now governed by the Configure form (~575px) at roughly **~760px** total.
- **Long labels now wrap.** A GTK4 `Gtk.Label` without `wrap=True` requests its full
  single-line text as its *minimum* width. Added `wrap=True` (with `max_width_chars=70`
  on the static subtitles) to the long labels that lacked it — Pre-flight subtitle,
  Configure subtitle / `_labelled` field labels / kernel & status labels, Packages status —
  so each screen's own floor drops and the text reflows instead of forcing a single line.
- **Packages subtitle split into two lines** at the `ISO.` boundary for readability
  (one wrapping label became two stacked labels in a vertical box).
- Set the initial window width to 850px — wide enough that the Packages screen's two-line
  subtitle reads as one full sentence per line at startup; the window can still be dragged
  down to its ~760px minimum.

### What Changed

- **Four-screen wizard** (StackSidebar + Stack, ATT look): Pre-flight → Configure → Build → Done.
- **Pre-flight panel** — 11 host checks with one-click fixes that reuse `host-prep.sh`
  (`ensure_package`, `setup_chaotic`, `setup_cachyos`) so a GUI-fixed host equals a
  CLI-prepared one. Checks: repo present, not-root, Arch-based, polkit agent, `archiso`,
  `grub`, Chaotic, CachyOS, disk space, kernel tokens, NVIDIA choice.
- **Configure screen** — reads/writes the shared `build.conf` (`nvidia_driver`, `kernel`,
  `bump_version`, plus advanced knobs). No sed-editing of the build script. The kernel is
  chosen via two dropdowns — **First kernel** (boots the live ISO) and an optional **Second
  kernel** (`none` collapses to a single-kernel build). A **Detect available kernels** button
  loads the real list from the host via the shared `list-kernels.sh` (same filter the build
  uses — only kernels that have a matching `-headers`, no false positives), falling back to a
  curated list. Headers are not a separate choice: the build installs `<kernel>-headers`
  automatically for every selected kernel (required for the DKMS drivers).
- **Packages screen** (new wizard step between Configure and Build) — lists the ISO's
  **TIER 3** (user-changeable / optional) packages from `packages.x86_64`, grouped by category,
  reusing ATT's "streamline" pattern: category-level select-all (tri-state), per-package
  checkboxes, and a search filter. Unticked packages are written to `package-selection.conf`,
  which the build comments out — TIER 1/2 are never shown, so nothing here can break the build.
  **Save profile / Import profile** (also from streamline) export the current exclusion set to a
  file and load it back, so the same package set can be reused for a later rebuild. Profiles live
  in `~/.config/kiro-iso-builder/profiles/`, created at startup (ATT-style `ensure_app_dirs()`).
- **Persistent Quit button** in the window's bottom-right footer, on every screen.
- **Reset to defaults** button on the Configure screen — restores every knob to its shipped
  default (review, then Save to persist; shares one code path with the normal load).
- **Build progress** maps `Phase N` log lines to the bar; the total is auto-derived from the
  build script (12 phases today) so it never shows "Phase 11 / 9" again.
- **Build screen** — runs the build under a **PTY** so its internal `sudo` gets a tty and
  prompts once (answered via a GTK password dialog), then streams a live log and maps
  `Phase N` lines to a progress bar. Stoppable. An **input box** lets the user answer any
  prompt the build raises (e.g. mkarchiso/pacman's `[Y/n]`) by writing to the PTY master, and
  log output is **ANSI-stripped** so terminal colour codes don't clutter the view. The PTY is
  given a real window size (`TIOCSWINSZ`) and in-place progress bars (carriage-return redraws)
  collapse to their final state, so a big package no longer floods the log with hundreds of
  identical lines.
- **Build robustness fixes** — the PTY runner now exports `TERM=xterm-256color` so the build's
  `tput` calls don't abort with "No value for $TERM" when the app is launched from a desktop
  menu (where `TERM` is unset). The kernel pre-flight check now distinguishes "kiro-iso repo not
  yet present", `kernel=ask` (chosen at build time), and "no kernel set in build.conf" (a real
  WARN) instead of collapsing them all to a single misleading message.
- **Done screen** — open `~/kiro-Out`, show checksums, and test-boot the ISO in **QEMU or
  VirtualBox**. Both create a throwaway **UEFI** VM (OVMF / `--firmware efi`, not legacy BIOS)
  with a **50 GB disk** so the Calamares installer has a target. Boot order is **disk-first, CD
  fallback** (QEMU `-boot order=cd`, VirtualBox `--boot1 disk --boot2 dvd`) so an empty disk boots
  the ISO installer but the post-install reboot boots the installed system instead of looping the
  ISO. Both **overwrite** a single reusable test VM/disk (`kiro-iso-builder-test`) — fresh every
  run, no clutter. When a
  hypervisor is missing the button becomes **Install QEMU** / **Install VirtualBox** (pkexec) and
  flips back to Test once installed; the VirtualBox install (adapted from Erik's script) pulls
  `virtualbox` + `virtualbox-host-dkms`, the `-headers` for every installed kernel, loads and
  persists the modules, and adds the user to `vboxusers`.
- **Stale-mount fail-safe** — stopping a build halfway used to leave mkarchiso's bind-mounts
  (`dev/proc/sys/run/tmp/pts/shm/efivars`) live under the work dir, which blocks the next build,
  jams the file manager, and can freeze the host (it did — hard reboot). Now: (1) the **Build
  screen** runs a cleanup after any abnormal exit (Stop **or** failure) — it first checks for
  leftover mounts as the user (no prompt) and only `pkexec`-unmounts if some remain, re-arming
  **Start** only once clean; (2) a new **Pre-flight check** ("Stale build mounts") surfaces
  leftovers from an earlier crash with a one-click Fix; (3) `build-the-iso.sh` now unmounts on
  any early exit via an `EXIT` trap (the net — also covers a `set -e` build failure), with
  `INT`/`TERM` traps on top so a `Ctrl-C`/`kill` (CLI or the GUI's Stop) cleans up immediately.
  Backed by
  a new self-contained `kiro-iso/build-scripts/unmount-build.sh` (`check` = read-only detect,
  `clean` = unmount) that derives the work dir the same way the build does — one source of truth
  shared by the Stop handler, the pre-flight check, and the CLI.
- **build.conf is now a gitignored working copy** — the kiro-iso repo ships a tracked
  `build.conf.defaults` and gitignores the live `build.conf`. `ensure_build_conf()` seeds it from
  the defaults at app startup and after a clone (`refresh_paths`), so the GUI always has a real
  config to read/write while the user's local build tweaks can never be committed/pushed back to
  the repo.
- **Shareable build profiles** — a build can now be saved as a named, shareable
  `*.kiroprofile` that captures the **ISO-identity settings** (`desktop`, `kernel`,
  `nvidia_driver`) **plus** the removed-package set, so someone else can reproduce the
  same ISO recipe. **Save build profile…** lives on the Done screen (after a build),
  **Import build profile…** on the Configure screen (it populates the settings controls
  and writes `package-selection.conf`, so the Packages screen reflects it — then Save &
  Continue). Host/workflow knobs (`build_location`, `clean_pacman_cache`,
  `remove_build_folder`, `bump_version`) are deliberately **not** captured — they're
  about the builder's machine, not the ISO. The file records the recipe, not a
  byte-identical image (Kiro is rolling). The pre-existing package-only Save/Import
  buttons were relabelled **Save/Import package list…** to distinguish them.
- **Choose where the kiro-iso repo lives** — the Pre-flight screen now shows a
  **kiro-iso location** row with a **Browse…** button (instead of the old hardcoded
  `~/kiro-iso`). Browsing to a folder that already holds a clone points the app there;
  browsing to a new folder while a clone exists elsewhere offers to **move** it there
  (confirmation dialog showing from → to, run off the UI thread); browsing to a new folder
  with no clone anywhere remembers the spot so the clone fix populates it. The choice is
  persisted to `~/.config/kiro-iso-builder/repo_path` and tried first by repo discovery, so
  it survives a relaunch. Distinct from the `build_location` knob (which only moves
  `kiro-build`/`kiro-Out`).
- **Keep the kiro-iso clone up to date** — a new Pre-flight check **"kiro-iso up to date"**
  fetches and reports how many commits the clone is behind origin (offline → graceful WARN, no
  button), so users don't build from stale scripts/package lists. Its **Update** fix
  fast-forwards a clean tree (`git pull --ff-only`); when the tree is dirty from a previous build
  (the version bump seds tracked files), it offers a confirmed reset to the latest that **keeps
  the user's package selection** (backed up around `git reset --hard`; `build.conf` is gitignored
  so it survives on its own). Update is **excluded from Fix-all** — it's a deliberate action, not
  an unattended host-prep step.
- **Build-location hint** — the Configure screen's Build location dropdown now shows, live, where
  `kiro-build`/`kiro-Out` will actually land (`local → <repo-parent> (next to the repo)`,
  `home → <home> (your home folder)`), so it's clear the build folders sit *beside* the repo, not
  inside it.
- **Per-build log folder** — every build now writes `~/.config/kiro-iso-builder/logs/<timestamp>/`
  capturing everything needed to diagnose it afterwards: `build.log` (full output), `build.conf`
  (the settings used — nvidia_driver, kernel, …), `package-selection.conf` (exclusions), the
  **final post-sed `packages.x86_64`** from the build tree (shows exactly which NVIDIA variant
  shipped), and `summary.txt` (timestamp, duration, exit code, ISO name + size + checksums, host +
  archiso version, SUCCESS/FAILED). A Done-screen **"Open build log"** button opens the latest one.
  Prompted by a 390xx install failure where it wasn't clear what the ISO was actually built with.

- **Confirm-gated removal of the root-owned build folder** — after a stopped/failed build the
  app already unmounts the stale mounts (automatic, non-destructive). It now also offers to
  **remove the leftover `kiro-build` work dir** — but **never automatically**: mkarchiso runs its
  chroot as root, so the folder is root-owned and can't be deleted from a file manager, yet
  deleting from the user's home demands an explicit OK. So a `Gtk.AlertDialog` (message + **exact
  path** + size) asks first, **defaults to "Keep"**, and only an explicit **Remove** runs the
  `pkexec` cleanup (unmount via `unmount-build.sh clean`, then `rm -rf` the resolved
  `build_folder()` — never a broader/user-typed path). A pre-flight **"Leftover build folder"**
  check surfaces one from a previous session with the same confirmed fix; it's excluded from
  Fix-all (deliberate, confirm-only — like Update).
- **VirtualBox post-install guidance** — after installing VirtualBox from the Done screen, the
  note now says to **reboot** (a log-out isn't enough — the host kernel modules must load) and to
  **enable virtualization in the motherboard/BIOS-UEFI** (Intel VT-x / AMD-V / SVM), without which
  VirtualBox can't run VMs.

- **"Open folder" opens a real file manager** — the Done screen's *Open output folder* / *Open
  build log* (and any `open_path`) went through `xdg-open`, which obeys the `inode/directory` MIME
  default; on a host where that's bound to a disk-usage analyzer (e.g. baobab) it opened the wrong
  app. `open_path` now uses the freedesktop `org.freedesktop.FileManager1` D-Bus interface
  (`ShowFolders`) so it always lands in a file manager (Thunar/Nemo/Nautilus/…), falling back to a
  known FM binary, then `xdg-open`.

### Technical Details

- **Privilege model:** app runs as the normal user (never root). Fixes elevate via
  `pkexec bash host-prep-run.sh <fn>`; the build runs as the user and authenticates its own
  `sudo` inside the PTY. Wayland-safe — no root-owned GUI ever touches the display.
- **Portable:** no hardcoded paths/users; repo discovery via `KIRO_ISO_DIR`, sibling clone,
  `~/kiro-iso`, or `/usr/share`; Arch detection via `os-release` `ID`/`ID_LIKE`.
- **Pairs with kiro-iso changes:** the build config block was extracted into
  `build-scripts/build.conf`, and a thin `host-prep-run.sh` dispatcher was added so the GUI
  can invoke a single host-prep function in isolation.

### Files

- `kiro-iso-builder.py` (entry/Application/Window), `functions.py` (runners + config bridge),
  `host_checks.py`, `preflight_gui.py`, `configure_gui.py`, `build_gui.py`, `done_gui.py`,
  `style.css`, `kiro-iso-builder.desktop`, `README.md`.
- Stale-mount fail-safe: `functions.py` (`unmount_build_script`, `stale_mounts_present`,
  `run_cleanup_mounts`), `build_gui.py` (cleanup-on-abnormal-exit), `host_checks.py`
  (`check_stale_mounts`), `preflight_gui.py` (`unmount` fix). Pairs with new
  `kiro-iso/build-scripts/unmount-build.sh` + `INT`/`TERM` trap in `build-the-iso.sh`.
- build.conf seeding: `functions.py` (`build_conf_defaults_path`, `ensure_build_conf`,
  seed in `refresh_paths`), `kiro-iso-builder.py` (seed at startup). Pairs with
  `kiro-iso`'s gitignored `build.conf` + tracked `build.conf.defaults`.
- Build profiles: `functions.py` (`write_build_profile`/`read_build_profile`,
  `PROFILE_SETTINGS_KEYS`), `done_gui.py` (Save), `configure_gui.py` (Import),
  `packages_gui.py` (relabel).
- Repo-location chooser: `functions.py` (`default_repo_dir`, `saved_repo_path`,
  `save_repo_path`, `resolve_repo_dir`, discovery order), `host_checks.py`
  (`clone_cmd(dest)`), `preflight_gui.py` (Browse row + clone-fix persistence + move).
- Repo freshness/Update: `functions.py` (`repo_dir`, `git_fetch`, `commits_behind`,
  `repo_is_dirty`, `git_pull_ff_argv`, `git_force_update_argv`), `host_checks.py`
  (`check_repo_uptodate`), `preflight_gui.py` (Update fix + Fix-all exclusion).
