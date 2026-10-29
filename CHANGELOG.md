# Changelog

All notable changes to this project are documented here.

## [0.2.1]: 2026-10-29

### Fixed (found by fuzzing and stress tests)
- MKV/WebM live recordings (unknown-size segments): trailing junk could be taken as part of the file; only valid element ids are accepted now.
- NTFS: a damaged MFT size or run list could make the scan run for minutes or crash; reads are now bounded.
- exFAT/FAT: a damaged directory size could make the scan hang; directory reads are capped.
- Reading at negative or impossible offsets no longer raises; devices that raise an error at their end (Windows) are treated as the end.

### Added
- Fuzz phase in the real file-system test (damaged NTFS/exFAT/FAT32 images must never crash or hang).

## [0.2.0]: 2026-10-29

### Added
- **Desktop window** (`filerescue gui`, and `filerescue-gui-windows-x64.exe` in releases): drive picker, file-type choice, progress, thumbnail preview, "Recover selected".
- **`undelete` command**: lists and recovers deleted files **with their original names and folders** from FAT12/16/32, exFAT and NTFS volumes (also inside MBR/GPT disks), and tells which ones are still `intact` and which were `overwritten`.
- New formats: HEIC, AVIF, Opus/Ogg (WhatsApp voice notes), MKV, WebM, SQLite (WhatsApp databases), BMP, TIFF, RAW (CR2, NEF, ARW, DNG; experimental) and 7z. New `--types` groups: `raw`, `databases`.
- **HTML report** (`report.html`) with thumbnails and players, plus `recovered.csv`.
- `--dedupe` (remove identical files), `--date-names` (name by EXIF/MP4 date or ID3 artist and title), `--by-month`.
- `android` command: WhatsApp statuses and thumbnails besides trash; `--local` scans the phone itself (Termux); `--kinds`.
- Flutter Android app in `android_app/` (built in CI).
- Tests on real NTFS and exFAT volumes written by the real drivers, run in CI.

### Changed
- Scans can be cancelled (used by the window).

## [0.1.0]: 2026-10-27

### Fixed (found by testing on real FAT32 / ext4 images)
- GIF: animated GIFs were cut short because `00 3B` can occur inside image data; the block structure is now parsed.
- PNG: a file with the IEND bytes inside another chunk was cut short; chunks are now walked.
- PDF: incremental-update and linearized PDFs (several `%%EOF` markers) were cut short.
- ZIP/APK: an archive containing a stored ZIP was cut at the inner archive's end record; the matching record is now validated.
- MP4: unknown trailing data could extend a recovered video; only known box types are accepted.
- Files cut off by the end of the source are now reported as partial instead of complete.
- Partial files from false hits are capped (64 MB) so they cannot fill the disk.
- `--types` now keeps only the requested kinds (`music` no longer returns videos that share the MP4 container).
- No empty `archives` folder is left behind after APK/DOCX detection.

### Added
- File carving for JPG, PNG, GIF, WEBP, MP4/MOV/3GP/M4A, MP3, WAV, AVI, PDF, ZIP, APK, DOCX, XLSX, PPTX.
- Windows Recycle Bin listing and restore with original names.
- Android trash (`.trashed-*`) extraction over ADB.
- `image` command for safe raw device copies.
- English documentation and an education guide.
- Unit tests, real-media tests on FAT32/ext4 images, and GitHub Actions CI.
- Release workflow that builds the wheel, source package and a Windows `.exe`, with SHA-256 checksums.
