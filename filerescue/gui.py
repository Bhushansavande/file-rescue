"""A simple desktop window for File Rescue (Tkinter, ships with Python).

Pick a drive or image, pick an output folder, tick what you want back, press Scan.
"""

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading

from . import __version__


def list_drives():
    """Return [(label, path)] of drives the user can pick (best effort, never raises)."""
    drives = []
    try:
        if os.name == "nt":
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            kinds = {2: "removable", 3: "disk", 4: "network", 5: "cd"}
            for i in range(26):
                if mask >> i & 1:
                    letter = chr(65 + i)
                    kind = kinds.get(ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\"), "drive")
                    if kind not in ("cd", "network"):
                        drives.append((f"{letter}:  ({kind})", f"\\\\.\\{letter}:"))
        elif os.path.exists("/proc/partitions"):
            with open("/proc/partitions") as f:
                for line in f.read().splitlines()[2:]:
                    parts = line.split()
                    if len(parts) == 4 and not parts[3].startswith(("loop", "ram", "zram")):
                        gb = int(parts[2]) * 1024 / 1e9
                        drives.append((f"/dev/{parts[3]}  ({gb:.1f} GB)", f"/dev/{parts[3]}"))
    except Exception:  # noqa: BLE001  (a drive list must never break the window)
        pass
    return drives


# Only open file kinds that cannot run code. Recovered .exe/.apk/.bat/.js/.html come from an
# untrusted drive and must never be launched by a button click.
SAFE_OPEN = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "heic", "avif", "mp3", "m4a",
             "wav", "opus", "ogg", "mp4", "mov", "3gp", "mkv", "webm", "avi", "pdf", "txt"}


def can_open(path):
    return os.path.isdir(path) or path.rsplit(".", 1)[-1].lower() in SAFE_OPEN


def open_path(path):
    if not can_open(path):
        return
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        pass


KINDS = [("Photos", "photos"), ("Videos", "videos"), ("Music / voice notes", "music"),
         ("Apps (APK)", "apps"), ("Documents", "documents"), ("WhatsApp databases", "databases"),
         ("Camera RAW", "raw")]


def run(argv=None):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print("The window needs Tkinter. On Linux install it with: sudo apt install python3-tk")
        return 1
    return App(tk, ttk, filedialog, messagebox).start()


