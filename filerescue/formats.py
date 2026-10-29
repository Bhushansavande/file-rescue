"""Walkers that find where a file ends by following the structure of its container format.

Every walker takes ``rd(offset, n) -> bytes`` (a bounded random-access read), the file start
offset ``off`` and a size ``limit``. It returns ``(length, ext)``:

* ``length`` is the file size, or ``None`` when the header is valid but the end could not be
  found (a damaged or cut-off file, to be saved as "partial");
* ``ext`` is the file extension the format should get.

A walker raises ``NotThisFormat`` when the bytes are not a file of that format at all.
"""

import zlib


class NotThisFormat(Exception):
    """The candidate is not a real file of this format (a false hit)."""


def _u16(b, e="<"):
    return int.from_bytes(b, "little" if e == "<" else "big")


def _u32(b, e="<"):
    return int.from_bytes(b, "little" if e == "<" else "big")


# ------------------------------------------------------------------ Ogg (Opus, Vorbis, Theora)

def ogg_walk(rd, off, limit):
    """Walk Ogg pages until every logical stream has ended (EOS flag)."""
    pos = off
    open_streams = set()
    ext = None
    while pos - off < limit:
        h = rd(pos, 27)
        if len(h) < 27 or h[:4] != b"OggS" or h[4] != 0:
            if ext is None:
                raise NotThisFormat("ogg")
            return None, ext
        htype, serial, nseg = h[5], h[14:18], h[26]
        seg = rd(pos + 27, nseg)
        if len(seg) < nseg:
            return None, ext
        body = sum(seg)
        if ext is None:
            # the first page must open a stream (BOS, sequence 0) and name a known codec
            if not htype & 0x02 or _u32(h[18:22]) != 0:
                raise NotThisFormat("ogg")
            payload = rd(pos + 27 + nseg, min(body, 16))
            if payload.startswith(b"OpusHead"):
                ext = "opus"
            elif payload[:7] == b"\x01vorbis" or payload[:5] == b"\x7fFLAC" or payload[:8] == b"Speex   ":
                ext = "ogg"
            elif payload[:7] == b"\x80theora":
                ext = "ogv"
            else:
                raise NotThisFormat("ogg codec")
        if htype & 0x02:
            open_streams.add(serial)
        pos += 27 + nseg + body
        if htype & 0x04:
            open_streams.discard(serial)
        if not open_streams:
            return pos - off, ext
    return None, ext


# ------------------------------------------------------------------ Matroska / WebM (EBML)

_TOP_IDS = {0x114D9B74, 0x1549A966, 0x1654AE6B, 0x1F43B675, 0x1C53BB6B, 0x1941A469,
            0x1043A770, 0x1254C367}                 # SeekHead, Info, Tracks, Cluster, Cues, ...
_FILLER_IDS = {0xEC, 0xBF}                          # Void, CRC-32 (one byte: only trusted in context)
_CLUSTER_CHILD_IDS = {0xE7, 0xA7, 0xAB, 0xA3, 0xA0, 0xAF, 0x5854, 0xEC, 0xBF}


def _vint(b, i, keep_marker):
    """Parse an EBML variable-length integer at b[i]. Returns (value, length) or None."""
    if i >= len(b):
        return None
    first = b[i]
    length, mask = 1, 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8 or i + length > len(b):
        return None
    val = first if keep_marker else first & (mask - 1)
    for k in range(1, length):
        val = (val << 8) | b[i + k]
    return val, length


def _unknown(size, length):
    return size == (1 << (7 * length)) - 1


