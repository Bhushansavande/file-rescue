"""फाइल सिग्नेचर्स - फाइलच्या सुरुवातीच्या बाइट्सवरून फाइलचा प्रकार ओळखण्यासाठी."""

from dataclasses import dataclass

MB = 1024 * 1024


@dataclass(frozen=True)
class Signature:
    name: str
    ext: str
    header: bytes
    kind: str                # jpeg | footer | zip | riff | mp4 | mp3
    header_offset: int = 0   # header फाइलच्या सुरुवातीपासून इतक्या बाइट्सनंतर असतो
    footer: bytes = b""
    max_size: int = 100 * MB


SIGNATURES = [
    Signature("JPEG फोटो", "jpg", b"\xff\xd8\xff", "jpeg", max_size=60 * MB),
    Signature("PNG फोटो", "png", b"\x89PNG\r\n\x1a\n", "footer",
              footer=b"IEND\xaeB`\x82", max_size=60 * MB),
    Signature("GIF", "gif", b"GIF8", "footer", footer=b"\x00\x3b", max_size=30 * MB),
    # RIFF = WEBP / WAV / AVI (तिसरा प्रकार carver स्वतः ओळखतो)
    Signature("RIFF (WEBP/WAV/AVI)", "riff", b"RIFF", "riff", max_size=2048 * MB),
    Signature("MP3 गाणे", "mp3", b"ID3", "mp3", max_size=60 * MB),
    # ftyp बॉक्स फाइलमध्ये 4 बाइट्सनंतर येतो (MP4, MOV, 3GP, M4A)
    Signature("MP4/MOV/3GP/M4A", "mp4", b"ftyp", "mp4", header_offset=4,
              max_size=4096 * MB),
    Signature("PDF", "pdf", b"%PDF-", "footer", footer=b"%%EOF", max_size=300 * MB),
    # ZIP कुटुंब: ZIP, APK, DOCX, XLSX, PPTX
    Signature("ZIP/APK/DOCX", "zip", b"PK\x03\x04", "zip", footer=b"PK\x05\x06",
              max_size=1024 * MB),
]

# RIFF चा तिसरा भाग (बाइट 8..12) -> विस्तार
RIFF_TYPES = {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}

# फोल्डर वर्गीकरण
EXT_CATEGORY = {
    "jpg": "photos", "png": "photos", "gif": "photos", "webp": "photos",
    "mp4": "videos", "mov": "videos", "3gp": "videos", "avi": "videos",
    "mp3": "music", "m4a": "music", "wav": "music",
    "apk": "apps",
    "pdf": "documents", "docx": "documents", "xlsx": "documents", "pptx": "documents",
    "zip": "archives",
}


def select_signatures(types):
    """types: ['jpg','png','mp3','apk'...] किंवा None (सगळे)."""
    if not types:
        return list(SIGNATURES)
    wanted = {t.lower().lstrip(".") for t in types}
    mapping = {
        "jpg": ["jpg"], "jpeg": ["jpg"], "png": ["png"], "gif": ["gif"],
        "webp": ["riff"], "wav": ["riff"], "avi": ["riff"],
        "mp3": ["mp3"],
        "mp4": ["mp4"], "mov": ["mp4"], "3gp": ["mp4"], "m4a": ["mp4"],
        "pdf": ["pdf"],
        "zip": ["zip"], "apk": ["zip"], "docx": ["zip"], "xlsx": ["zip"], "pptx": ["zip"],
        "photos": ["jpg", "png", "gif", "riff"], "videos": ["mp4", "riff"],
        "music": ["mp3", "mp4", "riff"], "apps": ["zip"],
        "documents": ["pdf", "zip"],
    }
    exts = set()
    for t in wanted:
        if t not in mapping:
            raise ValueError(f"अज्ञात प्रकार: {t}")
        exts.update(mapping[t])
    return [s for s in SIGNATURES if s.ext in exts]
