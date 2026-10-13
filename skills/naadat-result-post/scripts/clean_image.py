#!/usr/bin/env python3
"""Write and verify metadata-free images.

Instagram shows an "AI info" label when an upload carries provenance metadata
that says AI was involved: C2PA / Content Credentials (JPEG APP11 JUMBF boxes,
PNG caBX chunks), IPTC DigitalSourceType in XMP, and similar EXIF/XMP fields.
This skill never uses a generative image model, and on top of that every
image it writes is re-encoded from raw pixels with no metadata segments at
all, so nothing can be inherited from a source photo or a font/logo file.

  python clean_image.py check out/*.jpg      # exit 1 if any metadata is found
  python clean_image.py clean in.jpg out.jpg # strip an existing image
"""
from __future__ import annotations

import struct
import sys

from PIL import Image

PNG_ALLOWED = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
JPEG_ALLOWED_APP = {0xE0}  # APP0 JFIF only


def save_clean(img: Image.Image, path: str) -> str:
    """Re-encode pixels only. No EXIF, XMP, ICC, IPTC, C2PA or text chunks."""
    pixels = img.convert("RGB")
    fresh = Image.new("RGB", pixels.size)
    fresh.paste(pixels)  # drops .info so nothing rides along
    if path.lower().endswith(".png"):
        fresh.save(path, "PNG", optimize=True)
    else:
        fresh.save(path, "JPEG", quality=94, optimize=True, subsampling=0)
    problems = inspect(path)
    if problems:
        raise RuntimeError(f"{path} still carries metadata: {problems}")
    return path


def inspect(path: str) -> list:
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg(data)
    return ["unknown format, cannot verify"]


def _png(data: bytes) -> list:
    found, i = [], 8
    while i + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[i:i + 8])
        if ctype not in PNG_ALLOWED:
            found.append(ctype.decode("latin-1"))
        i += 12 + length
    return found


def _jpeg(data: bytes) -> list:
    found, i = [], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan, metadata segments come before it
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i + 4:i + 2 + length]
        if 0xE0 <= marker <= 0xEF and marker not in JPEG_ALLOWED_APP:
            tag = {0xE1: "APP1 EXIF/XMP", 0xE2: "APP2 ICC/MPF", 0xED: "APP13 IPTC", 0xEB: "APP11 C2PA/JUMBF"}
            found.append(tag.get(marker, f"APP{marker - 0xE0}"))
        if marker == 0xFE:
            found.append("COM comment")
        if b"c2pa" in seg.lower() or b"jumb" in seg:
            found.append("C2PA manifest")
        i += 2 + length
    return found


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[0] == "check":
        bad = 0
        for p in argv[1:]:
            problems = inspect(p)
            print(f"{'CLEAN' if not problems else 'METADATA'}  {p}  {', '.join(problems)}")
            bad += bool(problems)
        return 1 if bad else 0
    if len(argv) == 3 and argv[0] == "clean":
        save_clean(Image.open(argv[1]), argv[2])
        print("CLEAN ", argv[2])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
