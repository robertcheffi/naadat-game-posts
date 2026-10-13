#!/usr/bin/env python3
"""Render Instagram result graphics for a game.json produced by fetch_game.py
(or written by hand from references/manual_game_template.json).

Outputs (all metadata-free, see clean_image.py):
  <out>/result_feed.jpg      1080x1350  feed post, slide 1
  <out>/summary_feed.jpg     1080x1350  feed post, slide 2 (goal log, shots, goalies)
  <out>/result_story.jpg     1080x1920  story

Everything is drawn with Pillow from real data, fonts and logo files. No
generative AI image model is involved anywhere.

  python render_card.py game.json --out out/
  python render_card.py game.json --out out/ --photo team_photo.jpg
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
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


# ---------------------------------------------------------------- basics

def rgb(hex_color: str, alpha: int = 255):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONTS, name), size)


def anton(size):
    return font("Anton-Regular.ttf", size)


def barlow(size, weight="Bold"):
    return font(f"BarlowCondensed-{weight}.ttf", size)


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


def wrap(draw, words, f, max_w, sep=" "):
    lines, cur = [], ""
    for wd in words:
        trial = wd if not cur else cur + sep + wd
        if draw.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- background

def background(w, h, colors, seed, photo=None):
    base = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
    if photo:
        ph = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
        ph = ImageOps.fit(ph, (w, h), Image.LANCZOS, centering=(0.5, 0.4))
        ph = ImageOps.grayscale(ph).convert("RGB")
        tint = Image.new("RGB", (w, h), rgb(colors["green"])[:3])
        ph = Image.blend(ph, tint, 0.35)
        base.paste(ph.convert("RGBA"), (0, 0))
        # readability veil: lighter at the top, solid behind the text block
        veil = Image.new("L", (1, h))
        for y in range(h):
            t = y / h
            veil.putpixel((0, y), int(255 * (0.45 + 0.5 * min(1, max(0, (t - 0.15) / 0.55)))))
        veil = veil.resize((w, h))
        dark = Image.new("RGBA", (w, h), rgb(colors["green_dark"]))
        base = Image.composite(dark, base, veil)
    else:
        # radial glow from the score area
        glow = Image.new("L", (w, h), 0)
        gd = ImageDraw.Draw(glow)
        cx, cy, r = w * 0.5, h * 0.45, max(w, h) * 0.75
        for i in range(60, 0, -1):
            rr = r * i / 60
            gd.ellipse((cx - rr, cy - rr * 0.8, cx + rr, cy + rr * 0.8), fill=int(255 * (1 - i / 60) ** 1.4))
        glow = glow.filter(ImageFilter.GaussianBlur(40))
        mid = Image.new("RGBA", (w, h), rgb(colors["green_mid"]))
        base = Image.composite(mid, base, glow)

    # diagonal pinstripes
    stripes = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stripes)
    for x in range(-h, w, 26):
        sd.line([(x, h), (x + h, 0)], fill=(255, 255, 255, 9), width=2)
    base = Image.alpha_composite(base, stripes)

    # film grain, deterministic per game
    noise = Image.effect_noise((w, h), 28).convert("L")
    noise = noise.point(lambda v: max(0, min(255, v - 128 + 128)))
    grain = Image.merge("RGBA", (noise, noise, noise, Image.new("L", (w, h), 14)))
    base = Image.alpha_composite(base, grain)

    # vignette
    vig = Image.new("L", (w, h), 0)
    vd = ImageDraw.Draw(vig)
    vd.rectangle((0, 0, w, h), fill=255)
    vd.ellipse((-w * 0.25, -h * 0.15, w * 1.25, h * 1.15), fill=0)
    vig = vig.filter(ImageFilter.GaussianBlur(120))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 120))
    base = Image.composite(shade, base, vig)
    return base


def ice_lines(img, colors, y0, y1):
    """Faint rink markings behind the score: centre red line and face-off circle."""
    w = img.width
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cy = (y0 + y1) / 2
    d.ellipse((w / 2 - 230, cy - 230, w / 2 + 230, cy + 230), outline=(255, 255, 255, 16), width=6)
    return Image.alpha_composite(img, layer)


# ---------------------------------------------------------------- logos

def logo_image(team: dict, cache_dir: str, colors: dict, cfg: dict):
    """Return (RGBA image, is_brand_tile)."""
    if team.get("is_us"):
        p = os.path.join(ASSETS, "brand", cfg["logo"])
        return Image.open(p).convert("RGBA"), True
    candidates = []
    if team.get("logo_file"):
        candidates.append(team["logo_file"])
    if team.get("association_id"):
        candidates.append(os.path.join(ASSETS, "logos", f"{team['association_id']}.png"))
    for c in candidates:
        if c and os.path.exists(c):
            return Image.open(c).convert("RGBA"), False
    url = team.get("logo_url")
    if url:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            dest = os.path.join(cache_dir, f"logo_{team.get('association_id', 'opp')}.png")
            req = urllib.request.Request(url, headers={"user-agent": "naadat-result-post/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                f.write(r.read())
            return Image.open(dest).convert("RGBA"), False
        except Exception as exc:  # noqa: BLE001  fall back to a monogram, but say so
            print(f"warning: could not download logo {url}: {exc}; using a monogram", file=sys.stderr)
    return monogram(team["name"], colors), False


def monogram(name: str, colors: dict):
    im = Image.new("RGBA", (400, 400), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    letters = "".join(w[0] for w in name.replace("-", " ").split()[:3]).upper() or "?"
    f = fit_font(d, letters, anton, 220, 300)
    d.text((200, 205), letters, font=f, fill=rgb(colors["green"]), anchor="mm")
    return im


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m


def logo_tile(img, logo, is_brand, box, colors):
    """Logo on a rounded tile with a soft drop shadow. box = (x, y, size)."""
    x, y, s = box
    radius = int(s * 0.12)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x + 6, y + 18, x + s + 6, y + s + 18), radius=radius, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(shadow)

    if is_brand:
        tile = ImageOps.fit(logo, (s, s), Image.LANCZOS)
    else:
        tile = Image.new("RGBA", (s, s), (255, 255, 255, 255))
        pad = int(s * 0.1)
        lg = logo.copy()
        lg.thumbnail((s - 2 * pad, s - 2 * pad), Image.LANCZOS)
        tile.alpha_composite(lg, ((s - lg.width) // 2, (s - lg.height) // 2))
    img.paste(tile, (x, y), rounded_mask((s, s), radius))
    ring = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle((x, y, x + s, y + s), radius=radius, outline=(255, 255, 255, 60), width=3)
    img.alpha_composite(ring)


# ---------------------------------------------------------------- pieces

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def date_label(iso: str) -> str:
    import datetime as dt
    d = dt.date.fromisoformat(iso)
    return f"{DOW[d.weekday()]} {d.day}.{d.month}.{d.year}"


def header_slab(img, title, badge, game, colors, top=88, size=150):
    d = ImageDraw.Draw(img)
    f = anton(size)
    tw = text_w(d, title, f, 4)
    x_end = 64 + tw + 70
    slab = [(-30, top), (x_end + 40, top), (x_end, top + 160), (-30, top + 160)]
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(px + 8, py + 12) for px, py in slab], fill=(0, 0, 0, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    d.polygon(slab, fill=rgb(colors["orange"]))
    draw_text(d, (64, top + 80 + size * 0.4), title, f, rgb(colors["green_dark"]), tracking=4)
    if badge:
        bf = barlow(44, "ExtraBold")
        bw = text_w(d, badge, bf, 2) + 36
        bx, by = x_end + 50, top + 92
        d.rounded_rectangle((bx, by, bx + bw, by + 56), radius=10, fill=rgb(colors["cream"]))
        draw_text(d, (bx + 18, by + 45), badge, bf, rgb(colors["green_dark"]), tracking=2)

    right = W - 64
    draw_text(d, (right, top + 62), date_label(game["date"]), barlow(42, "SemiBold"), rgb(colors["cream"]), 2, "rs")
    comp = (game.get("competition") or "").upper()
    if comp:
        cf = fit_font(d, comp, lambda s: barlow(s, "Bold"), 34, 360, tracking=3)
        draw_text(d, (right, top + 112), comp, cf, rgb(colors["orange"]), 3, "rs")


def finish_badge(game):
    return {"OT": "OT", "SO": "SO"}.get(game.get("finish"), "")


def scorer_line(rows):
    parts = []
    for r in rows:
        if r["goals"] <= 0:
            continue
        name = r["name"]["last"] or r["name"]["full"]
        parts.append(f"{name} {r['goals']}" if r["goals"] > 1 else name)
    return parts


def score_block(img, game, colors, cfg, cache_dir, top):
    d = ImageDraw.Draw(img)
    s = 280
    gap_mid = 300
    lx = (W - gap_mid) // 2 - s
    rx = (W + gap_mid) // 2
    home, away = game["home"], game["away"]
    img_h, brand_h = logo_image(home, cache_dir, colors, cfg)
    img_a, brand_a = logo_image(away, cache_dir, colors, cfg)
    logo_tile(img, img_h, brand_h, (lx, top, s), colors)
    logo_tile(img, img_a, brand_a, (rx, top, s), colors)
    d = ImageDraw.Draw(img)

    name_y = top + s + 66
    for team, x in ((home, lx), (away, rx)):
        label = team["name"].upper()
        nf = fit_font(d, label, lambda z: barlow(z, "Bold"), 50, s + 40, tracking=3)
        draw_text(d, (x + s / 2, name_y), label, nf, rgb(colors["cream"]), 3, "ms")

    hg, ag = home["goals"], away["goals"]
    sf = anton(300)
    num_y = name_y + 300
    for goals, other, x in ((hg, ag, lx), (ag, hg, rx)):
        txt = str(goals)
        f = fit_font(d, txt, anton, 300, s + 60)
        win = goals > other
        col = rgb(colors["white"]) if win or goals == other else rgb(colors["cream"], 120)
        if win:
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw_text(ImageDraw.Draw(glow), (x + s / 2, num_y), txt, f, rgb(colors["orange"], 150), 0, "ms")
            img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(26)))
            d = ImageDraw.Draw(img)
        draw_text(d, (x + s / 2, num_y), txt, f, col, 0, "ms")

    # centre column: period-by-period
    cx = W / 2
    periods = game.get("periods") or []
    if periods:
        row_h = 92 if len(periods) <= 3 else 74
        y = top + 40
        draw_text(d, (cx, y), "PERIODS", barlow(28, "Bold"), rgb(colors["orange"]), 5, "ms")
        d.line([(cx - 60, y + 18), (cx + 60, y + 18)], fill=rgb(colors["orange"], 160), width=2)
        y += 30
        for p in periods:
            y += row_h
            label = {"1": "1ST", "2": "2ND", "3": "3RD"}.get(p["label"], p["label"])
            draw_text(d, (cx, y - 48), label, barlow(24, "SemiBold"), rgb(colors["cream"], 140), 4, "ms")
            draw_text(d, (cx, y), f"{p['home']} – {p['away']}", barlow(48, "Bold"), rgb(colors["cream"]), 1, "ms")
    shots = game.get("shots")
    if shots:
        us_home = game["us_side"] == "home"
        hs, as_ = (shots["us"], shots["them"]) if us_home else (shots["them"], shots["us"])
        y = num_y - 40
        draw_text(d, (cx, y - 58), "SHOTS", barlow(28, "Bold"), rgb(colors["orange"]), 5, "ms")
        d.line([(cx - 60, y - 40), (cx + 60, y - 40)], fill=rgb(colors["orange"], 160), width=2)
        draw_text(d, (cx, y), f"{hs} – {as_}", barlow(48, "Bold"), rgb(colors["cream"]), 1, "ms")
    return num_y


def result_pill(img, game, colors, y):
    res = game["result"]
    if not res.endswith("W"):
        return y
    text = {"W": "WIN", "OTW": "OVERTIME WIN", "SOW": "SHOOTOUT WIN"}.get(res, "WIN")
    d = ImageDraw.Draw(img)
    f = barlow(40, "ExtraBold")
    tw = text_w(d, text, f, 5)
    x0 = (W - tw) / 2 - 30
    d.rounded_rectangle((x0, y, x0 + tw + 60, y + 60), radius=30, fill=rgb(colors["orange"]))
    draw_text(d, (W / 2, y + 46), text, f, rgb(colors["green_dark"]), 5, "ms")
    return y + 60


def scorers_block(img, game, colors, cfg, top, bottom):
    parts = scorer_line(game.get("scorers_us") or [])
    us_goals = (game["home"] if game["us_side"] == "home" else game["away"])["goals"]
    gk = next((g for g in game.get("goalies") or [] if g.get("is_us")), None)
    if not parts and (us_goals or not gk or gk.get("saves") is None):
        return  # scorers unknown (e.g. a hand-entered game): leave the band empty rather than guess
    panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((40, top, W - 40, bottom), radius=22, fill=(0, 0, 0, 85))
    pd.rounded_rectangle((40, top, 52, bottom), radius=6, fill=rgb(colors["orange"]))
    img.alpha_composite(panel)
    d = ImageDraw.Draw(img)
    us_name = cfg["display_name"].upper()
    x0, x1 = 84, W - 72
    draw_text(d, (x0, top + 52), f"{us_name} GOALS", barlow(30, "Bold"), rgb(colors["orange"]), 5)
    y = top + 112
    if parts:
        size = 48
        while size >= 30:
            f = barlow(size, "SemiBold")
            lines = wrap(d, parts, f, x1 - x0, sep="  ·  ")
            if (len(lines) - 1) * (size + 14) <= bottom - y - 24 and len(lines) <= 3:
                break
            size -= 2
        for line in lines[:3]:
            draw_text(d, (x0, y), line, f, rgb(colors["cream"]))
            y += size + 14
    else:
        line = f"{gk['name']['last']} made {gk['saves']} saves in net."
        draw_text(d, (x0, y), line, barlow(46, "SemiBold"), rgb(colors["cream"]))


def footer(img, game, colors, cfg, y):
    d = ImageDraw.Draw(img)
    bits = [game.get("arena") or ""]
    if game.get("attendance"):
        bits.append(f"ATTENDANCE {game['attendance']}")
    left = "  ·  ".join(b for b in bits if b).upper()
    draw_text(d, (64, y), left, barlow(30, "SemiBold"), rgb(colors["cream"], 190), 3)
    draw_text(d, (W - 64, y), cfg["instagram"].upper(), barlow(30, "Bold"), rgb(colors["orange"]), 3, "rs")


# ---------------------------------------------------------------- slides

def result_slide(game, cfg, cache_dir, photo=None, height=H, offset=0):
    colors = cfg["colors"]
    img = background(W, height, colors, game.get("game_id", 0), photo)
    img = ice_lines(img, colors, offset + 330, offset + 1000)
    header_slab(img, "FINAL", finish_badge(game), game, colors, top=offset + 88)
    num_y = score_block(img, game, colors, cfg, cache_dir, top=offset + 330)
    result_pill(img, game, colors, num_y + 24)
    scorers_block(img, game, colors, cfg, top=offset + 1062, bottom=offset + 1222)
    footer(img, game, colors, cfg, offset + 1300)
    return img


def summary_slide(game, cfg, cache_dir):
    colors = cfg["colors"]
    img = background(W, H, colors, game.get("game_id", 0) + 1)
    d = ImageDraw.Draw(img)
    header_slab(img, "SUMMARY", finish_badge(game), game, colors, size=124)
    home, away = game["home"], game["away"]
    d = ImageDraw.Draw(img)
    title = f"{home['name'].upper()}  {home['goals']} – {away['goals']}  {away['name'].upper()}"
    tf = fit_font(d, title, lambda s: barlow(s, "ExtraBold"), 64, W - 128 - 260, tracking=2)
    draw_text(d, (64, 330), title, tf, rgb(colors["cream"]), 2)

    goals = [g for g in game.get("goals") or [] if g.get("period") != 5]
    us_label, them_label = cfg["display_name"].upper(), None
    them = away if game["us_side"] == "home" else home
    them_label = them["name"].upper()

    top, bottom = 380, 1150
    only_us = False
    rows = len(goals) + len({g["period"] for g in goals})
    row_h = min(46, (bottom - top) / max(rows, 1))
    if row_h < 27:
        goals = [g for g in goals if g["team"] == "us"]
        only_us = True
        rows = len(goals) + len({g["period"] for g in goals})
        row_h = min(46, (bottom - top) / max(rows, 1))
    size = int(max(20, min(36, row_h * 0.78)))
    f_main = barlow(size, "SemiBold")
    f_bold = barlow(size, "Bold")
    f_small = barlow(int(size * 0.8), "SemiBold")

    y = top + row_h
    cur = None
    if not goals:
        draw_text(d, (64, y + 30), "No goals for " + cfg["display_name"] + ".", barlow(44, "SemiBold"), rgb(colors["cream"]))
    for g in goals:
        if g["period"] != cur:
            cur = g["period"]
            label = {1: "1ST PERIOD", 2: "2ND PERIOD", 3: "3RD PERIOD", 4: "OVERTIME"}.get(cur, f"PERIOD {cur}")
            y += row_h * 0.25
            draw_text(d, (64, y), label, barlow(int(size * 0.85), "Bold"), rgb(colors["orange"]), 4)
            y += row_h * 0.75
        ours = g["team"] == "us"
        col = rgb(colors["cream"]) if ours else rgb(colors["cream"], 140)
        if ours:
            d.rectangle((64, y - size * 0.78, 70, y + 4), fill=rgb(colors["orange"]))
        draw_text(d, (86, y), g["time"], f_main, col, 1)
        draw_text(d, (200, y), g["score_after"].replace("-", "–"), f_bold, col, 1)
        who = g["scorer"]["short"]
        assists = ", ".join(a["last"] for a in g["assists"])
        tag = f"  {g['type']}" if g.get("type") else ""
        x = 300
        draw_text(d, (x, y), who, f_bold, col, 1)
        x += text_w(d, who, f_bold, 1)
        if assists:
            draw_text(d, (x, y), f"  ({assists})", f_small, rgb(colors["cream"], 150 if ours else 110), 1)
        right = (tag.strip() if only_us else (us_label if ours else them_label) + tag)
        if right:
            draw_text(d, (W - 64, y), right, f_small, rgb(colors["orange"]) if ours else rgb(colors["cream"], 110), 2, "rs")
        y += row_h
    if only_us:
        draw_text(d, (W - 64, 330), f"{us_label} GOALS ONLY", barlow(26, "Bold"), rgb(colors["orange"]), 4, "rs")

    # stats strip
    sy = 1200
    d.line([(64, sy - 34), (W - 64, sy - 34)], fill=rgb(colors["cream"], 50), width=2)
    stats = []
    if game.get("shots"):
        stats.append(("SHOTS", f"{game['shots']['us']} – {game['shots']['them']}"))
    gk = [g for g in game.get("goalies") or [] if g.get("is_us") and g.get("saves") is not None]
    if gk:
        stats.append(("SAVES", " / ".join(f"{g['name']['last']} {g['saves']}" for g in gk)))
    if stats:
        col_w = (W - 128) / len(stats)
        for i, (k, v) in enumerate(stats):
            x = 64 + i * col_w
            draw_text(d, (x, sy), k, barlow(26, "Bold"), rgb(colors["orange"]), 4)
            vf = fit_font(d, v, lambda s: barlow(s, "SemiBold"), 40, col_w - 20)
            draw_text(d, (x, sy + 48), v, vf, rgb(colors["cream"]), 1)
    footer(img, game, colors, cfg, 1300)
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
        story = result_slide(game, cfg, cache, args.photo, height=STORY_H, offset=(STORY_H - H) // 2)
        written.append(save_clean(story, os.path.join(args.out, "result_story.jpg")))
    for p in written:
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
