"""Safety tests: names from untrusted drives, destructive arguments, spreadsheets, previews."""

import contextlib
import csv
import io
import os
import tempfile
import unittest

from filerescue import cli, filesystems as fsm, gui
from filerescue.carver import Recovered
from filerescue.organize import write_csv


class NamesFromUntrustedDrives(unittest.TestCase):
    def test_traversal_is_neutralised(self):
        self.assertEqual(fsm.safe_path("../../etc/passwd"), ["etc", "passwd"])
        self.assertEqual(fsm.safe_path("a/../b"), ["a", "b"])
        self.assertEqual(fsm.safe_path("/abs/olute"), ["abs", "olute"])
        self.assertEqual(fsm.safe_path(""), ["unnamed"])
        self.assertEqual(fsm.safe_path("..\\..\\x"), ["_.._x"])

    def test_windows_device_names_and_bad_characters(self):
        for bad in ("CON", "nul.txt", "COM1", "lpt9.jpg", "AUX"):
            self.assertTrue(fsm.safe_name(bad).startswith("_"), bad)
        self.assertNotIn(":", fsm.safe_name("C:evil"))
        self.assertNotIn("\x00", fsm.safe_name("a\x00b"))
        self.assertEqual(fsm.safe_name("photo.jpg"), "photo.jpg")
        self.assertLessEqual(len(fsm.safe_name("x" * 500)), 150)

    def test_safe_join_refuses_escape(self):
        with tempfile.TemporaryDirectory() as d:
            ok = fsm.safe_join(d, ["a", "b.jpg"])
            self.assertEqual(os.path.dirname(os.path.dirname(os.path.realpath(ok))), os.path.realpath(d))
            with self.assertRaises(ValueError):
                fsm.safe_join(d, ["..", "outside.txt"])

    def test_safe_join_does_not_follow_a_planted_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks")
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            try:
                os.symlink(other, os.path.join(d, "link"))
            except OSError:
                self.skipTest("symlinks not permitted")
            with self.assertRaises(ValueError):
                fsm.safe_join(d, ["link", "x.jpg"])


class Spreadsheets(unittest.TestCase):
    def test_formulas_in_names_and_tags_are_defused(self):
        with tempfile.TemporaryDirectory() as d:
            r = Recovered(os.path.join(d, "photos", "a.jpg"), 0, 10, "jpg", True,
                          title='=HYPERLINK("http://evil","x")', artist="@SUM(A1)")
            os.makedirs(os.path.dirname(r.path))
            path = write_csv([r], d)
            with open(path, newline="", encoding="utf-8") as f:
                row = list(csv.reader(f))[1]
        self.assertTrue(row[6].startswith("'="))
        self.assertTrue(row[7].startswith("'@"))


class DestructiveArguments(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(args))
        return code, buf.getvalue()

    def test_image_refuses_to_overwrite_its_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "disk.img")
            with open(p, "wb") as f:
                f.write(b"precious" * 100)
            code, out = self.run_cli("image", p, p)
            self.assertEqual(code, 2)
            with open(p, "rb") as f:
                self.assertEqual(f.read(), b"precious" * 100)

    def test_image_refuses_existing_destination_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "a.img"), os.path.join(d, "b.img")
            for p, data in ((src, b"A" * 2048), (dst, b"B" * 2048)):
                with open(p, "wb") as f:
                    f.write(data)
            code, _ = self.run_cli("image", src, dst)
            self.assertEqual(code, 2)
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b"B" * 2048)
            code, _ = self.run_cli("image", src, dst, "--force")
            self.assertEqual(code, 0)
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), b"A" * 2048)

    @unittest.skipUnless(os.path.exists("/dev/null"), "needs /dev/null")
    def test_image_refuses_device_destination(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.img")
            with open(src, "wb") as f:
                f.write(b"A" * 1024)
            code, out = self.run_cli("image", src, "/dev/null")
            self.assertEqual(code, 2)
            self.assertIn("device", out)


class Gui(unittest.TestCase):
    def test_executables_are_never_opened(self):
        for name in ("evil.exe", "setup.apk", "run.bat", "x.js", "page.html", "m.scr", "a.lnk", "noext"):
            self.assertFalse(gui.can_open(name), name)
        for name in ("photo.JPG", "song.mp3", "clip.mp4", "doc.pdf"):
            self.assertTrue(gui.can_open(name), name)


if __name__ == "__main__":
    unittest.main()
