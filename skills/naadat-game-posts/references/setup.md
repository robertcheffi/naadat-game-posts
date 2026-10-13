# Setup per app

The skill is one folder (`naadat-game-posts/`) with `SKILL.md` at its root. It needs Python 3.8+ with Pillow 8.2 or newer, and for automatic fetching of III-divisioona games, internet access to `tulospalvelu.leijonat.fi`. Without that access it still works: the assistant asks you for the game report (SKILL.md, "Blocked network").

FCAA games come from the FCAA stats app (fcaahockey.app), which lets only a real browser in: scripts and sandboxes get a "Vercel Security Checkpoint" page, so adding `fcaahockey.app` to the allowed domains does not help. An assistant with a browser tool (Claude in Chrome, or the browser built into the Claude desktop app) reads the app itself; any other assistant asks you to open the game in the app and paste the page text in the chat (SKILL.md, "FCAA games").

The commands below use `python`. On macOS and Linux, use `python3` where `python` is not found.

## Claude (claude.ai and the desktop app)

1. Settings > Capabilities: turn on **Code execution and file creation**.
2. Network access for code execution:
   - Individual plans (Pro, Max): on the same page, turn on **Allow network egress**.
   - Team and Enterprise: an owner opens Organization settings > Capabilities and picks **Allow network egress to package managers and specific domains**, then adds `tulospalvelu.leijonat.fi`, or picks **All domains**.
3. Customize > Skills > **+** > **Create skill** > **Upload a skill**, choose `naadat-game-posts.zip` (from the repo's `dist/` folder) and switch it on. On Team and Enterprise plans an owner first enables Skills under Organization settings > Plugins & skills (Policy tab).
4. In a new chat: "Make the result post for last night's Näädät game." or "Make the game day post for tonight."

## Claude Code

```bash
cp -r skills/naadat-game-posts ~/.claude/skills/
python -m pip install --user -U pillow
```

On Windows, copy the `skills\naadat-game-posts` folder in File Explorer into `%USERPROFILE%\.claude\skills\` (create the folder if it is missing), then run `python -m pip install --user -U pillow` in a terminal.

## Codex (CLI, IDE extension, app)

```bash
mkdir -p ~/.agents/skills
cp -r skills/naadat-game-posts ~/.agents/skills/
python -m pip install --user -U pillow
```

On Windows, copy the folder in File Explorer into `%USERPROFILE%\.agents\skills\` instead. A repo-level `.agents/skills/` folder works too. Older Codex builds read `~/.codex/skills/` instead.

Codex's default sandbox blocks the network. Either approve the fetch command when Codex asks, or allow network for the workspace in `~/.codex/config.toml` (`network_access` only applies in the `workspace-write` sandbox, so both lines are needed):

```toml
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
```

## Any other assistant (ChatGPT, Gemini, ...)

Upload the zip and say: "Unzip this, read naadat-game-posts/SKILL.md and follow it for last night's game." Sandboxes without internet use the "Blocked network" path in SKILL.md.

## Installing Pillow

`python -m pip install --user -U pillow` works on most systems. If pip refuses with `externally-managed-environment` (recent Debian, Ubuntu and Homebrew Pythons), use one of:

```bash
uv run --with pillow python SKILL_DIR/scripts/render_card.py ...   # no install, uv fetches Pillow per run
python3 -m venv ~/.venvs/naadat && ~/.venvs/naadat/bin/pip install pillow   # then run the scripts with ~/.venvs/naadat/bin/python
```

The distro package (`apt install python3-pil`) is too old on Debian 11 (Pillow 8.1); check with `python -c "import PIL; print(PIL.__version__)"`.

## Checking an install (for maintainers)

Run from any folder; `SKILL_DIR` is the installed `naadat-game-posts` folder. The output goes to a temporary folder, never into the skill folder (on claude.ai the skill folder is read-only).

```bash
OUT="$(mktemp -d)"
python SKILL_DIR/scripts/fetch_game.py --list
python SKILL_DIR/scripts/render_card.py SKILL_DIR/references/example_game.json --out "$OUT"
python SKILL_DIR/scripts/clean_image.py check "$OUT"/*.jpg
```

All three should finish without errors (`--list` needs network), the render should print a `photo:` line with the photographer credit and write `result_feed.jpg`, `summary_feed.jpg` and `photo_credit.txt`, and every `check` line should say `CLEAN`.

On Windows (PowerShell or cmd), make any empty folder instead of using `mktemp`, for example `C:\temp\naadat-check`, and run the same three commands with that folder as the output: `--out C:\temp\naadat-check` for the render, then `check` with the file names in it (`C:\temp\naadat-check\result_feed.jpg` and so on).
