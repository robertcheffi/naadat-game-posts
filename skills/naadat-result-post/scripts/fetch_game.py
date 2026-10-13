#!/usr/bin/env python3
"""Fetch a finished Näädät game from the Finnish Ice Hockey Association results
service (tulospalvelu.leijonat.fi) and write it as a normalized game.json.

Standard library only, so it runs in any sandbox that has Python 3.8+.

Examples:
  python fetch_game.py                      # latest finished game -> game.json
  python fetch_game.py --list               # recent finished games, newest first
  python fetch_game.py --game-id 2720857    # a specific game
  python fetch_game.py --date 2026-10-04    # the game played on that date

  python fetch_game.py --game-url "https://tulospalvelu.leijonat.fi/game?season=2027&gameid=2720857"

If this sandbox cannot reach the internet, fetch ONE document some other way
(a web-fetch tool, or the user's browser) and pass it in. The game report is
enough on its own (about 20 kB):
  https://tulospalvelu.leijonat.fi/gamereport/getgamereportdata?gameid=<ID>&season=<SEASON>
  python fetch_game.py --report-json report.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://tulospalvelu.leijonat.fi"
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(SKILL_DIR, "assets", "brand", "team.json")

FINAL_STATUSES = {2, 94}  # the site's own JS treats these as a final result
FINISH_TYPES = {1: "REG", 2: "OT", 3: "SO"}
# Only codes whose meaning is certain are translated. Others (seen: SR, TV) are
# kept in "type_raw" and left off the graphics rather than guessed.
GOAL_TYPES = {
    "YV": "PP",   # ylivoima, power play
    "YV2": "PP",
    "AV": "SH",   # alivoima, short-handed
    "AV2": "SH",
    "TM": "EN",   # tyhjä maali, empty net
}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def current_season(today: dt.date) -> int:
    # Leijonat numbers a season by the year it ends in: 2026-27 is 2027.
    return today.year + 1 if today.month >= 6 else today.year


def get_json(path: str, params: dict) -> object:
    query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{BASE}/{path}?{query}"
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "naadat-result-post/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(
            f"Could not reach {url}\n  {exc}\n"
            "This sandbox may block internet access. Fetch this URL with a web-fetch tool,\n"
            "save the response as JSON and rerun with --games-json / --report-json."
        )



def games_url(season: int, subserie_id: int) -> str:
    return (f"{BASE}/helpers/getgames?dwl=0&season={season}&subSerieId={subserie_id}"
            "&teamid=0&districtid=0&gamedays=3&dog=&levelid=-1")


def report_url(season: int, game_id: int) -> str:
    return f"{BASE}/gamereport/getgamereportdata?gameid={game_id}&season={season}"


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
    for sub in subseries or []:
        blocks = get_json("helpers/getgames", {
            "dwl": 0, "season": season, "subSerieId": sub["subSerieId"], "teamid": 0,
            "districtid": 0, "gamedays": 3, "dog": "", "levelid": -1,
        })
        found.extend(team_games(blocks, lt["association_id"]))
    return found


def team_games(blocks, association_id: int) -> list:
    out = []
    for block in blocks or []:
        for g in block.get("Games", []):
            if association_id in (g.get("HomeAssociationID"), g.get("AwayAssociationID")):
                out.append(g)
    return out


def game_datetime(g: dict) -> dt.datetime:
    return dt.datetime.strptime(f"{g['GameDate']} {g.get('GameTime') or '00:00:00'}", "%d.%m.%Y %H:%M:%S")


def is_final(g: dict) -> bool:
    return g.get("GameStatus") in FINAL_STATUSES and not g.get("DeniedResults")


def person(raw: str) -> dict:
    """'SILLANPÄÄ Otto' -> first 'Otto', last 'Sillanpää'. Surname tokens are upper case."""
    raw = (raw or "").strip()
    if not raw:
        return {"full": "", "short": "", "last": "", "first": ""}
    parts = raw.split()
    last = [p for p in parts if p.isupper() and len(p) > 1] or parts[:1]
    first = [p for p in parts if p not in last]
    last_s = " ".join(w.capitalize() if "-" not in w else "-".join(x.capitalize() for x in w.split("-")) for w in last)
    first_s = " ".join(first)
    full = f"{first_s} {last_s}".strip()
    short = f"{first_s[:1]}. {last_s}" if first_s else last_s
    return {"full": full, "short": short, "last": last_s, "first": first_s}


def mmss(seconds: int) -> str:
    """Leijonat logs elapsed game seconds; hockey summaries print them as mm:ss of game time."""
    seconds = int(seconds or 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def split_pair(text: str):
    try:
        a, b = str(text).split("-", 1)
        return int(a), int(b)
    except (ValueError, AttributeError):
        return None


def normalize(g: dict, report: dict, cfg: dict, season: int) -> dict:
    lt = cfg["leijonat"]
    us_home = g["HomeAssociationID"] == lt["association_id"]
    gu = (report.get("GamesUpdate") or [{}])[0]
    home_id = gu.get("HomeTeam", {}).get("Id", g["HomeTeam"])

    def side(prefix: str, is_us: bool) -> dict:
        assoc = g[f"{prefix}AssociationID"]
        img = g.get(f"{prefix}Img") or ""
        return {
            "name": cfg["display_name"] if is_us else g[f"{prefix}TeamAbbrv"],
            "source_name": g[f"{prefix}TeamAbbrv"],
            "association": g.get(f"{prefix}Association", ""),
            "association_id": assoc,
            "goals": g[f"{prefix}Goals"],
            "is_us": is_us,
            "logo_url": f"{BASE}/images/associations/weblogos/200x200/{img}" if img else None,
        }

    home, away = side("Home", us_home), side("Away", not us_home)
    us, them = (home, away) if us_home else (away, home)
    finish = FINISH_TYPES.get(g.get("FinishedType"), "REG")
    won = us["goals"] > them["goals"]
    result = ("W" if won else "L") if finish == "REG" else (finish + ("W" if won else "L"))

    periods = []
    ps = g.get("PeriodSummary") or {}
    pg = ps.get("PeriodGoals") or {}
    for i, (h, a) in enumerate(zip(pg.get("Home", []), pg.get("Away", [])), start=1):
        label = str(i) if i <= 3 else ("OT" if i == 4 else "SO")
        periods.append({"label": label, "home": h, "away": a})

    goals = []
    for e in report.get("GameLogsUpdate", []):
        if e.get("Type") != "Goal":
            continue
        is_us_goal = (e.get("TeamId") == home_id) == us_home
        assists = [person(n) for n in (e.get("FirstAssistName"), e.get("SecondAssistName")) if n and n.strip()]
        raw_type = (e.get("GoalType") or "").strip().upper()
        goals.append({
            "period": e.get("Period"),
            "time": mmss(e.get("GameTime")),
            "team": "us" if is_us_goal else "them",
            "scorer": person(e.get("ScorerName")),
            "jersey": e.get("ScorerJersey"),
            "assists": assists,
            "type": GOAL_TYPES.get(raw_type, ""),
            "type_raw": raw_type,
            "score_after": f"{e.get('HomeTeamGoals')}-{e.get('AwayTeamGoals')}",
        })

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
    saves_total = next((split_pair(s.get("Saves")) for s in summary.get("PeriodSaves", [])[-1:]), None)
    if saves_total:
        home_saves, away_saves = saves_total
        home_shots, away_shots = away_saves + home["goals"], home_saves + away["goals"]
        shots = {"us": home_shots if us_home else away_shots, "them": away_shots if us_home else home_shots}

    goalies = []
    for team in report.get("GoalkeeperSummary", []):
        for gk in team.get("TeamGoalkeepers", []):
            total = next((s["Saves"] for s in gk.get("GkSaves", []) if s.get("Period") == 0), None)
            goalies.append({"team_name": team.get("TeamName"), "name": person(gk.get("GkName")), "saves": total,
                            "is_us": team.get("TeamName") == us["source_name"]})

    when = game_datetime(g)
    return {
        "schema": "naadat-result-post/game@1",
        "source": "leijonat",
        "game_id": g["GameID"],
        "season": season,
        "url": f"{BASE}/game?season={season}&gameid={g['GameID']}&lang=fi",
        "competition": cfg.get("competition_label") or g.get("LevelName", ""),
        "series": g.get("SubSerieName", ""),
        "date": when.date().isoformat(),
        "time": when.strftime("%H:%M"),
        "arena": g.get("RinkName") or gu.get("Arena", ""),
        "attendance": g.get("Spectator") or gu.get("Spectators"),
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
        "status": "final" if is_final(g) else "not_final",
    }


def game_from_report(report: dict) -> dict:
    """Rebuild the schedule entry from a game report, for the offline path."""
    gu = (report.get("GamesUpdate") or [{}])[0]
    home, away = gu.get("HomeTeam") or {}, gu.get("AwayTeam") or {}
    rows = (report.get("PeriodSummary") or {}).get("PeriodGoals") or []
    pairs = [split_pair(r.get("Goals")) for r in rows]
    played = (report.get("PeriodSummary") or {}).get("PlayedPeriods") or max(len(pairs) - 1, 0)
    pairs = [p for p in pairs[:played] if p]
    return {
        "GameID": gu.get("Id"),
        "GameDate": gu.get("StartDate"),
        "GameTime": gu.get("StartTime") or "00:00:00",
        "GameStatus": gu.get("GameStatus"),
        "FinishedType": gu.get("FinishedType"),
        "DeniedResults": 0,
        "HomeTeam": home.get("Id"), "AwayTeam": away.get("Id"),
        "HomeGoals": home.get("Goals"), "AwayGoals": away.get("Goals"),
        "HomeTeamAbbrv": home.get("Name", ""), "AwayTeamAbbrv": away.get("Name", ""),
        "HomeAssociationID": home.get("AssId"), "AwayAssociationID": away.get("AssId"),
        "HomeImg": home.get("Image", ""), "AwayImg": away.get("Image", ""),
        "HomeAssociation": "", "AwayAssociation": "",
        "RinkName": gu.get("Arena", ""),
        "Spectator": gu.get("Spectators"),
        "LevelName": gu.get("LevelName", ""),
        "SubSerieName": gu.get("SubSerieName", ""),
        "PeriodSummary": {"PlayedPeriods": len(pairs),
                          "PeriodGoals": {"Home": [p[0] for p in pairs], "Away": [p[1] for p in pairs]}},
    }


def parse_game_url(url: str):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(q["gameid"][0]), int(q["season"][0]) if "season" in q else None
    except (KeyError, ValueError):
        sys.exit(f"Could not read gameid from {url}")


def describe(g: dict, cfg: dict) -> str:
    us_home = g["HomeAssociationID"] == cfg["leijonat"]["association_id"]
    state = "final" if is_final(g) else ("scheduled" if g.get("GameStatus") == 0 else f"status {g.get('GameStatus')}")
    score = f"{g['HomeGoals']}-{g['AwayGoals']}" if g.get("GameStatus") else "  -  "
    venue = "home" if us_home else "away"
    opp = g["AwayTeamAbbrv"] if us_home else g["HomeTeamAbbrv"]
    return f"{g['GameID']}  {g['GameDate']} {g['GameTime'][:5]}  {venue:4}  vs {opp:<16} {score:>6}  {state}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, help="Leijonat season number (2026-27 is 2027). Default: current.")
    ap.add_argument("--game-id", type=int)
    ap.add_argument("--game-url", help="a tulospalvelu.leijonat.fi game link (has gameid and season)")
    ap.add_argument("--date", help="YYYY-MM-DD, pick our game played on this date")
    ap.add_argument("--list", action="store_true", help="list our games and exit")
    ap.add_argument("--allow-unfinished", action="store_true", help="accept a game that is not marked final")
    ap.add_argument("--games-json", help="offline: saved response of helpers/getgames")
    ap.add_argument("--report-json", help="offline: saved response of gamereport/getgamereportdata")
    ap.add_argument("--out", default="game.json")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.game_url:
        args.game_id, url_season = parse_game_url(args.game_url)
        args.season = args.season or url_season
    season = args.season or current_season(dt.date.today())

    if args.report_json and not args.games_json:
        with open(args.report_json, encoding="utf-8") as f:
            report = json.load(f)
        g = game_from_report(report)
        if cfg["leijonat"]["association_id"] not in (g["HomeAssociationID"], g["AwayAssociationID"]):
            sys.exit("This game report is not a game of our club.")
        if not is_final(g) and not args.allow_unfinished:
            sys.exit(f"Game {g['GameID']} is not final yet. Pass --allow-unfinished to use it anyway.")
        return write(normalize(g, report, cfg, season), args.out)

    if args.games_json:
        with open(args.games_json, encoding="utf-8") as f:
            games = team_games(json.load(f), cfg["leijonat"]["association_id"])
    else:
        games = discover_games(cfg, season)
    if not games:
        sys.exit(f"No games found for association {cfg['leijonat']['association_id']} in season {season}.")
    games.sort(key=game_datetime)

    if args.list:
        for g in reversed(games):
            print(describe(g, cfg))
        return 0

    if args.game_id:
        pick = [g for g in games if g["GameID"] == args.game_id]
    elif args.date:
        pick = [g for g in games if game_datetime(g).date().isoformat() == args.date]
    else:
        now = dt.datetime.now()
        pick = [g for g in games if is_final(g) and game_datetime(g) <= now][-1:]
    if not pick:
        sys.exit("No matching game. Run with --list to see the schedule.")
    g = pick[-1]
    if not is_final(g) and not args.allow_unfinished:
        sys.exit(f"Game {g['GameID']} is not final yet ({describe(g, cfg)}). "
                 "Wait for the official result or pass --allow-unfinished.")

    if args.report_json:
        with open(args.report_json, encoding="utf-8") as f:
            report = json.load(f)
    else:
        report = get_json("gamereport/getgamereportdata", {"gameid": g["GameID"], "season": season})

    return write(normalize(g, report, cfg, season), args.out)


def write(data: dict, out: str) -> int:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    them = data["away"] if data["us_side"] == "home" else data["home"]
    logged = sum(1 for g in data["goals"] if g["period"] != 5)
    total = data["home"]["goals"] + data["away"]["goals"]
    print(f"Wrote {out}: {data['date']} {data['home']['name']} {data['home']['goals']}-{data['away']['goals']} "
          f"{data['away']['name']} ({data['result']}), opponent {them['name']}, {logged} goals in the log.")
    if data["finish"] != "SO" and logged != total:
        print(f"WARNING: the goal log has {logged} goals but the score adds up to {total}. "
              "Scorer lists may be incomplete; tell the user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
