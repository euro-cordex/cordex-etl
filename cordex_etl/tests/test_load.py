import numpy as np
import xarray as xr
import zarr

from cordex_etl.load import write_variable


def rotated(name, path):
    ds = xr.Dataset(
        {
            name: (
                ("time", "rlat", "rlon"),
                np.zeros((2, 3, 4), "f4"),
                {"grid_mapping": "rotated_pole"},
            ),
            "lat": (("rlat", "rlon"), np.zeros((3, 4)), {"units": "degrees_north"}),
            "rotated_pole": (
                (),
                0,
                {"grid_mapping_name": "rotated_latitude_longitude"},
            ),
        },
        coords={"time": [0, 1], "rlat": np.arange(3.0), "rlon": np.arange(4.0)},
    )
    ds = ds.assign_coords(lat=ds["lat"])
    ds[name].attrs["coordinates"] = "lat"
    ds.to_netcdf(path)
    return xr.open_dataset(path, decode_coords="all")


def test_grid_mapping_survives_write_and_append(tmp_path):
    store, pending = tmp_path / "s.zarr", tmp_path / "pending.json"
    for name in ("tas", "pr"):
        write_variable(rotated(name, tmp_path / f"{name}.nc"), name, store, pending)
    group = zarr.open_group(store, mode="r")
    for name in ("tas", "pr"):
        assert group[name].attrs["grid_mapping"] == "rotated_pole"
    with xr.open_zarr(store, consolidated=False, decode_coords="all") as ds:
        assert "rotated_pole" in ds.coords
        assert ds["pr"].encoding["grid_mapping"] == "rotated_pole"
        assert "lat" in ds.coords


def test_datetime_bounds_keep_source_units_and_calendar(tmp_path):
    store, pending = tmp_path / "s.zarr", tmp_path / "pending.json"
    times = xr.date_range("2000-01-01", periods=2, freq="MS", calendar="standard")
    bounds = np.stack([times, times + np.timedelta64(30, "D")], axis=1)
    ds = xr.Dataset(
        {
            "tas": (("time", "x"), np.zeros((2, 3), "f4")),
            "time_bnds": (("time", "bnds"), bounds),
        },
        coords={"time": times, "x": np.arange(3.0)},
    )
    ds["time"].attrs["bounds"] = "time_bnds"
    for name in ("time", "time_bnds"):
        ds[name].encoding.update(
            units="days since 1950-01-01T00:00:00Z", calendar="standard"
        )
    ds.to_netcdf(tmp_path / "a.nc")
    source = xr.open_dataset(tmp_path / "a.nc", decode_coords="all")
    write_variable(source, "tas", store, pending)
    with xr.open_zarr(store, consolidated=False, decode_coords="all") as written:
        for name in ("time", "time_bnds"):
            assert written[name].encoding["units"] == source[name].encoding["units"]
            assert source[name].encoding["calendar"] == "standard"
            assert written[name].encoding["calendar"] == "standard"


def scalar_height(name, value, path):
    ds = xr.Dataset(
        {name: (("time", "x"), np.zeros((2, 3), "f4"))},
        coords={
            "time": [0, 1],
            "x": np.arange(3.0),
            "height": ((), value, {"units": "m", "axis": "Z", "positive": "up"}),
        },
    )
    ds.to_netcdf(path)
    return xr.open_dataset(path, decode_coords="all")


def test_conflicting_scalar_heights_are_renamed(tmp_path):
    store, pending = tmp_path / "s.zarr", tmp_path / "pending.json"
    for name, value in (("tas", 2.0), ("sfcWind", 10.0), ("tasmax", 2.0)):
        source = scalar_height(name, value, tmp_path / f"{name}.nc")
        write_variable(source, name, store, pending)
    with xr.open_zarr(store, consolidated=False, decode_coords="all") as ds:
        assert float(ds["height"]) == 2.0
        assert float(ds["height10m"]) == 10.0
        assert ds["sfcWind"].encoding["coordinates"] == "height10m"
        assert ds["tasmax"].encoding["coordinates"] == "height"
        assert ds["height10m"].attrs["units"] == "m"
