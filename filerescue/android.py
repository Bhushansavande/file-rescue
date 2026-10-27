"""Android: pull files from the phone's trash/recycle folders over ADB.

On Android 11+, a file deleted from the Gallery or Files app first becomes
`.trashed-<expiry>-<name>` on the phone itself and stays there for about 30 days.
It can be pulled from there. This needs USB debugging enabled on the phone and
`adb` (Android platform-tools, free) installed on the computer.
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
    "-o -ipath '*/.recycle*/*' -o -ipath '*/.deleted/*' \\) 2>/dev/null"
)


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


def pull_all(paths, outdir, log=None):
    os.makedirs(outdir, exist_ok=True)
    pulled = []
    for p in paths:
        name, _ = parse_trashed_name(os.path.basename(p))
        target = os.path.join(outdir, name)
        i = 1
        while os.path.exists(target):
            stem, ext = os.path.splitext(name)
            target = os.path.join(outdir, f"{stem}_{i}{ext}")
            i += 1
        r = _adb("pull", p, target, timeout=600)
        if r.returncode == 0:
            pulled.append(target)
            if log:
                log(f"  pulled: {os.path.basename(target)}")
        elif log:
            log(f"  failed: {p}")
    return pulled
