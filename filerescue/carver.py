"""File carving: recover files from a raw disk, image or SD card by their signatures.

When a file is deleted its data is usually not erased; only its index entry is removed.
Instead of relying on that entry, this module looks for the start (header) of known file
types inside the raw data, measures where each file ends, and copies it out.
The source is only ever read (read-only); nothing is written to it.
"""

import os
import zipfile
from dataclasses import dataclass

from .signatures import (EXT_CATEGORY, RIFF_TYPES, SIGNATURES, allowed_extensions,
                         select_signatures)

SECTOR = 512
CHUNK = 4 * 1024 * 1024
PARTIAL_CAP = 64 * 1024 * 1024   # largest "partial" file we will write when no end is found


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


def find_bytes(f, start, end, needle):
    """Absolute offset of the first `needle` in [start, end), or None."""
    pos, carry = start, b""
    while pos < end:
        data = read_at(f, pos, min(CHUNK, end - pos))
        if not data:
            return None
        buf = carry + data
        j = buf.find(needle)
        if j != -1:
            return pos - len(carry) + j
        carry = buf[-(len(needle) - 1):] if len(needle) > 1 else b""
        pos += len(data)
    return None


def png_length(f, off, limit):
    """Walk the PNG chunks (length, type, data, CRC) until IEND.

    Searching for the IEND bytes alone would stop early if they appear inside another chunk."""
    pos = off + 8
    first = True
    while pos - off < limit:
        h = read_at(f, pos, 8)
        if len(h) < 8:
            return None
        n, typ = int.from_bytes(h[:4], "big"), h[4:8]
        if n > 0x7FFFFFFF or not typ.isalpha():
            return None
        if first and (typ != b"IHDR" or n != 13):
            return None
        first = False
        pos += 12 + n
        if typ == b"IEND":
            return pos - off
    return None


_PDF_OBJ_START = __import__("re").compile(rb"\d+ \d+ obj|xref|trailer|%PDF-")


def pdf_length(f, off, limit):
    """Find the %%EOF that really ends the PDF.

    Incremental updates and linearized PDFs contain several %%EOF markers; after one, if more
    PDF objects follow immediately, keep going."""
    pos = off + 5
    for _ in range(64):
        e = find_bytes(f, pos, off + limit, b"%%EOF")
        if e is None:
            return None
        end = e + 5
        nxt = read_at(f, end, 80)
        stripped = nxt.lstrip(b"\r\n \t")
        if not (stripped and _PDF_OBJ_START.match(stripped) and not stripped.startswith(b"%PDF-")):
            skipped = len(nxt) - len(stripped)
            if nxt[:skipped].strip(b"\r\n") == b"" and skipped <= 2:
                end += skipped              # trailing newline belongs to the file
            return end - off
        pos = end
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


def _skip_sub_blocks(f, pos, end):
    """Skip a GIF data sub-block chain. Returns the position after its 0-length terminator."""
    while pos < end:
        b = read_at(f, pos, 1)
        if not b:
            return None
        pos += 1 + b[0]
        if b[0] == 0:
            return pos
    return None


def gif_length(f, off, limit):
    """Walk the GIF block structure to the real trailer (0x3B).

    A bare 00 3B can occur inside compressed image data, so searching for it truncates files."""
    end = off + limit
    head = read_at(f, off, 13)
    if len(head) < 13 or head[:4] != b"GIF8" or head[4:5] not in (b"7", b"9") or head[5:6] != b"a":
        return None
    pos = off + 13
    if head[10] & 0x80:                      # global color table
        pos += 3 * (1 << ((head[10] & 7) + 1))
    while pos < end:
        b = read_at(f, pos, 1)
        if not b:
            return None
        if b[0] == 0x3B:                     # trailer
            return pos + 1 - off
        if b[0] == 0x21:                     # extension: label byte, then sub-blocks
            pos = _skip_sub_blocks(f, pos + 2, end)
        elif b[0] == 0x2C:                   # image: 9-byte descriptor, optional color table, LZW size, data
            d = read_at(f, pos, 10)
            if len(d) < 10:
                return None
            pos += 10
            if d[9] & 0x80:
                pos += 3 * (1 << ((d[9] & 7) + 1))
            pos = _skip_sub_blocks(f, pos + 1, end)
        else:
            return None
        if pos is None:
            return None
    return None


