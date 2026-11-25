# Project guidelines

CORDEX-CMIP6 ETL: discover datasets in the ESGF STAC catalog, group them, and
load them incrementally into one Zarr v3 store per dataset. See
[README.md](README.md) for usage and
[docs/ensemble-design.md](docs/ensemble-design.md) for the planned ensemble
layer and open decisions.

## Build and test

- Tests (synthetic NetCDF served locally; needs `netCDF4`):
  `PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with-editable . python -m pytest cordex_etl/tests -q -p no:cacheprovider`
- `test_live_stac.py` needs network and skips when ESGF is unreachable.
- Format with `black`; keep `git diff --check` clean.
- The system has no `python`; use `python3` or `uv`.

## Conventions

- Extract (`extract.py`), transform (`transform.py`), load (`load.py`) and
  orchestration (`workflow.py`) are separate modules.
- Use CQL2 `filter=` (`filter_lang="cql2-json"`) for STAC searches. The ESGF
  servers ignore the `query=` extension and silently return unfiltered items.
- Never disable TLS verification (`verify=False`).
- Group keys are the STAC id without variable and version; the newest
  publication per variable is selected.
- Never rewrite or replace existing shared Zarr arrays; validate them
  (`transform.validate_shared`) and only add new arrays.
- Pass Zarr chunks through the variable's `.encoding`, not the `to_zarr`
  `encoding=` argument, which replaces the encoding and rejects CF keys.
- Keep source `units`, `calendar` and `dtype` for datetime arrays, so appends
  match the stored arrays.
- Debug a failing ingestion with `--keep-scratch` (Python: `keep_scratch=True`).
