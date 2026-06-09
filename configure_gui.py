"""Configure screen — edits the knobs in build.conf (shared with the CLI)."""

import threading

import gi

import functions as fn

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

NVIDIA = ["open", "580xx", "390xx", "none"]
# Curated fallback list, used until the user clicks Detect (or if no repo DB).
KERNELS = ["linux-cachyos", "linux-zen", "linux", "linux-lts", "linux-hardened"]
NONE = "none"
# Editions that are full desktops (vs window managers) — splits the two blocks on
# the Configure screen. Anything not listed here is treated as a window manager.
DESKTOPS = {"xfce", "cinnamon", "plasma", "gnome", "mate", "budgie", "lxqt", "deepin"}

# Shipped defaults for the GUI-exposed knobs (the values build.conf ships with).
DEFAULTS = {
    "nvidia_driver": "open",
    "kernel": "linux-cachyos linux-zen",
    "editions": "xfce ohmychadwm",
    "default_session": "xfce",
    "bump_version": "yes",
    "clean_pacman_cache": "no",
    "remove_build_folder": "yes",
}


def _labelled(label, widget):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    lbl = Gtk.Label(label=label, xalign=0, hexpand=True, wrap=True)
    lbl.add_css_class("row-title")
    box.append(lbl)
    widget.set_halign(Gtk.Align.END)
    widget.set_valign(Gtk.Align.CENTER)
    box.append(widget)
    return box


