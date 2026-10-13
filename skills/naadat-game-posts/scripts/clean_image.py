#!/usr/bin/env python3
"""Write and verify metadata-free images, and refuse AI-declared sources.

Instagram shows an "AI info" label when an upload carries provenance metadata
that says AI was involved: C2PA / Content Credentials (JPEG APP11 JUMBF boxes,
PNG caBX chunks), IPTC DigitalSourceType in XMP, and similar EXIF/XMP fields.
This skill never uses a generative image model, and on top of that every
image it writes is re-encoded from raw pixels with no metadata segments at
all, so nothing can be inherited from a source photo or a font/logo file.

`check` walks the whole file (JPEG: every segment, including those between
progressive scans, and anything after EOI; FF00, reserved and JPGn markers
outside scan data are findings. PNG: every chunk up to IEND and anything after
it, plus the IDAT zlib stream: bytes after its end or more pixel data than IHDR
declares are findings) and also searches all bytes for provenance strings in
ASCII, UTF-16 and UTF-32. Any finding means METADATA.

`ai_provenance(path)` lists what in a SOURCE file declares AI involvement: an
IPTC/XMP DigitalSourceType that is not a known non-AI code (digitalCapture,
negativeFilm, minorHumanEdits, ...; read after removing CDATA markers and XML
comments and resolving entities, in any text encoding; fails closed on a value
it cannot read), any C2PA/JUMBF manifest or remote manifest reference (XMP
dcterms:provenance URL), or EXIF Software / XMP CreatorTool / PNG Software
naming a generative tool. `clean` refuses such a
file with exit code 4: it re-encodes camera photos, it does not launder
AI-made or AI-edited images. `clean` also cannot remove pixel watermarks
(SynthID and similar survive re-encoding), so a "no findings" result proves
only that no metadata declares AI, not that the pixels are unedited.

  python clean_image.py check out/*.jpg      # exit 1 if any metadata is found
  python clean_image.py ai in.jpg ...        # exit 4 if a file declares AI
  python clean_image.py clean in.jpg out.jpg # re-encode a camera photo (exit 4 if AI-declared)
"""
from __future__ import annotations

import html
import io
import re
import struct
import sys
import zlib

from PIL import Image, ImageOps

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_ALLOWED = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
JFIF_PREFIX = b"JFIF\x00"
JFIF_APP0_LENGTH = 16  # 2 length + 5 id + 2 version + 1 units + 4 density + 2 thumb size
AI_REFUSAL_EXIT = 4

APP_NAMES = {
    0xE0: "APP0",
    0xE1: "APP1 EXIF/XMP",
    0xE2: "APP2 ICC/MPF",
    0xEB: "APP11 C2PA/JUMBF",
    0xED: "APP13 IPTC",
}

# Backstop: provenance strings searched in the whole file. Long tokens are
# matched case-insensitively in ASCII, UTF-16LE/BE and UTF-32LE/BE; short
# box/chunk names and segment signatures exactly, as the specs write them.
# The signatures catch an APPn segment hidden from the segment walk.
BACKSTOP_NOCASE = (b"xmpmeta", b"iptc4xmpext", b"digitalsourcetype", b"trainedalgorithmicmedia",
                   b"algorithmicmedia", b"compositesynthetic", b"ns.adobe.com/xap")
BACKSTOP_EXACT = (b"c2pa", b"jumb", b"caBX", b"Exif\x00\x00", b"ICC_PROFILE\x00",
                  b"Photoshop 3.0\x00", b"http://ns.adobe.com/")
BACKSTOP_PREFIX = "byte pattern "
_WIDE = ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")

# AI provenance (ai_provenance). DigitalSourceType values from the IPTC
# NewsCodes vocabulary that mean a model made or changed the pixels.
AI_SOURCE_TYPES = ("compositewithtrainedalgorithmicmedia", "trainedalgorithmicmedia",
                   "algorithmicmedia", "compositesynthetic")
AI_PHRASES = ("edited with google ai", "made with google ai", "edited with ai tools",
              "created with ai", "generated with ai", "ai generated", "ai-generated")
