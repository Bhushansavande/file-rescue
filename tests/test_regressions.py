"""Regression tests for bugs found while testing against real media and real file systems.

Every test here corresponds to a failure that was reproduced and fixed.
"""

import contextlib
import hashlib
import io
import os
import random
import shutil
import stat
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

from filerescue import android, carver, cli, recyclebin
from filerescue.carver import carve, scan
from filerescue.signatures import select_signatures

from test_filerescue import make_mp3, make_mp4, make_png


def sha(b):
    return hashlib.sha256(b).hexdigest()


def rnd(rng, n):
    return bytes(rng.randrange(256) for _ in range(n))


def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)


def zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", compression) as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return b.getvalue()


class CarveBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rng = random.Random(1234)

    def image(self, data, pad=True):
        """Random noise, then `data` on a sector boundary, then more noise."""
        img = bytearray(rnd(self.rng, 1024))
        img += data
        if pad:
            img += b"\0" * ((-len(img)) % 512)
        return bytes(img)

    def carve_bytes(self, img, **kw):
        src = os.path.join(self.tmp, "src.img")
        with open(src, "wb") as fh:
            fh.write(img)
        out = os.path.join(self.tmp, "out")
        shutil.rmtree(out, ignore_errors=True)
        return carve(src, out, **kw)

    def assert_recovered(self, data, res):
        for r in res:
            with open(r.path, "rb") as fh:
                if sha(fh.read()) == sha(data):
                    self.assertTrue(r.complete)
                    return
        self.fail(f"original ({len(data)} bytes) not recovered exactly; got "
                  f"{[(r.ext, r.size, r.complete) for r in res]}")


class FormatEndDetection(CarveBase):
    def test_gif_with_trailer_bytes_inside_image_data(self):
        # 00 3B inside LZW data used to be mistaken for the GIF trailer
        gif = (b"GIF89a" + struct.pack("<HHBBB", 4, 4, 0x80, 0, 0) + b"\0\0\0\xff\xff\xff"
               + b"\x2c" + struct.pack("<HHHHB", 0, 0, 4, 4, 0) + b"\x02"
               + b"\x06" + b"ab\x00;cd" + b"\x00" + b"\x3b")
        self.assert_recovered(gif, self.carve_bytes(self.image(gif)))

    def test_real_animated_gif_structure(self):
        # extension blocks + several frames
        def frame(data):
            return (b"\x21\xf9\x04\x00\x0a\x00\x00\x00" + b"\x2c" + struct.pack("<HHHHB", 0, 0, 2, 2, 0)
                    + b"\x02" + bytes([len(data)]) + data + b"\x00")
        gif = (b"GIF89a" + struct.pack("<HHBBB", 2, 2, 0x80, 0, 0) + b"\0\0\0\xff\xff\xff"
               + b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
               + frame(b"\x00;\x00;\x00;") + frame(rnd(self.rng, 40)) + b"\x3b")
        self.assert_recovered(gif, self.carve_bytes(self.image(gif)))

    def test_png_with_iend_bytes_inside_another_chunk(self):
        raw = b"".join(b"\x00" + rnd(self.rng, 30) for _ in range(10))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 8, 0, 0, 0, 0))
               + chunk(b"tEXt", b"k\x00IEND\xaeB`\x82tail") + chunk(b"IDAT", zlib.compress(raw))
               + chunk(b"IEND", b""))
        self.assert_recovered(png, self.carve_bytes(self.image(png)))

    def test_apk_containing_a_stored_zip(self):
        # the nested archive has its own end record; the first one found is the wrong one
        inner = zip_bytes({"in.txt": b"inner" * 100})
        apk = zip_bytes({"AndroidManifest.xml": b"m", "assets/nested.zip": inner}, zipfile.ZIP_STORED)
        res = self.carve_bytes(self.image(apk))
        self.assert_recovered(apk, res)
        self.assertEqual([r.ext for r in res], ["apk"])

    def test_pdf_with_incremental_update(self):
        body1 = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nxref\n0 1\ntrailer\n<<>>\nstartxref\n9\n%%EOF\n"
        body2 = b"2 0 obj\n<<>>\nendobj\nxref\n0 2\ntrailer\n<<>>\nstartxref\n99\n%%EOF\n"
        pdf = body1 + body2
        self.assert_recovered(pdf, self.carve_bytes(self.image(pdf)))

    def test_pdf_does_not_swallow_the_next_file(self):
        pdf = b"%PDF-1.4\n" + rnd(self.rng, 300).replace(b"%", b"x") + b"\n%%EOF\n"
        res = self.carve_bytes(self.image(pdf) + make_png(self.rng))
        self.assertEqual(sorted(r.ext for r in res), ["pdf", "png"])
        self.assert_recovered(pdf, res)

    def test_mp4_followed_by_printable_noise_does_not_overshoot(self):
        mp4 = make_mp4(self.rng)
        noise = struct.pack(">I", 700) + b"abcd" + b"x" * 2000
        self.assert_recovered(mp4, self.carve_bytes(self.image(mp4, pad=False) + noise))


