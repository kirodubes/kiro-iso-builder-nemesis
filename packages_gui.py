"""Packages screen — pick which optional (TIER 3) packages ship on the ISO.

Reuses the ATT "streamline" idea: TIER 3 packages grouped by category, with
category-level select-all (tri-state) and a search filter. Unticked packages are
written to package-selection.conf, which the build comments out. TIER 1/2 are
never shown, so nothing here can break the build. A row that ships even with every
row unticked (core, a ticked edition or add-on needs it) is hidden and listed under
the core expander instead; one that only another ticked row brings back says so on its row.
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
        self.sections = []     # (category, arrow_button, [header_widgets], [(name_lower, widget)])
        self._expanded = set()  # categories folded open; kept across visits, folded by default
        self._count_of = {}    # category CheckButton -> its 'ticked/total' Label
        self._syncing = False
        self._kept = {}
        self._kept_ready = False
        self._core = []
        self._hidden = {}      # pkg -> reason it always ships; its row is hidden
        self._pkg_of = {}      # CheckButton -> pkg

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
            label="Packages that always ship (core, or needed by your editions) are not listed — "
                  "open the list below to see them.",
            xalign=0, wrap=True, max_width_chars=70)
        for s in (sub1, sub2, sub3):
            s.add_css_class("att-orange")
            subbox.append(s)
        self.widget.append(subbox)

        self.core_expander = Gtk.Expander(label="Show the core packages")
        self.core_label = Gtk.Label(xalign=0, wrap=True, selectable=True, max_width_chars=110)
        self.core_label.add_css_class("dim-label")
        # Scrolls on its own, so opening ~300 names never pushes the window past the screen.
        core_scroll = Gtk.ScrolledWindow(max_content_height=220, propagate_natural_height=True)
        core_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        core_scroll.set_child(self.core_label)
        self.core_expander.set_child(core_scroll)
        self.widget.append(self.core_expander)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry(hexpand=True, placeholder_text="Search packages…")
        self.search.connect("search-changed", lambda e: self._apply_filter(e.get_text()))
        all_btn = Gtk.Button(label="Select all")
        all_btn.connect("clicked", lambda _w: self._set_all(True))
        none_btn = Gtk.Button(label="Deselect all")
        none_btn.connect("clicked", lambda _w: self._set_all(False))
        expand_btn = Gtk.Button(label="Expand all")
        expand_btn.connect("clicked", lambda _w: self._set_expanded_all(True))
        collapse_btn = Gtk.Button(label="Collapse all")
        collapse_btn.connect("clicked", lambda _w: self._set_expanded_all(False))
        toolbar.append(self.search)
        toolbar.append(expand_btn)
        toolbar.append(collapse_btn)
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
        self._hidden, self._pkg_of, self._count_of = {}, {}, {}

        if fn.packages_file() is None:
            self.status.set_text("packages.x86_64 not found — fix the clone on the Pre-flight screen.")
            self.widget.set_sensitive(False)
            return
        self.widget.set_sensitive(True)

        self._core = fn.read_core()
        self._show_always_list()

        categories = fn.package_rows(self._editions())
        excluded = fn.read_excludes()
        if not categories:
            self.status.set_text("No optional apps were found to choose from.")
            return

        total = 0
        for category, pkgs in categories:
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, margin_top=10)
            arrow = Gtk.Button(has_frame=False, tooltip_text="Show or hide the packages in this group")
            arrow.connect("clicked", self._on_arrow_clicked, category)
            cat_check = Gtk.CheckButton(label=category)
            cat_check.add_css_class("row-title")
            count = Gtk.Label()
            count.add_css_class("dim-label")
            self._count_of[cat_check] = count
            header.append(arrow)
            header.append(cat_check)
            header.append(count)
            self.container.append(header)

            child_checks, items = [], []
            for pkg in pkgs:
                row = Gtk.CheckButton(label=pkg)
                row.set_margin_start(44)
                row.set_active(pkg not in excluded)
                row.connect("toggled", self._on_child_toggled, cat_check, child_checks)
                self.container.append(row)
                self.checks.append((pkg, row))
                self._pkg_of[row] = pkg
                child_checks.append(row)
                items.append((pkg.lower(), row))
                total += 1

            cat_check.connect("toggled", self._on_category_toggled, child_checks)
            self.groups.append((cat_check, child_checks))
            self.sections.append((category, arrow, [header], items))
            self._sync_header(cat_check, child_checks)

        self._apply_filter(self.search.get_text())
        self._update_status(total)
        self._start_kept_scan()

    # ── tri-state sync (from ATT streamline) ────────────────────────
    def _sync_header(self, cat_check, child_checks):
        states = [c.get_active() for c in child_checks if self._pkg_of.get(c) not in self._hidden] or [True]
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
        shown = [c for c in child_checks if self._pkg_of.get(c) not in self._hidden]
        if cat_check in self._count_of:
            self._count_of[cat_check].set_text(f"{sum(c.get_active() for c in shown)}/{len(shown)}")

    def _on_category_toggled(self, cat_check, child_checks):
        if self._syncing:
            return
        active = cat_check.get_active()
        self._syncing = True
        for c in child_checks:
            if self._pkg_of.get(c) not in self._hidden:
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
        for pkg, row in self.checks:
            if pkg not in self._hidden:
                row.set_active(active)
        self._syncing = False
        for cat_check, child_checks in self.groups:
            self._sync_header(cat_check, child_checks)
        self._update_status()

    # ── search filter (from ATT streamline) ─────────────────────────
    def _apply_filter(self, query):
        # A search shows its matches even inside folded groups; with no search a group shows its
        # rows only when folded open.
        q = query.strip().lower()
        for category, arrow, headers, items in self.sections:
            is_open = category in self._expanded or bool(q)
            arrow.set_icon_name("pan-down-symbolic" if is_open else "pan-end-symbolic")
            any_visible = False
            for name, widget in items:
                match = ((not q) or (q in name)) and name not in self._hidden
                widget.set_visible(match and is_open)
                if match:
                    any_visible = True
            for header in headers:
                header.set_visible(any_visible)

    def _on_arrow_clicked(self, _w, category):
        self._expanded ^= {category}
        self._apply_filter(self.search.get_text())

    def _set_expanded_all(self, is_open):
        self._expanded = {category for category, *_ in self.sections} if is_open else set()
        self._apply_filter(self.search.get_text())

    def _update_status(self, total=None):
        self._refresh_kept()
        rows = [row for pkg, row in self.checks if pkg not in self._hidden]
        total = len(rows)
        shipping = sum(1 for row in rows if row.get_active())
        text = f"Packages that will ship: {shipping}/{total}  ({total - shipping} left out)"
        if self._kept:
            text += f" — {len(self._kept)} unticked still ship, see the rows marked 'still ships'"
        self.status.set_text(text)

    def _show_always_list(self):
        n = len(self._core) + len(self._hidden)
        self.core_expander.set_label(f"Show the packages that always ship ({n})")
        text = "Core: " + ", ".join(self._core)
        if self._hidden:
            text += "\n\nAlso ships because of your editions or add-ons:\n" + "\n".join(
                f"{pkg} — {reason}" for pkg, reason in sorted(self._hidden.items()))
        self.core_label.set_text(text)

    # ── unticked packages that come back anyway ─────────────────────
    def _start_kept_scan(self):
        """Work out (off the GTK thread) which rows always ship, then hide them and mark the rest."""
        rows, editions = [pkg for pkg, _row in self.checks], self._editions()

        def worker():
            hidden = fn.always_shipped(rows, editions)
            GLib.idle_add(self._on_kept_ready, hidden)

        threading.Thread(target=worker, daemon=True).start()

    def _on_kept_ready(self, hidden):
        self._kept_ready = True
        self._hidden = hidden
        for cat_check, child_checks in self.groups:
            self._sync_header(cat_check, child_checks)
        self._apply_filter(self.search.get_text())
        self._show_always_list()
        self._update_status()
        return False

    def _refresh_kept(self):
        if not self._kept_ready:
            return
        self._kept = {p: r for p, r in fn.kept_packages(self._excludes(), self._editions()).items()
                      if p not in self._hidden}
        for pkg, row in self.checks:
            reason = self._kept.get(pkg)
            row.set_label(f"{pkg}   — still ships: {reason}" if reason else pkg)
            row.set_tooltip_text(
                f"Unticked, but still on the ISO: {reason}." if reason else None)

    def _excludes(self):
        # Keep exclusions for rows not shown now (an edition unticked on Configure), so
        # ticking that edition again brings back the user's earlier choice.
        # A row hidden because it always ships is never saved as unticked.
        shown = {pkg for pkg, _row in self.checks}
        hidden = fn.read_excludes() - shown
        return hidden | {pkg for pkg, row in self.checks if not row.get_active() and pkg not in self._hidden}

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