GENERATIVE_TOOL = re.compile(
    r"generative\s*(?:fill|expand|erase|remove|edit|ai)|firefly|\bdall\b|\bdall[\s\-_.·]?e\b"
    r"|midjourney|stable\s*diffusion|\bsdxl\b|\bimagen\b|magic\s*edit(?:or)?|magic\s*eraser"
    r"|galaxy\s*ai|\bgemini\b|nano\s*banana|chatgpt|openai|gpt[\s\-]?4o|gpt[\s\-]?image"
    r"|ideogram|leonardo\.ai|\bflux(?:\.1|[\s\-](?:pro|dev|schnell))\b|google\s*ai",
    re.IGNORECASE)
# XMP property local names whose value names the creating or editing software.
_XMP_TOOL_KEYS = ("creatortool", "softwareagent", ":software")
_XMP_VALUE = re.compile(r"""\s*(?:=\s*(["'])(.*?)\1|[^<>]*>([^<]*)<)""", re.DOTALL)
_JUMBF = re.compile(rb"jumb.{4}jumd", re.DOTALL)
_C2PA_LABELS = (b"c2pa.claim", b"c2pa.assertions", b"c2pa.signature", b"c2pa.actions",
                b"c2pa.hash", b"urn:c2pa:")
_C2PA_BMFF_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")
_XMP_EXT = b"http://ns.adobe.com/xmp/extension/\x00"
_TEXT_LIMIT = 16 * 1024 * 1024  # cap for decompressed PNG text chunks


# --------------------------------------------------------------------- write

def _flatten_rgb(img: Image.Image) -> Image.Image:
    """RGB copy with transparency composited on white."""
    if img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA", "PA") or img.mode.endswith("a"):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img.convert("RGB")


def _encode(fresh: Image.Image, path: str, attempt: int) -> None:
    if path.lower().endswith(".png"):
        fresh.save(path, "PNG", compress_level=(9, 8, 7, 6)[attempt])
    else:
        fresh.save(path, "JPEG", quality=(94, 93, 95, 92)[attempt], optimize=True, subsampling=0)


def save_clean(img: Image.Image, path: str) -> str:
    """Re-encode pixels only. No EXIF, XMP, ICC, IPTC, C2PA or text chunks.

    The caller decides orientation: pass an already upright image (see
    load_upright). Raises RuntimeError if the written file is not clean.
    The caller is also responsible for the source: run ai_provenance() on it
    first (clean_file does both).
    """
    pixels = _flatten_rgb(img)
    fresh = Image.new("RGB", pixels.size)
    fresh.paste(pixels)  # drops .info so nothing rides along
    problems = []
    for attempt in range(4):
        _encode(fresh, path, attempt)
        problems = inspect(path)
        if not problems:
            return path
        # A backstop string can, very rarely, occur by chance inside compressed
        # pixel data. Re-encode with other settings; anything structural is final.
        if not all(p.startswith(BACKSTOP_PREFIX) for p in problems):
            break
    raise RuntimeError(f"{path} still carries metadata: {problems}")


def load_upright(path: str) -> Image.Image:
    """Open an image, apply its EXIF orientation and convert it to sRGB."""
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # broken EXIF on old Pillow: keep pixels as stored
        img.load()
    icc = img.info.get("icc_profile")
    if icc:
        try:
            from PIL import ImageCms

            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            base = _flatten_rgb(img)
            img = ImageCms.profileToProfile(base, src, dst, outputMode="RGB")
        except Exception:  # no littlecms or a bad profile: pixels as stored
            pass
    return img


class AIProvenanceError(Exception):
    """The source file declares AI generation or AI editing."""

    def __init__(self, path: str, reasons: list):
        self.path, self.reasons = path, list(reasons)
        super().__init__(f"{path} declares AI involvement: {'; '.join(self.reasons)}")


def clean_file(src: str, dst: str) -> str:
    """Re-encode a camera photo to dst. Raises AIProvenanceError if src declares AI."""
    reasons = ai_provenance(src)
    if reasons:
        raise AIProvenanceError(src, reasons)
    return save_clean(load_upright(src), dst)


# ------------------------------------------------------------- AI provenance

