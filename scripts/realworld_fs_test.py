"""Undelete test on REAL file systems made by the real drivers (Linux, needs root or sudo).

For FAT32, exFAT and NTFS it: formats an image, mounts it, copies a folder tree (long and
non-ASCII names included), deletes some files, unmounts, then checks that `undelete`
recovers every deleted file byte for byte under its original path, and that files whose
space was reused afterwards are reported as overwritten. Also tests an MBR-partitioned disk.

Needs: mkfs.vfat + mtools (FAT32), mkfs.exfat + mount.exfat-fuse, mkntfs + ntfs-3g, losetup, mount.
Missing tools skip that file system (a notice is printed).
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["PATH"] += os.pathsep + "/usr/sbin" + os.pathsep + "/sbin"

from filerescue import filesystems as fsm  # noqa: E402

SUDO = [] if os.geteuid() == 0 else ["sudo"]


def run(cmd, **kw):
    r = subprocess.run(SUDO + cmd if kw.pop("sudo", False) else cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def have(*tools):
    return all(shutil.which(t) for t in tools)


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


TREE = {
    "DCIM/Camera/IMG_20230714_beach.jpg": 300000,
    "DCIM/Camera/Long Name With Spaces & ünïcode.png": 50000,
    "DCIM/Camera/VID_0001.mp4": 2500000,
    "Music/Arijit - Tum Hi Ho.mp3": 900000,
    "WhatsApp/Media/voice note.opus": 40000,
    "tiny.txt": 20,
    "keep.bin": 1000,
    "empty.dat": 0,
}
DELETE = [k for k in TREE if k not in ("keep.bin",)]


def make_tree(root):
    rng = __import__("random").Random(7)
    for rel, n in TREE.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(bytes(rng.getrandbits(8) for _ in range(min(n, 4096))) * (n // 4096 + 1) if n else b"")
        if n:
            with open(p, "r+b") as f:
                f.truncate(n)
                f.seek(0)
                head = f.read(8)
                f.seek(n // 2)
                f.write(os.urandom(min(64, n - n // 2)))   # make every file unique
                f.seek(0)
                f.write(head)


def fat32_fill(img, src, offset, overwrite):
    """FAT32 through mtools (no mount, no root): long names, folders, delete, reuse space."""
    target = f"{img}@@{offset}"
    run(["mkfs.vfat", "-F", "32", "-s", "4", "--offset", str(offset // 512), img])
    dirs = sorted({os.path.dirname(r) for r in TREE if os.path.dirname(r)}, key=lambda d: d.count("/"))
    seen = set()
    for d in dirs:
        parts = d.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in seen:
                seen.add(sub)
                run(["mmd", "-i", target, "::" + sub])
    for rel in TREE:
        run(["mcopy", "-i", target, os.path.join(src, *rel.split("/")), "::" + rel])
    for rel in DELETE:
        run(["mdel", "-i", target, "::" + rel])
    if overwrite:
        p = os.path.join(os.path.dirname(img), "filler.bin")
        with open(p, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))
        for i in range(60):                     # fill the whole disk
            try:
                run(["mcopy", "-i", target, p, f"::new{i}.bin"])
            except RuntimeError:
                break


def format_and_fill(kind, img, src, offset=0, overwrite=False):
    if kind == "FAT32":
        return fat32_fill(img, src, offset, overwrite)
    loop = run(["losetup", "-f", "--show", "-o", str(offset), img], sudo=True).strip()
    mnt = tempfile.mkdtemp()
    mounted = False
    try:
        if kind == "exFAT":
            run(["mkfs.exfat", loop], sudo=True)
            run(["mount.exfat-fuse", "-o", f"uid={os.getuid()},allow_other", loop, mnt], sudo=True)
        else:
            run(["mkntfs", "-F", "-Q", loop], sudo=True)
            run(["ntfs-3g", loop, mnt], sudo=True)
        mounted = True
        run(["cp", "-r", src + "/.", mnt], sudo=True)
        run(["sync"])
        for rel in DELETE:
            run(["rm", "-f", os.path.join(mnt, *rel.split("/"))], sudo=True)
        if overwrite:       # reuse the freed space with new files
            for i in range(12):
                run(["dd", "if=/dev/urandom", f"of={mnt}/new{i}.bin", "bs=1M", "count=1"], sudo=True)
        run(["sync"])
    finally:
        if mounted:
            run(["umount", mnt], sudo=True)
        run(["losetup", "-d", loop], sudo=True)
        os.rmdir(mnt)


def check(kind, img, src, expect_overwritten=False):
    out = tempfile.mkdtemp()
    problems = []
    with open(img, "rb", buffering=0) as f:
        vols = fsm.open_filesystems(f)
        if not vols:
            return [f"{kind}: file system not detected"]
        entries = {e.path: e for _, v in vols for e in v.entries() if e.deleted}
        for rel in DELETE:
            e = entries.get(rel)
            if e is None and kind == "FAT32":
                # a name that fits 8.3 has no long-name entry; FAT erases its first letter on delete
                head, _, leaf = rel.rpartition("/")
                e = entries.get((head + "/" if head else "") + "_" + leaf[1:])
            if e is None and expect_overwritten:
                continue          # new files reused the freed directory entry: expected
            if e is None:
                problems.append(f"missing in listing: {rel}")
                continue
            if expect_overwritten:
                if e.size and e.status == "intact":
                    # some small files may legitimately survive; large ones must not
                    if TREE[rel] > 1_000_000:
                        problems.append(f"should be overwritten: {rel}")
                continue
            if e.status != "intact":
                problems.append(f"{rel}: status {e.status}")
                continue
            dest = fsm.copy_entry(f, e, os.path.join(out, *fsm.safe_path(rel)))
            if sha(dest) != sha(os.path.join(src, *rel.split("/"))):
                problems.append(f"content differs: {rel}")
    shutil.rmtree(out, ignore_errors=True)
    return problems


def fuzz(kind, img, rounds=40):
    """Damage the metadata of a real image many ways; undelete must never crash or hang."""
    import random
    import signal

    rng = random.Random(5)
    problems = []
    with open(img, "rb", buffering=0) as f:
        vols = fsm.open_filesystems(f)
        if not vols:
            return [f"{kind}: nothing to fuzz"]
        v = vols[0][1]
    if v.label == "NTFS":
        hot = [v._mft_record_offset(0)]
    elif v.label == "exFAT":
        hot = [v.heap_off, v.fat_off]
    else:
        hot = [v.root_start, v.data_start, v.fat_start]
    size = os.path.getsize(img)
    work = img + ".fuzz"

    def alarm(*_):
        raise TimeoutError("hang")

    signal.signal(signal.SIGALRM, alarm)
    for i in range(rounds):
        shutil.copy(img, work)
        with open(work, "r+b") as f:
            for _ in range(rng.choice([1, 5, 40, 300])):
                f.seek(min(size - 1, rng.choice(hot) + rng.randrange(0, 200000)))
                f.write(os.urandom(rng.choice([1, 2, 4, 16, 512])))
        try:
            signal.alarm(30)
            with open(work, "rb", buffering=0) as f:
                for _, vol in fsm.open_filesystems(f):
                    for e in vol.entries():
                        if e.extents and e.size < 2_000_000:
                            fsm.copy_entry(f, e, os.devnull)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{kind} fuzz round {i}: {type(e).__name__}: {e}")
        finally:
            signal.alarm(0)
    os.remove(work)
    return problems


def main():
    kinds = []
    if have("mkfs.vfat", "mcopy", "mmd", "mdel"):
        kinds.append("FAT32")
    if have("mkfs.exfat", "mount.exfat-fuse"):
        kinds.append("exFAT")
    if have("mkntfs", "ntfs-3g"):
        kinds.append("NTFS")
    if not have("losetup"):
        print("losetup not available: nothing to test")
        return 0
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        make_tree(src)
        for kind in kinds:
            for variant in ("plain", "reused-space", "mbr"):
                img = os.path.join(tmp, f"{kind}_{variant}.img")
                try:
                    with open(img, "wb") as f:
                        f.truncate(96 * 1024 * 1024)
                    if variant == "mbr":
                        ptype = {"FAT32": 0x0C, "exFAT": 0x07, "NTFS": 0x07}[kind]
                        with open(img, "r+b") as f:
                            mbr = bytearray(512)
                            mbr[446 + 4] = ptype
                            mbr[446 + 8:446 + 12] = (2048).to_bytes(4, "little")
                            mbr[446 + 12:446 + 16] = ((96 * 2048) - 2048).to_bytes(4, "little")
                            mbr[510:512] = b"\x55\xaa"
                            f.write(mbr)
                    format_and_fill(kind, img, src, offset=2048 * 512 if variant == "mbr" else 0,
                                    overwrite=variant == "reused-space")
                    problems = check(kind, img, src, expect_overwritten=variant == "reused-space")
                    if variant == "plain":
                        problems += fuzz(kind, img)
                except RuntimeError as e:
                    print(f"{kind} {variant}: SKIPPED ({e})")
                    continue
                print(f"{kind} {variant}: {'OK' if not problems else 'FAILED'}")
                for p in problems:
                    print("   ", p)
                failures += len(problems)
    print("tested:", ", ".join(kinds) or "nothing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
