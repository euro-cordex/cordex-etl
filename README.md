# cordex-etl

EURO-CORDEX ETL: discover CORDEX-CMIP6 datasets in the ESGF STAC catalog and
load them incrementally into one Zarr v3 store per dataset.

## Quick start

```bash
python jobs/etl.py \
  --domain-id EUR-12 --driving-source-id MPI-ESM1-2-HR \
  --driving-experiment-id historical --source-id HCLIM43-ALADIN \
  --variable-id tas pr --frequency mon --add-fx orog sftlf \
  --ingest --output-dir ./output --scratch-dir ./scratch
```

Without `--ingest` the command only lists the discovered dataset groups.

```python
from cordex_etl.extract import get_items, group_items_by_attributes
from cordex_etl.workflow import ingest_groups

items = get_items(domain_id="EUR-12", variable_id=["tas", "pr"],
                  frequency="mon", add_fx=["orog", "sftlf"])
groups = group_items_by_attributes(items, merge_fx=True)
stores = ingest_groups(groups, output_dir="./output", scratch_dir="./scratch")
```

## Pipeline

| Stage | Module | Role |
|---|---|---|
| Extract | `extract.py` | STAC discovery, grouping, fx fields, atomic downloads |
| Transform | `transform.py` | Variable selection, compatibility checks |
| Load | `load.py` | Incremental Zarr writes, verification |
| Orchestration | `workflow.py` | Sequencing, manifests, locking, cleanup |

Currently supported: monthly and daily data, local output, one writer per
store. Not yet implemented: S3 output, regridding, subdaily data, replacement of
changed publications, and the ensemble layer.

## Documentation

- [Discovery and grouping](docs/discovery.md): fx fields, group keys, search filters.
- [Sequential ingestion](docs/ingestion.md): API, CLI, checkpoints and restarts,
  vertical coordinates, tests.
- [Ensemble design](docs/ensemble-design.md): planned ensemble layer and open decisions.

## Development

```bash
python -m pytest cordex_etl/tests
```

See [AGENTS.md](AGENTS.md) for conventions.