class TruncationAndLimits(CarveBase):
    def test_cut_off_mp3_and_mp4_are_reported_partial(self):
        for data in (make_mp3(self.rng, frames=60), make_mp4(self.rng)):
            res = self.carve_bytes(self.image(data[: len(data) // 2], pad=False))
            self.assertTrue(res)
            self.assertTrue(all(not r.complete for r in res), [(r.ext, r.complete) for r in res])
            self.assertTrue(all("_partial" in os.path.basename(r.path) for r in res))

    def test_complete_file_at_very_end_of_image(self):
        for data in (make_mp3(self.rng), make_mp4(self.rng), make_png(self.rng)):
            self.assert_recovered(data, self.carve_bytes(self.image(data, pad=False)))

    def test_partial_files_are_capped(self):
        old = carver.PARTIAL_CAP
        carver.PARTIAL_CAP = 2048
        self.addCleanup(setattr, carver, "PARTIAL_CAP", old)
        # a JPEG header followed by junk without any end: a false hit or a destroyed file
        fake = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + rnd(self.rng, 50000)
        res = self.carve_bytes(self.image(fake))
        self.assertTrue(res)
        self.assertTrue(all(r.size <= 2048 and not r.complete for r in res))

    def test_good_file_after_a_damaged_one(self):
        good = make_png(self.rng)
        mp4 = make_mp4(self.rng)
        damaged = mp4[:1000] + rnd(self.rng, 500) + mp4[1500:]
        img = self.image(damaged) + good + b"\0" * 1024
        self.assert_recovered(good, self.carve_bytes(img))

    def test_unaligned_file_needs_no_align_flag(self):
        png = make_png(self.rng)
        img = rnd(self.rng, 1000) + png + rnd(self.rng, 3000)
        self.assertEqual(self.carve_bytes(img), [])
        self.assert_recovered(png, self.carve_bytes(img, aligned=False))

    def test_tiny_and_empty_sources(self):
        for size in (0, 1, 100, 511, 512, 513):
            self.assertEqual(self.carve_bytes(rnd(self.rng, size)), [], size)

    def test_signature_split_across_scan_chunks(self):
        png = make_png(self.rng)
        for shift in (0, 512, 1024, 4096 - 512):
            data = b"\0" * (4096 * 3 + shift) + png + b"\0" * 2048
            path = os.path.join(self.tmp, "chunk.img")
            with open(path, "wb") as fh:
                fh.write(data)
            with open(path, "rb", buffering=0) as fh:
                hits = scan(fh, select_signatures(None), chunk=4096)
            self.assertIn(4096 * 3 + shift, [o for o, _ in hits], shift)

    def test_no_empty_output_folders_after_apk_detection(self):
        apk = zip_bytes({"AndroidManifest.xml": b"m", "classes.dex": b"d" * 100})
        res = self.carve_bytes(self.image(apk))
        out = os.path.join(self.tmp, "out")
        self.assertEqual([r.ext for r in res], ["apk"])
        self.assertEqual(os.listdir(out), ["apps"])


class TypeFilter(CarveBase):
    """--types must return only what was asked for, even when kinds share a container format."""

    def setUp(self):
        super().setUp()
        self.files = {
            "png": make_png(self.rng),
            "mp3": make_mp3(self.rng),
            "mp4": make_mp4(self.rng),
            "apk": zip_bytes({"AndroidManifest.xml": b"m", "classes.dex": b"d" * 100}),
            "docx": zip_bytes({"[Content_Types].xml": b"<t/>", "word/document.xml": b"<w/>"}),
        }
        self.img = b"".join(self.image(d) for d in self.files.values())

    def exts(self, *types):
        return sorted(r.ext for r in self.carve_bytes(self.img, types=list(types)))

    def test_everything_without_a_filter(self):
        self.assertEqual(sorted(r.ext for r in self.carve_bytes(self.img)),
                         ["apk", "docx", "mp3", "mp4", "png"])

    def test_groups_return_only_their_own_kinds(self):
        self.assertEqual(self.exts("photos"), ["png"])
        self.assertEqual(self.exts("music"), ["mp3"])      # the MP4 shares M4A's container
        self.assertEqual(self.exts("videos"), ["mp4"])
        self.assertEqual(self.exts("apps"), ["apk"])
        self.assertEqual(self.exts("documents"), ["docx"])

    def test_single_types_and_combinations(self):
        self.assertEqual(self.exts("apk"), ["apk"])
        self.assertEqual(self.exts("docx"), ["docx"])
        self.assertEqual(self.exts("mp4"), ["mp4"])
        self.assertEqual(self.exts("png", "music"), ["mp3", "png"])

    def test_unwanted_files_leave_nothing_behind(self):
        self.carve_bytes(self.img, types=["apps"])
        out = os.path.join(self.tmp, "out")
        left = [os.path.join(d, f) for d, _, fs in os.walk(out) for f in fs]
        self.assertEqual([os.path.basename(p).rsplit(".", 1)[1] for p in left], ["apk"])
        self.assertEqual(os.listdir(out), ["apps"])

    def test_unknown_type_is_an_error(self):
        with self.assertRaises(ValueError):
            self.carve_bytes(self.img, types=["bogus"])


class CommandLine(CarveBase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(args))
        return code, buf.getvalue()

    def test_types(self):
        code, out = self.run_cli("types")
        self.assertEqual(code, 0)
        self.assertIn("JPEG photo", out)

    def test_carve_end_to_end(self):
        png = make_png(self.rng)
        src = os.path.join(self.tmp, "d.img")
        with open(src, "wb") as fh:
            fh.write(self.image(png))
        out = os.path.join(self.tmp, "o")
        code, text = self.run_cli("carve", src, "-o", out, "--types", "photos")
        self.assertEqual(code, 0)
        self.assertIn("1 complete", text)
        self.assertEqual(len(os.listdir(os.path.join(out, "photos"))), 1)

    def test_carve_rejects_unknown_type_and_missing_source(self):
        src = os.path.join(self.tmp, "d.img")
        with open(src, "wb") as fh:
            fh.write(b"\0" * 1024)
        self.assertEqual(self.run_cli("carve", src, "-o", self.tmp + "/o", "--types", "bogus")[0], 2)
        self.assertEqual(self.run_cli("carve", self.tmp + "/nope.img", "-o", self.tmp + "/o")[0], 2)

    def test_image_command_makes_identical_copy(self):
        data = rnd(self.rng, 3 * 1024 * 1024 + 123)
        src = os.path.join(self.tmp, "a.bin")
        dst = os.path.join(self.tmp, "b.bin")
        with open(src, "wb") as fh:
            fh.write(data)
        self.assertEqual(self.run_cli("image", src, dst)[0], 0)
        with open(dst, "rb") as fh:
            self.assertEqual(fh.read(), data)


class RecycleBinFormats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def filetime(self, y):
        from datetime import datetime
        return int((datetime(y, 1, 1) - datetime(1601, 1, 1)).total_seconds() * 10_000_000)

    def test_version1_info_file_and_folder_restore(self):
        orig = "C:\\Users\\Me\\Music\\song.mp3"
        raw = orig.encode("utf-16-le")
        info = struct.pack("<QQQ", 1, 99, self.filetime(2020)) + raw + b"\0" * (520 - len(raw))
        with open(os.path.join(self.tmp, "$IAAA111.mp3"), "wb") as fh:
            fh.write(info)
        with open(os.path.join(self.tmp, "$RAAA111.mp3"), "wb") as fh:
            fh.write(b"mp3data")
        # a deleted folder
        folder = "C:\\Users\\Me\\Old"
        raw2 = folder.encode("utf-16-le") + b"\0\0"
        info2 = struct.pack("<QQQ", 2, 0, self.filetime(2021)) + struct.pack("<I", len(raw2) // 2) + raw2
        with open(os.path.join(self.tmp, "$IBBB222"), "wb") as fh:
            fh.write(info2)
        os.makedirs(os.path.join(self.tmp, "$RBBB222"))
        with open(os.path.join(self.tmp, "$RBBB222", "note.txt"), "wb") as fh:
            fh.write(b"hello")
        items = recyclebin.list_items(self.tmp)
        self.assertEqual({i.original_path for i in items}, {orig, folder})
        self.assertEqual(items[0].deleted_at.year, 2021)   # newest first
        done = recyclebin.restore(items, os.path.join(self.tmp, "out"))
        names = sorted(os.path.basename(p) for p in done)
        self.assertEqual(names, ["Old", "song.mp3"])
        with open(os.path.join(self.tmp, "out", "Old", "note.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"hello")

    def test_missing_data_and_corrupt_info_are_handled(self):
        with open(os.path.join(self.tmp, "$ICCC333.jpg"), "wb") as fh:
            fh.write(b"too short")
        info = struct.pack("<QQQ", 2, 5, self.filetime(2022)) + struct.pack("<I", 3) + "a.b".encode("utf-16-le")
        with open(os.path.join(self.tmp, "$IDDD444.jpg"), "wb") as fh:
            fh.write(info)
        items = recyclebin.list_items(self.tmp)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].data_path, "")
        self.assertEqual(recyclebin.restore(items, os.path.join(self.tmp, "out")), [])

    def test_name_collisions_get_a_suffix(self):
        for tag in ("E1", "E2"):
            raw = "C:\\same.jpg".encode("utf-16-le") + b"\0\0"
            info = struct.pack("<QQQ", 2, 3, self.filetime(2023)) + struct.pack("<I", len(raw) // 2) + raw
            with open(os.path.join(self.tmp, f"$I{tag}.jpg"), "wb") as fh:
                fh.write(info)
            with open(os.path.join(self.tmp, f"$R{tag}.jpg"), "wb") as fh:
                fh.write(tag.encode())
        done = recyclebin.restore(recyclebin.list_items(self.tmp), os.path.join(self.tmp, "out"))
        self.assertEqual(sorted(os.path.basename(p) for p in done), ["same.jpg", "same_1.jpg"])


@unittest.skipIf(os.name == "nt", "uses a fake POSIX shell script as adb")
class AndroidWithFakeAdb(unittest.TestCase):
    """A fake `adb` lets us test the whole ADB flow without a phone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.phone = os.path.join(self.tmp, "phone")
        os.makedirs(os.path.join(self.phone, "DCIM", "Camera"))
        for name, data in ((".trashed-1893456000-IMG_1.jpg", b"jpg1"),
                           (".trashed-1893456000-IMG_2.jpg", b"jpg2")):
            with open(os.path.join(self.phone, "DCIM", "Camera", name), "wb") as fh:
                fh.write(data)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        script = os.path.join(self.bin, "adb")
        with open(script, "w") as fh:
            fh.write(f"""#!/bin/sh
PHONE="{self.phone}"
case "$1" in
  devices) printf 'List of devices attached\\nABC123\\tdevice\\nXYZ\\tunauthorized\\n\\n' ;;
  shell) (cd "$PHONE" && find . -type f -name '.trashed-*' | sed 's#^\\.#/sdcard#') ;;
  pull) src="$PHONE$(echo "$2" | sed 's#^/sdcard##')"; cp "$src" "$3" ;;
esac
""")
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = self.bin + os.pathsep + self.old_path
        self.addCleanup(os.environ.__setitem__, "PATH", self.old_path)

    def test_device_listing_find_and_pull(self):
        ok, msg = android.check_device()
        self.assertTrue(ok, msg)
        self.assertIn("ABC123", msg)
        paths = android.find_trashed()
        self.assertEqual(len(paths), 2)
        out = os.path.join(self.tmp, "out")
        pulled = android.pull_all(paths, out)
        self.assertEqual(sorted(os.path.basename(p) for p in pulled), ["IMG_1.jpg", "IMG_2.jpg"])
        with open(os.path.join(out, "IMG_1.jpg"), "rb") as fh:
            self.assertEqual(fh.read(), b"jpg1")

    def test_cli_android(self):
        out = os.path.join(self.tmp, "cliout")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["android", "-o", out])
        self.assertEqual(code, 0)
        self.assertIn("IMG_1.jpg", buf.getvalue())
        self.assertEqual(sorted(os.listdir(out)), ["IMG_1.jpg", "IMG_2.jpg"])

    def test_classify(self):
        c = android.classify
        self.assertEqual(c("/sdcard/DCIM/Camera/.trashed-1893456000-a.jpg"), "trash")
        self.assertEqual(c("/sdcard/Android/data/x/files/.Trash/a.jpg"), "trash")
        self.assertEqual(c("/sdcard/WhatsApp/Media/.Statuses/v.mp4"), "status")
        self.assertEqual(c("/sdcard/Android/media/com.whatsapp/WhatsApp/Media/.Statuses/a.jpg"), "status")
        self.assertEqual(c("/sdcard/DCIM/.thumbnails/123"), "thumbnails")
        self.assertIsNone(c("/sdcard/DCIM/Camera/IMG_1.jpg"))

    def test_local_mode_finds_and_copies_all_three_kinds(self):
        for rel, data in ((".Statuses/s1.mp4", b"status"), (".thumbnails/99", b"thumb"),
                          ("Pictures/normal.jpg", b"normal")):
            p = os.path.join(self.phone, "WhatsApp", "Media", rel) if rel.startswith(".S") \
                else os.path.join(self.phone, "DCIM", rel) if rel.startswith(".t") \
                else os.path.join(self.phone, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
        out = os.path.join(self.tmp, "local_out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["android", "--local", "--root", self.phone, "-o", out])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertEqual(sorted(os.listdir(out)), ["IMG_1.jpg", "IMG_2.jpg", "thumbnails", "whatsapp-status"])
        self.assertEqual(os.listdir(os.path.join(out, "whatsapp-status")), ["s1.mp4"])
        self.assertNotIn("normal.jpg", buf.getvalue())

    def test_local_mode_kind_filter_and_bad_kind(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["android", "--local", "--root", self.phone, "--kinds", "status"]), 0)
            self.assertEqual(cli.main(["android", "--kinds", "bogus"]), 2)
        self.assertIn("Nothing found", buf.getvalue())

    def test_no_adb_gives_a_helpful_message(self):
        os.environ["PATH"] = os.path.join(self.tmp, "empty")
        ok, msg = android.check_device()
        self.assertFalse(ok)
        self.assertIn("platform-tools", msg)


if __name__ == "__main__":
    unittest.main()
