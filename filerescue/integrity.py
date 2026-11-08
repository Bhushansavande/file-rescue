"""Check a recovered file and say why it looks damaged ("suspect"), or return "" if it looks fine.

Checks that need nothing but the standard library: PNG chunk checksums, ZIP/Office/APK member
checksums, MP4 structure, PDF end marker, long runs of zero bytes (overwritten or never written
data). If Pillow is installed, photos are also decoded completely, which catches JPEG data that
is cut off or corrupted in the middle, and a grey or empty lower part of the picture.

A file that passes is not guaranteed perfect; a "suspect" mark is a hint to look at it first.
"""

import os
import struct
import zipfile
import zlib

ZERO_RUN = 64 * 1024          # a run this long inside media is almost never real content
READ_BLOCK = 1 << 20


def _zero_runs(path):
    """Length in bytes of the longest run of zero bytes in the file."""
    import re
    longest = run = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(READ_BLOCK)
            if not b:
                break
            lead = len(b) - len(b.lstrip(b"\0"))
            if lead == len(b):
                run += len(b)
            else:
                longest = max(longest, run + lead)
                inner = max((len(m) for m in re.findall(rb"\x00{4096,}", b)), default=0)
                longest = max(longest, inner)
                run = len(b) - len(b.rstrip(b"\0"))
    return max(longest, run)


def _png(path):
    with open(path, "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            return "not a PNG"
        while True:
            h = f.read(8)
            if len(h) < 8:
                return "PNG ends before its IEND chunk"
            n, typ = struct.unpack(">I4s", h)
            data = f.read(n)
            crc = f.read(4)
            if len(data) < n or len(crc) < 4:
                return "PNG is cut off"
            if zlib.crc32(typ + data) & 0xFFFFFFFF != struct.unpack(">I", crc)[0]:
                return f"PNG chunk {typ.decode('latin1')} has a wrong checksum (damaged data)"
            if typ == b"IEND":
                return ""


def _zip(path):
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError, zlib.error) as e:
        return f"archive cannot be read ({e})"
    return f"archive member {bad} is damaged" if bad else ""


def _mp4(path):
    seen, pos, size = set(), 0, os.path.getsize(path)
    with open(path, "rb") as f:
        while pos + 8 <= size:
            f.seek(pos)
            h = f.read(16)
            n, typ = struct.unpack(">I4s", h[:8])
            if n == 1 and len(h) == 16:
                n = struct.unpack(">Q", h[8:16])[0]
            elif n == 0:
                n = size - pos
            if n < 8:
                break
            seen.add(typ)
            pos += n
    if b"moov" not in seen and b"moof" not in seen:
        return "video has no index (moov) so most players cannot open it"
    if pos > size:
        return "video is cut off"
    return ""


def _pdf(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 2048))
        tail = f.read()
    return "" if b"%%EOF" in tail and b"startxref" in tail else "PDF has no complete end (startxref / %%EOF)"


def _pillow(path, ext):
    try:
        from PIL import Image
    except ImportError:
        return ""
    try:
        Image.MAX_IMAGE_PIXELS = 300_000_000
        with Image.open(path) as im:
            im.load()
            w, h = im.size
            if ext in ("jpg", "jpeg") and h >= 64:
                g = im.convert("L").resize((min(w, 64), 64))
                rows = [g.crop((0, y, g.width, y + 1)).getextrema() for y in range(64)]
                flat = [lo == hi for lo, hi in rows]
                tail = 0
                for v in reversed(flat):
                    if not v:
                        break
                    tail += 1
                if tail >= 12 and not all(flat):       # lower fifth is one flat colour: data ran out
                    return "lower part of the picture is empty/grey (image data ends early)"
    except Exception as e:  # noqa: BLE001 - Pillow raises many kinds of errors for broken images
        return f"picture cannot be decoded ({type(e).__name__}: {str(e)[:80]})"
    return ""


def check(path, ext, complete=True):
    """Return "" if the file looks fine, else a short reason it is suspect."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "file missing"
    if size == 0:
        return "empty file"
    if not complete:
        return "only part of the file could be recovered (the rest was overwritten or is missing)"
    reason = ""
    try:
        if ext == "png":
            reason = _png(path) or _pillow(path, ext)
        elif ext in ("jpg", "jpeg", "gif", "bmp", "webp"):
            reason = _pillow(path, ext)
        elif ext in ("zip", "apk", "docx", "xlsx", "pptx", "jar"):
            reason = _zip(path)
        elif ext in ("mp4", "mov", "3gp", "m4a"):
            reason = _mp4(path)
        elif ext == "pdf":
            reason = _pdf(path)
    except (OSError, struct.error, ValueError) as e:
        reason = f"could not be checked ({type(e).__name__})"
    if reason:
        return reason
    if ext in ("jpg", "jpeg", "png", "mp4", "mov", "mp3", "m4a", "flac", "avi", "mkv", "mp3", "3gp") and size > 128 * 1024:
        longest = _zero_runs(path)
        if longest >= ZERO_RUN:
            return f"contains {longest // 1024} KB of empty (zero) data: likely overwritten or never written"
    return ""
