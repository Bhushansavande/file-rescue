# Security Policy

## What File Rescue promises

- **Read-only on the source.** Scanning and recovery only read from the device or image you point at.
  The tool refuses to save recovered files onto the drive it is recovering from (override: `--force`),
  and `filerescue image` refuses to overwrite its own source, a device, or an existing file (override: `--force`
  for existing files only).
- **No network access.** The Python tool and the Android app make no network connections. The Android app does not
  request the INTERNET permission (the build fails if it ever does).
- **No code execution from recovered data.** Names read from untrusted drives are sanitised (no `..`, no absolute
  paths, no Windows device names like `CON`), writes are confined to the output folder, and the window never opens
  executables, APKs, scripts or HTML from the results list.
- **Bounded work on damaged input.** Corrupt file systems and files cannot make the scanner read terabytes, loop
  forever or crash it; this is fuzz-tested in CI on real NTFS, exFAT and FAT32 images.
- **Safe reports.** `report.html` escapes every name and tag; `recovered.csv` neutralises spreadsheet formulas.

## Limits you should know

- Recovered files come from an untrusted drive. APKs, documents and archives can be malicious; scan them with
  antivirus and do not install or open them blindly.
- Image previews use Pillow (optional); keep it up to date.
- The Windows `.exe` files are not code-signed. Check them against `SHA256SUMS.txt` from the same release, or
  install from source.
- Recovered files may contain private data. Keep them safe and never commit them to a repository.

## Reporting a problem

If you find a security problem (for example a crafted disk image that makes the tool crash, hang or write outside
the output folder), please report it privately through GitHub's **Security > Report a vulnerability** on this
repository instead of opening a public issue.
