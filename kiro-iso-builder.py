#!/usr/bin/env python3
"""kiro-iso-builder — a GTK4 front-end for building the Kiro ISO.

Runs as the normal user (never root). Privileged steps are elevated on demand:
pre-flight fixes via pkexec, the build by answering its own sudo prompt once.
The build pipeline itself stays in kiro-iso/build-scripts — this only drives it.
"""

import sys
from pathlib import Path

import gi

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

APP_DIR = Path(__file__).resolve().parent
APP_ID = "be.kiroproject.kiro-iso-builder"

# Sidebar order = wizard order.
SCREENS = [
    ("preflight", "1 · Pre-flight", PreflightScreen),
    ("configure", "2 · Configure", ConfigureScreen),
    ("packages", "3 · Packages", PackagesScreen),
    ("extras", "4 · Add apps", ExtrasScreen),
    ("build", "5 · Build", BuildScreen),
    ("done", "6 · Done", DoneScreen),
]


class BuilderWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kiro ISO Builder")
        self.set_default_size(870, 600)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)

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
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.set_hexpand(True)
        quit_btn.set_halign(Gtk.Align.END)
        quit_btn.connect("clicked", lambda _w: self.close())
        footer.append(quit_btn)
        outer.append(footer)

        self.screens = {}
        self.page_order = []        # stack child names in wizard order
        self.edition_pages = {}     # edition name -> its conditional page name
        for name, title, cls in SCREENS:
            screen = cls(self)
            self.screens[name] = screen
            self.stack.add_titled(screen.widget, name, title)
            self.page_order.append(name)
            # Conditional per-edition extras pages slot in right after "Add apps".
            # Each is hidden until its edition is ticked (update_edition_pages).
            if name == "extras":
                for ed in fn.edition_extras():
                    page_name = f"extras-{ed}"
                    escreen = ExtrasScreen(self, edition=ed)
                    self.screens[page_name] = escreen
                    # Numbered "4 · …" like the fixed pages so the sidebar aligns; these
                    # always slot in right after "4 · Add apps".
                    page = self.stack.add_titled(escreen.widget, page_name, f"4 · {ed.capitalize()} extras")
                    page.set_visible(False)
                    self.page_order.append(page_name)
                    self.edition_pages[ed] = page_name

        self.stack.connect("notify::visible-child-name", self._on_switch)
        # The first add_titled already made 'preflight' visible, so navigate()
        # emits no notify — fire on_show for the starting screen explicitly.
        self.navigate("preflight")
        self._on_switch()

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