def ai_provenance(path: str) -> list:
    """Reasons why the SOURCE file declares AI involvement; empty if none.

    Reads bytes only (no pixel decode). Finds DigitalSourceType values and AI
    disclosure phrases in any text encoding, C2PA/JUMBF manifests in any
    container, remote C2PA manifest references (XMP dcterms:provenance with a
    URL), and software fields (EXIF Software/ProcessingSoftware, XMP
    CreatorTool/softwareAgent, PNG Software/Source) naming a generative tool.
    XMP text is normalised before the property scan (CDATA markers and XML
    comments removed, every character entity resolved), and the scan fails
    closed: a DigitalSourceType whose value is not a known non-AI IPTC code
    (digitalCapture, negativeFilm, minorHumanEdits, ...) is a reason too.
    Compressed PNG text chunks and split JPEG extended XMP are scanned after
    reassembly.
    """
    with open(path, "rb") as f:
        data = f.read()
    reasons: list = []
    _scan_text(data, reasons, "")
    extra = _png_text_blobs(data) + [("extended XMP", b) for b in _xmp_extension_blobs(data)]
    for label, blob in extra:
        _scan_text(blob, reasons, label)
    _scan_c2pa(data, reasons)
    for value in _exif_software(data) + _png_software(data):
        _tool_reason(value, "software field", reasons)
    return _dedupe(reasons)


def _encodings(token: bytes):
    text = token.decode("ascii")
    yield "ascii", token
    for enc in _WIDE:
        yield enc, text.encode(enc)


def _find_all(hay: bytes, needle: bytes, limit: int = 256):
    i, n = hay.find(needle), 0
    while i >= 0 and n < limit:
        yield i
        n += 1
        i = hay.find(needle, i + 1)


def _find_all_str(hay: str, needle: str):
    i = hay.find(needle)
    while i >= 0:
        yield i
        i = hay.find(needle, i + 1)


# Non-AI codes of the IPTC DigitalSourceType vocabulary
# (cv.iptc.org/newscodes/digitalsourcetype). Anything else, including
# algorithmicallyEnhanced and dataDrivenMedia, an empty or unreadable value,
# or two values in one property, counts as an AI declaration (fail closed).
NON_AI_SOURCE_TYPES = frozenset((
    "digitalcapture", "computationalcapture", "negativefilm", "positivefilm", "print",
    "humanedits", "minorhumanedits", "compositecapture", "composite", "digitalcreation",
    "digitalart", "virtualrecording", "screencapture"))
_IPTC_DST_BASES = ("http://cv.iptc.org/newscodes/digitalsourcetype/",
                   "https://cv.iptc.org/newscodes/digitalsourcetype/")
# A blob is also decoded as UTF-16/UTF-32 text where one of these occurs in
# that encoding (XMP may be written in either).
_WIDE_TRIGGERS = (b"digitalsourcetype", b"provenance", b"xmpmeta", b"rdf:", b"creatortool",
                  b"softwareagent")
_ASCII_LOWER = {c: c + 32 for c in range(65, 91)}  # lowercase that keeps every offset
_CDATA_MARKS = re.compile(r"<!\[CDATA\[|\]\]>")
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<[^<>]*>")
_ATTR_VALUE = re.compile(r"""["']?\s*[:=]\s*(["'])(.*?)\1""", re.DOTALL)
_START_TAG_REST = re.compile(r"""((?:\s(?:[^<>"']|"[^"]*"|'[^']*')*)?)(/?)>""", re.DOTALL)
_RDF_RESOURCE = re.compile(r"""rdf:resource\s*=\s*(["'])(.*?)\1""", re.DOTALL)
_OPEN_BEFORE = re.compile(r"<(/?)([\w.\-]*:)?$")
_NAME_CHAR = re.compile(r"[\w.\-]")


def _decoded_texts(blob: bytes):
    """(encoding, text) views of a blob: latin-1 always, UTF-16/32 where XMP-like text occurs."""
    yield "ascii", blob.decode("latin-1")
    lower = blob.lower()
    for enc in _WIDE:
        width = 2 if enc.startswith("utf-16") else 4
        aligns = set()
        for trig in _WIDE_TRIGGERS:
            needle = trig.decode("ascii").encode(enc)
            for pos in _find_all(lower, needle, 64):
                aligns.add(pos % width)
        for a in sorted(aligns):
            raw = blob[a:]
            raw = raw[:len(raw) - len(raw) % width]
            yield enc, raw.decode(enc, errors="replace")