def ebml_walk(rd, off, limit):
    head = rd(off, 96)
    if head[:4] != b"\x1a\x45\xdf\xa3":
        raise NotThisFormat("ebml")
    s = _vint(head, 4, False)
    if not s or s[0] > 80:
        raise NotThisFormat("ebml header")
    hsize, hl = s
    body = head[4 + hl: 4 + hl + hsize]
    doctype = None
    i = 0
    while i < len(body):                       # elements inside the EBML header
        eid = _vint(body, i, True)
        if not eid:
            break
        sz = _vint(body, i + eid[1], False)
        if not sz:
            break
        start = i + eid[1] + sz[1]
        if eid[0] == 0x4282:
            doctype = body[start:start + sz[0]]
        i = start + sz[0]
    if doctype == b"webm":
        ext = "webm"
    elif doctype in (b"matroska", b"matroska\x00"):
        ext = "mkv"
    else:
        raise NotThisFormat("ebml doctype")
    pos = off + 4 + hl + hsize
    seg = rd(pos, 16)
    if seg[:4] != b"\x18\x53\x80\x67":
        raise NotThisFormat("no segment")
    s = _vint(seg, 4, False)
    if not s:
        raise NotThisFormat("segment size")
    ssize, sl = s
    data_start = pos + 4 + sl
    if not _unknown(ssize, sl):
        end = data_start + ssize
        return (end - off, ext) if end - off <= limit else (None, ext)
    # unknown-size segment (live recordings): walk the top-level children
    p = data_start
    while p - off < limit:
        h = rd(p, 16)
        eid = _vint(h, 0, True)
        if not eid or (eid[0] not in _TOP_IDS and eid[0] not in _FILLER_IDS):
            break
        sz = _vint(h, eid[1], False)
        if not sz:
            break
        size, sl2 = sz
        if eid[0] in _FILLER_IDS:              # a one-byte id is too weak alone: the next element
            nxt = _vint(rd(p + eid[1] + sl2 + size, 8), 0, True)   # must be a real top-level one
            if _unknown(size, sl2) or not nxt or nxt[0] not in _TOP_IDS:
                break
        if _unknown(size, sl2):                # an unknown-size Cluster: ends at the next top-level id
            q = p + eid[1] + sl2
            while q - off < limit:
                c = rd(q, 16)
                cid = _vint(c, 0, True)
                if not cid or cid[0] not in _CLUSTER_CHILD_IDS:
                    break
                cs = _vint(c, cid[1], False)
                if not cs or _unknown(*cs):
                    break
                q += cid[1] + cs[1] + cs[0]
            p = q
            continue
        p += eid[1] + sl2 + size
    return (p - off, ext) if p > data_start else (None, ext)


# ------------------------------------------------------------------ SQLite (WhatsApp databases)

def sqlite_walk(rd, off, limit):
    h = rd(off, 100)
    if len(h) < 100 or h[:16] != b"SQLite format 3\x00":
        raise NotThisFormat("sqlite")
    page = _u16(h[16:18], ">")
    if page == 1:
        page = 65536
    if page < 512 or page & (page - 1):
        raise NotThisFormat("page size")
    if h[18] not in (1, 2) or h[19] not in (1, 2) or (h[21], h[22], h[23]) != (64, 32, 32):
        raise NotThisFormat("sqlite header")
    pages = _u32(h[28:32], ">")
    if pages == 0 or _u32(h[24:28], ">") != _u32(h[92:96], ">"):
        raise NotThisFormat("page count not valid")
    length = page * pages
    return (length if length <= limit else None), "sqlite"


# ------------------------------------------------------------------ BMP

def bmp_walk(rd, off, limit):
    h = rd(off, 30)
    if len(h) < 30 or h[:2] != b"BM" or h[6:10] != b"\0\0\0\0":
        raise NotThisFormat("bmp")
    size, data_off, dib = _u32(h[2:6]), _u32(h[10:14]), _u32(h[14:18])
    if dib not in (12, 40, 52, 56, 64, 108, 124):
        raise NotThisFormat("bmp dib")
    if dib == 12:
        width, height, planes, bpp = _u16(h[18:20]), _u16(h[20:22]), _u16(h[22:24]), _u16(h[24:26])
    else:
        width = int.from_bytes(h[18:22], "little", signed=True)
        height = int.from_bytes(h[22:26], "little", signed=True)
        planes, bpp = _u16(h[26:28]), _u16(h[28:30])
    if planes != 1 or bpp not in (1, 2, 4, 8, 16, 24, 32) or width <= 0 or height == 0 \
            or abs(height) > 100000 or width > 100000:
        raise NotThisFormat("bmp fields")
    if data_off < 14 + dib or data_off >= size or size > limit:
        raise NotThisFormat("bmp size")
    return size, "bmp"


# ------------------------------------------------------------------ 7-Zip

