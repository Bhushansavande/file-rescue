"""Real-world recovery test: real media files on real FAT32 and ext4 file systems.

1. Generates real JPEG/PNG/GIF/WEBP/MP4/MOV/3GP/AVI/MP3/M4A/WAV/PDF/DOCX/XLSX/APK/ZIP files.
2. Copies them into a FAT32 image (mtools) and an ext4 image (debugfs), then deletes them all.
3. Runs the carver on each image and checks that every file comes back byte for byte.

Needs (Linux): ffmpeg, mtools, dosfstools, e2fsprogs and `pip install pillow`.
Usage: python scripts/realworld_test.py
"""

import hashlib
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from filerescue.carver import carve  # noqa: E402


# mkfs.*, mke2fs and debugfs live in sbin directories that normal users often lack in PATH
os.environ["PATH"] += os.pathsep + os.pathsep.join(["/usr/sbin", "/sbin"])


def run(cmd, **kw):
    """subprocess.run that always captures output and explains failures."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        tail = (r.stderr or r.stdout or "").strip()[-600:]
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:200]} :: {tail}")
    return r


def fail(msg):
    """Print an error that GitHub Actions also shows as an annotation, then exit."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=realworld_test::" + msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    sys.exit(msg)


def need(tool):
    if not shutil.which(tool):
        fail(f"Missing tool: {tool} (PATH={os.environ['PATH']})")


def make_media(d):
    from PIL import Image, ImageDraw, ImageFilter

    def photo(w, h, seed):
        r = random.Random(seed)
        im = Image.effect_noise((w, h), 60).convert("RGB")
        dr = ImageDraw.Draw(im)
        for _ in range(25):
            x, y = r.randrange(w), r.randrange(h)
            dr.ellipse([x, y, x + r.randrange(20, 200), y + r.randrange(20, 200)],
                       fill=tuple(r.randrange(256) for _ in range(3)))
        return im.filter(ImageFilter.GaussianBlur(1.2))

    p = lambda n: os.path.join(d, n)  # noqa: E731
    photo(1600, 1200, 1).save(p("photo_baseline.jpg"), "JPEG", quality=90)
    photo(1600, 1200, 1).save(p("photo_progressive.jpg"), "JPEG", quality=85, progressive=True)
    ex = Image.Exif()
    ex[0x010F] = "TestCam"
    photo(3000, 2200, 2).save(p("photo_exif.jpg"), "JPEG", quality=92, exif=ex.tobytes())
    photo(64, 48, 3).save(p("photo_tiny.jpg"), "JPEG", quality=70)
    photo(800, 600, 4).convert("L").save(p("photo_grey.jpg"), "JPEG", quality=80)
    photo(900, 700, 6).save(p("shot.png"))
    photo(300, 300, 7).convert("P").save(p("icon_palette.png"))
    frames = [photo(200, 150, 10 + i).convert("P") for i in range(6)]
    frames[0].save(p("anim.gif"), save_all=True, append_images=frames[1:], duration=100, loop=0)
    photo(1000, 800, 8).save(p("pic.webp"), quality=85)
    pages = [photo(600, 800, 20 + i) for i in range(3)]
    pages[0].save(p("doc.pdf"), save_all=True, append_images=pages[1:])
    rng = random.Random(42)
    blob = lambda n: bytes(rng.randrange(256) for _ in range(n))  # noqa: E731

    def zipit(name, entries):
        with zipfile.ZipFile(p(name), "w", zipfile.ZIP_DEFLATED) as z:
            for k, v in entries.items():
                z.writestr(k, v)

    zipit("report.docx", {"[Content_Types].xml": "<Types/>", "word/document.xml": "hello " * 2000})
    zipit("sheet.xlsx", {"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<w/>", "xl/s1.xml": "1,2;" * 3000})
    zipit("app.apk", {"AndroidManifest.xml": blob(900), "classes.dex": blob(300000),
                      "resources.arsc": blob(40000), "META-INF/CERT.RSA": blob(1200)})
    zipit("archive.zip", {"a.txt": "hello" * 5000, "b.bin": blob(50000)})

    def ff(*args):
        run(["ffmpeg", "-loglevel", "error", "-y", *args])

    v = ["-f", "lavfi", "-i"]
    ff(*v, "testsrc2=size=640x360:rate=25", *v, "sine=frequency=440", "-t", "6",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", p("clip.mp4"))
    ff(*v, "testsrc2=size=480x270:rate=25", *v, "sine=frequency=330", "-t", "4", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", p("clip_faststart.mp4"))
    ff(*v, "testsrc2=size=320x240:rate=25", *v, "sine=frequency=550", "-t", "3", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mov", p("clip.mov"))
    ff(*v, "testsrc2=size=176x144:rate=15", *v, "sine=frequency=220", "-t", "3", "-c:v", "mpeg4",
       "-c:a", "aac", "-f", "3gp", p("clip.3gp"))
    ff(*v, "testsrc2=size=320x240:rate=25", *v, "sine=frequency=500", "-t", "3", "-c:v", "mpeg4",
       "-c:a", "mp3", p("clip.avi"))
    ff(*v, "sine=frequency=880:duration=20", "-c:a", "libmp3lame", "-b:a", "128k", "-id3v2_version", "3",
       "-metadata", "title=Test Song", p("song128.mp3"))
    ff(*v, "sine=frequency=660:duration=15", "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "4",
       p("song320.mp3"))
    ff(*v, "sine=frequency=440:duration=10", "-c:a", "libmp3lame", "-ar", "22050", "-b:a", "64k",
       "-write_id3v1", "1", "-metadata", "title=Old Style", p("song_v1tag.mp3"))
    ff(*v, "sine=frequency=770:duration=8", "-c:a", "aac", "-f", "ipod", p("voice.m4a"))
    ff(*v, "sine=frequency=300:duration=5", "-c:a", "pcm_s16le", p("tone.wav"))


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def build_image(kind, img, media):
    run(["truncate", "-s", "256M", img])
    names = sorted(os.listdir(media))
    if kind == "FAT32":
        run(["mkfs.vfat", "-F", "32", "-s", "4", img])
        for n in names:
            run(["mcopy", "-i", img, os.path.join(media, n), "::" + n])
        for n in names:
            run(["mdel", "-i", img, "::" + n])
    else:
        run(["mke2fs", "-q", "-F", "-t", "ext4", "-b", "4096", img])
        cmds = "".join(f"write {os.path.join(media, n)} {n}\n" for n in names)
        cmds += "".join(f"rm {n}\n" for n in names)
        run(["debugfs", "-w", img], input=cmds)


def main():
    for t in ("ffmpeg", "mkfs.vfat", "mcopy", "mdel", "mke2fs", "debugfs", "truncate"):
        need(t)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        media = os.path.join(tmp, "media")
        os.makedirs(media)
        make_media(media)
        original = {n: sha(os.path.join(media, n)) for n in os.listdir(media)}
        for kind in ("FAT32", "ext4"):
            img = os.path.join(tmp, kind + ".img")
            out = os.path.join(tmp, "out_" + kind)
            build_image(kind, img, media)
            results = carve(img, out)
            got = {sha(r.path) for r in results}
            missing = [n for n, h in original.items() if h not in got]
            extra = [r for r in results if sha(r.path) not in set(original.values())]
            print(f"{kind}: {len(original) - len(missing)}/{len(original)} files recovered exactly, "
                  f"{len(extra)} unexpected extra files")
            for n in missing:
                print("   MISSING:", n)
            failures += len(missing) + len(extra)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        fail(f"{type(e).__name__}: {e}")
