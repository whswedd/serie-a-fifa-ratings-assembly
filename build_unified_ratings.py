#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(".")
RAW = ROOT / "data/raw/ratings"
OUT = ROOT / "data/processed"
RAW.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

SERIE_A_TOKENS = {
    "serie a", "serie a tim", "italy serie a", "italian serie a",
    "campionato italiano", "serie a enilive"
}

STANDARD_COLUMNS = [
    "Season","Edition","PlayerID","Player","FullName","DOB","Age",
    "Club","League","Nationality","Overall","Positions",
    "PrimaryPosition","Potential","SourceType","SourceDataset",
    "SourceFile","SourceURL"
]

def norm(s):
    if pd.isna(s):
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def col(df, *names):
    lower = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None

def series(df, *names, default=""):
    c = col(df, *names)
    if c is None:
        return pd.Series([default] * len(df), index=df.index)
    return df[c]

def get(url, timeout=120):
    r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r

def kaggle_files(dataset):
    owner, slug = dataset.split("/", 1)
    urls = [
        f"https://www.kaggle.com/api/v1/datasets/view/{owner}/{slug}",
        f"https://www.kaggle.com/api/v1/datasets/list/{owner}/{slug}",
    ]
    last = None
    for u in urls:
        try:
            data = get(u).json()
            files = data.get("resources") or data.get("files") or []
            out = []
            for x in files:
                name = x.get("path") or x.get("name") or x.get("ref")
                size = x.get("size") or x.get("totalBytes") or 0
                if name:
                    out.append((name, size))
            if out:
                return out
        except Exception as e:
            last = e
    raise RuntimeError(f"Could not list Kaggle files for {dataset}: {last}")

def choose_kaggle_file(files, patterns):
    names = [x[0] for x in files]
    for p in patterns:
        for n in names:
            if n.lower() == p.lower():
                return n
    # Prefer CSVs that clearly describe male/player data and avoid teams/coaches.
    csvs = [n for n in names if n.lower().endswith(".csv")]
    scored = []
    for n in csvs:
        z = n.lower()
        score = 0
        if "male" in z: score += 8
        if "player" in z: score += 8
        if "legacy" in z: score += 5
        if "team" in z or "coach" in z or "female" in z: score -= 20
        scored.append((score, n))
    if scored:
        scored.sort(reverse=True)
        return scored[0][1]
    raise RuntimeError("No CSV found in Kaggle dataset")

