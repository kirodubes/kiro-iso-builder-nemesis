"""Configure screen — edits the knobs in build.conf (shared with the CLI)."""

import threading

import gi

import functions as fn

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

NVIDIA = ["open", "580xx", "390xx"]
# Curated fallback list, used until the user clicks Detect (or if no repo DB).
KERNELS = ["linux-cachyos", "linux-zen", "linux", "linux-lts", "linux-hardened"]
NONE = "none"

# Shipped defaults for the GUI-exposed knobs (the values build.conf ships with).
DEFAULTS = {
    "nvidia_driver": "open",
    "kernel": "linux-cachyos linux-zen",
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

        self.bump = Gtk.Switch(valign=Gtk.Align.CENTER)
        form.append(_labelled("Bump version before building", self.bump))

        adv = Gtk.Expander(label="Advanced")
        advbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.clean = Gtk.Switch(valign=Gtk.Align.CENTER)
        advbox.append(_labelled("Clean pacman cache", self.clean))
        self.remove_build = Gtk.Switch(valign=Gtk.Align.CENTER)
        advbox.append(_labelled("Remove build folder after build", self.remove_build))
        adv.set_child(advbox)
        form.append(adv)

        self.widget.append(form)

        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class("dim-label")
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
        self._apply(fn.read_conf())
        self.status.set_text("")
        self._loaded = True

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
        fn.set_conf("nvidia_driver", NVIDIA[self.nvidia.get_selected()])
        first = self._selected(self.kernel1)
        second = self._selected(self.kernel2)
        kernels = [first] + ([second] if second and second not in (NONE, first) else [])
        fn.set_conf("kernel", " ".join(k for k in kernels if k))
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
