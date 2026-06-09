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
        for name, title, cls in SCREENS:
            screen = cls(self)
            self.screens[name] = screen
            self.stack.add_titled(screen.widget, name, title)

        self.stack.connect("notify::visible-child-name", self._on_switch)
        # The first add_titled already made 'preflight' visible, so navigate()
        # emits no notify — fire on_show for the starting screen explicitly.
        self.navigate("preflight")
        self._on_switch()

    def navigate(self, name):
        self.stack.set_visible_child_name(name)

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
    return BuilderApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