def _normalised(text: str):
    """XMP text as an XML reader sees it, ASCII-lowercased.

    CDATA markers go (their content stays) and every character entity is
    resolved, over the whole text, not a window. Two views: with XML comments
    kept and with them removed, so a comment opened outside a packet cannot
    hide the packet.
    """
    base = _CDATA_MARKS.sub("", text)
    yield html.unescape(base).translate(_ASCII_LOWER)
    if "<!--" in base:
        yield html.unescape(_XML_COMMENT.sub("", base)).translate(_ASCII_LOWER)


def _property_values(low: str, name: str, limit: int = 256):
    """Values of every XML element or attribute (or JSON key) whose local name is `name`.

    Yields (value or None, form); None means the value cannot be read.
    Closing tags are skipped.
    """
    for n, pos in enumerate(_find_all_str(low, name)):
        if n >= limit:
            yield None, "too many occurrences"
            return
        end = pos + len(name)
        if _NAME_CHAR.match(low, end):
            continue  # a longer name, e.g. digitalsourcetypes
        if pos and _NAME_CHAR.match(low, pos - 1):
            continue  # a longer name ending in `name`; a namespace prefix ends in ':'
        if pos and low[pos - 1] == "/" and low[pos - 2:pos] != "</":
            continue  # a URL path segment (the IPTC code URIs), read via its property
        tag = _OPEN_BEFORE.search(low, max(0, pos - 80), pos)
        if tag and tag.group(1):
            continue  # closing tag
        if tag:  # element
            m = _START_TAG_REST.match(low, end)
            if not m:
                yield None, "element"
                continue
            res = _RDF_RESOURCE.search(m.group(1) or "")
            if res:
                yield res.group(2), "rdf:resource"
                continue
            if m.group(2):
                yield None, "empty element"
                continue
            qname = low[tag.start() + 1:end]
            close = re.compile(r"</" + re.escape(qname) + r"\s*>").search(low, m.end())
            if not close:
                yield None, "unterminated element"
                continue
            yield _TAG.sub("", low[m.end():close.start()]), "element"
            continue
        m = _ATTR_VALUE.match(low, end)
        yield (m.group(2), "attribute") if m else (None, "unreadable")


def _source_type_reason(value, form: str, tag: str):
    """None when value is a known non-AI IPTC code, else the reason to refuse."""
    if value is None:
        return f"DigitalSourceType value cannot be read ({form}{tag})"
    v = value.strip()
    code = v
    for base in _IPTC_DST_BASES:
        if v.startswith(base):
            code = v[len(base):]
            break
    if code in NON_AI_SOURCE_TYPES:
        return None
    for token in AI_SOURCE_TYPES:
        if token in v:
            return f"DigitalSourceType {token} ({form}{tag})"
    return f"DigitalSourceType value is not a known non-AI code: {v[:80]!r} ({form}{tag})"


def _scan_text(blob: bytes, reasons: list, label: str = "") -> None:
    """Token search in every encoding, then a normalised XMP property scan.

    label names where a non-raw blob came from (e.g. "iTXt" for a decompressed
    PNG chunk); it stands in for "ascii" in the reasons.
    """
    def where(enc: str) -> str:
        return label if label and enc == "ascii" else enc

    lower = blob.lower()
    matched: list = []
    for token in AI_SOURCE_TYPES:  # longest first; skip the shorter ones it contains
        if any(token in longer for longer in matched):
            continue
        for enc, needle in _encodings(token.encode("ascii")):
            if needle in lower:
                reasons.append(f"DigitalSourceType {token} ({where(enc)})")
                matched.append(token)
    for phrase in AI_PHRASES:
        for enc, needle in _encodings(phrase.encode("ascii")):
            if needle in lower:
                reasons.append(f"AI disclosure text '{phrase}' ({where(enc)})")
    for enc, text in _decoded_texts(blob):
        tag = ", " + where(enc)
        for low in _normalised(text):
            for phrase in AI_PHRASES:
                if phrase in low:
                    reasons.append(f"AI disclosure text '{phrase}' (normalised{tag})")
            for value, form in _property_values(low, "digitalsourcetype"):
                reason = _source_type_reason(value, form, tag)
                if reason:
                    reasons.append(reason)
            for value, form in _property_values(low, "provenance"):
                v = (value or "").strip()
                if value is None or "://" in v or "c2pa" in v or v.startswith("urn:"):
                    reasons.append(f"C2PA remote manifest reference (XMP provenance {form} "
                                   f"{v[:80]!r}{tag})")
            for key in _XMP_TOOL_KEYS:
                for n, pos in enumerate(_find_all_str(low, key)):
                    if n >= 256:
                        break
                    m = _XMP_VALUE.match(low, pos + len(key))
                    if m:
                        value = m.group(2) if m.group(2) is not None else m.group(3)
                        _tool_reason(value, f"XMP {key.strip(':')}", reasons)


