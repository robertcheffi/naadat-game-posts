#!/usr/bin/env python3
"""Render Instagram result graphics for a game.json produced by fetch_game.py
(or written by hand from references/manual_game_template.json).

Look: a TV-sports match graphics package. Slanted type, chamfered panels, a
scorebug with both logos and the score as the hero, a box score, speed lines
and chevrons as motion cues, and the club lockup (crest + HERNESAAREN NÄÄDÄT).

Outputs (all metadata-free, see clean_image.py):
  <out>/result_feed.jpg      1080x1350  feed post, slide 1
  <out>/summary_feed.jpg     1080x1350  feed post, slide 2 (goal log, shots, goalies)
  <out>/result_story.jpg     1080x1920  story (own 9:16 layout)

Slide 2 needs a goal log; a hand-entered game with "goals": [] skips it with a
note. logo_file paths in game.json are resolved relative to the game.json
folder. Everything is drawn with Pillow (8.2+) from real data, fonts and logo
files. No generative AI image model is involved anywhere.

A --photo is shown in colour at the top of slide 1 and the story; slide 2
stays photo-free on purpose (the goal log needs a calm background). A photo or
logo_file that declares AI generation or editing (IPTC DigitalSourceType, C2PA)
is refused with exit code 4. Downloaded logos are cached in the system temp
folder, never inside --out.

  python render_card.py game.json --out out/
  python render_card.py game.json --out out/ --photo team_photo.jpg
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
import json  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import tempfile  # noqa: E402
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
sys.path.insert(0, HERE)
import clean_image  # noqa: E402
from clean_image import save_clean  # noqa: E402

AI_REFUSAL_EXIT = 4
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

W, H = 1080, 1350
STORY_H = 1920
SLANT = 0.21          # italic shear used for type and panel edges
L, R = 50, 1030       # safe content edges (profile grid crops 4:5 sides)

try:
    RESAMPLE = Image.Resampling.LANCZOS
    BICUBIC = Image.Resampling.BICUBIC
    AFFINE = Image.Transform.AFFINE
except AttributeError:  # Pillow < 9.1
    RESAMPLE = Image.LANCZOS
    BICUBIC = Image.BICUBIC
    AFFINE = Image.AFFINE

GAME_DIR = "."        # folder of game.json; relative logo_file paths resolve here
_LOGOS = {}           # (name, is_us) -> (image, kind); one lookup and one note per team


# ---------------------------------------------------------------- basics

def rgb(hex_color, alpha=255):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), int(size))


def anton(size):
    return font("Anton-Regular.ttf", size)


def barlow(size, weight="Bold"):
    return font("BarlowCondensed-%s.ttf" % weight, size)


def text_w(text, f, tracking=0):
    if not text:
        return 0
    return f.getlength(text) + tracking * (len(text) - 1)


def _chars(d, x, y, text, f, fill, tracking, va):
    if not tracking:
        d.text((x, y), text, font=f, fill=fill, anchor="l" + va)
        return
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, anchor="l" + va)
        x += f.getlength(ch) + tracking


def draw_text(img, xy, text, f, fill, tracking=0, anchor="ls"):
    """Draw text with letter spacing; anchor = l/m/r + baseline 's' or middle 'm'.

    A fill with alpha < 255 is drawn on a transparent layer and composited, so
    it really blends (ImageDraw on an RGBA canvas would overwrite the pixels).
    """
    x, y = xy
    width = text_w(text, f, tracking)
    if anchor[0] == "m":
        x -= width / 2
    elif anchor[0] == "r":
        x -= width
    if not text:
        return width
    if len(fill) < 4 or fill[3] >= 255:
        _chars(ImageDraw.Draw(img), x, y, text, f, fill, tracking, anchor[1])
        return width
    s = f.size
    top, bot = max(0, int(y - 2 * s)), min(img.height, int(y + 2 * s))
    if bot <= top:
        return width
    layer = Image.new("RGBA", (img.width, bot - top), (0, 0, 0, 0))
    _chars(ImageDraw.Draw(layer), x, y - top, text, f, tuple(fill[:3]) + (255,), tracking, anchor[1])
    k = fill[3]
    layer.putalpha(layer.getchannel("A").point(lambda v: v * k // 255))
    img.alpha_composite(layer, (0, top))
    return width


def line(img, pts, fill, width=2):
    """Alpha-correct line."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).line(pts, fill=fill, width=width)
    img.alpha_composite(layer)


def fit_font(text, maker, start, max_w, min_size=10, tracking=0):
    size = start
    while size > min_size and text_w(text, maker(size), tracking) > max_w:
        size -= 2
    return maker(size)


def ink_box(f, text):
    """Ink bbox of text relative to a left-baseline origin."""
    return f.getbbox(text, anchor="ls")


def baseline_for_center(f, text, cy):
    """Baseline y that puts the ink of `text` vertically centred on cy."""
    _, t, _, b = ink_box(f, text)
    return cy - (t + b) / 2


def slant_text(img, xy, text, f, fill, tracking=0, anchor="ls", k=SLANT, shadow=None):
    """Draw text sheared like an italic (top leans right), about its baseline."""
    x, yb = xy
    _, t, _, b = ink_box(f, text)
    pad = 30
    top = int(yb + t - pad)
    hgt = int(b - t + 2 * pad)
    layer = Image.new("RGBA", (img.width, hgt), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    width = text_w(text, f, tracking)
    if anchor[0] == "m":
        x -= width / 2
    elif anchor[0] == "r":
        x -= width
    by = yb - top
    _chars(d, x, by, text, f, fill, tracking, anchor[1])  # transparent layer: alpha blends on composite
    layer = layer.transform(layer.size, AFFINE, (1, k, -k * by, 0, 1, 0), resample=BICUBIC)
    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").point(lambda v: v * shadow[0] // 255))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow[1]))
        _composite_at(img, sh, shadow[2], top + shadow[3])
    _composite_at(img, layer, 0, top)


def _composite_at(img, layer, x, y):
    """alpha_composite that tolerates layers hanging off the canvas."""
    x, y = int(x), int(y)
    sx, sy = max(0, -x), max(0, -y)
    if sx or sy:
        layer = layer.crop((sx, sy, layer.width, layer.height))
    x, y = max(0, x), max(0, y)
    w, h = min(layer.width, img.width - x), min(layer.height, img.height - y)
    if w <= 0 or h <= 0:
        return
    if (w, h) != layer.size:
        layer = layer.crop((0, 0, w, h))
    img.alpha_composite(layer, (x, y))


def para(x0, y0, x1, y1, k=SLANT, left=True, right=True):
    """Parallelogram (top edge shifted right). left/right: slant that edge."""
    s = k * (y1 - y0)
    return [(x0 + (s if left else 0), y0), (x1 + (s if right else 0), y0),
            (x1, y1), (x0, y1)]


def cell(x0, y0, x1, y1, k=0.17):
    """Parallelogram kept inside x0..x1 (top edge shifted right)."""
    sh = k * (y1 - y0)
    return [(x0 + sh, y0), (x1, y0), (x1 - sh, y1), (x0, y1)]


def chamfer(x0, y0, x1, y1, c, corners="tl br"):
    pts = []
    pts += [(x0, y0 + c), (x0 + c, y0)] if "tl" in corners else [(x0, y0)]
    pts += [(x1 - c, y0), (x1, y0 + c)] if "tr" in corners else [(x1, y0)]
    pts += [(x1, y1 - c), (x1 - c, y1)] if "br" in corners else [(x1, y1)]
    pts += [(x0 + c, y1), (x0, y1 - c)] if "bl" in corners else [(x0, y1)]
    return pts


def poly(img, pts, fill, shadow=None):
    """Alpha-correct polygon, optional soft drop shadow (alpha, blur, dx, dy)."""
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).polygon([(x + shadow[2], y + shadow[3]) for x, y in pts],
                                   fill=(0, 0, 0, shadow[0]))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(shadow[1])))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(pts, fill=fill)
    img.alpha_composite(layer)


def poly_mask(size, pts):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def wrap_parts(parts, f, max_w, sep):
    lines, cur = [], []
    for p in parts:
        trial = sep.join(cur + [p])
        if not cur or f.getlength(trial) <= max_w:
            cur.append(p)
        else:
            lines.append(cur)
            cur = [p]
    if cur:
        lines.append(cur)
    return lines


