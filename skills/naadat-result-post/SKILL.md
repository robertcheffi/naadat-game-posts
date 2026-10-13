---
name: naadat-result-post
description: Näädät (@naadathockey) Instagram posts incl. FCAA: pre-game game-day post (face-off time, both logos) and post-game result post (final score card, summary, caption). Use for matchday or result posts.
---

# Näädät game posts

Two kinds of Instagram post for a Näädät game, every image in feed post size (1080x1350, 4:5), each with an English caption you write from the same data:

| branch | when | files |
|---|---|---|
| **Pre-game** | game day or before: "game day post", "matchday post", "post for tonight's game" | `pregame_feed.jpg`: competition, date, face-off time, rink, both logos |
| **Post-game** | after the final horn: "result post", "final score", "recap" | `result_feed.jpg` (slide 1: FINAL, logos, score, periods, shots, Näädät scorers) and `summary_feed.jpg` (slide 2: goal log, shots, goalie saves; only when there is a goal log) |

Pick the branch from the user's words. A game still to be played is pre-game; a finished game is post-game. If the request fits neither clearly ("post for the KHK game"), ask which one. Story versions (1080x1920) are made only when the user asks for a story: add `--story` to the render command.

## No AI label

Every image is drawn by `scripts/render_card.py` from real data, bundled fonts and real logo files, and saved without metadata. Without `--photo`, the files carry **none of the signals Meta reads for its "AI info" label**: no C2PA Content Credentials, no IPTC/XMP `DigitalSourceType`, no AI watermarks. With `--photo`, the result is only as clean as the photo: a watermark that an AI edit left in the pixels survives re-encoding. Instagram can also add a label at upload time (its own AI edits, or the self-disclosure toggle), which step 5 covers.

Keep every input real:

- **Graphics** come only from the render script. Image-generation tools stay out of this workflow, also when the user asks for "a nicer picture": the way to a nicer picture is a real photo through `--photo`.
- **Photos**: only unedited camera photos. Before using a user's photo, ask once whether it was edited with an AI tool (Magic Editor or Magic Eraser, Photoshop Generative Fill, Galaxy AI, iPhone Clean Up, Instagram Restyle and similar). If it was, ask for the unedited original or post without a photo. `python SKILL_DIR/scripts/clean_image.py ai photo.jpg` checks a photo up front (exit 4 = it declares AI generation).
- **Logos**: real files from the user, the club or the results service. Never draw or generate one.

