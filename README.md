# cordex-aws-etl

EURO-CORDEX transform and loading from ESGF to S3.

## Including fixed fields in discovered groups

```python
from cordex_etl.extract import get_items, group_items_by_attributes

items = get_items(
    domain_id="EUR-12",
    variable_id=["tas", "pr"],
    frequency=["mon", "day"],
    add_fx=["orog", "sftlf"],  # True discovers all available fixed fields.
)
groups = group_items_by_attributes(items, merge_fx=True)
```

Both options are disabled by default. Discovery searches separately for
`frequency="fx"`, replacing the variable and frequency filters while preserving
all other filters, the endpoint, and the collection. It warns per simulation
about missing requested fields; `add_fx=True` warns only when none match.
Discovery checks use the STAC identity excluding frequency, even with custom
grouping. Discovery returns all publications; default grouping selects the latest.

By default, group keys are strings derived from CORDEX-CMIP6 STAC item IDs by
removing the final variable and publication-version components. For example:

```text
Item:  CORDEX-CMIP6.DD.EUR-12.HCLIMcom-SMHI.MPI-ESM1-2-HR.ssp370.r1i1p1f1.HCLIM43-ALADIN.v1-r1.mon.tas.v20260914
Group: CORDEX-CMIP6.DD.EUR-12.HCLIMcom-SMHI.MPI-ESM1-2-HR.ssp370.r1i1p1f1.HCLIM43-ALADIN.v1-r1.mon
```

Use `list(groups)` directly for dataset IDs; do not join the keys as tuples.
The newest valid `vYYYYMMDD` publication is selected independently per variable,
including fx fields. Invalid IDs, invalid publication dates, conflicting
frequency/variable metadata, or distinct candidates tied for the newest version
raise `ValueError`. Repeated item IDs are deduplicated.

With `merge_fx=True`, fixed fields are appended to each matching temporal group's
item list without changing its key. Matching uses the full STAC-derived identity
except frequency, including the project and product prefixes.
No standalone fx groups are emitted,
and an empty temporal search creates no groups. This does not open or merge the
datasets; merging static fields into the data remains a later step.

Explicit `grouping_attrs` preserves attribute-based grouping with tuple keys and
all temporal publications. In that mode, fx matching uses only the supplied
attributes except frequency, and distinct matching fx items of the same variable
still raise `ValueError`, even if their publication versions differ.

## Sequential local ingestion

The initial ingestion pipeline writes **one local Zarr v3 store per dataset ID**,
downloading only one STAC item's NetCDF assets at a time:

```python
from cordex_etl.extract import get_items, group_items_by_attributes
from cordex_etl.workflow import ingest_groups

items = get_items(
    domain_id="EUR-12",
    driving_source_id="MPI-ESM1-2-HR",
    driving_experiment_id="historical",
    source_id="HCLIM43-ALADIN",
    variable_id=["tas", "pr"],
    frequency="mon",
    add_fx=["orog", "sftlf"],
)
groups = group_items_by_attributes(items, merge_fx=True)
stores = ingest_groups(
    groups,
    output_dir="./output",
    scratch_dir="./scratch",
    # Optional: use your source's actual dimension names.
    # chunks={"time": 120, "rlat": 128, "rlon": 128},
)
```

Without explicit chunk sizes, Dask automatic chunking is used. Chunk sizes must
be positive integers; dimensions absent from temporal data are rejected. Only
applicable dimensions are used for static fields. Sharding is not configured yet.

The stages are separated into:

- `extract`: discovery, grouping, and atomic HTTP(S) downloads.
- `transform`: variable/dependency selection and strict compatibility checks.
- `load`: incremental Zarr writes, full new-array value verification, checkpoints.
- `workflow`: sequencing, manifests, locking, and temporary-file cleanup.

Only HTTP(S) NetCDF assets are selected (NetCDF media type or `.nc`, `.nc4`,
`.cdf` suffix). All selected assets for **one item** must fit scratch storage;
this is not yet a one-file-at-a-time streaming pipeline. Datasets are closed and
synchronous writes/verification finish before temporary files are removed.

