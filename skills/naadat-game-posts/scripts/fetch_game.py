#!/usr/bin/env python3
"""Fetch a Näädät game from the Finnish Ice Hockey Association results service
(tulospalvelu.leijonat.fi): a finished game as game.json (post-game), or an
upcoming one as pregame.json (pre-game: kick-off, both teams, logos, rink).

Standard library only, so it runs in any sandbox that has Python 3.8+.
Only III-divisioona games are on the results service. FCAA and other games are
entered by hand from references/manual_game_template.json (post-game) or
references/manual_pregame_template.json (pre-game); check the file with --validate.

Examples:
  python fetch_game.py                      # latest finished game -> game.json
  python fetch_game.py --list               # all our games of the season, newest first
  python fetch_game.py --game-id 2720857    # a specific game (season found automatically)
  python fetch_game.py --date 2026-10-04    # the game played on that date
  python fetch_game.py --game-url "https://tulospalvelu.leijonat.fi/game?season=2027&gameid=2720857"
  python fetch_game.py --next               # next game not started yet -> pregame.json
  python fetch_game.py --pregame --game-id 2720880   # pre-game data of a scheduled game
  python fetch_game.py --pregame --date 2026-10-18
  python fetch_game.py --validate game.json # check a hand-written game.json or pregame.json

The summary line reads Näädät first, for example
  Wrote game.json: 10.10.2026 Näädät 4-28 KHK (L, away), 32 goals in the log.
  Wrote pregame.json: SUN 18.10.2026 21:10 Näädät vs HC Ludwig (home) at Kaarela 1

Exit codes:
  0  file written, or --validate found nothing to fix (it may still print a
     note, e.g. a partial scorer list, or a WARNING about a missing logo_file
     alone: pass both on to the user)
  1  error (no such game, a pre-game for a game already played, unreadable input,
     no network; the message says what to do)
  2  no result yet. Either the game has not been played (its face-off is still
     ahead): make the pre-game post with --pregame instead; --allow-unfinished
     does not change this. Or the game has started but is not final yet (also
     without --game-id: a newer game than the last final one has been played):
     wait for the official result, or pass --allow-unfinished to use it anyway
  3  --validate printed a WARNING other than a missing logo_file. Fix the file
     or ask the user, then run --validate again. Do not render before it exits 0

If this sandbox cannot reach the internet, a game id is needed (the user can
copy the game link from tulospalvelu.leijonat.fi). Get the raw JSON text of the
game report some other way, for example by asking the user to open it in a
browser and paste or upload it (about 20 kB; a summary is not enough):
  https://tulospalvelu.leijonat.fi/gamereport/getgamereportdata?gameid=<ID>&season=<SEASON>
  python fetch_game.py --report-json report.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://tulospalvelu.leijonat.fi"
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.abspath(__file__)
SKILL_DIR = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(SKILL_DIR, "assets", "brand", "team.json")
REPORT_TEMPLATE = BASE + "/gamereport/getgamereportdata?gameid=<GAME_ID>&season=<SEASON>"
MANUAL_HINT = ("Only III-divisioona games are on tulospalvelu.leijonat.fi. For FCAA or other games, "
               "fill references/manual_game_template.json by hand (SKILL.md step 1, FCAA) and check it "
               "with --validate.")

EXIT_ERROR, EXIT_NOT_FINAL, EXIT_WARNINGS = 1, 2, 3
FINAL_STATUSES = {2, 94}  # the site's own JS treats these as a final result
FINISH_TYPES = {1: "REG", 2: "OT", 3: "SO"}
# GoalType is a space-separated list of codes, e.g. "YV TM". Only codes whose
# meaning is certain are translated:
#   YV/YV2 ylivoima (power play), AV/AV2 alivoima (short-handed), TM tyhjä maali
#   (empty net), RL rangaistuslaukaus (scored penalty shot), RL0 missed penalty
#   shot (not a goal), VL voittolaukaus (shootout winner), TV tasavoima (even
#   strength), SR siirretty rangaistus (delayed penalty). IM is unknown.
# "type" is only ever '', PP, SH or EN; every other code (TV, SR, RL, VL, IM)
# gives '' and stays readable in "type_raw".
# Priority: EN wins over PP/SH, so "YV TM" and "AV TM" are EN. The graphic has room
# for one tag, and an empty net is what the goal looked like.
GOAL_TYPES = {"YV": "PP", "YV2": "PP", "AV": "SH", "AV2": "SH", "TM": "EN"}
GOAL_TYPE_PRIORITY = ("EN", "PP", "SH")
MISSED_PENALTY_SHOT = "RL0"
NAME_PARTICLES = {"von", "van", "de", "af", "der", "den", "da", "du", "zu",
                  "la", "le", "del", "di", "dos", "ten", "ter"}
# Values from references/manual_game_template.json that must not reach a post.
PLACEHOLDER_TEAM_NAMES = {"opponent"}
PLACEHOLDER_PERSON_WORDS = {"esimerkki", "malli", "kuvitteellinen"}
PLACEHOLDER_FULL_NAMES = {"first last"}
# The templates ship these literal placeholders; --validate warns while one is left.
PLACEHOLDER_DATE = "YYYY-MM-DD"
PLACEHOLDER_TIME = "HH:MM"
GAME_SCHEMA = "naadat-game-posts/game@1"
PREGAME_SCHEMA = "naadat-game-posts/pregame@1"
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def weekday_code(day: dt.date) -> str:
    """English three-letter weekday in capitals, e.g. 'SUN'."""
    return WEEKDAYS[day.weekday()]


class NetError(Exception):
    def __init__(self, url: str, reason: object):
        super().__init__(f"{url}: {reason}")
        self.url, self.reason = url, reason


class Stop(Exception):
    """A clean stop with a message for the user and an exit code."""

    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.message, self.code = message, code


def setup_streams() -> None:
    """Never crash on a print of 'Näädät', whatever the console encoding is."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def helsinki_now() -> dt.datetime:
    """Current Helsinki wall-clock time as a naive datetime, comparable with the
    kick-off times the service publishes."""
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return dt.datetime.now(ZoneInfo("Europe/Helsinki")).replace(tzinfo=None)
    except Exception:
        # Python 3.8 or no tz database: EET is UTC+2, EEST UTC+3. +3 can call a
        # game started up to an hour early in winter, which only delays a post.
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).replace(tzinfo=None)


def helsinki_today() -> dt.date:
    return helsinki_now().date()


def season_for(day: dt.date) -> int:
    # Leijonat numbers a season by the year it ends in: 2026-27 is 2027.
    return day.year + 1 if day.month >= 6 else day.year


def fi_date(iso_or_date) -> str:
    """'2026-10-04' -> '4.10.2026'. Anything unparseable is returned as given."""
    try:
        d = iso_or_date if isinstance(iso_or_date, dt.date) else dt.datetime.strptime(str(iso_or_date), "%Y-%m-%d").date()
    except ValueError:
        return str(iso_or_date)
    return f"{d.day}.{d.month}.{d.year}"


def network_help(url: str, exc: object, game_id: int = None, season: int = None) -> str:
    if "/gamereport/getgamereportdata" in url:
        report = url
    elif game_id and season:
        report = report_url(season, game_id)
    elif game_id:
        report = None
    else:
        report = REPORT_TEMPLATE
    lines = [
        f"Could not reach {url}",
        f"  {exc}",
        "This sandbox cannot get data from tulospalvelu.leijonat.fi.",
    ]
    if game_id:
        lines.append(f"Offline route for game {game_id}: get the raw JSON text of this game-report URL")
        lines.append("(a browser shows it; a summarising web-fetch tool is not enough), save it as report.json:")
        if report:
            lines.append(f"  {report}")
            lines.append("  (if it shows \"GamesUpdate\":null, the season is wrong: try the previous year)")
        else:
            current = season_for(helsinki_today())
            lines.append(f"  {report_url(current, game_id)}")
            lines.append(f"  (a game of last season needs season={current - 1}; the season is the year it ends in)")
    else:
        lines += [
            "Offline route: a game id is needed. Ask the user for the game link from",
            "tulospalvelu.leijonat.fi (it contains gameid= and season=, season 2026-27 is 2027),",
            "then get the raw JSON text of this game-report URL (a browser shows it; a",
            "summarising web-fetch tool is not enough), save it as report.json:",
            f"  {report}",
        ]
    lines += [
        f'and run:  python "{SCRIPT}" --report-json report.json --out game.json',
        "Only the game-report URL works with --report-json; the schedule URLs",
        "(serie/helpers/getsubseries, helpers/getgames) are not its input.",
    ]
    return "\n".join(lines)


