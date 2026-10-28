"""Command line interface."""

import argparse
import os
import sys

from . import __version__
from .signatures import SIGNATURES


def _out(msg):
    print(msg, flush=True)


def cmd_carve(a):
    from .carver import carve

    if not os.path.exists(a.source):
        _out(f"Source not found: {a.source}")
        return 2
    total = os.path.getsize(a.source) if os.path.isfile(a.source) else 0
    last = {"mb": -1}

    def progress(pos):
        mb = pos // (50 * 1024 * 1024)
        if mb != last["mb"]:
            last["mb"] = mb
            if total:
                print(f"\r  scanning: {pos * 100 // total}%", end="", file=sys.stderr, flush=True)
            else:
                print(f"\r  scanning: {pos // (1024 * 1024):,} MB", end="", file=sys.stderr, flush=True)

    types = a.types.split(",") if a.types else None
    _out(f"Scanning {a.source} (read-only; the source is never modified)")
    try:
        res = carve(a.source, a.output, types=types, aligned=not a.no_align,
                    keep_partial=not a.no_partial, progress=progress, log=_out)
    except ValueError as e:
        print(file=sys.stderr)
        _out(str(e))
        return 2
    print(file=sys.stderr)
    from . import organize as org

    removed = 0
    if res:
        res, removed = org.organize(res, a.output, dedupe=a.dedupe, date_names=a.date_names,
                                    by_month=a.by_month, log=_out)
        if not a.no_report:
            org.write_report(res, a.output, source=os.path.basename(a.source))
    full = sum(1 for r in res if r.complete)
    extra = f", {removed} duplicates removed" if removed else ""
    _out(f"\nDone: {full} complete, {len(res) - full} partial{extra} -> {os.path.abspath(a.output)}")
    if res and not a.no_report:
        _out(f"Open {os.path.join(os.path.abspath(a.output), 'report.html')} in a browser to look through the results.")
    return 0


def cmd_types(_a):
    _out("Supported types:")
    for s in SIGNATURES:
        _out(f"  {s.name}")
    _out("\nExamples for --types: jpg,png | photos | videos | music | apps | documents | "
         "raw | databases")
    return 0


def cmd_recyclebin(a):
    from . import recyclebin as rb

    dirs = [a.path] if a.path else rb.default_bin_dirs()
    if not dirs:
        _out("No Recycle Bin folder found (this feature is for Windows; or pass --path).")
        return 1
    items = []
    for d in dirs:
        items.extend(rb.list_items(d))
    if not items:
        _out("The Recycle Bin is empty.")
        return 0
    for it in items:
        status = "" if it.data_path else "  (data missing)"
        _out(f"{it.deleted_at:%Y-%m-%d %H:%M}  {it.size:>12,}  {it.original_path}{status}")
    if a.output:
        done = rb.restore(items, a.output)
        _out(f"\nCopied {len(done)} items -> {os.path.abspath(a.output)}")
    else:
        _out("\nAdd -o <folder> to copy them out.")
    return 0


def cmd_android(a):
    from . import android

    ok, msg = android.check_device()
    _out(msg)
    if not ok:
        return 1
    paths = android.find_trashed()
    if not paths:
        _out("No trashed files found on the phone.")
        return 0
    for p in paths:
        name, exp = android.parse_trashed_name(os.path.basename(p))
        extra = f"  (auto-deletes: {exp:%Y-%m-%d})" if exp else ""
        _out(f"  {name}{extra}")
    if a.output:
        pulled = android.pull_all(paths, a.output, log=_out)
        _out(f"\nPulled {len(pulled)} files -> {os.path.abspath(a.output)}")
    else:
        _out("\nAdd -o <folder> to pull them.")
    return 0


def cmd_image(a):
    """Make a safe copy (image) of a raw device; unreadable parts are zero-filled."""
    chunk = 1024 * 1024
    done = 0
    with open(a.source, "rb", buffering=0) as src, open(a.dest, "wb") as dst:
        while True:
            try:
                data = src.read(chunk)
            except OSError:
                data = b"\x00" * chunk
                src.seek(done + chunk)
            if not data:
                break
            dst.write(data)
            done += len(data)
            print(f"\r  copied: {done // (1024 * 1024):,} MB", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    _out(f"Image created: {a.dest} ({done:,} bytes)")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="filerescue",
        description="File Rescue: recover deleted photos, videos, music and APKs "
                    "(free and open source)")
    p.add_argument("--version", action="version", version=f"filerescue {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("carve", help="find and extract files from a disk, SD card or image")
    c.add_argument("source", help=r"file, image or device (Windows: \\.\E:  Linux: /dev/sdb1)")
    c.add_argument("-o", "--output", required=True,
                   help="output folder (use a different drive than the source)")
    c.add_argument("--types", help="e.g. jpg,png,mp3 or photos,videos,music,apps")
    c.add_argument("--no-align", action="store_true",
                   help="also look for files that do not start on a sector boundary "
                        "(slower, more false hits)")
    c.add_argument("--no-partial", action="store_true", help="skip partially recovered files")
    c.add_argument("--dedupe", action="store_true",
                   help="delete byte-identical duplicate files (the first copy is kept)")
    c.add_argument("--date-names", action="store_true",
                   help="name photos/videos by the date inside the file (EXIF, MP4) and "
                        "songs by 'Artist - Title' (ID3)")
    c.add_argument("--by-month", action="store_true",
                   help="sort files into YYYY-MM folders by their capture date")
    c.add_argument("--no-report", action="store_true",
                   help="do not write report.html and recovered.csv")
    c.set_defaults(fn=cmd_carve)

    t = sub.add_parser("types", help="list supported file types")
    t.set_defaults(fn=cmd_types)

    r = sub.add_parser("recyclebin", help="list or restore files from the Windows Recycle Bin")
    r.add_argument("--path", help="a specific $Recycle.Bin\\<SID> folder")
    r.add_argument("-o", "--output", help="copy items here")
    r.set_defaults(fn=cmd_recyclebin)

    a = sub.add_parser("android", help="pull trashed files from an Android phone (over ADB)")
    a.add_argument("-o", "--output", help="pull files here")
    a.set_defaults(fn=cmd_android)

    i = sub.add_parser("image", help="create a safe image copy of a device")
    i.add_argument("source")
    i.add_argument("dest")
    i.set_defaults(fn=cmd_image)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except PermissionError:
        _out("Permission denied. Reading a raw device needs Administrator (Windows) or sudo (Linux).")
        return 13
    except KeyboardInterrupt:
        _out("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