def ellipsize(text, f, max_w):
    if f.getlength(text) <= max_w:
        return text
    while text and f.getlength(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip(" ,(") + "…" if text else ""


# ---------------------------------------------------------------- background

def noise_tile(seed, size=256):
    rng = random.Random(seed)
    data = bytes(rng.getrandbits(8) for _ in range(size * size))
    return Image.frombytes("L", (size, size), data)


def grain(w, h, seed, alpha):
    tile = noise_tile(seed)
    grain_l = Image.new("L", (w, h))
    for ty in range(0, h, tile.height):
        for tx in range(0, w, tile.width):
            grain_l.paste(tile, (tx, ty))
    return Image.merge("RGBA", (grain_l, grain_l, grain_l, Image.new("L", (w, h), alpha)))


def speed_lines(w, h, rng, y0, y1, side, count, colors):
    """Horizontal streaks entering from one side, fading toward the centre."""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        y = rng.uniform(y0, y1)
        ln = rng.uniform(140, 520)
        th = rng.choice((2, 2, 3, 4, 6))
        a = rng.randint(14, 46)
        col = rgb(colors["orange"], a) if rng.random() < 0.22 else (255, 255, 255, a)
        if side == "l":
            x0 = rng.uniform(-60, 120)
            pts = [(x0, y), (x0 + ln, y)]
        else:
            x1 = w - rng.uniform(-60, 120)
            pts = [(x1 - ln, y), (x1, y)]
        d.line(pts, fill=col, width=th)
    ramp = Image.new("L", (w, 1))
    for x in range(w):
        t = x / (w - 1)
        v = (1 - t) if side == "l" else t
        ramp.putpixel((x, 0), int(255 * min(1, max(0, v * 1.6 - 0.15))))
    ramp = ramp.resize((w, h))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), ramp))
    return layer.filter(ImageFilter.GaussianBlur(0.8))


