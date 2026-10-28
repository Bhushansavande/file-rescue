"""Read the date a photo/video was taken, or a song's title and artist, from a recovered file.

Everything here is best effort: a damaged file simply returns no information.
"""

import struct
from datetime import datetime, timedelta

_MIN_YEAR, _MAX_YEAR = 1990, 2100


def _valid(dt):
    return dt if dt and _MIN_YEAR <= dt.year <= _MAX_YEAR else None


def _parse_exif_text(raw):
    raw = raw.split(b"\x00")[0].decode("ascii", "ignore").strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return _valid(datetime.strptime(raw[:19], fmt))
        except ValueError:
            pass
    return None


_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _read_ifd(buf, off, e):
    """Return {tag: (type, count, value_bytes_or_int)} for one TIFF IFD (or {})."""
    if off < 8 or off + 2 > len(buf):
        return {}
    n = struct.unpack_from(e + "H", buf, off)[0]
    out = {}
    for i in range(min(n, 500)):
        p = off + 2 + i * 12
        if p + 12 > len(buf):
            break
        tag, typ, cnt = struct.unpack_from(e + "HHI", buf, p)
        size = _SIZES.get(typ, 1) * cnt
        if size <= 4:
            val = buf[p + 8:p + 8 + size]
        else:
            vo = struct.unpack_from(e + "I", buf, p + 8)[0]
            val = buf[vo:vo + size]
        out[tag] = (typ, cnt, val)
    return out


def exif_date(tiff):
    """Capture date from a TIFF/EXIF block (as found in JPEG, HEIC, TIFF and RAW files)."""
    if tiff[:4] == b"II*\x00":
        e = "<"
    elif tiff[:4] == b"MM\x00*":
        e = ">"
    else:
        return None
    ifd0 = _read_ifd(tiff, struct.unpack_from(e + "I", tiff, 4)[0], e)
    sub = None
    if 0x8769 in ifd0 and len(ifd0[0x8769][2]) >= 4:
        sub = _read_ifd(tiff, struct.unpack_from(e + "I", ifd0[0x8769][2], 0)[0], e)
    for ifd, tags in ((sub, (0x9003, 0x9004)), (ifd0, (0x0132,))):
        for t in tags:
            if ifd and t in ifd:
                d = _parse_exif_text(ifd[t][2])
                if d:
                    return d
    return None


def _jpeg_date(data):
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        m = data[i + 1]
        if m == 0xDA or m == 0xD9:
            break
        ln = struct.unpack_from(">H", data, i + 2)[0]
        if m == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            return exif_date(data[i + 10:i + 2 + ln])
        i += 2 + ln
    return None


def _png_date(data):
    i = 8
    while i + 8 <= len(data):
        ln, typ = struct.unpack_from(">I4s", data, i)
        body = data[i + 8:i + 8 + ln]
        if typ == b"eXIf":
            d = exif_date(body)
            if d:
                return d
        elif typ == b"tIME" and ln == 7:
            y, mo, d, h, mi, s = struct.unpack(">HBBBBB", body)
            try:
                return _valid(datetime(y, mo, d, h, mi, s))
            except ValueError:
                return None
        elif typ in (b"IDAT", b"IEND"):
            break
        i += 12 + ln
    return None


def _heic_date(data):
    k = data.find(b"Exif\x00\x00")
    while k != -1:
        d = exif_date(data[k + 6:])
        if d:
            return d
        k = data.find(b"Exif\x00\x00", k + 1)
    return None


def _mp4_date(data):
    """creation_time of the 'mvhd' box (seconds since 1904-01-01)."""
    k = data.find(b"mvhd")
    if k == -1 or k + 24 > len(data):
        return None
    version = data[k + 4]
    if version == 1:
        secs = struct.unpack_from(">Q", data, k + 8)[0]
    else:
        secs = struct.unpack_from(">I", data, k + 8)[0]
    if not secs:
        return None
    try:
        return _valid(datetime(1904, 1, 1) + timedelta(seconds=secs))
    except OverflowError:
        return None


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _decode_id3(frame):
    if not frame:
        return ""
    enc, body = frame[0], frame[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    return body.decode(codec, "ignore").strip("\x00 ").split("\x00")[0].strip()


def id3_info(data):
    """Title / artist / year from an ID3v2.3 or 2.4 tag at the start of an MP3."""
    if data[:3] != b"ID3" or data[3] not in (3, 4):
        return {}
    ver, size = data[3], _syncsafe(data[6:10])
    pos, end, info = 10, min(10 + size, len(data)), {}
    while pos + 10 <= end:
        fid = data[pos:pos + 4]
        if not fid.strip(b"\x00"):
            break
        n = _syncsafe(data[pos + 4:pos + 8]) if ver == 4 else struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 10:pos + 10 + n]
        key = {b"TIT2": "title", b"TPE1": "artist", b"TDRC": "year", b"TYER": "year"}.get(fid)
        if key and key not in info:
            info[key] = _decode_id3(body)
        pos += 10 + n
    return info


def read_info(path, ext):
    """Return {'date': datetime|None, 'title': str, 'artist': str} for a recovered file."""
    info = {"date": None, "title": "", "artist": ""}
    try:
        with open(path, "rb") as f:
            data = f.read(8 * 1024 * 1024 if ext in ("tif", "cr2", "dng", "nef", "arw") else 1024 * 1024)
        if ext == "jpg":
            info["date"] = _jpeg_date(data)
        elif ext in ("tif", "cr2", "dng", "nef", "arw"):
            info["date"] = exif_date(data)
        elif ext == "png":
            info["date"] = _png_date(data)
        elif ext in ("heic", "avif"):
            info["date"] = _heic_date(data[:262144])
        elif ext in ("mp4", "mov", "3gp", "m4a"):
            info["date"] = _mp4_date(data)
        elif ext == "mp3":
            t = id3_info(data)
            info["title"], info["artist"] = t.get("title", ""), t.get("artist", "")
            y = t.get("year", "")[:4]
            if y.isdigit() and _MIN_YEAR <= int(y) <= _MAX_YEAR:
                info["year"] = int(y)
    except (OSError, struct.error, ValueError, IndexError):
        pass
    return info
