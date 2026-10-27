# File Rescue

[![CI](https://github.com/Bhushansavande/file-rescue/actions/workflows/ci.yml/badge.svg)](https://github.com/Bhushansavande/file-rescue/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)

**Free, open-source tool to recover deleted photos, videos, music and APK files** from laptops (Windows/Linux), SD cards, USB drives and Android phones.

> ⚠️ **Most important rule:** once a file is deleted, do **not** save anything new to the drive you want to recover from. Every new write can permanently overwrite the old data. Read [docs/EDUCATION.md](docs/EDUCATION.md) first.

## What it does

| Feature | Command | Best for |
|---|---|---|
| **File carving** (finds files by their content signatures) | `filerescue carve` | SD cards, USB drives, HDDs, disk images |
| **Recycle Bin restore** (keeps original file names) | `filerescue recyclebin` | Windows laptops |
| **Android trash files** (over ADB) | `filerescue android` | Android 11+ phones |
| **Safe disk image copy** | `filerescue image` | Any drive: image first, recover second |

**Supported file types:** JPG, PNG, GIF, WEBP, MP4, MOV, 3GP, M4A, MP3, WAV, AVI, **APK**, PDF, DOCX, XLSX, PPTX, ZIP.

Everything runs **read-only**: the source drive is never written to. No third-party dependencies, only Python 3.9+.

## Install

```bash
git clone https://github.com/Bhushansavande/file-rescue.git
cd file-rescue
pip install .
```

You can also run it without installing: `python -m filerescue ...`

**Windows without Python:** download `filerescue-windows-x64.exe` from the [Releases](https://github.com/Bhushansavande/file-rescue/releases) page and run it from an Administrator Command Prompt (`filerescue-windows-x64.exe carve ...`). The file is not code-signed, so Windows SmartScreen may show a warning; if you prefer, install from source as shown above.

## Usage

### 1) SD card / USB drive / disk (most effective)

Always write the output to a **different drive** than the one you are recovering from.

```bash
# Windows (open Command Prompt as Administrator; E: is the card)
filerescue carve \\.\E: -o D:\recovered --types photos,videos

# Linux
sudo filerescue carve /dev/sdb1 -o ~/recovered --types jpg,mp3,apk

# A disk image file
filerescue carve disk.img -o recovered
```

For a failing card or drive, make an image first and work on the image:

```bash
filerescue image \\.\E: D:\sdcard.img
filerescue carve D:\sdcard.img -o D:\recovered
```

`--types` examples: `jpg,png` · `photos` · `videos` · `music` · `apps` (APK) · `documents`

Recovered files are sorted into `photos/ videos/ music/ apps/ documents/ archives/`.
Files ending in `_partial` may be incomplete (their end was overwritten).

### 2) Windows Recycle Bin

```bash
filerescue recyclebin                  # list: original path, size, deletion time
filerescue recyclebin -o D:\restored   # copy out with original names
```

### 3) Android phone

1. On the phone enable **Developer options → USB debugging**.
2. On the computer download [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) (free) and put `adb` on your PATH.
3. Connect the phone with USB and accept the prompt on the phone, then:

```bash
filerescue android                    # list trashed files
filerescue android -o D:\phone-trash  # pull them
```

> A phone's **internal storage** is encrypted and cannot be carved directly. See [docs/EDUCATION.md](docs/EDUCATION.md) for what does work on Android. A phone's **SD card** can be scanned with method 1 using a card reader.

## Limitations (honest ones)

- **SSDs and modern phones** use TRIM, which really erases deleted data quickly; carving usually cannot help there.
- Carving cannot restore a file's **name or folder** (that information is lost on delete); you get the content only.
- Large files stored in **fragments** may come back incomplete or damaged. The carver reads a file from its start to where its own structure ends, so pieces stored elsewhere on the drive are missed. Such files are normally flagged `_partial`, but "complete" only means the file's structure was read to a clean end, not that every byte is guaranteed intact; open important recoveries to check them.
- Files with no recognizable end (partly overwritten) are saved as `_partial` and capped at 64 MB each.
- MP3 detection currently covers songs that start with an `ID3` tag.
- A photo with a video appended (some phones' "motion photos") is recovered as the photo and the video separately.
- This is an early (v0.1) tool. For critical data also try **PhotoRec/TestDisk** (free, open source).

## Development

```bash
python -m unittest discover -s tests -v
```

The unit tests build synthetic disk images and check that recovered files match the originals byte for byte.

`python scripts/realworld_test.py` goes further (Linux; needs `ffmpeg`, `mtools`, `dosfstools`, `e2fsprogs`, `pillow`): it generates real JPEG, PNG, GIF, WEBP, MP4, MOV, 3GP, AVI, MP3, M4A, WAV, PDF, DOCX, XLSX, APK and ZIP files, puts them on real FAT32 and ext4 images, deletes them and checks that every file is recovered exactly. CI runs it on every push. It has **not** yet been tested on physical SD cards or phones, so reports from real hardware are very welcome.

See [docs/ROADMAP.md](docs/ROADMAP.md) for what is planned and [CONTRIBUTING.md](CONTRIBUTING.md) to help.

## Legal

MIT licensed. Use this only on devices you own or have explicit permission to examine; recovering someone else's data without consent may be illegal. Recovered files can contain private information, so keep them safe and never commit them to a repository.

---
Made by **Bhushan Savande** · Life Style Knowledge Hub (LSKH)