def chevrons(img, x, y, n, size, color, step=None, thick=None, fade=True):
    """Row of '>' chevrons pointing right, fading out."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = step or size * 0.62
    thick = thick or max(3, int(size * 0.16))
    for i in range(n):
        a = int(color[3] * (1 - i / max(n, 1) * 0.8)) if fade else color[3]
        cx = x + i * step
        pts = [(cx, y - size / 2), (cx + size * 0.45, y), (cx, y + size / 2)]
        d.line(pts, fill=color[:3] + (a,), width=thick, joint="curve")
    img.alpha_composite(layer)


def _photo_layer(photo, w, h, colors, text_end, window_end, top=0):
    """The photo graded toward the brand and veiled where text sits over it.

    The photo fills the top of the frame (0..window_end + fade) in colour with a
    light green grade. The veil is moderate behind the header text (top), light
    in the clear photo window (text_end..window_end) and rises to solid dark
    behind the panels below it."""
    fade = 190
    ph_h = int(min(h, window_end + fade)) - top
    ph = ImageOps.fit(photo.convert("RGB"), (w, ph_h), RESAMPLE, centering=(0.5, 0.42))
    ph = ImageEnhance.Color(ph).enhance(0.88)
    ph = ImageEnhance.Contrast(ph).enhance(1.06)
    ph = Image.blend(ph, Image.new("RGB", ph.size, rgb(colors["green"])[:3]), 0.14)
    layer = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
    layer.paste(ph.convert("RGBA"), (0, top))
    veil = Image.new("L", (1, h))
    for y in range(h):
        if y < text_end:
            a = 0.62 - 0.24 * (y / max(1, text_end))          # 0.62 -> 0.38 behind the header
        elif y < window_end:
            t = (y - text_end) / max(1, window_end - text_end)
            a = 0.38 - 0.26 * min(1, t * 2.5)                  # clear photo window, ~0.12
        else:
            t = min(1, (y - window_end) / fade)
            a = 0.12 + 0.84 * (t * t * (3 - 2 * t))             # smooth rise to solid dark
        veil.putpixel((0, y), int(255 * a))
    veil = veil.resize((w, h))
    dark = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
    return Image.composite(dark, layer, veil)


def background(w, h, colors, seed, photo=None, hero=(0.45,), photo_window=None, photo_top=0):
    """Brand background. photo: a PIL image; photo_window = (text_end, window_end)."""
    rng = random.Random(seed)
    base = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
    if photo is not None:
        text_end, window_end = photo_window or (int(h * 0.22), int(h * 0.40))
        base = _photo_layer(photo, w, h, colors, text_end, window_end, photo_top)
    else:
        glow = Image.new("L", (w, h), 0)
        gd = ImageDraw.Draw(glow)
        for cyf in hero:
            cx, cy, r = w * 0.5, h * cyf, w * 0.95
            for i in range(40, 0, -1):
                rr = r * i / 40
                gd.ellipse((cx - rr, cy - rr * 0.55, cx + rr, cy + rr * 0.55),
                           fill=int(200 * (1 - i / 40) ** 1.3))
        glow = glow.filter(ImageFilter.GaussianBlur(60))
        mid = Image.new("RGBA", (w, h), rgb(colors["green_mid"]))
        base = Image.composite(mid, base, glow)
    del rng

    shards = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shards)
    sd.polygon([(w * 0.58, 0), (w * 1.0, 0), (w * 1.0, h * 0.10), (w * 0.30, h)], fill=(255, 255, 255, 7))
    sd.polygon([(w * 0.70, 0), (w * 0.78, 0), (w * 0.40, h), (w * 0.32, h)], fill=(255, 255, 255, 6))
    sd.polygon([(-w * 0.1, h * 0.70), (w * 0.38, h * 0.70 - w * 0.48 * 0.6), (w * 0.44, h * 0.70 - w * 0.48 * 0.6),
                (-w * 0.04, h * 0.70 + 40)], fill=rgb(colors["orange"], 10))
    base = Image.alpha_composite(base, shards)

    scan = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    scd = ImageDraw.Draw(scan)
    for y in range(0, h, 6):
        scd.line([(0, y), (w, y)], fill=(0, 0, 0, 22), width=2)
    base = Image.alpha_composite(base, scan)
    base = Image.alpha_composite(base, grain(w, h, seed, 11))

    vig = Image.new("L", (w, h), 255)
    ImageDraw.Draw(vig).ellipse((-w * 0.3, -h * 0.12, w * 1.3, h * 1.12), fill=0)
    vig = vig.filter(ImageFilter.GaussianBlur(110))
    base = Image.composite(Image.new("RGBA", (w, h), (0, 0, 0, 95 if photo is not None else 150)), base, vig)
    return base


def add_speed(img, colors, seed, y0, y1, count=26):
    rng = random.Random(seed * 7 + 3)
    img.alpha_composite(speed_lines(img.width, img.height, rng, y0, y1, "l", count, colors))
    img.alpha_composite(speed_lines(img.width, img.height, rng, y0, y1, "r", count, colors))


def hem(img, colors, seed, top):
    """Full-bleed jersey hem: orange band with cream and dark stripes.

    top=True fills y 0..STORY_HEM (under the Instagram story header);
    top=False mirrors it exactly at the bottom (under the reply bar)."""
    w, h = img.size
    o, c, g = rgb(colors["orange"]), rgb(colors["cream"]), rgb(colors["green_dark"])
    # (height, colour) from the outer edge inwards
    bands = [(None, o), (16, c), (20, g), (34, o), (10, c)]
    outer = STORY_HEM - sum(b for b, _ in bands[1:])   # same height top and bottom
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if top:
        y = 0
        for hh, col in bands:
            hh = outer if hh is None else hh
            d.rectangle((0, y, w, y + hh - 1), fill=col)
            y += hh
    else:
        y = h
        for hh, col in bands:
            hh = outer if hh is None else hh
            d.rectangle((0, y - hh, w, y - 1), fill=col)
            y -= hh
    # knit texture and a soft shade so the band reads as fabric, not a flat fill
    tex = grain(w, h, seed + 99, 26)
    tex.putalpha(ImageChops.multiply(tex.getchannel("A"), layer.getchannel("A")))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(-h, w, 18):
        sd.line([(x, 0), (x + h * 0.21, h)], fill=(0, 0, 0, 14), width=6)
    shade.putalpha(ImageChops.multiply(shade.getchannel("A"), layer.getchannel("A")))
    img.alpha_composite(layer)
    img.alpha_composite(shade)
    img.alpha_composite(tex)


# ---------------------------------------------------------------- logos

def logo_image(team, cache_dir, colors, cfg):
    """Return (RGBA image, kind) with kind in brand | logo | badge."""
    key = (team.get("name"), bool(team.get("is_us")))
    if key in _LOGOS:
        return _LOGOS[key]
    _LOGOS[key] = res = _find_logo(team, cache_dir, colors, cfg)
    return res


def _find_logo(team, cache_dir, colors, cfg):
    name = team.get("name") or "opponent"
    if team.get("is_us"):
        p = os.path.join(ASSETS, "brand", cfg["logo"])
        return Image.open(p).convert("RGBA"), "brand"
    candidates = []
    lf = team.get("logo_file")
    if lf:
        p = lf if os.path.isabs(lf) else os.path.join(GAME_DIR, lf)
        if os.path.exists(p):
            candidates.append(p)
        else:
            print("warning: logo_file for %s not found: %s" % (name, p), file=sys.stderr)
    if team.get("association_id"):
        candidates.append(os.path.join(ASSETS, "logos", "%s.png" % team["association_id"]))
    for c in candidates:
        if os.path.exists(c):
            try:
                return Image.open(c).convert("RGBA"), "logo"
            except Exception as exc:  # noqa: BLE001
                print("warning: could not read logo %s: %s" % (c, exc), file=sys.stderr)
    url = team.get("logo_url")
    if url:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, "logo_%s.png" % (team.get("association_id") or "opp"))
            req = urllib.request.Request(url, headers={"user-agent": "naadat-result-post/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                f.write(r.read())
            marks = ai_marks(dest)
            if marks:
                raise ValueError("downloaded logo declares AI provenance (%s)" % "; ".join(marks))
            return Image.open(dest).convert("RGBA"), "logo"
        except Exception as exc:  # noqa: BLE001  fall back to a badge, but say so
            print("warning: could not download logo %s: %s" % (url, exc), file=sys.stderr)
    print("note: no logo for %s, using a lettered badge" % name)
    return monogram(name, colors), "badge"


def abbrev(name):
    """2-3 letter badge text. Never a single letter (a lone O reads as a zero)."""
    words = ["".join(ch for ch in w if ch.isalpha()) for w in name.replace("-", " ").split()]
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


def monogram(name, colors):
    """Lettered badge for an opponent with no logo: cream plate, slanted letters."""
    s = 600
    im = Image.new("RGBA", (s, s), rgb(colors["cream"]))
    d = ImageDraw.Draw(im)
    ink = (34, 38, 36, 255)
    d.line(chamfer(34, 34, s - 34, s - 34, 70) + [(34, 104)], fill=ink, width=10)
    letters = abbrev(name)
    f = fit_font(letters, anton, 300, s - 170)
    _, t, _, b = ink_box(f, letters)
    slant_text(im, (s / 2 - 12, s / 2 - (t + b) / 2), letters, f, ink, 0, "ms", k=0.14)
    return im


def tile_image(logo, kind, s):
    """Square tile with the logo on it (white tile for opponent logos)."""
    if kind == "brand":
        return ImageOps.fit(logo, (s, s), RESAMPLE)
    if kind == "badge":
        return logo.resize((s, s), RESAMPLE)
    tile = Image.new("RGBA", (s, s), (255, 255, 255, 255))
    pad = int(s * 0.08)
    lg = logo.copy()
    lg.thumbnail((s - 2 * pad, s - 2 * pad), RESAMPLE)
    tile.alpha_composite(lg, ((s - lg.width) // 2, (s - lg.height) // 2))
    return tile


def logo_tile(img, logo, kind, box, colors, cut=None, shadow=True, keyline=None):
    """Logo on a chamfered square tile. box = (x, y, size).

    The club's own (orange) tile gets a cream keyline so it never fuses with an
    orange score cell next to it."""
    x, y, s = box
    x, y, s = int(x), int(y), int(s)
    cut = int(s * 0.13) if cut is None else cut
    pts = chamfer(0, 0, s - 1, s - 1, cut)
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).polygon([(px + x + 8, py + y + 16) for px, py in pts], fill=(0, 0, 0, 140))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)))
    if keyline is None:
        keyline = max(2, round(s * 0.035)) if kind == "brand" else 0
    if keyline:
        plate = Image.new("RGBA", (s, s), rgb(colors["cream"]))
        img.paste(plate, (x, y), poly_mask((s, s), pts))
        gap = max(1, keyline // 2)  # thin dark line inside the cream one
        inner = s - 2 * keyline
        dark = Image.new("RGBA", (inner, inner), rgb(colors["green_dark"]))
        img.paste(dark, (x + keyline, y + keyline),
                  poly_mask((inner, inner), chamfer(0, 0, inner - 1, inner - 1, max(2, cut - keyline))))
        x, y, s = x + keyline + gap, y + keyline + gap, inner - 2 * gap
        cut = max(2, cut - keyline - gap)
        pts = chamfer(0, 0, s - 1, s - 1, cut)
    tile = tile_image(logo, kind, s)
    img.paste(tile, (x, y), poly_mask((s, s), pts))


def small_logo(img, logo, kind, x, y, s, colors):
    logo_tile(img, logo, kind, (x, y, s), colors, cut=max(4, s // 6), shadow=False,
              keyline=(2 if kind == "brand" else 0))


def lockup(img, cfg, colors, x, cy, crest, size, handle=False, anchor="l"):
    """Club lockup: crest + HERNESAAREN NÄÄDÄT in tracked caps (+ handle below)."""
    name = (cfg.get("full_name") or cfg["display_name"]).upper()
    nf = barlow(size, "ExtraBoldItalic")
    hf = barlow(int(size * 0.86), "ExtraBoldItalic")
    tw = text_w(name, nf, 5)
    if handle:
        tw = max(tw, text_w(cfg["instagram"].upper(), hf, 4))
    total = crest + 18 + tw
    x0 = x if anchor == "l" else x - total
    brand, _ = logo_image({"name": cfg["display_name"], "is_us": True}, None, colors, cfg)
    logo_tile(img, brand, "brand", (x0, cy - crest / 2, crest), colors, cut=max(4, crest // 6), shadow=False,
              keyline=max(2, crest // 22))
    tx = x0 + crest + 18
    if handle:
        _, t1, _, b1 = ink_box(nf, "H")
        _, t2, _, b2 = ink_box(hf, "H")
        gap = size * 0.34
        blk = (b1 - t1) + gap + (b2 - t2)
        y1 = cy - blk / 2 - t1
        draw_text(img, (tx, y1), name, nf, rgb(colors["cream"]), 5)
        draw_text(img, (tx, y1 + (b1 - t1) + gap + (b2 - t2)), cfg["instagram"].upper(), hf,
                  rgb(colors["orange"]), 4)
    else:
        draw_text(img, (tx, baseline_for_center(nf, "H", cy)), name, nf, rgb(colors["cream"]), 5)
    return x0, x0 + total


# ---------------------------------------------------------------- data helpers

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
TYPE_LABEL = {"PP": "PP", "SH": "SH", "EN": "EN", "PS": "PS", "YV": "PP", "AV": "SH", "TM": "EN"}


def date_label(iso):
    import datetime as dt
    try:
        d = dt.date(*[int(p) for p in str(iso)[:10].split("-")])
    except (TypeError, ValueError):
        return str(iso or "").upper()
    return "%s %d.%d.%d" % (DOW[d.weekday()], d.day, d.month, d.year)


def game_seed(game):
    gid = str(game.get("game_id") or game.get("date") or "0")
    return int(gid) if gid.isdigit() else zlib.crc32(gid.encode("utf-8"))


def name_of(n, style="short"):
    """Display a name object that may carry only 'last' (hand-entered games)."""
    if isinstance(n, str):
        return n
    n = n or {}
    last = (n.get("last") or "").strip()
    first = (n.get("first") or "").strip()
    full = (n.get("full") or "").strip()
    if style == "last":
        return last or full or "?"
    short = (n.get("short") or "").strip()
    if short:
        return short
    if first and last:
        return "%s. %s" % (first[0], last)
    return last or full or "?"


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
        t = g.get("team")
        add(t, g.get("scorer"))
        for a in g.get("assists") or []:
            add(t, a)
    for key, team in (("scorers_us", "us"), ("scorers_them", "them")):
        for r in game.get(key) or []:
            add(team, r.get("name"))
    game["_clash"] = {k for k, v in seen.items() if len(v) > 1}
    return game["_clash"]


def brief_name(n, game, team):
    """Short name for assists and slide-1 scorers: the data's 'display' form when
    present (it already tells siblings apart), else the last name, or the short
    form ('J. Taivalmaa') when a teammate shares that last name."""
    if isinstance(n, dict) and (n.get("display") or "").strip():
        return n["display"].strip()
    last = name_of(n, "last")
    if isinstance(n, dict) and (team, last.lower()) in _clashes(game):
        return name_of(n)
    return last


def goals_of(r):
    try:
        return int(r.get("goals") or 0)
    except (TypeError, ValueError):
        return 0


def scorer_parts(game):
    parts = []
    for r in game.get("scorers_us") or []:
        g = goals_of(r)
        if g > 0:
            parts.append((brief_name(r.get("name"), game, "us"), g))
    return parts


def us_team(game):
    return game["home"] if game.get("us_side") == "home" else game["away"]


def them_team(game):
    return game["away"] if game.get("us_side") == "home" else game["home"]


def side_shots(game):
    shots = game.get("shots")
    if not shots or shots.get("us") is None or shots.get("them") is None:
        return None
    if game.get("us_side") == "home":
        return shots["us"], shots["them"]
    return shots["them"], shots["us"]


def period_label(lbl):
    return {"1": "1ST", "2": "2ND", "3": "3RD"}.get(str(lbl), str(lbl).upper())


def winner_side(game):
    """'home', 'away' or None (tie or not final). game['result'] wins over goals."""
    if "result" in game:
        r = (game.get("result") or "").upper()
        if not r:
            return None
        us = game.get("us_side", "home")
        if r in ("W", "OTW", "SOW"):
            return us
        if r in ("L", "OTL", "SOL"):
            return "away" if us == "home" else "home"
        return None
    h, a = game["home"]["goals"], game["away"]["goals"]
    if h == a:
        return None
    return "home" if h > a else "away"


def result_tag(game):
    """(text, kind) for the result word, or None. Wins always; losses only after OT/SO."""
    r = (game.get("result") or "").upper()
    return {"W": ("WIN", "win"), "OTW": ("OVERTIME WIN", "win"), "SOW": ("SHOOTOUT WIN", "win"),
            "OTL": ("OT LOSS", "loss"), "SOL": ("SO LOSS", "loss")}.get(r)


def is_loss(game):
    return (game.get("result") or "").upper() in ("L", "OTL", "SOL")


def accent(game, colors, alpha=255):
    """Colour of the result accents (OT/SO badge, underline, chevrons, bug stripe).

    Rule: orange is the club colour and here means "celebrate". It is used on
    wins and on neutral cards (no result yet). On every loss card (L, OTL, SOL)
    these accents are cream, so no orange ever celebrates a loss. A regulation
    loss deliberately gets no word tag (the score says it; OT/SO losses get a
    cream 'OT LOSS' / 'SO LOSS' tag because the extra point is worth naming),
    only the cream underline and chevrons."""
    return rgb(colors["cream"] if is_loss(game) else colors["orange"], alpha)


def score_style(game, side, colors):
    """Fill, digit colour and sheen of a score cell.

    Orange only when Näädät win; an opponent win is cream with dark digits;
    the losing cell is dark and muted; ties and unfinished games are neutral."""
    win = winner_side(game)
    if win is None:
        return (255, 255, 255, 24), rgb(colors["cream"]), False
    if win == side:
        if game[side].get("is_us"):
            return rgb(colors["orange"]), rgb(colors["green_dark"]), True
        return rgb(colors["cream"]), rgb(colors["green_dark"]), True
    return (255, 255, 255, 12), rgb(colors["cream"], 118), False


def name_color(game, side, colors):
    win = winner_side(game)
    return rgb(colors["cream"]) if win in (None, side) else rgb(colors["cream"], 170)


def table_labels(pairs, maker, start, max_w, min_px=28, tracking=2):
    """Labels + ONE font for the rows of a table (box score, stats strip, bug).

    pairs: [(full_label, short_label)]. A row keeps its full label when it fits
    at min_px or more, else it switches to the short label (the abbreviation the
    box score uses). All rows then share the largest size at which every label
    fits, so the rows read as one table."""
    labels = []
    for full, short in pairs:
        f = fit_font(full, maker, start, max_w, min_px, tracking)
        labels.append(full if text_w(full, f, tracking) <= max_w else short)
    size = start
    while size > 12 and any(text_w(t, maker(size), tracking) > max_w for t in labels):
        size -= 2
    return labels, maker(size)


def short_label(team, cfg):
    if team.get("is_us"):
        return (cfg.get("short_name") or cfg["display_name"]).upper()
    return abbrev(team.get("name") or "Opponent")


def team_label(team, cfg):
    return (cfg["display_name"] if team.get("is_us") else team.get("name") or "Opponent").upper()


# ---------------------------------------------------------------- pieces

def header(img, title, game, colors, cfg, top, size, tag=True, right_top=None, scrim=False):
    """Slanted giant title, result tag under it, date / competition / venue on the right.

    scrim: soft dark plate behind the right-hand text (used over a photo)."""
    date_txt = date_label(game.get("date"))
    comp = (game.get("competition") or "").upper()
    arena = (game.get("arena") or "").upper()
    att = game.get("attendance")
    rmax = 340
    rlines = [(date_txt, barlow(44, "SemiBold"), rgb(colors["cream"]), 2)]
    if comp:
        rlines.append((comp, fit_font(comp, lambda s: barlow(s, "ExtraBoldItalic"), 36, rmax, 22, 3),
                       rgb(colors["orange"]), 3))
    if arena:
        rlines.append((arena, fit_font(arena, lambda s: barlow(s, "SemiBold"), 30, rmax, 20, 2),
                       rgb(colors["cream"], 200), 2))
    if att:
        rlines.append(("ATTENDANCE %s" % att, barlow(26, "SemiBold"), rgb(colors["cream"], 160), 3))
    rcol = max(text_w(t, f, tr) for t, f, _, tr in rlines)
    rt = (top + 4) if right_top is None else right_top
    if scrim:
        rh = sum(ink_box(f, "H")[3] - ink_box(f, "H")[1] + 14 for _, f, _, _ in rlines)
        sc = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sc).rounded_rectangle((R - rcol - 40, rt - 26, R + 40, rt + rh + 22), 30,
                                             fill=rgb(colors["green_dark"], 150))
        img.alpha_composite(sc.filter(ImageFilter.GaussianBlur(22)))
    y = rt
    for t, f, col, tr in rlines:
        _, a, _, b = ink_box(f, "H")
        y += (b - a)
        draw_text(img, (R, y), t, f, col, tr, "rs")
        y += 14

    rt_tag = result_tag(game) if tag else None
    badge = "" if rt_tag else {"OT": "OT", "SO": "SO"}.get(game.get("finish"), "")
    avail = R - rcol - 40 - L

    def measure(sz):
        f = anton(sz)
        _, t, _, b = ink_box(f, title)
        cap = b - t
        tw = text_w(title, f, 6) + SLANT * cap
        bw = 0
        if badge:
            bw = text_w(badge, anton(int(cap * 0.42)), 3) + 44 + 26 + SLANT * cap * 0.56
        return f, cap, tw, tw + bw

    f, cap, tw, total = measure(size)
    while total > avail and size > 60:
        size -= 4
        f, cap, tw, total = measure(size)
    yb = top + cap
    slant_text(img, (L, yb), title, f, rgb(colors["cream"]), 6, "ls", shadow=(150, 14, 6, 10))
    if badge:
        x = L + tw + 26
        bf = anton(int(cap * 0.42))
        bw = text_w(badge, bf, 3) + 44
        bh = cap * 0.56
        by = yb - bh
        poly(img, para(x, by, x + bw, yb), accent(game, colors), shadow=(120, 8, 4, 6))
        slant_text(img, (x + bw / 2 + SLANT * bh / 2, baseline_for_center(bf, badge, by + bh / 2)),
                   badge, bf, rgb(colors["green_dark"]), 3, "ms")
    ry = yb + 26
    if rt_tag:
        text, kind = rt_tag
        th = max(48, int(cap * 0.34))
        tf = anton(int(th * 0.62))
        tw2 = text_w(text, tf, 4) + 56
        fill = rgb(colors["orange"]) if kind == "win" else rgb(colors["cream"])
        poly(img, para(L, ry, L + tw2, ry + th), fill, shadow=(120, 8, 4, 6))
        slant_text(img, (L + tw2 / 2 + SLANT * th / 2, baseline_for_center(tf, text, ry + th / 2)),
                   text, tf, rgb(colors["green_dark"]), 4, "ms")
        chevrons(img, L + tw2 + 34, ry + th / 2, 5, 26, accent(game, colors, 220), step=19, thick=5)
        bottom = ry + th
    else:
        poly(img, para(L, ry, L + 230, ry + 10), accent(game, colors))
        chevrons(img, L + 262, ry + 5, 5, 22, accent(game, colors, 220), step=17, thick=5)
        bottom = ry + 10
    return max(bottom, y - 14)


def score_cell(img, game, side, pts, hgt, max_w, size_k=0.78):
    colors = game["_colors"]
    fill, ink, sheen = score_style(game, side, colors)
    poly(img, pts, fill)
    ky0, ky1 = min(p[1] for p in pts), max(p[1] for p in pts)
    if sheen:
        sh = Image.new("RGBA", img.size, (255, 255, 255, 0))
        ImageDraw.Draw(sh).rectangle((0, ky0, img.width, ky0 + (ky1 - ky0) * 0.42), fill=(255, 255, 255, 26))
        sh.putalpha(ImageChops.multiply(sh.getchannel("A"), poly_mask(img.size, pts)))
        img.alpha_composite(sh)
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    txt = str(game[side]["goals"])
    f = fit_font(txt, anton, int(hgt * size_k), max_w)
    slant_text(img, (cx, baseline_for_center(f, txt, (ky0 + ky1) / 2)), txt, f, ink, 0, "ms", k=0.12)


def scorebug_h(img, game, colors, cfg, cache_dir, y0, hgt=250, names=True):
    """Hero scorebug: [logo][score] [score][logo] across x 50..1030."""
    home, away = game["home"], game["away"]
    lh, kh = logo_image(home, cache_dir, colors, cfg)
    la, ka = logo_image(away, cache_dir, colors, cfg)
    y1 = y0 + hgt
    pad = 12
    s = hgt - 2 * pad
    poly(img, chamfer(L, y0, R, y1, 34), (4, 16, 9, 235), shadow=(170, 22, 0, 18))
    poly(img, [(L + 34, y0), (R, y0), (R, y0 + 5), (L + 29, y0 + 5)], accent(game, colors))
    lx, rx = L + pad, R - pad - s
    logo_tile(img, lh, kh, (lx, y0 + pad, s), colors, shadow=False)
    logo_tile(img, la, ka, (rx, y0 + pad, s), colors, shadow=False)
    gap = 22
    mid = (L + R) / 2
    sx0, sx1 = lx + s + gap, rx - gap
    ky0, ky1 = y0 + pad, y1 - pad
    sk = 0.17
    shh = sk * (ky1 - ky0)
    score_cell(img, game, "home", cell(sx0, ky0, mid + shh / 2 - 9, ky1, sk), hgt, (sx1 - sx0) / 2 - 70)
    score_cell(img, game, "away", cell(mid - shh / 2 + 9, ky0, sx1, ky1, sk), hgt, (sx1 - sx0) / 2 - 70)
    if not names:
        return y1
    tf = barlow(24, "Bold")
    ty = y1 + 42
    draw_text(img, (L + 8, ty), "HOME", tf, rgb(colors["orange"]), 5, "ls")
    draw_text(img, (R - 8, ty), "AWAY", tf, rgb(colors["orange"]), 5, "rs")
    ny = ty + 66
    labels, nf = table_labels([(game[sd]["name"].upper(), short_label(game[sd], cfg)) for sd in ("home", "away")],
                              lambda z: barlow(z, "ExtraBoldItalic"), 60, 430, 36)
    for (side, x, anc), label in zip((("home", L + 6, "ls"), ("away", R - 6, "rs")), labels):
        draw_text(img, (x, ny), label, nf, name_color(game, side, colors), 2, anc)
    return ny + 4


def scorebug_v(img, game, colors, cfg, cache_dir, y0, row_h=232, gap=18):
    """Story scorebug: two stacked team rows, [logo][tag+name][score]."""
    y = y0
    for side, tag in (("home", "HOME"), ("away", "AWAY")):
        team = game[side]
        win = winner_side(game) == side
        y1 = y + row_h
        poly(img, chamfer(L, y, R, y1, 30), (4, 16, 9, 235), shadow=(160, 20, 0, 16))
        if win and team.get("is_us"):
            poly(img, [(L + 30, y), (R, y), (R, y + 5), (L + 25, y + 5)], rgb(colors["orange"]))
        pad = 12
        s = row_h - 2 * pad
        lg, kind = logo_image(team, cache_dir, colors, cfg)
        logo_tile(img, lg, kind, (L + pad, y + pad, s), colors, shadow=False)
        cw = 290
        pts = cell(R - pad - cw, y + pad, R - pad, y1 - pad, 0.17)
        score_cell(img, game, side, pts, row_h, cw - 70, size_k=0.76)
        nx = L + pad + s + 34
        maxw = (R - pad - cw - 30) - nx
        label = team["name"].upper()
        nf = fit_font(label, lambda z: barlow(z, "ExtraBoldItalic"), 88, maxw, tracking=2)
        tf = barlow(28, "Bold")
        _, nt, _, nb = ink_box(nf, label)
        _, tt, _, tb = ink_box(tf, tag)
        block = (tb - tt) + 18 + (nb - nt)
        top0 = y + row_h / 2 - block / 2
        draw_text(img, (nx + 2, top0 - tt), tag, tf, rgb(colors["orange"]), 6, "ls")
        draw_text(img, (nx, top0 + (tb - tt) + 18 - nt), label, nf, name_color(game, side, colors), 2, "ls")
        y = y1 + gap
    return y - gap


def box_score(img, game, colors, cfg, cache_dir, x0, x1, y0, row_h=84, head_h=50):
    """Box score: per-period goals, total and shots, one row per team.

    Digits are sized from row_h (row_h 80 -> ~41 px cap height at 1080 width)."""
    periods = game.get("periods") or []
    shots = side_shots(game)
    if not periods and not shots:
        return y0
    cols = [(period_label(p.get("label", i + 1)), "p", i) for i, p in enumerate(periods)]
    cols.append(("T", "t", None))
    if shots:
        cols.append(("SHOTS", "s", None))
    unit = row_h * 1.3
    widths = [{"p": unit, "t": unit * 1.12, "s": unit * 1.5}[k] for _, k, _ in cols]
    room = (x1 - x0) - 18 - max(300, row_h * 3.4)  # keep space for logo + team name
    if sum(widths) > room:
        widths = [w_ * room / sum(widths) for w_ in widths]
    total_w = sum(widths)
    y1 = y0 + head_h + 2 * row_h + 6
    poly(img, chamfer(x0, y0, x1, y1, 22), (4, 16, 9, 228), shadow=(150, 18, 0, 14))
    poly(img, chamfer(x0, y0, x1, y0 + head_h, 22, "tl"), (255, 255, 255, 16))
    hf = barlow(int(head_h * 0.58), "Bold")
    hb = baseline_for_center(hf, "H", y0 + head_h / 2)
    draw_text(img, (x0 + 28, hb), "BOX SCORE", hf, rgb(colors["orange"]), 5)
    cx = x1 - total_w - 18
    col_x = []
    for (lab, kind, _), wdt in zip(cols, widths):
        col_x.append((cx, cx + wdt))
        draw_text(img, (cx + wdt / 2, hb), lab, hf,
                  rgb(colors["orange"]) if kind == "t" else rgb(colors["cream"], 190), 3, "ms")
        cx += wdt
    for (lab, kind, _), (a, b) in zip(cols, col_x):
        if kind == "t":
            poly(img, [(a + 6, y0 + head_h), (b - 6, y0 + head_h), (b - 6, y1 - 8), (a + 6, y1 - 8)],
                 (255, 255, 255, 20))
    win = winner_side(game)
    ls = int(row_h * 0.68)
    nmax = col_x[0][0] - (x0 + 24 + ls + 18) - 14
    labels, nf = table_labels([(game[sd]["name"].upper(), short_label(game[sd], cfg)) for sd in ("home", "away")],
                              lambda z: barlow(z, "Bold"), int(row_h * 0.5), nmax, 28)
    df = barlow(int(row_h * 0.72), "SemiBold")
    tfnt = barlow(int(row_h * 0.78), "ExtraBold")
    for r, side in enumerate(("home", "away")):
        team = game[side]
        ry = y0 + head_h + 3 + r * row_h
        if r == 1:
            line(img, [(x0 + 24, ry), (x1 - 24, ry)], (255, 255, 255, 34), 2)
        lg, kind = logo_image(team, cache_dir, colors, cfg)
        small_logo(img, lg, kind, x0 + 24, ry + (row_h - ls) / 2, ls, colors)
        nx = x0 + 24 + ls + 18
        label = labels[r]
        cy = ry + row_h / 2
        draw_text(img, (nx, baseline_for_center(nf, "NA", cy)), label, nf, name_color(game, side, colors), 2)
        for (lab, kind, i), (a, b) in zip(cols, col_x):
            if kind == "p":
                v, f, col = str(periods[i].get(side, "")), df, rgb(colors["cream"], 235)
            elif kind == "t":
                v, f = str(team["goals"]), tfnt
                if win is None:
                    col = rgb(colors["cream"])
                elif win == side:
                    col = rgb(colors["orange"]) if team.get("is_us") else rgb(colors["cream"])
                else:
                    col = rgb(colors["cream"], 150)
            else:
                v, f, col = str(shots[0] if side == "home" else shots[1]), df, rgb(colors["cream"], 235)
            draw_text(img, ((a + b) / 2, baseline_for_center(f, "0", cy)), v, f, col, 0, "ms")
    return y1


def scorers_panel(img, game, colors, cfg, x0, x1, y0, max_lines=3, size=46, min_h=0):
    """Näädät goal scorers ('Kuosmanen 3 · Ahola 2'); goalie line when no goals."""
    parts = scorer_parts(game)
    us_goals = us_team(game)["goals"]
    gk = [g for g in game.get("goalies") or [] if g.get("is_us") and g.get("saves") is not None]
    if not parts and (us_goals or not gk):
        return y0  # unknown (hand-entered game without scorers): leave it out rather than guess
    us_name = cfg["display_name"].upper()
    label = "%s GOALS" % us_name if parts else "%s SAVES" % us_name
    tx0 = x0 + 30
    tx1 = x1 - 30
    sep = "  ·  "
    missing = us_goals - sum(c for _, c in parts) if parts else 0
    if parts:
        tokens = ["%s %d" % (n, c) if c > 1 else n for n, c in parts]
        if missing > 0:
            # hand-entered game with only some scorers known: say so, never imply
            # the listed players scored every goal
            tokens.append("+%d more" % missing)
        while True:
            f = barlow(size, "SemiBold")
            lines = wrap_parts(tokens, f, tx1 - tx0, sep)
            if len(lines) <= max_lines or size <= 30:
                break
            size -= 2
        lines = lines[:max_lines]
    else:
        f = barlow(size, "SemiBold")
        lines = [["%s %d" % (name_of(g.get("name"), "last"), g["saves"]) for g in gk]]
    tab_h = 50
    line_h = int(size * 1.22)
    body_top = y0 + tab_h + 26
    y1 = max(body_top + line_h * len(lines) + 14, y0 + min_h)
    poly(img, chamfer(x0, y0 + tab_h / 2, x1, y1, 22, "br"), (4, 16, 9, 215), shadow=(140, 18, 0, 14))
    lf = barlow(30, "ExtraBoldItalic")
    lw = text_w(label, lf, 4) + 62
    poly(img, para(x0, y0, x0 + lw, y0 + tab_h), rgb(colors["orange"]), shadow=(110, 8, 3, 6))
    draw_text(img, (x0 + 24, baseline_for_center(lf, label, y0 + tab_h / 2)), label, lf,
              rgb(colors["green_dark"]), 4)
    chevrons(img, x0 + lw + 30, y0 + tab_h / 2 + 2, 3, 20, rgb(colors["orange"], 200), step=15, thick=4)
    y = body_top + size * 0.82
    dot = rgb(colors["orange"])
    fb = barlow(size, "ExtraBold")
    for ln in lines:
        x = tx0
        for i, tok in enumerate(ln):
            if i:
                draw_text(img, (x, y), sep, f, dot)
                x += f.getlength(sep)
            head, _, tail = tok.rpartition(" ")
            if tok.startswith("+"):
                draw_text(img, (x, y), tok, f, rgb(colors["cream"], 165))
                x += f.getlength(tok)
            elif tail.isdigit() and head:
                draw_text(img, (x, y), head + " ", f, rgb(colors["cream"]))
                x += f.getlength(head + " ")
                draw_text(img, (x, y), tail, fb, dot)
                x += fb.getlength(tail)
            else:
                draw_text(img, (x, y), tok, f, rgb(colors["cream"]))
                x += f.getlength(tok)
        y += line_h
    return y1


def footer(img, game, colors, cfg, y):
    """Rule, club lockup (crest + HERNESAAREN NÄÄDÄT) left, handle right."""
    line(img, [(L, y - 46), (R, y - 46)], (255, 255, 255, 40), 2)
    poly(img, para(L, y - 49, L + 120, y - 43), rgb(colors["orange"]))
    handle = cfg["instagram"].upper()
    hf = barlow(32, "ExtraBoldItalic")
    hw = text_w(handle, hf, 3)
    _, t, _, b = ink_box(hf, "H")
    cy = y + (t + b) / 2
    lockup(img, cfg, colors, L, cy, 48, 30)
    draw_text(img, (R, y), handle, hf, rgb(colors["orange"]), 3, "rs")
    chevrons(img, R - hw - 54, cy, 2, 18, rgb(colors["orange"], 230), step=13, thick=4, fade=False)


# ---------------------------------------------------------------- slides

def _layout(free_total, n_blocks, want_window, min_gap, max_gap):
    """Split free height into gaps (+ a clear photo window). Returns (gap, window, lead)."""
    window = min(want_window, max(0, free_total - min_gap * (n_blocks + 1.3)))
    gap = (free_total - window) / (n_blocks + 1.3)
    gap = max(min_gap * 0.6, min(gap, max_gap))
    rest = free_total - window - gap * (n_blocks + 1.3)
    if want_window:
        window += max(0, rest)          # photo: every spare pixel shows the photo
        lead = gap * 1.3 + window
    else:
        lead = gap * 1.3 + max(0, rest) / 2   # no photo: centre the block
    return gap, window, lead


def result_slide(game, cfg, cache_dir, photo=None, height=H):
    """Slide 1. height=H: 4:5 feed. height=STORY_H: dedicated 9:16 story layout."""
    if height > H:
        return _result_story(game, cfg, cache_dir, photo)
    colors = cfg["colors"]
    seed = game_seed(game)
    scratch = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hb = header(scratch, "FINAL", game, colors, cfg, top=66, size=204)
    content_end = 1206  # footer rule at 1250, baseline 1296
    has_box = bool(game.get("periods") or side_shots(game))
    box_h = box_score(scratch, game, colors, cfg, cache_dir, L, R, 0) if has_box else 0
    sc_h = scorers_panel(scratch, game, colors, cfg, L, R, 0, max_lines=2, size=46)
    blocks = [b for b in (box_h, sc_h) if b]
    want = 0 if photo is None else 230
    # the scorebug grows into space a sparse game (no box score) would leave empty
    for hgt in range(320 if not has_box else 300, 199, -4):
        bug_h = hgt + 112          # scorebug_h with names: panel + HOME/AWAY + name line
        free = content_end - hb - bug_h - sum(blocks)
        if free - want >= 46 * (len(blocks) + 1.3):
            break
    gap, window, lead = _layout(free, len(blocks), want, 46, 72 if has_box else 115)
    y0 = hb + lead
    img = background(W, H, colors, seed, photo, hero=(0.40,), photo_window=(hb, y0 + 30))
    header(img, "FINAL", game, colors, cfg, top=66, size=204, scrim=photo is not None)
    add_speed(img, colors, seed, y0 + 10, y0 + hgt - 10)
    y = scorebug_h(img, game, colors, cfg, cache_dir, y0, hgt)
    if has_box:
        y = box_score(img, game, colors, cfg, cache_dir, L, R, y + gap)
    if sc_h:
        scorers_panel(img, game, colors, cfg, L, R, y + gap, max_lines=2, size=46)
    footer(img, game, colors, cfg, 1296)
    return img


STORY_HEM = 232     # top and bottom hem height (mirrored)


def _result_story(game, cfg, cache_dir, photo=None):
    colors = cfg["colors"]
    seed = game_seed(game)
    lock_cy, head_top = 318, 396    # key content starts below y~270 (IG header band)
    scratch = Image.new("RGBA", (W, STORY_H), (0, 0, 0, 0))
    hb = header(scratch, "FINAL", game, colors, cfg, top=head_top, size=224)
    content_end = STORY_H - STORY_HEM - 40
    has_box = bool(game.get("periods") or side_shots(game))
    box_rh, box_hh = 88, 56
    box_h = box_score(scratch, game, colors, cfg, cache_dir, L, R, 0, row_h=box_rh, head_h=box_hh) if has_box else 0
    sc_h = scorers_panel(scratch, game, colors, cfg, L, R, 0, max_lines=3, size=50)
    blocks = [b for b in (box_h, sc_h) if b]
    want = 0 if photo is None else 300
    rgap = 18
    for row_h in range(320, 179, -4):
        bug_h = 2 * row_h + rgap
        free = content_end - hb - bug_h - sum(blocks)
        if free - want >= 40 * (len(blocks) + 1.3):
            break
    gap, window, lead = _layout(free, len(blocks), want, 40, 90 if has_box else 130)
    y = hb + lead
    img = background(W, STORY_H, colors, seed, photo, hero=(0.42,), photo_window=(hb, y + 40),
                     photo_top=STORY_HEM)
    hem(img, colors, seed, top=True)      # y 0..232, under the IG story header
    hem(img, colors, seed, top=False)     # mirrored, y 1688..1920 under the reply bar
    # no @handle in the story lockup: Instagram shows the account name right above it
    lockup(img, cfg, colors, L, lock_cy, 84, 36)
    header(img, "FINAL", game, colors, cfg, top=head_top, size=224, scrim=photo is not None)
    add_speed(img, colors, seed, y - 20, y + bug_h + 20, count=30)
    y = scorebug_v(img, game, colors, cfg, cache_dir, y, row_h=row_h, gap=rgap)
    if has_box:
        y = box_score(img, game, colors, cfg, cache_dir, L, R, y + gap, row_h=box_rh, head_h=box_hh)
    if sc_h:
        scorers_panel(img, game, colors, cfg, L, R, y + gap, max_lines=3, size=50)
    return img


def _log_rows(goals):
    rows, cur = [], None
    for g in goals:
        if g.get("period") != cur:
            cur = g.get("period")
            rows.append(("hdr", cur, False))
        rows.append(("goal", g, False))
    return rows


def _split_two(rows):
    """Split log rows into two columns of near-equal length.

    A split inside a period repeats its header as '(CONT.)' in the right column."""
    best = None
    for i in range(1, len(rows)):
        left, right = rows[:i], rows[i:]
        if left[-1][0] == "hdr":
            continue
        r = ([("hdr", right[0][1]["period"], True)] if right[0][0] == "goal" else []) + right
        key = (max(len(left), len(r)), abs(len(left) - len(r)), -len(left))
        if best is None or key < best[0]:
            best = (key, left, r)
    return best[1], best[2]


PERIOD_NAMES = {1: "1ST PERIOD", 2: "2ND PERIOD", 3: "3RD PERIOD", 4: "OVERTIME"}


def _goal_type(g):
    for k in ("type", "type_raw"):
        t = (g.get(k) or "").strip().upper()
        if t in TYPE_LABEL:
            return TYPE_LABEL[t]
    return ""


def _draw_log_col(img, rows, game, cfg, cache_dir, x0, x1, top, row_h, size, compact, pitch=None, chip=False):
    """One column of the goal log. pitch: vertical step per row (>= row_h when a
    shorter column is spread to end level with its neighbour). chip: draw the
    scoring team's logo at the right edge (full-width layout)."""
    colors = cfg["colors"]
    pitch = pitch or row_h
    f_time = barlow(int(size * 0.84), "SemiBold")
    f_score = barlow(size, "ExtraBold")
    f_name = barlow(size, "Bold")
    f_tag = barlow(int(size * 0.66), "ExtraBold")
    tw_time = text_w("00:00", f_time)
    tw_score = max(text_w("00–00", f_score),
                   max((text_w((it.get("score_after") or "").replace("-", "–"), f_score)
                        for k, it, _ in rows if k == "goal"), default=0))
    y = top
    for kind, item, cont in rows:
        if kind == "hdr":
            label = PERIOD_NAMES.get(item, "PERIOD %s" % item)
            if cont:
                label += " (CONT.)"
            hf = barlow(int(size * 0.76), "ExtraBoldItalic")
            th = min(row_h * 0.70, size * 1.15)
            ty = y + row_h - th - row_h * 0.10
            tw = text_w(label, hf, 3) + 40
            poly(img, para(x0, ty, x0 + tw, ty + th), rgb(colors["orange"]))
            draw_text(img, (x0 + 16, baseline_for_center(hf, label, ty + th / 2)), label, hf,
                      rgb(colors["green_dark"]), 3)
            line(img, [(x0 + tw + 14, ty + th / 2), (x1, ty + th / 2)], (255, 255, 255, 40), 2)
            y += pitch
            continue
        g = item
        ours = g.get("team") == "us"
        cy = y + row_h / 2
        if ours:
            poly(img, [(x0, y + 3), (x1, y + 3), (x1, y + row_h - 3), (x0, y + row_h - 3)],
                 rgb(colors["orange"], 30))
            poly(img, para(x0, y + 3, x0 + 8, y + row_h - 3), rgb(colors["orange"]))
        col = rgb(colors["cream"]) if ours else rgb(colors["cream"], 165)
        bl = baseline_for_center(f_name, "H", cy)
        x = x0 + 20
        draw_text(img, (x, bl), g.get("time") or "", f_time, rgb(colors["cream"], 205 if ours else 140))
        x += tw_time + size * 0.5
        sc = (g.get("score_after") or "").replace("-", "–")
        draw_text(img, (x, bl), sc, f_score, rgb(colors["orange"]) if ours else col)
        x += tw_score + size * 0.5
        right = x1 - 10
        if chip:
            side = game.get("us_side", "home") if ours else ("away" if game.get("us_side", "home") == "home" else "home")
            lg, kind = logo_image(game[side], cache_dir, colors, cfg)
            cs = int(row_h * 0.62)
            small_logo(img, lg, kind, right - cs, cy - cs / 2, cs, colors)
            right -= cs + 18
        tag = _goal_type(g)
        if tag:
            tw = text_w(tag, f_tag, 2) + 16
            th = size * 0.86
            fill = rgb(colors["orange"]) if ours else rgb(colors["cream"], 210)
            poly(img, para(right - tw, cy - th / 2, right, cy + th / 2, k=0.15), fill)
            draw_text(img, (right - tw / 2 + 0.075 * th, baseline_for_center(f_tag, tag, cy)), tag, f_tag,
                      rgb(colors["green_dark"]), 2, "ms")
            right -= tw + 14
        avail = right - x
        who = name_of(g.get("scorer"))
        names = [brief_name(a, game, g.get("team")) for a in (g.get("assists") or [])]
        assists = ", ".join(n for n in names if n and n != "?")
        nw = f_name.getlength(who)
        if nw > avail:
            who, assists = ellipsize(who, f_name, avail), ""
            nw = f_name.getlength(who)
        draw_text(img, (x, bl), who, f_name, col)
        x += nw
        if assists:
            room = avail - nw - size * 0.42
            k = 0.80
            fa = barlow(int(size * k), "Medium")
            while fa.getlength(assists) > room and k > 0.64:
                k -= 0.04
                fa = barlow(int(size * k), "Medium")
            txt = ellipsize(assists, fa, room) if room > size else ""
            if txt:
                draw_text(img, (x + size * 0.42, bl), txt, fa, rgb(colors["cream"], 150 if ours else 115))
        y += pitch
    return y


