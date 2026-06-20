"""Add apps screen — opt-in apps the ISO does NOT ship by default.

The mirror image of the Packages screen: there everything is ticked and you untick
to remove; here NOTHING is ticked and you tick to ADD. Selected app keys are written
to package-additions.conf, which the build uncomments (apply_package_additions). The
catalog is auto-discovered from the EXTRA-APP blocks in packages.x86_64 — one source
of truth, so a new app appears here with no code change.
"""

import gi

import functions as fn

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402


class ExtrasScreen:
    def __init__(self, window, edition=None):
        # edition=None → the global "Add apps" page (unscoped apps only). edition="plasma"
        # → a per-edition page shown only while that edition is ticked (scoped apps only).
        self.window = window
        self.edition = edition
        self.checks = []       # (key, CheckButton)
        self.groups = []       # (category_check, [child_checks])
        self.sections = []     # ([header_widgets], [(name_lower, widget)])
        self._syncing = False

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.widget, m)(18)

        if edition is None:
            title_text = "Add apps"
            subs = ("These apps are NOT on the standard Kiro — tick the ones you want added.",
                    "Nothing is ticked by default. Tick none and your ISO stays the standard Kiro.")
        else:
            ed = edition.capitalize()
            title_text = f"{ed} extras"
            subs = (f"Optional {ed} apps, added only to this {ed} build — tick what you want.",
                    "Nothing is ticked by default.")
        title = Gtk.Label(label=title_text, xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)
        subbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for text in subs:
            s = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=86)
            s.add_css_class("att-orange")
            subbox.append(s)
        self.widget.append(subbox)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry(hexpand=True, placeholder_text="Search apps…")
        self.search.connect("search-changed", lambda e: self._apply_filter(e.get_text()))
        none_btn = Gtk.Button(label="Deselect all")
        none_btn.connect("clicked", lambda _w: self._set_all(False))
        toolbar.append(self.search)
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
        save_prof = Gtk.Button(label="Save app list…")
        save_prof.connect("clicked", lambda _w: self._save_profile())
        import_prof = Gtk.Button(label="Import app list…")
        import_prof.connect("clicked", lambda _w: self._import_profile())
        profile.append(save_prof)
        profile.append(import_prof)
        self.widget.append(profile)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        # Relative nav: edition pages slot in dynamically between "Add apps" and "Build",
        # so step to the adjacent VISIBLE page rather than a hardcoded name.
        back = Gtk.Button(label="← Back")
        back.connect("clicked", lambda _w: self.window.navigate_relative(self, -1))
        save = Gtk.Button(label="Save & Continue →")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _w: self._save())
        nav.append(back)
        nav.append(save)
        self.widget.append(nav)

    def on_show(self):
        self._populate()

    def _in_scope(self, app):
        """True if `app` belongs on this page: unscoped apps on the global page,
        edition-scoped apps on their edition's page."""
        if self.edition is None:
            return not app["editions"]
        return self.edition in app["editions"]

    def _catalog_keys(self):
        """All app keys this page owns — used to reconcile the shared additions file
        without touching keys owned by other pages."""
        return fn.extra_app_keys(self.edition)

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

        # Keep only apps in this page's scope: the global page shows unscoped apps,
        # an edition page shows apps scoped to its edition. Empty categories drop out.
        categories = []
        for category, apps in fn.read_extra_apps():
            scoped = [a for a in apps if self._in_scope(a)]
            if scoped:
                categories.append((category, scoped))
        added = fn.read_additions()
        if not categories:
            self.status.set_text("No extra apps are offered yet.")
            return

        total = 0
        for category, apps in categories:
            cat_check = Gtk.CheckButton(label=category)
            cat_check.add_css_class("row-title")
            cat_check.set_margin_top(10)
            self.container.append(cat_check)

            child_checks, items = [], []
            for app in apps:
                row = Gtk.CheckButton(label=app["label"])
                row.set_margin_start(24)
                row.set_active(app["key"] in added)
                row.set_tooltip_text(f"{app['repo']}: {' '.join(app['packages'])}")
                row.connect("toggled", self._on_child_toggled, cat_check, child_checks)
                self.container.append(row)
                self.checks.append((app["key"], row))
                child_checks.append(row)
                items.append((f"{app['label']} {app['key']} {' '.join(app['packages'])}".lower(), row))
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
        for _key, row in self.checks:
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
        added = sum(1 for _k, row in self.checks if row.get_active())
        self.status.set_text(f"Apps that will be added: {added}/{total}")

    def _save(self):
        keys = {key for key, row in self.checks if row.get_active()}
        # Reconcile only this page's keys into the shared additions file, so the global
        # and per-edition pages don't overwrite each other.
        fn.reconcile_additions(self._catalog_keys(), keys)
        self.window.navigate_relative(self, +1)

    # ── save / import a named app list ──────────────────────────────
    def _apply_keys(self, keys):
        self._syncing = True
        for key, row in self.checks:
            row.set_active(key in keys)
        self._syncing = False
        for cat_check, child_checks in self.groups:
            self._sync_header(cat_check, child_checks)
        self._update_status()

    def _save_profile(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Save app list")
        dialog.set_initial_name("kiro-extra-apps.txt")
        dialog.set_initial_folder(Gio.File.new_for_path(str(fn.profiles_dir())))
        dialog.save(self.window, None, self._on_save_ready)

    def _on_save_ready(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        if gfile:
            keys = {key for key, row in self.checks if row.get_active()}
            fn.write_additions_file(gfile.get_path(), keys)
            self.status.set_text(f"Saved app list ({len(keys)} added) → {gfile.get_path()}")

    def _import_profile(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Import app list")
        dialog.set_initial_folder(Gio.File.new_for_path(str(fn.profiles_dir())))
        dialog.open(self.window, None, self._on_open_ready)

    def _on_open_ready(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile:
            keys = fn.read_additions_file(gfile.get_path())
            self._apply_keys(keys)
            self.status.set_text(f"Imported app list — {len(keys)} app(s) marked to add.")