class App:
    def __init__(self, tk, ttk, filedialog, messagebox):
        self.tk, self.ttk, self.fd, self.mb = tk, ttk, filedialog, messagebox
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.items = {}          # tree id -> dict(path | entry)
        self.tmp = tempfile.mkdtemp(prefix="filerescue_preview_")
        self.photo = None
        self.entries_source = None

    # ---------------------------------------------------------------- layout
    def start(self, autoquit=None):
        tk, ttk = self.tk, self.ttk
        self.root = root = tk.Tk()
        root.title(f"File Rescue {__version__}")
        root.geometry("1040x720")
        root.minsize(900, 620)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="1. Where were the files?").grid(row=0, column=0, sticky="w")
        self.source = tk.StringVar()
        self.drive_box = ttk.Combobox(top, textvariable=self.source)
        self.drive_box.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Refresh", command=self.refresh_drives).grid(row=0, column=2)
        ttk.Button(top, text="Image file...", command=self.pick_image).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(top, text="2. Save recovered files to").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.output = tk.StringVar()
        ttk.Entry(top, textvariable=self.output).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Button(top, text="Browse...", command=self.pick_output).grid(row=1, column=2, pady=(8, 0))
        ttk.Label(top, text="Use a DIFFERENT drive than the one you are recovering from.",
                  foreground="#b7791f").grid(row=2, column=1, sticky="w", padx=6)

        opts = ttk.LabelFrame(root, text="3. What to look for", padding=8)
        opts.pack(fill="x", padx=10)
        self.mode = tk.StringVar(value="undelete")
        ttk.Radiobutton(opts, text="Deleted files with their names (FAT32, exFAT, NTFS: fast)",
                        variable=self.mode, value="undelete").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Radiobutton(opts, text="Deep scan by file content (any file system, slower, no names)",
                        variable=self.mode, value="carve").grid(row=1, column=0, columnspan=4, sticky="w")
        self.kinds = {}
        for i, (label, key) in enumerate(KINDS):
            v = tk.BooleanVar(value=key in ("photos", "videos", "music"))
            self.kinds[key] = v
            ttk.Checkbutton(opts, text=label, variable=v).grid(row=2 + i // 4, column=i % 4, sticky="w",
                                                              padx=(0, 18), pady=(6 if i < 4 else 0, 0))
        self.dedupe = tk.BooleanVar(value=True)
        self.datenames = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Remove duplicates (deep scan)", variable=self.dedupe).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(opts, text="Name by date / song title (deep scan)", variable=self.datenames).grid(
            row=4, column=2, columnspan=2, sticky="w", pady=(6, 0))

        bar = ttk.Frame(root, padding=(10, 8))
        bar.pack(fill="x")
        self.scan_btn = ttk.Button(bar, text="Scan", command=self.scan)
        self.scan_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop.set, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.recover_btn = ttk.Button(bar, text="Recover selected", command=self.recover_selected,
                                      state="disabled")
        self.recover_btn.pack(side="left")
        self.report_btn = ttk.Button(bar, text="Open report", command=self.open_report, state="disabled")
        self.report_btn.pack(side="right")
        self.folder_btn = ttk.Button(bar, text="Open folder", command=self.open_folder)
        self.folder_btn.pack(side="right", padx=6)
        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=10)
        self.status = tk.StringVar(value="Ready. Nothing is ever written to the drive you scan.")
        ttk.Label(root, textvariable=self.status, padding=(10, 4)).pack(fill="x")

        mid = ttk.PanedWindow(root, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        left = ttk.Frame(mid)
        cols = ("size", "state", "date")
        self.tree = ttk.Treeview(left, columns=cols, selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("size", text="Size")
        self.tree.heading("state", text="State")
        self.tree.heading("date", text="Date")
        self.tree.column("#0", width=330)
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("state", width=90)
        self.tree.column("date", width=120)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        mid.add(left, weight=3)
        right = ttk.Frame(mid, padding=(10, 0))
        self.preview = ttk.Label(right, text="Select a file to preview it", anchor="center", wraplength=260,
                                 justify="center")
        self.preview.pack(fill="both", expand=True)
        self.info = ttk.Label(right, text="", wraplength=260, justify="left")
        self.info.pack(fill="x")
        self.open_btn = ttk.Button(right, text="Open file", command=self.open_selected, state="disabled")
        self.open_btn.pack(pady=6)
        mid.add(right, weight=2)

        self.refresh_drives()
        root.after(120, self.pump)
        root.protocol("WM_DELETE_WINDOW", self.close)
        if autoquit:
            root.after(autoquit, self.close)
        root.mainloop()
        return 0

    # ---------------------------------------------------------------- small actions
    def refresh_drives(self):
        drives = list_drives()
        self.drive_map = {label: path for label, path in drives}
        self.drive_box["values"] = [label for label, _ in drives]

    def pick_image(self):
        p = self.fd.askopenfilename(title="Choose a disk image or device file")
        if p:
            self.source.set(p)

    def pick_output(self):
        p = self.fd.askdirectory(title="Choose the output folder")
        if p:
            self.output.set(p)

    def source_path(self):
        s = self.source.get().strip()
        return self.drive_map.get(s, s)

    def open_folder(self):
        if self.output.get() and os.path.isdir(self.output.get()):
            open_path(self.output.get())

    def open_report(self):
        p = os.path.join(self.output.get(), "report.html")
        if os.path.exists(p):
            open_path(p)

    def open_selected(self):
        sel = self.tree.selection()
        if sel and self.items.get(sel[0], {}).get("file"):
            path = self.items[sel[0]]["file"]
            if can_open(path):
                open_path(path)
            else:
                self.mb.showinfo("File Rescue", "For safety this kind of file is not opened from here. "
                                 "Use 'Open folder' and scan it with antivirus first.")
                open_path(os.path.dirname(path))

    def close(self):
        self.stop.set()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.root.destroy()

    def chosen_types(self):
        return [k for k, v in self.kinds.items() if v.get()]

    # ---------------------------------------------------------------- scanning
    def scan(self):
        src, out = self.source_path(), self.output.get().strip()
        if not src or not os.path.exists(src):
            self.mb.showwarning("File Rescue", "Choose a drive or an image file first.")
            return
        if not out:
            self.mb.showwarning("File Rescue", "Choose the folder where recovered files are saved.")
            return
        from .cli import _same_drive

        if _same_drive(src, out):
            self.mb.showwarning("File Rescue", "The output folder is on the drive you are recovering from. "
                                "Saving there could overwrite the files you want back. Choose another drive.")
            return
        types = self.chosen_types()
        if not types:
            self.mb.showwarning("File Rescue", "Tick at least one kind of file.")
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.stop.clear()
        self.scan_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.recover_btn.config(state="disabled")
        self.report_btn.config(state="disabled")
        self.progress.config(value=0)
        target = self._work_undelete if self.mode.get() == "undelete" else self._work_carve
        self.worker = threading.Thread(target=target, args=(src, out, types), daemon=True)
        self.worker.start()

    def _work_carve(self, src, out, types):
        from . import organize as org
        from .carver import Cancelled, carve

        total = os.path.getsize(src) if os.path.isfile(src) else 0
        try:
            self.q.put(("status", "Scanning (deep scan reads the whole drive)..."))
            res = carve(src, out, types=types, aligned=True,
                        progress=lambda pos: self.q.put(("progress", pos * 100 / total if total else 0)),
                        cancel=self.stop.is_set)
            self.q.put(("status", "Sorting results and writing the report..."))
            res, removed = org.organize(res, out, dedupe=self.dedupe.get(), date_names=self.datenames.get())
            org.write_report(res, out, os.path.basename(src))
            for r in res:
                self.q.put(("item", {"name": os.path.relpath(r.path, out), "size": r.size,
                                     "state": "complete" if r.complete else "partial",
                                     "date": f"{r.date:%Y-%m-%d}" if r.date else "", "file": r.path}))
            msg = f"Done: {len(res)} files recovered" + (f", {removed} duplicates removed" if removed else "")
            self.q.put(("done", msg + ". Open the report to browse them."))
        except Cancelled:
            self.q.put(("done", "Stopped."))
        except PermissionError:
            self.q.put(("error", "Permission denied. Run File Rescue as Administrator (Windows) or "
                                 "with sudo (Linux) to read a whole drive."))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"{type(e).__name__}: {e}"))

    def _work_undelete(self, src, out, types):
        from . import filesystems as fs
        from .signatures import allowed_extensions

        wanted = allowed_extensions(types)
        try:
            self.entries_source = src
            with open(src, "rb", buffering=0) as f:
                vols = fs.open_filesystems(f)
                if not vols:
                    self.q.put(("error", "No FAT32, exFAT or NTFS file system found here. "
                                         "Try the deep scan instead."))
                    return
                n = 0
                for label, vol in vols:
                    self.q.put(("status", f"Reading {vol.label} directory structure..."))
                    for e in vol.entries():
                        if self.stop.is_set():
                            self.q.put(("done", "Stopped."))
                            return
                        if not e.deleted:
                            continue
                        ext = e.path.rsplit(".", 1)[-1].lower() if "." in e.path else ""
                        if wanted is not None and ext not in wanted and not (
                                "databases" in types and ext in ("db", "sqlite", "crypt14", "crypt15")):
                            continue
                        n += 1
                        self.q.put(("item", {"name": e.path, "size": e.size, "state": e.status,
                                             "date": f"{e.modified:%Y-%m-%d}" if e.modified else "",
                                             "entry": e, "label": label}))
            self.q.put(("done", f"Found {n} deleted files. 'intact' ones are very likely recoverable. "
                                "Select files and press Recover selected."))
        except PermissionError:
            self.q.put(("error", "Permission denied. Run File Rescue as Administrator (Windows) or "
                                 "with sudo (Linux) to read a whole drive."))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"{type(e).__name__}: {e}"))

    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "progress":
                    self.progress.config(value=val)
                elif kind == "status":
                    self.status.set(val)
                elif kind == "item":
                    iid = self.tree.insert("", "end", text=val["name"],
                                           values=(f"{val['size']:,}", val["state"], val["date"]))
                    self.items[iid] = val
                elif kind in ("done", "error"):
                    self.status.set(val)
                    self.progress.config(value=100 if kind == "done" else 0)
                    self.scan_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    if self.mode.get() == "undelete" and self.items:
                        self.recover_btn.config(state="normal")
                    if os.path.exists(os.path.join(self.output.get(), "report.html")):
                        self.report_btn.config(state="normal")
                    if kind == "error":
                        self.mb.showerror("File Rescue", val)
        except queue.Empty:
            pass
        self.root.after(120, self.pump)

    # ---------------------------------------------------------------- recover + preview
    def recover_selected(self):
        from . import filesystems as fs

        sel = [self.items[i] for i in (self.tree.selection() or self.tree.get_children()) if i in self.items]
        out = self.output.get().strip()
        if not sel or not out:
            return
        copied = skipped = 0
        with open(self.entries_source, "rb", buffering=0) as f:
            for it in sel:
                e = it["entry"]
                if e.status != "intact":
                    skipped += 1
                    continue
                top = [it["label"].replace(" ", "_")] if it["label"] else []
                fs.copy_entry(f, e, fs.unique_path(os.path.join(out, *top, *fs.safe_path(e.path))))
                copied += 1
        msg = f"Recovered {copied} files to {out}."
        if skipped:
            msg += f" Skipped {skipped} whose space was overwritten."
        self.status.set(msg)

    def on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.items:
            return
        it = self.items[sel[0]]
        path = it.get("file")
        if not path and it.get("entry") is not None and it["entry"].status == "intact" \
                and it["entry"].size < 40 * 1024 * 1024:
            from . import filesystems as fs
            path = os.path.join(self.tmp, "preview_" + fs.safe_path(it["name"])[-1])
            try:
                with open(self.entries_source, "rb", buffering=0) as f:
                    fs.copy_entry(f, it["entry"], path)
            except OSError:
                path = None
        it["file"] = path if path and os.path.exists(path) else None
        self.open_btn.config(state="normal" if it["file"] else "disabled")
        self.info.config(text=f"{it['name']}\n{it['size']:,} bytes · {it['state']}"
                              + (f" · {it['date']}" if it["date"] else ""))
        self.show_preview(it["file"])

    def show_preview(self, path):
        self.photo = None
        text = "No preview for this type"
        if path:
            ext = path.rsplit(".", 1)[-1].lower()
            if ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "heic", "avif"):
                try:
                    from PIL import Image, ImageTk
                    Image.MAX_IMAGE_PIXELS = 50_000_000     # refuse decompression bombs
                    if ext in ("heic", "avif"):
                        try:
                            import pillow_heif
                            pillow_heif.register_heif_opener()
                        except ImportError:
                            pass
                    with Image.open(path) as im:
                        im.thumbnail((340, 340))
                        self.photo = ImageTk.PhotoImage(im.convert("RGB"))
                except Exception:  # noqa: BLE001
                    if ext in ("png", "gif"):
                        try:
                            self.photo = self.tk.PhotoImage(file=path)
                        except Exception:  # noqa: BLE001
                            pass
                    text = "Cannot preview (damaged or unsupported file)" if not self.photo else text
            elif ext in ("mp3", "m4a", "wav", "opus", "ogg"):
                text = "Audio file: press Open file to play it"
            elif ext in ("mp4", "mov", "3gp", "mkv", "webm", "avi"):
                text = "Video file: press Open file to play it"
        if self.photo:
            self.preview.config(image=self.photo, text="")
        else:
            self.preview.config(image="", text=text)