def zip_length(f, off, limit):
    """Find the End Of Central Directory record that belongs to this archive.

    A stored (uncompressed) ZIP nested inside an APK/ZIP carries its own EOCD, so the first
    one found is not necessarily the right one. The real one satisfies
    start + central_dir_offset + central_dir_size == its own position."""
    pos = off + 4
    while pos < off + limit:
        p = find_bytes(f, pos, off + limit, b"PK\x05\x06")
        if p is None:
            return None
        rec = read_at(f, p, 22)
        if len(rec) == 22:
            cd_size = int.from_bytes(rec[12:16], "little")
            cd_off = int.from_bytes(rec[16:20], "little")
            comment = int.from_bytes(rec[20:22], "little")
            zip64 = cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF
            if zip64 or off + cd_off + cd_size == p:
                return p - off + 22 + comment
        pos = p + 1
    return None


_MP4_BOXES = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"uuid", b"moof", b"mfra",
              b"styp", b"sidx", b"pdin", b"meta", b"udta", b"junk", b"pnot", b"prft", b"emsg"}


def mp4_length(f, off, limit):
    """Add up the sizes of the top-level MP4 boxes."""
    pos, saw_media = off, False
    while pos - off < limit:
        h = read_at(f, pos, 16)
        if len(h) < 8:
            break
        size = int.from_bytes(h[:4], "big")
        typ = h[4:8]
        if typ not in _MP4_BOXES:       # unknown type: this is where the file ends
            break
        if size == 1:
            if len(h) < 16:
                break
            size = int.from_bytes(h[8:16], "big")
        if size < 8 or pos - off + size > limit:
            break
        if typ in (b"moov", b"mdat", b"moof"):
            saw_media = True
        pos += size
    return pos - off if saw_media else None


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
        if sig.ext == "png":
            if head[12:16] != b"IHDR":
                return None
            length = png_length(f, off, limit)
        elif sig.ext == "gif":
            if head[:6] not in (b"GIF87a", b"GIF89a"):
                return None
            length = gif_length(f, off, limit)
        elif sig.ext == "pdf":
            length = pdf_length(f, off, limit)
        else:
            length = find_footer(f, off, sig, limit)
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
    # no clean end found (partly overwritten): take up to the next candidate, the size limit,
    # or PARTIAL_CAP, so a false hit can never produce a huge junk file
    part = min(limit, PARTIAL_CAP, (nxt - off) if nxt else PARTIAL_CAP)
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
    allowed = allowed_extensions(types)   # None = keep every type
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
            if allowed is not None and sig.kind != "zip" and ext not in allowed:
                last_end = off + length   # a real file of a type that was not asked for
                continue
            category = EXT_CATEGORY.get(ext, "other")
            folder = os.path.join(outdir, category)
            os.makedirs(folder, exist_ok=True)
            suffix = "" if complete else "_partial"
            path = os.path.join(folder, f"{len(results) + 1:05d}_{off:#x}{suffix}.{ext}")
            size = copy_range(f, off, length, path)
            if complete and size < length:
                # the measured end lies beyond the end of the source: the file is cut off
                complete = False
                new_path = path.rsplit(".", 1)[0] + "_partial." + ext
                os.replace(path, new_path)
                path = new_path
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
            if allowed is not None and ext not in allowed:
                os.remove(path)           # e.g. an APK when only documents were requested
                try:
                    os.rmdir(os.path.dirname(path))   # drop the folder if it is now empty
                except OSError:
                    pass
                last_end = off + size
                continue
            last_end = off + size
            results.append(Recovered(path, off, size, ext, complete))
            if log:
                log(f"  [{ext}] {size:>12,} bytes  {os.path.relpath(path, outdir)}")
    return results
