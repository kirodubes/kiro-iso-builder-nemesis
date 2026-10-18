"""Unit tests for the package logic in functions.py (no pacman, no network, no GUI)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
import functions as fn  # noqa: E402


class PackageLogic(unittest.TestCase):
    def setUp(self):
        self.tmp, bs = fixture.make_repo()
        fixture.install(fn, bs)
        self.editions = ["xfce", "qtile"]

    def tearDown(self):
        self.tmp.cleanup()

    # ── parsers ──
    def test_tiers_split_at_tier2_and_stop_at_tier3(self):
        tier1, tier2 = fn.read_tiers()
        self.assertEqual(tier1, ["base", "linux"])
        self.assertEqual(tier2, ["archlinux-tweak-tool", "font-y"])
        self.assertEqual(fn.read_core(), tier1 + tier2)

    def test_tier3_categories(self):
        self.assertEqual(fn.read_tier3(), [("Utilities", ["bat", "flameshot-git", "gimp"]),
                                           ("Fonts and groups", ["foo", "mygroup"])])

    def test_optional_sections_only_for_ticked_editions(self):
        self.assertEqual(fn.read_edition_optional(["xfce"]),
                         [("XFCE extras (xfce4-goodies)", ["mousepad", "ristretto"])])
        self.assertEqual(fn.read_edition_optional(["qtile"]), [])

    def test_shipped_names_reasons(self):
        names = fn._shipped_names({"gimp", "mousepad"}, self.editions)
        self.assertNotIn("gimp", names)
        self.assertNotIn("mousepad", names, "an unticked optional edition package must not ship")
        self.assertEqual(names["ristretto"], "listed by the xfce edition")
        self.assertEqual(names["kiro-qtile"], "listed by the qtile edition")
        self.assertNotIn("krita", names, "an add-on that isn't ticked must not ship")

    def test_ticked_addon_ships(self):
        fn.write_additions({"krita"})
        self.assertEqual(fn._shipped_names(set(), [])["krita"], "part of the krita add-on")

    # ── kept_packages ──
    def test_kept_reasons(self):
        kept = fn.kept_packages({"bat", "gimp", "tool-a", "flameshot-git", "mousepad", "thunar"}, self.editions)
        self.assertEqual(kept["bat"], "required by archlinux-tweak-tool")
        self.assertEqual(kept["tool-a"], "part of the mygroup group")
        self.assertEqual(kept["flameshot-git"], "flameshot ships instead (required by kiro-qtile)")
        self.assertEqual(kept["thunar"], "listed by the xfce edition", "fixed edition lines still ship")
        self.assertNotIn("gimp", kept)
        self.assertNotIn("mousepad", kept)

    def test_nothing_excluded_nothing_kept(self):
        self.assertEqual(fn.kept_packages(set(), self.editions), {})

    def test_selected_provider_satisfies_virtual_dependency(self):
        parent = fn._resolve(fn._shipped_names(set(), self.editions), set(), fn._SYNC_DB, fn._GROUPS)
        self.assertIn("font-y", parent)
        self.assertNotIn("font-x", parent, "font-y already provides ttf-font")

    def test_real_package_wins_over_group(self):
        fn._GROUPS["bat"] = "bat"
        parent = fn._resolve({"bat": ""}, set(), fn._SYNC_DB, fn._GROUPS)
        self.assertEqual(parent["bat"], "")

    # ── summary ──
    def test_summary_sections(self):
        s = fn.build_summary({"bat", "gimp", "mousepad"}, self.editions)
        self.assertEqual(s["removed"], ["gimp", "mousepad"])
        self.assertEqual(s["kept"], {"bat": "required by archlinux-tweak-tool"})
        self.assertEqual(s["frozen"], ["base", "linux"])
        self.assertIn("qtile", s["pulled_in"])
        self.assertIn("python-x", s["pulled_in"])
        self.assertNotIn("babl", s["pulled_in"], "gimp is removed, so its dependency must not appear")
        self.assertNotIn("font-y", s["pulled_in"], "core packages are never 'pulled in'")
        self.assertEqual(s["blocks"]["xfce edition"], ["xfwm4", "thunar", "ristretto"])
        self.assertEqual(s["settings"]["Default session"], "xfce")

    def test_summary_html_is_escaped_and_complete(self):
        fn.set_conf("default_session", "<script>x</script>")
        s = fn.build_summary({"bat"}, self.editions)
        path = fn.write_summary_html(s, Path(self.tmp.name) / "out" / "summary.html")
        text = path.read_text()
        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertNotIn("<script>x</script>", text)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", text)
        for heading in ("Removed (", "Comes back anyway (1)", "Pulled in by your choices", "Frozen, TIER 1 (2)"):
            self.assertIn(heading, text)

    # ── selection file ──
    def test_exclude_file_round_trip(self):
        fn.write_excludes({"gimp", "mousepad"})
        self.assertEqual(fn.read_excludes(), {"gimp", "mousepad"})
        self.assertTrue(fn.package_selection_path().read_text().startswith("# Optional packages to EXCLUDE"))


class PackagesScreenExcludes(unittest.TestCase):
    """_excludes() keeps saved exclusions for rows that aren't on screen."""

    def setUp(self):
        self.tmp, bs = fixture.make_repo()
        fixture.install(fn, bs)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hidden_exclusions_survive(self):
        import packages_gui

        class Row:
            def __init__(self, active):
                self.active = active

            def get_active(self):
                return self.active

        fn.write_excludes({"mousepad", "gimp"})
        screen = type("S", (), {})()
        # xfce unticked on Configure: mousepad has no row, gimp is shown and ticked again, bat unticked.
        screen.checks = [("gimp", Row(True)), ("bat", Row(False))]
        self.assertEqual(packages_gui.PackagesScreen._excludes(screen), {"mousepad", "bat"})


if __name__ == "__main__":
    unittest.main()
