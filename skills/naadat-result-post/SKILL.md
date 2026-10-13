---
name: naadat-result-post
description: Näädät (@naadathockey) post-game Instagram post: result card, summary slide, story, caption. Use for a result post, final score graphic or game recap, incl. hand-entered FCAA games.
---

# Näädät result post

Produces a ready-to-upload Instagram post for the latest (or a chosen) Näädät game:

- `result_feed.jpg` 1080x1350, slide 1: FINAL, both logos, score, periods, shots, Näädät goal scorers
- `summary_feed.jpg` 1080x1350, slide 2: goal log with assists, shots, goalie saves (only when `game.json` has a goal log)
- `result_story.jpg` 1080x1920, story version of slide 1
- an English caption with hashtags, written by you from the same data

## No AI label

Every image is drawn by `scripts/render_card.py` from real data, bundled fonts and real logo files, and saved without metadata. Without `--photo`, the files carry **none of the signals Meta reads for its "AI info" label**: no C2PA Content Credentials, no IPTC/XMP `DigitalSourceType`, no AI watermarks. With `--photo`, the result is only as clean as the photo: a watermark that an AI edit left in the pixels survives re-encoding. Instagram can also add a label at upload time (its own AI edits, or the self-disclosure toggle), which step 5 covers.

Keep every input real:

- **Graphics** come only from the render script. Image-generation tools stay out of this workflow, also when the user asks for "a nicer picture": the way to a nicer picture is a real photo through `--photo`.
- **Photos**: only unedited camera photos. Before using a user's photo, ask once whether it was edited with an AI tool (Magic Editor or Magic Eraser, Photoshop Generative Fill, Galaxy AI, iPhone Clean Up, Instagram Restyle and similar). If it was, ask for the unedited original or post without a photo.
- **Logos**: real files from the user, the club or the results service. Never draw or generate one.