The scripts back this up: `render_card.py` and `clean_image.py clean` refuse a photo or logo whose file declares AI generation, and exit with code 4. Tell the user to send the unedited original (for a logo, the club's own file), then rerun.

## Running the scripts

Scripts live next to this file; `SKILL_DIR` below means the folder containing this SKILL.md. Run them from a working folder (outputs land there, never in `SKILL_DIR`). They need Python 3.8+ and Pillow 8.2+ (`python -m pip install --user -U pillow` if `import PIL` fails or the script reports an old Pillow). Where `python` is not found (stock macOS, some Linux), use `python3`.

Only Näädät's III-divisioona games are on the results service, tulospalvelu.leijonat.fi. **FCAA games and any other game not on the service take the manual path** of each branch; they never show up in `--list`, so do not search for them there.

**Home or away** on a manual path: home when the user says so or the rink name starts with an entry of `home_arenas` in `assets/brand/team.json` (it ships `["Kaarela"]`, so Kaarela 1 and Kaarela 2 are home, FCAA games included); away when the user says so or names another team's rink. If it is still unclear, ask.

**Blocked network** (error "Could not reach ..."): the sandbox cannot reach the results service.

- With a known game id (`--game-id`, or `--game-url` with the game link the user copied from tulospalvelu.leijonat.fi), the script reads the game report directly, so a blocked schedule alone does not stop it. For "the latest game" or "the next game" there is no id offline: ask the user for the game link.
- If the report itself is blocked too, the error prints the report URL for that game. Ask the user to open that URL in a browser and send the page as a file. On iPhone: long-press the link, choose Download Linked File, then attach the file from the Files app; or open the page, select all the text, copy and paste it in the chat. The script needs the complete raw JSON: a web-fetch tool that summarises pages is not enough, while one that returns the raw response (curl and similar) is fine. Only the game-report URL works as input; the schedule URLs do not. Save it as `report.json` and run:

  ```bash
  python SKILL_DIR/scripts/fetch_game.py --report-json report.json --out game.json                # post-game
  python SKILL_DIR/scripts/fetch_game.py --pregame --report-json report.json --out pregame.json   # pre-game
  ```

To make fetching work directly next time, the sandbox needs `tulospalvelu.leijonat.fi` on its allowed domains (`references/setup.md`).

## Pre-game steps

### 1. Get the game

**III-divisioona** games come from the results service:

```bash
python SKILL_DIR/scripts/fetch_game.py --next --out pregame.json                                # next game not started yet (later today counts)
python SKILL_DIR/scripts/fetch_game.py --pregame --game-url "<game link>" --out pregame.json    # a specific game
python SKILL_DIR/scripts/fetch_game.py --pregame --date 2026-10-18 --out pregame.json
```

Exit 1 with "has already been played" means the game is over: tell the user and offer the result post (post-game branch). A `WARNING` that the results service names no rink means: ask the user for the rink and fill in `arena` in `pregame.json`.

**Manual path** (FCAA and any game the results service lacks):

1. Ask once, in one message, for whatever the user has not already given: opponent, date, face-off time, rink, home or away (see "Home or away"), and the opponent's logo file.
2. Copy `references/manual_pregame_template.json` to `pregame.json` in the working folder and fill it in from what the user said; its `_help` block explains each field. Save an uploaded opponent logo next to `pregame.json` and set `logo_file` to its file name, or remove `logo_file` when there is none.
3. Check it with `python SKILL_DIR/scripts/fetch_game.py --validate pregame.json`. Exit codes work as in post-game step 1: exit 3 lists what to fix (a missing time or rink, a date in the past, leftover template values and similar); a missing `logo_file` alone still exits 0.

**Done when** the script's summary line (fetch: `Wrote pregame.json: ...`; manual: `Checked pregame.json: ...`, for example `SUN 18.10.2026 21:10 Näädät vs HC Ludwig (home) at Kaarela 1`) shows the right date, face-off time, teams and rink, on the manual path `--validate` exited 0, you have read that line to the user, and the user has confirmed it. Wait for the reply before step 2.

### 2-5. Render, check, caption, hand over

Run the shared steps below with `pregame.json`. The render writes `pregame_feed.jpg`; the caption follows the Pre-game section of `references/caption-guide.md`.

## Post-game steps

### 1. Get the game data

**III-divisioona** games come from the results service:

```bash
python SKILL_DIR/scripts/fetch_game.py --out game.json                          # latest finished game
python SKILL_DIR/scripts/fetch_game.py --list                                   # schedule, newest first, Näädät score first
python SKILL_DIR/scripts/fetch_game.py --game-url "<game link>" --out game.json  # a specific game
python SKILL_DIR/scripts/fetch_game.py --date 2026-10-18 --out game.json
```

Pick the variant from what the user said ("last night's game", a date, an opponent: use `--list` to find the game id, then `--game-id <id>`).

**Exit 2** means the game has started but is not final yet. Tell the user and stop; never retry silently. When a newer game is still running, the message suggests `--game-id <previous>` for the last final game: use it only if the user says they want the post for that earlier game. There is no provisional result post: `render_card.py` refuses a game that is not final.

**Manual path** (FCAA and any game the results service lacks):

1. Ask once, in one message, for whatever the user has not already given: final score, opponent name, date, home or away (see "Home or away"), whether it went to overtime or a shootout, Näädät scorers if known (goals, and assists if known), and the opponent's logo file.
2. Copy `references/manual_game_template.json` to `game.json` in the working folder and fill it in; its `_help` block explains each field. Record only what the user said: unknown assists are `null`, a player known only by last name gets only `last` filled, and unknown lists stay empty. FCAA scorer lists are often partial; record the scorers the user knows, and the graphic marks the list as partial. Save an uploaded opponent logo next to `game.json` and set `logo_file` to its file name.
3. Check it:

   ```bash
   python SKILL_DIR/scripts/fetch_game.py --validate game.json
   ```

   - **Exit 0**: nothing to fix. It may still print two things to pass on: a `note:` that the scorer list is partial (fine, the graphic marks it), and a `WARNING` that `logo_file` was not found, the one WARNING that keeps exit 0. For that one, ask the user for the logo or remove `logo_file`.
   - **Exit 3**: a WARNING to fix (result that does not match the score, scorers adding up to more goals than Näädät scored, leftover template values and similar). Fix `game.json` from what the user said, or ask the user what is right, and run it again.

**Done when** both of these hold, on either path:

- the script's last summary line (fetch: `Wrote game.json: ...`; manual: `Checked game.json: ...`) shows the right date, teams, score and result, with Näädät first, and on the manual path `--validate` exited 0;
- you have read that line to the user and the user has confirmed it is the right game. Wait for the reply before step 2. Pass on any note the fetch printed (for example that the scorer list may be incomplete).

### 2-5. Render, check, caption, hand over

Run the shared steps below with `game.json`. The render writes `result_feed.jpg` and `summary_feed.jpg`; the caption follows the Post-game section of `references/caption-guide.md`.

## Shared steps

`DATA` below is `pregame.json` or `game.json`, from step 1 of your branch.

### 2. Render the graphics

```bash
python SKILL_DIR/scripts/render_card.py DATA --out post/
python SKILL_DIR/scripts/render_card.py DATA --out post/ --photo game_photo.jpg   # with a real photo
python SKILL_DIR/scripts/render_card.py DATA --out post/ --story                  # also pregame_story.jpg / result_story.jpg (1080x1920), only on request
```

If the user has a real, unedited photo (see "No AI label"), use it: it makes the biggest visual difference. The photo appears on `pregame_feed.jpg` or on slide 1 (and the story); slide 2 stays photo-free. Opponent logos come from `logo_file` (relative to the folder of `DATA`), then `assets/logos/<association_id>.png`, then the results service, then a lettered badge. Exit code 4 means a photo or logo declares AI generation: ask for the unedited original (see "No AI label").

**Done when** the script has printed a `wrote` line for every file of your branch (post-game without a goal log: `result_feed.jpg` plus `note: no goal log ... skipped`, 0-0 games included), every `note: no logo for <team>, using a lettered badge` and `warning: logo_file ... not found` has been passed to the user so they can send a logo, and you have looked at the first image yourself (`pregame_feed.jpg` or `result_feed.jpg`), checking that every name, number, time and logo is right and nothing is clipped.

### 3. Verify the files are clean

```bash
python SKILL_DIR/scripts/clean_image.py check post/*.jpg
```

**Done when** every line says `CLEAN`. Any other line means the file must not be uploaded: `AI-declared:` is a file that declares AI generation, a `byte pattern` hit is a provenance marker found by the raw-byte backstop, and `N bytes after EOI` / `after IEND` means data was hidden after the image. Re-render, and if it persists, stop and tell the user.

`clean_image.py clean in.jpg out.jpg` is for one case only: the user wants to add one of their own unedited camera photos as an extra slide. Confirm it has no AI edits (see "No AI label"), run `clean` on it and hand over `out.jpg`. `clean` removes metadata, not pixel watermarks, so it never turns an AI-edited image into a usable one; it refuses files that declare AI generation (exit 4).

### 4. Write the caption

Read `references/caption-guide.md` (the shared ending plus your branch's section) and write the caption in English from `DATA` and the user's own words only.

**Done when** the caption states the correct facts for the branch (pre-game: opponent, face-off time and rink; post-game: score with Näädät first, and opponent), every fact in it can be traced to `DATA` or the user's own words, and it ends with the sign-off and hashtags from the guide.

### 5. Hand over

Give the user the files in slide order (pre-game: `pregame_feed.jpg`; post-game: `result_feed.jpg`, then `summary_feed.jpg` if it was written; a story file, if asked for, separately) and the caption in a copyable block. The user uploads in the Instagram app; post nothing yourself. Tell them to post the files as they are: no Restyle, Meta AI or AI filters in the Instagram editor, and leave the AI label toggle under Advanced settings off. Offer one round of tweaks (shorter caption, a different photo, leave out slide 2).

## Adapting

Team name, colours, logo, hashtags, sign-off, home rinks and the results-service ids live in `assets/brand/team.json`; its `_help` block explains each key. A sharper team logo goes in `assets/brand/` (square PNG, 1000 px or more) with its filename set in `logo`. A new opponent logo can be dropped in `assets/logos/` named by the club's association id (the number in the club's logo link on tulospalvelu.leijonat.fi). On claude.ai the skill folder is read-only: these changes are for whoever maintains the skill, who rebuilds and re-uploads the zip (`README.md`).
