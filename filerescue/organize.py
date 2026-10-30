"""After recovery: remove duplicates, name files by their date, sort by month, write a report."""

import csv
import hashlib
import html
import os
import re
from datetime import datetime
from urllib.parse import quote

from . import __version__
from .metadata import read_info

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
IMAGE_EXTS = {"jpg", "png", "gif", "webp", "bmp", "avif"}
VIDEO_EXTS = {"mp4", "mov", "3gp", "webm", "mkv", "ogv"}
AUDIO_EXTS = {"mp3", "m4a", "wav", "opus", "ogg"}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _clean(text, limit=80):
    return _BAD.sub("_", text).strip(" .")[:limit]


def _unique(folder, name):
    base, dot, ext = name.rpartition(".")
    candidate, n = name, 1
    while os.path.exists(os.path.join(folder, candidate)):
        n += 1
        candidate = f"{base}_{n}.{ext}"
    return candidate


def _remove_empty(folder):
    try:
        os.rmdir(folder)
    except OSError:
        pass


def organize(results, outdir, dedupe=False, date_names=False, by_month=False, log=None):
    """Update `results` in place (paths, dates, hashes). Returns (kept, duplicates_removed)."""
    kept, seen, removed = [], {}, 0
    for r in results:
        r.sha256 = sha256_of(r.path)
        info = read_info(r.path, r.ext)
        r.date, r.title, r.artist = info["date"], info["title"], info["artist"]
        if dedupe and r.complete:
            first = seen.get(r.sha256)
            if first is not None:
                os.remove(r.path)
                _remove_empty(os.path.dirname(r.path))
                removed += 1
                if log:
                    log(f"  duplicate removed: {os.path.relpath(r.path, outdir)}")
                continue
            seen[r.sha256] = r
        kept.append(r)

    for r in kept:
        folder, old = os.path.dirname(r.path), os.path.basename(r.path)
        new_name = old
        if date_names:
            tag = "_partial" if not r.complete else ""
            if r.date:
                new_name = f"{r.date:%Y-%m-%d_%H%M%S}_{r.offset:x}{tag}.{r.ext}"
            elif r.ext == "mp3" and (r.title or r.artist):
                label = " - ".join(x for x in (r.artist, r.title) if x)
                new_name = f"{_clean(label)}{tag}.{r.ext}"
        if by_month and os.path.basename(folder) in ("photos", "videos"):
            sub = f"{r.date:%Y-%m}" if r.date else "undated"
            folder = os.path.join(folder, sub)
        if new_name != old or folder != os.path.dirname(r.path):
            os.makedirs(folder, exist_ok=True)
            new_name = _unique(folder, new_name)
            dest = os.path.join(folder, new_name)
            old_folder = os.path.dirname(r.path)
            os.replace(r.path, dest)
            r.path = dest
            if old_folder != folder:
                _remove_empty(old_folder)
    return kept, removed


# ---------------------------------------------------------------- reports

def _cell(value):
    """Stop spreadsheets from running formulas hidden in names or tags (=, +, -, @ ...)."""
    value = str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def write_csv(results, outdir):
    path = os.path.join(outdir, "recovered.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "type", "size_bytes", "complete", "offset", "taken", "title", "artist",
                    "sha256"])
        for r in results:
            w.writerow([_cell(os.path.relpath(r.path, outdir).replace(os.sep, "/")), r.ext, r.size,
                        "yes" if r.complete else "partial", r.offset,
                        f"{r.date:%Y-%m-%d %H:%M:%S}" if r.date else "", _cell(r.title),
                        _cell(r.artist), r.sha256])
    return path


