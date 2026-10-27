"""File carving: recover files from a raw disk, image or SD card by their signatures.

When a file is deleted its data is usually not erased; only its index entry is removed.
Instead of relying on that entry, this module looks for the start (header) of known file
types inside the raw data, measures where each file ends, and copies it out.
The source is only ever read (read-only); nothing is written to it.
"""

import os
import zipfile
from dataclasses import dataclass

from .signatures import EXT_CATEGORY, RIFF_TYPES, SIGNATURES, select_signatures

SECTOR = 512
CHUNK = 4 * 1024 * 1024


# ---------------------------------------------------------------- reading helpers

def _read_exact(f, n):
    parts, got = [], 0
    while got < n:
        b = f.read(n - got)
        if not b:
            break
        parts.append(b)
        got += len(b)
    return b"".join(parts)


def read_at(f, offset, length):
    """Read from any offset. Raw devices require sector-aligned I/O; that is handled here."""
    if length <= 0:
        return b""
    start = offset - offset % SECTOR
    end = offset + length
    end += (-end) % SECTOR
    f.seek(start)
    data = _read_exact(f, end - start)
    return data[offset - start: offset - start + length]


# ---------------------------------------------------------------- scanning

def scan(f, sigs, aligned=True, progress=None, chunk=CHUNK):
    """Find all candidate file starts. Returns [(offset, Signature), ...] in order."""
    reach = max(s.header_offset + len(s.header) for s in sigs)
    hits = {}
    pos, carry = 0, b""
    f.seek(0)
    while True:
        data = _read_exact(f, chunk)
        if not data:
            break
        buf = carry + data
        base = pos - len(carry)
        for sig in sigs:
            i = buf.find(sig.header)
            while i != -1:
                start = base + i - sig.header_offset
                if start >= 0 and start not in hits and (not aligned or start % SECTOR == 0):
                    hits[start] = sig
                i = buf.find(sig.header, i + 1)
        pos += len(data)
        carry = buf[-(reach - 1):] if reach > 1 else b""
        if progress:
            progress(pos)
    return sorted(hits.items())


# ---------------------------------------------------------------- measuring length

def find_footer(f, off, sig, limit):
    """Read until the first footer. Returns the length up to the end of the footer, or None."""
    pos = off + sig.header_offset + len(sig.header)
    carry = b""
    while pos - off < limit:
        data = read_at(f, pos, min(CHUNK, off + limit - pos))
        if not data:
            return None
        buf = carry + data
        base = pos - len(carry)
        j = buf.find(sig.footer)
        if j != -1:
            return base + j + len(sig.footer) - off
        carry = buf[-(len(sig.footer) - 1):] if len(sig.footer) > 1 else b""
        pos += len(data)
    return None


def jpeg_length(f, off, limit):
    """Walk the JPEG markers to find the real end (FFD9).

    This is not fooled by FFD9 bytes inside embedded thumbnails."""
    pos = off + 2
    while pos < off + limit:
        h = read_at(f, pos, 4)
        if len(h) < 2 or h[0] != 0xFF:
            return None
        m = h[1]
        if m == 0xD9:
            return pos + 2 - off
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            pos += 2
            continue
        if len(h) < 4:
            return None
        seglen = int.from_bytes(h[2:4], "big")
        if seglen < 2:
            return None
        pos += 2 + seglen
        if m == 0xDA:  # entropy-coded scan data: skip until the next real marker
            while pos < off + limit:
                data = read_at(f, pos, CHUNK)
                if len(data) < 2:
                    return None
                k = 0
                found = False
                while True:
                    k = data.find(b"\xff", k)
                    if k == -1 or k + 1 >= len(data):
                        break
                    n = data[k + 1]
                    if n == 0 or 0xD0 <= n <= 0xD7:
                        k += 2
                        continue
                    found = True
                    break
                if found:
                    pos += k
                    break
                pos += len(data) if k == -1 else k
    return None


def zip_length(f, off, limit):
    sig = next(s for s in SIGNATURES if s.kind == "zip")
    end = find_footer(f, off, sig, limit)
    if end is None:
        return None
    tail = read_at(f, off + end, 18)
    if len(tail) < 18:
        return None
    comment = int.from_bytes(tail[16:18], "little")
    return end + 18 + comment


def mp4_length(f, off, limit):
    """Add up the sizes of the top-level MP4 boxes."""
    pos, saw_media = off, False
    while pos - off < limit:
        h = read_at(f, pos, 16)
        if len(h) < 8:
            break
        size = int.from_bytes(h[:4], "big")
        typ = h[4:8]
        if not all(32 <= c < 127 for c in typ):
            break
        if size == 1:
            if len(h) < 16:
                break
            size = int.from_bytes(h[8:16], "big")
        if size < 8:
            break
        if typ in (b"moov", b"mdat"):
            saw_media = True
        pos += size
    return min(pos - off, limit) if saw_media else None


