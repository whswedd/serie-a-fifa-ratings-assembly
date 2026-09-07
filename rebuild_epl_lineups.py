
import json, time, sys
from pathlib import Path
import requests
import pandas as pd

BASE = "https://sdp-prem-prod.premier-league-prod.pulselive.com"
COMPETITION_ID = 8
SEASONS = {
    "2021-22": 2021,
    "2022-23": 2022,
    "2023-24": 2023,
    "2024-25": 2024,
    "2025-26": 2025,
}
OUT = Path("output")
OUT.mkdir(exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; EPL-Lineup-Rebuilder/1.0)",
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://www.premierleague.com",
    "Referer": "https://www.premierleague.com/",
})

def get_json(path, params=None, tries=5):
    url = BASE + path
    last = None
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"{r.status_code} {r.text[:300]}")
        except Exception as e:
            last = e
        time.sleep(1.5 * (i+1))
    raise last

def as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ("content","matches","data","items"):
            if isinstance(x.get(k), list):
                return x[k]
    return []

def team_name(t):
    if not isinstance(t, dict):
        return None
    return t.get("name") or t.get("shortName") or t.get("clubName")

def match_id(m):
    return m.get("matchId") or m.get("id")

def get_side(lineups, side):
    # Flexible parser for v3/v2/v1 shapes.
    candidates = [
        side,
        side.lower(),
        side.replace("Team","_team").lower(),
        "home_team" if side=="homeTeam" else "away_team",
        "home" if side=="homeTeam" else "away",
    ]
    for k in candidates:
        if isinstance(lineups, dict) and k in lineups:
            return lineups[k]
    # Some APIs wrap the payload.
    for wrap in ("lineups","data","content"):
        if isinstance(lineups, dict) and isinstance(lineups.get(wrap), dict):
            found = get_side(lineups[wrap], side)
            if found is not None:
                return found
    return None

def players_from_side(side_obj):
    if side_obj is None:
        return []
    if isinstance(side_obj, list):
        return side_obj
    if isinstance(side_obj, dict):
        for k in ("players","lineup","starters","startingXI","starting_xi"):
            if isinstance(side_obj.get(k), list):
                return side_obj[k]
    return []

def pget(p, *keys):
    cur = p
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

def player_id(p):
    return (
        p.get("playerId") or p.get("id") or
        pget(p,"player","id") or pget(p,"player","playerId")
    )

def player_name(p):
    return (
        p.get("name") or p.get("displayName") or
        pget(p,"player","name") or pget(p,"player","displayName") or
        " ".join(filter(None,[pget(p,"player","firstName"),pget(p,"player","lastName")])).strip()
    )

def is_starter(p):
    # Prefer explicit fields if available.
    for k in ("starter","isStarter","starting","isStarting"):
        if k in p:
            return bool(p[k])
    for path in (("player","starter"),("player","isStarter")):
        v = pget(p,*path)
        if v is not None:
            return bool(v)
    # Common status/role encodings.
    txt = str(p.get("role") or p.get("status") or p.get("type") or "").lower()
    if txt:
        if any(x in txt for x in ("sub","bench")):
            return False
        if any(x in txt for x in ("start","lineup")):
            return True
    # In current v3 response, starting XI is typically first 11.
    return None

def fetch_season_matches(season_id):
    data = get_json("/api/v2/matches", {
        "competition": COMPETITION_ID,
        "season": season_id,
        "_limit": 500,
        "_sort": "kickoff:asc"
    })
    matches = as_list(data)
    if len(matches) < 300:
        raise RuntimeError(f"Only {len(matches)} matches returned for season {season_id}")
    return matches

all_rows = []
coverage = []
matches_out = []

for season_label, season_id in SEASONS.items():
    print(f"\n=== {season_label} ===", flush=True)
    matches = fetch_season_matches(season_id)
    print("matches:", len(matches), flush=True)

    for n, m in enumerate(matches, 1):
        mid = match_id(m)
        home = team_name(m.get("homeTeam",{}))
        away = team_name(m.get("awayTeam",{}))
        mw = m.get("matchWeek") or m.get("matchweek")
        kickoff = m.get("kickoff") or m.get("kickoffTime")
        matches_out.append({
            "Season": season_label, "SeasonID": season_id, "MatchID": mid,
            "Matchweek": mw, "Date": kickoff, "Home": home, "Away": away
        })

        lineups = None
        used_version = None
        err = None
        for ver in ("v3","v2","v1"):
            try:
                lineups = get_json(f"/api/{ver}/matches/{mid}/lineups")
                used_version = ver
                break
            except Exception as e:
                err = str(e)

        hobj = get_side(lineups, "homeTeam") if lineups is not None else None
        aobj = get_side(lineups, "awayTeam") if lineups is not None else None
        hp = players_from_side(hobj)
        ap = players_from_side(aobj)

        def extract_starting(ps):
            flags=[is_starter(p) for p in ps]
            if any(x is True for x in flags):
                return [p for p,f in zip(ps,flags) if f is True][:11]
            return ps[:11]

        hs = extract_starting(hp)
        a_s = extract_starting(ap)

        for side, team, ps in [("Home",home,hs),("Away",away,a_s)]:
            for i,p in enumerate(ps,1):
                all_rows.append({
                    "Season": season_label,
                    "SeasonID": season_id,
                    "MatchID": mid,
                    "Matchweek": mw,
                    "Date": kickoff,
                    "Home": home,
                    "Away": away,
                    "Side": side,
                    "Team": team,
                    "StarterNo": i,
                    "PlayerID": player_id(p),
                    "Player": player_name(p),
                    "RawJSON": json.dumps(p, ensure_ascii=False, separators=(",",":"))
                })

        coverage.append({
            "Season": season_label, "MatchID": mid, "Matchweek": mw,
            "Home": home, "Away": away, "HomeStarters": len(hs),
            "AwayStarters": len(a_s), "CompleteXI": len(hs)==11 and len(a_s)==11,
            "EndpointVersion": used_version, "Error": err if lineups is None else None
        })

        if n % 20 == 0 or n == len(matches):
            pd.DataFrame(all_rows).to_csv(OUT/"epl_starting_xi.csv", index=False)
            pd.DataFrame(coverage).to_csv(OUT/"epl_lineup_coverage.csv", index=False)
            pd.DataFrame(matches_out).to_csv(OUT/"epl_matches.csv", index=False)
            print(f"{n}/{len(matches)} | total complete: {sum(x['CompleteXI'] for x in coverage)}/{len(coverage)}", flush=True)
        time.sleep(0.08)

cov = pd.DataFrame(coverage)
xi = pd.DataFrame(all_rows)
mat = pd.DataFrame(matches_out)

print("\n=== FINAL COVERAGE ===")
print(cov.groupby("Season").agg(
    matches=("MatchID","nunique"),
    complete=("CompleteXI","sum"),
    min_home=("HomeStarters","min"),
    min_away=("AwayStarters","min")
).to_string())

# Hard validation; fail workflow rather than silently accepting bad extraction.
bad = cov[~cov.CompleteXI]
if len(mat) != 1900:
    print(f"WARNING: expected 1900 matches, got {len(mat)}", file=sys.stderr)
if len(bad):
    print(f"WARNING: {len(bad)} matches do not have complete 11+11 lineups.", file=sys.stderr)
    bad.to_csv(OUT/"epl_incomplete_lineups.csv", index=False)

xi.to_csv(OUT/"epl_starting_xi.csv", index=False)
cov.to_csv(OUT/"epl_lineup_coverage.csv", index=False)
mat.to_csv(OUT/"epl_matches.csv", index=False)
