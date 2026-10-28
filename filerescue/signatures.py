"""File signatures: identify file types from their first bytes."""

from dataclasses import dataclass

MB = 1024 * 1024


@dataclass(frozen=True)
class Signature:
    name: str
    ext: str
    header: bytes
    kind: str                # jpeg | footer | zip | riff | mp4 | mp3 | walker
    header_offset: int = 0   # the header starts this many bytes into the file
    footer: bytes = b""
    max_size: int = 100 * MB
    walker: str = ""         # for kind "walker": the function in formats.py that measures the file
    strict: bool = False     # a short header gives many false hits: never save these as partial


SIGNATURES = [
    Signature("JPEG photo", "jpg", b"\xff\xd8\xff", "jpeg", max_size=60 * MB),
    Signature("PNG photo", "png", b"\x89PNG\r\n\x1a\n", "footer",
              footer=b"IEND\xaeB`\x82", max_size=60 * MB),
    Signature("GIF", "gif", b"GIF8", "footer", footer=b"\x00\x3b", max_size=30 * MB),
    # RIFF container = WEBP / WAV / AVI (the carver tells them apart)
    Signature("RIFF (WEBP/WAV/AVI)", "riff", b"RIFF", "riff", max_size=2048 * MB),
    Signature("MP3 audio", "mp3", b"ID3", "mp3", max_size=60 * MB),
    # the 'ftyp' box sits 4 bytes into the file (MP4, MOV, 3GP, M4A)
    Signature("MP4/MOV/3GP/M4A", "mp4", b"ftyp", "mp4", header_offset=4,
              max_size=4096 * MB),
    Signature("PDF", "pdf", b"%PDF-", "footer", footer=b"%%EOF", max_size=300 * MB),
    # ZIP family: ZIP, APK, DOCX, XLSX, PPTX
    Signature("ZIP/APK/DOCX", "zip", b"PK\x03\x04", "zip", footer=b"PK\x05\x06",
              max_size=1024 * MB),
    Signature("Ogg (Opus/Vorbis)", "ogg", b"OggS", "walker", walker="ogg_walk", max_size=500 * MB),
    Signature("Matroska/WebM", "ebml", b"\x1a\x45\xdf\xa3", "walker", walker="ebml_walk",
              max_size=4096 * MB),
    Signature("SQLite database", "sqlite", b"SQLite format 3\x00", "walker",
              walker="sqlite_walk", max_size=1024 * MB),
    Signature("BMP", "bmp", b"BM", "walker", walker="bmp_walk", strict=True, max_size=200 * MB),
    Signature("7-Zip", "7z", b"7z\xbc\xaf\x27\x1c", "walker", walker="sevenzip_walk",
              max_size=1024 * MB),
    Signature("TIFF / RAW (little-endian)", "tiff", b"II*\x00", "walker", walker="tiff_walk",
              strict=True, max_size=200 * MB),
    Signature("TIFF / RAW (big-endian)", "tiff", b"MM\x00*", "walker", walker="tiff_walk",
              strict=True, max_size=200 * MB),
]

# RIFF sub-type (bytes 8..12) -> extension
RIFF_TYPES = {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}

# output folder for each extension
EXT_CATEGORY = {
    "jpg": "photos", "png": "photos", "gif": "photos", "webp": "photos",
    "heic": "photos", "avif": "photos", "bmp": "photos",
    "tif": "photos", "cr2": "photos", "dng": "photos", "nef": "photos", "arw": "photos",
    "mp4": "videos", "mov": "videos", "3gp": "videos", "avi": "videos",
    "mkv": "videos", "webm": "videos", "ogv": "videos",
    "mp3": "music", "m4a": "music", "wav": "music", "opus": "music", "ogg": "music",
    "apk": "apps",
    "pdf": "documents", "docx": "documents", "xlsx": "documents", "pptx": "documents",
    "sqlite": "databases",
    "zip": "archives", "7z": "archives",
}


# What each --types name means: the final file extensions that are kept.
TYPE_EXTS = {
    "jpg": {"jpg"}, "jpeg": {"jpg"}, "png": {"png"}, "gif": {"gif"}, "webp": {"webp"},
    "wav": {"wav"}, "avi": {"avi"}, "mp3": {"mp3"},
    "mp4": {"mp4"}, "mov": {"mov"}, "3gp": {"3gp"}, "m4a": {"m4a"},
    "pdf": {"pdf"}, "zip": {"zip"}, "apk": {"apk"},
    "docx": {"docx"}, "xlsx": {"xlsx"}, "pptx": {"pptx"},
    "heic": {"heic"}, "avif": {"avif"}, "bmp": {"bmp"}, "tif": {"tif"}, "tiff": {"tif"},
    "cr2": {"cr2"}, "dng": {"dng"}, "nef": {"nef"}, "arw": {"arw"},
    "mkv": {"mkv"}, "webm": {"webm"}, "ogv": {"ogv"}, "opus": {"opus"}, "ogg": {"ogg"},
    "sqlite": {"sqlite"}, "db": {"sqlite"}, "7z": {"7z"},
    "raw": {"cr2", "dng", "nef", "arw", "tif"},
    "photos": {"jpg", "png", "gif", "webp", "heic", "avif", "bmp", "tif", "cr2", "dng",
               "nef", "arw"},
    "videos": {"mp4", "mov", "3gp", "avi", "mkv", "webm", "ogv"},
    "music": {"mp3", "m4a", "wav", "opus", "ogg"},
    "apps": {"apk"},
    "documents": {"pdf", "docx", "xlsx", "pptx"},
    "databases": {"sqlite"},
}

# Which signature (container format) has to be scanned for each final extension.
EXT_SIGNATURE = {
    "jpg": "jpg", "png": "png", "gif": "gif", "mp3": "mp3", "pdf": "pdf",
    "webp": "riff", "wav": "riff", "avi": "riff",
    "mp4": "mp4", "mov": "mp4", "3gp": "mp4", "m4a": "mp4",
    "zip": "zip", "apk": "zip", "docx": "zip", "xlsx": "zip", "pptx": "zip",
    "heic": "mp4", "avif": "mp4", "bmp": "bmp", "7z": "7z", "sqlite": "sqlite",
    "mkv": "ebml", "webm": "ebml", "opus": "ogg", "ogg": "ogg", "ogv": "ogg",
    "tif": "tiff", "cr2": "tiff", "dng": "tiff", "nef": "tiff", "arw": "tiff",
}


def allowed_extensions(types):
    """Final extensions to keep for the given --types names, or None for everything."""
    if not types:
        return None
    exts = set()
    for t in types:
        t = t.strip().lower().lstrip(".")
        if not t:
            continue
        if t not in TYPE_EXTS:
            raise ValueError(f"Unknown type: {t}")
        exts.update(TYPE_EXTS[t])
    return exts or None


def select_signatures(types):
    """Signatures to scan for. types: ['jpg', 'music', 'apk', ...] or None for everything."""
    allowed = allowed_extensions(types)
    if allowed is None:
        return list(SIGNATURES)
    needed = {EXT_SIGNATURE[e] for e in allowed}
    return [s for s in SIGNATURES if s.ext in needed]
