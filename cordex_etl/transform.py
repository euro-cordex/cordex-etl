"""Variable selection and strict compatibility checks for incremental ingestion."""

import re

import xarray as xr


def _references(variable):
    refs = set()
    metadata = {**variable.attrs, **variable.encoding}
    for key in ("coordinates", "ancillary_variables", "bounds", "climatology"):
        refs.update(str(metadata.get(key, "")).split())
    mapping = str(metadata.get("grid_mapping", ""))
    refs.update(re.findall(r"(\S+):", mapping) if ":" in mapping else mapping.split())
    for key in ("cell_measures", "formula_terms"):
        refs.update(re.findall(r"\S+:\s*(\S+)", str(metadata.get(key, ""))))
    return refs


def select_variable(dataset, variable):
    """Keep the requested variable, its coordinates, and CF dependencies."""
    if variable not in dataset.data_vars:
        raise ValueError(f"Source does not contain data variable {variable!r}")
    selected = {variable}
    pending = list(dataset[[variable]].variables)
    while pending:
        name = pending.pop()
        dependencies = _references(dataset[name])
        for dependency in dependencies:
            if dependency not in dataset.variables:
                raise ValueError(f"{name!r} references missing variable {dependency!r}")
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return dataset[sorted(selected)]


def assert_values_equal(left, right, name):
    """Compare decoded values in bounded Dask blocks, without coordinate alignment."""
    if left.dims != right.dims or left.shape != right.shape:
        raise ValueError(f"Dimensions or shape differ for {name!r}")
    a = xr.Variable(left.dims, left.data)
    b = xr.Variable(right.dims, right.data)
    if not bool(((a == b) | (a.isnull() & b.isnull())).all().compute().item()):
        raise ValueError(f"Values differ for {name!r}")


def validate_shared(incoming, existing):
    """Reject implicit alignment or replacement of any existing shared array."""
    for name, size in incoming.sizes.items():
        if name in existing.sizes and size != existing.sizes[name]:
            raise ValueError(f"Dimension size differs for {name!r}")
    required_coords = {
        name
        for name in existing.coords
        if set(existing[name].dims).issubset(incoming.dims)
        and (existing[name].dims or "grid_mapping_name" in existing[name].attrs)
    }
    missing = required_coords - set(incoming.coords)
    if missing:
        raise ValueError(
            f"Incoming variable lacks shared coordinates: {sorted(missing)}"
        )
    for name in incoming.variables.keys() & existing.variables.keys():
        left, right = incoming[name], existing[name]
        assert_values_equal(left, right, name)
        if not xr.DataArray(0, attrs=left.attrs).identical(
            xr.DataArray(0, attrs=right.attrs)
        ):
            raise ValueError(f"Attributes differ for shared array {name!r}")
        if _references(left) != _references(right):
            raise ValueError(f"CF references differ for shared array {name!r}")
        if left.encoding.get("calendar") != right.encoding.get("calendar"):
            raise ValueError(f"Calendar differs for shared array {name!r}")
