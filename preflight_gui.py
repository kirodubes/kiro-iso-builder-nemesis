"""Pre-flight screen — runs host checks and offers one-click fixes."""

import threading

import gi

import functions as fn
import host_checks as hc

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

_ICON = {hc.OK: "emblem-ok-symbolic",
         hc.WARN: "dialog-warning-symbolic",
         hc.FAIL: "dialog-error-symbolic"}


class PreflightScreen:
    def __init__(self, window):
        self.window = window
        self.rows = {}
        self._fix_queue = []

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.widget.set_margin_top(18)
        self.widget.set_margin_bottom(18)
        self.widget.set_margin_start(18)
        self.widget.set_margin_end(18)

        title = Gtk.Label(label="Pre-flight checks", xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)

        # The decision that matters most, surfaced first: one folder that holds the
        # clone, the build files and the finished ISO.
        folder_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        folder_card.add_css_class("card")
        folder_heading = Gtk.Label(label="Where should Kiro build?", xalign=0)
        folder_heading.add_css_class("row-title")
        folder_card.append(folder_heading)

        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.repo_label = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=True)
        self.repo_label.add_css_class("dim-label")
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.connect("clicked", lambda _w: self._browse_repo())
        folder_row.append(self.repo_label)
        folder_row.append(browse_btn)
        folder_card.append(folder_row)

        folder_help = Gtk.Label(
            label="The clone, the build files (kiro-build) and the finished ISO (kiro-Out) "
                  "all live in this one folder.",
            xalign=0, wrap=True, max_width_chars=70)
        folder_help.add_css_class("dim-label")
        folder_card.append(folder_help)
        self.widget.append(folder_card)

        subtitle = Gtk.Label(
            label="Then confirm the host is ready and fix anything red before building.",
            xalign=0, wrap=True, max_width_chars=70)
        subtitle.add_css_class("dim-label")
        self.widget.append(subtitle)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.recheck_btn = Gtk.Button(label="Re-check")
        self.recheck_btn.connect("clicked", lambda _w: self.refresh())
        self.fixall_btn = Gtk.Button(label="Fix all")
        self.fixall_btn.add_css_class("suggested-action")
        self.fixall_btn.connect("clicked", lambda _w: self._fix_all())
        toolbar.append(self.recheck_btn)
        toolbar.append(self.fixall_btn)
        self.widget.append(toolbar)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.listbox)
        self.widget.append(scroller)

        log_exp = Gtk.Expander(label="Fix log")
        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.log_buf = self.log_view.get_buffer()
        log_scroll = Gtk.ScrolledWindow(min_content_height=140)
        log_scroll.set_child(self.log_view)
        log_exp.set_child(log_scroll)
        self.widget.append(log_exp)

        self.continue_btn = Gtk.Button(label="Continue to Configure →")
        self.continue_btn.add_css_class("suggested-action")
        self.continue_btn.set_halign(Gtk.Align.END)
        self.continue_btn.connect("clicked", lambda _w: self.window.navigate("configure"))
        self.widget.append(self.continue_btn)

        self._update_repo_label()

    def on_show(self):
        if not self.rows:
            self.refresh()

    # ── checks ──────────────────────────────────────────────────────
    def refresh(self):
        self.recheck_btn.set_sensitive(False)
        self._log("Running checks…")

        def worker():
            # Re-discover the clone first so a folder deleted on disk since the
            # last run is reflected — otherwise check_repo() reports the stale
            # cached path as "Found" and the update check degrades to "offline?".
            fn.refresh_paths()
            results = hc.run_all()
            GLib.idle_add(self._populate, results)

        threading.Thread(target=worker, daemon=True).start()

    def _populate(self, results):
        child = self.listbox.get_first_child()
        while child:
            self.listbox.remove(child)
            child = self.listbox.get_first_child()
        self.rows.clear()
        current_section = None
        for r in results:
            section = r.get("section")
            if section and section != current_section:
                self.listbox.append(self._build_header(section))
                current_section = section
            self.listbox.append(self._build_row(r))
        self.recheck_btn.set_sensitive(True)
        n_fail = sum(1 for r in results if r["status"] == hc.FAIL)
        self.continue_btn.set_sensitive(n_fail == 0)
        self.fixall_btn.set_sensitive(any(r["fix"] for r in results))
        self._update_repo_label()

    def _update_repo_label(self):
        if fn.BUILD_SCRIPTS:
            self.repo_label.set_text(str(fn.BUILD_SCRIPTS.parent))
        else:
            target = fn.saved_repo_path() or fn.default_repo_dir()
            self.repo_label.set_text(f"not found — will clone to {target}")

    def _browse_repo(self):
        dialog = Gtk.FileDialog()
        dialog.set_title(f"Choose where {fn.REPO_NAME} lives (or should be cloned)")
        start = fn.saved_repo_path() or fn.default_repo_dir()
        parent = start.parent if not start.is_dir() else start
        dialog.set_initial_folder(Gio.File.new_for_path(str(parent)))
        dialog.select_folder(self.window, None, self._on_repo_chosen)

    def _on_repo_chosen(self, dialog, result):
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if not gfile:
            return
        target = fn.resolve_repo_dir(gfile.get_path())
        # Already a clone at the chosen spot → just point the app there.
        if (target / "build-scripts" / "build-the-iso.sh").is_file():
            self._set_repo_location(target, f"{fn.REPO_NAME} location set to {target}")
            return
        if target.exists():
            self._log(f"[error] {target} already exists but isn't a {fn.REPO_NAME} clone — "
                      "pick another folder.")
            return
        # Never move an existing repo — it may be a source checkout (on the dev
        # box, a Kiro-HQ folder). Just point the app at the chosen target;
        # Pre-flight clones a fresh copy there if one isn't already present.
        self._set_repo_location(target, f"{fn.REPO_NAME} location set to {target} (will clone here).")

    def _set_repo_location(self, repo, msg):
        fn.save_repo_path(repo)
        fn.refresh_paths()
        self._log(msg)
        self.refresh()

    def _build_header(self, text):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("section-header")
        label.set_margin_top(12)
        label.set_margin_bottom(4)
        label.set_margin_start(10)
        label.set_margin_end(10)
        row.set_child(label)
        return row

    def _build_row(self, r):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        icon = Gtk.Image.new_from_icon_name(_ICON[r["status"]])
        icon.add_css_class(f"status-{r['status']}")
        box.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = Gtk.Label(label=r["title"], xalign=0)
        title.add_css_class("row-title")
        detail = Gtk.Label(label=r["detail"], xalign=0, wrap=True)
        detail.add_css_class("dim-label")
        text.append(title)
        text.append(detail)
        box.append(text)

        if r["fix"]:
            btn = Gtk.Button(label="Fix")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda _w, key=r["key"]: self._fix_one(key))
            box.append(btn)
            self.rows[r["key"]] = {"row": row, "fix": r["fix"], "btn": btn}

        row.set_child(box)
        return row

    # ── fixes ───────────────────────────────────────────────────────
    def _fix_all(self):
        # Update and build-folder removal are deliberate, potentially-destructive
        # actions — never run them unattended inside the Fix-all batch.
        self._fix_queue = [k for k in self.rows if k not in ("uptodate", "leftover_build")]
        self._next_in_queue()

    def _next_in_queue(self):
        if not self._fix_queue:
            self.refresh()
            return
        self._fix_one(self._fix_queue.pop(0), chained=True)

    def _fix_one(self, key, chained=False):
        entry = self.rows.get(key)
        if not entry:
            self._next_in_queue() if chained else None
            return
        entry["btn"].set_sensitive(False)
        kind = entry["fix"][0]
        self._log(f"── Fixing {key} ──")

        def done(code):
            self._log(f"[{'ok' if code == 0 else 'failed'}] {key} (exit {code})")
            if chained:
                self._next_in_queue()
            else:
                self.refresh()

        if kind == "hostprep":
            fn.run_hostprep_fix(entry["fix"][1], self._log, done)
        elif kind == "unmount":
            fn.run_cleanup_mounts(self._log, done)
        elif kind == "update":
            self._update_repo(done)
        elif kind == "removebuild":
            self._remove_build_folder(done)
        elif kind == "clone":
            cmd = hc.clone_cmd()
            if cmd is None:
                self._log(f"[error] git not installed — cannot clone {fn.REPO_NAME}")
                done(1)
                return
            dest = cmd[-1]
            fn.run_pipe(cmd, self._log, lambda c: self._on_cloned(c, dest, done))
        else:
            done(0)

    def _on_cloned(self, code, dest, done):
        if code == 0:
            fn.save_repo_path(dest)   # persist so discovery finds it next launch
        fn.refresh_paths()
        done(code)

    # ── update the kiro-iso clone (git) ─────────────────────────────
    def _update_repo(self, done):
        repo = fn.repo_dir()
        if repo is None:
            done(1)
            return
        if not fn.repo_is_dirty(repo):
            self._log(f"Updating {fn.REPO_NAME} (fast-forward)…")
            fn.run_pipe(fn.git_pull_ff_argv(repo), self._log,
                        lambda c: self._after_update(c, done))
            return
        # Dirty tree (build byproducts) — confirm a reset that keeps the user's data.
        dlg = Gtk.AlertDialog()
        dlg.set_modal(True)
        dlg.set_message(f"Update {fn.REPO_NAME} to the latest?")
        dlg.set_detail("The clone has local changes from building. They'll be discarded and "
                       "the repo reset to the latest. Your package selection and build.conf "
                       "are kept.")
        dlg.set_buttons(["Cancel", "Discard & update"])
        dlg.set_default_button(1)
        dlg.set_cancel_button(0)
        dlg.choose(self.window, None, lambda d, r: self._on_update_confirmed(d, r, repo, done))

    def _on_update_confirmed(self, dlg, result, repo, done):
        try:
            idx = dlg.choose_finish(result)
        except GLib.Error:
            done(0)
            return
        if idx != 1:   # Cancel
            done(0)
            return
        self._log(f"Updating {fn.REPO_NAME} (resetting to latest, preserving your package selection)…")
        fn.run_pipe(fn.git_force_update_argv(repo, fn.package_selection_path()), self._log,
                    lambda c: self._after_update(c, done))

    def _after_update(self, code, done):
        self._log(f"{fn.REPO_NAME} updated." if code == 0 else f"[update failed] exit {code}")
        fn.refresh_paths()
        done(code)

    # ── confirm-gated removal of the (root-owned) build folder ──────
    def _remove_build_folder(self, done):
        if not fn.build_folder_present():
            done(0)
            return
        size = fn.build_folder_human_size()
        size_txt = f"  (~{size})" if size else ""
        dlg = Gtk.AlertDialog()
        dlg.set_modal(True)
        dlg.set_message("Remove the leftover build folder?")
        dlg.set_detail(f"{fn.build_folder()}{size_txt}\n\nIt's root-owned (mkarchiso ran as root), "
                       "so you can't delete it from your file manager. Keeping it is fine — the "
                       "next build reuses this location.")
        dlg.set_buttons(["Keep", "Remove"])
        dlg.set_default_button(0)   # safe default = Keep
        dlg.set_cancel_button(0)
        dlg.choose(self.window, None, lambda d, r: self._on_remove_build_confirmed(d, r, done))

    def _on_remove_build_confirmed(self, dlg, result, done):
        try:
            idx = dlg.choose_finish(result)
        except GLib.Error:
            done(0)
            return
        if idx != 1:   # Keep
            done(0)
            return
        self._log("── Removing build folder (with your approval) ──")
        fn.run_remove_build_folder(self._log, done)

    # ── log ─────────────────────────────────────────────────────────
    def _log(self, line):
        end = self.log_buf.get_end_iter()
        self.log_buf.insert(end, line + "\n")
