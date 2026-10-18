"""Packages screen — pick which optional (TIER 3) packages ship on the ISO.

Reuses the ATT "streamline" idea: TIER 3 packages grouped by category, with
category-level select-all (tri-state) and a search filter. Unticked packages are
written to package-selection.conf, which the build comments out. TIER 1/2 are
never shown, so nothing here can break the build. An unticked package that still
ships (an edition, a dependency or a package group brings it back) says so on its row.
"""

import threading

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
        self._kept = {}
        self._kept_ready = False

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.widget, m)(18)

        title = Gtk.Label(label="Choose packages", xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)
        subbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sub1 = Gtk.Label(
            label="If everything is selected you get the packages from the default Kiro ISO.",
            xalign=0, wrap=True, max_width_chars=86)
        sub2 = Gtk.Label(
            label="Unselect what you do not want.",
            xalign=0, wrap=True, max_width_chars=86)
        sub3 = Gtk.Label(
            label="Core packages always ship and can't be unticked — open the list below to see them.",
            xalign=0, wrap=True, max_width_chars=70)
        for s in (sub1, sub2, sub3):
            s.add_css_class("att-orange")
            subbox.append(s)
        self.widget.append(subbox)

        self.core_expander = Gtk.Expander(label="Show the core packages")
        self.core_label = Gtk.Label(xalign=0, wrap=True, selectable=True, max_width_chars=110)
        self.core_label.add_css_class("dim-label")
        self.core_expander.set_child(self.core_label)
        self.widget.append(self.core_expander)

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

        self.status = Gtk.Label(xalign=0, wrap=True)
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
            self.status.set_text("packages.x86_64 not found — fix the clone on the Pre-flight screen.")
            self.widget.set_sensitive(False)
            return
        self.widget.set_sensitive(True)

        core = fn.read_core()
        self.core_expander.set_label(f"Show the core packages ({len(core)})")
        self.core_label.set_text(", ".join(core))

        categories = fn.read_edition_optional(self._editions()) + fn.read_tier3()
        excluded = fn.read_excludes()
        if not categories:
            self.status.set_text("No optional apps were found to choose from.")
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
                row.set_active(pkg not in excluded)
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
        self._start_kept_scan()

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
        self._refresh_kept()
        shipping = sum(1 for _p, row in self.checks if row.get_active())
        text = f"Packages that will ship: {shipping}/{total}  ({total - shipping} left out)"
        if self._kept:
            text += f" — {len(self._kept)} unticked still ship, see the rows marked 'still ships'"
        self.status.set_text(text)

    # ── unticked packages that come back anyway ─────────────────────
    def _start_kept_scan(self):
        """Warm the pacman caches off the GTK thread, then mark the rows."""
        excludes, editions = self._excludes(), self._editions()

        def worker():
            fn.kept_packages(excludes, editions)
            GLib.idle_add(self._on_kept_ready)

        threading.Thread(target=worker, daemon=True).start()

    def _on_kept_ready(self):
        self._kept_ready = True
        self._update_status()
        return False

    def _refresh_kept(self):
        if not self._kept_ready:
            return
        self._kept = fn.kept_packages(self._excludes(), self._editions())
        for pkg, row in self.checks:
            reason = self._kept.get(pkg)
            row.set_label(f"{pkg}   — still ships: {reason}" if reason else pkg)
            row.set_tooltip_text(
                f"Unticked, but still on the ISO: {reason}." if reason else None)

    def _excludes(self):
        # Keep exclusions for rows not shown now (an edition unticked on Configure), so
        # ticking that edition again brings back the user's earlier choice.
        shown = {pkg for pkg, _row in self.checks}
        hidden = fn.read_excludes() - shown
        return hidden | {pkg for pkg, row in self.checks if not row.get_active()}

    @staticmethod
    def _editions():
        return fn.read_conf().get("editions", "").split()

    def _save(self):
        fn.write_excludes(self._excludes())
        self.window.navigate("extras")

    # ── save / import a named profile (from ATT streamline) ─────────
    def _apply_excludes(self, excludes):
        self._syncing = True
        for pkg, row in self.checks:
            row.set_active(pkg not in excludes)
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
            excludes = self._excludes()
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
