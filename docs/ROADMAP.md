# Roadmap

## Goal

Help anyone recover deleted photos, videos, music and APKs using **free and open-source** tools, and be honest about where recovery is not possible.

## Done

### v0.1
- [x] File carving: JPG, PNG, GIF, WEBP, MP4/MOV/3GP/M4A, MP3, WAV, AVI, PDF, ZIP, APK, DOCX/XLSX/PPTX
- [x] Exact length measurement per format, partial files flagged
- [x] Windows Recycle Bin restore with original names
- [x] Android trash over ADB, safe image copy, CI on Linux/Windows/macOS

### v0.2
- [x] Desktop window: drive picker, type selection, progress, thumbnail preview, recover selected
- [x] `undelete`: original names and folders on FAT12/16/32, exFAT and NTFS, MBR/GPT disks, overwritten-space detection
- [x] More formats: HEIC, AVIF, Opus/Ogg (WhatsApp voice notes), MKV, WebM, SQLite (WhatsApp databases), BMP, TIFF, Canon/Nikon/Sony/DNG RAW, 7z
- [x] HTML report with previews, CSV list, duplicate removal, EXIF/MP4/ID3 date naming
- [x] Android: WhatsApp statuses, thumbnails and trash, over ADB or directly on the phone (Termux)
- [x] Flutter Android app (builds in CI; not yet tested on many phones)
- [x] Tests on real NTFS and exFAT volumes created by the real drivers

## Next

### v0.3: harder recovery
- [ ] Fragmented files: join pieces using file-system hints and format checks
- [ ] ext4 and APFS undelete (names via journal / snapshots)
- [ ] MP3 without ID3, AAC, FLAC, FLV, legacy Office (DOC/XLS)
- [ ] Resume an interrupted scan

### v0.4: more apps
- [ ] Decrypt WhatsApp backups with the user's own key (read-only helper)
- [ ] Signed Windows builds, winget and Scoop packages
- [ ] Flutter app on Google Play / F-Droid

### v1.0
- [ ] Stable CLI, documented library API, tested on a wide range of real cards and phones

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
| v0.2 | Window, undelete with names, more formats, report, Android app ✅ |
| v0.3 | Fragmented files, ext4, more audio/office types |
| v1.0 | Stable and tested on real hardware at scale |