def _tool_reason(value, where: str, reasons: list) -> None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = str(value or "").replace("\x00", " ").strip()
    m = GENERATIVE_TOOL.search(value)
    if m:
        reasons.append(f"{where} names a generative tool: {value[:80]!r}")


def _scan_c2pa(data: bytes, reasons: list) -> None:
    if _JUMBF.search(data):
        reasons.append("C2PA/JUMBF manifest box")
    for label in _C2PA_LABELS:
        if label in data:
            reasons.append(f"C2PA label {label.decode('ascii')}")
    if _C2PA_BMFF_UUID in data:
        reasons.append("C2PA uuid box (BMFF)")
    if data[:8] == PNG_SIGNATURE:
        for ctype, _ in _png_chunks(data):
            if ctype == b"caBX":
                reasons.append("C2PA caBX chunk")
    elif data[:2] == b"\xff\xd8":
        # Inside entropy data FF is always followed by 00 or RSTn, so FF EB
        # can only be a real (or hidden) APP11 marker.
        for m in re.finditer(rb"\xff\xeb..JP", data, re.DOTALL):
            reasons.append(f"JPEG APP11 JUMBF segment at offset {m.start()}")
            break
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        i = 12
        while i + 8 <= len(data):
            ctype, size = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
            if ctype.upper() == b"C2PA":
                reasons.append("C2PA chunk (WebP)")
            i += 8 + size + (size & 1)


def _png_chunks(data: bytes):
    """Lenient chunk walk: yields (type, payload) until the data runs out."""
    i, n = 8, len(data)
    while i + 8 <= n:
        length, ctype = struct.unpack(">I4s", data[i:i + 8])
        yield ctype, data[i + 8:min(n, i + 8 + length)]
        if ctype == b"IEND":
            return
        i += 12 + length


def _inflate(payload: bytes) -> bytes:
    try:
        d = zlib.decompressobj()
        return d.decompress(payload, _TEXT_LIMIT)
    except zlib.error:
        return b""


def _png_text_entries(data: bytes):
    """(keyword, text bytes, label) for every tEXt/zTXt/iTXt chunk, decompressed.

    label is "zTXt" or "iTXt" for a compressed chunk, "" for plain text.
    """
    if data[:8] != PNG_SIGNATURE:
        return
    for ctype, p in _png_chunks(data):
        if ctype not in (b"tEXt", b"zTXt", b"iTXt"):
            continue
        key, _, rest = p.partition(b"\x00")
        if ctype == b"tEXt":
            yield key, rest, ""
        elif ctype == b"zTXt":
            yield key, _inflate(rest[1:]), "zTXt"
        else:
            compressed = rest[:1] == b"\x01"
            parts = rest[2:].split(b"\x00", 2)
            text = parts[2] if len(parts) == 3 else b""
            yield key, (_inflate(text) if compressed else text), ("iTXt" if compressed else "")


def _png_text_blobs(data: bytes) -> list:
    """(label, text) of compressed PNG text chunks; plain ones are in the raw-byte scan."""
    return [(label, text) for _, text, label in _png_text_entries(data) if text and label]


def _png_software(data: bytes) -> list:
    return [text for key, text, _ in _png_text_entries(data)
            if key.strip().lower() in (b"software", b"source", b"creator tool", b"creatortool")]


def _xmp_extension_blobs(data: bytes) -> list:
    """Reassemble JPEG extended XMP (split across APP1 segments) per GUID."""
    parts: dict = {}
    for pos in _find_all(data, _XMP_EXT, 4096):
        if pos < 2:
            continue
        seglen = struct.unpack(">H", data[pos - 2:pos])[0]
        head = pos + len(_XMP_EXT)
        guid = data[head:head + 32]
        offset = struct.unpack(">I", data[head + 36:head + 40])[0] if head + 40 <= len(data) else 0
        chunk = data[head + 40:pos - 2 + seglen]
        parts.setdefault(guid, []).append((offset, chunk))
    return [b"".join(c for _, c in sorted(v)) for v in parts.values()]


