"""
Fantasy Hockey CSV feed.

Nolasa NHL statistiku un kalendāru un uzraksta CSV failus mapē docs/,
ko GitHub Pages publicē kā https://<lietotajs>.github.io/<repo>/<fails>.csv

Faili:
  skaters.csv    - laukuma spēlētāji: sezona + pēdējās 7/14/30 dienas
  goalies.csv    - vārtsargi: sezona + pēdējās 14 dienas
  schedule.csv   - spēles 3 fantasy nedēļām (P-Sv), ar "light day" atzīmi
  team_weeks.csv - katrai komandai: spēles nedēļā, spēles vieglajās dienās, back-to-back
  meta.csv       - atjaunošanas laiks un sezona

Palaišana:  python fantasy_feed.py            (vajag: pip install requests)
"""

import csv
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

STATS_API = "https://api.nhle.com/stats/rest/en"
WEB_API = "https://api-web.nhle.com/v1"
OUT_DIR = Path(__file__).resolve().parent / "docs"

WINDOWS = [7, 14, 30]          # dienu logi "formai"
GOALIE_WINDOW = 14
WEEKS_AHEAD = 3                # cik fantasy nedēļas kalendārā (ieskaitot šo)
LIGHT_DAY_MAX_GAMES = 7        # diena ar <= 7 spēlēm = "light day" (off-night)
PAGE = 100

session = requests.Session()
session.headers["User-Agent"] = "fantasy-hockey-feed/1.0"


