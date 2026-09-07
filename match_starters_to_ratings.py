#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, unicodedata
from pathlib import Path
import pandas as pd
from rapidfuzz import fuzz

def norm(s):
    if pd.isna(s): return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def surname_key(s):
    z = norm(s).split()
    return z[-1] if z else ""

def first_initial(s):
    z = norm(s).split()
    return z[0][0] if z and z[0] else ""

def best_match(starter, candidates):
    sp = norm(starter["Player"])
    steam = norm(starter["Team"])
    sage = starter.get("Age", None)

    best = None
    for _, r in candidates.iterrows():
        names = [norm(r.get("Player","")), norm(r.get("FullName",""))]
        names = [x for x in names if x]
        if not names:
            continue

        name_score = max(fuzz.ratio(sp, n) for n in names)
        token_score = max(fuzz.token_sort_ratio(sp, n) for n in names)
        score = 0.7*name_score + 0.3*token_score

        club = norm(r.get("Club",""))
        if steam and club:
            club_score = fuzz.ratio(steam, club)
            if club_score >= 90:
                score += 8
            elif club_score >= 75:
                score += 4

        rage = r.get("Age", None)
        try:
            if pd.notna(sage) and pd.notna(rage):
                d = abs(float(sage)-float(rage))
                if d <= 1: score += 3
                elif d >= 4: score -= 4
        except Exception:
            pass

        # surname and first-initial consistency are useful for common names.
        if surname_key(sp) and surname_key(sp) == surname_key(r.get("FullName","")):
            score += 4
        if first_initial(sp) and first_initial(sp) == first_initial(r.get("FullName","")):
            score += 1

        if best is None or score > best[0]:
            best = (score, r)

    return best

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--lineups", required=True)
    ap.add_argument("--outdir", default="data/processed")
    args = ap.parse_args()

    ratings = pd.read_csv(args.ratings, low_memory=False)
    lineups = pd.read_csv(args.lineups, low_memory=False)

    # Exclude the 2022-23 relegation playoff already identified in the project.
    if "MatchID" in lineups.columns:
        lineups = lineups.loc[lineups["MatchID"] != 4185671].copy()

    out = []
    audit = []

    for season in sorted(lineups["Season"].astype(str).unique()):
        L = lineups.loc[lineups["Season"].astype(str)==season].copy()
        R = ratings.loc[ratings["Season"].astype(str)==season].copy()

        # We only need one distinct starter-season identity for the unified map.
        U = (L.sort_values(["Player","DateUTC"])
               .drop_duplicates(["Player"], keep="first")
               .copy())

        # Precompute normalized names.
        R["_p"] = R["Player"].map(norm)
        R["_f"] = R["FullName"].map(norm)
        exact_map = {}
        for i, r in R.iterrows():
            for k in {r["_p"], r["_f"]}:
                if k:
                    exact_map.setdefault(k, []).append(i)

        season_rows = []
        for _, s in U.iterrows():
            k = norm(s["Player"])
            hit_idx = exact_map.get(k, [])

            method = ""
            score = None
            chosen = None

            if len(hit_idx) == 1:
                chosen = R.loc[hit_idx[0]]
                method = "exact"
                score = 100.0
            elif len(hit_idx) > 1:
                subset = R.loc[hit_idx]
                # Disambiguate exact-name duplicates with club and age.
                bm = best_match(s, subset)
                if bm:
                    score, chosen = bm
                    method = "exact_disambiguated"
            else:
                # Candidate blocking: surname first, otherwise full season pool.
                sk = surname_key(k)
                cand = R[
                    (R["Player"].map(surname_key)==sk) |
                    (R["FullName"].map(surname_key)==sk)
                ] if sk else R.iloc[0:0]

                if len(cand) == 0:
                    cand = R

                bm = best_match(s, cand)
                if bm and bm[0] >= 84:
                    score, chosen = bm
                    method = "fuzzy"

            row = {
                "Season": season,
                "FotMobPlayerID": s.get("PlayerID",""),
                "StarterPlayer": s["Player"],
                "StarterTeam": s.get("Team",""),
                "StarterAge": s.get("Age",""),
                "Matched": chosen is not None,
                "MatchMethod": method if chosen is not None else "unmatched",
                "MatchScore": score if score is not None else "",
            }

            if chosen is not None:
                for c in [
                    "Edition","PlayerID","Player","FullName","DOB","Age","Club",
                    "League","Nationality","Overall","Positions","PrimaryPosition",
                    "Potential","SourceType","SourceDataset","SourceFile","SourceURL"
                ]:
                    row["Rating_"+c] = chosen.get(c,"")
            season_rows.append(row)

        S = pd.DataFrame(season_rows)
        out.append(S)
        audit.append({
            "Season": season,
            "DistinctStarters": len(U),
            "Matched": int(S["Matched"].sum()),
            "Coverage": float(S["Matched"].mean()),
            "Exact": int((S["MatchMethod"]=="exact").sum()),
            "ExactDisambiguated": int((S["MatchMethod"]=="exact_disambiguated").sum()),
            "Fuzzy": int((S["MatchMethod"]=="fuzzy").sum()),
            "Unmatched": int((S["MatchMethod"]=="unmatched").sum()),
        })

    O = Path(args.outdir)
    O.mkdir(parents=True, exist_ok=True)
    matches = pd.concat(out, ignore_index=True)
    matches.to_csv(O/"serie_a_starter_rating_matches_2021_26.csv", index=False)
    pd.DataFrame(audit).to_csv(O/"serie_a_starter_rating_match_audit.csv", index=False)

    # Unified five-edition table = one row per distinct actual Serie A starter-season.
    unified = matches.loc[matches["Matched"]].copy()
    unified.to_csv(O/"serie_a_unified_fifa_ea_starter_ratings_2021_26.csv", index=False)

    print(pd.DataFrame(audit).to_string(index=False))
    print("\nWrote:")
    for p in sorted(O.glob("serie_a_*rating*.csv")):
        print(" ", p, p.stat().st_size)

if __name__ == "__main__":
    main()
