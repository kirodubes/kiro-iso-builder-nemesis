"""Packages screen — pick which optional (TIER 3) packages ship on the ISO.

Reuses the ATT "streamline" idea: TIER 3 packages grouped by category, with
category-level select-all (tri-state) and a search filter. Unticked packages are
written to package-selection.conf, which the build comments out. TIER 1/2 are
never shown, so nothing here can break the build.
"""

import gi

import functions as fn

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402


class PackagesScreen:
    def __init__(self, window):
        self.window = window
        self.checks = []       # (pkg, CheckButton)
        self.groups = []       # (category_check, [child_checks])
        self.sections = []     # ([header_widgets], [(name_lower, widget)])
        self._syncing = False

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.widget, m)(18)

        title = Gtk.Label(label="Choose packages", xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)
        sub = Gtk.Label(
            label="All optional (TIER 3) apps ship by default — tick any you want REMOVED from "
                  "the ISO. Core packages always ship and aren't listed here.",
            xalign=0, wrap=True, max_width_chars=70)
        sub.add_css_class("att-orange")
        self.widget.append(sub)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry(hexpand=True, placeholder_text="Search packages…")
        self.search.connect("search-changed", lambda e: self._apply_filter(e.get_text()))
        all_btn = Gtk.Button(label="Select all")
        all_btn.connect("clicked", lambda _w: self._set_all(True))
        none_btn = Gtk.Button(label="Deselect all")
        none_btn.connect("clicked", lambda _w: self._set_all(False))
        toolbar.append(self.search)
        toolbar.append(all_btn)
        toolbar.append(none_btn)
        self.widget.append(toolbar)

        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.container)
        self.widget.append(scroller)

        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("att-orange-note")
        self.widget.append(self.status)

        profile = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_prof = Gtk.Button(label="Save package list…")
        save_prof.connect("clicked", lambda _w: self._save_profile())
        import_prof = Gtk.Button(label="Import package list…")
        import_prof.connect("clicked", lambda _w: self._import_profile())
        profile.append(save_prof)
        profile.append(import_prof)
        self.widget.append(profile)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        back = Gtk.Button(label="← Back")
        back.connect("clicked", lambda _w: self.window.navigate("configure"))
        save = Gtk.Button(label="Save & Continue →")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _w: self._save())
        nav.append(back)
        nav.append(save)
        self.widget.append(nav)

    def on_show(self):
        self._populate()

    # ── build the list ──────────────────────────────────────────────
    def _populate(self):
        child = self.container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.container.remove(child)
            child = nxt
        self.checks, self.groups, self.sections = [], [], []

        if fn.packages_file() is None:
            self.status.set_text("packages.x86_64 not found — fix the repo on the Pre-flight screen.")
            self.widget.set_sensitive(False)
            return
        self.widget.set_sensitive(True)

        categories = fn.read_tier3()
        excluded = fn.read_excludes()
        if not categories:
            self.status.set_text("No TIER 3 packages found in packages.x86_64.")
            return

        total = 0
        for category, pkgs in categories:
            cat_check = Gtk.CheckButton(label=category)
            cat_check.add_css_class("row-title")
            cat_check.set_margin_top(10)
            self.container.append(cat_check)

            child_checks, items = [], []
            for pkg in pkgs:
                row = Gtk.CheckButton(label=pkg)
                row.set_margin_start(24)
                row.set_active(pkg in excluded)
                row.connect("toggled", self._on_child_toggled, cat_check, child_checks)
                self.container.append(row)
                self.checks.append((pkg, row))
                child_checks.append(row)
                items.append((pkg.lower(), row))
                total += 1

            cat_check.connect("toggled", self._on_category_toggled, child_checks)
            self.groups.append((cat_check, child_checks))
            self.sections.append(([cat_check], items))
            self._sync_header(cat_check, child_checks)

        self._apply_filter(self.search.get_text())
        self._update_status(total)

    # ── tri-state sync (from ATT streamline) ────────────────────────
    def _sync_header(self, cat_check, child_checks):
        states = [c.get_active() for c in child_checks]
        self._syncing = True
        if all(states):
            cat_check.set_inconsistent(False)
            cat_check.set_active(True)
        elif not any(states):
            cat_check.set_inconsistent(False)
            cat_check.set_active(False)
        else:
            cat_check.set_active(False)
            cat_check.set_inconsistent(True)
        self._syncing = False

    def _on_category_toggled(self, cat_check, child_checks):
        if self._syncing:
            return
        active = cat_check.get_active()
        self._syncing = True
        for c in child_checks:
            c.set_active(active)
        cat_check.set_inconsistent(False)
        self._syncing = False
        self._update_status()

    def _on_child_toggled(self, _child, cat_check, child_checks):
        if self._syncing:
            return
        self._sync_header(cat_check, child_checks)
        self._update_status()

    def _set_all(self, active):
        self._syncing = True
        for _pkg, row in self.checks:
            row.set_active(active)
        self._syncing = False
        for cat_check, child_checks in self.groups:
            self._sync_header(cat_check, child_checks)
        self._update_status()

    # ── search filter (from ATT streamline) ─────────────────────────
    def _apply_filter(self, query):
        q = query.strip().lower()
        for headers, items in self.sections:
            any_visible = False
            for name, widget in items:
                match = (not q) or (q in name)
                widget.set_visible(match)
                if match:
                    any_visible = True
            for header in headers:
                header.set_visible(any_visible)

    def _update_status(self, total=None):
        if total is None:
            total = len(self.checks)
        removed = sum(1 for _p, row in self.checks if row.get_active())
        self.status.set_text(f"Packages that will be removed from the iso: {removed}/{total}")

    def _save(self):
        excludes = {pkg for pkg, row in self.checks if row.get_active()}
        fn.write_excludes(excludes)
        self.window.navigate("build")

    # ── save / import a named profile (from ATT streamline) ─────────
    def _apply_excludes(self, excludes):
        self._syncing = True
        for pkg, row in self.checks:
            row.set_active(pkg in excludes)
        self._syncing = False
        for cat_check, child_checks in self.groups:
            self._sync_header(cat_check, child_checks)
        self._update_status()

    def _save_profile(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Save package profile")
        dialog.set_initial_name("kiro-packages.txt")
        dialog.set_initial_folder(Gio.File.new_for_path(str(fn.profiles_dir())))
        dialog.save(self.window, None, self._on_save_ready)

    def _on_save_ready(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        if gfile:
            excludes = {pkg for pkg, row in self.checks if row.get_active()}
            fn.write_exclude_file(gfile.get_path(), excludes)
            self.status.set_text(f"Saved profile ({len(excludes)} excluded) → {gfile.get_path()}")

    def _import_profile(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Import package profile")
        dialog.set_initial_folder(Gio.File.new_for_path(str(fn.profiles_dir())))
        dialog.open(self.window, None, self._on_open_ready)

    def _on_open_ready(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile:
            excludes = fn.read_exclude_file(gfile.get_path())
            self._apply_excludes(excludes)
            self.status.set_text(
                f"Imported profile — {len(excludes)} package(s) marked for exclusion.")