# ------------------------------------------------------------------ API palīgi
def get_json(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == tries - 1:
                raise
            wait = 3 * (i + 1)
            print(f"  ! {e} - mēģinu vēlreiz pēc {wait}s")
            time.sleep(wait)


def stats_report(report, cayenne, aggregate):
    """Nolasa visu atskaiti pa lapām (NHL stats API dod max ~100 rindas vienā pieprasījumā)."""
    rows, start = [], 0
    while True:
        j = get_json(f"{STATS_API}/{report}", {
            "isAggregate": str(aggregate).lower(), "isGame": "false",
            "start": start, "limit": PAGE, "cayenneExp": cayenne,
            "sort": '[{"property":"playerId","direction":"ASC"}]',
        })
        data = j.get("data", [])
        rows += data
        start += PAGE
        if not data or start >= j.get("total", 0):
            return rows


def season_filter(season_id):
    return f"gameTypeId=2 and seasonId={season_id}"


def window_filter(days, today):
    d_from = (today - timedelta(days=days)).isoformat()
    d_to = (today - timedelta(days=1)).isoformat()
    return f'gameTypeId=2 and gameDate>="{d_from}" and gameDate<="{d_to}"'


def by_player(rows):
    return {r["playerId"]: r for r in rows}


def num(v, nd=None):
    if v is None or v == "":
        return ""
    return round(v, nd) if nd is not None else v


def current_season(today):
    y = today.year if today.month >= 8 else today.year - 1
    return f"{y}{y + 1}"


def last_team(abbrevs):
    # mainīts spēlētājs: "TOR,MTL" -> pašreizējā komanda ir pēdējā
    return (abbrevs or "").split(",")[-1].strip()


# ------------------------------------------------------------------ spēlētāji
def build_skaters(season_id, today):
    print("Laukuma spēlētāji: sezona...")
    cay = season_filter(season_id)
    summ = by_player(stats_report("skater/summary", cay, aggregate=False))
    real = by_player(stats_report("skater/realtime", cay, aggregate=False))

    windows = {}
    for d in WINDOWS:
        print(f"Laukuma spēlētāji: pēdējās {d} dienas...")
        cay_w = window_filter(d, today)
        windows[d] = (by_player(stats_report("skater/summary", cay_w, aggregate=True)),
                      by_player(stats_report("skater/realtime", cay_w, aggregate=True)))

    out = []
    for pid, s in summ.items():
        rt = real.get(pid, {})
        row = {
            "player_id": pid,
            "name": s.get("skaterFullName"),
            "team": last_team(s.get("teamAbbrevs")),
            "pos": s.get("positionCode"),
            "gp": s.get("gamesPlayed"),
            "g": s.get("goals"), "a": s.get("assists"), "pts": s.get("points"),
            "pm": s.get("plusMinus"), "pim": s.get("penaltyMinutes"),
            "ppg": s.get("ppGoals"), "ppp": s.get("ppPoints"), "shp": s.get("shPoints"),
            "gwg": s.get("gameWinningGoals"), "sog": s.get("shots"),
            "hit": rt.get("hits", 0), "blk": rt.get("blockedShots", 0),
            "tk": rt.get("takeaways", 0), "gv": rt.get("giveaways", 0),
            "fo_pct": num(s.get("faceoffWinPct"), 3),
            "toi_min": num((s.get("timeOnIcePerGame") or 0) / 60, 2),
        }
        for d in WINDOWS:
            ws, wr = windows[d][0].get(pid, {}), windows[d][1].get(pid, {})
            p = f"l{d}_"
            row.update({
                p + "gp": ws.get("gamesPlayed", 0), p + "g": ws.get("goals", 0),
                p + "a": ws.get("assists", 0), p + "pts": ws.get("points", 0),
                p + "pm": ws.get("plusMinus", 0), p + "pim": ws.get("penaltyMinutes", 0),
                p + "ppp": ws.get("ppPoints", 0), p + "shp": ws.get("shPoints", 0),
                p + "gwg": ws.get("gameWinningGoals", 0),
                p + "sog": ws.get("shots", 0), p + "hit": wr.get("hits", 0),
                p + "blk": wr.get("blockedShots", 0),
                p + "toi_min": num((ws.get("timeOnIcePerGame") or 0) / 60, 2),
            })
        out.append(row)
    out.sort(key=lambda r: (-(r["pts"] or 0), r["name"] or ""))
    return out


def build_goalies(season_id, today):
    print("Vārtsargi...")
    season = by_player(stats_report("goalie/summary", season_filter(season_id), aggregate=False))
    win = by_player(stats_report("goalie/summary", window_filter(GOALIE_WINDOW, today), aggregate=True))
    out = []
    for pid, s in season.items():
        w = win.get(pid, {})
        p = f"l{GOALIE_WINDOW}_"
        out.append({
            "player_id": pid, "name": s.get("goalieFullName"),
            "team": last_team(s.get("teamAbbrevs")),
            "gp": s.get("gamesPlayed"), "gs": s.get("gamesStarted"),
            "w": s.get("wins"), "l": s.get("losses"), "otl": s.get("otLosses"),
            "ga": s.get("goalsAgainst"), "gaa": num(s.get("goalsAgainstAverage"), 2),
            "sv": s.get("saves"), "sa": s.get("shotsAgainst"),
            "sv_pct": num(s.get("savePct"), 3), "so": s.get("shutouts"),
            p + "gp": w.get("gamesPlayed", 0), p + "gs": w.get("gamesStarted", 0),
            p + "w": w.get("wins", 0), p + "l": w.get("losses", 0), p + "otl": w.get("otLosses", 0),
            p + "ga": w.get("goalsAgainst", 0), p + "sv": w.get("saves", 0),
            p + "sa": w.get("shotsAgainst", 0), p + "so": w.get("shutouts", 0),
            p + "gaa": num(w.get("goalsAgainstAverage"), 2), p + "sv_pct": num(w.get("savePct"), 3),
        })
    out.sort(key=lambda r: (-(r["w"] or 0), r["name"] or ""))
    return out


# ------------------------------------------------------------------ kalendārs
def build_schedule(today):
    print("Kalendārs...")
    monday = today - timedelta(days=today.weekday())
    games = []
    for k in range(WEEKS_AHEAD):
        wk = get_json(f"{WEB_API}/schedule/{(monday + timedelta(weeks=k)).isoformat()}")
        for day in wk.get("gameWeek", []):
            for g in day.get("games", []):
                if g.get("gameType") != 2:   # tikai regulārā sezona
                    continue
                games.append({
                    "date": day["date"], "game_id": g["id"],
                    "away": g["awayTeam"]["abbrev"], "home": g["homeTeam"]["abbrev"],
                    "start_utc": g.get("startTimeUTC", ""), "state": g.get("gameState", ""),
                })

    per_day = defaultdict(int)
    for g in games:
        per_day[g["date"]] += 1
    for g in games:
        d = date.fromisoformat(g["date"])
        g["weekday"] = d.strftime("%a")
        g["week_start"] = (d - timedelta(days=d.weekday())).isoformat()
        g["games_that_day"] = per_day[g["date"]]
        g["light_day"] = 1 if per_day[g["date"]] <= LIGHT_DAY_MAX_GAMES else 0
    games.sort(key=lambda g: (g["date"], g["start_utc"]))

    # komandu nedēļas
    teams = sorted({g["away"] for g in games} | {g["home"] for g in games})
    weeks = sorted({g["week_start"] for g in games} |
                   {(monday + timedelta(weeks=k)).isoformat() for k in range(WEEKS_AHEAD)})
    tw = []
    for t in teams:
        for ws in weeks:
            mine = [g for g in games if g["week_start"] == ws and t in (g["away"], g["home"])]
            dates = sorted(date.fromisoformat(g["date"]) for g in mine)
            b2b = sum(1 for a, b in zip(dates, dates[1:]) if (b - a).days == 1)
            tw.append({
                "team": t, "week_start": ws,
                "week_end": (date.fromisoformat(ws) + timedelta(days=6)).isoformat(),
                "games": len(mine), "light_day_games": sum(g["light_day"] for g in mine),
                "home_games": sum(1 for g in mine if g["home"] == t),
                "back_to_backs": b2b,
                "opponents": " ".join(("vs " + g["away"]) if g["home"] == t else ("@" + g["home"])
                                      for g in sorted(mine, key=lambda g: g["date"])),
            })
    cols = ["date", "weekday", "week_start", "game_id", "away", "home", "start_utc",
            "state", "games_that_day", "light_day"]
    return [{c: g[c] for c in cols} for g in games], tw


# ------------------------------------------------------------------ rakstīšana
def write_csv(name, rows, fields=None):
    if not rows and not fields:
        print(f"  ! {name}: nav datu, fails netiek pārrakstīts")
        return
    fields = fields or list(rows[0].keys())
    path = OUT_DIR / name
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    print(f"  {name}: {len(rows)} rindas")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / ".nojekyll").touch()
    today = datetime.now(timezone.utc).date()
    season_id = current_season(today)
    print(f"Sezona {season_id}, datums {today}")

    errors = []
    for name, fn in [("skaters.csv", lambda: build_skaters(season_id, today)),
                     ("goalies.csv", lambda: build_goalies(season_id, today))]:
        try:
            write_csv(name, fn())
        except Exception as e:  # viena faila kļūda neaptur pārējos
            errors.append(f"{name}: {e}")
    try:
        sched, team_weeks = build_schedule(today)
        write_csv("schedule.csv", sched)
        write_csv("team_weeks.csv", team_weeks)
    except Exception as e:
        errors.append(f"schedule: {e}")

    write_csv("meta.csv", [{
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "season": season_id, "status": "OK" if not errors else "PARTIAL",
    }])
    if errors:
        print("KĻŪDAS:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("Gatavs.")


if __name__ == "__main__":
    main()
