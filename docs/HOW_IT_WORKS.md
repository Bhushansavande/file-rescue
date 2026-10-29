# How File Rescue works

Why can a deleted file come back, and when can it not? This page explains the three ideas the tool is built on.

## 1. What "delete" really does

A drive stores two things: the **data** of your files, and an **index** (the file system) that says "photo.jpg is called photo.jpg, lives in DCIM, is 2 MB, and starts at cluster 5000".

When you delete a file, the system usually only edits the index: the entry is marked deleted and its clusters are marked free. The 2 MB of photo data stays where it was **until something new is written over it**. That is why you should stop using the drive right away.

Exceptions: **TRIM** on SSDs and modern phones tells the drive to really erase freed space soon after, and **encryption** hides the data without the key.

## 2. Undelete: read the index (`filerescue undelete`)

The deleted index entry usually still exists, with the name, size, dates and first cluster:

* **FAT32 / FAT16 / FAT12** (SD cards, USB sticks): directory entries are 32 bytes. A deleted one starts with the byte `0xE5`. Long names are stored in extra entries just before it, which survive too (only the first letter of an 8.3 name is lost). The file's cluster chain in the FAT is cleared on delete, so the tool assumes the file was stored in one piece starting at the first cluster, and checks that those clusters are still free.
* **exFAT** (big SD cards): a file is a set of entries (file, stream, name). Deleting clears the top bit of each entry's type. The stream entry keeps the first cluster, the size and a flag for "stored contiguously". An allocation bitmap tells whether the clusters were reused.
* **NTFS** (Windows disks): every file is a record in the Master File Table with attributes ($FILE_NAME, $DATA). Deleting clears an "in use" flag. Small files live inside the record; bigger ones have a *run list* that names exactly which clusters hold the data, even when fragmented. The `$Bitmap` file says which clusters have been reused.

So `undelete` can print the original path and say `intact` (space still free) or `overwritten` (space reused, the content is probably wrong).

## 3. Carving: find files by their shape (`filerescue carve`)

If the index is gone (formatted card, damaged file system, unknown file system), the data can still be there. Most file formats start with a recognizable **signature**: JPEG starts `FF D8 FF`, PNG with `89 PNG`, MP4 has `ftyp` four bytes in, and so on.

The carver scans the whole drive for these signatures at 512-byte boundaries, then works out **where each file ends** by following the format's own structure, which is much more reliable than looking for an end marker:

* JPEG: walk the marker segments until the end-of-image marker (thumbnails inside EXIF contain their own end marker, which is why a naive search cuts photos short);
* PNG: walk the chunks until `IEND`; GIF: walk the blocks; PDF: follow the `%%EOF` chain;
* ZIP/APK/DOCX: find the end record that really belongs to this archive;
* MP4/MOV/HEIC: add up the sizes of the top-level boxes; MKV/WebM: walk the EBML elements;
* MP3: step through the frames; Ogg/Opus: walk the pages until the end-of-stream flag;
* SQLite: page size times page count from the header; BMP/7z/TIFF: sizes from their headers.

A file whose end cannot be found (because its tail was overwritten) is saved with the suffix `_partial`. A false hit never makes a huge junk file: partial files are capped.

The limit of carving: names and folders are not stored in the file itself, and a file stored in **fragments** (not in one piece) is cut where the first fragment ends.

## 4. Phones

Android keeps some deleted things on purpose: the gallery trash (`.trashed-*`, about 30 days), WhatsApp statuses (`.Statuses`, about 24 hours) and thumbnails (`.thumbnails`). `filerescue android` copies them out, over USB (`adb`) or directly on the phone (Termux).

## Why read-only matters

Every recovery step only **reads** the source. If the tool wrote anything to the drive you are rescuing, it could destroy the very data you want back.