The first temporal item establishes coordinates. Subsequent items must match
shared dimensions, coordinate/dependency values, attributes, and calendars.
Shared arrays are validated but not rewritten. CF bounds, grid mappings, cell
measures, formula terms, and ancillary variables are retained when referenced.
Packed NetCDF values are written decoded, rather than retaining source packing.
No scientific fixes, regridding, resampling, or dtype narrowing are applied.

This version supports **monthly and daily groups only**, with exact coordinate
matching. Subdaily mean/point partitioning, source replacement, time appends,
S3, automatic partial-write recovery, and catalog publication are not implemented.
Sources sharing a frequency but incompatible coordinates fail explicitly.

### CLI

Discovery remains the default. Add `--ingest` and a local `--output-dir` to write:

```bash
python jobs/etl.py \
  --domain-id EUR-12 \
  --driving-source-id MPI-ESM1-2-HR \
  --driving-experiment-id historical \
  --source-id HCLIM43-ALADIN \
  --variable-id tas pr --frequency mon \
  --add-fx orog sftlf \
  --ingest --output-dir ./output --scratch-dir ./scratch \
  --chunk time=120
```

Add `--keep-scratch` (Python: `keep_scratch=True`) to leave the downloaded
files of a failing item in the scratch directory for debugging; the path is
logged. Successful items are still cleaned, and kept directories must be
removed manually.

The existing Docker entrypoint accepts the same arguments; mount writable local
volumes for output and scratch.

### Scalar vertical coordinates

Variables measured at different levels (for example `tas` at 2 m and `sfcWind`
at 10 m) carry a scalar `height` coordinate with different values. The first
variable keeps the name; a later variable whose value differs gets the
coordinate renamed to name + value + units (`height10m`, `plev850Pa`) and its
CF `coordinates` reference is updated. Equal values stay shared and are
validated. The naming therefore depends on ingest order; all scalar vertical
coordinates (height, plev, depth, lev, level, altitude, or axis `Z` /
`positive`) are handled. Bounds of scalar vertical coordinates are not renamed.

### Checkpoints and restarts

For each dataset ID, the output directory contains:

```text
<dataset_id>.zarr/          data
<dataset_id>.manifest.json verified per-variable provenance and array structure
<dataset_id>.pending.json  present during an uncommitted write
<dataset_id>.lock          present while a writer owns the store
```

The manifest records selected asset URLs/metadata, a source fingerprint,
completion times, and output array names/dimensions/shapes/dtypes. Each new array
is compared in full with decoded source values using Dask blocks before its
item is committed. A completed manifest means all items in the latest requested
group were ingested; it does not assert that every variable in ESGF is present.

Rerunning skips committed unchanged items without downloading them, and checks
that committed arrays still have their recorded structure. It does not reread
all previously committed data or detect externally missing/corrupted chunks.
Existing variables are retained when omitted from a later request. Changed
source IDs, properties, or selected asset metadata stop ingestion before any
new writes for that group. Fingerprints are conservative: even URL/metadata
changes can require review. They do not prove source-file immutability or verify
advertised checksums.

Failures are logged and stop the run. Download failures and pre-write validation
failures can be retried directly. Once a write starts, the pending marker remains
until verification and manifest persistence succeed. A pending marker or stale
lock requires explicit inspection; **do not simply remove it and trust arrays
that happen to exist**. For a failed bootstrap, rebuilding that group's output
in a fresh directory is the simplest recovery. Recovering a partially updated
existing store requires reconciling its arrays and manifest first.

Use one writer per store. Writes and the manifest are **not a transaction**:
readers can see partially written arrays. During bootstrap, use an unpublished
output directory/prefix and advertise a store only after its manifest is complete.
Coordinate reader access separately during later updates.

### Tests

```bash
python -m pytest cordex_etl/tests
```

Integration tests serve synthetic NetCDF files over local HTTP and exercise
sequential ingestion, resume/addition, shared-coordinate preservation, dependency
handling, full verification, and failure checkpoints without contacting ESGF.
