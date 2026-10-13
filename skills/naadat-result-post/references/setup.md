# Setup per app

The skill is one folder (`naadat-result-post/`) with `SKILL.md` at its root. It needs Python 3.8+ with Pillow, and for automatic fetching, internet access to `tulospalvelu.leijonat.fi`.

## Claude (desktop app and claude.ai)

1. Settings > Capabilities: turn on **Code execution and file creation**.
2. Same page, network access for code execution: allow `tulospalvelu.leijonat.fi` (or all domains). If your plan or admin does not allow this, the skill still works: Claude fetches the one game report with its web tool or asks you to paste it (SKILL.md step 1, "Blocked network").
3. Settings > Capabilities > Skills: upload `naadat-result-post.zip` (from the repo's `dist/` folder) and switch it on.
4. In a new chat: "Make the result post for last night's Näädät game."

## Claude Code

```bash
cp -r skills/naadat-result-post ~/.claude/skills/
pip install pillow
```

## Codex (CLI, IDE extension, app)

```bash
cp -r skills/naadat-result-post ~/.codex/skills/
pip install pillow
```

Codex's default sandbox blocks the network. Either approve the fetch command when Codex asks, or allow network for the workspace in `~/.codex/config.toml`:

```toml
[sandbox_workspace_write]
network_access = true
```

## Any other assistant (ChatGPT, Gemini, ...)

Upload the zip and say: "Unzip this, read naadat-result-post/SKILL.md and follow it for last night's game." Sandboxes without internet use the "Blocked network" path in SKILL.md step 1.

## Checking an install

```bash
python scripts/fetch_game.py --list
python scripts/render_card.py references/example_game.json --out test/
python scripts/clean_image.py check test/*.jpg
```
