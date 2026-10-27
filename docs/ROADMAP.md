# Roadmap

## Goal

Help anyone recover deleted photos, videos, music and APKs using **free and open-source** tools, and be honest about where recovery is not possible.

## Current status: v0.1 ✅

- [x] File carving: JPG, PNG, GIF, WEBP, MP4/MOV/3GP/M4A, MP3, WAV, AVI, PDF, ZIP, APK, DOCX/XLSX/PPTX
- [x] Exact length measurement (JPEG markers, MP4 boxes, MP3 frames, ZIP end record)
- [x] Windows Recycle Bin restore (original names)
- [x] Android `.trashed-*` / trash files over ADB
- [x] Safe image copy (`image`)
- [x] English documentation
- [x] Automated tests and CI (Linux, Windows, macOS)

## v0.2: usability

- [ ] **GUI** (Tkinter or Flutter desktop): pick a drive, pick types, press a button
- [ ] Thumbnail preview, save only selected files
- [ ] Automatic drive listing (Windows/Linux)
- [ ] Progress, time estimate, pause/resume
- [ ] Translatable interface (multi-language support)

## v0.3: more file types

- [ ] MP3 without ID3, AAC, FLAC, OGG, OPUS (WhatsApp audio)
- [ ] HEIC/HEIF (iPhone), RAW (CR2/NEF/ARW), BMP, TIFF
- [ ] MKV, WEBM, FLV; legacy Office (DOC/XLS)
- [ ] SQLite (WhatsApp `msgstore.db`)
- [ ] Duplicate removal (by hash); name files from EXIF/ID3 dates

## v0.4: file systems

- [ ] FAT32/exFAT directory entries, so recovery keeps **original names**
- [ ] NTFS MFT parsing (names, dates)
- [ ] Simple heuristics for fragmented files

## v0.5: Android improvements

- [ ] Flutter app that scans trash folders and backups on the phone (via Storage Access Framework)
- [ ] Wizard that checks Google Photos / WhatsApp backups
- [ ] Windows `.exe` and standalone releases for non-technical users

## Technical decisions

| Decision | Reason |
|---|---|
| Python, zero third-party dependencies | Easy to install, easy to learn, portable |
| Read-only access | The source data stays safe |
| 512-byte alignment | Fewer false hits, faster scans |
| MIT license | Anyone can use and improve it |

## Quality rules

- Every new file type ships with a test (a synthetic image whose recovered hash matches exactly).
- Strict validators against false hits; when in doubt, skip the candidate.
- The README always states limitations honestly.

## Milestones

| Version | Goal |
|---|---|
| v0.1 | CLI, main types, Recycle Bin, Android trash ✅ |
| v0.2 | GUI + preview |
| v0.3 | More types + duplicates |
| v0.4 | FAT/NTFS with original names |
| v1.0 | Stable, fully documented, Windows `.exe` release |
