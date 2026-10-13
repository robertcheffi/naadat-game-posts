# Agents

This repo ships one agent skill: `skills/naadat-game-posts/`. To make a Näädät pre-game or post-game Instagram post, read `skills/naadat-game-posts/SKILL.md` and follow it.

When changing the skill:

- Keep it runnable with Python 3.8+ and Pillow 8.2+ plus the standard library, nothing else.
- Every image comes from `scripts/render_card.py` drawing real data, bundled fonts, real logo files and the bundled camera photos. No generative AI anywhere in the pipeline, so the renders carry none of the signals Meta reads for its AI label (C2PA, IPTC/XMP DigitalSourceType, AI watermarks); inputs that declare AI generation are refused (exit 4).
- Output is feed post size only (1080x1350): `pregame_feed.jpg`, `result_feed.jpg`, `summary_feed.jpg`. Home games render green, away games orange, from `us_side`.
- The photo library is `assets/photos/` plus `assets/photos/photos.json` (`file`, `credit`, `focus`, `jersey` per entry). The club holds usage rights to these photos for its posts. Library files are resized (long edge 1600 px) and saved metadata-free through `clean_image.save_clean`. A new photo also adds its photographer to the README photo credits.
- Photo rotation: `stable_key` is `game_id` when present, else the sum of the ISO date digits; the result post uses `stable_key % N`, the pre-game post `(stable_key + 1) % N`. `--photo-index K` overrides. The render prints `photo: <file> (credit: <credit>)` and writes `<out>/photo_credit.txt`, which the caption guide turns into the `📸 <credit>` line.
- Exit codes are shared by all scripts: 0 ok, 1 error, 2 not final or not played yet, 3 `--validate` warning to fix, 4 AI-declared photo or logo. SKILL.md documents them in one table.
- README.md promises only what SKILL.md does. A behaviour change updates both, and a visual change refreshes the README example renders in `docs/` (`example_result_home.jpg`, `example_result_away.jpg`, `example_pregame.jpg`, `example_summary.jpg`).
- `game.json` (post-game) and `pregame.json` (pre-game) are the contracts between the writers (`fetch_game.py` for III-divisioona, `fcaa.py` for FCAA, hand-entered games from `references/manual_game_template.json` and `references/manual_pregame_template.json`) and `render_card.py`; a key change updates every writer, the templates and SKILL.md. The templates carry literal placeholders (`YYYY-MM-DD`, `HH:MM`, `Opponent`) that `--validate` rejects until replaced.
- FCAA files use the same schemas with `source` `fcaa`, `competition` `FCAA`, `game_id` the FCAA game id string, and an optional per-team `logo_asset` (`fcaa/<slug>.png`, a path under `assets/logos/`) that `render_card.py` resolves before `association_id`. Näädät is `is_us` by the name `Näädät`. FCAA has no overtime: `finish` is `REG` or `SO`.
- fcaahockey.app (API and logos) sits behind a Vercel bot challenge that answers every non-browser client with 429. `fcaa.py` therefore parses data a browser obtained: raw API JSON saved by an assistant's browser tool, or the game page text a user copied from the app. Keep both inputs working; a change to the app's page layout breaks the page-text parser first.
- Keep the SKILL.md `description` at 200 characters or fewer (the claude.ai upload limit; `tools/build_zip.py` enforces it).
- Rebuild the upload zip with `python tools/build_zip.py` and commit `dist/naadat-game-posts.zip`: non-technical users download it straight from the repo.