def _row_need(g, size, colw, game):
    """Width a compact log row needs at `size` (time, score, name, assists, tag)."""
    f_time = barlow(int(size * 0.84), "SemiBold")
    f_score = barlow(size, "ExtraBold")
    need = 20 + text_w("00:00", f_time) + size * 0.5
    need += max(text_w("00–00", f_score), text_w((g.get("score_after") or "").replace("-", "–"), f_score)) + size * 0.5
    need += barlow(size, "Bold").getlength(name_of(g.get("scorer")))
    names = [brief_name(a, game, g.get("team")) for a in (g.get("assists") or [])]
    assists = ", ".join(n for n in names if n and n != "?")
    if assists:
        need += size * 0.42 + barlow(int(size * 0.72), "Medium").getlength(assists)
    if _goal_type(g):
        need += text_w(_goal_type(g), barlow(int(size * 0.66), "ExtraBold"), 2) + 30
    return need + 10


def _team_stat_lines(game, cfg, values):
    """[(label, value)] in home-away order, matching slide 1."""
    out = []
    for side in ("home", "away"):
        out.append((team_label(game[side], cfg), values[side], bool(game[side].get("is_us"))))
    return out


def summary_stats(game, cfg):
    stats = []
    sh = side_shots(game)
    if sh:
        stats.append(("SHOTS", _team_stat_lines(game, cfg, {"home": str(sh[0]), "away": str(sh[1])})))
    goals = [g for g in game.get("goals") or [] if g.get("period") != 5]
    typed = any(_goal_type(g) for g in goals) or (game.get("source") not in (None, "", "manual"))
    if goals and typed:
        pp = {"home": 0, "away": 0}
        us = game.get("us_side", "home")
        them = "away" if us == "home" else "home"
        for g in goals:
            if _goal_type(g) == "PP":
                pp[us if g.get("team") == "us" else them] += 1
        stats.append(("PP GOALS", _team_stat_lines(game, cfg, {k: str(v) for k, v in pp.items()})))
    gk = [g for g in game.get("goalies") or [] if g.get("saves") is not None]
    if gk:
        vals = {}
        for side in ("home", "away"):
            mine = [g for g in gk if bool(g.get("is_us")) == bool(game[side].get("is_us"))]
            vals[side] = "  ·  ".join("%s %s" % (name_of(g.get("name"), "last"), g["saves"]) for g in mine) or "–"
        stats.append(("SAVES", _team_stat_lines(game, cfg, vals)))
    return stats