def _exif_software(data: bytes) -> list:
    """Software tags from every TIFF/EXIF structure in the file, hidden ones included."""
    values = []
    starts = set()
    for sig in (b"II*\x00", b"MM\x00*"):
        starts.update(_find_all(data, sig, 64))
    for start in sorted(starts):
        try:
            exif = Image.Exif()
            exif.load(data[start:start + 4 * 1024 * 1024])
            for tag in (0x0131, 0x000B):  # Software, ProcessingSoftware
                if exif.get(tag):
                    values.append(exif.get(tag))
        except Exception:  # not a real TIFF header: random bytes
            continue
    return values


# ------------------------------------------------------------------- inspect

def inspect(path: str) -> list:
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] == PNG_SIGNATURE:
        found = _png(data)
    elif data[:2] == b"\xff\xd8":
        found = _jpeg(data)
    else:
        found = ["unknown format, cannot verify"]
    found.extend(_backstop(data))
    return _dedupe(found)


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _backstop(data: bytes) -> list:
    found = []
    lower = data.lower()
    for token in BACKSTOP_NOCASE:
        for enc, needle in _encodings(token):
            if needle in lower:
                label = token.decode("ascii") + ("" if enc == "ascii" else f" ({enc})")
                found.append(BACKSTOP_PREFIX + label)
    for token in BACKSTOP_EXACT:
        if token in data:
            found.append(BACKSTOP_PREFIX + token.decode("latin-1").replace("\x00", "\\0"))
    return found


