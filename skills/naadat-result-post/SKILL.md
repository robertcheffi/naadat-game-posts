---
name: naadat-result-post
description: Makes the Näädät hockey post-game Instagram post (result graphic, summary slide, story and English caption) from the official result. Use when asked for a result post, final score graphic, game recap or post-match post for Näädät / Hernesaaren Näädät / @naadathockey, including FCAA games entered by hand.
---

# Näädät result post

Produces a ready-to-upload Instagram post for the latest (or a chosen) Näädät game:

- `result_feed.jpg` 1080x1350, slide 1: FINAL, both logos, score, periods, shots, Näädät goal scorers
- `summary_feed.jpg` 1080x1350, slide 2: goal log with assists, shots, goalie saves
- `result_story.jpg` 1080x1920, story version of slide 1
- an English caption with hashtags, written by you from the same data

Every image is drawn by `scripts/render_card.py` from real data, bundled fonts and real logo files, and saved metadata-free. **The pipeline is fully deterministic: graphics come only from the render script, so the upload carries no AI provenance and Instagram shows no "AI info" label.** Image-generation tools stay out of this workflow even when the user asks for "a nicer picture": the way to a nicer picture is a real photo through `--photo`.

Scripts live next to this file. Run them from a working folder and pass paths; `SKILL_DIR` below means the folder containing this SKILL.md. They need Python 3.8+ and Pillow (`pip install pillow` if `import PIL` fails).

## Steps

### 1. Get the game data

III-divisioona games come from the official results service (tulospalvelu.leijonat.fi):

```bash
python SKILL_DIR/scripts/fetch_game.py --out game.json            # latest finished game
python SKILL_DIR/scripts/fetch_game.py --list                     # schedule, newest first
python SKILL_DIR/scripts/fetch_game.py --game-url "<game link>"   # a specific game
python SKILL_DIR/scripts/fetch_game.py --date 2026-10-18 --out game.json
```

Pick the variant from what the user said ("last night's game", a date, an opponent: use `--list` to find the id). The script refuses games that are not final yet; tell the user and stop rather than passing `--allow-unfinished`, unless they explicitly want a provisional post.

**Blocked network** (error "Could not reach ..."): the sandbox has no internet access. Fetch this one URL with your web-fetch tool, or ask the user to open it in a browser and paste or upload the result, save it as `report.json`, then run `fetch_game.py --report-json report.json --season <SEASON> --out game.json`:

`https://tulospalvelu.leijonat.fi/gamereport/getgamereportdata?gameid=<GAME_ID>&season=<SEASON>`

The game id and season are in the game's link on the results site (season 2026-27 is `2027`). To make the script work directly next time, the sandbox needs `tulospalvelu.leijonat.fi` on its allowed domains (see `references/setup.md`).

**FCAA and other games not on the results service**: copy `references/manual_game_template.json` to `game.json` and fill it in from what the user gives you (score, opponent, scorers, date, arena). Ask once for anything the graphic needs that is missing: final score, opponent name, date, and an opponent logo file. Never invent scorers or stats; leave those lists empty instead. Save an uploaded opponent logo next to `game.json` and point `away.logo_file` / `home.logo_file` at it.

Done when `game.json` exists and the script's summary line (date, teams, score, result) matches what the user asked for. Read that line to the user. If it prints a WARNING about the goal log, pass it on.

### 2. Render the graphics

```bash
python SKILL_DIR/scripts/render_card.py game.json --out post/
python SKILL_DIR/scripts/render_card.py game.json --out post/ --photo game_photo.jpg   # with a real photo
```

If the user has a photo from the game, use it: it makes the biggest visual difference. Opponent logos come from `assets/logos/<association_id>.png`, then the results service, then a lettered badge; mention the badge so the user can supply a logo.

Done when the script has printed three `wrote` lines and you have looked at `post/result_feed.jpg` yourself, checking that every name, number and logo is right and nothing is clipped.

### 3. Verify the files are clean

```bash
python SKILL_DIR/scripts/clean_image.py check post/*.jpg
```

Done when every line says `CLEAN`. If the user wants to upload some other image with the post (their own photo, an edited version), run `clean_image.py clean in.jpg out.jpg` on it first.

### 4. Write the caption

Read `references/caption-guide.md` and write the caption in English from `game.json` only.

Done when the caption states the correct score and opponent, every fact in it can be traced to `game.json` or the user's own words, and it ends with the sign-off and hashtags from the guide.

### 5. Hand over

Give the user the files (slide order: `result_feed.jpg`, then `summary_feed.jpg`; story separately) and the caption in a copyable block. Upload is done by the user in the Instagram app; post nothing yourself. Offer one round of tweaks (shorter caption, a different photo, leave out slide 2).

## Adapting

Team name, colours, logo, hashtags, sign-off and the results-service ids live in `assets/brand/team.json`. A sharper team logo goes in `assets/brand/` (square PNG, 1000 px or more) with its filename set in `logo`. A new opponent logo can be dropped in `assets/logos/` named by its association id.
