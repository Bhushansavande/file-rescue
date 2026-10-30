"""Android: find and copy files that are deleted but still on the phone.

Three places keep such files without any root access:

* trash: on Android 11+ a file deleted from the Gallery or Files app first becomes
  `.trashed-<expiry>-<name>` and stays about 30 days (gallery apps use `.Trash` folders too);
* WhatsApp statuses: the app keeps viewed statuses in a hidden `.Statuses` folder, but
  only for about 24 hours;
* thumbnails: small previews of photos in `.thumbnails` folders often outlive the photo.

Two ways to reach them: over USB with `adb` (Android platform-tools, free) from a computer,
or `--local` when File Rescue itself runs on the phone (Termux).
"""

import os
import re
import shutil
import subprocess
from datetime import datetime

TRASHED_RE = re.compile(r"^\.trashed-(\d+)-(.+)$")

# Look in these places on internal storage (no root needed)
FIND_CMD = (
    "find /sdcard -type f \\( -name '.trashed-*' -o -ipath '*/.trash/*' "
    "-o -ipath '*/.recycle*/*' -o -ipath '*/.deleted/*' "
    "-o -ipath '*/.statuses/*' -o -ipath '*/.thumbnails/*' \\) 2>/dev/null"
)

KINDS = ("trash", "status", "thumbnails")
KIND_FOLDER = {"trash": "", "status": "whatsapp-status", "thumbnails": "thumbnails"}


def classify(path):
    """Which kind of recoverable file is this path? ('trash', 'status', 'thumbnails' or None)"""
    p = path.replace("\\", "/")
    low = p.lower()
    base = os.path.basename(p)
    if TRASHED_RE.match(base) or "/.trash/" in low or "/.recycle" in low or "/.deleted/" in low:
        return "trash"
    if "/.statuses/" in low:
        return "status" if not base.startswith(".nomedia") else None
    if "/.thumbnails/" in low:
        return "thumbnails"
    return None


def scan_local(root="/storage/emulated/0", kinds=KINDS):
    """Walk the phone's own storage (for File Rescue running in Termux). Returns [path]."""
    found = []
    for folder, dirs, files in os.walk(root, onerror=lambda e: None):
        for name in files:
            full = os.path.join(folder, name)
            kind = classify(full)
            if kind in kinds and os.path.isfile(full) and not os.path.islink(full):
                found.append(full)
    return sorted(found)


def local_root():
    for r in ("/storage/emulated/0", "/sdcard"):
        if os.path.isdir(r):
            return r
    return None


def copy_local(paths, outdir, log=None):
    """Copy files found by scan_local() into outdir (same layout as pull_all)."""
    return _place(paths, outdir, lambda src, dst: shutil.copy2(src, dst) or True, log)


def parse_trashed_name(filename):
    """'.trashed-1735689600-IMG_1.jpg' -> ('IMG_1.jpg', auto-delete time)."""
    m = TRASHED_RE.match(filename)
    if not m:
        return filename.lstrip("."), None
    try:
        expiry = datetime.fromtimestamp(int(m.group(1)))
    except (OverflowError, OSError, ValueError):
        expiry = None
    return m.group(2), expiry


def adb_path():
    return shutil.which("adb")


def _adb(*args, timeout=120):
    return subprocess.run([adb_path(), *args], capture_output=True, text=True, timeout=timeout)


def check_device():
    """Returns (ok, message)."""
    if not adb_path():
        return False, ("adb not found. Download Android platform-tools "
                       "(developer.android.com/tools/releases/platform-tools) and add it to PATH.")
    out = _adb("devices").stdout.strip().splitlines()[1:]
    devices = [l.split()[0] for l in out if l.strip().endswith("device")]
    if not devices:
        return False, ("No phone found. Enable USB debugging, connect the cable, and tap "
                       "'Allow USB debugging' on the phone.")
    return True, f"Phone connected: {devices[0]}"


def find_trashed():
    r = _adb("shell", FIND_CMD)
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def _place(paths, outdir, fetch, log=None):
    pulled = []
    for p in paths:
        from .filesystems import safe_name

        name, _ = parse_trashed_name(os.path.basename(p))
        name = safe_name(name) or "unnamed"
        folder = os.path.join(outdir, KIND_FOLDER.get(classify(p) or "trash", ""))
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, name)
        i = 1
        while os.path.exists(target):
            stem, ext = os.path.splitext(name)
            target = os.path.join(folder, f"{stem}_{i}{ext}")
            i += 1
        try:
            ok = fetch(p, target)
        except OSError:
            ok = False
        if ok:
            pulled.append(target)
            if log:
                log(f"  copied: {os.path.relpath(target, outdir)}")
        elif log:
            log(f"  failed: {p}")
    return pulled


def pull_all(paths, outdir, log=None):
    os.makedirs(outdir, exist_ok=True)
    return _place(paths, outdir, lambda src, dst: _adb("pull", src, dst, timeout=600).returncode == 0, log)
