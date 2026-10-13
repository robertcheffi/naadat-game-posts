#!/usr/bin/env python3
"""Turn FCAA (Finnish college hockey, fcaahockey.app) data into the same game.json
(post-game) and pregame.json (pre-game) files fetch_game.py writes for III-divisioona.

fcaahockey.app sits behind a Vercel bot check: plain HTTP (curl, Python, a sandbox)
gets HTTP 429 "Vercel Security Checkpoint". Only a real browser gets data, so this
script never needs the network itself. Three ways in, best first:

 1. An assistant with a browser tool: open https://fcaahockey.app/stats/mens first
    (that passes the check), then open the API URLs that `fcaa.py urls` prints in
    the same browser and save each raw JSON response to the file name it gives.
       python fcaa.py list --recent recent.json --upcoming upcoming.json   (find the game id)
       python fcaa.py result --recent recent.json --upcoming upcoming.json --events events.json \\
                             [--rosters rosters.json] [--goalies goalies.json] --out game.json
       python fcaa.py pregame --upcoming upcoming.json --next --out pregame.json
 2. No browser tool: the user opens fcaahockey.app/stats/mens on the phone or
    computer, taps Recent and then the game (pre-game: the Upcoming tab), selects
    all text, copies it and pastes it in the chat. Save the paste unchanged:
       python fcaa.py result --page-text pasted.txt --out game.json
       python fcaa.py pregame --page-text pasted.txt --next --out pregame.json
 3. Last resort: the manual templates in references/ (SKILL.md, manual path).

Other subcommands:
  list  --recent recent.json [--upcoming upcoming.json]
        Näädät's games newest first (id, date, Näädät-first score, status, opponent);
        says which past-season URL to open when the file has no finished Näädät game
  urls  [--game-id ID] [--season 2025-26]   the URLs to open in a browser, in order
  try   [--game-id ID] [--season S] [--save-dir DIR]
        one plain HTTP request; on the bot check it says that a browser or a paste
        is needed (it never tries to get around the check). If the API answers,
        it saves the JSON files into --save-dir.

Exit codes (as fetch_game.py):
  0  file written
  1  error: bad or missing input, not a Näädät game, game not in the file (read the message)
  2  no result yet: the game is not final (result), or it has not been played yet
     (result: make the pre-game post instead)
Check every written file with `python fetch_game.py --validate <file>` before rendering.

Standard library only, Python 3.8+. Reuses fetch_game.py helpers (summary line,
name clash handling, validation) by import.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import fetch_game as fg  # noqa: E402  (same folder; stdlib only, no side effects on import)

SCRIPT = os.path.abspath(__file__)
SKILL_DIR = os.path.dirname(HERE)
LOGO_DIR = os.path.join(SKILL_DIR, "assets", "logos")
FCAA_LOGO_INDEX = os.path.join(LOGO_DIR, "fcaa", "index.json")

ORIGIN = "https://fcaahockey.app"
STATS_URL = ORIGIN + "/stats/mens"
API = ORIGIN + "/api/public"
NAADAT_TEAM_ID = "team:1758726133829:1mayp7m6o"
NAADAT_NAME = "Näädät"
COMPETITION = "FCAA"
RECENT_LIMIT = 500   # without ?limit= recent-games returns only the latest 20 games

EXIT_ERROR, EXIT_NOT_FINAL = fg.EXIT_ERROR, fg.EXIT_NOT_FINAL
Stop = fg.Stop

# Labels the app prints after a goal ("Goal by #41 Olli Huovinen(Even strength)")
# mapped to the API's goalType, so the page path and the JSON path write the same
# type_raw. Labels not listed here are slugified.
PAGE_GOAL_LABELS = {
    "even strength": "even-strength",
    "power play goal": "power-play",
    "5-on-3 power play goal": "power-play-2",
    "shorthanded goal": "short-handed",
    "short-handed goal": "short-handed",
    "empty net goal": "empty-net",
    "penalty shot goal": "penalty-shot",
    "shootout goal": "shootout",
}
PAGE_DATE_NOTE = ("ASK THE USER: Did this game start at 21:00 or later? The page then shows the next day "
                  "(the app converts the start time from UTC). If it did, set 'date' in the file one day "
                  "earlier.")
SEASON_MONTHS = (8, 9, 10, 11, 12, 1, 2, 3, 4)   # FCAA plays from autumn to spring
FIRST_SEASON_START = dt.date(2025, 8, 1)         # the app's first season is 2025-26


# --------------------------------------------------------------------------- helpers

def config() -> dict:
    try:
        cfg = fg.load_config()
    except (OSError, ValueError):
        cfg = {}
    fc = cfg.get("fcaa") if isinstance(cfg.get("fcaa"), dict) else {}
    return {
        "display_name": cfg.get("display_name") or NAADAT_NAME,
        "team_id": fc.get("team_id") or NAADAT_TEAM_ID,
        "name": fc.get("name") or NAADAT_NAME,
    }


def nfc(text) -> str:
    """Composed Unicode: macOS and some clipboards paste 'ä' as 'a' plus a combining mark."""
    return unicodedata.normalize("NFC", str(text or ""))


def fold(text) -> str:
    return " ".join(nfc(text).split()).casefold()


def slug(name) -> str:
    """fcaahockey.app logo slug: lowercased, accents stripped, other characters -> '-'."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", s)).strip("-")


_LOGO_INDEX = None


def logo_asset(team_name: str):
    """'fcaa/<file>' (path under assets/logos) of a bundled FCAA logo, or None."""
    global _LOGO_INDEX
    if _LOGO_INDEX is None:
        _LOGO_INDEX = {}
        try:
            with open(FCAA_LOGO_INDEX, encoding="utf-8-sig") as f:
                raw = json.load(f)
            for k, v in raw.items():
                if not k.startswith("_") and isinstance(v, str):
                    _LOGO_INDEX[slug(k)] = v
        except (OSError, ValueError):
            pass
    candidates = []
    if slug(team_name) in _LOGO_INDEX:
        candidates.append(_LOGO_INDEX[slug(team_name)])
    candidates.append(slug(team_name) + ".png")
    for file in candidates:
        rel = os.path.normpath(os.path.join("fcaa", file)).replace(os.sep, "/")
        if rel.startswith("..") or os.path.isabs(rel):
            continue
        if os.path.isfile(os.path.join(LOGO_DIR, rel)):
            return rel
    return None


def is_us_team(name, team_id=None, cfg=None) -> bool:
    cfg = cfg or config()
    if team_id and team_id == cfg["team_id"]:
        return True
    return fold(name) in (fold(cfg["name"]), fold(cfg["display_name"]))