def get_json(path: str, params: dict) -> object:
    query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{BASE}/{path}?{query}"
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "naadat-game-posts/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise NetError(url, exc)
    try:
        return json.loads(body.decode("utf-8-sig"))
    except ValueError as exc:
        raise NetError(url, f"the response was not JSON ({exc}); a proxy or block page may be in the way")


def report_url(season: int, game_id: int) -> str:
    return f"{BASE}/gamereport/getgamereportdata?gameid={game_id}&season={season}"


def report_game(report) -> dict:
    """GamesUpdate[0] of a report, or None when the report has no game."""
    if not isinstance(report, dict):
        return None
    gu = report.get("GamesUpdate")
    if not isinstance(gu, list) or not gu or not isinstance(gu[0], dict) or not gu[0].get("Id"):
        return None
    return gu[0]


def discover_games(cfg: dict, season: int) -> list:
    """Every game of our club in every sub-series of the configured level and district.

    Sub-series change during a season (alkusarja -> jatkosarja), so they are
    listed fresh on every run and matched on the club's association id, which
    stays stable when the per-series team id does not.
    """
    lt = cfg["leijonat"]
    subseries = get_json("serie/helpers/getsubseries",
                         {"season": season, "levelid": lt["level_id"], "districtid": lt["district_id"]})
    found = []
    for sub in subseries if isinstance(subseries, list) else []:
        if not isinstance(sub, dict) or "subSerieId" not in sub:
            continue
        blocks = get_json("helpers/getgames", {
            "dwl": 0, "season": season, "subSerieId": sub["subSerieId"], "teamid": 0,
            "districtid": 0, "gamedays": 3, "dog": "", "levelid": -1,
        })
        found.extend(team_games(blocks, lt["association_id"]))
    return found


def team_games(blocks, association_id: int) -> list:
    out = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        for g in block.get("Games") or []:
            if not isinstance(g, dict) or not g.get("GameID") or not g.get("GameDate"):
                continue
            if association_id in (g.get("HomeAssociationID"), g.get("AwayAssociationID")):
                out.append(g)
    return out


def game_datetime(g: dict) -> dt.datetime:
    """Kick-off as naive Helsinki local time, as the service publishes it."""
    try:
        return dt.datetime.strptime(f"{g['GameDate']} {g.get('GameTime') or '00:00:00'}", "%d.%m.%Y %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        raise Stop(f"Game {g.get('GameID')} has an unreadable date {g.get('GameDate')!r} "
                   f"{g.get('GameTime')!r} (expected DD.MM.YYYY HH:MM:SS).")


def is_final(g: dict) -> bool:
    return g.get("GameStatus") in FINAL_STATUSES and not g.get("DeniedResults")


def _cap(word: str) -> str:
    """'SILLANPÄÄ' -> 'Sillanpää', 'ELONEN-KULMALA' -> 'Elonen-Kulmala', "O'BRIEN" -> "O'Brien".
    A dotted capital I (Turkish 'İ') lowers to a plain i, not i plus a combining dot."""
    word = word.replace("İ", "I").replace("̇", "")
    return "".join(p[:1].upper() + p[1:].lower() for p in re.split(r"([-'’])", word))


def person(raw: str) -> dict:
    """'SILLANPÄÄ Otto' -> first 'Otto', last 'Sillanpää'. Surname tokens are upper case.

    "display" is the form to print when only a last name is wanted: the last name,
    or the short form when two teammates share it (set per team in normalize)."""
    raw = (raw or "").strip() if isinstance(raw, str) else ""
    if not raw:
        return {"full": "", "short": "", "last": "", "first": "", "display": ""}
    parts = raw.split()
    last = [p for p in parts if p.isupper() and len(p) > 1] or parts[:1]
    first = [p for p in parts if p not in last]
    words = []
    for i, w in enumerate(last):
        if w.lower() in NAME_PARTICLES and i < len(last) - 1:
            words.append(w.lower())  # VON SCHOULTZ -> von Schoultz, DE LA CRUZ -> de la Cruz
        else:
            words.append(_cap(w))
    last_s = " ".join(words)
    first_s = " ".join(first)
    full = f"{first_s} {last_s}".strip()
    if first:
        # Finnish convention: Veli-Matti -> V-M.
        initials = "-".join(x[:1] for x in first[0].split("-") if x)
        short = f"{initials}. {last_s}"
    else:
        short = last_s
    return {"full": full, "short": short, "last": last_s, "first": first_s, "display": last_s}


def disambiguate_names(name_objs: list) -> None:
    """Resolve clashes among the name objects of ONE team. Mutates in place.

    short:   two players sharing a short form ('J. Taivalmaa' for Jaakko and
             Justus) both get their full name as the short form.
    display: the last name, or the (already disambiguated) short form when two
             players share the last name ('M. Grundström', 'Jaakko Taivalmaa')."""
    shorts, lasts = {}, {}
    for n in name_objs:
        who = n.get("full") or n.get("short") or n.get("last")
        if n.get("short"):
            shorts.setdefault(n["short"], set()).add(who)
        if n.get("last"):
            lasts.setdefault(n["last"], set()).add(who)
    for n in name_objs:
        if n.get("short") and len(shorts[n["short"]]) > 1 and n.get("full"):
            n["short"] = n["full"]
    for n in name_objs:
        last = n.get("last") or ""
        n["display"] = (n.get("short") or last) if last and len(lasts[last]) > 1 else (last or n.get("full") or "")