def summary_slide(game, cfg, cache_dir):
    colors = cfg["colors"]
    seed = game_seed(game) + 1
    img = background(W, H, colors, seed, None, hero=(0.22,))
    add_speed(img, colors, seed, 280, 400, count=16)
    header(img, "SUMMARY", game, colors, cfg, top=62, size=140, tag=False)
    home, away = game["home"], game["away"]
    by0, bh = 262, 118
    poly(img, chamfer(L, by0, R, by0 + bh, 22), (4, 16, 9, 235), shadow=(160, 18, 0, 14))
    poly(img, [(L + 22, by0), (R, by0), (R, by0 + 4), (L + 18, by0 + 4)], accent(game, colors))
    pad = 10
    s = bh - 2 * pad
    lh, kh = logo_image(home, cache_dir, colors, cfg)
    la, ka = logo_image(away, cache_dir, colors, cfg)
    logo_tile(img, lh, kh, (L + pad, by0 + pad, s), colors, shadow=False)
    logo_tile(img, la, ka, (R - pad - s, by0 + pad, s), colors, shadow=False)
    mid = (L + R) / 2
    cw = 138
    shs = 0.17 * (bh - 2 * pad)
    score_cell(img, game, "home", cell(mid - 7 - cw, by0 + pad, mid + shs / 2 - 7, by0 + bh - pad, 0.17), 98, cw - 30)
    score_cell(img, game, "away", cell(mid - shs / 2 + 7, by0 + pad, mid + 7 + cw, by0 + bh - pad, 0.17), 98, cw - 30)
    maxw = (mid - 7 - cw - 24) - (L + pad + s + 24)
    labels, nf = table_labels([(game[sd]["name"].upper(), short_label(game[sd], cfg)) for sd in ("home", "away")],
                              lambda z: barlow(z, "ExtraBoldItalic"), 50, maxw, 30)
    for side, x, anc, label in (("home", L + pad + s + 24, "ls", labels[0]),
                                ("away", R - pad - s - 24, "rs", labels[1])):
        draw_text(img, (x, baseline_for_center(nf, "NA", by0 + bh / 2)), label, nf,
                  name_color(game, side, colors), 2, anc)

    # goal log
    goals = [g for g in game.get("goals") or [] if g.get("period") != 5]
    us_label = cfg["display_name"].upper()
    stats = summary_stats(game, cfg)
    sy1 = 1232
    sy0 = sy1 - 150 if stats else sy1
    top, bottom = by0 + bh + 26, sy0 - 26
    avail = bottom - top
    note = None
    rows = _log_rows(goals)
    # Up to 10 slots (goals + period headers): one full-width column, each row
    # carrying the scoring team's logo chip and PP/SH/EN badge at the right edge.
    # More: two columns balanced by slot count (headers included); the shorter
    # column is spread so both end at the same height.
    single = bool(rows) and len(rows) <= 10
    if single:
        row_h = min(86, avail / len(rows))
        size = int(min(46, row_h * 0.6))
        t0 = top + (avail - row_h * len(rows)) / 2
        _draw_log_col(img, rows, game, cfg, cache_dir, L, R, t0, row_h, size, False, chip=True)
    elif rows:
        left, right = _split_two(rows)
        n = max(len(left), len(right))
        if avail / n < 30:
            goals = [g for g in goals if g.get("team") == "us"]
            note = "%s GOALS SHOWN" % us_label
            rows = _log_rows(goals)
            left, right = _split_two(rows) if len(rows) > 1 else (rows, [])
            n = max(len(left), len(right), 1)
        row_h = min(58, avail / n)
        gx = 28
        colw = (R - L - gx) / 2
        size = int(min(32, row_h * 0.70))
        while size > 20 and any(_row_need(it, size, colw, game) > colw for k, it, _ in rows if k == "goal"):
            size -= 1
        row_h = min(row_h, max(size * 1.8, 36))
        t0 = top + (avail - row_h * n) / 2
        for col, cx0, cx1 in ((left, L, L + colw), (right, L + colw + gx, R)):
            if col:
                pitch = row_h * n / len(col)
                _draw_log_col(img, col, game, cfg, cache_dir, cx0, cx1, t0, row_h, size, True, pitch=pitch)
        line(img, [(L + colw + gx / 2, t0 + 10), (L + colw + gx / 2, t0 + row_h * n - 6)],
             (255, 255, 255, 34), 2)
    if note:
        draw_text(img, (R, by0 + bh + 20), note, barlow(22, "Bold"), rgb(colors["orange"]), 4, "rs")

    # stats strip: one cell per stat, home line over away line (same order as slide 1)
    if stats:
        col_w = min((R - L) / len(stats), 360)
        sx = L + ((R - L) - col_w * len(stats)) / 2
        poly(img, chamfer(sx, sy0, sx + col_w * len(stats), sy1, 18, "tl br"), (4, 16, 9, 220))
        kf = barlow(24, "Bold")
        # one label font for the whole strip, short labels as in the box score
        pairs = [(team_label(game[sd], cfg), short_label(game[sd], cfg)) for sd in ("home", "away")]
        labels, lf = table_labels(pairs, lambda z: barlow(z, "SemiBold"), 32, min(150, col_w * 0.42), 28, 1)
        lab_w = max(text_w(t, lf, 1) for t in labels)
        for i, (k, lines) in enumerate(stats):
            cx0 = sx + i * col_w + 26
            cx1 = sx + (i + 1) * col_w - 22
            if i:
                line(img, [(sx + i * col_w, sy0 + 18), (sx + i * col_w, sy1 - 18)], (255, 255, 255, 36), 2)
            draw_text(img, (cx0, sy0 + 38), k, kf, rgb(colors["orange"]), 4)
            vroom = cx1 - cx0 - lab_w - 18
            split = []
            for _, val, _ in lines:
                head, _, num = val.rpartition(" ")
                if not (head and num.isdigit() and "·" not in head):
                    head, num = "", val
                split.append((head, num))

            def vwidth(sz, hd, nm):
                return text_w(nm, barlow(sz, "ExtraBold")) + (barlow(sz, "SemiBold").getlength(hd + " ") if hd else 0)

            vsize = 36  # one value size per cell
            while vsize > 24 and any(vwidth(vsize, hd, nm) > vroom for hd, nm in split):
                vsize -= 2
            vf, hf2 = barlow(vsize, "ExtraBold"), barlow(vsize, "SemiBold")
            for j, ((lab_, val, is_us), (head, num)) in enumerate(zip(lines, split)):
                yb = sy0 + 84 + j * 44
                vcol = rgb(colors["orange"]) if is_us else rgb(colors["cream"])
                draw_text(img, (cx0, yb), labels[j], lf, rgb(colors["cream"], 215), 1)
                if not head:
                    num = ellipsize(num, vf, vroom)
                nw = draw_text(img, (cx1, yb), num, vf, vcol, 0, "rs")
                if head:
                    head = ellipsize(head, hf2, vroom - nw - hf2.getlength(" "))
                    draw_text(img, (cx1 - nw, yb), head + " ", hf2, vcol, 0, "rs")
    footer(img, game, colors, cfg, 1296)
    return img


