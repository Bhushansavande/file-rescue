"""Tests for the container formats added in 0.2 (built from synthetic structures)."""

import os
import struct
import tempfile
import unittest
import zlib

from filerescue.carver import carve
from filerescue.signatures import allowed_extensions


def ogg_page(serial, seq, flags, body):
    segs = []
    n = len(body)
    while n >= 255:
        segs.append(255)
        n -= 255
    segs.append(n)
    hdr = b"OggS\x00" + bytes([flags]) + b"\0" * 8 + struct.pack("<III", serial, seq, 0) \
        + bytes([len(segs)]) + bytes(segs)
    return hdr + body


def make_opus():
    return (ogg_page(7, 0, 2, b"OpusHead\x01\x01" + b"\0" * 9)
            + ogg_page(7, 1, 0, b"OpusTags" + b"x" * 300)
            + ogg_page(7, 2, 4, os.urandom(1000)))


def vint(n, length):
    return bytes([(0x80 >> (length - 1)) | (n >> (8 * (length - 1)))]) + \
        n.to_bytes(length, "big")[1:] if length > 1 else bytes([0x80 | n])


def make_mkv(doctype=b"matroska", unknown=False):
    ebml_body = b"\x42\x82" + vint(len(doctype), 1) + doctype
    head = b"\x1a\x45\xdf\xa3" + vint(len(ebml_body), 1) + ebml_body
    inner = b"\xe7\x81\x00" + b"\xa3" + vint(1990, 2) + os.urandom(1990)
    cluster = b"\x1f\x43\xb6\x75" + vint(len(inner), 2) + inner
    if unknown:
        return head + b"\x18\x53\x80\x67\x01\xff\xff\xff\xff\xff\xff\xff" + cluster + cluster
    body = cluster * 3
    return head + b"\x18\x53\x80\x67" + b"\x01" + len(body).to_bytes(7, "big") + body


def make_sqlite(pages=5, page=4096):
    hdr = bytearray(100)
    hdr[:16] = b"SQLite format 3\x00"
    hdr[16:18] = struct.pack(">H", page)
    hdr[18:20] = b"\x01\x01"
    hdr[21:24] = bytes([64, 32, 32])
    hdr[24:28] = hdr[92:96] = struct.pack(">I", 3)
    hdr[28:32] = struct.pack(">I", pages)
    return bytes(hdr) + os.urandom(page * pages - 100)


def make_bmp(w=40, h=30):
    row = (w * 3 + 3) // 4 * 4
    pix = os.urandom(row * h)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pix), 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 14 + 40 + len(pix), 0, 0, 54) + dib + pix


def make_7z(payload=3000):
    nxt = os.urandom(60)
    start = struct.pack("<QQI", payload, len(nxt), zlib.crc32(nxt))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start \
        + os.urandom(payload) + nxt


def make_tiff(make=b"NIKON CORPORATION\0"):
    strip = os.urandom(5000)
    # layout: header(8) | IFD at 8 | make string | strip data
    n = 4
    ifd_len = 2 + n * 12 + 4
    make_off = 8 + ifd_len
    strip_off = make_off + len(make)
    entries = [(0x10F, 2, len(make), make_off), (0x111, 4, 1, strip_off),
               (0x116, 4, 1, 100), (0x117, 4, 1, len(strip))]
    ifd = struct.pack("<H", n) + b"".join(struct.pack("<HHII", *e) for e in entries) + b"\0\0\0\0"
    return b"II*\x00" + struct.pack("<I", 8) + ifd + make + strip


def make_heic():
    ftyp = struct.pack(">I", 24) + b"ftypheic" + b"\0\0\0\0" + b"mif1heic"
    meta = struct.pack(">I", 40) + b"meta" + b"\0" * 32
    mdat = struct.pack(">I", 3008) + b"mdat" + os.urandom(3000)
    return ftyp + meta + mdat


def disk_with(files):
    img = bytearray(os.urandom(512 * 7))
    for f in files:
        img += f + os.urandom((-len(f)) % 512) + os.urandom(512 * 3)
    return bytes(img)


class NewFormats(unittest.TestCase):
    def carve_bytes(self, data, **kw):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "disk.img")
            with open(src, "wb") as fh:
                fh.write(data)
            res = carve(src, os.path.join(d, "out"), **kw)
            return [(r.ext, r.complete, open(r.path, "rb").read()) for r in res]

    def check(self, blob, ext):
        got = self.carve_bytes(disk_with([blob]))
        self.assertEqual([(e, c, b) for e, c, b in got], [(ext, True, blob)])

    def test_opus(self):
        self.check(make_opus(), "opus")

    def test_ogg_vorbis_extension(self):
        blob = (ogg_page(3, 0, 2, b"\x01vorbis" + b"\0" * 20)
                + ogg_page(3, 1, 4, os.urandom(500)))
        self.check(blob, "ogg")

    def test_mkv(self):
        self.check(make_mkv(), "mkv")

    def test_webm(self):
        self.check(make_mkv(b"webm"), "webm")

    def test_mkv_unknown_size_segment(self):
        self.check(make_mkv(unknown=True), "mkv")

    def test_sqlite(self):
        self.check(make_sqlite(), "sqlite")

    def test_bmp(self):
        self.check(make_bmp(), "bmp")

    def test_7z(self):
        self.check(make_7z(), "7z")

    def test_tiff_and_nikon_raw(self):
        self.check(make_tiff(b"Canon\0"), "tif")
        self.check(make_tiff(), "nef")

    def test_heic(self):
        self.check(make_heic(), "heic")

    def test_truncated_ogg_is_partial(self):
        blob = make_opus()
        blob = blob[:blob.index(b"OpusTags") + 308]   # the last page never reached the disk
        got = self.carve_bytes(disk_with([blob]))
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0][1])

    def test_short_header_false_hits_are_dropped(self):
        junk = b"BM" + os.urandom(600) + b"II*\x00" + os.urandom(600)
        self.assertEqual(self.carve_bytes(disk_with([junk])), [])

    def test_types_filter(self):
        data = disk_with([make_opus(), make_bmp(), make_sqlite()])
        self.assertEqual([e for e, _, _ in self.carve_bytes(data, types=["music"])], ["opus"])
        self.assertEqual([e for e, _, _ in self.carve_bytes(data, types=["db"])], ["sqlite"])
        self.assertEqual([e for e, _, _ in self.carve_bytes(data, types=["photos"])], ["bmp"])

    def test_type_names(self):
        self.assertEqual(allowed_extensions(["raw"]), {"cr2", "dng", "nef", "arw", "tif"})


if __name__ == "__main__":
    unittest.main()
