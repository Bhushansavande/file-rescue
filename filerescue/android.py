"""Android: ADB द्वारे फोनमधील ट्रॅश/रिसायकल फोल्डर्समधील फाइल्स काढा.

Android 11+ मध्ये गॅलरी/Files ॲपमध्ये डिलीट केलेली फाइल प्रथम `.trashed-<वेळ>-<नाव>` या नावाने
फोनमध्येच (साधारण 30 दिवस) राहते. तिथून ती परत मिळवता येते. यासाठी फोनवर USB debugging चालू
असावे आणि लॅपटॉपवर `adb` (Android platform-tools, मोफत) इन्स्टॉल असावे.
"""

import os
import re
import shutil
import subprocess
from datetime import datetime

TRASHED_RE = re.compile(r"^\.trashed-(\d+)-(.+)$")

# या ठिकाणी शोधा (फोनच्या अंतर्गत स्टोरेजमध्ये, root शिवाय)
FIND_CMD = (
    "find /sdcard -type f \\( -name '.trashed-*' -o -ipath '*/.trash/*' "
    "-o -ipath '*/.recycle*/*' -o -ipath '*/.deleted/*' \\) 2>/dev/null"
)


def parse_trashed_name(filename):
    """'.trashed-1735689600-IMG_1.jpg' -> ('IMG_1.jpg', स्वयं-हटवण्याची वेळ)."""
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
    """(ok, संदेश)"""
    if not adb_path():
        return False, ("adb सापडले नाही. Android platform-tools डाउनलोड करा "
                       "(developer.android.com/tools/releases/platform-tools) आणि PATH मध्ये टाका.")
    out = _adb("devices").stdout.strip().splitlines()[1:]
    devices = [l.split()[0] for l in out if l.strip().endswith("device")]
    if not devices:
        return False, ("फोन सापडला नाही. USB debugging चालू करा, केबल जोडा आणि फोनवर "
                       "'Allow USB debugging' ला परवानगी द्या.")
    return True, f"फोन जोडलेला आहे: {devices[0]}"


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
                log(f"  मिळाले: {os.path.basename(target)}")
        elif log:
            log(f"  अयशस्वी: {p}")
    return pulled
