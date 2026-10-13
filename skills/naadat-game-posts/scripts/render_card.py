#!/usr/bin/env python3
"""Render the Instagram posts (1080x1350) for a Näädät game.

Input is a game.json (post-game) or a pregame.json (game day), both written by
fetch_game.py or by hand from the templates in references/. The kind is
detected from the file's schema (naadat-game-posts/pregame@1 = game day).

Outputs, all 1080x1350 and metadata-free (see clean_image.py):
  post-game   <out>/result_feed.jpg    slide 1: photo, FINAL, both logos, score,
                                        date, rink, period scores
              <out>/summary_feed.jpg   slide 2: score header, goal log, shots,
                                        power-play goals, goalie saves
                                        (skipped with a note without a goal log)
  game day    <out>/pregame_feed.jpg   photo, GAME DAY, both logos, date,
                                        face-off time, teams, rink
  always      <out>/photo_credit.txt   the credit of the photo used, nothing else

Look: a club poster. A home game is green, an away game orange (from
us_side). A graded photo panel on the left, the title set vertically on the
right, square logo tiles overlapping the photo edge with the scores beside
them, and a ruled info row at the foot. Anton for display type, Barlow for
text and labels.

Photos come from the skill's own library (assets/photos/photos.json), never
from the user. The pick steps through the library game by game: index =
season_index % N, where season_index (written by fetch_game.py) is the game's
place in the season schedule; hand-entered games use the date (days // 3).
The game-day post is offset by two, so it differs from the result posts of
the same and the neighbouring games. --photo-index K picks a library photo by
hand; --photo PATH with --photo-credit uses another real photo. The script
prints the photo, its index and the index to use for a different photo.

A game that is not final is refused (exit 2) unless --allow-unfinished is
given; that card then says LIVE / PROVISIONAL, never FINAL. A photo or
logo_file that declares AI generation or editing (IPTC DigitalSourceType,
C2PA) is refused with exit code 4. logo_file paths resolve relative to the
folder of the JSON file; logo_asset paths (FCAA: 'fcaa/<slug>.png') resolve
under assets/logos and come next, before association_id. Downloaded logos are cached in the system temp
folder, never inside --out. Everything is drawn with Pillow (8.2+) from real
data, bundled fonts, real logo files and real photos; no generative AI.

  python render_card.py game.json --out post/
  python render_card.py pregame.json --out post/
  python render_card.py game.json --out post/ --photo-index 2
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep the (possibly read-only) skill folder free of __pycache__


def _utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_utf8_streams()

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
import unicodedata  # noqa: E402
import urllib.request  # noqa: E402
import zlib  # noqa: E402

try:
    from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("Pillow 8.2+ required: pip install -U pillow")
if not hasattr(ImageDraw.ImageDraw, "rounded_rectangle") or not hasattr(ImageFont.FreeTypeFont, "getlength"):
    sys.exit("Pillow 8.2+ required: pip install -U pillow")

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL_DIR, "assets")
FONTS = os.path.join(ASSETS, "fonts")
PHOTOS = os.path.join(ASSETS, "photos")
sys.path.insert(0, HERE)
import clean_image  # noqa: E402
from clean_image import save_clean  # noqa: E402

try:
    LANCZOS = Image.Resampling.LANCZOS
    BICUBIC = Image.Resampling.BICUBIC
except AttributeError:  # Pillow < 9.1
    LANCZOS = Image.LANCZOS
    BICUBIC = Image.BICUBIC

AI_REFUSAL_EXIT = 4
EXIT_NOT_FINAL = 2
PREGAME_SCHEMA = "naadat-game-posts/pregame@1"
# Fallback when clean_image has no ai_provenance(): byte-pattern hits that mean
# the file declares AI generation or editing (IPTC DigitalSourceType, C2PA).
_AI_BACKSTOP_KEYS = ("digitalsourcetype", "c2pa", "jumb", "cabx")


def ai_marks(path):
    """List of AI-provenance findings in the SOURCE bytes of an input image."""
    fn = getattr(clean_image, "ai_provenance", None)
    if fn is not None:
        res = fn(path)
        if isinstance(res, (list, tuple, set)):
            return [str(r) for r in res]
        return ["declares AI provenance"] if res else []
    prefix = getattr(clean_image, "BACKSTOP_PREFIX", "byte pattern ")
    return [h for h in clean_image.inspect(path)
            if h.startswith(prefix) and any(k in h.lower() for k in _AI_BACKSTOP_KEYS)]


def refuse_if_ai(path, what):
    marks = ai_marks(path)
    if marks:
        print("ERROR: %s %s: this %s declares AI generation or AI editing (%s); use the unedited original"
              % (what, path, "photo" if what == "photo" else "logo file", "; ".join(marks)), file=sys.stderr)
        sys.exit(AI_REFUSAL_EXIT)


def load_photo(path):
    loader = getattr(clean_image, "load_upright", None)
    if loader is not None:
        return loader(path).convert("RGB")
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:  # tolerate a UTF-8 BOM (Windows editors)
        return json.load(f)


# ------------------------------------------------------------------ palette

W, H = 1080, 1350
M = 64                     # outer margin, all four sides
CREAM = (238, 235, 225)
GREEN = (11, 51, 30)
ORANGE = (247, 137, 42)
AWAY_BG = (240, 130, 38)


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


THEMES = {
    "home": {
        "bg": GREEN, "accent": ORANGE, "text": CREAM, "on_accent": GREEN,
        "panel": mix(GREEN, (0, 0, 0), 0.34),        # loser / neutral tile
        "title": mix(GREEN, (92, 178, 122), 0.52),    # vertical title, tonal
        "board": mix(GREEN, (0, 0, 0), 0.30),         # rink-board corners
        "contour": mix(GREEN, (90, 170, 120), 0.20),
        "rule": mix(GREEN, CREAM, 0.24),
        "dim": mix(GREEN, CREAM, 0.66),               # labels, assists
        "faint": mix(GREEN, CREAM, 0.52),             # their goals, captions
        "loser": mix(GREEN, CREAM, 0.42),             # losing score
        "duo_dark": (4, 24, 14), "duo_light": (246, 240, 226),
    },
    "away": {
        "bg": AWAY_BG, "accent": GREEN, "text": GREEN, "on_accent": ORANGE,
        "panel": (204, 92, 12),
        "title": (186, 74, 4),
        "board": (214, 102, 18),
        "contour": (250, 152, 64),
        "rule": mix(AWAY_BG, GREEN, 0.40),
        "dim": mix(AWAY_BG, GREEN, 0.86),
        "faint": mix(AWAY_BG, GREEN, 0.74),
        "loser": mix(AWAY_BG, GREEN, 0.72),
        "duo_dark": (8, 34, 20), "duo_light": (252, 238, 220),
    },
}


# ------------------------------------------------------------------ type

_FC = {}
FONT_FILES = {
    "display": "Anton-Regular.ttf",
    "cond": "BarlowCondensed-Medium.ttf",
    "cond_r": "BarlowCondensed-Regular.ttf",
    "cond_sb": "BarlowCondensed-SemiBold.ttf",
    "cond_b": "BarlowCondensed-Bold.ttf",
    "label": "Barlow-SemiBold.ttf",
    "label_b": "Barlow-Bold.ttf",
}


def font(name, size):
    key = (name, int(size))
    if key not in _FC:
        _FC[key] = ImageFont.truetype(os.path.join(FONTS, FONT_FILES[name]), int(size))
    return _FC[key]


def tracked(d, xy, text, f, fill, track=0.0, anchor="ls"):
    """Draw text with letter spacing (track in em). anchor: l?/r?/m? + baseline row."""
    sp = f.size * track
    widths = [f.getlength(c) for c in text]
    total = sum(widths) + sp * max(0, len(text) - 1)
    x, y = xy
    if anchor[0] == "r":
        x -= total
    elif anchor[0] == "m":
        x -= total / 2
    for c, w in zip(text, widths):
        d.text((x, y), c, font=f, fill=fill, anchor="l" + anchor[1])
        x += w + sp
    return total


def tracked_len(text, f, track):
    return sum(f.getlength(c) for c in text) + f.size * track * max(0, len(text) - 1)


LABEL_TRACK = 0.16


def label(d, xy, text, color, size=19, anchor="ls"):
    return tracked(d, xy, text.upper(), font("label", size), color, LABEL_TRACK, anchor)


def label_len(text, size):
    return tracked_len(text.upper(), font("label", size), LABEL_TRACK)


def fit_size(text, name, start, max_w, min_size=10):
    s = start
    while s > min_size and font(name, s).getlength(text) > max_w:
        s -= 1
    return s


def segs_len(segs, f):
    return sum(f.getlength(s) for s, _ in segs)


def draw_segs(d, xy, segs, f, anchor="ls"):
    """Draw [(text, color), ...] as one run from xy (anchor l or r)."""
    x, y = xy
    if anchor[0] == "r":
        x -= segs_len(segs, f)
    for s, c in segs:
        d.text((x, y), s, font=f, fill=c, anchor="l" + anchor[1])
        x += f.getlength(s)


# ------------------------------------------------------------------ background

def grain(img, seed, amount=4):
    rng = random.Random(seed)
    n = 256
    tile = Image.frombytes("L", (n, n), bytes(rng.randint(128 - amount * 3, 128 + amount * 3) for _ in range(n * n)))
    tile = tile.filter(ImageFilter.GaussianBlur(0.6))
    full = Image.new("L", (W, H))
    for y in range(0, H, n):
        for x in range(0, W, n):
            full.paste(tile, (x, y))
    noise = Image.merge("RGB", (full, full, full))
    return ImageChops.add(img.convert("RGB"), noise, 1.0, -128)


def contours(t, seed):
    """Fine topographic lines in two diagonal corners, like a printed relief map."""
    rng = random.Random(seed)
    gw, gh = 7, 9
    small = Image.new("L", (gw, gh))
    small.putdata([rng.randint(30, 225) for _ in range(gw * gh)])
    field = small.resize((W, H), BICUBIC).filter(ImageFilter.GaussianBlur(24))
    lines = Image.new("L", (W, H), 0)
    for level in range(36, 230, 9):
        b = field.point(lambda v, lv=level: 255 if v > lv else 0)
        lines = ImageChops.lighter(lines, b.filter(ImageFilter.FIND_EDGES))
    lines = lines.point(lambda v: 255 if v > 60 else 0).filter(ImageFilter.GaussianBlur(0.5))
    region = Image.new("L", (W, H), 0)
    rd = ImageDraw.Draw(region)
    rd.ellipse((-420, 700, 520, 1640), fill=255)
    rd.ellipse((700, -360, 1400, 420), fill=255)
    region = region.filter(ImageFilter.GaussianBlur(110))
    layer = Image.new("RGBA", (W, H), t["contour"] + (0,))
    layer.putalpha(ImageChops.multiply(lines, region))
    return layer


_BG = {}


def background(theme, seed):
    key = (theme, seed)
    if key not in _BG:
        t = THEMES[theme]
        img = Image.new("RGBA", (W, H), t["bg"] + (255,))
        img.alpha_composite(contours(t, seed))
        # rink boards: the four rounded corners of a rink, traced inside the edge
        band = Image.new("L", (W, H), 0)
        ImageDraw.Draw(band).rounded_rectangle((18, 18, W - 18, H - 18), radius=120, outline=255, width=12)
        arm = 230
        keep = Image.new("L", (W, H), 0)
        kd = ImageDraw.Draw(keep)
        for x0, y0 in ((0, 0), (W - arm, 0), (0, H - arm), (W - arm, H - arm)):
            kd.rectangle((x0, y0, x0 + arm, y0 + arm), fill=255)
        layer = Image.new("RGBA", (W, H), t["board"] + (0,))
        layer.putalpha(ImageChops.multiply(band, keep))
        img.alpha_composite(layer)
        _BG[key] = img
    return _BG[key].copy()


# ------------------------------------------------------------------ photo

def graded_photo(photo, size, focus, t):
    """Fit the photo to size around focus and grade it toward the theme."""
    w, h = size
    ph = ImageOps.fit(photo, (w, h), LANCZOS, centering=focus)
    lum = ImageOps.autocontrast(ph.convert("L"), cutoff=0.5)
    duo = ImageOps.colorize(lum, t["duo_dark"], t["duo_light"], mid=mix(t["duo_dark"], t["duo_light"], 0.45))
    ph = ImageEnhance.Color(ph).enhance(0.92)
    ph = Image.blend(ph, duo, 0.24)
    ph = ImageEnhance.Contrast(ph).enhance(1.06)
    vig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vig).ellipse((-w * 0.35, -h * 0.25, w * 1.35, h * 1.25), fill=255)
    vig = ImageOps.invert(vig.filter(ImageFilter.GaussianBlur(120))).point(lambda v: int(v * 0.35))
    return Image.composite(Image.new("RGB", (w, h), t["duo_dark"]), ph, vig)


def photo_library():
    path = os.path.join(PHOTOS, "photos.json")
    try:
        lib = load_json(path)
    except (OSError, ValueError) as exc:
        sys.exit("photo library missing or unreadable: %s (%s)" % (path, exc))
    if not isinstance(lib, list) or not lib:
        sys.exit("photo library is empty: %s" % path)
    return lib


def stable_key(game):
    """A number that steps by one from one game to the next, so the photo rotates.

    fetch_game.py writes season_index (the game's place in our season schedule).
    Hand-entered games fall back to the date: games are rarely under three days
    apart, so days // 3 also advances by about one per game.
    """
    idx = game.get("season_index")
    if isinstance(idx, int) and idx >= 0:
        return idx
    try:
        return dt.date.fromisoformat(str(game.get("date"))).toordinal() // 3
    except ValueError:
        return zlib.crc32(json.dumps(game, sort_keys=True, default=str).encode("utf-8"))


def pick_photo(game, pregame, args):
    """(path, credit, focus) of the photo for this post."""
    if args.photo:
        return args.photo, args.photo_credit.strip(), (0.5, 0.4)
    lib = photo_library()
    if args.photo_index is not None:
        if not 0 <= args.photo_index < len(lib):
            sys.exit("--photo-index must be 0..%d; the library has: %s"
                     % (len(lib) - 1, ", ".join("%d=%s" % (i, e.get("file")) for i, e in enumerate(lib))))
        i = args.photo_index
    else:
        i = (stable_key(game) + (2 if pregame else 0)) % len(lib)
    e = lib[i]
    focus = e.get("focus") or [0.5, 0.4]
    pick_photo.index = (i, len(lib))
    return os.path.join(PHOTOS, e["file"]), str(e.get("credit") or "").strip(), (float(focus[0]), float(focus[1]))


# ------------------------------------------------------------------ logos & tiles

GAME_DIR = "."        # folder of the JSON file; relative logo_file paths resolve here
_LOGOS = {}           # (name, is_us) -> (image or None, kind); one lookup and one note per team
CFG = {}


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def strip_white(logo):
    """Remove a white background connected to the logo's edge (club PNGs often have one)."""
    im = logo.convert("RGBA")
    im.thumbnail((400, 400), LANCZOS)
    w, h = im.size
    a = im.getchannel("A")
    border = [a.getpixel((x, y)) for x in range(0, w, 4) for y in (0, h - 1)] + \
             [a.getpixel((x, y)) for y in range(0, h, 4) for x in (0, w - 1)]
    if sum(1 for v in border if v < 16) > len(border) * 0.5:
        return im                     # already cut out: leave it as the club made it
    px = im.load()
    seen = bytearray(w * h)
    stack = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    while stack:
        x, y = stack.pop()
        i = y * w + x
        if seen[i]:
            continue
        r, g, b, al = px[x, y]
        if al > 0 and min(r, g, b) < 225:
            continue
        seen[i] = 1
        px[x, y] = (r, g, b, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                stack.append((nx, ny))
    a = im.getchannel("A").filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    im.putalpha(a)
    return im


def team_logo(team):
    """(RGBA image or None, kind) with kind in brand | logo | badge."""
    key = (team.get("name"), bool(team.get("is_us")))
    if key not in _LOGOS:
        _LOGOS[key] = _find_logo(team)
    return _LOGOS[key]


LOGO_DIR = os.path.join(ASSETS, "logos")


def slug(name):
    """fcaahockey.app logo slug: lowercased, accents stripped, other characters -> '-'."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _inside_logos(rel):
    """Absolute path of rel under assets/logos, or None if it would leave that folder."""
    root = os.path.normpath(LOGO_DIR)
    p = os.path.normpath(os.path.join(root, str(rel).replace("\\", "/")))
    try:
        return p if os.path.commonpath([p, root]) == root else None
    except ValueError:          # another drive on Windows
        return None


def _fcaa_index():
    if "_fcaa" not in _LOGOS:
        try:
            idx = load_json(os.path.join(LOGO_DIR, "fcaa", "index.json"))
        except (OSError, ValueError):
            idx = {}
        _LOGOS["_fcaa"] = {slug(k): v for k, v in idx.items()
                           if not k.startswith("_") and isinstance(v, str)}
    return _LOGOS["_fcaa"]


def asset_logo(team):
    """Path of the team's logo under assets/logos from 'logo_asset' (e.g. 'fcaa/hc-lumikot.png').

    A logo_asset that is missing falls back to assets/logos/fcaa/index.json, matched
    by the slug of the file name and then of the team name (PUS-Hockey maps to its
    III-divisioona logo there)."""
    rel = team.get("logo_asset")
    if not rel:
        return None
    p = _inside_logos(rel)
    if p and os.path.isfile(p):
        return p
    idx = _fcaa_index()
    for key in (slug(os.path.splitext(os.path.basename(str(rel)))[0]), slug(team.get("name"))):
        if key in idx:
            q = _inside_logos(os.path.join("fcaa", idx[key]))
            if q and os.path.isfile(q):
                return q
    print("warning: logo_asset for %s not found under assets/logos: %s" % (team.get("name"), rel), file=sys.stderr)
    return None


def _find_logo(team):
    name = team.get("name") or "opponent"
    if team.get("is_us"):
        p = os.path.join(ASSETS, "brand", CFG.get("logo") or "naadat_n_logo.png")
        return Image.open(p).convert("RGBA"), "brand"
    candidates = []
    lf = team.get("logo_file")          # a file the user supplied wins over the bundled ones
    if lf:
        p = lf if os.path.isabs(lf) else os.path.join(GAME_DIR, lf)
        if os.path.exists(p):
            candidates.append(p)
        else:
            print("warning: logo_file for %s not found: %s" % (name, p), file=sys.stderr)
    ap_ = asset_logo(team)              # bundled logo, e.g. FCAA: fcaa/<slug>.png
    if ap_:
        candidates.append(ap_)
    if team.get("association_id"):
        candidates.append(os.path.join(ASSETS, "logos", "%s.png" % team["association_id"]))
    for c in candidates:
        if os.path.exists(c):
            try:
                return strip_white(Image.open(c)), "logo"
            except Exception as exc:  # noqa: BLE001
                print("warning: could not read logo %s: %s" % (c, exc), file=sys.stderr)
    url = team.get("logo_url")
    if url:
        try:
            cache = os.path.join(tempfile.gettempdir(), "naadat-game-posts-logos")
            os.makedirs(cache, exist_ok=True)
            dest = os.path.join(cache, "logo_%s.png" % (team.get("association_id") or zlib.crc32(url.encode())))
            req = urllib.request.Request(url, headers={"user-agent": "naadat-game-posts/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                f.write(r.read())
            marks = ai_marks(dest)
            if marks:
                raise ValueError("downloaded logo declares AI provenance (%s)" % "; ".join(marks))
            return strip_white(Image.open(dest)), "logo"
        except Exception as exc:  # noqa: BLE001  fall back to a badge, but say so
            print("warning: could not download logo %s: %s" % (url, exc), file=sys.stderr)
    print("note: no logo for %s, using a lettered badge" % name, file=sys.stderr)
    return None, "badge"


def abbrev(name):
    """2-3 letter badge text. Never a single letter (a lone O reads as a zero)."""
    words = ["".join(ch for ch in w if ch.isalpha()) for w in (name or "").replace("-", " ").split()]
    words = [w for w in words if w]
    if not words:
        return "OPP"
    if len(words) == 1:
        out = words[0][:3]
    else:
        out = "".join(w[0] for w in words[:3])
        if len(out) < 2:
            out = words[0][:3]
    out = out.upper()
    return out if len(out) >= 2 else "OPP"


_MARKS = {}
BRAND_RING = (6, 30, 17)             # keyline round our N where its orange outline meets an orange tile


def brand_mark(logo, s, fill):
    """Our crest (a cut-out N: green body, orange outline) centred on an s x s tile.

    The N fills 82 % of the tile height. Where the tile is about as light as the N's
    own outline (an orange tile: a win at home, any tile on an away card), a dark
    green keyline is set round the N so its outline still separates from the tile."""
    key = (id(logo), s, tuple(fill))
    if key in _MARKS:
        return _MARKS[key]
    box = int(round(s * 0.82))
    lg = logo.copy()
    k = box / max(lg.size)
    lg = lg.resize((max(1, int(round(lg.width * k))), max(1, int(round(lg.height * k)))), LANCZOS)
    a = lg.getchannel("A")
    # colour of the outermost ring of the mark (its outline)
    inner = a.filter(ImageFilter.MinFilter(5))
    rgb_b, a_b, i_b = lg.convert("RGB").tobytes(), a.tobytes(), inner.tobytes()
    ring = [tuple(rgb_b[3 * i:3 * i + 3]) for i in range(len(a_b)) if a_b[i] > 200 and i_b[i] < 64]
    ring_lum = sum(lum(p) for p in ring) / len(ring) if ring else 0
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ox, oy = (s - lg.width) // 2, (s - lg.height) // 2
    if abs(ring_lum - lum(fill)) < 60:
        grow = max(3, int(s * 0.03)) | 1
        edge = Image.new("L", (s, s), 0)
        edge.paste(a, (ox, oy))
        edge = edge.filter(ImageFilter.MaxFilter(2 * grow + 1)).filter(ImageFilter.GaussianBlur(0.7))
        keyline = Image.new("RGBA", (s, s), BRAND_RING + (0,))
        keyline.putalpha(edge)
        out.alpha_composite(keyline)
    out.alpha_composite(lg, (ox, oy))
    _MARKS[key] = out
    return out


def tile(img, team, x, y, s, fill, t):
    """Square logo tile: the tile colour, then the logo (or a lettered badge) on it."""
    d = ImageDraw.Draw(img)
    logo, kind = team_logo(team)
    if kind == "brand":
        d.rectangle((x, y, x + s - 1, y + s - 1), fill=fill)
        img.alpha_composite(brand_mark(logo, s, fill), (x, y))
        return
    d.rectangle((x, y, x + s - 1, y + s - 1), fill=fill)
    if logo is None:
        letters = abbrev(team.get("name"))
        f = font("display", int(s * 0.42))
        while f.getlength(letters) > s * 0.76 and f.size > 12:
            f = font("display", f.size - 2)
        d.text((x + s / 2, y + s / 2), letters, font=f, fill=t["text"] if fill != t["accent"] else t["on_accent"],
               anchor="mm")
        return
    lg = logo.copy()
    box = int(s * 0.74)
    lg.thumbnail((box, box), LANCZOS)
    if lg.width < box and lg.height < box:
        k = box / max(lg.size)
        lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), LANCZOS)
    # low contrast against the tile? give the logo a crisp cream die-cut edge
    a = lg.getchannel("A")
    rgb_b, a_b = lg.convert("RGB").tobytes(), a.tobytes()
    px = [tuple(rgb_b[3 * i:3 * i + 3]) for i in range(len(a_b)) if a_b[i] > 128]
    low = sum(1 for p in px if abs(lum(p) - lum(fill)) < 55) / max(1, len(px))
    if low > 0.35:
        edge = a.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(0.6))
        cut = Image.new("RGBA", lg.size, CREAM + (0,))
        cut.putalpha(edge)
        pad = Image.new("RGBA", lg.size, (0, 0, 0, 0))
        pad.alpha_composite(cut)
        pad.alpha_composite(lg)
        lg = pad
    img.alpha_composite(lg, (x + (s - lg.width) // 2, y + (s - lg.height) // 2))


# ------------------------------------------------------------------ game facts

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DOW_LONG = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def date_parts(iso):
    """(weekday index or None, '18.10.', '18.10.2026') for an ISO date."""
    try:
        y, m, d = (int(v) for v in str(iso)[:10].split("-"))
        return dt.date(y, m, d).weekday(), "{}.{}.".format(d, m), "{}.{}.{}".format(d, m, y)
    except (TypeError, ValueError):
        return None, str(iso or ""), str(iso or "")


def goals_of(team):
    try:
        return int(team.get("goals") or 0)
    except (TypeError, ValueError):
        return 0


def is_final(game):
    """True for a finished game. A file without 'status' counts as final when it carries a result."""
    st = game.get("status")
    if st is None:
        return bool((game.get("result") or "").strip())
    return str(st).lower() == "final"


def winner_side(game):
    """'home', 'away' or None (tie or not final). game['result'] wins over goals."""
    if not is_final(game):
        return None
    if "result" in game:
        r = (game.get("result") or "").upper()
        us = game.get("us_side", "home")
        if r in ("W", "OTW", "SOW"):
            return us
        if r in ("L", "OTL", "SOL"):
            return "away" if us == "home" else "home"
        return None
    h, a = goals_of(game["home"]), goals_of(game["away"])
    if h == a:
        return None
    return "home" if h > a else "away"


def finish_word(game):
    fin = (game.get("finish") or "").upper()
    return fin if fin in ("OT", "SO") else ""


def status_segs(game, t, color):
    """'FULL TIME · OVERTIME' (OT part in accent), or PROVISIONAL for a game not final."""
    if not is_final(game):
        return [("PROVISIONAL", color)]
    fin = finish_word(game)
    segs = [("FULL TIME", color)]
    if fin:
        segs.append((" · " + {"OT": "OVERTIME", "SO": "SHOOTOUT"}[fin], t["accent"]))
    return segs


def period_rows(game):
    """[(label, 'h–a', is_extra)] from game['periods'], in order."""
    out = []
    for p in game.get("periods") or []:
        try:
            hs, as_ = int(p.get("home")), int(p.get("away"))
        except (TypeError, ValueError):
            continue
        lbl = str(p.get("label") or "").upper()
        extra = lbl in ("OT", "SO", "4", "5", "JA", "VL")
        if lbl in ("4", "JA"):
            lbl = "OT"
        elif lbl in ("5", "VL"):
            lbl = "SO"
        out.append((lbl, "{}–{}".format(hs, as_), extra))
    return out


def period_segs(game, t, color):
    segs = []
    for i, (lbl, sc, extra) in enumerate(period_rows(game)):
        if i:
            segs.append(("   ", color))
        if extra:
            segs.append((lbl + " ", t["accent"]))
        segs.append((sc, color))
    return segs


# ------------------------------------------------------------------ names

def _clashes(game):
    """{(team, last name)} where two different players of one team share a last name."""
    if "_clash" in game:
        return game["_clash"]
    seen = {}

    def add(team, n):
        if isinstance(n, dict) and (n.get("last") or "").strip():
            key = (team, n["last"].strip().lower())
            seen.setdefault(key, set()).add((n.get("full") or n.get("first") or "").strip().lower())

    for g in game.get("goals") or []:
        add(g.get("team"), g.get("scorer"))
        for a in g.get("assists") or []:
            add(g.get("team"), a)
    game["_clash"] = {k for k, v in seen.items() if len(v) > 1}
    return game["_clash"]


def _short(n):
    first, last = (n.get("first") or "").strip(), (n.get("last") or "").strip()
    if first and last:
        return "{}. {}".format(first[0], last)
    return (n.get("short") or "").strip() or last or (n.get("full") or "").strip()


def log_name(n, game, team, scorer):
    """Scorer: 'I. Koskimäki'; assist: 'Koskimäki'. When the data's 'display' form is
    disambiguated ('Justus Taivalmaa', 'N. Raassina'), that form is used as is."""
    if isinstance(n, str):
        return n.strip() or "?"
    n = n or {}
    last = (n.get("last") or "").strip()
    disp = (n.get("display") or "").strip()
    if disp and last and disp.lower() != last.lower():
        return disp
    if not disp and last and (team, last.lower()) in _clashes(game):
        full = (n.get("full") or "").strip()
        return full or _short(n)
    if scorer:
        return _short(n) or disp or "?"
    return disp or last or (n.get("full") or "").strip() or "?"


def assist_names(g, game):
    """Assist names; an assist that would read like the scorer gets its fuller form."""
    team = g.get("team")
    sc = g.get("scorer") or {}
    sc_last = (sc.get("last") or "").strip().lower() if isinstance(sc, dict) else ""
    out = []
    for a in g.get("assists") or []:
        nm = log_name(a, game, team, False)
        if isinstance(a, dict) and sc_last and nm.lower() == sc_last:
            # same last name as the scorer: never let it read as a self-assist
            nm = (a.get("full") or "").strip() or _short(a) or nm
        if nm and nm != "?":
            out.append(nm)
    return out


def last_name(n):
    if isinstance(n, str):
        return n
    n = n or {}
    return (n.get("display") or "").strip() or (n.get("last") or "").strip() or (n.get("full") or "").strip() or "?"


# ------------------------------------------------------------------ shared pieces

PHOTO = (M, 116, 676, 1132)          # photo panel
TILE = 172                           # logo tile edge
TILE_X = 598                         # tiles overlap the photo's right edge
TILE_Y2 = PHOTO[3] - TILE + 18       # lower tile drops 18 px below the photo
TILE_Y1 = TILE_Y2 - TILE - 14
SCORE_X = TILE_X + TILE + 34
GRID = [M, 360, TILE_X]              # info row columns when the content allows
INFO_Y = 1196
TITLE_GAP = 44                       # vertical title foot to upper tile row


def vertical_title(img, parts, x_right, y_top, max_len, max_w):
    """Title rotated to read bottom-to-top, right edge on the margin. parts = [(text, color)]."""
    f = font("display", 300)
    gap = f.getlength(" ")
    total = sum(f.getlength(p) for p, _ in parts) + gap * (len(parts) - 1)
    tmp = Image.new("RGBA", (int(total) + 40, 460), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    x = 10
    for p, c in parts:
        d.text((x, 40), p, font=f, fill=c + (255,))
        x += f.getlength(p) + gap
    tmp = tmp.crop(tmp.getbbox())
    k = min(max_len / tmp.width, max_w / tmp.height)
    tmp = tmp.resize((int(tmp.width * k), int(tmp.height * k)), LANCZOS)
    rot = tmp.rotate(90, expand=True)
    img.alpha_composite(rot, (x_right - rot.width, y_top))
    return rot.size


def top_row(img, t, left, right_segs):
    d = ImageDraw.Draw(img)
    base = M + 14
    if left:
        w = label(d, (M, base), left, t["text"], 22)
        d.rectangle((M, base + 14, M + min(40, w), base + 18), fill=t["accent"])
    f = font("label", 22)
    x = W - M
    for s, c in reversed(right_segs):
        x -= tracked(d, (x, base), s.upper(), f, c, LABEL_TRACK, "rs")
        x -= f.size * LABEL_TRACK


def info_row(img, t, cols, y):
    """Ruled row of LABEL / value columns. cols = [(label, segs)], segs = [(text, color)].

    Columns sit on the poster grid when their content fits it; otherwise they are
    packed by content width with a fixed gutter, and the type shrinks if needed."""
    d = ImageDraw.Draw(img)
    d.line((M, y, W - M, y), fill=t["rule"], width=2)
    cols = [(lab, segs) for lab, segs in cols if segs and "".join(s for s, _ in segs).strip()]
    if not cols:
        return
    gutter, lab_size = 52, 17
    size = 38
    while True:
        f = font("cond_sb", size)
        widths = [max(label_len(lab, lab_size), segs_len(segs, f)) for lab, segs in cols]
        grid = GRID[:len(cols)]
        ok = all(grid[i] + widths[i] + gutter <= (grid[i + 1] if i + 1 < len(cols) else W - M + gutter)
                 for i in range(len(cols)))
        if ok:
            xs = grid
            break
        if sum(widths) + gutter * (len(cols) - 1) <= W - 2 * M:
            xs, x = [], M
            for wdt in widths:
                xs.append(x)
                x += wdt + gutter
            break
        if size <= 22:
            xs = grid
            break
        size -= 1
    for x, (lab, segs) in zip(xs, cols):
        label(d, (x, y + 40), lab, t["dim"], lab_size)
        draw_segs(d, (x, y + 86), segs, f)


def fit_display(txt, max_w, max_h, start=260):
    """Largest Anton size whose ink box fits max_w x max_h."""
    s = start
    while s > 20:
        f = font("display", s)
        l_, t_, r_, b_ = f.getbbox(txt, anchor="ls")
        if r_ - l_ <= max_w and b_ - t_ <= max_h:
            return f
        s -= 2
    return font("display", s)


def draw_score(img, txt, x, cy, f, color, outline, t):
    """Score digits, ink centred on cy. A losing score is set as an outline."""
    d = ImageDraw.Draw(img)
    l_, t_, r_, b_ = f.getbbox(txt, anchor="ls")
    base = cy - (t_ + b_) / 2
    if outline:
        d.text((x - l_, base), txt, font=f, fill=t["bg"], anchor="ls", stroke_width=7, stroke_fill=color)
    else:
        d.text((x - l_, base), txt, font=f, fill=color, anchor="ls")


def photo_panel(img, t, photo, focus):
    x0, y0, x1, y1 = PHOTO
    img.paste(graded_photo(photo, (x1 - x0, y1 - y0), focus, t), (x0, y0))


def weekday_title(i, style="short"):
    if i is None:
        return ""
    return (DOW if style == "short" else DOW_LONG)[i]


# ------------------------------------------------------------------ result card

def result_card(game, photo, focus):
    theme = "away" if game.get("us_side") == "away" else "home"
    t = THEMES[theme]
    img = background(theme, 11)
    photo_panel(img, t, photo, focus)
    final = is_final(game)
    fin = finish_word(game)
    parts = [("FINAL" if final else "LIVE", t["title"])]
    if final and fin:
        parts.append((fin, t["accent"]))
    vertical_title(img, parts, W - M, PHOTO[1], TILE_Y1 - TITLE_GAP - PHOTO[1], W - M - PHOTO[2] - 34)

    home, away = game["home"], game["away"]
    hg, ag = goals_of(home), goals_of(away)
    win = winner_side(game)
    # one size for both scores, so 9 and 18 stand on the same body; the ink is kept
    # 36 px short of the row pitch so stacked digits never touch (9 over 8 is not 98)
    pitch = TILE_Y2 - TILE_Y1
    f = None
    for txt in (str(hg), str(ag)):
        cand = fit_display(txt, W - M - SCORE_X - 6, pitch - 36)
        if f is None or cand.size < f.size:
            f = cand
    for team, side, y, own in ((home, "home", TILE_Y1, hg), (away, "away", TILE_Y2, ag)):
        won = win == side
        lost = win is not None and not won
        tile(img, team, TILE_X, y, TILE, t["accent"] if won else t["panel"], t)
        color = t["accent"] if won else (t["loser"] if lost else t["text"])
        draw_score(img, str(own), SCORE_X, y + TILE / 2, f, color, lost, t)

    wd, short, full = date_parts(game.get("date"))
    top_row(img, t, game.get("competition") or "", status_segs(game, t, t["text"]))
    date_txt = ("{} {}".format(weekday_title(wd).title(), full)).strip()
    info_row(img, t, [("DATE", [(date_txt, t["text"])]),
                      ("ARENA", [(game.get("arena") or "", t["text"])]),
                      ("PERIODS", period_segs(game, t, t["text"]))], INFO_Y)
    return img


# ------------------------------------------------------------------ pregame card

def pregame_card(game, photo, focus):
    theme = "away" if game.get("us_side") == "away" else "home"
    t = THEMES[theme]
    img = background(theme, 12)
    photo_panel(img, t, photo, focus)
    vertical_title(img, [("GAME DAY", t["title"])], W - M, PHOTO[1], TILE_Y1 - TITLE_GAP - PHOTO[1],
                   W - M - PHOTO[2] - 34)

    home, away = game["home"], game["away"]
    for team, y in ((home, TILE_Y1), (away, TILE_Y2)):
        tile(img, team, TILE_X, y, TILE, t["accent"] if team.get("is_us") else t["panel"], t)
    d = ImageDraw.Draw(img)
    wd, short, full = date_parts(game.get("date"))
    avail = W - M - SCORE_X
    label(d, (SCORE_X, TILE_Y1 + 40), weekday_title(wd, "long") or "DATE", t["dim"], 19)
    f = font("display", fit_size(short, "display", 92, avail))
    d.text((SCORE_X, TILE_Y1 + TILE - 14), short, font=f, fill=t["text"], anchor="ls")
    label(d, (SCORE_X, TILE_Y2 + 40), "FACE-OFF", t["dim"], 19)
    tm = (game.get("time") or "").strip() or "TBA"
    f = font("display", fit_size(tm, "display", 92, avail))
    d.text((SCORE_X, TILE_Y2 + TILE - 14), tm, font=f, fill=t["accent"], anchor="ls")

    top_row(img, t, game.get("competition") or "",
            [("HOME GAME" if theme == "home" else "AWAY GAME", t["text"])])
    info_row(img, t, [("HOME", [(home.get("name") or "", t["text"])]),
                      ("AWAY", [(away.get("name") or "", t["text"])]),
                      ("ARENA", [(game.get("arena") or "", t["text"])])], INFO_Y)
    return img


# ------------------------------------------------------------------ summary card

def period_name(p):
    return {1: "1ST PERIOD", 2: "2ND PERIOD", 3: "3RD PERIOD", 4: "OVERTIME"}.get(p, "PERIOD {}".format(p))


def _period_key(g):
    p = g.get("period")
    try:
        return int(p)
    except (TypeError, ValueError):
        s = str(p or "").upper()
        return {"OT": 4, "JA": 4, "SO": 5, "VL": 5}.get(s, s or None)


def goal_type(g):
    for k in ("type", "type_raw"):
        v = (g.get(k) or "").strip().upper()
        v = {"YV": "PP", "AV": "SH", "TM": "EN"}.get(v, v)
        if v in ("PP", "SH", "EN", "PS"):
            return v
    return ""


def log_goals(game):
    """Goal log without shootout attempts, each with a score_after we can show."""
    goals = [g for g in game.get("goals") or [] if _period_key(g) != 5]
    us_home = game.get("us_side") != "away"
    complete = len(goals) == goals_of(game["home"]) + goals_of(game["away"]) - (
        1 if finish_word(game) == "SO" else 0)
    h = a = 0
    out = []
    for g in goals:
        g = dict(g)
        home_goal = (g.get("team") == "us") == us_home
        h, a = (h + 1, a) if home_goal else (h, a + 1)
        sc = (g.get("score_after") or "").strip()
        if not sc:
            sc = "{}-{}".format(h, a) if complete else ""
        g["_score"] = sc.replace("-", "–")
        out.append(g)
    return out, complete


def period_scores_by_key(game):
    out = {}
    for lbl, sc, _ in period_rows(game):
        key = {"OT": 4, "SO": 5}.get(lbl)
        if key is None:
            try:
                key = int(lbl)
            except ValueError:
                continue
        out[key] = sc
    return out


def summary_stats(game):
    """[(LABEL, home value, away value, caption)] for the stats band, home first like the header."""
    home, away = game["home"], game["away"]
    us_home = game.get("us_side") != "away"
    names = "{} · {}".format(home.get("name") or "", away.get("name") or "")
    stats = []
    shots = game.get("shots") or {}
    if shots.get("us") is not None and shots.get("them") is not None:
        hs, as_ = (shots["us"], shots["them"]) if us_home else (shots["them"], shots["us"])
        stats.append(("SHOTS", hs, as_, names))
    goals = [g for g in game.get("goals") or [] if _period_key(g) != 5]
    typed = any(goal_type(g) for g in goals) or (game.get("source") not in (None, "", "manual"))
    if goals and typed:
        pp_us = sum(1 for g in goals if goal_type(g) == "PP" and g.get("team") == "us")
        pp_them = sum(1 for g in goals if goal_type(g) == "PP" and g.get("team") != "us")
        stats.append(("POWER-PLAY GOALS", pp_us if us_home else pp_them, pp_them if us_home else pp_us, names))
    gk = [g for g in game.get("goalies") or [] if g.get("saves") is not None]
    if gk:
        def side(is_home):
            want_us = is_home == us_home
            return [g for g in gk if bool(g.get("is_us")) == want_us]
        gh, ga = side(True), side(False)
        if gh or ga:
            def total(lst):
                return sum(int(g.get("saves") or 0) for g in lst) if lst else None

            def nm(lst):
                return " / ".join(last_name(g.get("name")) for g in lst) or "–"
            stats.append(("SAVES", total(gh), total(ga), "{} · {}".format(nm(gh), nm(ga))))
    return stats


def draw_pair(d, x, base, a, b, f, color, dash_color):
    """'a – b' in display type with a drawn dash (Anton's en dash is too short)."""
    sa = "–" if a is None else str(a)
    sb = "–" if b is None else str(b)
    d.text((x, base), sa, font=f, fill=color, anchor="ls")
    x += f.getlength(sa) + f.size * 0.12
    dash = f.size * 0.30
    cy = base - f.size * 0.42
    d.line((x, cy, x + dash, cy), fill=dash_color, width=max(4, int(f.size * 0.08)))
    x += dash + f.size * 0.12
    d.text((x, base), sb, font=f, fill=color, anchor="ls")


def name_block(nm, max_w):
    """(font, lines) for a team name in the summary header: one to three lines,
    broken at spaces or after hyphens, set as large as max_w allows (40..20 px)."""
    tokens, cur = [], ""
    for ch in nm:
        cur += ch
        if ch in " -":
            tokens.append(cur)
            cur = ""
    if cur:
        tokens.append(cur)
    options = [[nm]]
    n = len(tokens)
    for i in range(1, n):
        options.append(["".join(tokens[:i]).strip(), "".join(tokens[i:]).strip()])
        for j in range(i + 1, n):
            options.append(["".join(tokens[:i]).strip(), "".join(tokens[i:j]).strip(), "".join(tokens[j:]).strip()])
    best = None
    for lines in options:
        size = 40
        while size > 20 and max(tracked_len(ln, font("cond_b", size), 0.04) for ln in lines) > max_w:
            size -= 1
        key = (size - 3 * (len(lines) - 1), -len(lines))
        if best is None or key > best[0]:
            best = (key, size, lines)
    return font("cond_b", best[1]), best[2]


def summary_header(img, d, game, t):
    home, away = game["home"], game["away"]
    hg, ag = goals_of(home), goals_of(away)
    win = winner_side(game)
    ty, ts = 128, 132
    tile(img, home, M, ty, ts, t["accent"] if win == "home" else t["panel"], t)
    tile(img, away, W - M - ts, ty, ts, t["accent"] if win == "away" else t["panel"], t)
    d = ImageDraw.Draw(img)
    cy = ty + ts // 2
    fsc = font("display", 132)
    gap = 34
    hcol = t["accent"] if win == "home" else t["text"]
    acol = t["accent"] if win == "away" else t["text"]
    for txt, x, col, anc in ((str(hg), W / 2 - gap, hcol, "rs"), (str(ag), W / 2 + gap, acol, "ls")):
        l_, t_, r_, b_ = fsc.getbbox(txt, anchor="ls")
        d.text((x, cy - (t_ + b_) / 2), txt, font=fsc, fill=col, anchor=anc)
    d.line((W / 2 - 14, cy, W / 2 + 14, cy), fill=t["dim"], width=5)
    for team, side, x, anc in ((home, "home", M + ts + 26, "l"), (away, "away", W - M - ts - 26, "r")):
        score_w = fsc.getlength(str(hg if side == "home" else ag))
        namew = W / 2 - gap - score_w - 40 - (M + ts + 26)
        f, lines = name_block((team.get("name") or "").upper(), namew)
        lh = f.size * 0.96
        y0 = cy + 14 - (len(lines) - 1) * lh / 2 + (8 if len(lines) > 1 else 0)
        for k, ln in enumerate(lines):
            tracked(d, (x, y0 + k * lh), ln, f, t["text"], 0.04, anc + "s")
        label(d, (x, y0 - f.size - 4), "HOME" if side == "home" else "AWAY", t["dim"], 15, anc + "s")
    # status and period scores under the score
    sy = ty + ts + 36
    segs = status_segs(game, t, t["dim"])
    f = font("label", 16)
    total = sum(tracked_len(s.upper(), f, LABEL_TRACK) for s, _ in segs) + f.size * LABEL_TRACK * (len(segs) - 1)
    x = W / 2 - total / 2
    for s, c in segs:
        x += tracked(d, (x, sy), s.upper(), f, c, LABEL_TRACK, "ls") + f.size * LABEL_TRACK
    ps = period_segs(game, t, t["text"])
    if ps:
        pf = font("cond_sb", 30)
        draw_segs(d, (W / 2 - segs_len(ps, pf) / 2, sy + 44), ps, pf)
    return sy + 44 if ps else sy


STATS_Y = 1136                       # rule above the stats band of the summary


def summary_card(game):
    theme = "away" if game.get("us_side") == "away" else "home"
    t = THEMES[theme]
    img = background(theme, 13)
    d = ImageDraw.Draw(img)
    wd, short, full = date_parts(game.get("date"))
    top_row(img, t, game.get("competition") or "", [("{} {}".format(weekday_title(wd), full).strip(), t["text"])])
    head_bottom = summary_header(img, d, game, t)
    d = ImageDraw.Draw(img)

    goals, complete = log_goals(game)
    stats = summary_stats(game)
    pscore = period_scores_by_key(game)

    # ---- goal log: period headers + goals, split into two columns
    items, cur = [], object()
    for g in goals:
        pk = _period_key(g)
        if pk != cur:
            cur = pk
            items.append(("h", pk))
        items.append(("g", g))

    top = head_bottom + 46
    bottom = STATS_Y - 42 if stats else H - M - 10
    col_gap = 40
    col_w = (W - 2 * M - col_gap) // 2
    cols_x = [M, M + col_w + col_gap]
    n_rows = len(items)
    best = None

    def hh(seq):
        return sum(1.5 if k == "h" else 1.0 for k, _ in seq)

    for i in range(1, n_rows):
        if items[i - 1][0] == "h":
            continue
        a, b = items[:i], items[i:]
        cost = max(hh(a), hh(b) + (1.5 if items[i][0] == "g" else 0)) + (0 if items[i][0] == "h" else 0.6)
        if best is None or cost < best[0]:
            best = (cost, i)
    split = best[1] if best else n_rows
    columns = [items[:split], items[split:]]
    if columns[1] and columns[1][0][0] == "g":
        prev = [it for it in items[:split] if it[0] == "h"][-1]
        columns[1] = [("h", prev[1])] + columns[1]

    # few goals: scorer over assists (two lines, larger type); many goals: one line each
    stack = len(goals) <= 24
    hw = 0.95 if stack else 1.5
    units = max(sum(hw if k == "h" else 1.0 for k, _ in c) for c in columns if c) if items else 1
    roomy = stack and units <= 9          # a short log gets larger type, not a sea of space
    row = min((96.0 if roomy else 80.0) if stack else 58.0, (bottom - top) / max(units, 1))
    fs_main = int(min(42 if roomy else 34, row * (0.46 if stack else 0.62)))

    def row_need(g, fs):
        ft, fsc_, fn, fa_ = font("cond", fs - 2), font("cond_b", fs), font("cond_sb", fs), font("cond_r", fs - 4)
        ftag = font("label_b", max(12, int(fs * 0.5)))
        nx = ft.getlength("00:00") + 16 + fsc_.getlength("00–00") + 22
        tag = goal_type(g)
        tw = (tracked_len(tag, ftag, 0.1) + 28) if tag else 0
        nm = log_name(g.get("scorer"), game, g.get("team"), True)
        asts = ", ".join(assist_names(g, game))
        if stack:
            return nx + max(fn.getlength(nm) + tw, fa_.getlength(asts))
        return nx + fn.getlength(nm) + (12 + fa_.getlength(asts) if asts else 0) + tw

    while fs_main > 20 and any(row_need(g, fs_main) > col_w for g in goals):
        fs_main -= 1
    row = min(row, fs_main * (2.25 if stack else 2.05))
    used = units * row
    slack = max(0, ((bottom - top) - used) / 2 - row * 0.25)
    top = top + (slack * 0.8 if roomy else min(slack, row * 0.6))
    f_time = font("cond", fs_main - 2)
    f_score = font("cond_b", fs_main)
    f_name = font("cond_sb", fs_main)
    f_ast = font("cond_r", fs_main - 4)
    f_tag = font("label_b", max(12, int(fs_main * 0.5)))
    dropped = False

    for ci, col in enumerate(columns):
        x0 = cols_x[ci]
        x1 = x0 + col_w
        y = top
        for kind, val in col:
            if kind == "h":
                y += row * (hw - 1.0) if not stack else row * 0.15
                base = y + row * (0.62 if not stack else 0.45)
                hsize = max(14, int(fs_main * 0.6))
                name = period_name(val) if isinstance(val, int) else (str(val or "GOALS").upper())
                w = label(d, (x0, base), name, t["accent"], hsize)
                ps = pscore.get(val, "")
                pw = 0
                if ps:
                    pf = font("cond_sb", max(16, int(fs_main * 0.72)))
                    d.text((x1, base), ps, font=pf, fill=t["dim"], anchor="rs")
                    pw = pf.getlength(ps)
                ly = base - hsize * 0.36
                d.line((x0 + w + 16, ly, x1 - pw - 16, ly), fill=t["rule"], width=1)
                y += row if not stack else row * 0.8
                continue
            g = val
            ours = g.get("team") == "us"
            base = y + row * (0.68 if not stack else 0.5)
            tcol = t["text"] if ours else t["faint"]
            d.text((x0 + f_time.getlength("00:00"), base), (g.get("time") or "").strip(), font=f_time,
                   fill=t["dim"], anchor="rs")
            sc = g["_score"]
            sx = x0 + f_time.getlength("00:00") + 16
            pill_w = f_score.getlength("00–00") + 12
            if ours and sc:
                ph_ = fs_main * 1.02
                pt = base - fs_main * 0.36 - ph_ / 2
                d.rounded_rectangle((sx - 6, pt, sx - 6 + pill_w, pt + ph_), radius=4, fill=t["accent"])
                d.text((sx - 6 + pill_w / 2, base), sc, font=f_score, fill=t["on_accent"], anchor="ms")
            else:
                d.text((sx - 6 + pill_w / 2, base), sc, font=f_score, fill=t["faint"], anchor="ms")
            nx = sx + pill_w + 10
            tag = goal_type(g)
            tag_w = 0
            if tag:
                tw = tracked_len(tag, f_tag, 0.1)
                bx1 = x1
                bx0 = bx1 - tw - 14
                th = f_tag.size + 9
                by1 = base - fs_main * 0.36 + th / 2
                d.rounded_rectangle((bx0, by1 - th, bx1, by1), radius=3, outline=tcol, width=2)
                tracked(d, ((bx0 + bx1) / 2, by1 - 5), tag, f_tag, tcol, 0.1, "ms")
                tag_w = tw + 14 + 14
            nm = log_name(g.get("scorer"), game, g.get("team"), True)
            d.text((nx, base), nm, font=f_name, fill=tcol, anchor="ls")
            ax = nx + f_name.getlength(nm) + 12
            if stack:
                ax = nx
                tag_w = 0
            asts = assist_names(g, game)
            s = ", ".join(asts)
            fa = f_ast
            while s and fa.getlength(s) > x1 - tag_w - ax:
                if fa.size > fs_main - 9:
                    fa = font("cond_r", fa.size - 1)
                    continue
                asts = asts[:-1]
                s = ", ".join(asts)
                dropped = True
            if s:
                d.text((ax, base + (fs_main * 0.92 if stack else 0)), s, font=fa, fill=t["dim"], anchor="ls")
            y += row
    if dropped:
        print("note: some assists did not fit the goal log and were left out of summary_feed.jpg", file=sys.stderr)
    if not complete:
        label(d, (W - M, bottom + 6), "PARTIAL GOAL LOG", t["dim"], 14, "rs")

    # ---- stats band, home first like the header
    if stats:
        sy = STATS_Y
        d.line((M, sy, W - M, sy), fill=t["rule"], width=2)
        cw = (W - 2 * M) / len(stats)
        for i, (lab, hv, av, sub) in enumerate(stats):
            x = M + i * cw + (0 if i == 0 else 28)
            if i:
                d.line((M + i * cw, sy + 26, M + i * cw, H - M), fill=t["rule"], width=1)
            label(d, (x, sy + 44), lab, t["dim"], 16)
            fv = font("display", 62)
            draw_pair(d, x, sy + 116, hv, av, fv, t["text"], t["text"])
            f = font("cond", fit_size(sub, "cond", 24, cw - 40))
            d.text((x, sy + 150), sub, font=f, fill=t["dim"], anchor="ls")
    return img


# ------------------------------------------------------------------ main

def finish(img, seed):
    return grain(img, seed)


def is_pregame(game):
    # naadat-result-post/... is the schema name of files written before the skill was renamed
    return str(game.get("schema") or "").startswith(("naadat-game-posts/pregame", "naadat-result-post/pregame"))


def main(argv=None):
    global GAME_DIR, CFG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_json", help="game.json (result post) or pregame.json (schema %s)" % PREGAME_SCHEMA)
    ap.add_argument("--out", default="out", help="output folder (default: out)")
    ap.add_argument("--no-summary", action="store_true", help="skip slide 2 (result post)")
    ap.add_argument("--photo-index", type=int, help="use library photo K (0-based, see assets/photos/photos.json) "
                    "instead of the automatic pick")
    ap.add_argument("--photo", help="use this real, unedited photo instead of the library (needs --photo-credit); "
                    "a photo that declares AI generation/editing is refused (exit 4)")
    ap.add_argument("--photo-credit", help="credit for --photo, written to photo_credit.txt")
    ap.add_argument("--pregame", action="store_true",
                    help="render the game-day post; normally detected from the file's schema")
    ap.add_argument("--allow-unfinished", action="store_true",
                    help="render a game that is not final; the card then says LIVE / PROVISIONAL, never FINAL")
    ap.add_argument("--png", action="store_true", help="also write lossless PNG copies")
    ap.add_argument("--no-story", action="store_true", help=argparse.SUPPRESS)  # old flag; stories are gone
    args = ap.parse_args(argv)

    CFG = load_json(os.path.join(ASSETS, "brand", "team.json"))
    game = load_json(args.game_json)
    GAME_DIR = os.path.dirname(os.path.abspath(args.game_json))
    pregame = args.pregame or is_pregame(game)
    names = [str((game.get(s) or {}).get("name", "")) for s in ("home", "away")]
    if game.get("date") == "YYYY-MM-DD" or game.get("time") == "HH:MM" or "Opponent" in names:
        print("ERROR: %s still holds template placeholders (YYYY-MM-DD, HH:MM or 'Opponent'). Fill them in "
              "and run fetch_game.py --validate first." % args.game_json, file=sys.stderr)
        return 3
    for side in ("home", "away"):
        game.setdefault(side, {})
        game[side].setdefault("is_us", game.get("us_side") == side)
        if not pregame:
            game[side]["goals"] = goals_of(game[side])
    if not pregame and not is_final(game) and not args.allow_unfinished:
        print("ERROR: this game is not final (status %r), so there is no result to post yet. For a game-day "
              "post use a pregame file (fetch_game.py --pregame)." % game.get("status"), file=sys.stderr)
        return EXIT_NOT_FINAL
    if args.photo:
        if not os.path.isfile(args.photo):
            sys.exit("photo not found: %s" % args.photo)
        if not (args.photo_credit or "").strip():
            sys.exit("--photo needs --photo-credit (every caption ends with the photographer credit)")
    # Inputs that declare AI generation or editing are refused before any pixel
    # is read, so a render never launders an AI marker into a "clean" file.
    photo_path, credit, focus = pick_photo(game, pregame, args)
    if not os.path.exists(photo_path):
        sys.exit("library photo missing: %s" % photo_path)
    refuse_if_ai(photo_path, "photo")
    for side in ("home", "away"):
        lf = game[side].get("logo_file")
        if lf and not game[side].get("is_us"):
            p = lf if os.path.isabs(lf) else os.path.join(GAME_DIR, lf)
            if os.path.isfile(p):
                refuse_if_ai(p, "logo_file")
    try:
        photo = load_photo(photo_path)
    except Exception as exc:  # noqa: BLE001
        sys.exit("could not read photo %s: %s" % (photo_path, exc))
    os.makedirs(args.out, exist_ok=True)
    if args.photo:
        print("photo: %s (credit: %s)" % (photo_path, credit))
    else:
        k, n = pick_photo.index
        print("photo: %s (index %d of 0..%d; credit: %s). For another photo rerun with --photo-index %d"
              % (os.path.basename(photo_path), k, n - 1, credit, (k + 1) % n))

    written = []

    def save(im, name):
        written.append(save_clean(im, os.path.join(args.out, name + ".jpg")))
        if args.png:
            written.append(save_clean(im, os.path.join(args.out, name + ".png")))

    if pregame:
        save(finish(pregame_card(game, photo, focus), 7), "pregame_feed")
    else:
        save(finish(result_card(game, photo, focus), 7), "result_feed")
        has_log = bool(log_goals(game)[0])
        if not args.no_summary and not has_log:
            print("note: no goal log in %s, so slide 2 (summary) was skipped" % os.path.basename(args.game_json))
        elif not args.no_summary:
            save(finish(summary_card(game), 8), "summary_feed")
    cred = os.path.join(args.out, "photo_credit.txt")
    with open(cred, "w", encoding="utf-8", newline="") as f:
        f.write(credit)
    for p in written:
        print("wrote", p)
    print("wrote", cred)
    return 0


if __name__ == "__main__":
    sys.exit(main())
