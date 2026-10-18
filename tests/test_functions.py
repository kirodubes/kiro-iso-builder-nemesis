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
        self.assertEqual(fn.read_tier3(), [("Utilities", ["bat", "babl", "flameshot-git", "gimp"]),
                                           ("Fonts and groups", ["foo", "mygroup"])])

    def test_optional_sections_only_for_ticked_editions(self):
        self.assertEqual(fn.read_edition_optional(["xfce"]),
                         [("XFCE extras (xfce4-goodies)", ["mousepad", "ristretto"])])
        self.assertEqual(fn.read_edition_optional(["qtile"]), [])

    def test_package_rows_one_row_per_package(self):
        pf = fn.packages_file()
        pf.write_text(pf.read_text().replace("### Utilities\n", "### Utilities\nristretto\n", 1))
        rows = fn.package_rows(["xfce"])
        self.assertEqual(rows[0], ("XFCE extras (xfce4-goodies)", ["mousepad"]), "ristretto keeps its TIER 3 row")
        names = [p for _c, pkgs in rows for p in pkgs]
        self.assertEqual(len(names), len(set(names)))

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

    def test_always_shipped_rows(self):
        rows = [p for _c, ps in fn.read_edition_optional(self.editions) + fn.read_tier3() for p in ps]
        hidden = fn.always_shipped(rows, self.editions)
        self.assertEqual(hidden["bat"], "required by archlinux-tweak-tool", "core needs it")
        self.assertIn("flameshot-git", hidden, "kiro-qtile pulls the flameshot variant in")
        self.assertNotIn("gimp", hidden)
        self.assertNotIn("mousepad", hidden, "an optional edition package is removable")
        self.assertNotIn("flameshot-git", fn.always_shipped(rows, ["xfce"]), "without qtile it is removable")
        self.assertNotIn("babl", hidden, "only gimp, another row, brings it back")

    def test_reported_kept_skips_always_shipped(self):
        kept = fn.reported_kept({"bat", "babl"}, self.editions)
        self.assertEqual(kept, {"babl": "required by gimp"})

    # ── summary ──
    def test_summary_sections(self):
        s = fn.build_summary({"bat", "babl", "mousepad"}, self.editions)
        self.assertEqual(s["removed"], ["mousepad"])
        self.assertEqual(s["kept"], {"babl": "required by gimp"},
                         "bat always ships (core needs it), so a stale exclusion of it is not reported")
        self.assertEqual(s["frozen"], ["base", "linux"])
        self.assertIn("qtile", s["pulled_in"])
        self.assertIn("python-x", s["pulled_in"])
        self.assertNotIn("babl", s["pulled_in"], "babl is a row of its own, not a pulled-in dependency")
        self.assertNotIn("font-y", s["pulled_in"], "core packages are never 'pulled in'")
        self.assertEqual(s["blocks"]["xfce edition"], ["xfwm4", "thunar", "ristretto"])
        self.assertEqual(s["settings"]["Default session"], "xfce")

    def test_summary_html_is_escaped_and_complete(self):
        fn.set_conf("default_session", "<script>x</script>")
        s = fn.build_summary({"babl"}, self.editions)
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


class ForceUpdateKeepsChoices(unittest.TestCase):
    """The dirty-tree update resets the clone but keeps the selection and add-on files."""

    def test_reset_keeps_selection_and_additions(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            def git(*args, cwd):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                               check=True, capture_output=True)

            origin, clone = Path(tmp) / "origin", Path(tmp) / "clone"
            origin.mkdir()
            git("init", "-q", "-b", "main", cwd=origin)
            for name in ("package-selection.conf", "package-additions.conf", "other"):
                (origin / name).write_text("upstream\n")
            git("add", ".", cwd=origin)
            git("commit", "-qm", "one", cwd=origin)
            git("clone", "-q", str(origin), str(clone), cwd=tmp)
            (origin / "other").write_text("newer\n")
            git("commit", "-qam", "two", cwd=origin)
            for name in ("package-selection.conf", "package-additions.conf", "other"):
                (clone / name).write_text("mine\n")
            argv = fn.git_force_update_argv(clone, [clone / "package-selection.conf", clone / "package-additions.conf"])
            subprocess.run(argv, check=True, capture_output=True)
            self.assertEqual((clone / "package-selection.conf").read_text(), "mine\n")
            self.assertEqual((clone / "package-additions.conf").read_text(), "mine\n")
            self.assertEqual((clone / "other").read_text(), "newer\n", "everything else is reset to upstream")


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
        screen._hidden = {}
        self.assertEqual(packages_gui.PackagesScreen._excludes(screen), {"mousepad", "bat"})

    def test_hidden_rows_never_saved_as_unticked(self):
        import packages_gui

        class Row:
            def get_active(self):
                return False

        screen = type("S", (), {})()
        screen.checks = [("bat", Row()), ("gimp", Row())]
        screen._hidden = {"bat": "required by archlinux-tweak-tool"}
        self.assertEqual(packages_gui.PackagesScreen._excludes(screen), {"gimp"})


if __name__ == "__main__":
    unittest.main()