def sevenzip_walk(rd, off, limit):
    h = rd(off, 32)
    if len(h) < 32 or h[:6] != b"7z\xbc\xaf\x27\x1c" or zlib.crc32(h[12:32]) != _u32(h[8:12]):
        raise NotThisFormat("7z")
    length = 32 + int.from_bytes(h[12:20], "little") + int.from_bytes(h[20:28], "little")
    return (length if length <= limit else None), "7z"


# ------------------------------------------------------------------ TIFF and TIFF-based RAW

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8,
              13: 4, 16: 8, 17: 8, 18: 8}
_SUB_IFD_TAGS = {0x14A, 0x8769, 0x8825, 0xA005}      # SubIFDs, Exif, GPS, Interoperability


def tiff_walk(rd, off, limit):
    """Follow every IFD, strip, tile and out-of-line value to find the highest byte used.

    Camera RAW formats built on TIFF (CR2, DNG, NEF, ARW) end at their last referenced data.
    This is a good estimate, not a proof: files with unreferenced trailing data come back short."""
    h = rd(off, 16)
    if h[:4] == b"II*\x00":
        e = "<"
    elif h[:4] == b"MM\x00*":
        e = ">"
    else:
        raise NotThisFormat("tiff")
    ifd0 = _u32(h[4:8], e)
    if ifd0 < 8 or ifd0 > limit - 6:
        raise NotThisFormat("tiff ifd offset")
    ext = "cr2" if h[8:10] == b"CR" else "tif"
    end, todo, seen, make = 8, [(ifd0, 0)], set(), b""

    def values(typ, cnt, raw, valoff):
        size = _TYPE_SIZE[typ] * cnt
        data = raw[:size] if size <= 4 else rd(off + valoff, min(size, 800000))
        step = _TYPE_SIZE[typ]
        return [int.from_bytes(data[i:i + step], "little" if e == "<" else "big")
                for i in range(0, len(data) - step + 1, step)]

    first = True
    while todo and len(seen) < 64:
        o, depth = todo.pop()
        if o in seen or depth > 4:
            continue
        seen.add(o)
        n = _u16(rd(off + o, 2), e)
        data = rd(off + o + 2, n * 12 + 4) if 0 < n <= 1000 else b""
        if len(data) < n * 12 + 4 or n == 0:
            if first:
                raise NotThisFormat("tiff ifd")
            continue
        end = max(end, o + 2 + n * 12 + 4)
        prev, tags = -1, {}
        for i in range(n):
            ent = data[i * 12:(i + 1) * 12]
            tag, typ, cnt = _u16(ent[0:2], e), _u16(ent[2:4], e), _u32(ent[4:8], e)
            if typ not in _TYPE_SIZE or tag < prev:
                if first:
                    raise NotThisFormat("tiff entry")
                break
            prev = tag
            size = _TYPE_SIZE[typ] * cnt
            valoff = _u32(ent[8:12], e) if size > 4 else 0
            if size > 4:
                if valoff + size > limit:
                    if first:
                        raise NotThisFormat("tiff value")
                    break
                end = max(end, valoff + size)
            tags[tag] = (typ, cnt, ent[8:12], valoff)
        first = False
        for tag in tags:
            typ, cnt, raw, valoff = tags[tag]
            if tag == 0x10F and typ == 2 and not make:
                make = rd(off + valoff, min(cnt, 32)) if cnt > 4 else raw[:cnt]
            elif tag == 0xC612:
                ext = "dng"
            elif tag in _SUB_IFD_TAGS and typ in (3, 4, 13):
                todo.extend((v, depth + 1) for v in values(typ, cnt, raw, valoff)[:16])
        for off_tag, len_tag in ((0x111, 0x117), (0x144, 0x145), (0x201, 0x202)):
            if off_tag in tags and len_tag in tags:
                offs = values(*tags[off_tag])
                lens = values(*tags[len_tag])
                for a, b in zip(offs, lens):
                    end = max(end, a + b)
        nxt = _u32(data[n * 12:n * 12 + 4], e)
        if nxt:
            todo.append((nxt, depth))
    if end > limit:
        return None, ext
    if ext == "tif":
        m = make.rstrip(b"\x00 ").upper()
        if m.startswith(b"NIKON"):
            ext = "nef"
        elif m.startswith(b"SONY"):
            ext = "arw"
    return end, ext
