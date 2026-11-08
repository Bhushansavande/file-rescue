"""Join files that were stored in two pieces (fragmented), for JPEG photos and MP4 videos.

A file system normally writes a file in one run of clusters, but a fuller drive splits some
files into two or more pieces with other data in between. Plain carving then returns only the
first piece. This module tries the common "two pieces" case:

* JPEG: the piece that holds the end of the picture is found by looking for stray end-of-image
  markers. The two pieces are joined and the result is decoded with Pillow; a join is accepted
  only when the whole picture decodes without errors and looks like a normal photo (no visible
  seam where the wrong data was joined in). Needs Pillow (pip install Pillow).
* MP4/MOV: the index (moov box) is located, which says where every video chunk should be. The
  piece boundary is chosen so that the video chunks line up with valid video data.

Honest limits: only two pieces, the cluster size must be right (default 4096, see
--cluster-size), JPEG needs Pillow, and a photo with a lot of fine noise or texture can be
rejected. A join that cannot be proven is not written; the partial file stays as it was.
"""

import io
import re
import struct
import time

from .carver import CHUNK, read_at

TIME_BUDGET = 60.0            # seconds per file
MAX_FIRST = 2048              # clusters tried for the first piece
MAX_SECOND = 2048
MAX_SECOND_SCAN = 600       # clusters tried for the second piece (about 2.4 MB)
MAX_ORPHANS = 12


class _Budget:
    def __init__(self, seconds=TIME_BUDGET):
        self.end = time.monotonic() + seconds

    def left(self):
        return time.monotonic() > self.end


def find_all(f, needle, start=0, end=None, limit=2_000_000):
    """Offsets of every occurrence of `needle` in the source (used once per run)."""
    out, pos, carry = [], start, b""
    while end is None or pos < end:
        data = read_at(f, pos, CHUNK)
        if not data:
            break
        buf = carry + data
        base = pos - len(carry)
        i = buf.find(needle)
        while i != -1:
            out.append(base + i)
            i = buf.find(needle, i + 1)
        carry = buf[-(len(needle) - 1):]
        pos += len(data)
        if len(out) > limit:
            break
    return sorted(set(out))


# ------------------------------------------------------------------ JPEG

def _row_energy(img):
    """Mean vertical pixel difference per row (0 for flat rows): noise is high, photos are low."""
    from PIL import Image, ImageChops
    g = img.convert("L")
    w, h = g.size
    if h < 3:
        return []
    shifted = ImageChops.offset(g, 0, 1)
    diff = ImageChops.difference(g, shifted).crop((0, 1, w, h))
    col = diff.resize((1, h - 1), Image.BOX)
    return list(col.tobytes())


