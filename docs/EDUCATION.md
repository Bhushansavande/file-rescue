# Data Recovery Basics

## 1. What really happens when you delete a file?

Think of a drive as a big book:

- **Data** = the pages (the actual bytes of your photo or song).
- **File system (NTFS, FAT32, exFAT, ext4)** = the table of contents: which file lives on which pages.

When you **delete** a file, the system normally only removes its entry from the table of contents and marks those pages as "free to reuse". The text on the pages stays until **something new is written over it**. That is why recovery is possible at all.

So the first rule: **after a deletion, write nothing to that drive**: no downloads, no new photos, no app installs, no WhatsApp media.

## 2. Three ways to recover

| Method | How it works | Name/folder kept? | Chance |
|---|---|---|---|
| **Recycle Bin / trash** | The file is still intact, just moved | Yes | Best |
| **File-system recovery** (TestDisk, Recuva) | Finds the old table-of-contents entry | Often | Good |
| **File carving** (this project, PhotoRec) | Ignores the table of contents; looks for a file's starting signature in the raw data | No (new names are generated) | Good if the data is not overwritten |

### How carving works

Every file type starts with a characteristic signature (**magic number / header**):

| Type | Starting bytes |
|---|---|
| JPEG | `FF D8 FF` |
| PNG | `89 50 4E 47 0D 0A 1A 0A` |
| PDF | `%PDF-` |
| ZIP / APK / DOCX | `50 4B 03 04` ("PK") |
| MP4 | `ftyp` after 4 bytes |
| MP3 | `ID3` |

The tool reads the whole drive, finds such signatures, and then **measures where each file ends**:

- JPEG: walks the file's internal markers to find the true end (not fooled by thumbnails).
- PNG / PDF: looks for the fixed end marker (`IEND`, `%%EOF`).
- MP4: adds up the sizes of the file's top-level "boxes".
- MP3: counts audio frames.
- APK: finds the ZIP central-directory end, then names the file `.apk` if it contains `AndroidManifest.xml`.

Files normally start on a **512-byte boundary**, so the tool only checks such positions by default (`--no-align` turns this off). This removes most false hits.

## 3. TRIM and encryption: the big obstacles

- **SSD / flash + TRIM:** after a delete the OS tells the drive "these blocks are unused" and the drive erases them soon after (seconds to minutes). Recovery is then close to impossible. A **spinning HDD** gives much better chances.
- **A phone's internal storage:** modern Android phones encrypt it and use TRIM. No computer tool, free or paid, can normally bring back deleted photos from it, with or without root. Ads promising "one-click phone recovery" are often misleading.
- **SD cards and USB sticks:** usually not encrypted and use TRIM less. Carving works best here.

## 4. Real ways to get files back from a phone (in order)

1. **Gallery "Recently deleted / Trash / Recycle bin"**: Google Photos (60 days), Samsung Gallery (30 days), Xiaomi Gallery, and others. Check this first.
2. **Google Photos / Drive backup**: check photos.google.com, including its Trash.
3. **`.trashed-` files (this tool):** on Android 11+, pull them with `filerescue android`.
4. **WhatsApp:** restore the chat backup; the same media may also sit in `Android/media/com.whatsapp/WhatsApp/Media`.
5. **The phone's SD card:** put it in a card reader and run `filerescue carve`.
6. **APK files:** re-download the app from the Play Store. If it is your own app, rebuild the APK from your build folder or Git history.
7. **Older copies:** check old USB transfers, email, cloud storage and messenger "Saved" folders.

## 5. Steps for a laptop

1. **Stop using the computer immediately** (shut it down if you can).
2. Check the **Recycle Bin**: `filerescue recyclebin`.
3. Check **OneDrive / Google Drive** trash and "File History / Previous Versions" (folder → Properties → Previous Versions).
4. If possible connect the drive to another computer, or **make an image**: `filerescue image`.
5. Run `filerescue carve` with the output on a **different drive**.
6. If needed use **PhotoRec / TestDisk** (free, open source, proven).

## 6. Other free / open-source tools

| Tool | Notes |
|---|---|
| **PhotoRec / TestDisk** (cgsecurity.org) | The gold standard for carving, 480+ types, Windows/Linux/Mac |
| **ddrescue** | Best for imaging failing drives |
| **The Sleuth Kit / Autopsy** | Forensic analysis, deep file-system support |
| **scalpel / foremost** | Older, simple carving tools |
| **adb (platform-tools)** | Talks to Android devices |

This project aims to be small, readable Python that you can learn from and extend.

## 7. Safety and law

- Use it only on **your own** devices or with explicit permission. Taking data from someone else's phone or laptop without consent may be a crime.
- Recovered files may contain private information. Keep them safe and never upload them to this repo or any public place (`.gitignore` blocks common cases).
- **Inspect recovered APKs before running them.** If you do not know where one came from, do not install it.

## 8. Check yourself

1. Is deleted data erased immediately? (Usually no, only its entry is removed.)
2. Why is SSD recovery hard? (TRIM.)
3. Why does carving not give back original file names? (Names live in the table of contents, not in the data.)
4. Why not save recovered files to the same drive? (They could overwrite data you have not recovered yet.)
