#!/usr/bin/env python3
"""Build dist/naadat-result-post.zip for uploading to Claude (Settings > Capabilities > Skills)
or any other assistant. The zip holds the skill folder at its root, as Claude expects."""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "naadat-result-post")
OUT = os.path.join(ROOT, "dist", "naadat-result-post.zip")
SKIP_DIRS = {"__pycache__", ".cache"}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(SKILL):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            z.write(full, os.path.join("naadat-result-post", os.path.relpath(full, SKILL)))
print("wrote", OUT, os.path.getsize(OUT), "bytes")
