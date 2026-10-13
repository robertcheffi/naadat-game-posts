# Agents

This repo ships one agent skill: `skills/naadat-result-post/`. To make a Näädät pre-game or post-game Instagram post, read `skills/naadat-result-post/SKILL.md` and follow it.

When changing the skill:

- Keep it runnable with Python 3.8+ and Pillow 8.2+ plus the standard library, nothing else.
- Every image comes from `scripts/render_card.py` drawing real data, bundled fonts and real logo files. No generative AI anywhere in the pipeline, so a render without `--photo` carries none of the signals Meta reads for its AI label (C2PA, IPTC/XMP DigitalSourceType, AI watermarks). A `--photo` render is only as clean as the photo, because pixel watermarks survive re-encoding; inputs that declare AI generation are refused (exit 4).
- README.md promises only what SKILL.md does. A behaviour change updates both, and a visual change refreshes the README example renders in `docs/`.
- `game.json` (post-game) and `pregame.json` (pre-game) are the contracts between `fetch_game.py`, hand-entered games (`references/manual_game_template.json`, `references/manual_pregame_template.json`) and `render_card.py`; a key change updates all three plus SKILL.md.
- Every default output is feed post size (1080x1350); story renders stay opt-in behind `--story`.
- Keep the SKILL.md `description` at 200 characters or fewer (the claude.ai upload limit).
- Rebuild the upload zip with `python tools/build_zip.py` and commit `dist/naadat-result-post.zip`: non-technical users download it straight from the repo.
