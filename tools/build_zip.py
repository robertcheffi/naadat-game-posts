#!/usr/bin/env python3
"""Build dist/naadat-game-posts.zip for uploading to Claude (Customize > Skills > + > Create skill >
Upload a skill) or any other assistant. The zip holds the skill folder at its root, as Claude expects.
dist/ is committed so non-technical users can download the zip from the repo; rebuild after every
change to skills/. Skips __pycache__, .cache and compiled .pyc files. Refuses to build when the
SKILL.md description is too long or the photo library and photos.json disagree."""
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "naadat-game-posts")
OUT = os.path.join(ROOT, "dist", "naadat-game-posts.zip")
PHOTOS = os.path.join(SKILL, "assets", "photos")
SKIP_DIRS = {"__pycache__", ".cache", ".ruff_cache", ".pytest_cache", ".mypy_cache"}
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def check_description():
    # claude.ai rejects an upload whose SKILL.md description is longer than 200 characters.
    with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    if not m:
        sys.exit("SKILL.md has no description line in its frontmatter")
    desc = m.group(1).strip()
    if len(desc) > 200:
        sys.exit("SKILL.md description is %d characters; claude.ai allows 200" % len(desc))


def check_photos():
    # render_card.py rotates through photos.json and the caption credits each photo, so every
    # listed file must exist with a credit, and no unlisted (uncredited) photo may ship.
    manifest = os.path.join(PHOTOS, "photos.json")
    if not os.path.isfile(manifest):
        sys.exit("assets/photos/photos.json is missing")
    with open(manifest, encoding="utf-8") as f:
        entries = json.load(f)
    if not isinstance(entries, list) or not entries:
        sys.exit("assets/photos/photos.json must be a non-empty list")
    listed = set()
    for e in entries:
        name = e.get("file") if isinstance(e, dict) else None
        if not name or not os.path.isfile(os.path.join(PHOTOS, name)):
            sys.exit("photos.json lists a missing file: %r" % (name,))
        if not str(e.get("credit") or "").strip():
            sys.exit("photos.json entry %s has no credit" % name)
        listed.add(name)
    unlisted = sorted(n for n in os.listdir(PHOTOS)
                      if n.lower().endswith(PHOTO_EXTS) and n not in listed)
    if unlisted:
        sys.exit("photos not in photos.json (no credit): %s" % ", ".join(unlisted))


check_description()
check_photos()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(SKILL):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, name)
            z.write(full, os.path.join("naadat-game-posts", os.path.relpath(full, SKILL)))
print("wrote", OUT, os.path.getsize(OUT), "bytes")
