#!/usr/bin/env python3
"""Build dist/naadat-result-post.zip for uploading to Claude (Customize > Skills > + > Create skill >
Upload a skill) or any other assistant. The zip holds the skill folder at its root, as Claude expects.
dist/ is committed so non-technical users can download the zip from the repo; rebuild after every
change to skills/. Skips __pycache__, .cache and compiled .pyc files."""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "naadat-result-post")
OUT = os.path.join(ROOT, "dist", "naadat-result-post.zip")
SKIP_DIRS = {"__pycache__", ".cache"}


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


check_description()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(SKILL):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, name)
            z.write(full, os.path.join("naadat-result-post", os.path.relpath(full, SKILL)))
print("wrote", OUT, os.path.getsize(OUT), "bytes")
