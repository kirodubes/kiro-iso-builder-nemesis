"""Build screen — runs build-the-iso.sh under a PTY with live log + progress."""

import os
import re
import shutil
import signal
import time

import gi

import functions as fn

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

_PHASE_RE = re.compile(r"\bPhase\s+(\d+)\b")


class BuildScreen:
    def __init__(self, window):
        self.window = window
        self.proc = None
        self.send = None
        self._stopping = False
        self._log_dir = None
        self._log_fh = None
        self._t0 = None
        self.total_phases = fn.max_build_phase()

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.widget, m)(18)

        title = Gtk.Label(label="Build the ISO", xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)
        sub = Gtk.Label(
            label="Runs build-the-iso.sh as your user. You'll be asked for your password once; "
                  "if the build prompts (e.g. pacman's [Y/n]), type your reply in the box below.",
            xalign=0, wrap=True, max_width_chars=78)
        sub.add_css_class("dim-label")
        self.widget.append(sub)

        # Spell out exactly where this build's work dir and ISO land — refreshed
        # on every show since the build folder can change on the Pre-flight screen.
        self.paths = Gtk.Label(xalign=0, wrap=True, max_width_chars=78, selectable=True)
        self.paths.add_css_class("dim-label")
        self.widget.append(self.paths)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.start_btn = Gtk.Button(label="Start build")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", lambda _w: self._start())
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", lambda _w: self._stop())
        bar.append(self.start_btn)
        bar.append(self.stop_btn)
        self.widget.append(bar)

        self.progress = Gtk.ProgressBar(show_text=True, text="idle")
        self.widget.append(self.progress)

        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.log_buf = self.log_view.get_buffer()
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.log_view)
        self.widget.append(scroller)

        # Input row — answer prompts the build raises (e.g. pacman's [Y/n]).
        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.input = Gtk.Entry(hexpand=True, sensitive=False,
                               placeholder_text="Reply to a build prompt (e.g. y) and press Enter")
        self.input.connect("activate", lambda _w: self._send_input())
        self.send_btn = Gtk.Button(label="Send", sensitive=False)
        self.send_btn.connect("clicked", lambda _w: self._send_input())
        input_row.append(self.input)
        input_row.append(self.send_btn)
        self.widget.append(input_row)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        back = Gtk.Button(label="← Back")
        back.connect("clicked", lambda _w: self.window.navigate("packages"))
        nav.append(back)
        self.widget.append(nav)

    def on_show(self):
        self._show_paths()
        if fn.build_script() is None:
            self._log(f"{fn.REPO_NAME} clone not found — fix it on the Pre-flight screen.")
            self.start_btn.set_sensitive(False)

    def _show_paths(self):
        build, out = fn.build_folder(), fn.out_folder()
        if build and out:
            self.paths.set_markup(
                f"<b>Work dir:</b>  {GLib.markup_escape_text(str(build))}\n"
                f"<b>ISO output:</b>  {GLib.markup_escape_text(str(out))}")
        else:
            self.paths.set_text("Output location unknown — set the clone path on the Pre-flight screen.")

    def _start(self):
        script = fn.build_script()
        if script is None:
            return
        # Last gate before the build: pin build_location=local so the script writes
        # under the chosen folder, never $HOME — keeps the build and the Work-dir
        # label above it from ever disagreeing, whatever wrote build.conf.
        fn.normalize_build_location()
        self._stopping = False
        self._open_build_log()
        self.start_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)
        self._set_input_enabled(True)
        self.progress.set_fraction(0.0)
        self.progress.set_text("starting…")
        self._log("── Starting build ──")
        fn.run_in_pty(
            ["bash", str(script)], str(fn.BUILD_SCRIPTS),
            self._on_line, self._on_done,
            lambda prompt: fn.ask_password(self.window, prompt),
            on_start=self._on_proc,
        )

    def _on_proc(self, proc, send):
        self.proc = proc
        self.send = send

    def _set_input_enabled(self, on):
        self.input.set_sensitive(on)
        self.send_btn.set_sensitive(on)

    def _send_input(self):
        if not self.send:
            return
        text = self.input.get_text()
        self.send(text + "\n")
        self._log(f"> {text}")
        self.input.set_text("")

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self._stopping = True
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self.proc.terminate()
            self._log("── Stop requested ──")

    def _on_line(self, line):
        line = fn.strip_ansi(line)
        if self._log_fh:
            try:
                self._log_fh.write(line + "\n")
            except OSError:
                pass
        m = _PHASE_RE.search(line)
        if m:
            n = int(m.group(1))
            total = max(self.total_phases, n)
            self.progress.set_fraction(min(n / total, 1.0))
            self.progress.set_text(f"Phase {n} / {total}")
        self._log(line)

    def _on_done(self, code):
        self.proc = None
        self.send = None
        self.stop_btn.set_sensitive(False)
        self._set_input_enabled(False)
        self._finalize_build_log(code)
        if code == 0:
            self.progress.set_fraction(1.0)
            self.progress.set_text("done")
            self._log("── Build finished ──")
            self.start_btn.set_sensitive(True)
            self.window.navigate("done")
            return
        # Abnormal exit (user Stop or a build failure): mkarchiso may have left
        # bind-mounts live under the work dir — the thing that wedges the host.
        # Unmount them before re-arming Start. The user-side check first means a
        # clean failure raises no polkit prompt.
        verb = "stopped" if self._stopping else "failed"
        self._stopping = False
        self.progress.set_text(f"{verb} (exit {code})")
        self._log(f"── Build {verb} (exit {code}) ──")
        if fn.stale_mounts_present():
            self.progress.set_text("cleaning up mounts…")
            self._log("── Stale build mounts found — cleaning up ──")
            fn.run_cleanup_mounts(self._log, self._on_cleanup_done)
        else:
            self._maybe_offer_remove_build()

    def _on_cleanup_done(self, code):
        self._log(f"── Mount cleanup {'done' if code == 0 else f'exit {code}'} ──")
        self.progress.set_text("cleaned up" if code == 0 else f"cleanup exit {code}")
        self._maybe_offer_remove_build()

    # ── confirm-gated removal of the (root-owned) build folder ──────
    def _maybe_offer_remove_build(self):
        # Unmounting (above) is automatic and safe. Removing the build folder
        # deletes from the user's home, so it is NEVER automatic — ask first,
        # default to Keep, and show the exact path.
        if not fn.build_folder_present():
            self.start_btn.set_sensitive(True)
            return
        path = fn.build_folder()
        size = fn.build_folder_human_size()
        size_txt = f"  (~{size})" if size else ""
        dlg = Gtk.AlertDialog()
        dlg.set_modal(True)
        dlg.set_message("Remove the leftover build folder?")
        dlg.set_detail(f"{path}{size_txt}\n\nIt's root-owned (mkarchiso ran as root), so you "
                       "can't delete it from your file manager. Keeping it is fine — the next "
                       "build reuses this location.")
        dlg.set_buttons(["Keep", "Remove"])
        dlg.set_default_button(0)   # safe default = Keep
        dlg.set_cancel_button(0)
        dlg.choose(self.window, None, self._on_remove_confirmed)

    def _on_remove_confirmed(self, dlg, result):
        try:
            idx = dlg.choose_finish(result)
        except GLib.Error:
            self.start_btn.set_sensitive(True)
            return
        if idx != 1:   # Keep
            self.start_btn.set_sensitive(True)
            return
        self.progress.set_text("removing build folder…")
        self._log("── Removing build folder (with your approval) ──")
        fn.run_remove_build_folder(self._log, self._on_removed)

    def _on_removed(self, code):
        self._log("── Build folder removed ──" if code == 0 else f"── Remove failed (exit {code}) ──")
        self.progress.set_text("removed" if code == 0 else f"remove failed (exit {code})")
        self.start_btn.set_sensitive(True)

    # ── per-build log folder ────────────────────────────────────────
    def _open_build_log(self):
        self._t0 = time.time()
        try:
            self._log_dir = fn.new_build_log_dir()
            self._log_fh = open(self._log_dir / "build.log", "w")
        except OSError as exc:
            self._log_dir = None
            self._log_fh = None
            self._log(f"[warn] couldn't open build log: {exc}")

    def _finalize_build_log(self, code):
        if self._log_fh:
            try:
                self._log_fh.close()
            except OSError:
                pass
        self._log_fh = None
        log_dir = self._log_dir
        if not log_dir:
            return
        conf = fn.read_conf()
        # Snapshot the inputs: the settings used + the final, post-sed package list.
        bf = fn.build_folder()
        sources = [fn.build_conf_path(), fn.package_selection_path(),
                   (bf / "archiso" / "packages.x86_64") if bf else None]
        for src in sources:
            try:
                if src and src.is_file():
                    shutil.copy(src, log_dir / src.name)
            except OSError:
                pass
        self._write_summary(log_dir, conf, code)
        self._log(f"Build log saved to {log_dir}")

    def _write_summary(self, log_dir, conf, code):
        dur = int(time.time() - self._t0) if self._t0 else 0
        iso = self._latest_iso()
        lines = [
            f"Timestamp:      {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Result:         {'SUCCESS' if code == 0 else f'FAILED (exit {code})'}",
            f"Duration:       {dur // 60}m {dur % 60}s",
            f"Build folder:   {fn.build_base_dir()}",
            f"nvidia_driver:  {conf.get('nvidia_driver', '?')}",
            f"kernel:         {conf.get('kernel', '?')}",
            f"Host:           {self._host_pretty_name()}",
            f"archiso:        {fn.cmd_out(['pacman', '-Q', 'archiso']) or '?'}",
            f"ISO:            {iso.name if iso else '(none produced)'}",
            f"ISO size:       {f'{iso.stat().st_size / 1_000_000_000:.2f} GB' if iso else '-'}",
        ]
        if iso:
            for ext in ("sha256", "sha1", "md5"):
                f = iso.with_name(iso.name + "." + ext)
                if f.is_file():
                    lines.append(f"{ext}:         {f.read_text().split()[0]}")
        fn.write_build_summary(log_dir, lines)

    def _latest_iso(self):
        of = fn.out_folder()
        if not of or not of.is_dir():
            return None
        isos = sorted(of.glob("*.iso"), key=lambda p: p.stat().st_mtime, reverse=True)
        return isos[0] if isos else None

    @staticmethod
    def _host_pretty_name():
        try:
            for line in open("/etc/os-release", encoding="utf-8"):
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "?"

    def _log(self, line):
        end = self.log_buf.get_end_iter()
        self.log_buf.insert(end, line + "\n")
        mark = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
        self.log_view.scroll_mark_onscreen(mark)
