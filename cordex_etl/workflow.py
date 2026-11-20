"""Sequential, restartable local ingestion of discovered CORDEX dataset groups."""

from datetime import datetime, timezone
from contextlib import ExitStack
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import xarray as xr

from .extract import _stac_identity, download_item_assets
from .load import validate_checkpoint, write_json, write_variable
from .transform import select_variable

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _data_assets(item):
    assets = {}
    for key, asset in item.assets.items():
        parsed = urlsplit(asset.href)
        media_type = (asset.media_type or "").lower()
        if (
            parsed.path.lower().endswith((".nc", ".nc4", ".cdf"))
            or "netcdf" in media_type
        ):
            if parsed.scheme not in ("http", "https"):
                raise ValueError(
                    f"Only HTTP(S) NetCDF assets are supported: {asset.href}"
                )
            assets[key] = asset
    if not assets:
        raise ValueError(f"No HTTP(S) NetCDF assets in {item.id!r}")
    return assets


def _source_record(item, assets):
    source = {
        "item_id": item.id,
        "properties": item.properties,
        "assets": {
            key: {"href": asset.href, "metadata": asset.extra_fields}
            for key, asset in sorted(assets.items())
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"item_id": item.id, "fingerprint": fingerprint, "assets": source["assets"]}


def ingest_groups(groups, output_dir, scratch_dir=None, chunks=None):
    """Ingest default STAC-ID groups into one local Zarr v3 store per group.

    Only mon/day groups with identical shared coordinates are supported.
    Each item is downloaded and closed/cleaned before the next item starts.
    Unchanged committed sources are skipped. Changed sources, partial writes,
    and untracked stores raise instead of overwriting data. Run only one writer
    per store; lock files left by a killed process require explicit inspection.

    Returns a mapping of dataset IDs to their local Zarr paths.
    """
    if "://" in str(output_dir):
        raise ValueError("Only local output directories are supported")
    if chunks is not None and (
        not isinstance(chunks, dict)
        or any(
            not isinstance(size, int) or isinstance(size, bool) or size <= 0
            for size in chunks.values()
        )
    ):
        raise ValueError("chunks must map dimension names to positive integers")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if scratch_dir is not None:
        Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    results = {}
    for dataset_id, entries in groups.items():
        if not isinstance(dataset_id, str) or Path(dataset_id).name != dataset_id:
            raise ValueError(
                "Ingestion requires default STAC-derived string group keys"
            )
        items = list(entries)
        if not items:
            raise ValueError(f"Empty dataset group {dataset_id!r}")
        variables = set()
        plan = []
        for item in items:
            key, variable, _ = _stac_identity(item)
            frequency = key.rsplit(".", 1)[1]
            if dataset_id.rsplit(".", 1)[-1] not in ("mon", "day"):
                raise ValueError("Only mon/day dataset groups are supported")
            if key != dataset_id and not (
                frequency == "fx"
                and key.rsplit(".", 1)[0] == dataset_id.rsplit(".", 1)[0]
            ):
                raise ValueError(f"Item {item.id!r} does not belong to {dataset_id!r}")
            if variable in variables:
                raise ValueError(f"Multiple items for variable {variable!r}")
            variables.add(variable)
            assets = _data_assets(item)
            plan.append((item, variable, assets, _source_record(item, assets)))
        # A temporal item establishes coordinates before any fx item is written.
        plan.sort(
            key=lambda entry: entry[0].properties["cordex-cmip6:frequency"] == "fx"
        )
        if all(item.properties["cordex-cmip6:frequency"] == "fx" for item in items):
            raise ValueError(f"No temporal items in {dataset_id!r}")
        store = root / f"{dataset_id}.zarr"
        manifest_path = root / f"{dataset_id}.manifest.json"
        pending = root / f"{dataset_id}.pending.json"
        lock = root / f"{dataset_id}.lock"
        try:
            with lock.open("x") as handle:
                handle.write(f"Started {_now()}\n")
        except FileExistsError as exc:
            raise RuntimeError(
                f"Store is locked; inspect active/stale writer: {lock}"
            ) from exc
        try:
            if pending.exists():
                raise RuntimeError(
                    f"Incomplete write detected; inspect {pending} and {store}. "
                    "Explicit recovery is required before retrying."
                )
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                if (
                    manifest.get("schema_version") != 1
                    or manifest.get("dataset_id") != dataset_id
                ):
                    raise ValueError(
                        f"Invalid manifest identity/schema: {manifest_path}"
                    )
            else:
                manifest = {
                    "schema_version": 1,
                    "dataset_id": dataset_id,
                    "variables": {},
                    "status": "incomplete",
                }
            validate_checkpoint(store, manifest)
            # Detect changed sources before making any writes for this group.
            for _, variable, _, source in plan:
                previous = manifest["variables"].get(variable)
                if previous is not None and previous["source"] != source:
                    raise ValueError(
                        f"Source changed for {dataset_id}/{variable}; "
                        "replacement is not supported yet"
                    )
            manifest["status"] = "incomplete"
            manifest["requested_variables"] = sorted(variables)
            write_json(manifest_path, manifest)
            for item, variable, assets, source in plan:
                if variable in manifest["variables"]:
                    logger.info("Skipping verified item %s", item.id)
                    continue
                logger.info("Ingesting %s", item.id)
                with tempfile.TemporaryDirectory(
                    dir=scratch_dir, prefix="cordex-etl-"
                ) as scratch:
                    files = download_item_assets(
                        item,
                        output_dir=scratch,
                        skip_existing=False,
                        asset_keys=assets,
                        raise_on_error=True,
                    )
                    with ExitStack() as stack:
                        sources = [
                            stack.enter_context(
                                xr.open_dataset(
                                    path, decode_coords="all", chunks="auto"
                                )
                            )
                            for path in files.values()
                        ]
                        dataset = xr.combine_by_coords(
                            sources,
                            join="exact",
                            compat="equals",
                            data_vars="minimal",
                            coords="minimal",
                            combine_attrs="override",
                        )
                        selected = select_variable(dataset, variable)
                        selected.attrs = {**selected.attrs, "dataset_id": dataset_id}
                        if item.properties["cordex-cmip6:frequency"] != "fx":
                            if "time" not in selected.indexes:
                                raise ValueError(f"No indexed time axis in {item.id!r}")
                            time = selected.indexes["time"]
                            if not time.is_unique or not time.is_monotonic_increasing:
                                raise ValueError(
                                    f"Time axis is not unique/increasing in {item.id!r}"
                                )
                            unknown = set(chunks or {}) - set(selected.dims)
                            if unknown:
                                raise ValueError(
                                    f"Unknown chunk dimensions: {sorted(unknown)}"
                                )
                        elif "time" in selected.dims:
                            raise ValueError(
                                f"Static field has a time dimension: {item.id!r}"
                            )
                        arrays = write_variable(
                            selected, variable, store, pending, chunks
                        )
                    manifest["variables"][variable] = {
                        "source": source,
                        "arrays": arrays,
                        "written_at": _now(),
                    }
                    write_json(manifest_path, manifest)
                    pending.unlink()
            manifest["status"] = "complete"
            manifest["completed_at"] = _now()
            write_json(manifest_path, manifest)
            results[dataset_id] = store
        except Exception:
            logger.exception("Ingestion failed for %s", dataset_id)
            raise
        finally:
            lock.unlink()
    return results
