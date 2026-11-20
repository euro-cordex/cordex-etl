"""Local Zarr v3 writes and durable JSON checkpoints (single writer per store)."""

import json
import os
from pathlib import Path
import tempfile

import xarray as xr

from .transform import assert_values_equal, validate_shared


def write_json(path, value):
    """Atomically replace a local checkpoint after flushing its contents."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, encoding="utf-8"
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def describe_array(array):
    return {
        "dimensions": list(array.dims),
        "shape": list(array.shape),
        "dtype": str(array.dtype),
    }


def validate_checkpoint(store, manifest):
    """Check that committed arrays still have the recorded structure."""
    if not manifest["variables"]:
        if store.exists():
            raise ValueError(f"Untracked Zarr store: {store}")
        return
    with xr.open_zarr(
        store, consolidated=False, chunks={}, decode_coords="all"
    ) as existing:
        for record in manifest["variables"].values():
            for name, expected in record["arrays"].items():
                if name not in existing.variables:
                    raise ValueError(
                        f"Committed array {name!r} is missing from {store}"
                    )
                if describe_array(existing[name]) != expected:
                    raise ValueError(f"Committed array {name!r} has changed in {store}")
        known = {
            name
            for record in manifest["variables"].values()
            for name in record["arrays"]
        }
        if set(existing.variables) != known:
            raise ValueError(f"Untracked arrays found in {store}")


def write_variable(dataset, variable, store, pending_path, chunks=None):
    """Add new arrays without rewriting shared arrays, then verify every new value."""
    dataset = dataset.copy()
    # NetCDF storage encodings are not valid Zarr creation settings.
    for name in dataset.variables:
        dataset[name].encoding = {
            key: value
            for key, value in dataset[name].encoding.items()
            if key
            in (
                "units",
                "calendar",
                "_FillValue",
                "coordinates",
                "bounds",
                "grid_mapping",
                "climatology",
                "cell_measures",
                "formula_terms",
                "ancillary_variables",
            )
        }
    applicable_chunks = {
        name: size for name, size in (chunks or {}).items() if name in dataset.dims
    }
    dataset = dataset.chunk(applicable_chunks or "auto")
    if store.exists():
        with xr.open_zarr(
            store, consolidated=False, chunks={}, decode_coords="all"
        ) as existing:
            # A requested field can already be a committed dependency of another item.
            validate_shared(dataset, existing)
            shared = set(dataset.variables) & set(existing.variables)
            global_attrs = dict(existing.attrs)
        addition = dataset.drop_vars(shared)
        addition.attrs = global_attrs
        mode = "a"
    else:
        addition = dataset
        mode = "w-"
    # Fail closed after a crash: only the orchestrator clears this after commit.
    write_json(pending_path, {"variable": variable, "store": str(store)})
    if addition.variables:
        addition.to_zarr(
            store,
            mode=mode,
            zarr_format=3,
            consolidated=False,
            encoding={
                name: {"chunks": tuple(axis[0] for axis in array.chunks)}
                for name, array in addition.variables.items()
                if array.chunks is not None
            },
        )
    with xr.open_zarr(
        store, consolidated=False, chunks={}, decode_coords="all"
    ) as written:
        for name in addition.variables:
            assert_values_equal(dataset[name], written[name], name)
        return {name: describe_array(written[name]) for name in dataset.variables}
