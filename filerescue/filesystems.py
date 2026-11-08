"""Undelete with original names: read FAT12/16/32, exFAT and NTFS directly (read-only).

Carving finds file *contents* but loses names and folders. When a file is deleted, the
file system usually keeps its directory entry (name, size, first cluster) for a while.
These readers walk the directory structures, list the deleted entries and tell whether
the data area of each one is still free (likely intact) or has been reused (overwritten).
"""

import os
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache

from .carver import CHUNK, read_at

MAX_DEPTH = 40
MAX_ENTRIES = 2_000_000
MAX_DIR_BYTES = 64 * 1024 * 1024      # no real directory is bigger; a damaged size field must not hang us


@dataclass
class FsEntry:
    path: str                      # e.g. "DCIM/Camera/IMG_0001.jpg"
    size: int
    deleted: bool
    modified: object = None        # datetime or None
    status: str = "ok"             # ok | intact | overwritten | unsupported
    extents: list = field(default_factory=list)   # [(absolute_offset, length)]; offset None = zeros
    data: bytes = b""              # resident data (small NTFS files)
    note: str = ""


class UnsupportedFs(Exception):
    pass


def _sdt(y, mo, d, h=0, mi=0, s=0):
    try:
        return datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None


def fat_datetime(date, time_):
    if not date:
        return None
    return _sdt(1980 + (date >> 9), (date >> 5) & 15, date & 31,
                time_ >> 11, (time_ >> 5) & 63, (time_ & 31) * 2)


def exfat_datetime(v):
    if not v:
        return None
    return _sdt(1980 + (v >> 25), (v >> 21) & 15, (v >> 16) & 31,
                (v >> 11) & 31, (v >> 5) & 63, (v & 31) * 2)


