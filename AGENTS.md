# Agents

This repo ships one agent skill: `skills/naadat-result-post/`. To make a Näädät post-game Instagram post, read `skills/naadat-result-post/SKILL.md` and follow it.

When changing the skill, keep it runnable with only Python 3.8+ and Pillow, keep every image produced by `scripts/render_card.py` (no image-generation models), and rebuild the upload zip with `python tools/build_zip.py`.
