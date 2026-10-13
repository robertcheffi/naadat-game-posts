#!/usr/bin/env python3
"""Render Instagram result graphics for a game.json produced by fetch_game.py
(or written by hand from references/manual_game_template.json).

Look: a TV-sports match graphics package. Slanted type, chamfered panels, a
scorebug with both logos and the score as the hero, a box score, speed lines
and chevrons as motion cues.

Outputs (all metadata-free, see clean_image.py):
  <out>/result_feed.jpg      1080x1350  feed post, slide 1
  <out>/summary_feed.jpg     1080x1350  feed post, slide 2 (goal log, shots, goalies)
  <out>/result_story.jpg     1080x1920  story (own 9:16 layout)

Everything is drawn with Pillow from real data, fonts and logo files. No
generative AI image model is involved anywhere.

  python render_card.py game.json --out out/
  python render_card.py game.json --out out/ --photo team_photo.jpg
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.request

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required: pip install pillow")

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL_DIR, "assets")
FONTS = os.path.join(ASSETS, "fonts")
sys.path.insert(0, HERE)
from clean_image import save_clean  # noqa: E402

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


def text_w(draw, text, f, tracking=0):
    if not text:
        return 0
    return draw.textlength(text, font=f) + tracking * (len(text) - 1)


def draw_text(draw, xy, text, f, fill, tracking=0, anchor="ls"):
    """Draw text with letter spacing. anchor: l/m/r horizontal + baseline 's'."""
    x, y = xy
    width = text_w(draw, text, f, tracking)
    if anchor[0] == "m":
        x -= width / 2
    elif anchor[0] == "r":
        x -= width
    if not tracking:
        draw.text((x, y), text, font=f, fill=fill, anchor="l" + anchor[1])
        return width
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill, anchor="l" + anchor[1])
        x += draw.textlength(ch, font=f) + tracking
    return width


def fit_font(draw, text, maker, start, max_w, min_size=10, tracking=0):
    size = start
    while size > min_size and text_w(draw, text, maker(size), tracking) > max_w:
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
    draw_text(d, (x, yb - top), text, f, fill, tracking, anchor)
    by = yb - top
    layer = layer.transform(layer.size, AFFINE, (1, k, -k * by, 0, 1, 0), resample=BICUBIC)
    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").point(lambda v: v * shadow[0] // 255))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow[1]))
        img.alpha_composite(sh, (shadow[2], top + shadow[3]))
    img.alpha_composite(layer, (0, top))


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


def wrap_parts(draw, parts, f, max_w, sep):
    lines, cur = [], []
    for p in parts:
        trial = sep.join(cur + [p])
        if not cur or draw.textlength(trial, font=f) <= max_w:
            cur.append(p)
        else:
            lines.append(cur)
            cur = [p]
    if cur:
        lines.append(cur)
    return lines


def ellipsize(draw, text, f, max_w):
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text.rstrip(" ,(") + "…"


# ---------------------------------------------------------------- background

def noise_tile(seed, size=256):
    rng = random.Random(seed)
    data = bytes(rng.getrandbits(8) for _ in range(size * size))
    return Image.frombytes("L", (size, size), data)


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
    # fade the streak tips: multiply alpha by a horizontal ramp
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


def background(w, h, colors, seed, photo=None, hero=(0.45,)):
    rng = random.Random(seed)
    base = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
    if photo:
        ph = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
        ph = ImageOps.fit(ph, (w, h), RESAMPLE, centering=(0.5, 0.4))
        ph = ImageOps.grayscale(ph).convert("RGB")
        tint = Image.new("RGB", (w, h), rgb(colors["green"])[:3])
        ph = Image.blend(ph, tint, 0.45)
        base.paste(ph.convert("RGBA"), (0, 0))
        veil = Image.new("L", (1, h))
        for y in range(h):
            t = y / h
            veil.putpixel((0, y), int(255 * (0.48 + 0.40 * min(1, max(0, (t - 0.30) / 0.45)))))
        veil = veil.resize((w, h))
        dark = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
        base = Image.composite(dark, base, veil)
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

    # angular light shards: two big slanted planes crossing the frame
    shards = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shards)
    sd.polygon([(w * 0.58, 0), (w * 1.0, 0), (w * 1.0, h * 0.10), (w * 0.30, h)], fill=(255, 255, 255, 7))
    sd.polygon([(w * 0.70, 0), (w * 0.78, 0), (w * 0.40, h), (w * 0.32, h)], fill=(255, 255, 255, 6))
    sd.polygon([(-w * 0.1, h * 0.70), (w * 0.38, h * 0.70 - w * 0.48 * 0.6), (w * 0.44, h * 0.70 - w * 0.48 * 0.6),
                (-w * 0.04, h * 0.70 + 40)], fill=rgb(colors["orange"], 10))
    base = Image.alpha_composite(base, shards)

    # fine scanline texture
    scan = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    scd = ImageDraw.Draw(scan)
    for y in range(0, h, 6):
        scd.line([(0, y), (w, y)], fill=(0, 0, 0, 22), width=2)
    base = Image.alpha_composite(base, scan)

    # deterministic grain
    tile = noise_tile(seed)
    grain_l = Image.new("L", (w, h))
    for ty in range(0, h, tile.height):
        for tx in range(0, w, tile.width):
            grain_l.paste(tile, (tx, ty))
    grain = Image.merge("RGBA", (grain_l, grain_l, grain_l, Image.new("L", (w, h), 11)))
    base = Image.alpha_composite(base, grain)

    # vignette
    vig = Image.new("L", (w, h), 255)
    ImageDraw.Draw(vig).ellipse((-w * 0.3, -h * 0.12, w * 1.3, h * 1.12), fill=0)
    vig = vig.filter(ImageFilter.GaussianBlur(110))
    base = Image.composite(Image.new("RGBA", (w, h), (0, 0, 0, 150)), base, vig)
    return base


def add_speed(img, colors, seed, y0, y1, count=26):
    rng = random.Random(seed * 7 + 3)
    img.alpha_composite(speed_lines(img.width, img.height, rng, y0, y1, "l", count, colors))
    img.alpha_composite(speed_lines(img.width, img.height, rng, y0, y1, "r", count, colors))


# ---------------------------------------------------------------- logos

def logo_image(team, cache_dir, colors, cfg):
    """Return (RGBA image, kind) with kind in brand | logo | badge."""
    if team.get("is_us"):
        p = os.path.join(ASSETS, "brand", cfg["logo"])
        return Image.open(p).convert("RGBA"), "brand"
    candidates = []
    if team.get("logo_file"):
        candidates.append(team["logo_file"])
    if team.get("association_id"):
        candidates.append(os.path.join(ASSETS, "logos", "%s.png" % team["association_id"]))
    for c in candidates:
        if c and os.path.exists(c):
            return Image.open(c).convert("RGBA"), "logo"
    url = team.get("logo_url")
    if url:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, "logo_%s.png" % team.get("association_id", "opp"))
            req = urllib.request.Request(url, headers={"user-agent": "naadat-result-post/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                f.write(r.read())
            return Image.open(dest).convert("RGBA"), "logo"
        except Exception as exc:  # noqa: BLE001  fall back to a badge, but say so
            print("warning: could not download logo %s: %s; using a lettered badge" % (url, exc),
                  file=sys.stderr)
    return monogram(team["name"], colors), "badge"


def abbrev(name):
    words = [w for w in name.replace("-", " ").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


def monogram(name, colors):
    """Lettered badge for an opponent with no logo: cream plate, slanted letters."""
    s = 600
    im = Image.new("RGBA", (s, s), rgb(colors["cream"]))
    d = ImageDraw.Draw(im)
    ink = (34, 38, 36, 255)
    d.polygon(chamfer(34, 34, s - 34, s - 34, 70), outline=ink)
    d.line(chamfer(34, 34, s - 34, s - 34, 70) + [(34, 104)], fill=ink, width=10)
    letters = abbrev(name)
    f = fit_font(d, letters, anton, 300, s - 170)
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


def logo_tile(img, logo, kind, box, colors, cut=None, shadow=True):
    """Logo on a chamfered square tile. box = (x, y, size)."""
    x, y, s = box
    cut = int(s * 0.13) if cut is None else cut
    pts = chamfer(0, 0, s - 1, s - 1, cut)
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).polygon([(px + x + 8, py + y + 16) for px, py in pts], fill=(0, 0, 0, 140))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)))
    tile = tile_image(logo, kind, s)
    img.paste(tile, (int(x), int(y)), poly_mask((s, s), pts))


def small_logo(img, logo, kind, x, y, s):
    tile = tile_image(logo, kind, s)
    img.paste(tile, (int(x), int(y)), poly_mask((s, s), chamfer(0, 0, s - 1, s - 1, max(4, s // 6))))


# ---------------------------------------------------------------- data helpers

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def date_label(iso):
    import datetime as dt
    d = dt.date(*[int(p) for p in iso[:10].split("-")])
    return "%s %d.%d.%d" % (DOW[d.weekday()], d.day, d.month, d.year)


def finish_badge(game):
    return {"OT": "OT", "SO": "SO"}.get(game.get("finish"), "")


def scorer_parts(rows):
    parts = []
    for r in rows or []:
        if r.get("goals", 0) <= 0:
            continue
        parts.append((r["name"].get("last") or r["name"].get("full"), r["goals"]))
    return parts


def us_team(game):
    return game["home"] if game["us_side"] == "home" else game["away"]


def side_shots(game):
    shots = game.get("shots")
    if not shots:
        return None
    if game["us_side"] == "home":
        return shots["us"], shots["them"]
    return shots["them"], shots["us"]


def period_label(lbl):
    return {"1": "1ST", "2": "2ND", "3": "3RD"}.get(str(lbl), str(lbl).upper())


# ---------------------------------------------------------------- pieces

def header(img, title, game, colors, top, size, right_top=None):
    """Slanted giant title (+OT/SO tag) on the left, date and competition right."""
    d = ImageDraw.Draw(img)
    df = barlow(44, "SemiBold")
    date_txt = date_label(game["date"])
    comp = (game.get("competition") or "").upper()
    cf = fit_font(d, comp, lambda s: barlow(s, "ExtraBoldItalic"), 36, 330, tracking=3) if comp else None
    rcol = max(text_w(d, date_txt, df, 2), text_w(d, comp, cf, 3) if comp else 0)
    badge = finish_badge(game)
    avail = R - rcol - 44 - L

    def measure(sz):
        f = anton(sz)
        _, t, _, b = ink_box(f, title)
        cap = b - t
        tw = text_w(d, title, f, 6) + SLANT * cap
        bw = 0
        if badge:
            bw = text_w(d, badge, anton(int(cap * 0.42)), 3) + 44 + 26 + SLANT * cap * 0.56
        return f, cap, tw, tw + bw

    f, cap, tw, total = measure(size)
    while total > avail and size > 60:
        size -= 4
        f, cap, tw, total = measure(size)
    yb = top + cap
    slant_text(img, (L, yb), title, f, rgb(colors["cream"]), 6, "ls", shadow=(150, 14, 6, 10))
    x = L + tw + 26
    if badge:
        bf = anton(int(cap * 0.42))
        bw = text_w(d, badge, bf, 3) + 44
        bh = cap * 0.56
        by = yb - bh
        poly(img, para(x, by, x + bw, yb), rgb(colors["orange"]), shadow=(120, 8, 4, 6))
        slant_text(img, (x + bw / 2 + SLANT * bh / 2, baseline_for_center(bf, badge, by + bh / 2)),
                   badge, bf, rgb(colors["green_dark"]), 3, "ms")
    # orange rule with chevrons (motion cue)
    ry = yb + 26
    poly(img, para(L, ry, L + 230, ry + 10), rgb(colors["orange"]))
    chevrons(img, L + 262, ry + 5, 5, 22, rgb(colors["orange"], 220), step=17, thick=5)
    d = ImageDraw.Draw(img)
    rt = top + 8 if right_top is None else right_top
    draw_text(d, (R, rt + 36), date_txt, df, rgb(colors["cream"]), 2, "rs")
    if comp:
        draw_text(d, (R, rt + 86), comp, cf, rgb(colors["orange"]), 3, "rs")
    return ry + 10


def score_text_color(goals, other, colors):
    return rgb(colors["green_dark"]) if goals > other else rgb(colors["cream"], 150)


def scorebug_h(img, game, colors, cfg, cache_dir, y0, hgt=250, names=True):
    """Hero scorebug: [logo][score] / [score][logo] across x 50..1030."""
    home, away = game["home"], game["away"]
    lh, kh = logo_image(home, cache_dir, colors, cfg)
    la, ka = logo_image(away, cache_dir, colors, cfg)
    y1 = y0 + hgt
    pad = 12
    s = hgt - 2 * pad
    # backing plate
    poly(img, chamfer(L, y0, R, y1, 34), (4, 16, 9, 235), shadow=(170, 22, 0, 18))
    # thin orange top edge
    poly(img, [(L + 34, y0), (R, y0), (R, y0 + 5), (L + 29, y0 + 5)], rgb(colors["orange"], 255))
    lx, rx = L + pad, R - pad - s
    logo_tile(img, lh, kh, (lx, y0 + pad, s), colors, shadow=False)
    logo_tile(img, la, ka, (rx, y0 + pad, s), colors, shadow=False)
    gap = 18
    mid = (L + R) / 2
    sx0, sx1 = lx + s + gap, rx - gap
    ky0, ky1 = y0 + pad, y1 - pad
    sk = 0.17
    shh = sk * (ky1 - ky0)
    cells = [
        (home["goals"], away["goals"], cell(sx0, ky0, mid + shh / 2 - gap / 2, ky1, sk)),
        (away["goals"], home["goals"], cell(mid - shh / 2 + gap / 2, ky0, sx1, ky1, sk)),
    ]
    for goals, other, pts in cells:
        win = goals > other
        fill = rgb(colors["orange"]) if win else (255, 255, 255, 16)
        poly(img, pts, fill)
        if win:
            # sheen on the winning cell
            sheen = Image.new("RGBA", img.size, (255, 255, 255, 0))
            ImageDraw.Draw(sheen).rectangle((0, ky0, img.width, ky0 + (ky1 - ky0) * 0.42), fill=(255, 255, 255, 26))
            sheen.putalpha(ImageChops.multiply(sheen.getchannel("A"), poly_mask(img.size, pts)))
            img.alpha_composite(sheen)
        cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        txt = str(goals)
        f = fit_font(ImageDraw.Draw(img), txt, anton, int(hgt * 0.78), (sx1 - sx0) / 2 - 70)
        slant_text(img, (cx, baseline_for_center(f, txt, (ky0 + ky1) / 2)), txt, f,
                   score_text_color(goals, other, colors), 0, "ms", k=0.12)
    if not names:
        return y1
    d = ImageDraw.Draw(img)
    ny = y1 + 64
    for team, other, x, anc in ((home, away, L + 8, "ls"), (away, home, R - 8, "rs")):
        label = team["name"].upper()
        nf = fit_font(d, label, lambda z: barlow(z, "ExtraBoldItalic"), 60, 400, tracking=2)
        col = rgb(colors["cream"]) if team["goals"] > other["goals"] else rgb(colors["cream"], 175)
        draw_text(d, (x, ny), label, nf, col, 2, anc)
    tf = barlow(24, "Bold")
    draw_text(d, (L + 8, ny + 34), "HOME", tf, rgb(colors["orange"], 220), 5, "ls")
    draw_text(d, (R - 8, ny + 34), "AWAY", tf, rgb(colors["orange"], 220), 5, "rs")
    return ny + 34


def scorebug_v(img, game, colors, cfg, cache_dir, y0, row_h=232, gap=18):
    """Story scorebug: two stacked team rows, [logo][name][score]."""
    rows = []
    for team, other, tag in ((game["home"], game["away"], "HOME"), (game["away"], game["home"], "AWAY")):
        rows.append((team, other, tag))
    y = y0
    for team, other, tag in rows:
        win = team["goals"] > other["goals"]
        y1 = y + row_h
        poly(img, chamfer(L, y, R, y1, 30), (4, 16, 9, 235), shadow=(160, 20, 0, 16))
        if win:
            poly(img, [(L + 30, y), (R, y), (R, y + 5), (L + 25, y + 5)], rgb(colors["orange"]))
        pad = 12
        s = row_h - 2 * pad
        lg, kind = logo_image(team, cache_dir, colors, cfg)
        logo_tile(img, lg, kind, (L + pad, y + pad, s), colors, shadow=False)
        # score cell, slanted left edge
        cw = 270
        pts = cell(R - pad - cw, y + pad, R - pad, y1 - pad, 0.17)
        poly(img, pts, rgb(colors["orange"]) if win else (255, 255, 255, 16))
        txt = str(team["goals"])
        f = fit_font(ImageDraw.Draw(img), txt, anton, int(row_h * 0.74), cw - 70)
        cx = R - pad - cw / 2 + 10
        slant_text(img, (cx, baseline_for_center(f, txt, y + row_h / 2)), txt, f,
                   score_text_color(team["goals"], other["goals"], colors), 0, "ms", k=0.12)
        d = ImageDraw.Draw(img)
        nx = L + pad + s + 34
        maxw = (R - pad - cw - 30) - nx
        label = team["name"].upper()
        nf = fit_font(d, label, lambda z: barlow(z, "ExtraBoldItalic"), 84, maxw, tracking=2)
        col = rgb(colors["cream"]) if win else rgb(colors["cream"], 180)
        tf = barlow(26, "Bold")
        _, nt, _, nb = ink_box(nf, label)
        _, tt, _, tb = ink_box(tf, tag)
        block = (tb - tt) + 16 + (nb - nt)
        top0 = y + row_h / 2 - block / 2
        draw_text(d, (nx + 2, top0 - tt), tag, tf, rgb(colors["orange"], 220), 6, "ls")
        draw_text(d, (nx, top0 + (tb - tt) + 16 - nt), label, nf, col, 2, "ls")
        y = y1 + gap
    return y - gap


def box_score(img, game, colors, cfg, cache_dir, x0, x1, y0, row_h=64, head_h=48):
    """Broadcast box score: per-period goals, total and shots, one row per team."""
    periods = game.get("periods") or []
    shots = side_shots(game)
    if not periods and not shots:
        return y0
    d = ImageDraw.Draw(img)
    cols = [(period_label(p["label"]), "p", i) for i, p in enumerate(periods)]
    cols.append(("T", "t", None))
    if shots:
        cols.append(("SHOTS", "s", None))
    widths = []
    for lab, kind, _ in cols:
        widths.append({"p": 96, "t": 104, "s": 132}[kind])
    total_w = sum(widths)
    y1 = y0 + head_h + 2 * row_h
    poly(img, chamfer(x0, y0, x1, y1, 22), (4, 16, 9, 225), shadow=(150, 18, 0, 14))
    # header band
    poly(img, chamfer(x0, y0, x1, y0 + head_h, 22, "tl"), (255, 255, 255, 14))
    d = ImageDraw.Draw(img)
    hf = barlow(28, "Bold")
    draw_text(d, (x0 + 30, y0 + head_h / 2 + 10), "BOX SCORE", hf, rgb(colors["orange"]), 5)
    cx = x1 - total_w - 26
    col_x = []
    for (lab, kind, _), wdt in zip(cols, widths):
        col_x.append((cx, cx + wdt))
        draw_text(d, (cx + wdt / 2, y0 + head_h / 2 + 10), lab, hf,
                  rgb(colors["orange"]) if kind == "t" else rgb(colors["cream"], 170), 3, "ms")
        cx += wdt
    # total column highlight
    for (lab, kind, _), (a, b) in zip(cols, col_x):
        if kind == "t":
            poly(img, [(a + 6, y0 + head_h), (b - 6, y0 + head_h), (b - 6, y1 - 8), (a + 6, y1 - 8)],
                 rgb(colors["orange"], 34))
    d = ImageDraw.Draw(img)
    sides = (("home", game["home"], game["away"]), ("away", game["away"], game["home"]))
    for r, (side, team, other) in enumerate(sides):
        ry = y0 + head_h + r * row_h
        if r == 1:
            d.line([(x0 + 24, ry), (x1 - 24, ry)], fill=(255, 255, 255, 30), width=2)
        lg, kind = logo_image(team, cache_dir, colors, cfg)
        ls = row_h - 20
        small_logo(img, lg, kind, x0 + 26, ry + 10, ls)
        d = ImageDraw.Draw(img)
        win = team["goals"] > other["goals"]
        nmax = col_x[0][0] - (x0 + 26 + ls + 20) - 16
        label = team["name"].upper()
        nf = fit_font(d, label, lambda z: barlow(z, "Bold"), 40, nmax, tracking=2)
        cy = ry + row_h / 2
        bl = baseline_for_center(barlow(40, "Bold"), "NA", cy)
        draw_text(d, (x0 + 26 + ls + 20, bl), label, nf,
                  rgb(colors["cream"]) if win else rgb(colors["cream"], 190), 2)
        for (lab, kind, i), (a, b) in zip(cols, col_x):
            if kind == "p":
                v = str(periods[i][side])
                f = barlow(42, "SemiBold")
                col = rgb(colors["cream"], 230)
            elif kind == "t":
                v = str(team["goals"])
                f = barlow(46, "ExtraBold")
                col = rgb(colors["orange"]) if win else rgb(colors["cream"])
            else:
                v = str(shots[0] if side == "home" else shots[1])
                f = barlow(42, "SemiBold")
                col = rgb(colors["cream"], 230)
            draw_text(d, ((a + b) / 2, baseline_for_center(f, "0", cy)), v, f, col, 0, "ms")
    return y1


def scorers_panel(img, game, colors, cfg, x0, x1, y0, max_lines=3, size=46, min_h=0):
    """Näädät goal scorers ('Kuosmanen 3 · Ahola 2'); goalie line when no goals."""
    parts = scorer_parts(game.get("scorers_us"))
    us_goals = us_team(game)["goals"]
    gk = [g for g in game.get("goalies") or [] if g.get("is_us") and g.get("saves") is not None]
    if not parts and (us_goals or not gk):
        return y0  # unknown (hand-entered game without scorers): leave it out rather than guess
    d = ImageDraw.Draw(img)
    us_name = cfg["display_name"].upper()
    label = "%s GOALS" % us_name if parts else "%s SAVES" % us_name
    tx0 = x0 + 30
    tx1 = x1 - 30
    sep = "  ·  "
    if parts:
        tokens = ["%s %d" % (n, c) if c > 1 else n for n, c in parts]
        while size >= 30:
            f = barlow(size, "SemiBold")
            lines = wrap_parts(d, tokens, f, tx1 - tx0, sep)
            if len(lines) <= max_lines:
                break
            size -= 2
        lines = lines[:max_lines]
    else:
        f = barlow(size, "SemiBold")
        lines = [["%s %d" % (g["name"]["last"], g["saves"]) for g in gk]]
    tab_h = 50
    line_h = int(size * 1.22)
    body_top = y0 + tab_h + 26
    y1 = max(body_top + line_h * len(lines) + 14, y0 + min_h)
    poly(img, chamfer(x0, y0 + tab_h / 2, x1, y1, 22, "br"), (4, 16, 9, 215), shadow=(140, 18, 0, 14))
    # label tab
    lf = barlow(30, "ExtraBoldItalic")
    lw = text_w(d, label, lf, 4) + 62
    poly(img, para(x0, y0, x0 + lw, y0 + tab_h), rgb(colors["orange"]), shadow=(110, 8, 3, 6))
    d = ImageDraw.Draw(img)
    draw_text(d, (x0 + 24, baseline_for_center(lf, label, y0 + tab_h / 2)), label, lf,
              rgb(colors["green_dark"]), 4)
    chevrons(img, x0 + lw + 30, y0 + tab_h / 2 + 2, 3, 20, rgb(colors["orange"], 200), step=15, thick=4)
    d = ImageDraw.Draw(img)
    y = body_top + size * 0.82
    dot = rgb(colors["orange"])
    for line in lines:
        x = tx0
        for i, tok in enumerate(line):
            if i:
                draw_text(d, (x, y), sep, f, dot)
                x += d.textlength(sep, font=f)
            # number in orange, name in cream
            head, _, tail = tok.rpartition(" ")
            if tail.isdigit() and head:
                draw_text(d, (x, y), head + " ", f, rgb(colors["cream"]))
                x += d.textlength(head + " ", font=f)
                fb = barlow(size, "ExtraBold")
                draw_text(d, (x, y), tail, fb, dot)
                x += d.textlength(tail, font=fb)
            else:
                draw_text(d, (x, y), tok, f, rgb(colors["cream"]))
                x += d.textlength(tok, font=f)
        y += line_h
    return y1


def footer(img, game, colors, cfg, y, rule=True):
    d = ImageDraw.Draw(img)
    bits = [game.get("arena") or ""]
    if game.get("attendance"):
        bits.append("ATTENDANCE %s" % game["attendance"])
    left = "  ·  ".join(b for b in bits if b).upper()
    handle = cfg["instagram"].upper()
    hf = barlow(32, "ExtraBoldItalic")
    hw = text_w(d, handle, hf, 3)
    if rule:
        d.line([(L, y - 44), (R, y - 44)], fill=(255, 255, 255, 34), width=2)
        poly(img, para(L, y - 47, L + 120, y - 41), rgb(colors["orange"]))
        d = ImageDraw.Draw(img)
    if left:
        lf = fit_font(d, left, lambda s: barlow(s, "SemiBold"), 30, R - L - hw - 90, tracking=3)
        draw_text(d, (L, y), left, lf, rgb(colors["cream"], 200), 3)
    draw_text(d, (R, y), handle, hf, rgb(colors["orange"]), 3, "rs")
    chevrons(img, R - hw - 54, y - 11, 2, 18, rgb(colors["orange"], 230), step=13, thick=4, fade=False)


# ---------------------------------------------------------------- slides

def result_slide(game, cfg, cache_dir, photo=None, height=H, offset=0):
    """Slide 1. height=H: 4:5 feed. height=STORY_H: dedicated 9:16 story layout."""
    if height > H:
        return _result_story(game, cfg, cache_dir, photo)
    colors = cfg["colors"]
    seed = int(game.get("game_id") or 0)
    img = background(W, H, colors, seed, photo, hero=(0.40,))
    hb = header(img, "FINAL", game, colors, top=74, size=212)
    content_end = 1218  # footer rule at 1254, baseline 1298
    has_box = bool(game.get("periods") or game.get("shots"))
    scratch = img.copy()
    bug_h = scorebug_h(scratch, game, colors, cfg, cache_dir, 0, 250)
    box_h = box_score(scratch, game, colors, cfg, cache_dir, L, R, 0) if has_box else 0
    sc_h = scorers_panel(scratch, game, colors, cfg, L, R, 0, max_lines=2, size=46)
    blocks = [b for b in (bug_h, box_h, sc_h) if b]
    gap = min(80, (content_end - hb - sum(blocks)) / len(blocks))
    # the first gap (header -> scorebug) is a little larger: it separates title from data
    y0 = hb + gap * 1.15
    add_speed(img, colors, seed, y0 + 10, y0 + 240)
    y = scorebug_h(img, game, colors, cfg, cache_dir, y0, 250)
    if has_box:
        y = box_score(img, game, colors, cfg, cache_dir, L, R, y + gap)
    if sc_h:
        scorers_panel(img, game, colors, cfg, L, R, y + gap, max_lines=2, size=46)
    footer(img, game, colors, cfg, 1298)
    return img


def _result_story(game, cfg, cache_dir, photo=None):
    colors = cfg["colors"]
    seed = int(game.get("game_id") or 0)
    img = background(W, STORY_H, colors, seed, photo, hero=(0.40,))
    # top 250 px stays decorative (IG header): brand chevrons only
    chevrons(img, L + 10, 180, 7, 40, rgb(colors["orange"], 120), step=28, thick=7)
    hb = header(img, "FINAL", game, colors, top=286, size=250, right_top=286)
    content_end = 1534  # footer rule sits at 1568, footer baseline 1612
    has_box = bool(game.get("periods") or game.get("shots"))
    # measure the lower blocks on a scratch copy, then distribute the free height
    scratch = img.copy()
    box_h = (box_score(scratch, game, colors, cfg, cache_dir, L, R, 0, row_h=72, head_h=54) if has_box else 0)
    sc_h = scorers_panel(scratch, game, colors, cfg, L, R, 0, max_lines=3, size=50)
    blocks = [b for b in (box_h, sc_h) if b]
    row_h, rgap = 236, 20
    while True:
        bug_h = 2 * row_h + rgap
        free = content_end - hb - bug_h - sum(blocks)
        gap = free / (len(blocks) + 1)
        if gap >= 44 or row_h <= 180:
            break
        row_h -= 4
    gap = min(gap, 96)
    add_speed(img, colors, seed, hb + gap - 20, hb + gap + bug_h + 20, count=30)
    y = scorebug_v(img, game, colors, cfg, cache_dir, hb + gap, row_h=row_h, gap=rgap)
    if has_box:
        y = box_score(img, game, colors, cfg, cache_dir, L, R, y + gap, row_h=72, head_h=54)
    if sc_h:
        scorers_panel(img, game, colors, cfg, L, R, y + gap, max_lines=3, size=50)
    footer(img, game, colors, cfg, 1612)
    # bottom 300 px: decorative only
    chevrons(img, R - 200, 1760, 6, 34, rgb(colors["orange"], 70), step=26, thick=6, fade=True)
    return img


def _log_rows(goals):
    rows, cur = [], None
    for g in goals:
        if g["period"] != cur:
            cur = g["period"]
            rows.append(("hdr", cur, False))
        rows.append(("goal", g, False))
    return rows


def _split_two(rows):
    """Split log rows into two columns, repeating the period header if needed."""
    best = None
    for i in range(1, len(rows)):
        left, right = rows[:i], rows[i:]
        if right[0][0] == "hdr" and left[-1][0] == "hdr":
            continue
        extra = []
        if right[0][0] == "goal":
            per = right[0][1]["period"]
            extra = [("hdr", per, True)]
        r = extra + right
        score = max(len(left), len(r))
        if best is None or score < best[0] or (score == best[0] and len(left) >= len(r) and best[3]):
            best = (score, left, r, len(left) < len(r))
    return best[1], best[2]


PERIOD_NAMES = {1: "1ST PERIOD", 2: "2ND PERIOD", 3: "3RD PERIOD", 4: "OVERTIME"}


def _draw_log_col(img, rows, colors, cfg, them_label, x0, x1, top, row_h, size, compact):
    d = ImageDraw.Draw(img)
    f_time = barlow(int(size * 0.86), "SemiBold")
    f_score = barlow(size, "ExtraBold")
    f_name = barlow(size, "Bold")
    f_ast = barlow(int(size * 0.8), "SemiBold")
    f_tag = barlow(int(size * 0.72), "Bold")
    y = top
    for kind, item, cont in rows:
        if kind == "hdr":
            label = PERIOD_NAMES.get(item, "PERIOD %s" % item)
            if cont:
                label += " (CONT.)"
            hf = barlow(int(size * 0.78), "ExtraBoldItalic")
            th = row_h * 0.72
            ty = y + row_h * 0.22
            tw = text_w(d, label, hf, 3) + 40
            poly(img, para(x0, ty, x0 + tw, ty + th), rgb(colors["orange"]))
            d = ImageDraw.Draw(img)
            draw_text(d, (x0 + 16, baseline_for_center(hf, label, ty + th / 2)), label, hf,
                      rgb(colors["green_dark"]), 3)
            d.line([(x0 + tw + 14, ty + th / 2), (x1, ty + th / 2)], fill=(255, 255, 255, 34), width=2)
            y += row_h
            continue
        g = item
        ours = g["team"] == "us"
        cy = y + row_h / 2
        if ours:
            poly(img, [(x0, y + 3), (x1, y + 3), (x1, y + row_h - 3), (x0, y + row_h - 3)],
                 rgb(colors["orange"], 26))
            poly(img, para(x0, y + 3, x0 + 8, y + row_h - 3), rgb(colors["orange"]))
            d = ImageDraw.Draw(img)
        col = rgb(colors["cream"]) if ours else rgb(colors["cream"], 150)
        bl = baseline_for_center(f_name, "Ag", cy) + size * 0.05
        x = x0 + 22
        draw_text(d, (x, bl), g["time"], f_time, rgb(colors["cream"], 200 if ours else 130))
        x += text_w(d, "00:00", f_time) + size * 0.55
        sc = g["score_after"].replace("-", "–")
        draw_text(d, (x, bl), sc, f_score, rgb(colors["orange"]) if ours else col)
        x += text_w(d, "00–00", f_score) + size * 0.5
        right_lbl = (cfg["display_name"].upper() if ours else them_label)
        tag = g.get("type") or ""
        right = tag if compact else ("%s  %s" % (right_lbl, tag)).strip()
        rw = text_w(d, right, f_tag, 2) if right else 0
        if right:
            draw_text(d, (x1 - 8, bl), right, f_tag,
                      rgb(colors["orange"]) if ours else rgb(colors["cream"], 120), 2, "rs")
        avail = x1 - 8 - (rw + 16 if right else 0) - x
        who = ellipsize(d, g["scorer"]["short"], f_name, avail)
        draw_text(d, (x, bl), who, f_name, col)
        x += d.textlength(who, font=f_name)
        assists = ", ".join(a["last"] for a in g.get("assists") or [])
        if assists:
            avail = x1 - 8 - (rw + 16 if right else 0) - x
            txt = ellipsize(d, "  (%s)" % assists, f_ast, avail)
            if len(txt) > 4:
                draw_text(d, (x, bl), txt, f_ast, rgb(colors["cream"], 150 if ours else 100))
        y += row_h
    return y


def summary_slide(game, cfg, cache_dir):
    colors = cfg["colors"]
    seed = int(game.get("game_id") or 0) + 1
    img = background(W, H, colors, seed, None, hero=(0.22,))
    add_speed(img, colors, seed, 300, 420, count=16)
    header(img, "SUMMARY", game, colors, top=70, size=150)
    # compact scorebug
    home, away = game["home"], game["away"]
    by0, bh = 272, 118
    poly(img, chamfer(L, by0, R, by0 + bh, 22), (4, 16, 9, 235), shadow=(160, 18, 0, 14))
    poly(img, [(L + 22, by0), (R, by0), (R, by0 + 4), (L + 18, by0 + 4)], rgb(colors["orange"]))
    pad = 10
    s = bh - 2 * pad
    lh, kh = logo_image(home, cache_dir, colors, cfg)
    la, ka = logo_image(away, cache_dir, colors, cfg)
    logo_tile(img, lh, kh, (L + pad, by0 + pad, s), colors, shadow=False)
    logo_tile(img, la, ka, (R - pad - s, by0 + pad, s), colors, shadow=False)
    mid = (L + R) / 2
    cw = 132
    shs = 0.17 * (bh - 2 * pad)
    for goals, other, pts in (
        (home["goals"], away["goals"], cell(mid - 7 - cw, by0 + pad, mid + shs / 2 - 7, by0 + bh - pad, 0.17)),
        (away["goals"], home["goals"], cell(mid - shs / 2 + 7, by0 + pad, mid + 7 + cw, by0 + bh - pad, 0.17)),
    ):
        poly(img, pts, rgb(colors["orange"]) if goals > other else (255, 255, 255, 16))
        cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        f = anton(76)
        slant_text(img, (cx, baseline_for_center(f, str(goals), by0 + bh / 2)), str(goals), f,
                   score_text_color(goals, other, colors), 0, "ms", k=0.12)
    d = ImageDraw.Draw(img)
    for team, other, x, anc in ((home, away, L + pad + s + 24, "ls"), (away, home, R - pad - s - 24, "rs")):
        label = team["name"].upper()
        maxw = (mid - 7 - cw - 24) - (L + pad + s + 24)
        nf = fit_font(d, label, lambda z: barlow(z, "ExtraBoldItalic"), 50, maxw, tracking=2)
        col = rgb(colors["cream"]) if team["goals"] > other["goals"] else rgb(colors["cream"], 180)
        draw_text(d, (x, baseline_for_center(barlow(50, "ExtraBoldItalic"), "NA", by0 + bh / 2)),
                  label, nf, col, 2, anc)

    # goal log
    goals = [g for g in game.get("goals") or [] if g.get("period") != 5]
    them = away if game["us_side"] == "home" else home
    them_label = them["name"].upper()
    us_label = cfg["display_name"].upper()
    top, bottom = 430, 1122
    avail = bottom - top
    note = None
    if not goals:
        draw_text(d, (L, top + 80), "No goals for %s." % cfg["display_name"], barlow(46, "SemiBold"),
                  rgb(colors["cream"]))
    else:
        rows = _log_rows(goals)
        one_h = avail / len(rows)
        if one_h >= 40:
            row_h = min(54, one_h)
            size = int(min(36, row_h * 0.68))
            _draw_log_col(img, rows, colors, cfg, them_label, L, R, top, row_h, size, False)
        else:
            left, right = _split_two(rows)
            row_h = avail / max(len(left), len(right))
            if row_h < 34:
                goals = [g for g in goals if g["team"] == "us"]
                note = "%s GOALS" % us_label
                rows = _log_rows(goals)
                if rows and avail / len(rows) >= 40:
                    row_h = min(54, avail / len(rows))
                    size = int(min(36, row_h * 0.68))
                    _draw_log_col(img, rows, colors, cfg, them_label, L, R, top, row_h, size, False)
                    left = right = None
                else:
                    left, right = _split_two(rows)
                    row_h = min(54, avail / max(len(left), len(right)))
            if left is not None:
                row_h = min(54, row_h)
                size = int(min(32, row_h * 0.70))
                gx = 24
                colw = (R - L - gx) / 2
                _draw_log_col(img, left, colors, cfg, them_label, L, L + colw, top, row_h, size, True)
                _draw_log_col(img, right, colors, cfg, them_label, L + colw + gx, R, top, row_h, size, True)
                d = ImageDraw.Draw(img)
                d.line([(L + colw + gx / 2, top + 10), (L + colw + gx / 2, top + row_h * max(len(left), len(right)) - 6)],
                       fill=(255, 255, 255, 26), width=2)
            if not note and left is not None:
                # legend for the compact two-column log
                pass
    d = ImageDraw.Draw(img)
    if note:
        nf = barlow(24, "Bold")
        draw_text(d, (R, by0 + bh + 34), note + " SHOWN", nf, rgb(colors["orange"]), 4, "rs")

    # stats strip
    sy = 1146
    stats = []
    sh = side_shots(game)
    if sh:
        stats.append(("SHOTS", "%d – %d" % sh))
    gk = [g for g in game.get("goalies") or [] if g.get("is_us") and g.get("saves") is not None]
    if gk:
        stats.append(("%s SAVES" % us_label, "  ·  ".join("%s %d" % (g["name"]["last"], g["saves"]) for g in gk)))
    ogk = [g for g in game.get("goalies") or [] if not g.get("is_us") and g.get("saves") is not None]
    if ogk:
        stats.append(("%s SAVES" % them_label, "  ·  ".join("%s %d" % (g["name"]["last"], g["saves"]) for g in ogk)))
    if stats:
        poly(img, chamfer(L, sy, R, sy + 82, 18, "tl br"), (4, 16, 9, 215))
        d = ImageDraw.Draw(img)
        col_w = (R - L) / len(stats)
        for i, (k, v) in enumerate(stats):
            x = L + 26 + i * col_w
            if i:
                d.line([(L + i * col_w, sy + 16), (L + i * col_w, sy + 66)], fill=(255, 255, 255, 30), width=2)
            kf = fit_font(d, k, lambda z: barlow(z, "Bold"), 22, col_w - 40, tracking=4)
            draw_text(d, (x, sy + 30), k, kf, rgb(colors["orange"]), 4)
            vf = fit_font(d, v, lambda z: barlow(z, "SemiBold"), 36, col_w - 40)
            draw_text(d, (x, sy + 68), v, vf, rgb(colors["cream"]), 1)
    footer(img, game, colors, cfg, 1298)
    return img


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--photo", help="optional real photo (jpg/png) used as the background")
    ap.add_argument("--no-summary", action="store_true", help="skip slide 2")
    ap.add_argument("--no-story", action="store_true")
    ap.add_argument("--png", action="store_true", help="also write lossless PNG copies")
    args = ap.parse_args(argv)

    with open(os.path.join(ASSETS, "brand", "team.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    with open(args.game_json, encoding="utf-8") as f:
        game = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, ".cache")

    written = []
    feed = result_slide(game, cfg, cache, args.photo)
    written.append(save_clean(feed, os.path.join(args.out, "result_feed.jpg")))
    if args.png:
        written.append(save_clean(feed, os.path.join(args.out, "result_feed.png")))
    has_log = any(g.get("period") != 5 for g in game.get("goals") or [])
    total = game["home"]["goals"] + game["away"]["goals"]
    if not args.no_summary and not has_log and total:
        print("note: no goal log in game.json, so slide 2 (summary) was skipped")
    elif not args.no_summary:
        summ = summary_slide(game, cfg, cache)
        written.append(save_clean(summ, os.path.join(args.out, "summary_feed.jpg")))
    if not args.no_story:
        story = result_slide(game, cfg, cache, args.photo, height=STORY_H)
        written.append(save_clean(story, os.path.join(args.out, "result_story.jpg")))
    for p in written:
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