def spikes(img, ignore_tail=0):
    """Rows whose vertical change is far above their neighbours: where wrongly joined data begins.

    Rows after the last real picture row (flat grey from a cut-off decode) are not counted, nor
    are the last `ignore_tail` real rows."""
    e = _row_energy(img)
    last = max((i for i, v in enumerate(e) if v > 0), default=-1) - ignore_tail
    count = 0
    for y in range(0, last + 1):
        win = [v for v in e[max(0, y - 8):y] + e[y + 1:y + 9] if v > 0]
        if len(win) < 4:
            continue
        win.sort()
        local = win[len(win) // 2]
        if e[y] > 2.2 * local + 6:
            count += 1
    return count


def _decode(data, strict, fast=False):
    """Decode a JPEG held in memory; None if it is broken (or, when `strict`, cut off).

    `fast` decodes a reduced-size version, which is enough to look for seams and much quicker."""
    from PIL import Image, ImageFile
    old = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = not strict
    try:
        Image.MAX_IMAGE_PIXELS = 200_000_000
        im = Image.open(io.BytesIO(data))
        if fast and max(im.size) > 1000:
            im.draft("RGB", (im.size[0] // 4, im.size[1] // 4))
        im.load()
        return im
    except Exception:  # noqa: BLE001 - any decoder error means "this join is wrong"
        return None
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old


def _complete(img):
    """False if the bottom of the picture is missing (the decoder fills it with flat grey)."""
    e = _row_energy(img)
    last = max((i for i, v in enumerate(e) if v > 0), default=-1)
    return last >= 0 and last >= len(e) - max(3, len(e) // 30)


def _first_len(a, k, cluster):
    return ((a // cluster) + k) * cluster - a


K_WINDOW = 24            # how many first-piece lengths below the largest clean one are tried


def join_jpeg(f, a, cluster=4096, orphans=None, exclude=None, budget=None):
    """Try to rebuild the JPEG that starts at offset `a`. Returns the file bytes or None.

    `orphans`: offsets of every FFD9 in the source; `exclude(off)` says whether an offset lies
    inside an already recovered file."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return None
    budget = budget or _Budget()
    head = read_at(f, a, _first_len(a, MAX_FIRST, cluster))
    if len(head) < cluster or head[:3] != b"\xff\xd8\xff":
        return None

    # 1) the longest start of the file that still decodes without a visible seam
    def clean(k):
        im = _decode(head[:_first_len(a, k, cluster)], strict=False, fast=True)
        return im is not None and spikes(im, ignore_tail=3) == 0

    lo, hi = 1, min(MAX_FIRST, len(head) // cluster)
    if not clean(lo):
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if clean(mid):
            lo = mid
        else:
            hi = mid - 1
    k_clean = lo

    # the piece cannot be longer than where the decoded picture stops growing (wrong data after
    # the real piece usually makes the decoder give up soon)
    def rows(k):
        im = _decode(head[:_first_len(a, k, cluster)], strict=False, fast=True)
        return 0 if im is None else max((i for i, v in enumerate(_row_energy(im)) if v > 0), default=-1) + 1

    top = min(MAX_FIRST, len(head) // cluster)
    full = rows(top)
    lo, hi = 1, top
    while lo < hi:
        mid = (lo + hi) // 2
        if rows(mid) >= full - 1:
            hi = mid
        else:
            lo = mid + 1
    k_top = min(k_clean, lo) + 1
    ks = range(k_top, max(0, k_top - K_WINDOW), -1)

    # 2) which stray end-of-image marker closes it, and where does the second piece begin
    cands = [e for e in (orphans or []) if abs(e - a) < (1 << 32) and not (exclude and exclude(e))
             and not (a <= e < a + _first_len(a, 1, cluster))]
    cands.sort(key=lambda e: abs(e - a))
    for e in cands[:MAX_ORPHANS]:
        end = e + 2
        last_cluster = (end - 1) // cluster
        tail_limit = min(MAX_SECOND, last_cluster)
        for k in ks:
            if budget.left():
                return None
            first = head[:_first_len(a, k, cluster)]
            if a <= e < a + len(first):
                continue

            def build(m):
                start = (last_cluster - m + 1) * cluster
                return first + read_at(f, start, end - start)

            # grow the second piece backwards until the picture is complete; a longer piece
            # would only add unrelated data in the middle
            for m in range(1, min(tail_limit, MAX_SECOND_SCAN) + 1):
                if budget.left():
                    return None
                im = _decode(build(m), strict=True, fast=True)
                if im is None or not _complete(im):
                    continue
                if spikes(im) == 0:
                    data = build(m)
                    if _decode(data, strict=True) is not None:     # full-size check of the result
                        return data
                break
    return None


# ------------------------------------------------------------------ MP4

def _boxes(data, pos=0, end=None):
    end = len(data) if end is None else end
    while pos + 8 <= end:
        size, typ = struct.unpack_from(">I4s", data, pos)
        hdr = 8
        if size == 1 and pos + 16 <= end:
            size, hdr = struct.unpack_from(">Q", data, pos + 8)[0], 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            return
        yield typ, pos + hdr, pos + size
        pos += size


def _video_samples(moov):
    """[(absolute_offset, size)] of every video sample, from the stco/stsc/stsz tables of moov."""
    out = []
    for typ, s, e in _boxes(moov, 8, len(moov)):
        if typ != b"trak":
            continue
        handler, chunks, stsc, sizes, fixed = b"", [], [], [], 0
        stack = [(s, e)]
        while stack:
            bs, be = stack.pop()
            for t, cs, ce in _boxes(moov, bs, be):
                if t in (b"mdia", b"minf", b"stbl"):
                    stack.append((cs, ce))
                elif t == b"hdlr":
                    handler = moov[cs + 8:cs + 12]
                elif t == b"stco":
                    n = struct.unpack_from(">I", moov, cs + 4)[0]
                    chunks = list(struct.unpack_from(">%dI" % min(n, (ce - cs - 8) // 4), moov, cs + 8))
                elif t == b"co64":
                    n = struct.unpack_from(">I", moov, cs + 4)[0]
                    chunks = list(struct.unpack_from(">%dQ" % min(n, (ce - cs - 8) // 8), moov, cs + 8))
                elif t == b"stsc":
                    n = struct.unpack_from(">I", moov, cs + 4)[0]
                    for i in range(min(n, (ce - cs - 8) // 12)):
                        stsc.append(struct.unpack_from(">III", moov, cs + 8 + 12 * i)[:2])
                elif t == b"stsz":
                    fixed, n = struct.unpack_from(">II", moov, cs + 4)
                    if not fixed:
                        sizes = list(struct.unpack_from(">%dI" % min(n, (ce - cs - 12) // 4), moov, cs + 12))
                    else:
                        sizes = [fixed] * min(n, 1_000_000)
        if handler != b"vide" or not chunks or not stsc or not sizes:
            continue
        si = 0
        for ci, off in enumerate(chunks, 1):
            per = 0
            for first, count in stsc:
                if first <= ci:
                    per = count
            pos = off
            for _ in range(per):
                if si >= len(sizes):
                    break
                out.append((pos, sizes[si]))
                pos += sizes[si]
                si += 1
            if len(out) > 1_000_000:
                return out
    return out


def _looks_like_nal(b):
    if len(b) < 5:
        return False
    n = struct.unpack(">I", b[:4])[0]
    t = b[4]
    return 0 < n < (32 << 20) and not t & 0x80 and (1 <= t & 31 <= 12 or ((t >> 1) & 63) <= 40)


def join_mp4(f, a, cluster=4096, moov_positions=None, exclude=None, budget=None):
    """Rebuild an MP4/MOV whose mdat is followed by the moov box in another place.

    Returns the file bytes or None."""
    budget = budget or _Budget()
    head = read_at(f, a, 64)
    if len(head) < 16:
        return None
    boxes = list(_boxes(head + b"\0" * 0, 0, len(head)))
    if not boxes or boxes[0][0] != b"ftyp":
        return None
    ftyp_end = boxes[0][2]
    h2 = read_at(f, a + ftyp_end, 16)
    while len(h2) >= 8 and h2[4:8] in (b"free", b"skip", b"wide"):         # padding boxes
        n = struct.unpack(">I", h2[:4])[0]
        if n < 8:
            return None
        ftyp_end += n
        h2 = read_at(f, a + ftyp_end, 16)
    if len(h2) < 16 or h2[4:8] != b"mdat":
        return None
    size = struct.unpack(">I", h2[:4])[0]
    if size == 1:
        size = struct.unpack(">Q", h2[8:16])[0]
    if size < 16:
        return None
    moov_logical = ftyp_end + size                    # where moov must begin in the finished file
    cands = [p for p in (moov_positions or []) if not (exclude and exclude(p))]
    cands.sort(key=lambda p: abs(p - a))
    for p in cands[:MAX_ORPHANS * 4]:
        if budget.left():
            return None
        hdr = read_at(f, p - 4, 8)
        if len(hdr) < 8 or hdr[4:8] != b"moov":
            continue
        msize = struct.unpack(">I", hdr[:4])[0]
        if not 100 < msize < (64 << 20):
            continue
        moov = read_at(f, p - 4, msize)
        if len(moov) < msize or b"mvhd" not in moov[:64]:
            continue
        samples = _video_samples(moov)
        if len(samples) < 3:
            continue
        total = moov_logical + msize
        step = max(1, len(samples) // 60)
        sample = [o for o, _sz in samples[::step]]
        # physical start of the second piece for each possible length of the first piece
        tail_end = p - 4 + msize                      # physical end of moov = end of the file
        best = None
        for k in range(1, MAX_FIRST + 1):
            first_len = _first_len(a, k, cluster)
            if first_len >= total:
                break
            second_start = tail_end - (total - first_len)
            if second_start < 0 or a <= second_start < a + first_len:
                continue

            def phys(logical):
                return a + logical if logical < first_len else second_start + (logical - first_len)

            ok = 0
            checked = 0
            for off in sample:
                if off >= total or off < ftyp_end:
                    continue
                checked += 1
                if _looks_like_nal(read_at(f, phys(off), 5)):
                    ok += 1
            if checked >= 3 and ok == checked:
                best = (k, first_len, second_start)
                break
            if budget.left():
                return None
        if best:
            _k, first_len, second_start = best
            return read_at(f, a, first_len) + read_at(f, second_start, total - first_len)
    return None
