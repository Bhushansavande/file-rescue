# Contributing

Contributions are welcome, including from beginners.

## Quick start

1. **Fork** the repo and clone your fork.
2. Run `python -m unittest discover -s tests -v` and make sure everything passes.
3. Make your change, add a test, and open a **Pull Request**.

## Adding a new file type

1. Add a `Signature(...)` in `filerescue/signatures.py`.
2. Add a way to measure its length in `measure()` in `filerescue/carver.py`. Use real validation to avoid false hits.
3. Add a test in `tests/test_filerescue.py` that builds a synthetic file and checks that the **recovered hash matches exactly**.
4. Update the supported-types list in `README.md`.

## Rules

- **Never write to the source device** (read-only).
- Avoid third-party dependencies where possible.
- Do not add real personal photos or files as test data; use synthetic data.
- Keep code comments, messages and docs in English so everyone can follow.

## Testing

- `python -m unittest discover -s tests -v` runs the unit tests (fast, no extra tools).
- `python scripts/realworld_test.py` generates real media, puts it on real FAT32 and ext4 images, deletes it and checks recovery. It needs Linux with `ffmpeg`, `mtools`, `dosfstools`, `e2fsprogs` and `pillow`; CI runs it too.
- Every bug fix should come with a regression test that fails without the fix.

## Reporting bugs

Open an issue and include your OS, Python version, the command you ran, and the output. Never attach private recovered files.
