# Ensemble design

Status: design agreed, ensemble layer **not implemented yet**. Update this file
whenever a decision changes and record the reason.

## Goal

Publish a EURO-CORDEX CMIP6 ensemble (balanced GCM-RCM-SSP matrix) that others
can read, with analyses running locally against S3. Scope to start: about a
dozen core variables, monthly then daily frequency, a couple of TB in total.

The matrix is sparse (roughly 26 of 36 GCM x RCM cells, for SSP1-2.6 and
SSP3-7.0), so missing cells are expected and must be explicit.

## Layers

1. **Member stores** (implemented): one plain Zarr v3 store per STAC dataset id
   (one simulation and frequency) on the native grid. Written by
   `cordex_etl.workflow.ingest_groups`.
2. **Regridded stores** (not implemented): only members that are not on the
   target grid, in a separate location. Never overwrite a member store. Record
   method, target grid and weights hash as provenance attributes.
3. **Ensemble layer** (not implemented): all members on one grid, one
   repository per frequency, one group per variable.
4. **Catalog** (not implemented): one row per member with grid, regridded or
   not, store path, publication version used, and ensemble tags. Resolves a
   member to its original or regridded store.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Ensemble layout | Dense `gcm x rcm x scenario` | Matches the balanced-matrix analysis (select, mean over a row or column) |
| Empty cells | Boolean `available(gcm, rcm, scenario)` array stored in the dataset | NaN is ambiguous (missing run vs. masked value); the data explains its own gaps |
| Chunking | Size 1 along `gcm`, `rcm`, `scenario`; a block along time (e.g. `time=120`); consider sharding | All-fill chunks are not written, so empty cells cost almost nothing |
| Target grid | EUR-12 | Most members are already on it |
| Calendar | Convert every member to one calendar | One shared `time` coordinate; record original calendar and method per member |
| Published format | Icechunk for the ensemble layer | Consistent reads and versioned snapshots over S3 |
| ETL layer format | Plain Zarr v3 stays | Icechunk would only replace the pending-marker and manifest scheme later |
| VirtualiZarr | Not used | Members are already converted; the ensemble should be a stable copy |
| Materialization | Real copy of the data | A couple of TB is manageable |
| Regridding | Before assembling; weights kept per source-target pair | Conservative for precipitation and fluxes, bilinear for smooth fields (per variable) |

## Open questions

- **Daily calendar:** which target calendar? Monthly can be relabelled by
  year-month without loss. Daily conversion creates leap-day gaps, and 360_day
  members cannot be converted without interpolating or dropping days. Check
  which calendars the daily data uses before choosing; decide whether to
  interpolate or exclude such members.
- **Historical period:** store it once and let the helper join it to each
  scenario, or duplicate it along time per scenario.
- **Per-variable height names:** the member stores rename conflicting scalar
  vertical coordinates to name + value + units (`height10m`) depending on ingest
  order. The ensemble needs one consistent name per variable across members.
- **Publication versions:** the ETL takes the latest version per variable;
  releases must record which version each member used.
- **Publishing:** license, DOI, CF/CORDEX global attributes, per-release README.
- **Icechunk without copying:** untested whether it can reference member Zarr
  chunks without copying them.

## Suggested order

1. Catalog with a grid identifier per store.
2. `open_ensemble(members, grid="EUR-12")` helper that fails clearly when
   members are on different grids and builds the `available` mask.
3. Regridding step that writes the extra stores and registers them.
4. Materialized Icechunk ensemble per frequency.
5. Release process (versions, README, DOI).

A member whose `rlat`/`rlon` differ fails `validate_shared`; that is the signal
that it needs regridding.
