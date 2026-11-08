"""Joining files that are stored in two pieces (JPEG, MP4)."""

import io
import os
import random
import shutil
import struct
import tempfile
import unittest

from filerescue import fragments
from filerescue.carver import carve

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover
    Image = None

C = 4096


def photo(seed, w=640, h=480):
    r = random.Random(seed)
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(int(255 * y / h), int(120 + 100 * y / h), 200 - int(150 * y / h)))
    for _ in range(25):
        x, y, rad = r.randrange(w), r.randrange(h), r.randrange(20, 90)
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=tuple(r.randrange(256) for _ in range(3)))
    b = io.BytesIO()
    im.filter(ImageFilter.GaussianBlur(3)).save(b, "JPEG", quality=85)
    return b.getvalue()


def pad(b):
    return b + os.urandom((-len(b)) % C)


def box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def fake_mp4(rng, samples=80):
    """A small MP4 with real box structure: ftyp, free, mdat of length-prefixed video samples, moov."""
    ftyp = box(b"ftyp", b"isom\0\0\2\0isomiso2avc1mp41")
    free = box(b"free", b"")
    sizes, body = [], b""
    for _ in range(samples):
        n = rng.randrange(1500, 6000)
        nal = b"\x41" + bytes(rng.randrange(256) for _ in range(n - 1))
        body += struct.pack(">I", n) + nal
        sizes.append(n + 4)
    mdat_start = len(ftyp) + len(free) + 8
    mdat = box(b"mdat", body)
    per_chunk = 10
    chunk_offsets, pos = [], mdat_start
    for i in range(0, samples, per_chunk):
        chunk_offsets.append(pos)
        pos += sum(sizes[i:i + per_chunk])
    full = lambda payload: b"\0\0\0\0" + payload  # noqa: E731 - version + flags
    stbl = box(b"stbl",
               box(b"stsz", full(struct.pack(">II", 0, samples) + b"".join(struct.pack(">I", s) for s in sizes)))
               + box(b"stsc", full(struct.pack(">IIII", 1, 1, per_chunk, 1)))
               + box(b"stco", full(struct.pack(">I", len(chunk_offsets)) + b"".join(struct.pack(">I", o) for o in chunk_offsets))))
    hdlr = box(b"hdlr", full(b"\0\0\0\0vide" + b"\0" * 12 + b"video\0"))
    trak = box(b"trak", box(b"mdia", hdlr + box(b"minf", stbl)))
    mvhd = box(b"mvhd", b"\0" * 100)
    return ftyp + free + mdat + box(b"moov", mvhd + trak)


@unittest.skipIf(Image is None, "Pillow not installed")
class JoinJpeg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def disk(self, parts):
        p = os.path.join(self.tmp, "disk.img")
        with open(p, "wb") as f:
            f.write(b"".join(parts))
        return p

    def test_photo_in_two_pieces_is_rebuilt(self):
        j = photo(1)
        k = max(1, len(j) // C // 2)
        f1, f2 = j[:k * C], j[k * C:]
        disk = self.disk([os.urandom(C * 4), f1, pad(photo(2)), os.urandom(C * 30), pad(f2), os.urandom(C * 8)])
        res = carve(disk, os.path.join(self.tmp, "out"), types=["jpg"], fragments=True, cluster=C)
        joined = [r for r in res if r.joined]
        self.assertEqual(len(joined), 1)
        with open(joined[0].path, "rb") as fh:
            self.assertEqual(fh.read(), j)
        self.assertTrue(joined[0].complete)

    def test_without_the_option_it_stays_partial(self):
        j = photo(1)
        k = max(1, len(j) // C // 2)
        disk = self.disk([os.urandom(C * 4), j[:k * C], pad(photo(2)), os.urandom(C * 30), pad(j[k * C:])])
        res = carve(disk, os.path.join(self.tmp, "out"), types=["jpg"])
        self.assertTrue(any(not r.complete for r in res))
        self.assertFalse(any(r.joined for r in res))

    def test_unrelated_pieces_are_not_joined(self):
        j = photo(1)
        k = max(1, len(j) // C // 2)
        # the real second piece is missing; only the end of a different photo exists
        other = photo(7)
        disk = self.disk([os.urandom(C * 4), j[:k * C], os.urandom(C * 12), pad(other[len(other) // 2:]),
                          os.urandom(C * 4)])
        res = carve(disk, os.path.join(self.tmp, "out"), types=["jpg"], fragments=True, cluster=C)
        for r in res:
            if r.joined:
                with open(r.path, "rb") as fh:
                    self.assertNotEqual(fh.read()[:len(j) // 2], j[:len(j) // 2])

    def test_jpeg_walker_stops_at_a_second_start_marker(self):
        j = photo(3)
        data = j[:len(j) // 2] + photo(4)
        disk = self.disk([os.urandom(1024), data, os.urandom(2048)])
        res = carve(disk, os.path.join(self.tmp, "out"), types=["jpg"])
        self.assertFalse(any(r.complete and r.size > len(j) for r in res))


class JoinMp4(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_mdat_and_moov_in_two_pieces(self):
        m = fake_mp4(random.Random(5))
        k = (len(m) // C) // 3
        p = os.path.join(self.tmp, "disk.img")
        with open(p, "wb") as f:
            f.write(os.urandom(C * 3) + m[:k * C] + os.urandom(C * 25) + pad(m[k * C:]) + os.urandom(C * 4))
        res = carve(p, os.path.join(self.tmp, "out"), types=["mp4"], fragments=True, cluster=C)
        joined = [r for r in res if r.joined]
        self.assertEqual(len(joined), 1, [(r.ext, r.size, r.complete) for r in res])
        with open(joined[0].path, "rb") as fh:
            self.assertEqual(fh.read(), m)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_real_ffmpeg_video(self):
        import subprocess
        src = os.path.join(self.tmp, "v.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "libx264", "-c:a", "aac",
                        "-pix_fmt", "yuv420p", src], check=True)
        with open(src, "rb") as fh:
            m = fh.read()
        k = (len(m) // C) // 3
        p = os.path.join(self.tmp, "disk.img")
        with open(p, "wb") as f:
            f.write(os.urandom(C * 3) + m[:k * C] + os.urandom(C * 20) + pad(m[k * C:]) + os.urandom(C * 4))
        res = carve(p, os.path.join(self.tmp, "out"), types=["mp4"], fragments=True, cluster=C)
        joined = [r for r in res if r.joined]
        self.assertEqual(len(joined), 1)
        with open(joined[0].path, "rb") as fh:
            self.assertEqual(fh.read(), m)

    def test_wrong_piece_is_not_joined(self):
        m = fake_mp4(random.Random(5))
        k = (len(m) // C) // 3
        p = os.path.join(self.tmp, "disk.img")
        with open(p, "wb") as f:
            f.write(os.urandom(C * 3) + m[:k * C] + os.urandom(C * 40))
        res = carve(p, os.path.join(self.tmp, "out"), types=["mp4"], fragments=True, cluster=C)
        self.assertFalse(any(r.joined for r in res))


if __name__ == "__main__":
    unittest.main()
