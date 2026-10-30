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


def make_flv(tags=6):
    out = b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00"
    for i in range(tags):
        body = os.urandom(200 + i)
        out += bytes([9 if i % 2 else 8]) + len(body).to_bytes(3, "big") + b"\0\0\0\0\0\0\0" + body
        out += (11 + len(body)).to_bytes(4, "big")
    return out


def make_adts(frames=40):
    out = b""
    for _ in range(frames):
        n = 7 + 180
        out += bytes([0xFF, 0xF1, 0x50, 0x80 | (n >> 11), (n >> 3) & 0xFF, ((n & 7) << 5) | 0x1F, 0xFC])
        out += os.urandom(n - 7)
    return out


def make_mp3raw(frames=30):
    frame = b"\xff\xfb\x90\x00" + os.urandom(413)          # 128 kbit/s, 44.1 kHz: 417 bytes
    return frame * frames


def make_flac(frames=8):
    from filerescue.formats import _crc8, _crc16
    info = bytearray(34)
    info[0:2] = struct.pack(">H", 4096)
    info[2:4] = struct.pack(">H", 4096)
    info[4:7] = (300).to_bytes(3, "big")
    info[7:10] = (600).to_bytes(3, "big")
    info[10:13] = bytes([0x0A, 0xC4, 0x42])
    info[13:18] = (4096 * frames).to_bytes(5, "big")
    out = b"fLaC" + b"\x80\x00\x00\x22" + bytes(info)
    for n in range(frames):
        hdr = bytes([0xFF, 0xF8, 0xC9, 0x18, n])
        hdr += bytes([_crc8(hdr)])
        body = hdr + os.urandom(300 + 20 * n)
        out += body + struct.pack(">H", _crc16(body))
    return out


def make_ole(kind="WordDocument", sectors=12):
    """Smallest useful OLE2 file: header, one FAT sector, one directory sector, data sectors."""
    ss = 512
    hdr = bytearray(512)
    hdr[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    hdr[24:28] = b"\x3e\x00\x03\x00"
    hdr[28:30] = b"\xfe\xff"
    hdr[30:32] = struct.pack("<H", 9)
    hdr[32:34] = struct.pack("<H", 6)
    hdr[44:48] = struct.pack("<I", 1)          # one FAT sector
    hdr[48:52] = struct.pack("<I", 1)          # directory starts at sector 1
    hdr[56:60] = struct.pack("<I", 4096)
    hdr[60:64] = struct.pack("<I", 0xFFFFFFFE)
    hdr[68:72] = struct.pack("<I", 0xFFFFFFFE)
    hdr[76:80] = struct.pack("<I", 0)          # DIFAT[0] = sector 0
    hdr[80:512] = b"\xff" * 432
    fat = [0xFFFFFFFD, 0xFFFFFFFE] + [i + 3 for i in range(sectors - 3)] + [0xFFFFFFFE]
    fat += [0xFFFFFFFF] * (128 - len(fat))
    fat_sec = b"".join(struct.pack("<I", v) for v in fat)
    d = bytearray(512)
    name = "Root Entry".encode("utf-16-le") + b"\0\0"
    d[0:len(name)] = name
    d[64:66] = struct.pack("<H", len(name))
    d[66] = 5
    name2 = kind.encode("utf-16-le") + b"\0\0"
    d[128:128 + len(name2)] = name2
    d[192:194] = struct.pack("<H", len(name2))
    d[194] = 2
    d[128 + 116:128 + 120] = struct.pack("<I", 2)
    data = os.urandom(ss * (sectors - 2))
    return bytes(hdr) + fat_sec + bytes(d) + data


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
            out = []
            for r in res:
                with open(r.path, "rb") as fh:
                    out.append((r.ext, r.complete, fh.read()))
            return out

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

    def test_mkv_live_recording_with_unknown_size_cluster(self):
        head = make_mkv(unknown=True)
        children = (b"\xe7\x81\x00" + b"\xa3" + vint(300, 2) + os.urandom(300)) * 3
        blob = head + b"\x1f\x43\xb6\x75\x01\xff\xff\xff\xff\xff\xff\xff" + children
        got = self.carve_bytes(b"\0" * 512 * 3 + blob + b"\0" * 4096)
        self.assertEqual([(e, c, b) for e, c, b in got], [("mkv", True, blob)])

    def test_flv(self):
        self.check(make_flv(), "flv")

    def test_aac_adts(self):
        self.check(make_adts(), "aac")

    def test_mp3_without_id3(self):
        self.check(make_mp3raw(), "mp3")

    def test_flac(self):
        self.check(make_flac(), "flac")

    def test_old_office_kinds(self):
        self.check(make_ole("WordDocument"), "doc")
        self.check(make_ole("Workbook"), "xls")
        self.check(make_ole("PowerPoint Document"), "ppt")

    def test_short_runs_of_mp3_or_aac_frames_are_not_files(self):
        junk = make_mp3raw(4) + os.urandom(900) + make_adts(5)
        self.assertEqual(self.carve_bytes(disk_with([junk])), [])

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