The scripts back this up: `render_card.py` and `clean_image.py clean` refuse a photo or logo whose file declares AI generation, and exit with code 4. Tell the user to send the unedited original (for a logo, the club's own file), then rerun.

## Running the scripts

Scripts live next to this file; `SKILL_DIR` below means the folder containing this SKILL.md. Run them from a working folder (outputs land there, never in `SKILL_DIR`). They need Python 3.8+ and Pillow 8.2+ (`python -m pip install --user -U pillow` if `import PIL` fails or the script reports an old Pillow). Where `python` is not found (stock macOS, some Linux), use `python3`.

## Steps

### 1. Get the game data

Only Näädät's III-divisioona games are on the results service, tulospalvelu.leijonat.fi. **FCAA games and any other game not on the service take the manual path below**; they never show up in `--list`, so do not search for them there.

**III-divisioona games** come from the results service:

```bash
python SKILL_DIR/scripts/fetch_game.py --out game.json                          # latest finished game
python SKILL_DIR/scripts/fetch_game.py --list                                   # schedule, newest first, Näädät score first
python SKILL_DIR/scripts/fetch_game.py --game-url "<game link>" --out game.json  # a specific game
python SKILL_DIR/scripts/fetch_game.py --date 2026-10-18 --out game.json
```

Pick the variant from what the user said ("last night's game", a date, an opponent: use `--list` to find the game id, then `--game-id <id>`). The script refuses games that are not final yet; tell the user and stop rather than passing `--allow-unfinished`, unless they explicitly want a provisional post.

**Blocked network** (error "Could not reach ..."): the sandbox cannot reach the results service.

- With a known game id (`--game-id`, or `--game-url` with the game link the user copied from tulospalvelu.leijonat.fi), the script reads the game report directly, so a blocked schedule alone does not stop it. For "the latest game" there is no id offline: ask the user for the game link.
- If the report itself is blocked too, the error prints the report URL for that game. Ask the user to open that URL in a browser, save the page as a file and upload it (saving works better than copy-paste on a phone; pasted text is the fallback). The script needs the complete raw JSON: a web-fetch tool that summarises pages is not enough, while one that returns the raw response (curl and similar) is fine. Only the game-report URL works as input; the schedule URLs do not. Save it as `report.json` and run:

  ```bash
  python SKILL_DIR/scripts/fetch_game.py --report-json report.json --out game.json
  ```

To make fetching work directly next time, the sandbox needs `tulospalvelu.leijonat.fi` on its allowed domains (`references/setup.md`).

**Manual path** (FCAA and any game the results service lacks):

1. Ask once, in one message, for whatever the user has not already given: final score, opponent name, date, home or away, whether it went to overtime or a shootout, Näädät scorers if known (goals, and assists if known), and the opponent's logo file.
2. Decide home or away: home when the user says so or the rink is in `home_arenas` in `assets/brand/team.json`, away when the user says so or names the opponent's rink. If it is still unclear, ask.
3. Copy `references/manual_game_template.json` to `game.json` in the working folder and fill it in. Its `_help` block explains each field. Record only what the user said: unknown assists are `null`, a player known only by last name gets only `last` filled, and unknown lists stay empty. FCAA scorer lists are often partial; record the scorers the user knows, and the graphic marks the list as partial. Save an uploaded opponent logo next to `game.json` and set `logo_file` to its file name.
4. Check it:

   ```bash
   python SKILL_DIR/scripts/fetch_game.py --validate game.json
   ```

   `--validate` exits 0 when it finds nothing to fix and 3 when it prints a WARNING (result that does not match the score, scorer goals above the score, a missing logo file, leftover template values and similar). On exit 3, fix `game.json` from what the user said, or ask the user what is right, and run it again.

**Done when** both of these hold, on either path:

- the script's last summary line (fetch: `Wrote game.json: ...`; manual: `Checked game.json: ...`) shows the right date, teams, score and result, with Näädät first, and on the manual path `--validate` ended with no WARNING (exit 0);
- you have read that line to the user and the user has confirmed it is the right game. Wait for the reply before step 2. Pass on any note the fetch printed (for example that the scorer list may be incomplete).

### 2. Render the graphics

```bash
python SKILL_DIR/scripts/render_card.py game.json --out post/
python SKILL_DIR/scripts/render_card.py game.json --out post/ --photo game_photo.jpg   # with a real photo
```

If the user has a real, unedited photo from the game (see "No AI label"), use it: it makes the biggest visual difference. Opponent logos come from `logo_file` (relative to the folder of `game.json`), then `assets/logos/<association_id>.png`, then the results service, then a lettered badge. Exit code 4 means a photo or logo declares AI generation: ask for the unedited original (see "No AI label").

**Done when** the script has printed three `wrote` lines (or two `wrote` lines plus `note: no goal log ... skipped` for a game without a goal log, 0-0 games included), every `note: no logo for <team>, using a lettered badge` and `warning: logo_file ... not found` has been passed to the user so they can send a logo, and you have looked at `post/result_feed.jpg` yourself, checking that every name, number and logo is right and nothing is clipped.

### 3. Verify the files are clean

```bash
python SKILL_DIR/scripts/clean_image.py check post/*.jpg
```

**Done when** every line says `CLEAN`. Any other line means the file must not be uploaded: a `byte pattern` hit is a provenance marker found by the raw-byte backstop, and `N bytes after EOI` / `after IEND` means data was hidden after the image. Re-render, and if it persists, stop and tell the user.

`clean_image.py clean in.jpg out.jpg` is for one case only: the user wants to add one of their own unedited camera photos as an extra slide. Confirm it has no AI edits (see "No AI label"), run `clean` on it and hand over `out.jpg`. `clean` removes metadata, not pixel watermarks, so it never turns an AI-edited image into a usable one; it refuses files that declare AI generation (exit 4).

### 4. Write the caption

Read `references/caption-guide.md` and write the caption in English from `game.json` and the user's own words only.

**Done when** the caption states the correct score (Näädät first) and opponent, every fact in it can be traced to `game.json` or the user's own words, and it ends with the sign-off and hashtags from the guide.

### 5. Hand over

Give the user the files in slide order (`result_feed.jpg`, then `summary_feed.jpg` if it was written; `result_story.jpg` separately) and the caption in a copyable block. The user uploads in the Instagram app; post nothing yourself. Tell them to post the files as they are: no Restyle, Meta AI or AI filters in the Instagram editor, and leave the AI label toggle under Advanced settings off. Offer one round of tweaks (shorter caption, a different photo, leave out slide 2).

## Adapting

Team name, colours, logo, hashtags, sign-off, home rinks and the results-service ids live in `assets/brand/team.json`; its `_help` block explains each key. A sharper team logo goes in `assets/brand/` (square PNG, 1000 px or more) with its filename set in `logo`. A new opponent logo can be dropped in `assets/logos/` named by the club's association id (the number in the club's logo link on tulospalvelu.leijonat.fi).