# ---------------------------------------------------------------- main

def main(argv=None):
    global GAME_DIR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--photo", help="optional real, unedited camera photo (jpg/png) shown at the top of "
                    "slide 1 and the story; slide 2 (summary) stays photo-free on purpose. A photo or "
                    "logo_file that declares AI generation/editing is refused (exit 4)")
    ap.add_argument("--no-summary", action="store_true", help="skip slide 2")
    ap.add_argument("--no-story", action="store_true")
    ap.add_argument("--png", action="store_true", help="also write lossless PNG copies")
    args = ap.parse_args(argv)

    with open(os.path.join(ASSETS, "brand", "team.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    with open(args.game_json, encoding="utf-8") as f:
        game = json.load(f)
    GAME_DIR = os.path.dirname(os.path.abspath(args.game_json))
    game["_colors"] = cfg["colors"]
    for side in ("home", "away"):
        game[side].setdefault("is_us", game.get("us_side") == side)
        game[side]["goals"] = goals_of(game[side])
    if args.photo and not os.path.exists(args.photo):
        sys.exit("photo not found: %s" % args.photo)
    # Inputs that declare AI generation or editing are refused before any pixel
    # is read, so a render never launders an AI marker into a "clean" file.
    if args.photo:
        refuse_if_ai(args.photo, "photo")
    for side in ("home", "away"):
        lf = game[side].get("logo_file")
        if lf and not game[side].get("is_us"):
            p = lf if os.path.isabs(lf) else os.path.join(GAME_DIR, lf)
            if os.path.exists(p):
                refuse_if_ai(p, "logo_file")
    photo = None
    if args.photo:
        try:
            photo = load_photo(args.photo)
        except Exception as exc:  # noqa: BLE001
            sys.exit("could not read photo %s: %s" % (args.photo, exc))
    os.makedirs(args.out, exist_ok=True)
    # downloaded logos are cached outside the hand-over folder
    cache = os.path.join(tempfile.gettempdir(), "naadat-result-post-logos")

    written = []
    feed = result_slide(game, cfg, cache, photo)
    written.append(save_clean(feed, os.path.join(args.out, "result_feed.jpg")))
    if args.png:
        written.append(save_clean(feed, os.path.join(args.out, "result_feed.png")))
    has_log = any(g.get("period") != 5 for g in game.get("goals") or [])
    if not args.no_summary and not has_log:
        print("note: no goal log in game.json, so slide 2 (summary) was skipped")
    elif not args.no_summary:
        summ = summary_slide(game, cfg, cache)
        written.append(save_clean(summ, os.path.join(args.out, "summary_feed.jpg")))
    if not args.no_story:
        story = result_slide(game, cfg, cache, photo, height=STORY_H)
        written.append(save_clean(story, os.path.join(args.out, "result_story.jpg")))
    for p in written:
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
