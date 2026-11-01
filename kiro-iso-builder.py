#!/usr/bin/env python3
"""kiro-iso-builder — a GTK4 front-end for building the Kiro ISO.

Runs as the normal user (never root). Privileged steps are elevated on demand:
pre-flight fixes via pkexec, the build by answering its own sudo prompt once.
The build pipeline itself stays in kiro-iso/build-scripts — this only drives it.
"""

# ── Force Python UTF-8 mode on a non-UTF-8 locale ─────────────────────────
# Never crash on a non-UTF-8 system locale (e.g. latin-1 fr_BE). Under such a
# locale Python encodes stdout and subprocess output with latin-1, so any
# non-ASCII glyph raises a Unicode error. UTF-8 mode forces UTF-8 regardless of
# LANG. Re-exec only when the locale's encoding is not UTF-8 — a normal UTF-8
# desktop is left untouched; the guard is loop-safe (the re-exec'd process is
# UTF-8 already).
import codecs
import os
import sys

if codecs.lookup(sys.getfilesystemencoding()).name != "utf-8":
    os.environ["PYTHONUTF8"] = "1"
    os.execv(sys.executable, [sys.executable, "-X", "utf8", *sys.argv])

import kib_i18n

# The build script we spawn inherits our locale; if it is not UTF-8 its output
# renders as mojibake in the Build log view. Keep the user's locale when it is
# already UTF-8, otherwise fall back to C.UTF-8 so child output stays readable.
_cur_locale = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or ""
if "utf-8" not in _cur_locale.lower() and "utf8" not in _cur_locale.lower():
    os.environ["LANG"] = "C.UTF-8"
    os.environ["LC_ALL"] = "C.UTF-8"

from pathlib import Path  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

import functions as fn  # noqa: E402
from build_gui import BuildScreen  # noqa: E402
from configure_gui import ConfigureScreen  # noqa: E402
from done_gui import DoneScreen  # noqa: E402
from extras_gui import ExtrasScreen  # noqa: E402
from packages_gui import PackagesScreen  # noqa: E402
from preflight_gui import PreflightScreen  # noqa: E402
from kib_i18n import _  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
APP_ID = "be.kiroproject.kiro-iso-builder"

# Sidebar order = wizard order.
SCREENS = [
    ("preflight", _("1 · Pre-flight"), PreflightScreen),
    ("configure", _("2 · Configure"), ConfigureScreen),
    ("packages", _("3 · Packages"), PackagesScreen),
    ("extras", _("4 · Add apps"), ExtrasScreen),
    ("build", _("5 · Build"), BuildScreen),
    ("done", _("6 · Done"), DoneScreen),
]

# Funding channels — GitHub Sponsors first (~100% payout). Keep in sync with
# kiro-website .github/FUNDING.yml and fish-tweak-tool's _FUNDING if those change.
_FUNDING = [
    ("GitHub Sponsors", "https://github.com/sponsors/erikdubois", _("Best value; almost all goes to the project")),
    ("Ko-fi", "https://ko-fi.com/erikdubois", _("Buy a coffee; one-off tip")),
    ("Patreon", "https://www.patreon.com/kiroproject", _("Membership tiers and perks")),
    ("YouTube membership", "https://www.youtube.com/@ErikDubois/join", _("Join on YouTube")),
    ("PayPal", "https://www.paypal.me/erikdubois", _("Direct one-off tip")),
]


def _open_url(parent, url):
    Gtk.UriLauncher.new(url).launch(parent, None, None)


def _show_support_dialog(window):
    dlg = Gtk.Window(title=_("Support Kiro"), transient_for=window, modal=True)
    dlg.set_default_size(440, -1)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for side in ("start", "end", "top", "bottom"):
        getattr(box, f"set_margin_{side}")(18)

    heading = Gtk.Label(xalign=0)
    heading.set_markup(f"<b>{_('Support Kiro')}</b>")
    box.append(heading)

    intro = Gtk.Label(xalign=0)
    intro.add_css_class("dim-label")
    intro.set_wrap(True)
    intro.set_max_width_chars(52)
    intro.set_label(
        _("Kiro and its tools are built by one person, for the community, and kept free. "
          "If Kiro ISO Builder saves you time, a little support keeps the work going. "
          "Thank you for being here.")
    )
    box.append(intro)

    for name, url, note in _FUNDING:
        btn = Gtk.Button()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{name}</b>")
        sub = Gtk.Label(label=note, xalign=0)
        sub.add_css_class("dim-label")
        content.append(label)
        content.append(sub)
        btn.set_child(content)
        btn.connect("clicked", lambda _w, u=url: _open_url(dlg, u))
        box.append(btn)

    close = Gtk.Button(label=_("Close"))
    close.set_halign(Gtk.Align.END)
    close.connect("clicked", lambda _w: dlg.close())
    box.append(close)

    dlg.set_child(box)
    dlg.present()


class BuilderWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kiro ISO Builder")
        self.set_default_size(870, 600)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        language_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.language_dropdown, self.restart_btn, self.language_note = self._language_controls()
        language_box.append(self.language_dropdown)
        language_box.append(self.restart_btn)
        support_btn = Gtk.Button(label=_("♥ Support"))
        support_btn.set_tooltip_text(_("Support Kiro's development"))
        support_btn.add_css_class("support-button")
        support_btn.connect("clicked", lambda _w: _show_support_dialog(self))
        language_box.append(support_btn)
        header.pack_end(language_box)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        self.language_note.set_margin_start(18)
        self.language_note.set_margin_end(18)
        self.language_note.set_margin_top(6)
        self.language_note.set_visible(False)
        outer.append(self.language_note)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        outer.append(body)

        self.stack = Gtk.Stack(hexpand=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self.stack)
        sidebar.set_size_request(180, -1)
        sidebar.add_css_class("sidebar")
        body.append(sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(self.stack)

        # Persistent footer with a Quit button in the bottom-right corner.
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(footer, m)(8)
        quit_btn = Gtk.Button(label=_("Quit"))
        quit_btn.set_hexpand(True)
        quit_btn.set_halign(Gtk.Align.END)
        quit_btn.connect("clicked", lambda _w: self.close())
        footer.append(quit_btn)
        outer.append(footer)

        self.screens = {}
        self.page_order = []        # stack child names in wizard order
        self.page_titles = {}       # stack child name -> sidebar title
        self.edition_pages = {}     # edition name -> its conditional page name
        for name, title, cls in SCREENS:
            self.screens[name] = cls(self)
            self.page_titles[name] = title
            self._add_page(name)
        self.sync_edition_pages()

        self.stack.connect("notify::visible-child-name", self._on_switch)
        # The first add_titled already made 'preflight' visible, so navigate()
        # emits no notify — fire on_show for the starting screen explicitly.
        self.navigate("preflight")
        self._on_switch()

    def _language_controls(self):
        codes = [None, *kib_i18n.available()]
        labels = [_("System ({language})").format(language=kib_i18n.name(kib_i18n.SYSTEM))]
        labels.extend(kib_i18n.name(code) for code in codes[1:])
        dropdown = Gtk.DropDown.new_from_strings(labels)
        dropdown.set_tooltip_text(_("Language"))
        dropdown.set_selected(codes.index(kib_i18n.chosen()))
        note = Gtk.Label(label=_("Saved. The app shows the new language when it starts again."), xalign=0)
        note.add_css_class("dim-label")
        restart = Gtk.Button(label=_("Restart now"), visible=False)

        def on_language_changed(_dropdown, _pspec):
            code = codes[dropdown.get_selected()]
            kib_i18n.save_choice(code)
            changed = (code or kib_i18n.SYSTEM) != (kib_i18n.ACTIVE or kib_i18n.SYSTEM)
            note.set_visible(changed)
            restart.set_visible(changed)

        def on_restart(_widget):
            # execv would orphan a running sudo build mid-way (mounts, half-written work dir)
            proc = getattr(self.screens.get("build"), "proc", None)
            if proc is not None and proc.poll() is None:
                note.set_label(_("A build is running. Restart after it finishes or after you stop it."))
                return
            argv = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
            kib_i18n.restore_environment()
            os.execv(sys.executable, argv)

        dropdown.connect("notify::selected", on_language_changed)
        restart.connect("clicked", on_restart)
        return dropdown, restart, note

    def _add_page(self, name, visible=True):
        page = self.stack.add_titled(self.screens[name].widget, name, self.page_titles[name])
        page.set_visible(visible)
        self.page_order.append(name)

    def sync_edition_pages(self):
        """Create a conditional extras page for every edition that has scoped EXTRA-APP
        blocks; each stays hidden until its edition is ticked (update_edition_pages).

        Re-runnable, and re-run whenever Configure loads: the edition list can only be
        read from packages.x86_64, so on a machine without the clone yet it is empty at
        startup and only becomes known mid-session, once Pre-flight has cloned or located
        the repo. Without this the page would appear on the next launch only.
        """
        editions = fn.edition_extras()
        if editions == sorted(self.edition_pages):
            return

        # Gtk.Stack can only append and the sidebar follows stack order, so lift out every
        # page after "Add apps" and put it back behind the edition pages to keep the wizard
        # order. Nothing here is ever the visible page when this runs, but restore it anyway.
        keep = self.stack.get_visible_child_name()
        first = self.page_order.index("extras") + 1
        moved = self.page_order[first:]
        was_visible = {n: self.stack.get_page(self.screens[n].widget).get_visible() for n in moved}
        fixed_tail = [n for n in moved if n not in self.edition_pages.values()]
        for name in moved:
            self.stack.remove(self.screens[name].widget)
        del self.page_order[first:]

        for ed in [e for e in self.edition_pages if e not in editions]:
            del self.screens[self.edition_pages.pop(ed)]
        for ed in editions:
            page_name = f"extras-{ed}"
            if ed not in self.edition_pages:
                self.screens[page_name] = ExtrasScreen(self, edition=ed)
                # Numbered "4 · …" like the fixed pages so the sidebar aligns; these
                # always slot in right after "4 · Add apps".
                self.page_titles[page_name] = _("{step} · {edition} extras").format(
                    step=4, edition=ed.capitalize())
                self.edition_pages[ed] = page_name
            self._add_page(page_name, visible=was_visible.get(page_name, False))
        for name in fixed_tail:
            self._add_page(name, visible=was_visible[name])

        if keep is not None:
            self.stack.set_visible_child_name(keep)

    def navigate(self, name):
        self.stack.set_visible_child_name(name)

    def navigate_relative(self, screen, delta):
        """Step from `screen` to the adjacent VISIBLE page (delta -1/+1). Used by the
        Extras pages, which are flanked by conditional per-edition pages."""
        cur = next((n for n, s in self.screens.items() if s is screen), None)
        order = [n for n in self.page_order
                 if self.stack.get_page(self.screens[n].widget).get_visible()]
        if cur in order:
            i = min(max(order.index(cur) + delta, 0), len(order) - 1)
            self.navigate(order[i])

    def update_edition_pages(self):
        """Show a per-edition extras page iff its edition is ticked on Configure; purge a
        page's keys from the additions overlay when it goes hidden (kept tidy)."""
        # Pick up editions the startup pass could not see yet (clone found mid-session).
        self.sync_edition_pages()
        cfg = self.screens.get("configure")
        ticked = {n for n, cb in cfg.edition_checks.items() if cb.get_active()} if cfg else set()
        for ed, page_name in self.edition_pages.items():
            page = self.stack.get_page(self.screens[page_name].widget)
            want = ed in ticked
            if page.get_visible() and not want:
                fn.reconcile_additions(fn.extra_app_keys(ed), set())
            page.set_visible(want)

    def _on_switch(self, *_a):
        name = self.stack.get_visible_child_name()
        screen = self.screens.get(name)
        if screen and hasattr(screen, "on_show"):
            screen.on_show()


class BuilderApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        # A second launch activates this running instance again: bring the open window forward and stop
        window = self.get_active_window()
        if window is not None:
            window.present()
            return
        fn.ensure_app_dirs()
        fn.ensure_build_conf()
        fn.normalize_build_location()
        self._load_css()
        if not self.get_windows():
            BuilderWindow(app).present()

    @staticmethod
    def _load_css():
        css = APP_DIR / "style.css"
        if not css.is_file():
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def main():
    print(f"[info] targeting {fn.REPO_NAME}")
    if fn.BUILD_SCRIPTS is None:
        print(f"[warn] {fn.REPO_NAME} clone not found — the Pre-flight screen can clone it.")
    app = BuilderApp()
    # Single-instance app: if a window is already open, registering reveals us as
    # the remote — say so instead of exiting silently with no new window.
    app.register(None)
    if app.get_is_remote():
        print("Kiro ISO Builder is already running — re-focusing that window. "
              "Close it first to launch a fresh instance.")
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
