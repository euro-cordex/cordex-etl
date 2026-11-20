import numpy as np
import pytest
import xarray as xr

from cordex_etl.transform import select_variable, validate_shared


def test_select_variable_preserves_dependencies_not_other_climate_variables():
    dataset = xr.Dataset(
        {
            "tas": (("time", "y", "x"), np.ones((2, 2, 2))),
            "pr": (("time", "y", "x"), np.ones((2, 2, 2))),
            "crs": 0,
            "areacella": (("y", "x"), np.ones((2, 2))),
            "quality": (("time", "y", "x"), np.ones((2, 2, 2))),
        },
        coords={"time": [0, 1], "y": [0, 1], "x": [0, 1]},
    )
    dataset.tas.attrs.update(
        {
            "grid_mapping": "crs",
            "cell_measures": "area: areacella",
            "ancillary_variables": "quality",
        }
    )
    selected = select_variable(dataset, "tas")
    assert set(selected.variables) == {
        "tas",
        "crs",
        "areacella",
        "quality",
        "time",
        "y",
        "x",
    }


def test_missing_dependency_rejected():
    dataset = xr.Dataset({"tas": ("time", [1, 2])})
    dataset.tas.attrs["grid_mapping"] = "missing"
    with pytest.raises(ValueError, match="missing variable"):
        select_variable(dataset, "tas")


def test_unrelated_scalar_height_does_not_have_to_be_present():
    existing = xr.Dataset(
        {"tas": ("time", [1, 2])},
        coords={"time": [0, 1], "height": 2.0},
    )
    incoming = xr.Dataset({"pr": ("time", [3, 4])}, coords={"time": [0, 1]})
    validate_shared(incoming, existing)


@pytest.mark.parametrize("difference", ["size", "calendar", "attrs", "values"])
def test_shared_metadata_and_values_are_checked(difference):
    existing = xr.Dataset({"tas": ("time", [1, 2])}, coords={"time": [0, 1]})
    incoming = xr.Dataset({"pr": ("time", [3, 4])}, coords={"time": [0, 1]})
    if difference == "size":
        incoming = incoming.isel(time=[0])
    elif difference == "calendar":
        incoming.time.encoding["calendar"] = "360_day"
    elif difference == "attrs":
        incoming.time.attrs["axis"] = "T"
    else:
        incoming = incoming.assign_coords(time=[1, 2])
    with pytest.raises(ValueError):
        validate_shared(incoming, existing)