_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c2330;--mut:#667;--card:#f4f6fa;--bd:#d8dde6;--ac:#2b6cb0;--warn:#b7791f}
@media(prefers-color-scheme:dark){:root{--bg:#12161d;--fg:#e6eaf2;--mut:#9aa5b8;--card:#1b212c;--bd:#2c3442;--ac:#63b3ed;--warn:#ecc94b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}h1{margin:0 0 4px;font-size:24px}
.sub{color:var(--mut);margin:0 0 20px}.stats{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}
.stat,.btn{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:6px 12px;color:var(--fg)}
.btn{cursor:pointer;font:inherit}.btn.on{border-color:var(--ac);color:var(--ac)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.item{background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.media{height:150px;display:flex;align-items:center;justify-content:center;background:#0002;overflow:hidden}
.media img,.media video{width:100%;height:100%;object-fit:cover}.media audio{width:92%}
.media .ph{color:var(--mut);font-size:22px;font-weight:600;text-transform:uppercase}
.meta{padding:8px 10px;font-size:13px;word-break:break-all}.meta a{color:var(--ac);text-decoration:none}
.meta small{display:block;color:var(--mut)}.partial{color:var(--warn)}
"""


def _media_html(r, rel):
    href = quote(rel)
    if r.ext in IMAGE_EXTS:
        return f'<img loading="lazy" src="{href}" alt="{html.escape(os.path.basename(rel))}">'
    if r.ext in VIDEO_EXTS:
        return f'<video preload="metadata" controls src="{href}"></video>'
    if r.ext in AUDIO_EXTS:
        return f'<audio preload="none" controls src="{href}"></audio>'
    return f'<span class="ph">{html.escape(r.ext)}</span>'


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def write_report(results, outdir, source=""):
    """Write report.html (browse thumbnails, play audio/video) and recovered.csv."""
    write_csv(results, outdir)
    cards = []
    for r in results:
        rel = os.path.relpath(r.path, outdir).replace(os.sep, "/")
        top = rel.split("/")[0]
        label = " - ".join(x for x in (r.artist, r.title) if x) or os.path.basename(rel)
        extra = f"{r.date:%Y-%m-%d %H:%M}" if r.date else ""
        state = '' if r.complete else ' <span class="partial">partial: may be damaged</span>'
        cards.append(
            f'<div class="item" data-cat="{html.escape(top)}" data-partial="{0 if r.complete else 1}">'
            f'<div class="media">{_media_html(r, rel)}</div>'
            f'<div class="meta"><a href="{quote(rel)}">{html.escape(label)}</a>'
            f'<small>{r.ext.upper()} · {_size(r.size)}{" · " + extra if extra else ""}{state}</small></div></div>')
    tops = sorted({os.path.relpath(r.path, outdir).split(os.sep)[0] for r in results})
    complete = sum(1 for r in results if r.complete)
    buttons = ['<button class="btn on" data-f="all">All</button>'] + \
        [f'<button class="btn" data-f="{html.escape(t)}">{html.escape(t)}</button>' for t in tops] + \
        ['<button class="btn" data-f="__partial">Partial only</button>']
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Rescue report</title><style>{_CSS}</style></head><body><main>
<h1>File Rescue report</h1>
<p class="sub">{html.escape(source)} · {datetime.now():%Y-%m-%d %H:%M} · File Rescue {__version__}</p>
<div class="stats"><span class="stat">{len(results)} files</span><span class="stat">{complete} complete</span>
<span class="stat">{len(results) - complete} partial</span></div>
<div class="stats">{''.join(buttons)}</div>
<div class="grid">{''.join(cards) or '<p>No files were recovered.</p>'}</div>
<p class="sub">Open this file from the recovery folder so the previews can load. Partial files may be incomplete.
The same list is in recovered.csv.</p>
</main><script>
var b=document.querySelectorAll('[data-f]'),it=document.querySelectorAll('.item');
b.forEach(function(x){{x.onclick=function(){{b.forEach(function(y){{y.classList.remove('on')}});x.classList.add('on');
var f=x.getAttribute('data-f');it.forEach(function(i){{var s=f=='all'||(f=='__partial'?i.dataset.partial=='1':i.dataset.cat==f);
i.style.display=s?'':'none'}})}}}});
</script></body></html>"""
    path = os.path.join(outdir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