_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _png_raw_size(ihdr: bytes):
    """Bytes of filtered scanlines the IHDR declares, or None if IHDR is odd."""
    if len(ihdr) != 13:
        return None
    w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", ihdr)
    if ctype not in _PNG_CHANNELS or interlace not in (0, 1):
        return None
    bpp = _PNG_CHANNELS[ctype] * depth

    def rows(pw, ph):
        return ph * (1 + (pw * bpp + 7) // 8) if pw and ph else 0

    if not interlace:
        return rows(w, h)
    total = 0
    for xs, ys, dx, dy in _ADAM7:
        pw = (w - xs + dx - 1) // dx if w > xs else 0
        ph = (h - ys + dy - 1) // dy if h > ys else 0
        total += rows(pw, ph)
    return total


def _idat_findings(idat: bytes, expected) -> list:
    if expected is None:
        return ["PNG IHDR missing or invalid"]
    d = zlib.decompressobj()
    total, buf, step = 0, idat, 1 << 20
    try:
        while True:
            out = d.decompress(buf, step)
            total += len(out)
            if total > expected:
                return ["IDAT holds more data than IHDR declares"]
            buf = d.unconsumed_tail
            if d.eof or (not buf and not out):
                break
        if not d.eof:
            total += len(d.flush())
    except zlib.error:
        return ["IDAT is not a valid zlib stream"]
    found = []
    if d.unused_data:
        found.append(f"{len(d.unused_data)} bytes after the zlib stream in IDAT")
    if not d.eof:
        found.append("IDAT zlib stream does not end")
    if total > expected:
        found.append("IDAT holds more data than IHDR declares")
    elif total < expected:
        found.append("IDAT holds less data than IHDR declares")
    return found


def _png(data: bytes) -> list:
    found, i, n = [], 8, len(data)
    ihdr, idat = None, []
    while True:
        if i + 8 > n:
            found.append("PNG ends without IEND")
            return found
        length, ctype = struct.unpack(">I4s", data[i:i + 8])
        end = i + 12 + length
        if end > n:
            found.append(f"PNG chunk {ctype.decode('latin-1')!r} truncated")
            return found
        if ctype not in PNG_ALLOWED:
            found.append(ctype.decode("latin-1"))
        elif ctype == b"IHDR" and ihdr is None:
            ihdr = data[i + 8:i + 8 + length]
        elif ctype == b"IDAT":
            idat.append(data[i + 8:i + 8 + length])
        if ctype == b"IEND":
            found.extend(_idat_findings(b"".join(idat), _png_raw_size(ihdr or b"")))
            if end < n:
                found.append(f"{n - end} bytes after IEND")
            return found
        i = end


def _skip_entropy(data: bytes, j: int) -> int:
    """Return the offset of the first non-RST marker after entropy-coded data."""
    n = len(data)
    while True:
        j = data.find(b"\xff", j)
        if j < 0 or j + 1 >= n:
            return n
        nxt = data[j + 1]
        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:  # stuffed byte or restart marker
            j += 2
        elif nxt == 0xFF:  # fill byte before a marker
            j += 1
        else:
            return j


def _jpeg(data: bytes) -> list:
    found, i, n = [], 2, len(data)
    while True:
        if i >= n:
            found.append("JPEG ends without EOI")
            return found
        if data[i] != 0xFF:
            found.append(f"stray bytes at offset {i}")
            nxt = data.find(b"\xff", i)
            if nxt < 0:
                found.append("JPEG ends without EOI")
                return found
            i = nxt
        while i + 1 < n and data[i + 1] == 0xFF:  # fill bytes
            i += 1
        if i + 1 >= n:
            found.append("JPEG ends without EOI")
            return found
        marker = data[i + 1]
        if marker == 0xD9:  # EOI
            end = i + 2
            if end < n:
                found.append(f"{n - end} bytes after EOI")
            return found
        if marker == 0xD8:
            found.append("embedded image (second SOI)")
            i += 2
            continue
        if marker == 0x00:
            # Decoders skip FF 00 here as two junk bytes and resync on the next
            # FF, so walk the same way; a clean file never has it.
            found.append(f"FF00 outside scan data at offset {i}")
            i += 2
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            i += 2
            continue
        if 0x02 <= marker <= 0xBF or marker == 0xC8:
            found.append(f"reserved JPEG marker 0x{marker:02X}")
        elif 0xF0 <= marker <= 0xFD:
            found.append(f"JPGn marker 0x{marker:02X}")
        if i + 4 > n:
            found.append("JPEG segment truncated")
            return found
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        end = i + 2 + length
        if length < 2 or end > n:
            found.append(f"JPEG segment 0x{marker:02X} has bad length")
            return found
        seg = data[i + 4:end]
        if marker == 0xE0:
            if length != JFIF_APP0_LENGTH or not seg.startswith(JFIF_PREFIX) or seg[12:14] != b"\x00\x00":
                found.append("APP0 is not a plain JFIF header")
        elif 0xE1 <= marker <= 0xEF:
            found.append(APP_NAMES.get(marker, f"APP{marker - 0xE0}"))
        elif marker == 0xFE:
            found.append("COM comment")
        if b"c2pa" in seg.lower() or b"jumb" in seg:
            found.append("C2PA manifest")
        i = end
        if marker == 0xDA:  # start of scan: entropy data runs to the next marker
            i = _skip_entropy(data, i)


# ---------------------------------------------------------------------- main

def _safe_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def _refusal(path: str, reasons: list) -> str:
    return (f"REFUSED  {path}: the file declares AI generation or AI editing "
            f"({'; '.join(reasons)}). clean re-encodes unedited camera photos only; it does not "
            "remove AI provenance. Use the unedited original, or post without this image.")


def main(argv=None) -> int:
    _safe_stdio()
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[0] == "check":
        bad = 0
        for p in argv[1:]:
            try:
                problems = inspect(p)
                problems += [f"AI-declared: {r}" for r in ai_provenance(p)]
            except OSError as e:
                problems = [f"cannot read: {e.strerror or e}"]
            print(f"{'CLEAN' if not problems else 'METADATA'}  {p}  {', '.join(problems)}")
            bad += bool(problems)
        return 1 if bad else 0
    if len(argv) >= 2 and argv[0] == "ai":
        flagged = 0
        for p in argv[1:]:
            try:
                reasons = ai_provenance(p)
            except OSError as e:
                print(f"ERROR  {p}  cannot read: {e.strerror or e}")
                return 1
            print(f"{'AI' if reasons else 'NONE'}  {p}  {'; '.join(reasons)}")
            flagged += bool(reasons)
        return AI_REFUSAL_EXIT if flagged else 0
    if len(argv) == 3 and argv[0] == "clean":
        try:
            clean_file(argv[1], argv[2])
        except AIProvenanceError as e:
            print(_refusal(e.path, e.reasons), file=sys.stderr)
            return AI_REFUSAL_EXIT
        except OSError as e:
            print(f"ERROR  cannot read or write: {e}", file=sys.stderr)
            return 1
        print("CLEAN ", argv[2])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