def mmss(seconds) -> str:
    """Leijonat logs elapsed game seconds; hockey summaries print them as mm:ss of game time."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def split_pair(text):
    try:
        a, b = str(text).split("-", 1)
        return int(a), int(b)
    except (ValueError, AttributeError):
        return None


def goal_type(raw_type: str) -> str:
    tags = {GOAL_TYPES[t] for t in raw_type.split() if t in GOAL_TYPES}
    return next((t for t in GOAL_TYPE_PRIORITY if t in tags), "")


def result_code(us_goals, them_goals, finish: str, final: bool) -> str:
    """W, L, OTW, OTL, SOW, SOL, or '' when the game is not final or is tied."""
    if not final or us_goals is None or them_goals is None or us_goals == them_goals:
        return ""
    won = us_goals > them_goals
    return ("W" if won else "L") if finish == "REG" else finish + ("W" if won else "L")


def partly_missing_saves(summary: dict, saves_rows: list) -> bool:
    """True when a played regulation period (1-3) shows saves '0-0' although goals
    were scored in it. The report lists one row per period and the total last."""
    goal_rows = [r for r in summary.get("PeriodGoals") or [] if isinstance(r, dict)]
    try:
        played = int(summary.get("PlayedPeriods") or 0)
    except (TypeError, ValueError):
        played = 0
    played = played or max(len(goal_rows) - 1, 0)
    for i in range(min(played, 3, len(goal_rows) - 1, len(saves_rows) - 1)):
        goals = split_pair(goal_rows[i].get("Goals"))
        saves = split_pair(saves_rows[i].get("Saves"))
        if saves == (0, 0) and goals and goals != (0, 0):
            return True
    return False


def normalize(g: dict, report: dict, cfg: dict, season: int) -> dict:
    lt = cfg["leijonat"]
    us_home = g["HomeAssociationID"] == lt["association_id"]
    gu = report_game(report) or {}
    home_id = (gu.get("HomeTeam") or {}).get("Id", g["HomeTeam"])

    def side(prefix: str, is_us: bool) -> dict:
        img = g.get(f"{prefix}Img") or ""
        return {
            "name": cfg["display_name"] if is_us else g[f"{prefix}TeamAbbrv"],
            "source_name": g[f"{prefix}TeamAbbrv"],
            "association_id": g[f"{prefix}AssociationID"],
            "goals": g[f"{prefix}Goals"],
            "is_us": is_us,
            "logo_url": f"{BASE}/images/associations/weblogos/200x200/{img}" if img else None,
        }

    home, away = side("Home", us_home), side("Away", not us_home)
    us, them = (home, away) if us_home else (away, home)
    finish = FINISH_TYPES.get(g.get("FinishedType"), "REG")
    final = is_final(g)
    result = result_code(us["goals"], them["goals"], finish, final)

    periods = []
    ps = g.get("PeriodSummary") or {}
    pg = ps.get("PeriodGoals") or {}
    for i, (h, a) in enumerate(zip(pg.get("Home", []), pg.get("Away", [])), start=1):
        label = str(i) if i <= 3 else ("OT" if i == 4 else "SO")
        periods.append({"label": label, "home": h, "away": a})

    goals = []
    prev_score = (0, 0)
    for e in report.get("GameLogsUpdate") or []:
        if not isinstance(e, dict) or e.get("Type") != "Goal":
            continue
        raw_type = " ".join(str(e.get("GoalType") or "").upper().split())
        score = (e.get("HomeTeamGoals"), e.get("AwayTeamGoals"))
        if MISSED_PENALTY_SHOT in raw_type.split():
            continue  # a missed penalty shot is logged as a "Goal" event but is not one
        if None not in score:
            if score == prev_score:
                continue  # the score did not change, so this event is not a goal
            prev_score = score
        is_us_goal = (e.get("TeamId") == home_id) == us_home
        assists = [person(n) for n in (e.get("FirstAssistName"), e.get("SecondAssistName"))
                   if isinstance(n, str) and n.strip()]
        period = e.get("Period")
        if "VL" in raw_type.split():
            # The shootout winner is logged as period 5, 6, or even 2 in short games.
            # game.json always uses period 5 for it, which every consumer skips.
            period = 5
        goals.append({
            "period": period,
            "time": mmss(e.get("GameTime")),
            "team": "us" if is_us_goal else "them",
            "scorer": person(e.get("ScorerName")),
            "jersey": e.get("ScorerJersey"),
            "assists": assists,
            "type": goal_type(raw_type),
            "type_raw": raw_type,
            "score_after": f"{score[0]}-{score[1]}",
        })

    goalies = []
    for team in report.get("GoalkeeperSummary") or []:
        if not isinstance(team, dict):
            continue
        for gk in team.get("TeamGoalkeepers") or []:
            if not isinstance(gk, dict):
                continue
            total = next((s.get("Saves") for s in gk.get("GkSaves") or []
                          if isinstance(s, dict) and s.get("Period") == 0), None)
            goalies.append({"team_name": team.get("TeamName"), "name": person(gk.get("GkName")), "saves": total,
                            "is_us": team.get("TeamName") == us["source_name"]})

    # Name clashes are resolved per team over everyone named in this game.
    for team, is_us in (("us", True), ("them", False)):
        objs = []
        for gl in goals:
            if gl["team"] == team:
                objs.append(gl["scorer"])
                objs.extend(gl["assists"])
        objs.extend(gk["name"] for gk in goalies if gk["is_us"] == is_us)
        disambiguate_names(objs)

    def leaders(team: str) -> list:
        table = {}
        for gl in goals:
            if gl["team"] != team or gl["period"] == 5:
                continue
            key = gl["scorer"]["full"]
            table.setdefault(key, {"name": gl["scorer"], "goals": 0, "assists": 0})["goals"] += 1
            for a in gl["assists"]:
                table.setdefault(a["full"], {"name": a, "goals": 0, "assists": 0})["assists"] += 1
        rows = list(table.values())
        rows.sort(key=lambda r: (-(r["goals"] + r["assists"]), -r["goals"], r["name"]["last"]))
        return rows

    shots = None
    summary = report.get("PeriodSummary") or {}
    saves_rows = [s for s in summary.get("PeriodSaves") or [] if isinstance(s, dict)]
    saves_total = split_pair(saves_rows[-1].get("Saves")) if saves_rows else None
    # A game that is not final, or whose saves were never entered ('0-0'), has no
    # shot count: shots on goal would be just the score. The same holds when only
    # some periods were entered: a played regulation period with saves '0-0' but
    # goals in it means its saves are missing, so the total is too low.
    if saves_total and saves_total != (0, 0) and partly_missing_saves(summary, saves_rows):
        saves_total = None
    if final and saves_total and saves_total != (0, 0):
        home_saves, away_saves = saves_total
        # The shootout winner's extra goal is in the score but was not a shot on goal
        # (the saves total leaves the shootout out too).
        home_so = 1 if finish == "SO" and home["goals"] > away["goals"] else 0
        away_so = 1 if finish == "SO" and away["goals"] > home["goals"] else 0
        home_shots = away_saves + home["goals"] - home_so
        away_shots = home_saves + away["goals"] - away_so
        shots = {"us": home_shots if us_home else away_shots, "them": away_shots if us_home else home_shots}

    when = game_datetime(g)
    return {
        "schema": GAME_SCHEMA,
        "source": "leijonat",
        "game_id": g["GameID"],
        "season": season,
        "url": f"{BASE}/game?season={season}&gameid={g['GameID']}&lang=fi",
        "competition": cfg.get("competition_label") or g.get("LevelName", ""),
        "series": g.get("SubSerieName", ""),
        "date": when.date().isoformat(),
        "time": when.strftime("%H:%M"),
        **arena_fields(gu.get("Arena"), g.get("ScheduleRinkName") or (g.get("RinkName") if not gu else "")),
        "attendance": g.get("Spectator") or None,  # 0 means not recorded
        "finish": finish,
        "result": result,
        "home": home,
        "away": away,
        "us_side": "home" if us_home else "away",
        "periods": periods,
        "goals": goals,
        "scorers_us": leaders("us"),
        "scorers_them": leaders("them"),
        "shots": shots,
        "goalies": goalies,
        "status": "final" if final else "not_final",
    }


def game_from_report(report: dict, schedule_entry: dict = None) -> dict:
    """Build the game entry from a game report. Both the online and the offline path
    use this, so they write the same game.json. A schedule entry, when there is one,
    only fills fields the report leaves empty and contributes DeniedResults."""
    s = schedule_entry or {}
    gu = report_game(report) or {}
    home, away = gu.get("HomeTeam") or {}, gu.get("AwayTeam") or {}
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise Stop("The game report has an unexpected shape: HomeTeam/AwayTeam are not objects.")
    ps = report.get("PeriodSummary") or {}
    if not isinstance(ps, dict):
        ps = {}
    rows = [r for r in ps.get("PeriodGoals") or [] if isinstance(r, dict)]
    pairs = [split_pair(r.get("Goals")) for r in rows]
    try:
        played = int(ps.get("PlayedPeriods") or 0)
    except (TypeError, ValueError):
        played = 0
    played = played or max(len(pairs) - 1, 0)
    pairs = [p for p in pairs[:played] if p]
    spectators = gu.get("Spectators")
    return {
        "GameID": gu.get("Id"),
        "GameDate": gu.get("StartDate") or s.get("GameDate"),
        "GameTime": gu.get("StartTime") or s.get("GameTime") or "00:00:00",
        "GameStatus": gu.get("GameStatus"),
        "FinishedType": gu.get("FinishedType"),
        "DeniedResults": s.get("DeniedResults", 0),
        "HomeTeam": home.get("Id"), "AwayTeam": away.get("Id"),
        "HomeGoals": home.get("Goals"), "AwayGoals": away.get("Goals"),
        "HomeTeamAbbrv": home.get("Name") or s.get("HomeTeamAbbrv", ""),
        "AwayTeamAbbrv": away.get("Name") or s.get("AwayTeamAbbrv", ""),
        "HomeAssociationID": home.get("AssId"), "AwayAssociationID": away.get("AssId"),
        "HomeImg": home.get("Image") or s.get("HomeImg", ""), "AwayImg": away.get("Image") or s.get("AwayImg", ""),
        "RinkName": gu.get("Arena") or s.get("RinkName", ""),
        "ScheduleRinkName": s.get("RinkName", ""),
        "Spectator": spectators if spectators is not None else s.get("Spectator"),
        "LevelName": gu.get("LevelName") or s.get("LevelName", ""),
        "SubSerieName": gu.get("SubSerieName") or s.get("SubSerieName", ""),
        "PeriodSummary": {"PlayedPeriods": len(pairs),
                          "PeriodGoals": {"Home": [p[0] for p in pairs], "Away": [p[1] for p in pairs]}},
    }


def clean_rink(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def arena_fields(report_arena, schedule_rink) -> dict:
    """The rink, the same way for post-game and pre-game: the report's Arena (the
    rink's full name, e.g. 'Ilmatar Areena') when it has one, else the schedule's
    RinkName (e.g. 'Matinkylä I'). When both exist and differ, the schedule's name
    is kept as arena_short so the user can check which one fans know."""
    report_arena, schedule_rink = clean_rink(report_arena), clean_rink(schedule_rink)
    out = {"arena": report_arena or schedule_rink}
    if report_arena and schedule_rink and report_arena.casefold() != schedule_rink.casefold():
        out["arena_short"] = schedule_rink
    return out


def arena_text(data: dict) -> str:
    """'Ilmatar Areena (schedule: Matinkylä I)', or the arena alone."""
    arena = clean_rink(data.get("arena"))
    short = clean_rink(data.get("arena_short"))
    return f"{arena} (schedule: {short})" if arena and short else arena


def load_json_file(path: str, what: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except OSError as exc:
        raise Stop(f"Could not open {what} {path}: {exc}")
    except ValueError as exc:
        raise Stop(f"{what} {path} is not valid JSON: {exc}\n"
                   "Save the raw response text exactly as the browser shows it.")


def load_report(path: str) -> dict:
    report = load_json_file(path, "--report-json")
    if report_game(report) is None:
        raise Stop(f"{path} is not a game report (no GamesUpdate list with a game Id). --report-json takes "
                   f"the response of\n  {REPORT_TEMPLATE}\n"
                   "(the schedule URLs getsubseries/getgames are not its input).")
    return report


def parse_game_url(url: str):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(q["gameid"][0]), (int(q["season"][0]) if "season" in q else None)
    except (KeyError, ValueError, IndexError):
        raise Stop(f"Could not read gameid from {url}")


def parse_date(text: str) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise Stop(f"--date must be YYYY-MM-DD, for example 2026-10-18 (got {text!r}).")


def us_view(g: dict, cfg: dict):
    us_home = g["HomeAssociationID"] == cfg["leijonat"]["association_id"]
    us_goals, them_goals = (g.get("HomeGoals"), g.get("AwayGoals")) if us_home else (g.get("AwayGoals"), g.get("HomeGoals"))
    return us_home, us_goals, them_goals


def describe(g: dict, cfg: dict) -> str:
    """One --list row. The score is Näädät first, then the result from our side."""
    us_home, us_goals, them_goals = us_view(g, cfg)
    final = is_final(g)
    state = "final" if final else ("scheduled" if g.get("GameStatus") == 0 else f"status {g.get('GameStatus')}")
    score = f"{us_goals}-{them_goals}" if g.get("GameStatus") else "-"
    res = result_code(us_goals, them_goals, FINISH_TYPES.get(g.get("FinishedType"), "REG"), final)
    venue = "home" if us_home else "away"
    opp = g.get("AwayTeamAbbrv" if us_home else "HomeTeamAbbrv") or "?"
    return f"{g['GameID']}  {g['GameDate']} {(g.get('GameTime') or '')[:5]}  {venue:4}  vs {opp:<16} {score:>6} {res:<3}  {state}"


def short_describe(g: dict, cfg: dict) -> str:
    us_home = g.get("HomeAssociationID") == cfg["leijonat"]["association_id"]
    opp = g.get("AwayTeamAbbrv" if us_home else "HomeTeamAbbrv") or "?"
    return f"game {g['GameID']}, {game_datetime(g).strftime('%d.%m.%Y %H:%M')} {'home' if us_home else 'away'} vs {opp}"


def logged_goals(data: dict) -> int:
    return sum(1 for g in data.get("goals") or [] if isinstance(g, dict) and g.get("period") != 5)


def summary_line(data: dict, verb: str, path: str, us_side: str = None) -> str:
    """'Wrote game.json: 10.10.2026 Näädät 4-28 KHK (L, away), 32 goals in the log.'"""
    home = data.get("home") if isinstance(data.get("home"), dict) else {}
    away = data.get("away") if isinstance(data.get("away"), dict) else {}
    side = us_side or data.get("us_side")
    result = data.get("result") or "no result"
    if side in ("home", "away"):
        us, them = (home, away) if side == "home" else (away, home)
        teams = f"{us.get('name')} {us.get('goals')}-{them.get('goals')} {them.get('name')} ({result}, {side})"
    else:
        teams = (f"home {home.get('name')} {home.get('goals')}-{away.get('goals')} away {away.get('name')} "
                 f"({result}, Näädät side unknown)")
    venue = ""
    if data.get("source") == "manual":
        # Hand-entered files: show the arena, so the user confirms it too.
        venue = f" at {arena_text(data)}" if arena_text(data) else " (no arena)"
    elif data.get("arena_short"):
        venue = f" at {arena_text(data)}"
    return f"{verb} {path}: {fi_date(data.get('date'))} {teams}{venue}, {logged_goals(data)} goals in the log."


def pregame_line(data: dict, verb: str, path: str, us_side: str = None) -> str:
    """'Wrote pregame.json: SUN 18.10.2026 21:10 Näädät vs HC Ludwig (home) at Kaarela 1'"""
    home = data.get("home") if isinstance(data.get("home"), dict) else {}
    away = data.get("away") if isinstance(data.get("away"), dict) else {}
    side = us_side or data.get("us_side")
    try:
        weekday = weekday_code(dt.datetime.strptime(str(data.get("date")), "%Y-%m-%d").date()) + " "
    except ValueError:
        weekday = ""
    when = f"{weekday}{fi_date(data.get('date'))} {data.get('time') or '(no time)'}"
    if side in ("home", "away"):
        us, them = (home, away) if side == "home" else (away, home)
        teams = f"{us.get('name')} vs {them.get('name')} ({side})"
    else:
        teams = f"home {home.get('name')} vs away {away.get('name')} (Näädät side unknown)"
    arena = arena_text(data)
    return f"{verb} {path}: {when} {teams} {'at ' + arena if arena else '(no arena)'}"


def goal_log_warning(data: dict):
    logged = logged_goals(data)
    try:
        total = int(data["home"]["goals"]) + int(data["away"]["goals"])
    except (KeyError, TypeError, ValueError):
        return None
    if data.get("finish") == "SO":
        total -= 1  # the shootout winner counts in the score, not in the log
    if logged != total:
        return (f"WARNING: the goal log has {logged} goals but the score adds up to {total}. "
                "Scorer lists may be incomplete; tell the user.")
    return None


def rink_note(data: dict) -> None:
    if data.get("arena_short"):
        print(f"note: the game report names the rink {data['arena']!r}, the schedule {data['arena_short']!r}; "
              "arena is the report's, arena_short the schedule's. Tell the user which one the post shows.")


_SCHEDULE_CACHE = {}


def add_season_index(data: dict) -> None:
    """Position of the game in our season schedule (0 = first game), used to rotate photos.

    Best effort: the post works without it (the renderer then rotates by date), so a
    schedule that cannot be reached only skips the field.
    """
    if data.get("season_index") is not None or not data.get("game_id") or not data.get("season"):
        return
    season = data["season"]
    try:
        if season not in _SCHEDULE_CACHE:
            games = discover_games(load_config(), season)
            games.sort(key=game_datetime)
            _SCHEDULE_CACHE[season] = [g["GameID"] for g in games]
        ids = _SCHEDULE_CACHE[season]
    except BaseException:  # noqa: BLE001  network or shape problem: rotate by date instead
        return
    if data["game_id"] in ids:
        data["season_index"] = ids.index(data["game_id"])


def write(data: dict, out: str) -> int:
    add_season_index(data)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(summary_line(data, "Wrote", out))
    rink_note(data)
    warning = goal_log_warning(data)
    if warning:
        print(warning)
    return 0


def build(g: dict, report: dict, cfg: dict, season: int) -> dict:
    try:
        return normalize(g, report, cfg, season)
    except Stop:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as exc:
        raise Stop(f"The game report for game {g.get('GameID')} has an unexpected shape "
                   f"({type(exc).__name__}: {exc}). Save the raw game-report JSON again, unedited.")


def name_texts(obj) -> list:
    """Every string in a name object (or a bare string)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [v for k, v in obj.items() if k in ("full", "short", "last", "first", "display") and isinstance(v, str)]
    return []


