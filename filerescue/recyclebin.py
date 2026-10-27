"""Windows Recycle Bin मधील फाइल्स मूळ नावासह परत मिळवा.

Recycle Bin मध्ये प्रत्येक डिलीट फाइलसाठी दोन फाइल्स असतात:
  $I......  = माहिती (मूळ पथ, आकार, डिलीट केल्याची वेळ)
  $R......  = प्रत्यक्ष फाइल (किंवा फोल्डर)
"""

import os
import shutil
import string
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class BinItem:
    original_path: str
    size: int
    deleted_at: datetime
    data_path: str  # $R... चा पथ (अस्तित्वात असल्यास)


def parse_i_file(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 24:
        raise ValueError("$I फाइल खूप लहान आहे")
    version = int.from_bytes(data[0:8], "little")
    size = int.from_bytes(data[8:16], "little")
    ft = int.from_bytes(data[16:24], "little")
    if version == 2:
        n = int.from_bytes(data[24:28], "little")
        raw = data[28:28 + n * 2]
    else:
        raw = data[24:24 + 520]
    name = raw.decode("utf-16-le", errors="ignore").split("\x00")[0]
    when = datetime(1601, 1, 1) + timedelta(microseconds=ft // 10)
    return name, size, when


def default_bin_dirs():
    """Windows वरील सर्व ड्राइव्ह्समधील $Recycle.Bin फोल्डर्स."""
    dirs = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\$Recycle.Bin"
        if os.path.isdir(root):
            try:
                for sid in os.listdir(root):
                    p = os.path.join(root, sid)
                    if os.path.isdir(p):
                        dirs.append(p)
            except OSError:
                pass
    return dirs


def list_items(bin_dir):
    items = []
    try:
        entries = os.listdir(bin_dir)
    except OSError:
        return items
    for e in entries:
        if not e.startswith("$I"):
            continue
        try:
            name, size, when = parse_i_file(os.path.join(bin_dir, e))
        except (ValueError, OSError):
            continue
        data = os.path.join(bin_dir, "$R" + e[2:])
        items.append(BinItem(name, size, when, data if os.path.exists(data) else ""))
    return sorted(items, key=lambda i: i.deleted_at, reverse=True)


def restore(items, outdir):
    """प्रत्येक आयटमची प्रत मूळ नावाने outdir मध्ये करा. परत: कॉपी केलेले पथ."""
    os.makedirs(outdir, exist_ok=True)
    done = []
    for it in items:
        if not it.data_path:
            continue
        base = os.path.basename(it.original_path.replace("\\", "/")) or os.path.basename(it.data_path)
        target = os.path.join(outdir, base)
        i = 1
        while os.path.exists(target):
            stem, ext = os.path.splitext(base)
            target = os.path.join(outdir, f"{stem}_{i}{ext}")
            i += 1
        if os.path.isdir(it.data_path):
            shutil.copytree(it.data_path, target)
        else:
            shutil.copy2(it.data_path, target)
        done.append(target)
    return done