_BR_V1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_BR_V2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3_length(f, off, head, limit):
    """After the ID3 tag, walk MPEG Layer III frames to find where the song ends."""
    if head[3] not in (2, 3, 4) or head[5] & 0x0F or any(b >= 0x80 for b in head[6:10]):
        return None
    tag = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
    pos = off + 10 + tag
    frames = 0
    while pos - off < limit:
        h = read_at(f, pos, 4)
        if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
            break
        ver, layer = (h[1] >> 3) & 3, (h[1] >> 1) & 3
        br_i, sr_i, pad = h[2] >> 4, (h[2] >> 2) & 3, (h[2] >> 1) & 1
        if ver == 1 or layer != 1 or br_i in (0, 15) or sr_i == 3:
            break
        sr = _SR[ver][sr_i]
        if ver == 3:
            flen = 144 * _BR_V1[br_i] * 1000 // sr + pad
        else:
            flen = 72 * _BR_V2[br_i] * 1000 // sr + pad
        if flen < 4:
            break
        pos += flen
        frames += 1
    if frames < 3:
        return None
    if read_at(f, pos, 3) == b"TAG":  # ID3v1 trailer
        pos += 128
    return pos - off


def measure(f, off, sig, nxt, keep_partial=True):
    """Check that a candidate looks real and measure it.

    Returns (length, ext, complete) or None to reject the candidate."""
    head = read_at(f, off, 64)
    hs = sig.header_offset
    if head[hs:hs + len(sig.header)] != sig.header:
        return None
    limit, ext, length = sig.max_size, sig.ext, None

    if sig.kind == "jpeg":
        if len(head) < 4 or head[3] < 0xC0:
            return None
        length = jpeg_length(f, off, limit)
    elif sig.kind == "footer":
        if sig.ext == "png" and head[12:16] != b"IHDR":
            return None
        length = find_footer(f, off, sig, limit)
        if length and sig.ext == "pdf":
            tail = read_at(f, off + length, 2)
            if tail[:2] == b"\r\n":
                length += 2
            elif tail[:1] in (b"\n", b"\r"):
                length += 1
    elif sig.kind == "zip":
        length = zip_length(f, off, limit)
    elif sig.kind == "riff":
        kind = head[8:12]
        if kind not in RIFF_TYPES:
            return None
        size = int.from_bytes(head[4:8], "little") + 8
        if size < 16 or size > limit:
            return None
        ext, length = RIFF_TYPES[kind], size
    elif sig.kind == "mp4":
        box = int.from_bytes(head[0:4], "big")
        if not 12 <= box <= 128:
            return None
        brand = head[8:12]
        if brand == b"qt  ":
            ext = "mov"
        elif brand.startswith(b"3g"):
            ext = "3gp"
        elif brand == b"M4A ":
            ext = "m4a"
        length = mp4_length(f, off, limit)
    elif sig.kind == "mp3":
        length = mp3_length(f, off, head, limit)
        if length is None:
            return None  # a bare "ID3" is a very common byte sequence; only keep real songs

    if length:
        return length, ext, True
    if not keep_partial:
        return None
    # no clean end found (partly overwritten): take up to the next candidate or the size limit
    part = min(limit, nxt - off) if nxt else min(limit, CHUNK)
    return (part, ext, False) if part > 0 else None


# ---------------------------------------------------------------- extraction

def copy_range(f, off, length, path):
    with open(path, "wb") as out:
        done = 0
        while done < length:
            data = read_at(f, off + done, min(CHUNK, length - done))
            if not data:
                break
            out.write(data)
            done += len(data)
    return done


def refine_zip_ext(path):
    """Tell APK / DOCX / XLSX / PPTX apart from a plain ZIP."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError):
        return "zip"
    if "AndroidManifest.xml" in names:
        return "apk"
    if "[Content_Types].xml" in names:
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
    return "zip"


@dataclass
class Recovered:
    path: str
    offset: int
    size: int
    ext: str
    complete: bool


def carve(source, outdir, types=None, aligned=True, keep_partial=True,
          progress=None, log=None):
    """Carve files out of `source` into `outdir`. Returns a list of Recovered."""
    sigs = select_signatures(types)
    os.makedirs(outdir, exist_ok=True)
    results = []
    with open(source, "rb", buffering=0) as f:
        hits = scan(f, sigs, aligned=aligned, progress=progress)
        if log:
            log(f"Candidate file starts found: {len(hits)}")
        last_end = -1
        for n, (off, sig) in enumerate(hits):
            if off < last_end:
                continue  # candidate lies inside a file we already recovered
            nxt = hits[n + 1][0] if n + 1 < len(hits) else None
            m = measure(f, off, sig, nxt, keep_partial)
            if m is None:
                continue
            length, ext, complete = m
            category = EXT_CATEGORY.get(ext, "other")
            folder = os.path.join(outdir, category)
            os.makedirs(folder, exist_ok=True)
            suffix = "" if complete else "_partial"
            path = os.path.join(folder, f"{len(results) + 1:05d}_{off:#x}{suffix}.{ext}")
            size = copy_range(f, off, length, path)
            if ext == "zip" and complete:
                new_ext = refine_zip_ext(path)
                if new_ext != "zip":
                    new_folder = os.path.join(outdir, EXT_CATEGORY[new_ext])
                    os.makedirs(new_folder, exist_ok=True)
                    new_path = os.path.join(new_folder, os.path.basename(path)[:-3] + new_ext)
                    os.replace(path, new_path)
                    path, ext = new_path, new_ext
                    try:
                        os.rmdir(folder)  # remove the now-empty "archives" folder
                    except OSError:
                        pass  # other files are still in it
            last_end = off + size
            results.append(Recovered(path, off, size, ext, complete))
            if log:
                log(f"  [{ext}] {size:>12,} bytes  {os.path.relpath(path, outdir)}")
    return results
