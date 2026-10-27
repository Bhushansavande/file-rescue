# Changelog

All notable changes to this project are documented here.

## [0.1.0]: 2026-10-27

### Fixed (found by testing on real FAT32 / ext4 images)
- GIF: animated GIFs were cut short because `00 3B` can occur inside image data; the block structure is now parsed.
- PNG: a file with the IEND bytes inside another chunk was cut short; chunks are now walked.
- PDF: incremental-update and linearized PDFs (several `%%EOF` markers) were cut short.
- ZIP/APK: an archive containing a stored ZIP was cut at the inner archive's end record; the matching record is now validated.
- MP4: unknown trailing data could extend a recovered video; only known box types are accepted.
- Files cut off by the end of the source are now reported as partial instead of complete.
- Partial files from false hits are capped (64 MB) so they cannot fill the disk.
- No empty `archives` folder is left behind after APK/DOCX detection.

### Added
- File carving for JPG, PNG, GIF, WEBP, MP4/MOV/3GP/M4A, MP3, WAV, AVI, PDF, ZIP, APK, DOCX, XLSX, PPTX.
- Windows Recycle Bin listing and restore with original names.
- Android trash (`.trashed-*`) extraction over ADB.
- `image` command for safe raw device copies.
- English documentation and an education guide.
- Unit tests, real-media tests on FAT32/ext4 images, and GitHub Actions CI.
- Release workflow that builds the wheel, source package and a Windows `.exe`.