def filetime(v):
    if not v:
        return None
    try:
        return datetime(1601, 1, 1) + timedelta(microseconds=v // 10)
    except OverflowError:
        return None


def _merge(extents):
    """Join neighbouring (offset, length) extents."""
    out = []
    for off, ln in extents:
        if out and off is not None and out[-1][0] is not None and out[-1][0] + out[-1][1] == off:
            out[-1] = (out[-1][0], out[-1][1] + ln)
        elif out and off is None and out[-1][0] is None:
            out[-1] = (None, out[-1][1] + ln)
        else:
            out.append((off, ln))
    return out


# ================================================================== FAT12 / FAT16 / FAT32

class Fat:
    label = "FAT"

    def __init__(self, f, base, boot):
        self.f, self.base = f, base
        b = boot
        self.bps = struct.unpack_from("<H", b, 11)[0]
        self.spc = b[13]
        self.reserved = struct.unpack_from("<H", b, 14)[0]
        self.nfats = b[16]
        self.root_entries = struct.unpack_from("<H", b, 17)[0]
        total16 = struct.unpack_from("<H", b, 19)[0]
        fat16 = struct.unpack_from("<H", b, 22)[0]
        total32 = struct.unpack_from("<I", b, 32)[0]
        fat32 = struct.unpack_from("<I", b, 36)[0]
        self.fat_sectors = fat16 or fat32
        total = total16 or total32
        self.root_cluster = struct.unpack_from("<I", b, 44)[0]
        root_sectors = (self.root_entries * 32 + self.bps - 1) // self.bps
        self.fat_start = self.reserved * self.bps
        self.root_start = (self.reserved + self.nfats * self.fat_sectors) * self.bps
        self.data_start = self.root_start + root_sectors * self.bps
        self.cs = self.bps * self.spc
        self.clusters = (total - self.reserved - self.nfats * self.fat_sectors - root_sectors) // self.spc
        if fat16 == 0 and self.root_entries == 0:      # FAT32 layout, whatever the cluster count
            self.bits, self.label = 32, "FAT32"
        elif self.clusters < 4085:
            self.bits, self.label = 12, "FAT12"
        elif self.clusters < 65525:
            self.bits, self.label = 16, "FAT16"
        else:
            self.bits, self.label = 32, "FAT32"
        self._sector = lru_cache(maxsize=512)(self._read_fat_sector)

    @staticmethod
    def looks_like(b):
        if len(b) < 512 or b[510:512] != b"\x55\xaa" or b[3:11] in (b"NTFS    ", b"EXFAT   "):
            return False
        bps = struct.unpack_from("<H", b, 11)[0]
        spc, nfats = b[13], b[16]
        return (bps in (512, 1024, 2048, 4096) and spc and not spc & (spc - 1)
                and nfats in (1, 2) and struct.unpack_from("<H", b, 14)[0] > 0
                and (struct.unpack_from("<H", b, 22)[0] or struct.unpack_from("<I", b, 36)[0]) > 0)

    def _read_fat_sector(self, idx):
        return read_at(self.f, self.base + self.fat_start + idx * self.bps, self.bps)

    def fat(self, n):
        """FAT entry of cluster n."""
        if self.bits == 32:
            idx, off = divmod(n * 4, self.bps)
            s = self._sector(idx)
            return struct.unpack_from("<I", s, off)[0] & 0x0FFFFFFF if len(s) >= off + 4 else 0x0FFFFFFF
        if self.bits == 16:
            idx, off = divmod(n * 2, self.bps)
            s = self._sector(idx)
            return struct.unpack_from("<H", s, off)[0] if len(s) >= off + 2 else 0xFFFF
        pos = n + n // 2
        idx, off = divmod(pos, self.bps)
        s = self._sector(idx) + self._sector(idx + 1)
        v = struct.unpack_from("<H", s, off)[0] if len(s) >= off + 2 else 0xFFF
        return v >> 4 if n & 1 else v & 0xFFF

    def _eoc(self):
        return {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.bits]

    def cluster_off(self, c):
        return self.base + self.data_start + (c - 2) * self.cs

    def chain(self, first):
        seen, c = set(), first
        while 2 <= c < self.clusters + 2 and c not in seen and len(seen) < 4_000_000:
            seen.add(c)
            yield c
            c = self.fat(c)
            if c >= self._eoc() or c == 0:
                break

    def read_dir(self, first, deleted_dir=False):
        if first == 0 and self.bits != 32:
            return read_at(self.f, self.base + self.root_start, self.root_entries * 32)
        if first == 0:
            first = self.root_cluster
        if deleted_dir:
            return read_at(self.f, self.cluster_off(first), self.cs) if 2 <= first < self.clusters + 2 else b""
        parts, total = [], 0
        for c in self.chain(first):
            parts.append(read_at(self.f, self.cluster_off(c), self.cs))
            total += self.cs
            if total >= MAX_DIR_BYTES:
                break
        return b"".join(parts)

    def entries(self):
        count = [0]
        yield from self._walk(0, "", False, 0, count)

    def _walk(self, first, prefix, parent_deleted, depth, count):
        if depth > MAX_DEPTH:
            return
        data = self.read_dir(first, parent_deleted)
        lfn = []
        for i in range(0, len(data) - 31, 32):
            e = data[i:i + 32]
            if e[0] == 0x00:
                break
            attr = e[11]
            deleted = e[0] == 0xE5
            if attr & 0x3F == 0x0F:                     # long file name piece
                chars = e[1:11] + e[14:26] + e[28:32]
                lfn.append((chars.decode("utf-16-le", "ignore"), e[13], deleted))
                continue
            parts, lfn = lfn, []
            if attr & 0x08:                             # volume label
                continue
            raw = bytes([0xE5]) if e[0] == 0x05 else e[0:1]
            short = raw + e[1:8]
            if deleted:
                short = b"_" + e[1:8]
            base_name = short.decode("cp437", "replace").rstrip()
            ext = e[8:11].decode("cp437", "replace").rstrip()
            if e[12] & 0x08:
                base_name = base_name.lower()
            if e[12] & 0x10:
                ext = ext.lower()
            name = base_name + ("." + ext if ext else "")
            if name in (".", ".."):
                continue
            if parts:
                checks = {p[1] for p in parts}
                ok = len(checks) == 1 and (deleted or checks == {self._checksum(e[0:11])})
                if ok:
                    text = "".join(p[0] for p in reversed(parts))
                    name = text.split("\x00")[0].split("￿")[0] or name
            name = name.replace("/", "_").replace("\\", "_")
            cl = struct.unpack_from("<H", e, 26)[0]
            if self.bits == 32:
                cl |= struct.unpack_from("<H", e, 20)[0] << 16
            size = struct.unpack_from("<I", e, 28)[0]
            mod = fat_datetime(struct.unpack_from("<H", e, 24)[0], struct.unpack_from("<H", e, 22)[0])
            is_deleted = deleted or parent_deleted
            path = prefix + name
            count[0] += 1
            if count[0] > MAX_ENTRIES:
                return
            if attr & 0x10:
                yield from self._walk(cl, path + "/", is_deleted, depth + 1, count)
                continue
            yield self._file_entry(path, cl, size, mod, is_deleted)

    @staticmethod
    def _checksum(name11):
        s = 0
        for c in name11:
            s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
        return s

    def _file_entry(self, path, cl, size, mod, deleted):
        if size == 0:
            return FsEntry(path, 0, deleted, mod, "intact" if deleted else "ok")
        n = (size + self.cs - 1) // self.cs
        if not 2 <= cl <= self.clusters + 1 or cl + n - 1 > self.clusters + 1:
            return FsEntry(path, size, deleted, mod, "overwritten" if deleted else "unsupported",
                           note="start cluster is invalid")
        if deleted:
            free = all(self.fat(c) == 0 for c in range(cl, cl + n))
            return FsEntry(path, size, True, mod, "intact" if free else "overwritten",
                           [(self.cluster_off(cl), size)])
        ext, left = [], size
        for c in self.chain(cl):
            take = min(self.cs, left)
            ext.append((self.cluster_off(c), take))
            left -= take
            if left <= 0:
                break
        status = "ok" if left <= 0 else "unsupported"
        return FsEntry(path, size, False, mod, status, _merge(ext))


# ================================================================== exFAT

class ExFat:
    label = "exFAT"

    def __init__(self, f, base, boot):
        self.f, self.base = f, base
        self.bps = 1 << boot[108]
        self.cs = self.bps << boot[109]
        self.fat_off = struct.unpack_from("<I", boot, 80)[0] * self.bps
        self.heap_off = struct.unpack_from("<I", boot, 88)[0] * self.bps
        self.clusters = struct.unpack_from("<I", boot, 92)[0]
        self.root = struct.unpack_from("<I", boot, 96)[0]
        self.bitmap = None
        if not 9 <= boot[108] <= 12 or boot[109] > 25 or not self.root:
            raise UnsupportedFs("bad exFAT boot sector")

    @staticmethod
    def looks_like(b):
        return b[3:11] == b"EXFAT   "

    def cluster_off(self, c):
        return self.base + self.heap_off + (c - 2) * self.cs

    def fat(self, c):
        raw = read_at(self.f, self.base + self.fat_off + c * 4, 4)
        return struct.unpack("<I", raw)[0] if len(raw) == 4 else 0xFFFFFFFF

    def chain(self, first, contiguous, length):
        n = (length + self.cs - 1) // self.cs
        if contiguous:
            yield from range(first, min(first + n, self.clusters + 2))
            return
        c, seen = first, set()
        while 2 <= c < self.clusters + 2 and c not in seen and len(seen) < 4_000_000:
            seen.add(c)
            yield c
            c = self.fat(c)
            if c >= 0xFFFFFFF8:
                break

    def is_free(self, c):
        if self.bitmap is None:
            return True
        i = c - 2
        return not (i >> 3 < len(self.bitmap) and self.bitmap[i >> 3] >> (i & 7) & 1)

    def entries(self):
        count = [0]
        yield from self._walk(self.root, 0, False, "", False, 0, count)

    def _read_dir(self, first, contiguous, length):
        length = min(length, MAX_DIR_BYTES) if length else 1 << 40
        parts, total = [], 0
        for c in self.chain(first, contiguous, length):
            parts.append(read_at(self.f, self.cluster_off(c), self.cs))
            total += self.cs
            if total >= MAX_DIR_BYTES:
                break
        return b"".join(parts)

    def _walk(self, first, length, contiguous, prefix, parent_deleted, depth, count):
        if depth > MAX_DEPTH:
            return
        data = self._read_dir(first, contiguous, length)
        i = 0
        while i + 32 <= len(data):
            t = data[i]
            if t == 0x00:
                break
            if t == 0x81 and depth == 0 and self.bitmap is None:       # allocation bitmap
                bc = struct.unpack_from("<I", data, i + 20)[0]
                bl = struct.unpack_from("<Q", data, i + 24)[0]
                if 2 <= bc < self.clusters + 2 and bl < (1 << 31):
                    self.bitmap = read_at(self.f, self.cluster_off(bc), bl)
            if t in (0x85, 0x05):
                sec = data[i + 1]
                block = data[i:i + 32 * (1 + sec)]
                if len(block) == 32 * (1 + sec) and sec >= 2 and block[32] in (0xC0, 0x40):
                    yield from self._file(block, prefix, parent_deleted or t == 0x05, depth, count)
                i += 32 * (1 + sec) if len(block) == 32 * (1 + sec) and sec >= 2 else 32
                continue
            i += 32

    def _file(self, block, prefix, deleted, depth, count):
        attr = struct.unpack_from("<H", block, 4)[0]
        mod = exfat_datetime(struct.unpack_from("<I", block, 12)[0])
        st = block[32:64]
        contiguous = bool(st[1] & 2)
        nlen = st[3]
        valid = struct.unpack_from("<Q", st, 8)[0]
        first = struct.unpack_from("<I", st, 20)[0]
        size = struct.unpack_from("<Q", st, 24)[0]
        name = b"".join(block[o + 2:o + 32] for o in range(64, len(block), 32)).decode(
            "utf-16-le", "ignore")[:nlen]
        name = name.replace("/", "_").replace("\\", "_") or "unnamed"
        path = prefix + name
        count[0] += 1
        if count[0] > MAX_ENTRIES:
            return
        if attr & 0x10:
            if 2 <= first < self.clusters + 2:
                yield from self._walk(first, size, contiguous or deleted, path + "/", deleted,
                                      depth + 1, count)
            return
        yield self._entry(path, first, size, valid, contiguous, deleted, mod)

    def _entry(self, path, first, size, valid, contiguous, deleted, mod):
        if size == 0:
            return FsEntry(path, 0, deleted, mod, "intact" if deleted else "ok")
        n = (size + self.cs - 1) // self.cs
        if not 2 <= first <= self.clusters + 1 or first + n - 1 > self.clusters + 1:
            return FsEntry(path, size, deleted, mod, "overwritten" if deleted else "unsupported",
                           note="start cluster is invalid")
        if deleted or contiguous:
            free = all(self.is_free(c) for c in range(first, first + n)) if deleted else True
            status = ("intact" if free else "overwritten") if deleted else "ok"
            return FsEntry(path, size, deleted, mod, status, [(self.cluster_off(first), size)])
        ext, left = [], size
        for c in self.chain(first, False, size):
            take = min(self.cs, left)
            ext.append((self.cluster_off(c), take))
            left -= take
            if left <= 0:
                break
        return FsEntry(path, size, False, mod, "ok" if left <= 0 else "unsupported", _merge(ext))


# ================================================================== NTFS

def _runs(buf, start):
    """Decode an NTFS run list into [(lcn or None, length_in_clusters)]."""
    out, pos, lcn = [], start, 0
    while pos < len(buf) and buf[pos]:
        hdr = buf[pos]
        ln, lo = hdr & 15, hdr >> 4
        pos += 1
        if ln == 0 or pos + ln + lo > len(buf):
            break
        length = int.from_bytes(buf[pos:pos + ln], "little")
        pos += ln
        if lo == 0:
            out.append((None, length))
        else:
            lcn += int.from_bytes(buf[pos:pos + lo], "little", signed=True)
            out.append((lcn, length))
        pos += lo
    return out


class Ntfs:
    label = "NTFS"

    def __init__(self, f, base, boot):
        self.f, self.base = f, base
        self.bps = struct.unpack_from("<H", boot, 11)[0]
        self.spc = boot[13]
        self.cs = self.bps * self.spc
        mft_lcn = struct.unpack_from("<Q", boot, 48)[0]
        rs = struct.unpack_from("<b", boot, 64)[0]
        self.rec = (1 << -rs) if rs < 0 else rs * self.cs
        if self.bps not in (512, 1024, 2048, 4096) or not self.spc or not 256 <= self.rec <= 65536:
            raise UnsupportedFs("bad NTFS boot sector")
        self.total_clusters = struct.unpack_from("<Q", boot, 40)[0] * self.bps // self.cs
        first = self._record_at(self.base + mft_lcn * self.cs)
        if first is None:
            raise UnsupportedFs("cannot read $MFT")
        info = self._attrs(first).get("data")
        if not info or info["resident"]:
            raise UnsupportedFs("cannot read $MFT")
        self.mft_runs, self.mft_size = info["runs"], info["size"]
        self._bitmap = None

    @staticmethod
    def looks_like(b):
        return b[3:11] == b"NTFS    "

    # --- low level
    def _record_at(self, off):
        raw = read_at(self.f, off, self.rec)
        return self._fixup(raw)

    def _fixup(self, raw):
        if len(raw) < self.rec or raw[:4] != b"FILE":
            return None
        uo, uc = struct.unpack_from("<HH", raw, 4)
        raw = bytearray(raw)
        if uo + uc * 2 > len(raw) or uc < 2:
            return None
        usn = raw[uo:uo + 2]
        for i in range(1, uc):
            end = i * 512
            if end > len(raw) or raw[end - 2:end] != usn:
                return None
            raw[end - 2:end] = raw[uo + 2 * i:uo + 2 * i + 2]
        return bytes(raw)

    def _runs_extents(self, runs):
        out = []
        for lcn, ln in runs:
            out.append((None if lcn is None else self.base + lcn * self.cs, ln * self.cs))
        return out

    def _read_extents(self, extents, offset, n):
        """Read n bytes at `offset` of a stream described by extents."""
        out, pos = [], 0
        for off, ln in extents:
            if offset < pos + ln and n > 0:
                skip = max(0, offset - pos)
                take = min(ln - skip, n)
                out.append(b"\0" * take if off is None else read_at(self.f, off + skip, take))
                offset, n = offset + take, n - take
            pos += ln
        return b"".join(out)

    def _attrs(self, rec):
        """Parse the attributes of one MFT record."""
        res = {"names": [], "std": None, "list": False}
        pos = struct.unpack_from("<H", rec, 20)[0]
        while pos + 16 <= len(rec):
            typ, ln = struct.unpack_from("<II", rec, pos)
            if typ == 0xFFFFFFFF or ln < 16 or pos + ln > len(rec):
                break
            nonres, nlen = rec[pos + 8], rec[pos + 9]
            flags = struct.unpack_from("<H", rec, pos + 12)[0]
            if nonres == 0:
                clen, coff = struct.unpack_from("<IH", rec, pos + 16)
                content = rec[pos + coff:pos + coff + clen]
            if typ == 0x10 and nonres == 0 and len(content) >= 16:
                res["std"] = filetime(struct.unpack_from("<Q", content, 8)[0])
            elif typ == 0x30 and nonres == 0 and len(content) >= 66:
                parent = struct.unpack_from("<Q", content, 0)[0] & 0xFFFFFFFFFFFF
                nsp, nl = content[65], content[64]
                name = content[66:66 + nl * 2].decode("utf-16-le", "ignore")
                res["names"].append((nsp, parent, name))
            elif typ == 0x20:
                res["list"] = True
            elif typ == 0x80 and nlen == 0 and "data" not in res:
                if nonres == 0:
                    res["data"] = {"resident": True, "content": content, "size": len(content), "flags": flags}
                else:
                    ro = struct.unpack_from("<H", rec, pos + 32)[0]
                    size = struct.unpack_from("<Q", rec, pos + 48)[0]
                    res["data"] = {"resident": False, "runs": _runs(rec[pos:pos + ln], ro),
                                   "size": size, "flags": flags}
            pos += ln
        return res

    def _records(self):
        extents = self._runs_extents(self.mft_runs)
        # a damaged size field must not make us read terabytes: stay inside the real extents
        total = min(self.mft_size, sum(ln for _, ln in extents)) // self.rec
        total = min(total, 100_000_000)
        step = max(1, CHUNK // self.rec)
        for first in range(0, total, step):
            n = min(step, total - first)
            blob = self._read_extents(extents, first * self.rec, n * self.rec)
            if not blob:
                break                       # ran past the end of the drive
            for k in range(n):
                raw = blob[k * self.rec:(k + 1) * self.rec]
                if len(raw) == self.rec and raw[:4] == b"FILE":
                    yield first + k, raw

    def _bitmap_bytes(self):
        if self._bitmap is None:
            self._bitmap = b""
            raw = self._record_at(self._mft_record_offset(6))
            d = self._attrs(raw).get("data") if raw else None
            if d and not d["resident"] and d["size"] < (512 << 20):
                self._bitmap = self._read_extents(self._runs_extents(d["runs"]), 0, d["size"])
        return self._bitmap

    def _mft_record_offset(self, n):
        pos = n * self.rec
        for off, ln in self._runs_extents(self.mft_runs):
            if pos < ln:
                return off + pos
            pos -= ln
        return 0

    def _all_free(self, runs):
        bm = self._bitmap_bytes()
        if not bm:
            return False
        for lcn, ln in runs:
            if lcn is None:
                continue
            for c in range(lcn, lcn + ln):
                if c < 0 or c >> 3 >= len(bm) or bm[c >> 3] >> (c & 7) & 1:
                    return False
        return True

    # --- main walk
    def entries(self):
        info = {}
        for n, raw in self._records():
            rec = self._fixup(raw)
            if rec is None:
                continue
            flags = struct.unpack_from("<H", rec, 22)[0]
            base_ref = struct.unpack_from("<Q", rec, 32)[0] & 0xFFFFFFFFFFFF
            if base_ref:
                continue                      # extension record of another file
            a = self._attrs(rec)
            if not a["names"]:
                continue
            good = [x for x in a["names"] if x[0] != 2] or a["names"]
            info[n] = (good[0][1], good[0][2], bool(flags & 2), bool(flags & 1), a)
        count = 0
        for n, (parent, name, is_dir, in_use, a) in info.items():
            if is_dir or n < 24 or (in_use and name.startswith("$")):
                continue
            path, ok = self._path(info, n)
            if not ok and in_use:
                continue
            count += 1
            if count > MAX_ENTRIES:
                return
            yield self._entry(path, a, not in_use)

    def _path(self, info, n):
        parts, cur, ok = [], n, True
        for _ in range(MAX_DEPTH):
            if cur == 5:
                break
            item = info.get(cur)
            if item is None or (cur != n and not item[2]):
                ok = False
                parts.append("_lost")
                break
            parts.append(item[1].replace("/", "_").replace("\\", "_"))
            cur = item[0]
        else:
            ok = False
        return "/".join(reversed(parts)), ok

    def _entry(self, path, a, deleted):
        mod = a["std"]
        d = a.get("data")
        if d is None:
            return FsEntry(path, 0, deleted, mod, "unsupported",
                           note="data is stored in an extension record" if a["list"] else "no data")
        if d["flags"] & 0x4001:
            return FsEntry(path, d["size"], deleted, mod, "unsupported",
                           note="compressed or encrypted file")
        if d["resident"]:
            return FsEntry(path, d["size"], deleted, mod, "intact" if deleted else "ok", data=d["content"])
        if a["list"]:
            note = "file has an attribute list: only the first fragments may be listed"
        else:
            note = ""
        ext, left = [], d["size"]
        for off, ln in self._runs_extents(d["runs"]):
            take = min(ln, left)
            ext.append((off, take))
            left -= take
        if deleted:
            free = left <= 0 and self._all_free(d["runs"])
            return FsEntry(path, d["size"], True, mod, "intact" if free else "overwritten", ext, note=note)
        return FsEntry(path, d["size"], False, mod, "ok" if left <= 0 and not note else "unsupported",
                       ext, note=note)


# ================================================================== ext2 / ext3 / ext4

class Ext4:
    """ext2/3/4. Deleting a file on ext3/ext4 clears its block map, so the old copy of the inode is
    looked up in the journal (jbd2), which keeps recent versions of metadata blocks. ext2 keeps the
    block map. Names come from live directories, journaled directory blocks and the unused space
    inside directory blocks. Works only while the journal has not been overwritten."""

    label = "ext4"
    JMAGIC = 0xC03B3998

    def __init__(self, f, base, boot):
        self.f, self.base = f, base
        sb = read_at(f, base + 1024, 1024)
        if len(sb) < 1024 or struct.unpack_from("<H", sb, 56)[0] != 0xEF53:
            raise UnsupportedFs("not ext")
        self.bs = 1024 << struct.unpack_from("<I", sb, 24)[0]
        if not 1024 <= self.bs <= 65536:
            raise UnsupportedFs("bad block size")
        self.first_data = struct.unpack_from("<I", sb, 20)[0]
        self.bpg = struct.unpack_from("<I", sb, 32)[0]
        self.ipg = struct.unpack_from("<I", sb, 40)[0]
        self.isize = struct.unpack_from("<H", sb, 88)[0] or 128
        self.compat = struct.unpack_from("<I", sb, 92)[0]
        self.incompat = struct.unpack_from("<I", sb, 96)[0]
        self.blocks = struct.unpack_from("<I", sb, 4)[0]
        if self.incompat & 0x80:
            self.blocks |= struct.unpack_from("<I", sb, 336)[0] << 32
        self.desc = (struct.unpack_from("<H", sb, 254)[0] or 64) if self.incompat & 0x80 else 32
        self.journal_ino = struct.unpack_from("<I", sb, 224)[0] if self.compat & 4 else 0
        if not self.bpg or not self.ipg or not 128 <= self.isize <= self.bs or self.desc < 32:
            raise UnsupportedFs("bad ext geometry")
        self.groups = (self.blocks - self.first_data + self.bpg - 1) // self.bpg
        if self.groups <= 0 or self.groups > 1_000_000:
            raise UnsupportedFs("bad ext geometry")
        self.label = "ext4" if self.incompat & 0x40 else ("ext3" if self.compat & 4 else "ext2")
        self._gd, self._bm, self._jv = {}, {}, None

    @staticmethod
    def looks_like(f, base=0):
        sb = read_at(f, base + 1024, 64)
        return len(sb) >= 58 and struct.unpack_from("<H", sb, 56)[0] == 0xEF53

    def blk(self, n):
        if n <= 0 or n >= self.blocks:
            return b"\0" * self.bs
        d = read_at(self.f, self.base + n * self.bs, self.bs)
        return d + b"\0" * (self.bs - len(d))

    def gdesc(self, g):
        if g not in self._gd:
            gdt = self.first_data + 1
            raw = read_at(self.f, self.base + gdt * self.bs + g * self.desc, self.desc)
            if len(raw) < self.desc:
                raise UnsupportedFs("group descriptor outside the drive")
            bb, it = struct.unpack_from("<I", raw, 0)[0], struct.unpack_from("<I", raw, 8)[0]
            if self.desc >= 64:
                bb |= struct.unpack_from("<I", raw, 32)[0] << 32
                it |= struct.unpack_from("<I", raw, 40)[0] << 32
            self._gd[g] = (bb, it)
        return self._gd[g]

    def inode_loc(self, ino):
        g, idx = divmod(ino - 1, self.ipg)
        if ino < 1 or g >= self.groups:
            return None
        return self.gdesc(g)[1] * self.bs + idx * self.isize

    def read_inode(self, ino):
        loc = self.inode_loc(ino)
        raw = read_at(self.f, self.base + loc, self.isize) if loc is not None else b""
        return raw if len(raw) >= 128 else None

    def block_free(self, n):
        g, i = divmod(n - self.first_data, self.bpg)
        if g < 0 or g >= self.groups:
            return False
        if g not in self._bm:
            self._bm[g] = self.blk(self.gdesc(g)[0])
        return not self._bm[g][i >> 3] >> (i & 7) & 1

    # ---- block maps
    def block_map(self, inode):
        """[(logical, physical, length)] or None when the map is unreadable or empty."""
        flags = struct.unpack_from("<I", inode, 32)[0]
        ib = inode[40:100]
        out = []
        ok = self._extents(ib, out, 0) if flags & 0x80000 else self._legacy(ib, out)
        return out if ok and out else None

    def _extents(self, node, out, depth):
        if len(node) < 12 or struct.unpack_from("<H", node, 0)[0] != 0xF30A or depth > 5:
            return False
        n, _mx, dep = struct.unpack_from("<HHH", node, 2)
        for i in range(min(n, (len(node) - 12) // 12)):
            e = node[12 + 12 * i:24 + 12 * i]
            if dep == 0:
                lb, ln, hi, lo = struct.unpack("<IHHI", e)
                out.append((lb, hi << 32 | lo, ln - 32768 if ln > 32768 else ln))
            else:
                lo, hi = struct.unpack_from("<IH", e, 4)
                if not self._extents(self.blk(hi << 32 | lo), out, depth + 1):
                    return False
            if len(out) > 1_000_000:
                return False
        return True

    def _legacy(self, ib, out):
        ptr, per = struct.unpack("<15I", ib), self.bs // 4

        def add(lb, pb):
            if not pb:
                return
            if out and out[-1][0] + out[-1][2] == lb and out[-1][1] + out[-1][2] == pb:
                out[-1] = (out[-1][0], out[-1][1], out[-1][2] + 1)
            else:
                out.append((lb, pb, 1))

        def walk(b, lvl, start):
            if not b or b >= self.blocks or len(out) > 1_000_000:
                return
            tab = struct.unpack("<%dI" % per, self.blk(b))
            for k, v in enumerate(tab):
                if lvl == 1:
                    add(start + k, v)
                else:
                    walk(v, lvl - 1, start + k * per ** (lvl - 1))

        for i in range(12):
            add(i, ptr[i])
        start = 12
        for lvl in (1, 2, 3):
            walk(ptr[11 + lvl], lvl, start)
            start += per ** lvl
        return True

    # ---- journal
    def journal(self):
        """{fs_block: [journal_block, ...]} oldest to newest, from the transactions still in the log."""
        if self._jv is not None:
            return self._jv
        self._jv = {}
        ino = self.read_inode(self.journal_ino) if self.journal_ino else None
        m = self.block_map(ino) if ino else None
        if not m:
            return self._jv
        jmap = {}
        for lb, pb, ln in m:
            for k in range(min(ln, 1 << 22)):
                jmap[lb + k] = pb + k
        sbj = self.blk(jmap[0]) if 0 in jmap else b""
        if len(sbj) < 64 or struct.unpack_from(">I", sbj, 0)[0] != self.JMAGIC \
                or struct.unpack_from(">I", sbj, 12)[0] != self.bs:
            return self._jv
        inc = struct.unpack_from(">I", sbj, 40)[0]
        v3, is64 = bool(inc & 0x10), bool(inc & 0x2)
        tag = 16 if v3 else (12 if is64 else 8)
        tail = 4 if inc & 0x18 else 0
        entries = []                                       # (sequence, position, fs_block, journal_block)
        j, last = 1, max(jmap) + 1
        while j < last:
            raw = self.blk(jmap[j]) if j in jmap else b""
            if struct.unpack_from(">II", raw, 0) != (self.JMAGIC, 1):
                j += 1
                continue
            seq = struct.unpack_from(">I", raw, 8)[0]
            pos, k = 12, 0
            while pos + tag <= self.bs - tail:
                nr = struct.unpack_from(">I", raw, pos)[0]
                flags = struct.unpack_from(">I", raw, pos + 4)[0] if v3 else struct.unpack_from(">H", raw, pos + 6)[0]
                if is64:
                    nr |= struct.unpack_from(">I", raw, pos + 8)[0] << 32
                k += 1
                if j + k in jmap and not flags & 4:
                    entries.append((seq, j + k, nr, jmap[j + k], bool(flags & 1)))
                pos += tag + (0 if flags & 2 else 16)
                if flags & 8:
                    break
            j += k + 1
        entries.sort(key=lambda e: (e[0] & 0xFFFFFFFF, e[1]))
        for _s, _p, nr, jb, esc in entries:
            self._jv.setdefault(nr, []).append((jb, esc))
        return self._jv

    def jblock(self, item):
        jb, esc = item
        d = self.blk(jb)
        return struct.pack(">I", self.JMAGIC) + d[4:] if esc else d

    def old_inodes(self, ino):
        """Journaled copies of this inode, newest first."""
        loc = self.inode_loc(ino)
        if loc is None:
            return
        fsblk, off = divmod(loc, self.bs)
        for item in reversed(self.journal().get(fsblk, [])):
            d = self.jblock(item)[off:off + self.isize]
            if len(d) >= 128:
                yield d

    # ---- directories
    def _dirents(self, data, slack):
        """(inode, name, file_type, deleted) from one directory block."""
        off = 0
        while off + 12 <= len(data):
            ino, rl, nl, ft = struct.unpack_from("<IHBB", data, off)
            if rl < 12 or rl % 4 or off + rl > len(data) or nl + 8 > rl:
                return
            if ino and nl:
                yield ino, data[off + 8:off + 8 + nl].decode("utf-8", "replace"), ft, False
            if slack:
                p = off + (8 + ((nl + 3) & ~3) if ino else 8)
                while p + 12 <= off + rl:
                    i2, r2, n2, f2 = struct.unpack_from("<IHBB", data, p)
                    if 0 < i2 <= self.ipg * self.groups and n2 and r2 % 4 == 0 and 8 + n2 <= r2 \
                            and p + r2 <= off + rl and 1 <= f2 <= 2:
                        yield i2, data[p + 8:p + 8 + n2].decode("utf-8", "replace"), f2, True
                        p += max(r2, 8 + ((n2 + 3) & ~3))
                    else:
                        p += 4
            off += rl

    def dir_blocks(self, ino):
        inode = self.read_inode(ino)
        if inode is None or struct.unpack_from("<H", inode, 0)[0] & 0xF000 != 0x4000:
            return []
        m = self.block_map(inode) or []
        return [pb + k for _l, pb, ln in m for k in range(min(ln, 4096))][:16384]

    def _fsize(self, rec):
        return struct.unpack_from("<I", rec, 4)[0] | struct.unpack_from("<I", rec, 108)[0] << 32

    def entries(self):
        names, dirs = {}, {2: ("", 2)}     # names: inode -> (name, parent); dirs: dir inode -> (name, parent)
        seen_dirs, queue = set(), [2]
        live = []
        while queue and len(seen_dirs) < 200000:
            d = queue.pop()
            if d in seen_dirs:
                continue
            seen_dirs.add(d)
            for b in self.dir_blocks(d):
                for ino, name, ft, deleted in self._dirents(self.blk(b), True):
                    if name in (".", ".."):
                        continue
                    if deleted:
                        names.setdefault(ino, (name, d))
                    elif ft == 2:
                        dirs[ino] = (name, d)
                        queue.append(ino)
                    elif ft == 1:
                        names[ino] = (name, d)
                        live.append(ino)
        # directory blocks that only exist in the journal give names of files deleted from them
        for fsblk, items in self.journal().items():
            for item in reversed(items):
                data = self.jblock(item)
                head = list(self._dirents(data[:48], False))[:2]
                if len(head) == 2 and head[0][1] == "." and head[1][1] == "..":
                    here = head[0][0]
                    for ino, name, ft, _d in self._dirents(data, True):
                        if name not in (".", "..") and ft == 1 and ino not in names:
                            names[ino] = (name, here)

        def dir_path(d, depth=0):
            if d == 2 or depth > MAX_DEPTH:
                return ""
            if d not in dirs:
                return "_lost_dir_%d/" % d
            name, parent = dirs[d]
            return dir_path(parent, depth + 1) + name.replace("/", "_") + "/"

        count = 0
        for ino in live:
            rec = self.read_inode(ino)
            if rec is None or struct.unpack_from("<H", rec, 0)[0] & 0xF000 != 0x8000:
                continue
            e = self._make(dir_path(names[ino][1]) + names[ino][0], rec, False)
            if e:
                yield e
        for g in range(self.groups):
            try:
                it = self.gdesc(g)[1]
            except UnsupportedFs:
                return
            if it <= 0 or it >= self.blocks:
                continue
            table = read_at(self.f, self.base + it * self.bs, min(self.ipg * self.isize, 256 << 20))
            for idx in range(len(table) // self.isize):
                rec = table[idx * self.isize:(idx + 1) * self.isize]
                if rec[20:24] == b"\0\0\0\0":              # dtime == 0: never deleted
                    continue
                ino = g * self.ipg + idx + 1
                count += 1
                if count > MAX_ENTRIES:
                    return
                e = self._deleted(ino, rec, names, dir_path)
                if e:
                    yield e

    def _deleted(self, ino, rec, names, dir_path):
        best = None
        for cand in [rec, *self.old_inodes(ino)]:
            mode = struct.unpack_from("<H", cand, 0)[0]
            if mode & 0xF000 != 0x8000:
                continue
            if self.block_map(cand) and self._fsize(cand):
                best = cand
                break
        if best is None or not self._fsize(best):
            return None
        if ino in names:
            name, parent = names[ino]
            path = dir_path(parent) + name
        else:
            path = "_unnamed/inode_%d" % ino
        return self._make(path, best, True)

    def _make(self, path, rec, deleted):
        size = self._fsize(rec)
        mtime = struct.unpack_from("<I", rec, 16)[0]
        try:
            mod = datetime.utcfromtimestamp(mtime) if mtime else None
        except (OverflowError, OSError, ValueError):
            mod = None
        m = self.block_map(rec)
        if not m:
            return None
        ext, left, free, prev = [], size, True, 0
        for lb, pb, ln in sorted(m):
            if lb * self.bs < size and lb > prev:          # hole = zeros
                ext.append((None, (lb - prev) * self.bs))
            take = min(ln * self.bs, max(0, size - lb * self.bs))
            if take <= 0:
                continue
            ext.append((self.base + pb * self.bs, take))
            prev = lb + ln
            if deleted and any(not self.block_free(pb + k) for k in range(0, ln, max(1, ln // 64))):
                free = False
        ext = _merge([(o, min(l, 1 << 50)) for o, l in ext])
        note = "" if not deleted else "found through the journal"
        status = ("intact" if free else "overwritten") if deleted else "ok"
        return FsEntry(path, size, deleted, mod, status, ext, note=note)


# ================================================================== detection and recovery

def _partitions(f):
    """Yield (label, byte_offset) for every partition found in an MBR or GPT disk."""
    s0 = read_at(f, 0, 512)
    if len(s0) < 512 or s0[510:512] != b"\x55\xaa":
        return
    found = []
    for i in range(4):
        e = s0[446 + 16 * i:462 + 16 * i]
        ptype, lba = e[4], struct.unpack_from("<I", e, 8)[0]
        if ptype == 0xEE:
            hdr = read_at(f, 512, 92)
            if hdr[:8] == b"EFI PART":
                tab = struct.unpack_from("<Q", hdr, 72)[0]
                n, size = struct.unpack_from("<II", hdr, 80)
                raw = read_at(f, tab * 512, min(n, 256) * size)
                for k in range(0, len(raw) - size + 1, size):
                    ent = raw[k:k + size]
                    if ent[:16] != b"\0" * 16:
                        found.append(struct.unpack_from("<Q", ent, 32)[0] * 512)
        elif ptype and ptype not in (0x05, 0x0F, 0x85) and lba:
            found.append(lba * 512)
    for n, off in enumerate(found, 1):
        yield f"partition {n}", off


def open_filesystems(f):
    """Return [(label, filesystem object)] for the volume(s) found in `f`."""
    cands = [("", 0)] + list(_partitions(f))
    out = []
    for label, base in cands:
        boot = read_at(f, base, 512)
        if len(boot) < 512:
            continue
        try:
            if Ntfs.looks_like(boot):
                out.append((label, Ntfs(f, base, boot)))
            elif ExFat.looks_like(boot):
                out.append((label, ExFat(f, base, boot)))
            elif Fat.looks_like(boot):
                out.append((label, Fat(f, base, boot)))
            elif Ext4.looks_like(f, base):
                out.append((label, Ext4(f, base, boot)))
        except (UnsupportedFs, struct.error, ValueError):
            continue
        if base == 0 and out:
            break
    return out


_RESERVED = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)},
             *{f"LPT{i}" for i in range(1, 10)}}


def safe_name(name):
    """One file or folder name that is safe to create on Windows, Linux and macOS."""
    name = "".join("_" if c in '<>:"|?*\\/' or ord(c) < 32 else c for c in name).strip(" .")
    if name in (".", ".."):
        return ""
    if name.split(".")[0].upper() in _RESERVED:        # CON, NUL, COM1 ... are devices on Windows
        name = "_" + name
    return name[:150]


def safe_path(path):
    parts = [safe_name(p) for p in path.split("/")]
    return [p for p in parts if p] or ["unnamed"]


def safe_join(root, parts):
    """Join under `root` and refuse anything that would end up outside it."""
    target = os.path.join(root, *parts)
    base = os.path.realpath(root)
    real = os.path.realpath(target)
    if os.path.commonpath([base, real]) != base:
        raise ValueError("refusing to write outside the output folder: " + target)
    return target


def copy_entry(f, entry, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as out:
        if entry.data:
            out.write(entry.data[:entry.size])
        left = entry.size - (len(entry.data) if entry.data else 0)
        for off, ln in entry.extents:
            if left <= 0:
                break
            ln = min(ln, left)
            done = 0
            while done < ln:
                n = min(CHUNK, ln - done)
                out.write(b"\0" * n if off is None else read_at(f, off + done, n))
                done += n
            left -= ln
    if entry.modified:
        try:
            ts = entry.modified.timestamp()
            os.utime(dest, (ts, ts))
        except (OverflowError, OSError, ValueError):
            pass
    return dest


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"