def season_of(day: dt.date) -> str:
    """FCAA season id, e.g. 2025-26 (a season starts in August)."""
    start = day.year if day.month >= 7 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def parse_iso_date(text) -> dt.date:
    try:
        return dt.datetime.strptime(str(text or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def norm_time(text) -> str:
    """'5:10' -> '05:10'; '' when unreadable."""
    m = re.fullmatch(r"\s*(\d{1,3}):(\d{2})\s*", str(text or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def seconds(mmss: str) -> int:
    m = re.fullmatch(r"(\d+):(\d{2})", mmss or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def fcaa_person(raw) -> dict:
    """'Olli Huovinen' -> first 'Olli', last 'Huovinen'. FCAA names are 'First Last'
    as typed (fetch_game.person expects the leijonat 'LAST First' form)."""
    raw = " ".join(str(raw or "").split()) if isinstance(raw, str) else ""
    raw = re.sub(r"^#\s*\d+\s+", "", raw)
    if not raw:
        return {"full": "", "short": "", "last": "", "first": "", "display": ""}
    parts = raw.split(" ")
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in fg.NAME_PARTICLES:
        i -= 1   # 'Daniel van der Berg' -> last 'van der Berg'
    first = parts[:i] if len(parts) > 1 else []
    last_words = parts[i:] if len(parts) > 1 else parts
    last = " ".join(w.lower() if w.lower() in fg.NAME_PARTICLES and j < len(last_words) - 1 else w
                    for j, w in enumerate(last_words))
    first_s = " ".join(first)
    if first:
        initials = "-".join(x[:1] for x in first[0].split("-") if x)
        short = f"{initials}. {last}"
    else:
        short = last
    return {"full": f"{first_s} {last}".strip(), "short": short, "last": last, "first": first_s, "display": last}


def goal_tag(raw_type: str) -> str:
    """'' | PP | SH | EN from the FCAA goal type slug."""
    t = (raw_type or "").lower()
    if "empty" in t:
        return "EN"
    if "power" in t:
        return "PP"
    if "short" in t:
        return "SH"
    return ""


def is_shootout_type(raw_type: str) -> bool:
    t = (raw_type or "").lower()
    return "shootout" in t or "shoot-out" in t


def load_json(path: str, what: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as exc:
        raise Stop(f"Could not open {what} {path}: {exc}")
    if "Vercel Security Checkpoint" in text or text.lstrip().startswith("<"):
        raise Stop(f"{path} is a web page, not the API's JSON (the bot check page or the app itself). "
                   "Open " + STATS_URL + " first in the same browser, then the API URL again, and save "
                   "the raw JSON text it shows.")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Stop(f"{what} {path} is not valid JSON: {exc}\nSave the raw response text exactly as "
                   "the browser shows it (a summary is not enough).")


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except OSError as exc:
        raise Stop(f"Could not open --page-text {path}: {exc}")


def lines_of(text: str) -> list:
    out = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        ln = nfc(ln.replace(" ", " ")).strip()
        if ln:
            out.append(ln)
    return out


def games_in(obj, what: str) -> list:
    """The game list of a recent-games / upcoming-games response, a bare list, or one game."""
    if isinstance(obj, dict) and isinstance(obj.get("games"), list):
        return [g for g in obj["games"] if isinstance(g, dict)]
    if isinstance(obj, list):
        return [g for g in obj if isinstance(g, dict)]
    if isinstance(obj, dict) and obj.get("homeTeam") and obj.get("awayTeam"):
        return [obj]
    raise Stop(f"{what} is not a recent-games/upcoming-games response (expected {{\"games\": [...]}}).")


def team_of(g: dict, side: str) -> dict:
    t = g.get(f"{side}Team")
    return t if isinstance(t, dict) else {}


def has_us(g: dict, cfg: dict) -> bool:
    return any(is_us_team(team_of(g, s).get("name"), team_of(g, s).get("id"), cfg) for s in ("home", "away"))


def game_when(g: dict):
    """(date, 'HH:MM') of a game record from scheduledDate/scheduledTime (local
    Helsinki time). startTime carries a false 'Z' on finished games, so it is only
    a fallback."""
    day = parse_iso_date(g.get("scheduledDate"))
    tm = norm_time(g.get("scheduledTime"))
    if day is None:
        st = str(g.get("startTime") or "")
        day = parse_iso_date(st[:10])
        if day is not None and not tm and len(st) >= 16:
            tm = norm_time(st[11:16])
    return day, tm


def describe(g: dict) -> str:
    day, tm = game_when(g)
    h, a = team_of(g, "home"), team_of(g, "away")
    score = f" {h.get('score')}-{a.get('score')}" if h.get("score") is not None else ""
    when = f"{fg.fi_date(day)} {tm}".strip() if day else "no date yet"
    return f"{g.get('id')}  {when}  {h.get('name')} vs {a.get('name')}{score}  {g.get('status') or ''}".rstrip()


def season_index(target_id, day, tm, game_lists, cfg) -> int:
    """Place of the game among Näädät's FCAA games of the same season with a date
    (0 = first), or None. Games of other seasons in the lists are not counted."""
    seen = {}
    for lst in game_lists:
        for g in lst:
            if not has_us(g, cfg):
                continue
            gd, gt = game_when(g)
            if gd is None:
                continue
            seen[g.get("id")] = (gd, gt or "00:00", str(g.get("id")))
    if target_id not in seen:
        if day is None:
            return None
        seen[target_id] = (day, tm or "00:00", str(target_id))
    season = season_of(seen[target_id][0])
    order = sorted((k for k in seen if season_of(seen[k][0]) == season), key=lambda k: seen[k])
    return order.index(target_id)


# --------------------------------------------------------------------------- game.json builder

def build_game(meta: dict, home_name: str, away_name: str, home_score, away_score, goals_raw: list,
               goalies_raw: list, cfg: dict) -> dict:
    """The game@1 record. goals_raw items: period (int), time 'MM:SS', side 'home'|'away',
    scorer (raw name), jersey (int|None), assists [raw names], raw_type, shootout (bool)."""
    us_home = is_us_team(home_name, meta.get("home_id"), cfg)
    us_away = is_us_team(away_name, meta.get("away_id"), cfg)
    if not us_home and not us_away:
        raise Stop(f"{home_name} vs {away_name} is not a Näädät game. This script makes posts only for "
                   "Näädät's own games.")
    if us_home and us_away:
        raise Stop(f"Both teams look like Näädät ({home_name!r}, {away_name!r}); check the input.")

    def side(name, score, is_us):
        team = {
            "name": cfg["display_name"] if is_us else name,
            "source_name": name,
            "association_id": None,
            "goals": score,
            "is_us": is_us,
            "logo_url": None,
        }
        asset = logo_asset(name)
        if asset:
            team["logo_asset"] = asset
        return team

    reg = [g for g in goals_raw if not g["shootout"]]
    so = [g for g in goals_raw if g["shootout"]]
    reg.sort(key=lambda g: (g["period"], seconds(g["time"]), g.get("order", 0)))
    reg_h = sum(1 for g in reg if g["side"] == "home")
    reg_a = len(reg) - reg_h
    so_h = sum(1 for g in so if g["side"] == "home")
    so_a = len(so) - so_h
    notes, warnings = [], []
    header = None if home_score is None or away_score is None else (home_score, away_score)
    matchup = f"{home_name} vs {away_name}"
    tie_help = ("Ask the user for the final result (and who won the shootout), or fill "
                "references/manual_game_template.json.")
    # A shootout needs positive evidence: shootout events (a shootout section, a period
    # above 3, a shootout goal type) or the game record's own shootout flag. It is never
    # inferred from the score: a goal missing from the log looks just like a shootout.
    so_evidence = bool(so) or bool(meta.get("so_evidence"))
    finish = "SO" if so_evidence else "REG"
    home_won = None
    if not goals_raw:
        if header is None:
            raise Stop(f"{matchup}: FCAA gives neither a final score nor goal events. " + tie_help)
        if header[0] == header[1]:
            raise Stop(f"{matchup}: FCAA shows a tied final score {header[0]}-{header[1]} and no goal events. "
                       "FCAA games have no ties (a shootout always decides), so the result is not complete "
                       "in FCAA. " + tie_help)
        home_score, away_score = header
        home_won = home_score > away_score
        if home_score + away_score > 0:
            warnings.append("WARNING: FCAA has no goal events for this game, so the post shows the score "
                            "without scorers. Tell the user; if FCAA adds the goal log later, make the file "
                            "again.")
    elif so_evidence:
        if so_h != so_a:
            home_won = so_h > so_a
            if header and header[0] != header[1] and (header[0] > header[1]) != home_won:
                # FCAA's own final score is the official result; a shootout log that points the
                # other way means the log is wrong or incomplete, so never flip the result silently.
                raise Stop(f"{matchup}: FCAA's final score {header[0]}-{header[1]} names "
                           f"{home_name if header[0] > header[1] else away_name} the winner, but the shootout "
                           f"goals ({so_h}-{so_a}) point to the other team. Ask the user who won, then use "
                           "the manual template. " + tie_help)
        elif header and header[0] != header[1]:
            home_won = header[0] > header[1]
        else:
            raise Stop(f"{matchup} went to a shootout, but neither the shootout goals ({so_h}-{so_a}) nor "
                       "the final score in FCAA says who won it. " + tie_help)
        if reg_h == reg_a:
            final = (reg_h + 1, reg_a) if home_won else (reg_h, reg_a + 1)
            if header and header != final:
                notes.append(f"FCAA's header says {header[0]}-{header[1]}; regulation ended {reg_h}-{reg_a} and "
                             f"{home_name if home_won else away_name} won the shootout, so the final score is "
                             f"{final[0]}-{final[1]} (the shootout winner gets one goal)")
            home_score, away_score = final
        else:
            if header is None or header[0] == header[1] or (header[0] > header[1]) != home_won:
                raise Stop(f"{matchup}: FCAA marks a shootout, but the regulation goals in the log add up to "
                           f"{reg_h}-{reg_a} (home-away), not a tie, so the goal log is incomplete and the final "
                           "score cannot be worked out. " + tie_help)
            home_score, away_score = header
            warnings.append(f"WARNING: FCAA marks a shootout, but the regulation goals in the log add up to "
                            f"{reg_h}-{reg_a} (home-away), not a tie: the goal log is incomplete. Tell the user "
                            "the scorer lists may miss goals.")
    else:
        if header is None:
            home_score, away_score = reg_h, reg_a
        if home_score == away_score:
            raise Stop(f"{matchup}: the final score in FCAA is tied {home_score}-{away_score}. FCAA games have "
                       "no ties (a shootout always decides), so the shootout winner is missing from FCAA or "
                       "the score is a placeholder. " + tie_help)
        home_won = home_score > away_score
        if (home_score, away_score) != (reg_h, reg_a):
            notes.append(f"the goal events add up to {reg_h}-{reg_a} (home-away) but the final score is "
                         f"{home_score}-{away_score}; the goal log is incomplete")
            if reg_h == reg_a and abs(home_score - away_score) == 1:
                warnings.append(f"WARNING: the goal log ends tied {reg_h}-{reg_a} and the final score is "
                                f"{home_score}-{away_score}, but FCAA shows no shootout. Most likely a goal is "
                                "missing from FCAA's log. Ask the user whether the game went to a shootout; "
                                "if it did, set finish to SO, result to SOW/SOL and add the SO row to periods.")

    home = side(home_name, home_score, us_home)
    away = side(away_name, away_score, us_away)
    us, them = (home, away) if us_home else (away, home)

    goals = []
    h = a = 0
    for g in reg:
        if g["side"] == "home":
            h += 1
        else:
            a += 1
        goals.append({
            "period": g["period"],
            "time": g["time"],
            "team": "us" if (g["side"] == "home") == us_home else "them",
            "scorer": fcaa_person(g["scorer"]),
            "jersey": g["jersey"],
            "assists": [fcaa_person(n) for n in g["assists"] if str(n or "").strip()],
            "type": goal_tag(g["raw_type"]),
            "type_raw": g["raw_type"],
            "score_after": f"{h}-{a}",
        })
    periods = []
    if goals_raw:   # no goal log at all: no period rows (the score alone is still right)
        for p in (1, 2, 3):
            periods.append({"label": str(p),
                            "home": sum(1 for g in reg if g["period"] == p and g["side"] == "home"),
                            "away": sum(1 for g in reg if g["period"] == p and g["side"] == "away")})
    for g in reg:
        if g["period"] not in (1, 2, 3):
            notes.append(f"a goal at {g['time']} is in period {g['period']}; FCAA has three periods")
    if finish == "SO" and periods:
        periods.append({"label": "SO", "home": 1 if home_won else 0, "away": 0 if home_won else 1})
        winners = [g for g in so if (g["side"] == "home") == home_won]
        if winners:
            w = winners[-1]
            goals.append({
                "period": 5, "time": w["time"] or "65:00",
                "team": "us" if (w["side"] == "home") == us_home else "them",
                "scorer": fcaa_person(w["scorer"]), "jersey": w["jersey"],
                "assists": [], "type": "", "type_raw": w["raw_type"] or "shootout",
                "score_after": f"{home_score}-{away_score}",
            })

    goalies = []
    for gk in goalies_raw:
        goalies.append({"team_name": gk["team_name"], "name": fcaa_person(gk["name"]),
                        "saves": gk["saves"], "is_us": is_us_team(gk["team_name"], gk.get("team_id"), cfg)})

    for team, is_us in (("us", True), ("them", False)):
        objs = []
        for gl in goals:
            if gl["team"] == team:
                objs.append(gl["scorer"])
                objs.extend(gl["assists"])
        objs.extend(gk["name"] for gk in goalies if gk["is_us"] == is_us)
        fg.disambiguate_names(objs)

    def leaders(team: str) -> list:
        table = {}
        for gl in goals:
            if gl["team"] != team or gl["period"] == 5:
                continue
            table.setdefault(gl["scorer"]["full"], {"name": gl["scorer"], "goals": 0, "assists": 0})["goals"] += 1
            for a_ in gl["assists"]:
                table.setdefault(a_["full"], {"name": a_, "goals": 0, "assists": 0})["assists"] += 1
        rows = list(table.values())
        rows.sort(key=lambda r: (-(r["goals"] + r["assists"]), -r["goals"], r["name"]["last"]))
        return rows

    shots = meta.get("shots_by_side")
    if shots is not None:
        us_shots, them_shots = (shots["home"], shots["away"]) if us_home else (shots["away"], shots["home"])
        so_us = 1 if finish == "SO" and us["goals"] > them["goals"] else 0
        so_them = 1 if finish == "SO" and them["goals"] > us["goals"] else 0
        # Every regulation goal and every save is a shot on goal, so a side's shots are at
        # least the other side's saves plus its own regulation goals. FCAA's per-period
        # goalie rows do not always agree with themselves; then the slide would show
        # numbers that cannot both be true.
        saves_us = sum(gk["saves"] for gk in goalies if gk["is_us"])
        saves_them = sum(gk["saves"] for gk in goalies if not gk["is_us"])
        bad = []
        for who, n_shots, n_saves, n_goals in ((us["name"], us_shots, saves_them, us["goals"] - so_us),
                                               (them["name"], them_shots, saves_us, them["goals"] - so_them)):
            if n_shots < n_saves + n_goals:
                bad.append(f"{who}: {n_shots} shots, but the other goalies made {n_saves} saves and "
                           f"{n_goals} goal(s) went in")
        if bad:
            notes.append("FCAA's goalie stats do not add up (" + "; ".join(bad) + "); shots left out, "
                         "saves kept")
            shots = None
        else:
            shots = {"us": us_shots, "them": them_shots}

    day = meta["date"]
    data = {
        "schema": fg.GAME_SCHEMA,
        "source": "fcaa",
        "game_id": meta.get("game_id"),
        "season": season_of(day),
        "url": STATS_URL,
        "competition": COMPETITION,
        "series": f"{COMPETITION} {season_of(day)}",
        "date": day.isoformat(),
        "time": "",
        "arena": fg.clean_rink(meta.get("arena")),
        "attendance": None,
        "finish": finish,
        "result": fg.result_code(us["goals"], them["goals"], finish, True),
        "home": home,
        "away": away,
        "us_side": "home" if us_home else "away",
        "periods": periods,
        "goals": goals,
        "scorers_us": leaders("us"),
        "scorers_them": leaders("them"),
        "shots": shots,
        "goalies": goalies,
        "status": "final",
    }
    if meta.get("season_index") is not None:
        data["season_index"] = meta["season_index"]
    data["_notes"] = notes
    data["_warnings"] = warnings
    return data


def write_game(data: dict, out: str) -> int:
    notes = data.pop("_notes", [])
    warnings = data.pop("_warnings", [])
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # source 'manual' only makes fetch_game show the arena in the line, so the user confirms it.
    print(fg.summary_line(dict(data, source="manual"), "Wrote", out))
    for side in ("home", "away"):
        t = data[side]
        if not t.get("is_us") and not t.get("logo_asset"):
            print(f"note: no bundled logo for {t['name']}; the graphic shows a lettered badge unless the "
                  "user sends the club's logo (save it next to the file and set logo_file).")
    for n in notes:
        print(f"note: {n}")
    for w in warnings:
        print(w)
    w = fg.goal_log_warning(data) if data["goals"] else None
    if w:
        print(w)
    if not data["arena"]:
        print("note: FCAA gives no rink for this game; ask the user and fill in arena, or leave it empty.")
    return 0


# --------------------------------------------------------------------------- result: JSON path

def player_names(events: list, rosters):
    """(playerId -> name from this game's events, playerId -> name from the rosters)."""
    roster_names = {}
    if isinstance(rosters, dict):
        for key in ("home", "away"):
            for p in rosters.get(key) or []:
                if isinstance(p, dict) and p.get("id") and p.get("name"):
                    roster_names.setdefault(p["id"], p["name"])
    event_names = {}
    for e in events:
        if e.get("playerId") and e.get("playerName"):
            event_names.setdefault(e["playerId"], e["playerName"])
    return event_names, roster_names


def goalie_rows(raw, home: dict, away: dict, notes: list, game_id=None, what="the goalie file"):
    """(goalie list, shots by side or None) from goalie-periods. Unknown shapes are skipped.
    Stops when a row belongs to another game than game_id."""
    if isinstance(raw, dict):
        raw = next((v for v in raw.values() if isinstance(v, list)), [])
    if not isinstance(raw, list) or not raw:
        return [], None
    other = sorted({str(r.get("gameId")) for r in raw
                    if isinstance(r, dict) and r.get("gameId") and r.get("gameId") != game_id})
    if game_id and other:
        raise Stop(f"{what} is for game {other[0]}, not {game_id}: it would put another game's goalies "
                   f"and saves in the post. Save {API}/games/{game_id}/goalie-periods (add ?season= for a "
                   "past season) and run again.")
    per, periods = {}, {}
    try:
        for r in raw:
            if not isinstance(r, dict):
                continue
            tid, tname = r.get("teamId"), r.get("teamName") or ""
            side = ("home" if (tid and tid == home.get("id")) or fold(tname) == fold(home.get("name"))
                    else "away" if (tid and tid == away.get("id")) or fold(tname) == fold(away.get("name"))
                    else None)
            if side is None:
                continue
            key = (side, r.get("playerId") or r.get("playerName"))
            row = per.setdefault(key, {"side": side, "team_name": tname or (home if side == "home" else away).get("name"),
                                       "team_id": tid, "name": r.get("playerName") or "", "saves": 0,
                                       "against": 0, "first": len(per)})
            if fg.is_count(r.get("saves")):
                row["saves"] += r["saves"]
            if fg.is_count(r.get("shotsAgainst")):
                row["against"] += r["shotsAgainst"]
            periods.setdefault(side, set()).add(r.get("period"))
    except (TypeError, AttributeError):
        notes.append("the goalie data has an unexpected shape; goalies left out")
        return [], None
    rows = sorted(per.values(), key=lambda r: r["first"])
    goalies = [{"team_name": r["team_name"], "team_id": r["team_id"], "name": r["name"], "saves": r["saves"]}
               for r in rows]
    shots = None
    if all({1, 2, 3} <= periods.get(s, set()) for s in ("home", "away")):
        against = {s: sum(r["against"] for r in rows if r["side"] == s) for s in ("home", "away")}
        shots = {"home": against["away"], "away": against["home"]}   # shots BY a side = against the other's goalie
    else:
        notes.append("goalie stats do not cover all three periods for both teams; shots left out")
    return goalies, shots


def run_result_json(args, cfg) -> int:
    ev = load_json(args.events, "--events")
    events = ev.get("events") if isinstance(ev, dict) else ev
    if not isinstance(events, list):
        raise Stop(f"{args.events} is not a games/<id>/events response (expected {{\"events\": [...]}}).")
    events = [e for e in events if isinstance(e, dict)]
    ids = {e.get("gameId") for e in events if e.get("gameId")}
    game_id = args.game_id
    if game_id and ids - {game_id}:
        raise Stop(f"The events file is for game {sorted(ids - {game_id})[0]}, not the requested {game_id}. "
                   f"Save {API}/games/{game_id}/events (add ?season= for a past season) and run again.")
    if not game_id:
        if len(ids) > 1:
            raise Stop("The events file mixes several games; pass --game-id.")
        game_id = next(iter(ids), None)
    if not game_id and not args.game:
        raise Stop(f"The events file {args.events} does not name its game (no gameId in it, or no events). "
                   "Pass --game-id <the game's id from recent.json>; `python fcaa.py list --recent "
                   "recent.json` lists Näädät's games with their ids.")

    source = args.game or args.recent
    if not source:
        raise Stop("The events alone do not say the date, the teams' home/away order or whether the game is "
                   "final. Add --recent recent.json (the recent-games response) or --game game.json "
                   "(one game record from it). `python fcaa.py urls` lists the URLs.")
    games = games_in(load_json(source, "--game" if args.game else "--recent"), source)
    if not game_id and len(games) == 1:   # --game: one record, the events did not name it
        game_id = games[0].get("id")
    g = next((x for x in games if x.get("id") == game_id), None)
    if g is None:
        raise Stop(f"Game {game_id} is not in {source} ({len(games)} games). recent-games returns only the "
                   f"latest 20 unless ?limit={RECENT_LIMIT} is added, and a past season needs "
                   "?season=YYYY-YY. `python fcaa.py urls --game-id ID --season S` prints the right URL.")
    if not has_us(g, cfg):
        raise Stop(f"Game {game_id} ({describe(g)}) is not a Näädät game.")
    status = str(g.get("status") or "")
    day, tm = game_when(g)
    if status == "not-started":
        raise Stop(f"Game {game_id} has not been played yet ({describe(g)}). There is no result to post; "
                   "make the pre-game post instead (fcaa.py pregame).", EXIT_NOT_FINAL)
    if status != "finished":
        raise Stop(f"Game {game_id} is not final yet (status {status or 'unknown'!r}: {describe(g)}). "
                   "Wait for the final result.", EXIT_NOT_FINAL)
    if day is None:
        raise Stop(f"Game {game_id} has no date in {source}.")

    home, away = team_of(g, "home"), team_of(g, "away")
    rosters = load_json(args.rosters, "--rosters") if args.rosters else None
    event_names, roster_names = player_names(events, rosters)
    dropped = []
    goals_raw = []
    for order, e in enumerate(reversed(events)):     # the API lists newest first
        if e.get("eventType") != "goal":
            continue
        tid, tname = e.get("teamId"), e.get("teamName")
        if (tid and tid == home.get("id")) or (not tid and fold(tname) == fold(home.get("name"))):
            side = "home"
        elif (tid and tid == away.get("id")) or (not tid and fold(tname) == fold(away.get("name"))):
            side = "away"
        elif fold(tname) == fold(home.get("name")):
            side = "home"
        elif fold(tname) == fold(away.get("name")):
            side = "away"
        else:
            raise Stop(f"A goal at {e.get('time')} is for team {tname!r}, neither {home.get('name')} nor "
                       f"{away.get('name')}; the events and the game record do not match.")
        tm_ = norm_time(e.get("time") or e.get("timeInPeriod"))
        try:
            period = int(e.get("period"))
        except (TypeError, ValueError):
            period = min(seconds(tm_) // 1200 + 1, 3)
        raw_type = str(e.get("goalType") or "").strip()
        assists = []
        for key in ("assist1PlayerId", "assist2PlayerId"):
            pid = e.get(key)
            if not pid:
                continue
            # The app names an assist only when the player appears in this game's own
            # events (it never looks at the roster), so the page and the JSON agree.
            if event_names.get(pid):
                assists.append(event_names[pid])
            else:
                dropped.append(f"{tm_} {roster_names.get(pid) or 'unknown player ' + pid}")
        number = e.get("playerNumber")
        goals_raw.append({
            "period": period, "time": tm_, "side": side, "scorer": e.get("playerName") or "",
            "jersey": number if fg.is_count(number) else None, "assists": assists, "raw_type": raw_type,
            "shootout": period > 3 or is_shootout_type(raw_type), "order": order,
        })

    notes = []
    if dropped:
        notes.append("FCAA's goal record names more assists than the app shows (the player is not in this "
                     "game's events); left out like the app does: " + ", ".join(dropped)
                     + ". Add one to the goal's assists only if the user confirms it.")
    goalies, shots = ([], None)
    if args.goalies:
        goalies, shots = goalie_rows(load_json(args.goalies, "--goalies"), home, away, notes, game_id,
                                     f"--goalies {args.goalies}")
    idx = None
    if args.recent:
        lists = [games]
        if args.upcoming:
            lists.append(games_in(load_json(args.upcoming, "--upcoming"), args.upcoming))
        idx = season_index(game_id, day, tm, lists, cfg)
    try:
        game_period = int(g.get("period") or 0)
    except (TypeError, ValueError):
        game_period = 0
    meta = {"game_id": game_id, "date": day, "arena": g.get("location") or "",
            "home_id": home.get("id"), "away_id": away.get("id"), "shots_by_side": shots,
            "season_index": idx, "so_evidence": g.get("isShootout") is True or game_period > 3}
    data = build_game(meta, home.get("name") or "", away.get("name") or "",
                      home.get("score") if fg.is_count(home.get("score")) else None,
                      away.get("score") if fg.is_count(away.get("score")) else None,
                      goals_raw, goalies, cfg)
    data["_notes"] = notes + data["_notes"]
    return write_game(data, args.out)


# --------------------------------------------------------------------------- result: page-text path

SCORE_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)$")
TIME_RE = re.compile(r"^\d{1,3}:\d{2}$")
DATE_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")
GOAL_RE = re.compile(r"^Goal by\s*(?:#\s*(\d+)\s+)?(.*?)\s*(?:\(([^()]*)\))?\s*$", re.I)
TYPE_RE = re.compile(r"^[a-z][a-z -]*$")
STATUS_WORDS = {"final", "finished", "live", "in progress", "not started", "scheduled", "upcoming",
                "postponed", "cancelled", "canceled"}


def page_date(text: str) -> dt.date:
    m = DATE_RE.match(text)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def parse_game_page(text: str) -> dict:
    lines = lines_of(text)
    stop_at = next((i for i, ln in enumerate(lines) if ln in ("Game Events", "Play-by-Play Events")), len(lines))
    start = next((i + 1 for i, ln in enumerate(lines[:stop_at]) if "Back to Statistics" in ln), 0)
    si = next((i for i in range(start, stop_at) if SCORE_RE.match(lines[i])), None)
    if si is None or si == 0 or si + 1 >= len(lines):
        raise Stop("This does not look like a copied FCAA game page: no 'Home  4 - 7  Away' header found. "
                   "Open the game in the FCAA app (Recent tab, tap the game), select all text, copy it "
                   "and paste it in full.")
    home_name, away_name = lines[si - 1], lines[si + 1]
    hs, as_ = (int(x) for x in SCORE_RE.match(lines[si]).groups())
    day, arena, status = None, "", ""
    for ln in lines[si + 2:stop_at]:
        if day is None and page_date(ln):
            day = page_date(ln)
        elif ln.casefold() in STATUS_WORDS:
            status = ln
        elif day is not None and not arena:
            arena = ln
    if day is None:
        raise Stop("The pasted game page has no date (DD/MM/YYYY) under the score; paste the whole page.")

    marker = max((i for i, ln in enumerate(lines) if ln == "All events from this game"), default=None)
    if marker is None:
        marker = max((i for i, ln in enumerate(lines) if ln == "Play-by-Play Events"), default=stop_at)
    body = lines[marker + 1:]
    goals, period, shootout_section = [], None, False
    i, order = 0, 0
    while i < len(body):
        ln = body[i]
        pm = re.match(r"^Period\s+(\d+)$", ln, re.I)
        if pm:
            period, shootout_section = int(pm.group(1)), int(pm.group(1)) > 3
            i += 1
            continue
        if re.match(r"^(shoot-?out|SO|penalty shootout)$", ln, re.I):
            period, shootout_section = 4, True
            i += 1
            continue
        if not TIME_RE.match(ln):
            i += 1
            continue
        block = [ln]
        j = i + 1
        while j < len(body) and not TYPE_RE.match(body[j]) and not TIME_RE.match(body[j]) \
                and not re.match(r"^Period\s+\d+$", body[j], re.I):
            block.append(body[j])
            j += 1
        etype = body[j] if j < len(body) and TYPE_RE.match(body[j]) else ""
        i = j + 1 if etype else j
        if etype != "goal" and not any(x.lower().startswith("goal by") for x in block):
            continue
        tm_ = norm_time(block[0])
        rest = block[1:]
        if rest and SCORE_RE.match(rest[0]):
            rest = rest[1:]
        if not rest:
            continue
        team = rest[0]
        desc = next((x for x in rest[1:] if x.lower().startswith("goal by")), "")
        assists_line = next((x for x in rest[1:] if x.lower().startswith("assists:")), "")
        gm = GOAL_RE.match(desc) if desc else None
        number, scorer, label = (gm.group(1), gm.group(2), gm.group(3) or "") if gm else (None, "", "")
        lab = " ".join(label.split()).casefold()
        raw_type = PAGE_GOAL_LABELS.get(lab, slug(re.sub(r"\bgoal\b", "", lab)) if lab else "")
        assists = []
        if assists_line:
            for part in assists_line.split(":", 1)[1].split(","):
                nm = re.sub(r"^#\s*\d+\s+", "", part.strip())
                if nm:
                    assists.append(nm)
        if fold(team) == fold(home_name):
            side = "home"
        elif fold(team) == fold(away_name):
            side = "away"
        else:
            raise Stop(f"A goal at {tm_} is credited to {team!r}, neither {home_name} nor {away_name}. "
                       "Paste the page text unchanged.")
        p = period if period is not None else min(seconds(tm_) // 1200 + 1, 3)
        goals.append({"period": p, "time": tm_, "side": side, "scorer": scorer,
                      "jersey": int(number) if number else None, "assists": assists, "raw_type": raw_type,
                      "shootout": shootout_section or p > 3 or is_shootout_type(raw_type), "order": order})
        order += 1
    events_view = any(ln in ("All events from this game", "Play-by-Play Events") for ln in lines)
    return {"home": home_name, "away": away_name, "home_score": hs, "away_score": as_, "date": day,
            "arena": arena, "status": status, "goals": goals, "events_view": events_view}


def date_doubts(day: dt.date, today: dt.date) -> list:
    """Warnings when a page date (read as DD/MM/YYYY) looks wrong: outside the FCAA season
    or in the future. A MM/DD/YYYY display would swap day and month."""
    def in_season(d: dt.date) -> bool:
        return d.month in SEASON_MONTHS and d >= FIRST_SEASON_START

    problems = []
    if not in_season(day):
        problems.append("is outside every FCAA season (August to April, from 2025-26 on)")
    if day > today:
        problems.append("is in the future")
    if not problems:
        return []
    msg = f"WARNING: the page date {fg.fi_date(day)} " + " and ".join(problems) + "."
    if day.day <= 12 and day.day != day.month:
        swapped = dt.date(day.year, day.day, day.month)
        if in_season(swapped) and swapped <= today:
            msg += (f" If the app shows dates month first, the game was on {fg.fi_date(swapped)}"
                    f" ({swapped.isoformat()}).")
    return [msg + " Ask the user for the game's date and fix 'date' in the file."]


def run_result_page(args, cfg) -> int:
    page = parse_game_page(read_text(args.page_text))
    if not (is_us_team(page["home"], None, cfg) or is_us_team(page["away"], None, cfg)):
        raise Stop(f"{page['home']} vs {page['away']} is not a Näädät game. This script makes posts only "
                   "for Näädät's own games.")
    if fold(page["status"]) not in ("final", "finished"):
        raise Stop(f"The pasted game page does not say Final (status {page['status'] or 'missing'!r}): "
                   f"{page['home']} {page['home_score']}-{page['away_score']} {page['away']}. Wait for the "
                   "final result. If the copy just missed the word, paste the whole page again.",
                   EXIT_NOT_FINAL)
    if not page["goals"] and page["home_score"] + page["away_score"] > 0:
        what = ("This paste is the Rosters or Goalie Stats view, not the goal log."
                if not page["events_view"] else "The pasted Game Events view lists no goals.")
        raise Stop(f"{what} Ask the user to open the game in the FCAA app, tap Game Events, select all, "
                   f"copy again and paste it ({page['home']} {page['home_score']}-{page['away_score']} "
                   f"{page['away']}).")
    meta = {"game_id": args.game_id, "date": page["date"], "arena": page["arena"], "shots_by_side": None,
            "season_index": None}
    data = build_game(meta, page["home"], page["away"], page["home_score"], page["away_score"],
                      page["goals"], [], cfg)
    rc = write_game(data, args.out)
    for w in date_doubts(page["date"], fg.helsinki_now().date()):
        print(w)
    print(PAGE_DATE_NOTE)
    return rc


# --------------------------------------------------------------------------- pregame

def pregame_record(game_id, day: dt.date, tm: str, arena: str, home_name: str, away_name: str,
                   cfg: dict, home_id=None, away_id=None, idx=None) -> dict:
    us_home = is_us_team(home_name, home_id, cfg)
    us_away = is_us_team(away_name, away_id, cfg)
    if not (us_home or us_away):
        raise Stop(f"{home_name} vs {away_name} is not a Näädät game.")

    def side(name, is_us):
        t = {"name": cfg["display_name"] if is_us else name, "source_name": name,
             "association_id": None, "is_us": is_us, "logo_url": None}
        asset = logo_asset(name)
        if asset:
            t["logo_asset"] = asset
        return t

    data = {
        "schema": fg.PREGAME_SCHEMA,
        "source": "fcaa",
        "game_id": game_id,
        "season": season_of(day),
        "url": STATS_URL,
        "competition": COMPETITION,
        "series": f"{COMPETITION} {season_of(day)}",
        "date": day.isoformat(),
        "time": tm,
        "weekday": fg.weekday_code(day),
        "arena": fg.clean_rink(arena),
        "home": side(home_name, us_home),
        "away": side(away_name, not us_home),
        "us_side": "home" if us_home else "away",
        "status": "scheduled",
    }
    if idx is not None:
        data["season_index"] = idx
    return data


def write_pregame(data: dict, out: str, started: bool) -> int:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(fg.pregame_line(data, "Wrote", out))
    if started:
        print(f"note: face-off {data['time']} on {fg.fi_date(data['date'])} is already past; a pre-game post "
              "normally goes out before the game.", file=sys.stderr)
    for side in ("home", "away"):
        t = data[side]
        if not t.get("is_us") and not t.get("logo_asset"):
            print(f"note: no bundled logo for {t['name']}; the graphic shows a lettered badge unless the user "
                  "sends the club's logo (save it next to the file and set logo_file).")
    if not data["arena"]:
        print("WARNING: FCAA names no rink for this game; ask the user and fill in arena.")
    return 0


def pick_upcoming(games: list, args, cfg, what: str):
    ours = [g for g in games if has_us(g, cfg)]
    if args.game_id:
        g = next((x for x in games if x.get("id") == args.game_id), None)
        if g is None:
            raise Stop(f"Game {args.game_id} is not in {what}. If it has been played, make the result post "
                       "(fcaa.py result).")
        return g
    now = fg.helsinki_now()
    dated = []
    for g in ours:
        day, tm = game_when(g)
        if day is None or not tm:
            continue
        when = dt.datetime.combine(day, dt.datetime.strptime(tm, "%H:%M").time())
        dated.append((when, g))
    dated.sort(key=lambda x: x[0])
    if args.date:
        day = fg.parse_date(args.date)
        found = [g for w, g in dated if w.date() == day]
        if not found:
            raise Stop(f"No Näädät FCAA game on {fg.fi_date(day)} in {what}.\n" + unscheduled_hint(ours))
        return found[0]
    found = [g for w, g in dated if w >= now]
    if not found:
        raise Stop(f"No upcoming scheduled Näädät game in {what}.\n" + unscheduled_hint(ours))
    return found[0]


def unscheduled_hint(ours: list) -> str:
    rows = [describe(g) for g in ours if game_when(g)[0] is None or not game_when(g)[1]]
    if not rows:
        return "FCAA lists no other Näädät games."
    return ("Näädät games FCAA lists without a date or time yet (ask the user for them, or use the manual "
            "template):\n  " + "\n  ".join(rows))


def run_pregame_json(args, cfg) -> int:
    games = games_in(load_json(args.upcoming, "--upcoming"), args.upcoming)
    g = pick_upcoming(games, args, cfg, args.upcoming)
    if not has_us(g, cfg):
        raise Stop(f"Game {g.get('id')} ({describe(g)}) is not a Näädät game.")
    status = str(g.get("status") or "")
    if status == "finished":
        raise Stop(f"Game {g.get('id')} has already been played ({describe(g)}); make the result post.")
    day, tm = game_when(g)
    if day is None or not tm:
        raise Stop(f"Game {g.get('id')} ({describe(g)}) has no date or face-off time yet in FCAA. Ask the user, "
                   "or fill references/manual_pregame_template.json.")
    lists = [games]
    if args.recent:
        lists.append(games_in(load_json(args.recent, "--recent"), args.recent))
    idx = season_index(g.get("id"), day, tm, lists, cfg) if args.recent else None
    h, a = team_of(g, "home"), team_of(g, "away")
    data = pregame_record(g.get("id"), day, tm, g.get("location") or "", h.get("name") or "",
                          a.get("name") or "", cfg, h.get("id"), a.get("id"), idx)
    when = dt.datetime.combine(day, dt.datetime.strptime(tm, "%H:%M").time())
    return write_pregame(data, args.out, status not in ("", "not-started") or when <= fg.helsinki_now())


def parse_upcoming_page(text: str) -> list:
    """Games of a copied Upcoming tab: blocks of [rink or 'Upcoming', DD/MM/YYYY or TBD,
    home, HH:MM or TBD, away]."""
    lines = lines_of(text)
    start = next((i + 1 for i, ln in enumerate(lines) if ln in ("Scheduled games", "Upcoming Games")), 0)
    end = next((i for i in range(start, len(lines)) if lines[i] in ("Total Games",)), len(lines))
    games = []
    i = start
    while i < end:
        ln = lines[i]
        day = page_date(ln)
        if (day or ln == "TBD") and i + 3 < len(lines) and (TIME_RE.match(lines[i + 2]) or lines[i + 2] == "TBD"):
            prev = lines[i - 1] if i - 1 >= start else ""
            rink = "" if prev in ("Upcoming", "TBD", "Scheduled games", "Upcoming Games") or page_date(prev) else prev
            tm = norm_time(lines[i + 2]) if TIME_RE.match(lines[i + 2]) else ""
            games.append({"id": None, "homeTeam": {"name": lines[i + 1]}, "awayTeam": {"name": lines[i + 3]},
                          "scheduledDate": day.isoformat() if day else "", "scheduledTime": tm,
                          "location": rink, "status": "not-started"})
            i += 4
            continue
        i += 1
    if not games:
        raise Stop("This does not look like the copied Upcoming tab of the FCAA app (no games found). Open "
                   f"{STATS_URL}, tap Upcoming, select all text, copy it and paste it in full.")
    return games


def run_pregame_page(args, cfg) -> int:
    games = parse_upcoming_page(read_text(args.page_text))
    if args.game_id:
        raise Stop("The copied Upcoming tab has no game ids; use --next or --date with --page-text.")
    g = pick_upcoming(games, args, cfg, "the pasted Upcoming tab")
    day, tm = game_when(g)
    h, a = team_of(g, "home"), team_of(g, "away")
    data = pregame_record(None, day, tm, g.get("location") or "", h.get("name"), a.get("name"), cfg)
    when = dt.datetime.combine(day, dt.datetime.strptime(tm, "%H:%M").time())
    return write_pregame(data, args.out, when <= fg.helsinki_now())


# --------------------------------------------------------------------------- urls / try

def api_urls(game_id=None, season=None) -> list:
    """(label, url, save-as) in the order to open them."""
    q = f"?season={season}" if season else ""
    rq = f"?season={season}&limit={RECENT_LIMIT}" if season else f"?limit={RECENT_LIMIT}"
    out = [("finished games (find the game id here)", f"{API}/recent-games{rq}", "recent.json"),
           ("upcoming games (pre-game)", f"{API}/upcoming-games", "upcoming.json")]
    if game_id:
        out += [("goal and penalty events", f"{API}/games/{game_id}/events{q}", "events.json"),
                ("rosters (assist names)", f"{API}/games/{game_id}/rosters{q}", "rosters.json"),
                ("goalie stats (may be [])", f"{API}/games/{game_id}/goalie-periods{q}", "goalies.json")]
    return out


def run_urls(args) -> int:
    print("fcaahockey.app answers only a real browser (Vercel bot check). In a browser tool:")
    print(f"  1. open {STATS_URL}  (wait until the standings show; this passes the check)")
    print("  2. in the same browser, open each URL below and save the raw JSON text it shows, unchanged:")
    for n, (label, url, save) in enumerate(api_urls(args.game_id, args.season)):
        print(f"       {chr(ord('a') + n)}. {url}")
        print(f"          -> {save}   ({label})")
    print("     Past seasons need --season (e.g. 2025-26) on these URLs; the current season works without.")
    print("     If a URL opens as a page instead of JSON, run this in the page's console and copy the")
    print("     output:  await (await fetch('<URL>')).text()")
    season = f" --season {args.season}" if args.season else ""
    if args.game_id:
        print("  3. make the files:")
        print(f'       python "{SCRIPT}" result --recent recent.json --upcoming upcoming.json --events events.json '
              "--rosters rosters.json --goalies goalies.json --out game.json")
    else:
        print("  3. find the game and its id (Näädät's games, newest first):")
        print(f'       python "{SCRIPT}" list --recent recent.json --upcoming upcoming.json')
        print(f'     then for a result post: python "{SCRIPT}" urls --game-id <id>{season}  (the game\'s own URLs)')
    print("  Pre-game post (needs only upcoming.json):")
    print(f'       python "{SCRIPT}" pregame --upcoming upcoming.json --recent recent.json --next --out pregame.json')
    print("No browser tool: ask the user to copy the game page (or the Upcoming tab) from the FCAA app and "
          "paste it; save it as pasted.txt and use --page-text pasted.txt.")
    return 0


def previous_season(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}-{(start + 1) % 100:02d}"


def run_list(args, cfg) -> int:
    """Näädät's games in recent-games (and upcoming-games), newest first."""
    recent = games_in(load_json(args.recent, "--recent"), args.recent)
    upcoming = games_in(load_json(args.upcoming, "--upcoming"), args.upcoming) if args.upcoming else []
    rows, seen = [], set()
    for g in recent + upcoming:
        gid = g.get("id")
        if not has_us(g, cfg) or (gid and gid in seen):
            continue
        seen.add(gid)
        day, tm = game_when(g)
        h, a = team_of(g, "home"), team_of(g, "away")
        us_home = is_us_team(h.get("name"), h.get("id"), cfg)
        us, them = (h, a) if us_home else (a, h)
        if fg.is_count(us.get("score")) and fg.is_count(them.get("score")) and g.get("status") != "not-started":
            score = f"{us['score']}-{them['score']}"
        else:
            score = "-"
        when = f"{fg.fi_date(day)} {tm}".strip() if day else "no date yet"
        line = (f"{gid or '(no id)'}  {when}  {cfg['display_name']} {score}  {g.get('status') or '?'}  "
                f"{'vs' if us_home else 'at'} {them.get('name')} ({'home' if us_home else 'away'})")
        rows.append(((day or dt.date.max), tm or "99:99", line, g.get("status") == "finished"))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    for r in rows:
        print(r[2])
    played = [r for r in rows if r[3]]
    dated = [game_when(g)[0] for g in recent if game_when(g)[0]]
    seasons = [season_of(d) for d in dated]
    season = max(set(seasons), key=seasons.count) if seasons else season_of(fg.helsinki_now().date())
    if not played:
        prev = previous_season(season)
        print(f"No finished Näädät game in {args.recent} (season {season}; games in the file: {len(recent)}). "
              f"Näädät's latest result is in an earlier season. Open "
              f"{API}/recent-games?season={prev}&limit={RECENT_LIMIT} in the browser (after {STATS_URL}), "
              "save it as recent.json and run list again; "
              f'python "{SCRIPT}" urls --season {prev} prints all the URLs for that season.')
        return EXIT_ERROR if not rows else 0
    if len(recent) == 20:
        print(f"note: {args.recent} has exactly 20 games; without ?limit={RECENT_LIMIT} recent-games returns "
              "only the latest 20, so older games may be missing.")
    newest = played[0][2].split()[0]
    past = "" if season == season_of(fg.helsinki_now().date()) else f" --season {season}"
    print(f"Newest finished game: {newest}. For its result post: "
          f'python "{SCRIPT}" urls --game-id {newest}{past}  (prints the URLs of that game)')
    return 0


def run_try(args) -> int:
    url = f"{API}/seasons"
    req = urllib.request.Request(url, headers={"accept": "application/json",
                                               "user-agent": "naadat-game-posts/1.0 (+fcaa.py try)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
    except (urllib.error.URLError, OSError) as exc:
        print(f"Could not reach {ORIGIN}: {exc}\nThis sandbox has no route to fcaahockey.app. "
              "Use a browser tool (fcaa.py urls) or ask the user to paste the game page.", file=sys.stderr)
        return EXIT_ERROR
    if code == 429 or "Vercel Security Checkpoint" in body or "<html" in body[:500].lower():
        print(f"{ORIGIN} answered HTTP {code} with its bot check (Vercel Security Checkpoint): plain HTTP "
              "cannot read FCAA data, and this script does not try to get around the check.\n"
              "Next step, pick one:\n"
              "  - with a browser tool: python fcaa.py urls [--game-id ID] [--season S] and open those URLs\n"
              "  - without: ask the user to open fcaahockey.app/stats/mens, tap Recent and the game (pre-game:\n"
              "    the Upcoming tab), select all, copy and paste it here; then fcaa.py result|pregame --page-text",
              file=sys.stderr)
        return EXIT_ERROR
    try:
        json.loads(body)
    except ValueError:
        print(f"{url} answered HTTP {code} but not with JSON; use a browser or a paste (fcaa.py urls).",
              file=sys.stderr)
        return EXIT_ERROR
    print(f"{ORIGIN} answers plain HTTP from here (HTTP {code}).")
    if not args.save_dir:
        print("Pass --save-dir DIR to save the JSON files listed by `fcaa.py urls`.")
        return 0
    os.makedirs(args.save_dir, exist_ok=True)
    for label, u, save in api_urls(args.game_id, args.season):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=req.headers), timeout=30) as resp:
                data = resp.read()
            json.loads(data.decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"could not get {u}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        path = os.path.join(args.save_dir, save)
        with open(path, "wb") as f:
            f.write(data)
        print(f"saved {path}  ({label})")
    return 0


# --------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    fg.setup_streams()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("result", help="post-game game.json from FCAA JSON or a pasted game page")
    r.add_argument("--events", help="games/<id>/events response")
    r.add_argument("--recent", help="recent-games response (?limit=500, plus ?season= for a past season)")
    r.add_argument("--game", help="one game record (an element of recent-games), instead of --recent")
    r.add_argument("--game-id", help="FCAA game id (game:...); default: the one in the events file")
    r.add_argument("--rosters", help="games/<id>/rosters response (fills assist names)")
    r.add_argument("--goalies", help="games/<id>/goalie-periods response (goalie saves, shots)")
    r.add_argument("--upcoming", help="upcoming-games response (only for the photo rotation index)")
    r.add_argument("--page-text", help="the game page copied from the FCAA app (select all, copy, paste)")
    r.add_argument("--out", default="game.json")

    p = sub.add_parser("pregame", help="pre-game pregame.json from FCAA JSON or a pasted Upcoming tab")
    p.add_argument("--upcoming", help="upcoming-games response")
    p.add_argument("--recent", help="recent-games response of this season (only for the photo rotation index)")
    p.add_argument("--page-text", help="the Upcoming tab copied from the FCAA app")
    p.add_argument("--game-id", help="FCAA game id (JSON input only)")
    p.add_argument("--next", action="store_true", help="the next Näädät game not started yet (default)")
    p.add_argument("--date", help="YYYY-MM-DD: the Näädät game on this date")
    p.add_argument("--out", default="pregame.json")

    li = sub.add_parser("list", help="Näädät's games in recent-games (and upcoming-games), newest first")
    li.add_argument("--recent", required=True, help="recent-games response")
    li.add_argument("--upcoming", help="upcoming-games response")

    u = sub.add_parser("urls", help="print the URLs a browser-equipped assistant opens, in order")
    u.add_argument("--game-id")
    u.add_argument("--season", help="past season id, e.g. 2025-26")

    t = sub.add_parser("try", help="one plain HTTP request; explains what to do when the bot check blocks it")
    t.add_argument("--game-id")
    t.add_argument("--season")
    t.add_argument("--save-dir")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return EXIT_ERROR
    try:
        cfg = config()
        if args.cmd == "urls":
            return run_urls(args)
        if args.cmd == "list":
            return run_list(args, cfg)
        if args.cmd == "try":
            return run_try(args)
        if args.cmd == "result":
            if args.page_text:
                if args.events or args.recent or args.game:
                    raise Stop("Use either --page-text or the JSON files (--events with --recent/--game).")
                return run_result_page(args, cfg)
            if not args.events:
                raise Stop("result needs --events (with --recent or --game) or --page-text. "
                           "`python fcaa.py urls` lists what to fetch.")
            return run_result_json(args, cfg)
        if args.cmd == "pregame":
            if args.game_id and args.date:
                raise Stop("Pick the game with --game-id or --date, not both.")
            if args.page_text:
                return run_pregame_page(args, cfg)
            if not args.upcoming:
                raise Stop("pregame needs --upcoming upcoming.json or --page-text (the copied Upcoming tab).")
            return run_pregame_json(args, cfg)
    except Stop as stop:
        print(stop.message, file=sys.stderr)
        return stop.code
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
