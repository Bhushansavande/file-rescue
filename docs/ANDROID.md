# Recovering files on Android

Be realistic first: a phone's internal storage is encrypted and uses TRIM, so deleted photos are
usually gone for good once the trash period has passed. What *can* be saved without root:

| Where | What | How long it lasts |
|---|---|---|
| Gallery / Files **trash** (`.trashed-*`, `.Trash`) | photos, videos, files you deleted | about 30 days |
| WhatsApp **statuses** (`.Statuses`) | statuses you viewed (photos and videos) | about 24 hours |
| **Thumbnails** (`.thumbnails`) | small previews of photos, sometimes of deleted ones | until the phone cleans them |
| The phone's **SD card** | everything a card reader can carve | until overwritten |

## Option A: from a computer over USB (adb)

1. Phone: Settings > About phone > tap *Build number* 7 times, then Developer options > **USB debugging**.
2. Computer: install [platform-tools](https://developer.android.com/tools/releases/platform-tools) and `pip install .` (or use the Windows download).
3. Connect the cable and tap *Allow* on the phone.

```bash
filerescue android                 # list what can be saved
filerescue android -o D:\phone     # copy it (trash/, whatsapp-status/, thumbnails/)
filerescue android --kinds status  # only WhatsApp statuses
```

## Option B: directly on the phone (no computer)

Install [Termux](https://f-droid.org/packages/com.termux/) from F-Droid, then:

```bash
pkg install python git
termux-setup-storage              # allow access to phone storage
git clone https://github.com/Bhushansavande/file-rescue.git
cd file-rescue
python -m filerescue android --local -o ~/storage/shared/Recovered
```

`--local` scans the phone's own storage, so no cable and no `adb` are needed. Run it soon after
viewing a WhatsApp status: they disappear after a day.

## Option C: SD card in a card reader

Use `filerescue carve` or `filerescue undelete` on the card (see the main README). Cards use
FAT32 or exFAT, so `undelete` can bring files back with their original names.

## What does not work

* Carving the phone's internal storage (encrypted; needs root and usually fails anyway).
* Encrypted WhatsApp backups (`msgstore.db.crypt14`) cannot be opened without your key.
* Files deleted a long time ago from internal storage.
