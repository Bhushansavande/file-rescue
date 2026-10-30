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


# ------------------------------------------------------------------ FLAC

def _crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def _crc16(data, c=0):
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x8005) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def _flac_frame_header(buf, i):
    """Parse a FLAC frame header at buf[i]. Returns (frame_or_sample_number, variable, key) or None."""
    if i + 6 > len(buf) or buf[i] != 0xFF or buf[i + 1] not in (0xF8, 0xF9):
        return None
    b2, b3 = buf[i + 2], buf[i + 3]
    bs, sr, ch, ssz = b2 >> 4, b2 & 15, b3 >> 4, (b3 >> 1) & 7
    if bs == 0 or sr == 15 or ch > 10 or ssz in (3, 7) or b3 & 1:
        return None
    first = buf[i + 4]
    if first < 0x80:
        n, ln = first, 1
    else:
        ln = 0
        while ln < 7 and first & (0x80 >> ln):
            ln += 1
        if ln < 2 or ln > 7:
            return None
        n = first & (0x7F >> ln)
        for k in range(1, ln):
            if i + 4 + k >= len(buf) or buf[i + 4 + k] & 0xC0 != 0x80:
                return None
            n = (n << 6) | (buf[i + 4 + k] & 0x3F)
    end = i + 4 + ln + (1 if bs == 6 else 2 if bs == 7 else 0) + (1 if sr == 12 else 2 if sr in (13, 14) else 0)
    if end >= len(buf) or _crc8(buf[i:end]) != buf[end]:
        return None
    return n, buf[i + 1] == 0xF9, (sr, ch, ssz)