def name_label(obj) -> str:
    return next((t for t in name_texts(obj) if t.strip()), "?")


def is_empty_name(obj) -> bool:
    return not any(t.strip() for t in name_texts(obj))


def is_placeholder_name(obj) -> bool:
    for text in name_texts(obj):
        low = text.strip().lower()
        if low in PLACEHOLDER_FULL_NAMES:
            return True
        if any(w.strip(".,") in PLACEHOLDER_PERSON_WORDS for w in low.split()):
            return True
    return False


def is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def fold(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def report_errors(path: str, errors: list) -> int:
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    print(f"{path} cannot be used.", file=sys.stderr)
    return EXIT_ERROR


def team_checks(data: dict, cfg: dict, warnings: list) -> str:
    """Checks shared by game and pregame files: us_side, the is_us flags, team
    names. Returns the Näädät side, or None when it cannot be told."""
    us_side = data.get("us_side")
    if us_side not in ("home", "away"):
        flagged = [k for k in ("home", "away") if data[k].get("is_us") is True]
        warnings.append(f"us_side is {us_side!r}; set it to 'home' or 'away' (it decides the logo order)")
        us_side = flagged[0] if len(flagged) == 1 else None
    elif data[us_side].get("is_us") is not True or data["away" if us_side == "home" else "home"].get("is_us"):
        warnings.append(f"us_side is '{us_side}' but the is_us flags on home/away do not agree")
    display = cfg.get("display_name") or "Näädät"
    for key in ("home", "away"):
        name = str(data[key].get("name") or "").strip()
        if name.lower() in PLACEHOLDER_TEAM_NAMES:
            warnings.append(f"{key}.name is the template placeholder {name!r}; use the opponent's real name")
    if us_side:
        them_side = "away" if us_side == "home" else "home"
        if fold(data[us_side].get("name")) != fold(display):
            warnings.append(f"{us_side} is Näädät (us_side, is_us) but its name is {data[us_side].get('name')!r}; "
                            f"name it {display!r}. If Näädät really is the {them_side} team, swap the two "
                            "blocks and set us_side")
        if fold(data[them_side].get("name")) == fold(display):
            warnings.append(f"the opponent ({them_side}) is named {display!r}; use the opponent's name")
    return us_side


def logo_checks(data: dict, path: str) -> list:
    out = []
    base = os.path.dirname(os.path.abspath(path))
    for key in ("home", "away"):
        logo = data[key].get("logo_file")
        if logo:
            resolved = logo if os.path.isabs(str(logo)) else os.path.join(base, str(logo))
            if not os.path.isfile(resolved):
                out.append(f"{key}.logo_file {logo!r} not found (looked in {resolved}); "
                           "the graphic will show a lettered badge")
    return out


def finish_validate(line: str, warnings: list, notes: list, logo_warnings: list, what: str) -> int:
    print(line)
    for w in warnings + logo_warnings:
        print(f"WARNING: {w}")
    for n in notes:
        print(f"note: {n}")
    if warnings:
        print(f"Fix {what} or ask the user, then run --validate again (exit {EXIT_WARNINGS}).")
        return EXIT_WARNINGS
    if logo_warnings:
        print("Only the missing logo is left, which keeps exit 0: ask the user for the logo, or remove logo_file.")
    elif not notes:
        print("No problems found.")
    return 0


def date_time_checks(data: dict, warnings: list, time_required: bool):
    """date and time of a game or pregame file. The templates ship the literal
    placeholders 'YYYY-MM-DD' and 'HH:MM'; one left in is a WARNING. Returns the
    game day, or None when the date is not usable."""
    date = str(data.get("date") or "").strip()
    day = None
    if date.upper() == PLACEHOLDER_DATE:
        warnings.append(f"date is the template placeholder {PLACEHOLDER_DATE!r}; set the game day the user gave "
                        "(ask if unknown)")
    else:
        try:
            day = dt.datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            warnings.append(f"date {data.get('date')!r} is not ISO YYYY-MM-DD; set the game day")
    time_s = str(data.get("time") or "").strip()
    if time_s.upper() == PLACEHOLDER_TIME:
        warnings.append(f"time is the template placeholder {PLACEHOLDER_TIME!r}; set the face-off time the user "
                        "gave as 24-hour HH:MM" + (" (ask if unknown)" if time_required else ", or leave it empty"))
    elif time_s or time_required:
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", time_s):
            warnings.append(f"time {data.get('time')!r} must be the face-off as 24-hour HH:MM (Helsinki time)"
                            + ("; ask the user if unknown" if time_required else ", or empty if unknown"))
    return day


def validate(path: str) -> int:
    """Check a game.json or pregame.json (usually hand-written); the schema field
    tells them apart. Exit 0 when clean (a missing logo file alone is fine, and a
    note never changes the exit code), 3 on any other warning, 1 when unusable."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read {path} as JSON: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if isinstance(data, dict) and data.get("schema") == PREGAME_SCHEMA:
        return validate_pregame(path, data)
    errors = []
    if not isinstance(data, dict):
        errors.append("the file is not a JSON object")
    else:
        for key in ("home", "away"):
            team = data.get(key)
            if not isinstance(team, dict):
                errors.append(f"'{key}' is missing or not an object")
                continue
            if not str(team.get("name") or "").strip():
                errors.append(f"'{key}.name' is empty")
            if not is_count(team.get("goals")):
                errors.append(f"'{key}.goals' must be a whole number, got {team.get('goals')!r}")
        for key in ("goals", "periods", "scorers_us", "scorers_them", "goalies"):
            value = data.get(key)
            if value is None:
                continue
            if not isinstance(value, list):
                errors.append(f"'{key}' must be a list, got {type(value).__name__}")
                continue
            for i, row in enumerate(value):
                if not isinstance(row, dict):
                    errors.append(f"'{key}[{i}]' must be an object like the template shows, got {row!r}")
        for i, g in enumerate(data.get("goals") if isinstance(data.get("goals"), list) else []):
            if not isinstance(g, dict):
                continue
            if g.get("assists") is not None and not isinstance(g.get("assists"), list):
                errors.append(f"'goals[{i}].assists' must be a list or null")
            if not isinstance(g.get("scorer"), (dict, type(None))):
                errors.append(f"'goals[{i}].scorer' must be a name object {{full, short, last, first}}")
        for key in ("scorers_us", "scorers_them"):
            for i, row in enumerate(data.get(key) if isinstance(data.get(key), list) else []):
                if isinstance(row, dict) and not isinstance(row.get("name"), dict):
                    errors.append(f"'{key}[{i}].name' must be a name object {{full, short, last, first}}")
        for i, gk in enumerate(data.get("goalies") if isinstance(data.get("goalies"), list) else []):
            if isinstance(gk, dict) and not isinstance(gk.get("name"), dict):
                errors.append(f"'goalies[{i}].name' must be a name object {{full, short, last, first}}, "
                              f"got {gk.get('name')!r}")
    if errors:
        return report_errors(path, errors)

    cfg = load_config()
    warnings, notes = [], []   # a warning makes the exit code 3; a note never does
    home, away = data["home"], data["away"]
    us_side = team_checks(data, cfg, warnings)

    date_time_checks(data, warnings, time_required=False)

    placeholders, empties = set(), set()
    for key in ("scorers_us", "scorers_them"):
        for i, row in enumerate(data.get(key) or []):
            if is_placeholder_name(row.get("name")):
                placeholders.add(f"{key}: {name_label(row.get('name'))!r}")
            if is_empty_name(row.get("name")):
                empties.add(f"{key}[{i}]")
    for i, g in enumerate(data.get("goals") or []):
        for obj in [g.get("scorer")] + list(g.get("assists") or []):
            if is_placeholder_name(obj):
                placeholders.add(f"goals: {name_label(obj)!r}")
        if g.get("scorer") is not None and is_empty_name(g.get("scorer")):
            empties.add(f"goals[{i}].scorer")
        for j, a in enumerate(g.get("assists") or []):
            if is_empty_name(a):
                empties.add(f"goals[{i}].assists[{j}]")
    for i, gk in enumerate(data.get("goalies") or []):
        if is_placeholder_name(gk.get("name")):
            placeholders.add(f"goalies: {name_label(gk.get('name'))!r}")
        if is_empty_name(gk.get("name")):
            empties.add(f"goalies[{i}]")
        saves = gk.get("saves")
        if saves is not None and not is_count(saves):
            warnings.append(f"goalies[{i}].saves must be a whole number or null, got {saves!r}")
    for p in sorted(placeholders):
        warnings.append(f"template example name left in ({p}); delete it or use the real name")
    for p in sorted(empties):
        warnings.append(f"{p} has a name object with every field empty (the graphic would print '?'); "
                        "fill at least 'last', or delete the row")

    finish = data.get("finish")
    if finish not in ("REG", "OT", "SO"):
        warnings.append(f"finish {finish!r} must be REG, OT or SO")
        finish = "REG"
    elif finish in ("OT", "SO") and abs(home["goals"] - away["goals"]) != 1:
        warnings.append(f"finish is {finish} but the margin is {abs(home['goals'] - away['goals'])}; "
                        "an overtime or shootout game is won by exactly one goal")
    so_h = 1 if finish == "SO" and home["goals"] > away["goals"] else 0
    so_a = 1 if finish == "SO" and away["goals"] > home["goals"] else 0
    if us_side:
        us, them = (home, away) if us_side == "home" else (away, home)
        final = data.get("status", "final") == "final"
        expected = result_code(us["goals"], them["goals"], finish, final)
        if data.get("result", "") != expected:
            warnings.append(f"result {data.get('result')!r} does not match the score {us['goals']}-{them['goals']} "
                            f"(Näädät first) and finish {finish}: expected {expected!r}")
        rows = data.get("scorers_us") or []
        scored, usable = 0, True
        for row in rows:
            try:
                scored += int(row.get("goals") or 0)
            except (TypeError, ValueError):
                usable = False
                warnings.append(f"scorers_us row {name_label(row.get('name'))!r} has no usable goal count "
                                f"({row.get('goals')!r})")
        # A shootout win adds one goal to the final score that no player scored in the game.
        counted = us["goals"] - (1 if finish == "SO" and us["goals"] > them["goals"] else 0)
        if usable and rows:
            if scored > us["goals"]:
                warnings.append(f"scorers_us adds up to {scored} goals but Näädät scored {us['goals']}")
            elif scored < counted:
                notes.append(f"partial scorer list: scorers_us adds up to {scored} of Näädät's {counted} "
                             "goals. That is fine when the user knows only these; the graphic marks the list "
                             "as partial. Tell the user.")

        shots = data.get("shots")
        if shots is not None:
            if not isinstance(shots, dict) or not is_count(shots.get("us")) or not is_count(shots.get("them")):
                warnings.append(f"shots must be null or {{\"us\": n, \"them\": n}} with whole numbers, got {shots!r}")
            else:
                us_so, them_so = (so_h, so_a) if us_side == "home" else (so_a, so_h)
                for who, team, so in (("us", us, us_so), ("them", them, them_so)):
                    if shots[who] < team["goals"] - so:
                        warnings.append(f"shots.{who} is {shots[who]}, fewer than the {team['goals'] - so} "
                                        "goals scored; every goal is a shot on goal. Fix it or set shots to null")

    if data.get("goals"):
        w = goal_log_warning(data)
        if w:
            warnings.append(w[len("WARNING: "):])

    periods_all = data.get("periods") or []
    if finish != "SO" and any(p.get("label") == "SO" for p in periods_all):
        warnings.append(f"periods has an SO row but finish is {finish}; set finish to SO or remove the row")
    if finish == "REG" and any(p.get("label") == "OT" for p in periods_all):
        warnings.append("periods has an OT row but finish is REG; set finish to OT or SO, or remove the row")
    periods = [p for p in periods_all if p.get("label") != "SO"]
    if periods:
        try:
            ph, pa = sum(int(p["home"]) for p in periods), sum(int(p["away"]) for p in periods)
            if (ph, pa) != (home["goals"] - so_h, away["goals"] - so_a):
                warnings.append(f"periods add up to {ph}-{pa} but the score is {home['goals']}-{away['goals']}")
        except (KeyError, TypeError, ValueError):
            warnings.append("periods need a label, home and away (whole numbers) on every row")

    return finish_validate(summary_line(data, "Checked", path, us_side), warnings, notes,
                           logo_checks(data, path), "game.json")


def validate_pregame(path: str, data: dict) -> int:
    errors = []
    for key in ("home", "away"):
        team = data.get(key)
        if not isinstance(team, dict):
            errors.append(f"'{key}' is missing or not an object")
        elif not str(team.get("name") or "").strip():
            errors.append(f"'{key}.name' is empty")
    if errors:
        return report_errors(path, errors)

    cfg = load_config()
    warnings, notes = [], []
    us_side = team_checks(data, cfg, warnings)

    day = date_time_checks(data, warnings, time_required=True)
    if day is not None:
        if day < helsinki_today():
            warnings.append(f"date {fi_date(day)} is in the past; a pre-game post is for an upcoming game")
        weekday = str(data.get("weekday") or "").strip()
        if weekday and weekday.upper() != weekday_code(day):
            warnings.append(f"weekday {weekday!r} does not match {fi_date(day)} ({weekday_code(day)}); "
                            "fix it or remove the key")
    if not str(data.get("arena") or "").strip():
        warnings.append("arena is empty; ask the user where the game is played")
    if data.get("status", "scheduled") != "scheduled":
        warnings.append(f"status is {data.get('status')!r}; a pre-game file has status 'scheduled'")
    if not str(data.get("competition") or "").strip():
        notes.append("competition is empty; the graphic shows no competition line")

    return finish_validate(pregame_line(data, "Checked", path, us_side), warnings, notes,
                           logo_checks(data, path), "pregame.json")


def no_match(what: str, seasons: list, explicit: int = None) -> str:
    seasons_s = " or ".join(str(s) for s in seasons) if seasons else "the given schedule"
    msg = f"No Näädät game matches {what} in season {seasons_s}.\n"
    if explicit:
        msg += (f"The season may be wrong: a season is numbered by the year it ends in (2026-27 is 2027). "
                f"Try --season {explicit - 1}, or leave --season out.\n")
    return msg + f"{MANUAL_HINT}\nRun with --list to see the III-divisioona schedule."


def is_unplayed(g: dict) -> bool:
    """Scheduled and the face-off still ahead: nothing to post a result of."""
    return g.get("GameStatus") in (0, None) and not is_final(g) and game_datetime(g) > helsinki_now()


def check_playable(g: dict, cfg: dict, allow_unfinished: bool, previous: dict = None) -> None:
    """Post-game mode: stop (exit 2) on a game not played yet, whatever the flags,
    and on a started game that is not final unless --allow-unfinished."""
    if is_unplayed(g):
        when = game_datetime(g)
        raise Stop(f"Game {g['GameID']} has not been played yet: face-off {weekday_code(when.date())} "
                   f"{fi_date(when.date())} at {when.strftime('%H:%M')} ({describe(g, cfg).strip()}).\n"
                   "There is no result to post. For the game-day post, run:\n"
                   f"  python \"{SCRIPT}\" --pregame --game-id {g['GameID']} --out pregame.json",
                   EXIT_NOT_FINAL)
    if not is_final(g) and not allow_unfinished:
        raise not_final_stop(g, cfg, previous)


def not_final_stop(g: dict, cfg: dict, previous: dict = None) -> Stop:
    msg = (f"Game {g['GameID']} is not final yet ({describe(g, cfg).strip()}). "
           "Wait for the official result, or pass --allow-unfinished to use it anyway.")
    if previous is not None:
        msg += (f"\nIt is newer than the last final game ({short_describe(previous, cfg)}); "
                f"to post that one instead, pass --game-id {previous['GameID']}.")
    return Stop(msg, EXIT_NOT_FINAL)


def check_season(explicit: int, g: dict) -> int:
    """The season to write: from the game date. An explicit season that disagrees
    is reported and ignored."""
    derived = season_for(game_datetime(g).date())
    if explicit and explicit != derived:
        print(f"warning: --season/link season {explicit} does not match the game date {g['GameDate']} "
              f"(season {derived}); using {derived}.", file=sys.stderr)
    return derived


def fetch_report_by_id(args, cfg: dict, explicit: int, seasons: list):
    """The game report of args.game_id, trying each season in turn; checked to be
    a game of our club. Returns (report, game entry, season)."""
    assoc = cfg["leijonat"]["association_id"]
    report = None
    for s in seasons:
        args._help_season = s
        r = get_json("gamereport/getgamereportdata", {"gameid": args.game_id, "season": s})
        if report_game(r) is not None:
            report = r
            break
    if report is None:
        raise Stop(no_match(f"game id {args.game_id}", seasons, explicit))
    g = game_from_report(report)
    if g["GameID"] != args.game_id:
        raise Stop(f"The results service returned game {g['GameID']} for game id {args.game_id}.")
    if assoc not in (g["HomeAssociationID"], g["AwayAssociationID"]):
        raise Stop(f"Game {args.game_id} is not a game of our club.\n{MANUAL_HINT}")
    return report, g, check_season(explicit, g)


def schedule_entry(cfg: dict, season: int, game_id: int):
    """The schedule entry of one game, or None when the schedule cannot be reached
    (the report alone is then used)."""
    try:
        return next((x for x in discover_games(cfg, season) if x.get("GameID") == game_id), None)
    except NetError as exc:
        print(f"note: the schedule could not be reached ({exc.reason}); using the game report alone.",
              file=sys.stderr)
        return None


def fetch_by_id(args, cfg: dict, explicit: int) -> int:
    """A known game id: the report carries everything, so fetch it first. The
    schedule only fills gaps (DeniedResults); if it cannot be reached, go on."""
    if explicit:
        seasons = [explicit]
    else:
        current = season_for(helsinki_today())
        seasons = [current, current - 1]
    report, _, season = fetch_report_by_id(args, cfg, explicit, seasons)
    entry = schedule_entry(cfg, season, args.game_id)
    merged = game_from_report(report, entry)
    check_playable(merged, cfg, args.allow_unfinished)
    return write(build(merged, report, cfg, season), args.out)


def pregame_data(g: dict, cfg: dict, season: int, report: dict = None, entry: dict = None) -> dict:
    """The pregame@1 record of a scheduled game. g is a game entry (schedule or
    game_from_report); the arena is the fullest rink name either source gives."""
    assoc = cfg["leijonat"]["association_id"]
    us_home = g["HomeAssociationID"] == assoc

    def side(prefix: str, is_us: bool) -> dict:
        img = g.get(f"{prefix}Img") or ""
        source_name = g.get(f"{prefix}TeamAbbrv") or ""
        return {
            "name": cfg["display_name"] if is_us else source_name,
            "source_name": source_name,
            "association_id": g.get(f"{prefix}AssociationID"),
            "is_us": is_us,
            "logo_url": f"{BASE}/images/associations/weblogos/200x200/{img}" if img else None,
        }

    # Same rule as the post-game path: the report's Arena, else the schedule's RinkName.
    schedule_rink = (entry or {}).get("RinkName") or g.get("ScheduleRinkName")
    if report is None:
        schedule_rink = schedule_rink or g.get("RinkName")
    when = game_datetime(g)
    return {
        "schema": PREGAME_SCHEMA,
        "source": "leijonat",
        "game_id": g["GameID"],
        "season": season,
        "url": f"{BASE}/game?season={season}&gameid={g['GameID']}&lang=fi",
        "competition": cfg.get("competition_label") or g.get("LevelName", ""),
        "series": g.get("SubSerieName", ""),
        "date": when.date().isoformat(),
        "time": when.strftime("%H:%M"),
        "weekday": weekday_code(when.date()),
        **arena_fields((report_game(report) or {}).get("Arena"), schedule_rink),
        "home": side("Home", us_home),
        "away": side("Away", not us_home),
        "us_side": "home" if us_home else "away",
        "status": "scheduled",
    }


def write_pregame(g: dict, cfg: dict, season: int, out: str, report: dict = None, entry: dict = None) -> int:
    if is_final(g):
        raise Stop(f"Game {g['GameID']} has already been played ({describe(g, cfg).strip()}). A pre-game post "
                   "is for an upcoming game: run without --pregame/--next for the result post, or use --next "
                   "for the next game.")
    try:
        data = pregame_data(g, cfg, season, report, entry)
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as exc:
        raise Stop(f"The data for game {g.get('GameID')} has an unexpected shape ({type(exc).__name__}: {exc}).")
    if g.get("GameStatus") not in (0, None) or game_datetime(g) <= helsinki_now():
        print(f"note: game {g['GameID']} has already started (kick-off {data['time']} on {fi_date(data['date'])}); "
              "a pre-game post normally goes out before kick-off.", file=sys.stderr)
    add_season_index(data)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(pregame_line(data, "Wrote", out))
    rink_note(data)
    if not data["arena"]:
        print("WARNING: the results service names no rink for this game; ask the user and fill in arena.")
    return 0


def run_pregame(args, cfg: dict, explicit: int, day) -> int:
    """--pregame / --next: write pregame.json for a game that has not been played."""
    assoc = cfg["leijonat"]["association_id"]
    current = season_for(helsinki_today())
    schedule = None
    if args.games_json:
        schedule = load_json_file(args.games_json, "--games-json")
        if not isinstance(schedule, list):
            raise Stop(f"{args.games_json} is not a helpers/getgames response (expected a JSON list).")
        schedule = team_games(schedule, assoc)

    if args.report_json:
        report = load_report(args.report_json)
        rid = report_game(report)["Id"]
        if args.game_id and rid != args.game_id:
            raise Stop(f"The report is for game {rid}, not the requested {args.game_id}.")
        entry = next((x for x in schedule or [] if x.get("GameID") == rid), None)
        g = game_from_report(report, entry)
        if assoc not in (g["HomeAssociationID"], g["AwayAssociationID"]):
            raise Stop("This game report is not a game of our club.")
        return write_pregame(g, cfg, check_season(explicit, g), args.out, report, entry)

    if args.game_id and schedule is None:
        seasons = [explicit] if explicit else [current, current + 1]
        report, _, season = fetch_report_by_id(args, cfg, explicit, seasons)
        entry = schedule_entry(cfg, season, args.game_id)
        return write_pregame(game_from_report(report, entry), cfg, season, args.out, report, entry)

    if explicit:
        seasons = [explicit]
    elif day:
        seasons = [season_for(day)]
    else:
        seasons = [current, current + 1]  # in summer the next game is in next season
    now = helsinki_now()
    pick = None
    for season in seasons:
        args._help_season = season
        games = schedule if schedule is not None else discover_games(cfg, season)
        games = sorted(games, key=game_datetime)
        if args.game_id:
            found = [x for x in games if x["GameID"] == args.game_id]
        elif day:
            same_day = [x for x in games if game_datetime(x).date() == day]
            found = [x for x in same_day if not is_final(x)] or same_day
        else:
            # Not started yet, kick-off still ahead (a game later today counts).
            found = [x for x in games if not is_final(x) and x.get("GameStatus") in (0, None)
                     and game_datetime(x) >= now]
        if found:
            pick = found[0]
            break
        if schedule is not None:
            break
    if pick is None:
        if args.game_id:
            what = f"game id {args.game_id}"
        elif day:
            what = f"date {day.isoformat()}"
        else:
            raise Stop(f"No upcoming Näädät game on the III-divisioona schedule (season "
                       f"{' or '.join(map(str, seasons))}).\nFor an FCAA or other game, fill "
                       "references/manual_pregame_template.json by hand and check it with --validate.")
        raise Stop(no_match(what, [] if schedule is not None else seasons, explicit))

    season = check_season(explicit, pick)
    args._help_season, args._help_game = season, pick["GameID"]
    report = None
    if schedule is None:
        try:
            r = get_json("gamereport/getgamereportdata", {"gameid": pick["GameID"], "season": season})
            if report_game(r) is not None and report_game(r)["Id"] == pick["GameID"]:
                report = r
        except NetError as exc:
            print(f"note: the game report could not be reached ({exc.reason}); using the schedule alone.",
                  file=sys.stderr)
    g = game_from_report(report, pick) if report is not None else pick
    return write_pregame(g, cfg, season, args.out, report, pick)


def run(args) -> int:
    cfg = load_config()
    assoc = cfg["leijonat"]["association_id"]
    day = parse_date(args.date) if args.date else None
    explicit = args.season
    if args.game_url:
        url_id, url_season = parse_game_url(args.game_url)
        if args.game_id and args.game_id != url_id:
            raise Stop(f"--game-id {args.game_id} and --game-url (gameid={url_id}) disagree.")
        args.game_id = url_id
        explicit = explicit or url_season
    args._help_season = explicit

    if args.next and (args.game_id or day):
        raise Stop("--next picks the next game itself; leave out --game-id, --game-url and --date, "
                   "or use them with --pregame instead of --next.")
    if (args.pregame or args.next) and not args.list:
        return run_pregame(args, cfg, explicit, day)

    report = load_report(args.report_json) if args.report_json else None
    if report is not None and args.games_json and not args.game_id and not day:
        # The report names its game; use it to pick the schedule entry.
        args.game_id = report_game(report)["Id"]

    if report is not None and not args.games_json:
        g = game_from_report(report)
        if assoc not in (g["HomeAssociationID"], g["AwayAssociationID"]):
            raise Stop("This game report is not a game of our club.")
        if args.game_id and g["GameID"] != args.game_id:
            raise Stop(f"The report is for game {g['GameID']}, not the requested {args.game_id}.")
        check_playable(g, cfg, args.allow_unfinished)
        season = check_season(explicit, g)
        return write(build(g, report, cfg, season), args.out)

    if args.game_id and not args.games_json and not args.list:
        return fetch_by_id(args, cfg, explicit)

    if explicit:
        seasons = [explicit]
    elif day:
        seasons = [season_for(day)]
    else:
        current = season_for(helsinki_today())
        seasons = [current, current - 1]
    if args.games_json:
        schedule = load_json_file(args.games_json, "--games-json")
        if not isinstance(schedule, list):
            raise Stop(f"{args.games_json} is not a helpers/getgames response (expected a JSON list).")
        sources = [(None, team_games(schedule, assoc))]
    else:
        sources = None

    if args.list:
        for season in seasons:
            args._help_season = season
            games = sources[0][1] if sources else discover_games(cfg, season)
            if games:
                if not sources:
                    print(f"Season {season} ({season - 1}-{str(season)[2:]}), newest first, score Näädät first:")
                for g in sorted(games, key=game_datetime, reverse=True):
                    print(describe(g, cfg))
                return 0
            if sources:
                break
        raise Stop(f"No games found for association {assoc} in season {' or '.join(map(str, seasons))}.\n{MANUAL_HINT}")

    if args.game_id:
        what = f"game id {args.game_id}"
    elif day:
        what = f"date {day.isoformat()}"
    else:
        what = "'latest finished game'"

    now = helsinki_now()
    g = None
    for i, season in enumerate(seasons):
        args._help_season = season
        games = sources[0][1] if sources else discover_games(cfg, season)
        games.sort(key=game_datetime)
        if args.game_id:
            pick = [x for x in games if x["GameID"] == args.game_id]
        elif day:
            pick = [x for x in games if game_datetime(x).date() == day]
        else:
            finals = [x for x in games if is_final(x)]
            started = [x for x in games if game_datetime(x) <= now]
            newest = started[-1] if started else None
            last_final = finals[-1] if finals else None
            if newest is not None and not is_final(newest) and (
                    last_final is None or game_datetime(newest) > game_datetime(last_final)):
                # A newer game has been played but is not final: never fall back
                # silently to the previous one.
                if not args.allow_unfinished:
                    raise not_final_stop(newest, cfg, last_final)
                pick = [newest]
            else:
                pick = finals
        if pick:
            g = pick[-1]
            if i > 0 and not args.game_id:
                print(f"note: no finished game yet in season {seasons[0]}; using the latest of season {season}.",
                      file=sys.stderr)
            break
        if sources:
            break
    if g is None:
        raise Stop(no_match(what, [] if sources else seasons, explicit))
    check_playable(g, cfg, args.allow_unfinished)

    season = check_season(explicit, g)
    args._help_season = season
    args._help_game = g["GameID"]
    if report is None:
        report = get_json("gamereport/getgamereportdata", {"gameid": g["GameID"], "season": season})
        if report_game(report) is None:
            raise Stop(f"The results service returned no report for game {g['GameID']} season {season}:\n"
                       f"  {report_url(season, g['GameID'])}")
    report_id = report_game(report)["Id"]
    if report_id != g["GameID"]:
        raise Stop(f"The game report is for game {report_id}, but the schedule picked game {g['GameID']} "
                   f"({describe(g, cfg).strip()}). Pass --game-id {report_id} to use the report's game, "
                   f"or save the report of game {g['GameID']}.")
    merged = game_from_report(report, g)
    # The schedule may already say final while the report lags behind (or the
    # other way round); the report is what gets written, so it decides.
    check_playable(merged, cfg, args.allow_unfinished)
    return write(build(merged, report, cfg, season), args.out)


def main(argv=None) -> int:
    setup_streams()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int,
                    help="Leijonat season number (2026-27 is 2027). Rarely needed: the default comes from "
                         "--date, the game link or the report, else the current season with a fallback to "
                         "the previous one. A season that contradicts the game date is reported and ignored.")
    ap.add_argument("--game-id", type=int, help="a game id; the game report is fetched directly")
    ap.add_argument("--game-url", help="a tulospalvelu.leijonat.fi game link (has gameid, usually season)")
    ap.add_argument("--date", help="YYYY-MM-DD, pick our game played on this date")
    ap.add_argument("--list", action="store_true",
                    help="list all our games of the season (scheduled ones too), newest first, "
                         "score Näädät first with the result, and exit")
    ap.add_argument("--allow-unfinished", action="store_true",
                    help="accept a game that has started but is not marked final (exit 2 otherwise). "
                         "A game not played yet is always refused: use --pregame for it")
    ap.add_argument("--games-json", help="offline: saved response of helpers/getgames (the schedule)")
    ap.add_argument("--report-json", help="offline: saved response of gamereport/getgamereportdata")
    ap.add_argument("--pregame", action="store_true",
                    help="write pregame.json (kick-off, teams, logos, rink) for a game not played yet. "
                         "Picks the game with --game-id, --game-url or --date; with none of them, the next game")
    ap.add_argument("--next", action="store_true",
                    help="the next Näädät game that has not started (a game later today counts); "
                         "implies --pregame")
    ap.add_argument("--validate", metavar="JSON",
                    help="check a game.json or pregame.json (e.g. a hand-entered FCAA game; the schema field "
                         "tells them apart), print its summary line, warnings and notes. Exit 0 = clean "
                         "(a note or a missing logo_file alone is fine), 3 = a WARNING to fix before "
                         "rendering, 1 = unusable")
    ap.add_argument("--out", help="output file (default game.json, or pregame.json with --pregame/--next)")
    args = ap.parse_args(argv)
    args._help_season = None
    args._help_game = None
    if not args.out:
        args.out = "pregame.json" if (args.pregame or args.next) else "game.json"

    if args.validate:
        return validate(args.validate)
    try:
        return run(args)
    except Stop as stop:
        print(stop.message, file=sys.stderr)
        return stop.code
    except NetError as exc:
        print(network_help(exc.url, exc.reason, args.game_id or args._help_game, args._help_season),
              file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
