# FAQ

**I deleted a photo an hour ago. What do I do right now?**
Stop using that drive or phone. Do not install recovery software onto it. Run File Rescue from another drive and save the output to another drive.

**Which command should I use first?**
For SD cards, USB sticks and Windows disks: `filerescue undelete <drive>`, because it returns original names. If that finds nothing (formatted card, damaged file system), use `filerescue carve`.

**Does it work on my phone's internal storage?**
Only for the trash, WhatsApp status and thumbnail folders (`filerescue android`). Internal storage is encrypted and uses TRIM, so deleted files are normally gone. A phone's **SD card** can be scanned through a card reader.

**Can it recover WhatsApp chats?**
The unencrypted `msgstore.db` (SQLite) can be carved, and WhatsApp media (photos, voice notes in Opus, videos) can be recovered. Encrypted backups (`.crypt14`) cannot be opened without your own key.

**A recovered photo is half grey or will not open.**
Part of it was overwritten or stored in fragments elsewhere. Files whose end could not be found are marked `_partial`; "complete" files can still be damaged inside. Try other recovery tools too: PhotoRec/TestDisk is excellent.

**It says `overwritten`.**
The space of that file was reused by newer data. The content that would be copied is wrong, so it is skipped unless you pass `--include-overwritten`.

**Is it safe?**
It only reads from the drive you scan. It never writes to it. The Windows download is not code-signed, so SmartScreen may warn; check `SHA256SUMS.txt` or install from source.

**Why is the deep scan slow?**
It reads every sector of the drive. Image the card first (`filerescue image`) and scan the image, or choose only the file types you need with `--types`.

**Does it need internet?**
No. Nothing is uploaded anywhere.