class ConfigureScreen:
    def __init__(self, window):
        self.window = window
        self._loaded = False

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.widget, m)(18)

        title = Gtk.Label(label="Configure the build", xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)
        sub = Gtk.Label(label="These write straight to build.conf — the CLI uses the same file.",
                        xalign=0, wrap=True, max_width_chars=70)
        sub.add_css_class("dim-label")
        self.widget.append(sub)

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        form.add_css_class("card")

        self.nvidia = Gtk.DropDown.new_from_strings(NVIDIA)
        form.append(_labelled("NVIDIA driver", self.nvidia))
        nv_hint = Gtk.Label(
            label="AMD / Intel / a VM (no NVIDIA card)? Pick 'none' — no NVIDIA driver is baked "
                  "in, and at boot choose the first entry, 'open source: AMD / Intel', which "
                  "blacklists NVIDIA. The other options bake that NVIDIA driver set.",
            xalign=0, wrap=True)
        nv_hint.add_css_class("dim-label")
        form.append(nv_hint)

        self.kernel1 = Gtk.DropDown()
        self._set_options(self.kernel1, KERNELS, KERNELS[0])
        form.append(_labelled("First kernel (boots the live ISO)", self.kernel1))
        self.kernel2 = Gtk.DropDown()
        self._set_options(self.kernel2, [NONE] + KERNELS, NONE)
        form.append(_labelled("Second kernel (optional)", self.kernel2))

        detect_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.detect_btn = Gtk.Button(label="Detect available kernels")
        self.detect_btn.connect("clicked", lambda _w: self._detect())
        detect_row.append(self.detect_btn)
        self.kernel_status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.kernel_status.add_css_class("dim-label")
        detect_row.append(self.kernel_status)
        form.append(detect_row)
        hint = Gtk.Label(
            label="Only kernels with a matching -headers are listed; the build installs "
                  "those headers automatically (needed for the DKMS drivers).",
            xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        form.append(hint)

        ed_title = Gtk.Label(label="Editions (desktops & window managers)", xalign=0)
        ed_title.add_css_class("row-title")
        form.append(ed_title)
        # The "T": desktops in a horizontal row (top bar), window managers in a
        # vertical column (stem). Both feed the Default-session dropdown below.
        dt_lbl = Gtk.Label(label="Desktops", xalign=0)
        dt_lbl.add_css_class("dim-label")
        form.append(dt_lbl)
        self.desktops_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        form.append(self.desktops_box)
        wm_lbl = Gtk.Label(label="Window managers", xalign=0)
        wm_lbl.add_css_class("dim-label")
        form.append(wm_lbl)
        self.wms_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        form.append(self.wms_box)
        self.edition_checks = {}
        ed_note = Gtk.Label(
            label="Tick what you want — each becomes a session you can pick at the SDDM login "
                  "screen. Tick at least one.",
            xalign=0, wrap=True)
        ed_note.add_css_class("dim-label")
        form.append(ed_note)

        self.default_session = Gtk.DropDown()
        # The default session auto-follows the preferred edition (DE > ohmychadwm > first)
        # until the user picks a value themselves; _ds_updating distinguishes a programmatic
        # set from a real user pick so the override flag is only set by the latter.
        self._ds_user_override = False
        self._ds_updating = False
        self.default_session.connect("notify::selected", self._on_default_session_changed)
        form.append(_labelled("Default session (boots first)", self.default_session))
        ds_note = Gtk.Label(
            label="Which of the ticked editions the live ISO logs into first.",
            xalign=0, wrap=True)
        ds_note.add_css_class("dim-label")
        form.append(ds_note)

        adv = Gtk.Expander(label="Advanced")
        advbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.bump = Gtk.Switch(valign=Gtk.Align.CENTER)
        advbox.append(_labelled("Bump version before building", self.bump))
        self.clean = Gtk.Switch(valign=Gtk.Align.CENTER)
        advbox.append(_labelled("Clean pacman cache", self.clean))
        self.remove_build = Gtk.Switch(valign=Gtk.Align.CENTER)
        advbox.append(_labelled("Remove build folder after build", self.remove_build))
        adv.set_child(advbox)
        form.append(adv)

        self.widget.append(form)

        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class("att-orange-note")
        self.widget.append(self.status)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        reset = Gtk.Button(label="Reset to defaults")
        reset.connect("clicked", lambda _w: self._reset())
        nav.append(reset)
        import_prof = Gtk.Button(label="Import build profile…")
        import_prof.connect("clicked", lambda _w: self._import_profile())
        nav.append(import_prof)
        nav.append(Gtk.Box(hexpand=True))  # spacer: pushes Back/Save to the right
        back = Gtk.Button(label="← Back")
        back.connect("clicked", lambda _w: self.window.navigate("preflight"))
        save = Gtk.Button(label="Save & Continue →")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _w: self._save())
        nav.append(back)
        nav.append(save)
        self.widget.append(nav)

    def on_show(self):
        # Load from build.conf only on the first successful visit; after that the
        # dropdowns hold the working state, so revisiting via the sidebar must not
        # clobber unsaved edits. Keep retrying while build.conf is still missing
        # (e.g. until Pre-flight clones the repo).
        if not self._loaded:
            self._load()

    def _load(self):
        if not fn.build_conf_path():
            self.status.set_text("build.conf not found — fix the clone on the Pre-flight screen.")
            self.widget.set_sensitive(False)
            return
        self.widget.set_sensitive(True)
        self._populate_editions()
        self._apply(fn.read_conf())
        self.status.set_text("")
        self._loaded = True

    def _populate_editions(self):
        """Build a checkbox per edition discovered in packages.x86_64 (EDITION-BLOCK
        markers), split into the Desktops row and the Window-managers column.
        Rebuilds only when the set changes (first load / after the clone)."""
        names = fn.list_editions()
        if list(self.edition_checks) == names:
            return
        for box in (self.desktops_box, self.wms_box):
            child = box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                box.remove(child)
                child = nxt
        self.edition_checks = {}
        for name in names:
            cb = Gtk.CheckButton(label=name)
            cb.connect("toggled", lambda *_: self._refresh_default_session_options())
            (self.desktops_box if name in DESKTOPS else self.wms_box).append(cb)
            self.edition_checks[name] = cb
        self._refresh_default_session_options()

    @staticmethod
    def _preferred_default(ticked):
        """Sensible default session: a full desktop wins, else flagship ohmychadwm, else
        the first ticked edition. Mirrors apply_editions' fallback in build-the-iso.sh."""
        for name in ticked:
            if name in DESKTOPS:
                return name
        return "ohmychadwm" if "ohmychadwm" in ticked else ticked[0]

    def _on_default_session_changed(self, _dropdown, _param):
        if not self._ds_updating:
            self._ds_user_override = True

    def _set_default_session(self, items, current):
        """Set the default-session dropdown without it counting as a user override."""
        self._ds_updating = True
        self._set_options(self.default_session, items, current)
        self._ds_updating = False

    def _refresh_default_session_options(self):
        """Dropdown lists only the ticked editions. Auto-follow the preferred default
        (DE > ohmychadwm > first) so a freshly ticked desktop becomes the boot session;
        a value the user picked themselves is kept as long as it stays ticked."""
        ticked = [n for n, cb in self.edition_checks.items() if cb.get_active()]
        if not ticked:
            self._ds_updating = True
            self.default_session.set_model(Gtk.StringList.new([]))
            self._ds_updating = False
            return
        current = self._selected(self.default_session)
        if self._ds_user_override and current in ticked:
            target = current
        else:
            self._ds_user_override = False
            target = self._preferred_default(ticked)
        self._set_default_session(ticked, target)

    def _apply(self, conf):
        """Set every control from a {key: value} dict (build.conf or DEFAULTS)."""
        self._select(self.nvidia, NVIDIA, conf.get("nvidia_driver", "open"))

        tokens = [t for t in conf.get("kernel", "").split() if t != "ask"]
        first = tokens[0] if tokens else KERNELS[0]
        second = tokens[1] if len(tokens) > 1 else NONE
        # Offer the curated list plus any saved kernel not already in it, so the
        # current value is always selectable even before Detect runs.
        first_opts = KERNELS + [t for t in tokens if t not in KERNELS]
        self._set_options(self.kernel1, first_opts, first)
        self._set_options(self.kernel2, [NONE] + first_opts, second)

        sel = conf.get("editions", "xfce ohmychadwm").split()
        for name, cb in self.edition_checks.items():
            cb.set_active(name in sel)
        ticked = [n for n, cb in self.edition_checks.items() if cb.get_active()]
        ds = conf.get("default_session", "xfce")
        if ticked:
            has_desktop = any(t in DESKTOPS for t in ticked)
            # Honour a saved session if it's a desktop, or if there's no desktop to prefer;
            # otherwise a saved WM loses to a ticked desktop (the forgetful-user fix).
            if ds in ticked and (ds in DESKTOPS or not has_desktop):
                target = ds
            else:
                target = self._preferred_default(ticked)
            self._ds_user_override = target == ds
            self._set_default_session(ticked, target)
        else:
            self._ds_updating = True
            self.default_session.set_model(Gtk.StringList.new([]))
            self._ds_updating = False

        self.bump.set_active(conf.get("bump_version", "yes") == "yes")
        self.clean.set_active(conf.get("clean_pacman_cache", "no") == "yes")
        self.remove_build.set_active(conf.get("remove_build_folder", "no") == "yes")

    def _reset(self):
        self._apply(DEFAULTS)
        self.status.set_text("Reset to defaults — click Save & Continue to apply.")

    # ── kernel detection ────────────────────────────────────────────
    def _detect(self):
        self.detect_btn.set_sensitive(False)
        self.kernel_status.set_text("Detecting…")
        cur1, cur2 = self._selected(self.kernel1), self._selected(self.kernel2)

        def worker():
            kernels = fn.list_kernels()
            GLib.idle_add(self._apply_detected, kernels, cur1, cur2)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_detected(self, kernels, cur1, cur2):
        self.detect_btn.set_sensitive(True)
        if not kernels:
            self.kernel_status.set_text("None found — sync repos first; keeping the built-in list.")
            return
        self._set_options(self.kernel1, kernels, cur1 if cur1 in kernels else kernels[0])
        second_opts = [NONE] + kernels
        self._set_options(self.kernel2, second_opts, cur2 if cur2 in second_opts else NONE)
        self.kernel_status.set_text(f"Found {len(kernels)} kernels.")

    # ── helpers ─────────────────────────────────────────────────────
    @staticmethod
    def _set_options(dropdown, items, current):
        dropdown.set_model(Gtk.StringList.new(items))
        dropdown.set_selected(items.index(current) if current in items else 0)

    @staticmethod
    def _selected(dropdown):
        item = dropdown.get_selected_item()
        return item.get_string() if item is not None else None

    @staticmethod
    def _select(dropdown, items, value):
        dropdown.set_selected(items.index(value) if value in items else 0)

    def _save(self):
        checked = [n for n, cb in self.edition_checks.items() if cb.get_active()]
        if not checked:
            self.status.set_text("Tick at least one desktop or window manager — an ISO needs a session.")
            return
        fn.set_conf("nvidia_driver", NVIDIA[self.nvidia.get_selected()])
        first = self._selected(self.kernel1)
        second = self._selected(self.kernel2)
        kernels = [first] + ([second] if second and second not in (NONE, first) else [])
        fn.set_conf("kernel", " ".join(k for k in kernels if k))
        fn.set_conf("editions", " ".join(checked))
        ds = self._selected(self.default_session)
        if ds:
            fn.set_conf("default_session", ds)
        fn.set_conf("bump_version", "yes" if self.bump.get_active() else "no")
        fn.set_conf("clean_pacman_cache", "yes" if self.clean.get_active() else "no")
        fn.set_conf("remove_build_folder", "yes" if self.remove_build.get_active() else "no")
        self.window.navigate("packages")

    # ── import a shareable build profile (settings + package selection) ─
    def _import_profile(self):
        if not fn.build_conf_path():
            self.status.set_text(f"{fn.REPO_NAME} clone not found — fix it on the Pre-flight screen.")
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Import build profile")
        dialog.set_initial_folder(Gio.File.new_for_path(str(fn.profiles_dir())))
        flt = Gtk.FileFilter()
        flt.set_name("Kiro build profiles")
        flt.add_pattern(f"*{fn.PROFILE_EXT}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)
        dialog.open(self.window, None, self._on_profile_open_ready)

    def _on_profile_open_ready(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if not gfile:
            return
        settings, excludes = fn.read_build_profile(gfile.get_path())
        conf = fn.read_conf()
        conf.update(settings)
        self._apply(conf)
        fn.write_excludes(excludes)
        self.status.set_text(
            f"Imported profile — {len(excludes)} package(s) to remove. "
            "Review the settings, then click Save & Continue.")
