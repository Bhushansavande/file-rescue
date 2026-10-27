"""कमांड लाइन इंटरफेस."""

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
        _out(f"स्रोत सापडला नाही: {a.source}")
        return 2
    total = os.path.getsize(a.source) if os.path.isfile(a.source) else 0
    last = {"mb": -1}

    def progress(pos):
        mb = pos // (50 * 1024 * 1024)
        if mb != last["mb"]:
            last["mb"] = mb
            if total:
                print(f"\r  स्कॅन: {pos * 100 // total}%", end="", file=sys.stderr, flush=True)
            else:
                print(f"\r  स्कॅन: {pos // (1024 * 1024):,} MB", end="", file=sys.stderr, flush=True)

    types = a.types.split(",") if a.types else None
    _out(f"स्कॅन सुरू: {a.source}  (फक्त वाचन, स्रोतमध्ये काहीही बदलले जात नाही)")
    res = carve(a.source, a.output, types=types, aligned=not a.no_align,
                keep_partial=not a.no_partial, progress=progress, log=_out)
    print(file=sys.stderr)
    full = sum(1 for r in res if r.complete)
    _out(f"\nपूर्ण: {full} फाइल्स, अर्धवट: {len(res) - full} फाइल्स -> {os.path.abspath(a.output)}")
    return 0


def cmd_types(_a):
    _out("समर्थित प्रकार:")
    for s in SIGNATURES:
        _out(f"  {s.name}")
    _out("\n--types उदाहरणे: jpg,png | photos | videos | music | apk | documents")
    return 0


def cmd_recyclebin(a):
    from . import recyclebin as rb

    dirs = [a.path] if a.path else rb.default_bin_dirs()
    if not dirs:
        _out("Recycle Bin फोल्डर सापडला नाही (हे फीचर Windows साठी आहे; किंवा --path द्या).")
        return 1
    items = []
    for d in dirs:
        items.extend(rb.list_items(d))
    if not items:
        _out("Recycle Bin मध्ये काहीही नाही.")
        return 0
    for it in items:
        status = "" if it.data_path else "  (डेटा नाही)"
        _out(f"{it.deleted_at:%Y-%m-%d %H:%M}  {it.size:>12,}  {it.original_path}{status}")
    if a.output:
        done = rb.restore(items, a.output)
        _out(f"\n{len(done)} आयटम्स कॉपी केले -> {os.path.abspath(a.output)}")
    else:
        _out("\nपरत मिळवण्यासाठी -o <फोल्डर> जोडा.")
    return 0


def cmd_android(a):
    from . import android

    ok, msg = android.check_device()
    _out(msg)
    if not ok:
        return 1
    paths = android.find_trashed()
    if not paths:
        _out("फोनमध्ये ट्रॅश फाइल्स सापडल्या नाहीत.")
        return 0
    for p in paths:
        name, exp = android.parse_trashed_name(os.path.basename(p))
        extra = f"  (आपोआप हटेल: {exp:%Y-%m-%d})" if exp else ""
        _out(f"  {name}{extra}")
    if a.output:
        pulled = android.pull_all(paths, a.output, log=_out)
        _out(f"\n{len(pulled)} फाइल्स काढल्या -> {os.path.abspath(a.output)}")
    else:
        _out("\nकाढण्यासाठी -o <फोल्डर> जोडा.")
    return 0


def cmd_image(a):
    """रॉ डिव्हाइसची सुरक्षित प्रत (इमेज) बनवा; वाचता न येणारे भाग शून्याने भरा."""
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
            print(f"\r  कॉपी: {done // (1024 * 1024):,} MB", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    _out(f"इमेज तयार: {a.dest} ({done:,} बाइट्स)")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="filerescue",
        description="File Rescue - डिलीट झालेले फोटो, व्हिडिओ, गाणी, APK परत मिळवा (मोफत, ओपन-सोर्स)")
    p.add_argument("--version", action="version", version=f"filerescue {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("carve", help="डिस्क/SD कार्ड/इमेजमधून फाइल्स शोधून काढा")
    c.add_argument("source", help=r"फाइल, इमेज किंवा डिव्हाइस (उदा. E:\ साठी \\.\E: , Linux: /dev/sdb1)")
    c.add_argument("-o", "--output", required=True, help="वेगळ्या ड्राइव्हवरील आउटपुट फोल्डर")
    c.add_argument("--types", help="उदा. jpg,png,mp3 किंवा photos,videos,music,apps")
    c.add_argument("--no-align", action="store_true", help="फाइल्स sector सीमेवर नसल्या तरी शोधा (हळू, जास्त चुकीचे हिट्स)")
    c.add_argument("--no-partial", action="store_true", help="अर्धवट फाइल्स सोडून द्या")
    c.set_defaults(fn=cmd_carve)

    t = sub.add_parser("types", help="समर्थित फाइल प्रकार दाखवा")
    t.set_defaults(fn=cmd_types)

    r = sub.add_parser("recyclebin", help="Windows Recycle Bin मधील फाइल्स यादी/परत मिळवा")
    r.add_argument("--path", help="विशिष्ट $Recycle.Bin\\<SID> फोल्डर")
    r.add_argument("-o", "--output", help="येथे कॉपी करा")
    r.set_defaults(fn=cmd_recyclebin)

    a = sub.add_parser("android", help="Android फोनमधील ट्रॅश फाइल्स (ADB द्वारे)")
    a.add_argument("-o", "--output", help="येथे काढा")
    a.set_defaults(fn=cmd_android)

    i = sub.add_parser("image", help="डिव्हाइसची सुरक्षित इमेज प्रत बनवा")
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
        _out("परवानगी नाही. रॉ डिव्हाइस वाचण्यासाठी Administrator (Windows) / sudo (Linux) लागते.")
        return 13
    except KeyboardInterrupt:
        _out("\nथांबवले.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