def flac_walk(rd, off, limit):
    h = rd(off, 42)
    if len(h) < 42 or h[:4] != b"fLaC" or h[4] & 0x7F != 0 or h[5:8] != b"\x00\x00\x22":
        raise NotThisFormat("flac")
    min_fs, max_fs = int.from_bytes(h[12:15], "big"), int.from_bytes(h[15:18], "big")
    total = int.from_bytes(h[21:26], "big") & 0xFFFFFFFFF
    pos = off + 4
    for _ in range(64):                    # metadata blocks: 1 header byte (last flag + type), 3 length bytes
        bh = rd(pos, 4)
        if len(bh) < 4 or bh[0] & 0x7F == 127:
            raise NotThisFormat("flac metadata")
        pos += 4 + int.from_bytes(bh[1:4], "big")
        if bh[0] & 0x80:
            break
    else:
        raise NotThisFormat("flac metadata")
    chunk_max = max(max_fs * 2 if max_fs else 0, 65536 * 4)
    cur = _flac_frame_header(rd(pos, 32), 0)
    if cur is None:
        raise NotThisFormat("flac first frame")
    number, variable, key = cur
    frames = 1
    while pos - off < limit:
        buf = rd(pos + 1, chunk_max)
        i, nxt = 0, None
        while True:
            i = buf.find(b"\xff", i)
            if i == -1 or i > len(buf) - 8:
                break
            hdr = _flac_frame_header(buf, i)
            if hdr and hdr[2] == key and hdr[1] == variable and (variable or hdr[0] == number + 1) \
                    and (not min_fs or i + 1 >= min_fs):
                nxt = (i + 1, hdr[0])
                break
            i += 1
        if nxt is None:
            break
        pos += nxt[0]
        number = nxt[1]
        frames += 1
    # last frame: it ends where its own CRC-16 matches the two bytes that follow
    tail = rd(pos, (max_fs or 65536 * 2) + 4)
    c, end = 0, None
    for n in range(len(tail) - 2):
        c = _crc16(tail[n:n + 1], c)
        if n + 1 >= 12 and c == (tail[n + 1] << 8 | tail[n + 2]):
            end = n + 3
            break
    if end is None or frames < 2 or (total and not variable and frames < total // 65535):
        return None, "flac"
    length = pos + end - off
    return (length if length <= limit else None), "flac"


# ------------------------------------------------------------------ MP3 without an ID3 tag, AAC (ADTS)

_BR_V1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_BR_V2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
_SRT = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3raw_walk(rd, off, limit):
    """Bare MP3 (no ID3 tag): needs a long run of consistent frames to be believed."""
    pos, frames, first = off, 0, None
    buf, base = b"", off
    while pos - off < limit:
        if pos + 4 > base + len(buf):
            buf, base = rd(pos, 262144), pos
            if len(buf) < 4:
                break
        h = buf[pos - base:pos - base + 4]
        if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
            break
        ver, layer = (h[1] >> 3) & 3, (h[1] >> 1) & 3
        br_i, sr_i, pad = h[2] >> 4, (h[2] >> 2) & 3, (h[2] >> 1) & 1
        if ver == 1 or layer != 1 or br_i in (0, 15) or sr_i == 3:
            break
        if first is None:
            first = (ver, sr_i)
        elif (ver, sr_i) != first:
            break
        sr = _SRT[ver][sr_i]
        flen = (144 * _BR_V1[br_i] * 1000 // sr if ver == 3 else 72 * _BR_V2[br_i] * 1000 // sr) + pad
        if flen < 4:
            break
        pos += flen
        frames += 1
    if frames < 12:
        raise NotThisFormat("too few mp3 frames")
    if rd(pos, 3) == b"TAG":
        pos += 128
    return pos - off, "mp3"


def aac_walk(rd, off, limit):
    pos, frames, first = off, 0, None
    while pos - off < limit:
        h = rd(pos, 7)
        if len(h) < 7 or h[0] != 0xFF or h[1] & 0xF6 != 0xF0:
            break
        key = (h[2] >> 6, (h[2] >> 2) & 15, h[1] & 8)
        flen = ((h[3] & 3) << 11) | (h[4] << 3) | (h[5] >> 5)
        if key[1] > 12 or flen < 7 or (first is not None and key != first):
            break
        first = first or key
        pos += flen
        frames += 1
    if frames < 20:
        raise NotThisFormat("too few aac frames")
    return pos - off, "aac"


# ------------------------------------------------------------------ FLV

def flv_walk(rd, off, limit):
    h = rd(off, 13)
    if len(h) < 13 or h[:4] != b"FLV\x01" or h[4] & 0xFA or h[5:9] != b"\x00\x00\x00\x09" \
            or h[9:13] != b"\x00\x00\x00\x00":
        raise NotThisFormat("flv")
    pos, tags = off + 13, 0
    while pos - off < limit:
        t = rd(pos, 15)
        if len(t) < 11 or t[0] not in (8, 9, 18) or t[8:11] != b"\x00\x00\x00":
            break
        size = int.from_bytes(t[1:4], "big")
        end = pos + 11 + size
        prev = rd(end, 4)
        if len(prev) < 4 or int.from_bytes(prev, "big") != 11 + size:
            break
        pos = end + 4
        tags += 1
    if tags < 3:
        raise NotThisFormat("too few flv tags")
    return pos - off, "flv"


# ------------------------------------------------------------------ OLE2 (legacy DOC / XLS / PPT)

def ole_walk(rd, off, limit):
    h = rd(off, 512)
    if len(h) < 512 or h[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or h[28:30] != b"\xfe\xff":
        raise NotThisFormat("ole")
    shift = _u16(h[30:32])
    if shift not in (9, 12) or _u16(h[32:34]) != 6:
        raise NotThisFormat("ole sector size")
    ss = 1 << shift
    n_fat, dir_start = _u32(h[44:48]), _u32(h[48:52])
    if not 0 < n_fat < 100000:
        raise NotThisFormat("ole fat")
    difat = [_u32(h[76 + 4 * i:80 + 4 * i]) for i in range(109)]
    nxt, n_dif = _u32(h[68:72]), _u32(h[72:76])
    for _ in range(min(n_dif, 64)):
        if nxt >= 0xFFFFFFFA:
            break
        s = rd(off + (nxt + 1) * ss, ss)
        if len(s) < ss:
            break
        difat += [_u32(s[4 * i:4 * i + 4]) for i in range(ss // 4 - 1)]
        nxt = _u32(s[-4:])
    fat_secs = [s for s in difat if s < 0xFFFFFFFA][:n_fat]
    if not fat_secs:
        raise NotThisFormat("ole fat sectors")
    highest, fat = 0, {}
    for k, fs in enumerate(fat_secs):
        raw = rd(off + (fs + 1) * ss, ss)
        if len(raw) < ss:
            return None, "ole"
        for i in range(ss // 4):
            v = _u32(raw[4 * i:4 * i + 4])
            if v != 0xFFFFFFFF:
                idx = k * (ss // 4) + i
                highest = max(highest, idx)
                fat[idx] = v
    length = (highest + 2) * ss
    ext, names, s, seen = "ole", set(), dir_start, 0
    while s < 0xFFFFFFFA and seen < 32:                  # directory entries tell the document kind
        raw = rd(off + (s + 1) * ss, ss)
        for i in range(0, len(raw) - 127, 128):
            nl = _u16(raw[i + 64:i + 66])
            if 2 <= nl <= 64:
                names.add(raw[i:i + nl - 2].decode("utf-16-le", "ignore"))
        s, seen = fat.get(s, 0xFFFFFFFE), seen + 1
    if "WordDocument" in names:
        ext = "doc"
    elif "Workbook" in names or "Book" in names:
        ext = "xls"
    elif "PowerPoint Document" in names:
        ext = "ppt"
    elif any(n.startswith("__substg1.0_") or n.startswith("__properties_version") for n in names):
        ext = "msg"
    return (length if length <= limit else None), ext
