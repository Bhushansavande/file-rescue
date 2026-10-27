"""Build synthetic disk images and check recovery: python -m unittest discover -s tests"""

import hashlib
import io
import os
import random
import struct
import tempfile
import unittest
import zipfile
import zlib
from datetime import datetime

from filerescue import android, recyclebin
from filerescue.carver import carve


def make_jpeg(rng, size=3000, with_thumb=True):
    """A JPEG with a valid marker structure; the APP1 segment deliberately contains a
    thumbnail with its own FFD9 so truncation bugs show up."""
    thumb = b"\xff\xd8\xff\xdb\x00\x04\x00\x00\xff\xd9" if with_thumb else b""
    app1 = b"Exif\x00\x00" + thumb
    scan = bytearray()
    while len(scan) < size:
        b = rng.randrange(256)
        scan.append(b)
        if b == 0xFF:
            scan.append(0x00)  # byte stuffing
    return (b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xdb\x00\x04\x00\x00" + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
            + bytes(scan) + b"\xff\xd9")


def make_png(rng):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rng.randrange(256) for _ in range(30)) for _ in range(10))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_mp4(rng):
    ftyp = struct.pack(">I", 20) + b"ftypisom" + b"\x00\x00\x00\x00" + b"isom"
    mdat = struct.pack(">I", 8 + 4000) + b"mdat" + bytes(rng.randrange(256) for _ in range(4000))
    moov = struct.pack(">I", 8 + 60) + b"moov" + bytes(60)
    return ftyp + mdat + moov


def make_mp3(rng, frames=40):
    frame = b"\xff\xfb\x90\x00" + bytes(rng.randrange(256) for _ in range(413))  # 417 bytes
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + frame * frames


def make_pdf(rng):
    return b"%PDF-1.4\n" + bytes(rng.randrange(32, 120) for _ in range(2500)) + b"\n%%EOF\n"


def make_zip(name, extra=b"x"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, extra * 500)
        z.writestr("classes.dex", b"dex\n035\x00" * 100)
    return buf.getvalue()


def sha(b):
    return hashlib.sha256(b).hexdigest()


class CarveTest(unittest.TestCase):
    def build_image(self, files, rng):
        img = bytearray()
        for data in files:
            img += bytes(rng.randrange(256) for _ in range(512 * rng.randrange(1, 6)))
            img += data
            img += b"\x00" * ((-len(img)) % 512)
        img += bytes(rng.randrange(256) for _ in range(2048))
        return bytes(img)

    def test_recovers_all_types_exactly(self):
        rng = random.Random(7)
        originals = {
            "jpg": make_jpeg(rng),
            "png": make_png(rng),
            "mp4": make_mp4(rng),
            "mp3": make_mp3(rng),
            "pdf": make_pdf(rng),
            "apk": make_zip("AndroidManifest.xml"),
            "docx": make_zip("[Content_Types].xml") ,
        }
        # a docx needs a word/ entry
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("[Content_Types].xml", "<x/>")
            z.writestr("word/document.xml", "<w/>")
        originals["docx"] = buf.getvalue()

        order = ["jpg", "png", "mp4", "mp3", "pdf", "apk", "docx", "jpg"]
        files = [originals[k] for k in order]
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "disk.img")
            with open(src, "wb") as fh:
                fh.write(self.build_image(files, rng))
            res = carve(src, os.path.join(tmp, "out"))
            got = {}
            for r in res:
                with open(r.path, "rb") as fh:
                    got.setdefault(r.ext, []).append(sha(fh.read()))
                self.assertTrue(r.complete, r.path)
            for ext, data in originals.items():
                self.assertIn(sha(data), got.get(ext, []), f"{ext} was not recovered")
            self.assertEqual(len(res), len(order))
            # no empty leftover folders (e.g. "archives" after APK/DOCX detection)
            for entry in os.listdir(os.path.join(tmp, "out")):
                self.assertTrue(os.listdir(os.path.join(tmp, "out", entry)), entry)

    def test_jpeg_thumbnail_does_not_truncate(self):
        rng = random.Random(3)
        j = make_jpeg(rng, size=5000, with_thumb=True)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "d.img")
            with open(src, "wb") as fh:
                fh.write(self.build_image([j], rng))
            res = carve(src, os.path.join(tmp, "o"), types=["jpg"])
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0].size, len(j))

    def test_type_filter(self):
        rng = random.Random(5)
        files = [make_png(rng), make_pdf(rng)]
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "d.img")
            with open(src, "wb") as fh:
                fh.write(self.build_image(files, rng))
            res = carve(src, os.path.join(tmp, "o"), types=["pdf"])
            self.assertEqual([r.ext for r in res], ["pdf"])

    def test_truncated_file_saved_as_partial(self):
        rng = random.Random(9)
        png = make_png(rng)[:-20]  # the end was overwritten
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "d.img")
            with open(src, "wb") as fh:
                fh.write(self.build_image([png], rng))
            res = carve(src, os.path.join(tmp, "o"), types=["png"])
            self.assertEqual(len(res), 1)
            self.assertFalse(res[0].complete)
            self.assertIn("_partial", res[0].path)
            res2 = carve(src, os.path.join(tmp, "o2"), types=["png"], keep_partial=False)
            self.assertEqual(res2, [])


class RecycleBinTest(unittest.TestCase):
    def test_parse_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            binp = os.path.join(tmp, "bin")
            os.makedirs(binp)
            orig = "C:\\Users\\Me\\Pictures\\trip.jpg"
            raw = orig.encode("utf-16-le") + b"\x00\x00"
            ft = int((datetime(2026, 10, 1, 12, 0) - datetime(1601, 1, 1)).total_seconds() * 10_000_000)
            info = (struct.pack("<QQQ", 2, 1234, ft) + struct.pack("<I", len(raw) // 2) + raw)
            with open(os.path.join(binp, "$IABC123.jpg"), "wb") as fh:
                fh.write(info)
            with open(os.path.join(binp, "$RABC123.jpg"), "wb") as fh:
                fh.write(b"photo-bytes")
            items = recyclebin.list_items(binp)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].original_path, orig)
            self.assertEqual(items[0].size, 1234)
            self.assertEqual(items[0].deleted_at.year, 2026)
            done = recyclebin.restore(items, os.path.join(tmp, "out"))
            self.assertEqual(os.path.basename(done[0]), "trip.jpg")
            with open(done[0], "rb") as fh:
                self.assertEqual(fh.read(), b"photo-bytes")


class AndroidNameTest(unittest.TestCase):
    def test_trashed_name(self):
        name, exp = android.parse_trashed_name(".trashed-1735689600-IMG_2024.jpg")
        self.assertEqual(name, "IMG_2024.jpg")
        self.assertIsNotNone(exp)
        name, exp = android.parse_trashed_name(".hidden.mp4")
        self.assertEqual(name, "hidden.mp4")
        self.assertIsNone(exp)


if __name__ == "__main__":
    unittest.main()
