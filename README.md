# Serie A FIFA / EA ratings assembler — v1

Builds one normalized five-edition player-rating table for the Serie A V2/FIFA experiment.

## Editions

| Serie A season | Edition |
|---|---|
| 2021-22 | FIFA 22 |
| 2022-23 | FIFA 23 |
| 2023-24 | EA FC 24 |
| 2024-25 | EA FC 25 |
| 2025-26 | EA FC 26 |

## Main output

`data/processed/serie_a_fifa_ea_ratings_2021_26.csv`

Columns:

`Season, Edition, PlayerID, Player, FullName, DOB, Age, Club, League,
Nationality, Overall, Positions, PrimaryPosition, Potential, SourceType,
SourceDataset, SourceFile, SourceURL`

It also writes:

- `serie_a_fifa_ea_source_audit.csv`
- `serie_a_fifa_ea_exact_name_coverage.csv` when the lineup CSV is supplied

## Recommended setup

Copy the cleaned starting-XI file into the repository as:

`data/serie_a_starting_xi.csv`

Then run:

**Actions → Build Serie A FIFA-EA ratings table → Run workflow**

Keep `use_lineups = true`.

Using the starting-XI file is preferable because players transferred during a season can otherwise be
missed by a simple "current club/league == Serie A" filter.

## Source behavior

The source manifest is `sources.json`. Every output row records the exact source dataset/file.

The script is deliberately schema-tolerant. It recognizes common SoFIFA / EA field variants such as:
- `sofifa_id` / `player_id` / `id`
- `short_name` / `name`
- `overall` / `overall_rating` / `OVR`
- `club_name` / `club` / `team`
- `player_positions` / `positions`

For Kaggle sources, it uses the public dataset API and selects a preferred player CSV.
If a canonical dataset contains multiple FIFA versions or updates, it filters to the requested
edition and keeps the latest available snapshot per player where an update date exists.

## Important

This assembler does **not** fuzzy-match lineups yet. The exact-name coverage CSV is intentionally
a first diagnostic. The next step is to run the EPL-style player matcher using player name,
club/team, position and age/DOB to resolve aliases and transfers safely.


## v2 fix

v2 replaces the obsolete FIFA 23 GitHub mirror with the canonical public Kaggle dataset:

`stefanoleone992/fifa-23-complete-player-dataset`

The downloader also now supports fallback URLs for direct CSV sources. A single dead mirror will no longer immediately terminate the build.


## v3 fix

v3 fixes Kaggle metadata parsing. Current Kaggle dataset metadata can expose files under
`datasetFiles`; v2 only checked older `resources` / `files` fields.

For FIFA 23, v3 explicitly prefers `male_players (legacy)_23.csv`, the compact one-row-per-player
snapshot, instead of the 5+ GB multi-update history table.

The run log now prints:
- Kaggle file names returned by metadata
- the selected source file
- downloaded byte size
- detailed endpoint errors if a source fails


## v4 lean-build changes

- FIFA 23 now uses the compact public GitHub snapshot:
  `YoNG-Zaii/MH3511-FIFA-Analysis/fifa23_players_data.csv`
- Full Kaggle dataset ZIP fallback is **disabled**.
- Direct source downloads larger than 100 MB are refused.
- The build only uses compact one-row-per-player snapshots.
- Added schema aliases for the compact FIFA 23 columns:
  `Known As`, `Full Name`, `Club Name`, `Positions Played`, `Best Position`.
