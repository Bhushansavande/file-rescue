"""Tests for dates, duplicate removal, renaming and the HTML report."""

import os
import random
import struct
import tempfile
import unittest
from datetime import datetime

from filerescue.carver import carve
from filerescue.metadata import exif_date, id3_info, read_info
from filerescue.organize import organize, write_report
from tests.test_filerescue import make_jpeg, make_mp3


RNG = random.Random(5)


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def exif_block(when=b"2023:07:14 15:30:12\x00"):
    """A little-endian TIFF block: IFD0 -> ExifIFD -> DateTimeOriginal."""
    ifd0_off, exif_off = 8, 8 + 2 + 12 + 4
    date_off = exif_off + 2 + 12 + 4
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_off) + b"\0\0\0\0"
    exif = struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(when), date_off) + b"\0\0\0\0"
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + exif + when


def jpeg_with_exif(when=b"2023:07:14 15:30:12\x00", pad=0):
    base = make_jpeg(RNG, size=3000 + pad)
    tiff = exif_block(when)
    app1 = b"Exif\x00\x00" + tiff
    return base[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + base[2:]


def id3_mp3(title, artist):
    def frame(fid, text):
        body = b"\x03" + text.encode()
        return fid + struct.pack(">I", len(body)) + b"\0\0" + body
    frames = frame(b"TIT2", title) + frame(b"TPE1", artist)
    size = len(frames)
    sync = bytes([(size >> 21) & 127, (size >> 14) & 127, (size >> 7) & 127, size & 127])
    return b"ID3\x03\x00\x00" + sync + frames


class Metadata(unittest.TestCase):
    def test_exif_date(self):
        self.assertEqual(exif_date(exif_block()), datetime(2023, 7, 14, 15, 30, 12))

    def test_exif_garbage_and_old_dates_are_ignored(self):
        self.assertIsNone(exif_date(exif_block(b"0000:00:00 00:00:00\x00")))
        self.assertIsNone(exif_date(b"not a tiff block"))

    def test_jpeg_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.jpg")
            write(p, jpeg_with_exif())
            self.assertEqual(read_info(p, "jpg")["date"], datetime(2023, 7, 14, 15, 30, 12))

    def test_id3(self):
        info = id3_info(id3_mp3("Tum Hi Ho", "Arijit") + b"\0" * 20)
        self.assertEqual((info["title"], info["artist"]), ("Tum Hi Ho", "Arijit"))

    def test_damaged_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.jpg")
            write(p, b"\xff\xd8\xff\xe1\x00\x05Ex")
            self.assertIsNone(read_info(p, "jpg")["date"])


class Organize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        a = jpeg_with_exif()
        b = jpeg_with_exif(b"2020:01:02 03:04:05\x00", pad=7)
        undated = make_jpeg(RNG, size=2500)
        parts = [os.urandom(1024)]
        for blob in (a, a, b, undated, id3_mp3("Song", "Band") + make_mp3(RNG)[10:]):
            parts += [blob, b"\0" * ((-len(blob)) % 512), os.urandom(1024)]
        self.src = os.path.join(self.tmp.name, "d.img")
        write(self.src, b"".join(parts))
        self.out = os.path.join(self.tmp.name, "out")

    def test_dedupe_names_and_months(self):
        res = carve(self.src, self.out)
        kept, removed = organize(res, self.out, dedupe=True, date_names=True, by_month=True)
        self.assertEqual(removed, 1)
        rel = sorted(os.path.relpath(r.path, self.out).replace(os.sep, "/") for r in kept)
        self.assertTrue(any(r.startswith("photos/2020-01/2020-01-02_030405_") for r in rel))
        photos = [r for r in rel if r.startswith("photos/")]
        self.assertEqual(sum(1 for r in photos if "/2023-07/" in r), 1)
        self.assertEqual(sum(1 for r in photos if "/2020-01/" in r), 1)
        self.assertEqual(sum(1 for r in photos if "/undated/" in r), 1)
        self.assertTrue(all(os.path.exists(r.path) for r in kept))
        self.assertTrue(any(r.endswith("Band - Song.mp3") for r in rel))

    def test_nothing_changes_without_flags(self):
        res = carve(self.src, self.out)
        names = sorted(os.path.basename(r.path) for r in res)
        kept, removed = organize(res, self.out)
        self.assertEqual((len(kept), removed), (len(res), 0))
        self.assertEqual(sorted(os.path.basename(r.path) for r in kept), names)

    def test_report_files(self):
        res = carve(self.src, self.out)
        kept, _ = organize(res, self.out)
        path = write_report(kept, self.out, "d.img")
        page = read_text(path)
        self.assertIn("<img", page)
        self.assertIn("<audio", page)
        for r in kept:
            self.assertIn(os.path.basename(r.path), page)
        csv_text = read_text(os.path.join(self.out, "recovered.csv"))
        self.assertEqual(len(csv_text.strip().splitlines()), len(kept) + 1)

    def test_report_escapes_names(self):
        res = carve(self.src, self.out)
        res[0].title = "<script>alert(1)</script>"
        res[0].artist = ""
        page = read_text(write_report(res, self.out, "<x>"))
        self.assertNotIn("<script>alert(1)", page)


if __name__ == "__main__":
    unittest.main()