def download_kaggle_file(dataset, filename, dest):
    owner, slug = dataset.split("/", 1)
    # Kaggle individual-file endpoint. Public datasets normally do not require a token.
    candidates = [
        f"https://www.kaggle.com/api/v1/datasets/download/{owner}/{slug}/{filename}",
        f"https://www.kaggle.com/api/v1/datasets/download/{owner}/{slug}?filename={filename}",
    ]
    last = None
    for u in candidates:
        try:
            r = get(u, timeout=300)
            content = r.content
            if content[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    members = z.namelist()
                    target = None
                    for m in members:
                        if m == filename or Path(m).name == Path(filename).name:
                            target = m
                            break
                    if target is None and len(members) == 1:
                        target = members[0]
                    if target is None:
                        raise RuntimeError(f"{filename} not in returned zip: {members[:20]}")
                    dest.write_bytes(z.read(target))
            else:
                dest.write_bytes(content)
            if dest.stat().st_size > 100:
                return u
        except Exception as e:
            last = e
    raise RuntimeError(f"Failed Kaggle download {dataset}/{filename}: {last}")

def load_source(season, cfg, force=False):
    edition = cfg["edition"]
    safe = edition.lower().replace(" ","_").replace("/","_")
    if cfg["type"] == "url_csv":
        dest = RAW / f"{safe}.csv"
        chosen_url = cfg["url"]
        if force or not dest.exists():
            urls = [cfg["url"]] + list(cfg.get("fallback_urls", []))
            last = None
            for u in urls:
                try:
                    print(f"Downloading {edition}: {u}", flush=True)
                    r = get(u, timeout=300)
                    if len(r.content) < 100:
                        raise RuntimeError(f"Response too small: {len(r.content)} bytes")
                    dest.write_bytes(r.content)
                    chosen_url = u
                    break
                except Exception as e:
                    last = e
                    print(f"  source failed: {e}", flush=True)
            else:
                raise RuntimeError(
                    f"All direct sources failed for {edition}. Last error: {last}"
                )
        return pd.read_csv(dest, low_memory=False), dest.name, chosen_url

    if cfg["type"] == "kaggle_dataset":
        listing = kaggle_files(cfg["dataset"])
        chosen = choose_kaggle_file(listing, cfg.get("preferred_patterns", []))
        dest = RAW / f"{safe}__{Path(chosen).name}"
        if force or not dest.exists():
            print(f"Downloading {edition} from Kaggle: {cfg['dataset']} / {chosen}", flush=True)
            src_url = download_kaggle_file(cfg["dataset"], chosen, dest)
        else:
            src_url = f"kaggle:{cfg['dataset']}/{chosen}"
        df = pd.read_csv(dest, low_memory=False)

        # Some canonical datasets contain multiple FIFA versions/updates.
        version_col = col(df, "fifa_version", "version")
        wanted = cfg.get("filter_fifa_version")
        if wanted is not None and version_col is not None:
            vals = pd.to_numeric(df[version_col], errors="coerce")
            if (vals == wanted).any():
                df = df.loc[vals == wanted].copy()

            # If multiple updates exist, keep the latest row per player.
            pid = col(df, "player_id", "sofifa_id", "id")
            update = col(df, "update_as_of", "fifa_update_date", "version_date")
            if pid and update:
                tmp = pd.to_datetime(df[update], errors="coerce")
                df = df.assign(_update_sort=tmp).sort_values("_update_sort")
                df = df.drop_duplicates(pid, keep="last").drop(columns="_update_sort")

        return df, chosen, src_url

    raise ValueError(cfg["type"])

def standardize(df, season, cfg, source_file, source_url):
    out = pd.DataFrame(index=df.index)
    out["Season"] = season
    out["Edition"] = cfg["edition"]

    out["PlayerID"] = series(df, "player_id","sofifa_id","id","playerid")
    out["Player"] = series(df, "short_name","name","player_name","long_name")
    out["FullName"] = series(df, "long_name","full_name","fullname","name")
    out["DOB"] = series(df, "dob","date_of_birth","birthdate")
    out["Age"] = series(df, "age")
    out["Club"] = series(df, "club_name","club","team","team_name")
    out["League"] = series(df, "league_name","league","competition")
    out["Nationality"] = series(df, "nationality_name","nationality","nation","country")
    out["Overall"] = pd.to_numeric(
        series(df, "overall","overall_rating","overallrating","ovr"),
        errors="coerce"
    )
    out["Potential"] = pd.to_numeric(
        series(df, "potential","potential_rating"),
        errors="coerce"
    )
    out["Positions"] = series(
        df, "player_positions","positions","alternative positions","position"
    )
    out["PrimaryPosition"] = series(
        df, "club_position","best_position","primary_position","position"
    )

    out["SourceType"] = cfg["type"]
    out["SourceDataset"] = cfg.get("dataset","")
    out["SourceFile"] = source_file
    out["SourceURL"] = source_url

    # Fill missing Player with FullName and vice versa.
    out["Player"] = out["Player"].where(out["Player"].astype(str).str.strip()!="", out["FullName"])
    out["FullName"] = out["FullName"].where(out["FullName"].astype(str).str.strip()!="", out["Player"])

    return out[STANDARD_COLUMNS]

def is_serie_a(row):
    league = norm(row["League"])
    club = norm(row["Club"])
    if any(tok in league for tok in SERIE_A_TOKENS):
        return True
    # Do not guess based purely on club here; club filtering is done against
    # actual lineup-team names when --lineups is supplied.
    return False

def filter_with_lineups(df, lineup_path, season):
    if not lineup_path or not Path(lineup_path).exists():
        league_mask = df.apply(is_serie_a, axis=1)
        return df.loc[league_mask].copy(), "league_field"

    li = pd.read_csv(lineup_path, low_memory=False)
    li = li.loc[li["Season"].astype(str) == season].copy()
    teams = {norm(x) for x in li["Team"].dropna().unique()}
    players = {norm(x) for x in li["Player"].dropna().unique()}

    club_match = df["Club"].map(norm).isin(teams)
    player_match = df["Player"].map(norm).isin(players) | df["FullName"].map(norm).isin(players)
    league_match = df.apply(is_serie_a, axis=1)
    # Include any player actually appearing in the season's Serie A lineups,
    # even if the source lists a transfer club outside Serie A.
    keep = league_match | club_match | player_match
    return df.loc[keep].copy(), "lineups+league"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="sources.json")
    ap.add_argument("--lineups", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cfgs = json.loads(Path(args.sources).read_text())
    unified = []
    audit = []

    for season, cfg in cfgs.items():
        print(f"\n=== {season} / {cfg['edition']} ===", flush=True)
        raw, source_file, source_url = load_source(season, cfg, force=args.force)
        std = standardize(raw, season, cfg, source_file, source_url)

        before = len(std)
        std = std.loc[std["Overall"].notna()].copy()
        with_rating = len(std)

        filtered, filter_method = filter_with_lineups(std, args.lineups, season)
        filtered = filtered.drop_duplicates(
            subset=["Season","PlayerID","Player","Club"], keep="first"
        )

        print(
            f"raw={before:,} with_overall={with_rating:,} "
            f"serie_a_relevant={len(filtered):,}",
            flush=True
        )

        unified.append(filtered)
        audit.append({
            "Season": season,
            "Edition": cfg["edition"],
            "RawRows": before,
            "RowsWithOverall": with_rating,
            "SerieARelevantRows": len(filtered),
            "FilterMethod": filter_method,
            "SourceType": cfg["type"],
            "SourceDataset": cfg.get("dataset",""),
            "SourceFile": source_file,
            "SourceURL": source_url,
        })

    all_df = pd.concat(unified, ignore_index=True)
    all_df.to_csv(OUT/"serie_a_fifa_ea_ratings_2021_26.csv", index=False)
    pd.DataFrame(audit).to_csv(OUT/"serie_a_fifa_ea_source_audit.csv", index=False)

    # Lightweight matching diagnostic against the actual starting-XI file.
    if args.lineups and Path(args.lineups).exists():
        li = pd.read_csv(args.lineups, low_memory=False)
        rows = []
        for season in cfgs:
            l = li.loc[li["Season"].astype(str)==season].copy()
            r = all_df.loc[all_df["Season"]==season].copy()
            names = set(r["Player"].map(norm)) | set(r["FullName"].map(norm))
            l["_matched_name"] = l["Player"].map(norm).isin(names)
            rows.append({
                "Season": season,
                "StarterRows": len(l),
                "ExactNameMatched": int(l["_matched_name"].sum()),
                "ExactNameCoverage": float(l["_matched_name"].mean()) if len(l) else 0,
                "DistinctStarters": int(l["Player"].nunique()),
                "DistinctRatingPlayers": int(r["Player"].nunique()),
            })
        pd.DataFrame(rows).to_csv(
            OUT/"serie_a_fifa_ea_exact_name_coverage.csv", index=False
        )

    print("\nWrote:", flush=True)
    for p in sorted(OUT.glob("*.csv")):
        print(f"  {p} ({p.stat().st_size:,} bytes)", flush=True)

if __name__ == "__main__":
    main()
