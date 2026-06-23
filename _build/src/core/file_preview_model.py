"""Pure preview/editor decisions shared by file preview UI and smoke tests."""
from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".ico", ".webp",
              ".svg", ".svgz", ".tif", ".tiff", ".tga", ".cur", ".xbm", ".xpm",
              ".pbm", ".pgm", ".ppm"}
MOVIE_EXTS = {".gif", ".webp"}
BINARY_EXTS = {".class", ".jar", ".war", ".zip", ".7z", ".tar", ".gz",
               ".pdf", ".doc", ".docx", ".xls", ".xlsx",
               ".so", ".dll", ".exe", ".dylib", ".bin"}
WRAP_EXTS = {".md", ".markdown", ".txt"}

LINE_COMMENT_BY_EXT: dict[str, str] = {
    ".py": "#", ".pyw": "#", ".pyi": "#",
    ".sh": "#", ".bash": "#", ".zsh": "#", ".fish": "#",
    ".yml": "#", ".yaml": "#",
    ".toml": "#",
    ".conf": "#", ".cfg": "#", ".ini": "#",
    ".env": "#",
    ".properties": "#",
    ".rb": "#",
    ".pl": "#", ".pm": "#",
    ".r": "#",
    ".tcl": "#",
    ".coffee": "#",
    ".feature": "#",
    ".java": "//", ".kt": "//", ".kts": "//",
    ".scala": "//", ".groovy": "//", ".gradle": "//",
    ".js": "//", ".jsx": "//", ".ts": "//", ".tsx": "//", ".mjs": "//", ".cjs": "//",
    ".vue": "//", ".svelte": "//",
    ".c": "//", ".cpp": "//", ".cc": "//", ".cxx": "//",
    ".h": "//", ".hpp": "//", ".hxx": "//",
    ".cs": "//", ".go": "//", ".rs": "//", ".swift": "//",
    ".dart": "//", ".php": "//", ".m": "//", ".mm": "//",
    ".scss": "//", ".less": "//", ".jsonc": "//",
    ".sql": "--", ".lua": "--", ".hs": "--",
    ".lisp": ";", ".cl": ";", ".el": ";", ".clj": ";", ".cljs": ";",
}

LINE_COMMENT_BY_NAME: dict[str, str] = {
    "dockerfile": "#",
    "makefile": "#",
    ".gitignore": "#",
    ".dockerignore": "#",
    ".env": "#",
}


def detect_encoding(raw: bytes) -> tuple[str, str]:
    """Detect text encoding without third-party dependencies."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"),
                     (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                break
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as e:
            if e.start > 0 and e.start >= len(raw) - 4:
                try:
                    return raw[:e.start].decode(enc), enc
                except UnicodeDecodeError:
                    pass
            continue
    return raw.decode("latin-1"), "latin-1"


def detect_newline(text: str) -> str:
    """Return newline value suitable for open(newline=...)."""
    first_lf = text.find("\n")
    if first_lf == -1:
        return "\r" if "\r" in text else ""
    if first_lf > 0 and text[first_lf - 1] == "\r":
        return "\r\n"
    return ""


def line_comment_prefix(path: str | Path) -> str | None:
    p = Path(path)
    name = p.name.lower()
    if name in LINE_COMMENT_BY_NAME:
        return LINE_COMMENT_BY_NAME[name]
    return LINE_COMMENT_BY_EXT.get(p.suffix.lower())


def is_binary_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in BINARY_EXTS


def should_wrap_text(path: str | Path) -> bool:
    return Path(path).suffix.lower() in WRAP_EXTS


def image_suffix_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    return suffix.lstrip(".") if suffix in IMAGE_EXTS else ""


def is_movie_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in MOVIE_EXTS
