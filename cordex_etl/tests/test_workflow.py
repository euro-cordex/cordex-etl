from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from functools import partial
from pathlib import Path
import threading

import numpy as np
import pytest
import pystac
import requests
import xarray as xr

from cordex_etl.extract import download_item_assets, group_items_by_attributes
from cordex_etl.workflow import ingest_groups


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    requests_seen = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(self.path)
            super().do_GET()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(directory))
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield directory, f"http://127.0.0.1:{server.server_port}", requests_seen
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def make_item(
    source,
    variable="tas",
    frequency="mon",
    version="v20260914",
    offset=0,
    nan=False,
    split=False,
    bounds=False,
):
    directory, url, _ = source
    coords = {"y": [0.0, 1.0], "x": [2.0, 3.0]}
    if frequency == "fx":
        dataset = xr.Dataset(
            {variable: (("y", "x"), np.ones((2, 2), dtype="float32"))}, coords=coords
        )
    else:
        coords["time"] = np.array(
            ["2001-01-16", "2001-02-16"], dtype="datetime64[ns]"
        ) + np.timedelta64(offset, "D")
        values = np.arange(8, dtype="float32").reshape(2, 2, 2)
        if nan:
            values[0, 0, 0] = np.nan
        dataset = xr.Dataset({variable: (("time", "y", "x"), values)}, coords=coords)
        if bounds:
            dataset["time_bnds"] = (
                ("time", "bnds"),
                np.stack(
                    [
                        coords["time"] - np.timedelta64(1, "D"),
                        coords["time"] + np.timedelta64(1, "D"),
                    ],
                    axis=1,
                ),
            )
            dataset.time.attrs["bounds"] = "time_bnds"
            for name in ("time", "time_bnds"):
                dataset[name].encoding["units"] = "days since 2001-01-01"
    dataset[variable].attrs["units"] = "K" if variable == "tas" else "1"
    properties = {
        "cordex-cmip6:variable_id": variable,
        "cordex-cmip6:frequency": frequency,
    }
    item = pystac.Item(
        id=f"CORDEX-CMIP6.DD.EUR-12.inst.driver.hist.r1.model.v1.{frequency}.{variable}.{version}",
        geometry=None,
        bbox=None,
        datetime=datetime(2001, 1, 1),
        properties=properties,
    )
    parts = (
        [dataset.isel(time=slice(i, i + 1)) for i in range(2)] if split else [dataset]
    )
    for index, part in enumerate(parts):
        name = f"{variable}-{version}-{index}.nc"
        part.to_netcdf(directory / name, engine="netcdf4")
        item.add_asset(
            f"data-{index}",
            pystac.Asset(
                href=f"{url}/{name}", media_type="application/x-netcdf", roles=["data"]
            ),
        )
    item.add_asset(
        "thumbnail",
        pystac.Asset(
            href=f"{url}/thumbnail.png", media_type="image/png", roles=["thumbnail"]
        ),
    )
    return item


def groups(*items):
    return group_items_by_attributes(items, merge_fx=True)


def manifest(output):
    return json.loads(next(output.glob("*.manifest.json")).read_text())


def test_sequential_bootstrap_resume_and_addition(source, tmp_path, monkeypatch):
    tas = make_item(source, nan=True, split=True)
    pr = make_item(source, "pr")
    fx = make_item(source, "orog", "fx")
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    from cordex_etl import workflow

    original = workflow.download_item_assets
    finished_downloads = []

    def tracked(item, **kwargs):
        if finished_downloads:
            assert not finished_downloads[-1].exists()
            with xr.open_zarr(next(output.glob("*.zarr")), consolidated=False) as ds:
                assert "tas" in ds
        result = original(item, **kwargs)
        finished_downloads.append(Path(kwargs["output_dir"]))
        return result

    monkeypatch.setattr(workflow, "download_item_assets", tracked)
    result = ingest_groups(groups(fx, tas, pr), output, scratch, chunks={"time": 1})
    store = next(iter(result.values()))
    with xr.open_zarr(store, consolidated=False) as ds:
        assert set(ds.data_vars) == {"tas", "pr", "orog"}
        assert ds.tas.shape == (2, 2, 2)
        assert np.isnan(ds.tas.values[0, 0, 0])
    assert len(source[2]) == 4
    assert list(scratch.iterdir()) == []
    saved = manifest(output)
    assert saved["status"] == "complete"
    assert set(saved["variables"]) == {"tas", "pr", "orog"}
    assert not list(output.glob("*.pending.json"))
    assert not list(output.glob("*.lock"))
    before = {p: p.read_bytes() for p in (store / "tas").rglob("*") if p.is_file()}
    ingest_groups(groups(tas, pr, fx), output, scratch, chunks={"time": 1})
    assert len(source[2]) == 4
    new = make_item(source, "hurs")
    ingest_groups(groups(tas, pr, new, fx), output, scratch)
    assert len(source[2]) == 5
    assert all(p.read_bytes() == value for p, value in before.items())
    with xr.open_zarr(store, consolidated=False) as ds:
        assert "hurs" in ds


def test_bounds_preserved_and_not_rewritten(source, tmp_path):
    tas = make_item(source, bounds=True)
    pr = make_item(source, "pr", bounds=True)
    result = ingest_groups(groups(tas), tmp_path / "out")
    store = next(iter(result.values()))
    before = {
        p: p.read_bytes()
        for name in ("time", "time_bnds", "x", "y")
        for p in (store / name).rglob("*")
        if p.is_file()
    }
    ingest_groups(groups(tas, pr), tmp_path / "out")
    assert all(p.read_bytes() == value for p, value in before.items())
    with xr.open_zarr(store, consolidated=False, decode_coords="all") as ds:
        assert ds.time.encoding["bounds"] == "time_bnds"


def test_coordinate_mismatch_rejected_before_write(source, tmp_path):
    tas = make_item(source)
    shifted = make_item(source, "pr", offset=1)
    output = tmp_path / "out"
    ingest_groups(groups(tas), output)
    with pytest.raises(ValueError, match="Values differ.*time"):
        ingest_groups(groups(tas, shifted), output)
    assert not list(output.glob("*.pending.json"))
    with xr.open_zarr(next(output.glob("*.zarr")), consolidated=False) as ds:
        assert "pr" not in ds


def test_keep_scratch_preserves_failed_item_only(source, tmp_path):
    item = make_item(source)
    source[0].joinpath("tas-v20260914-0.nc").unlink()
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    with pytest.raises(requests.HTTPError):
        ingest_groups(groups(item), output, scratch, keep_scratch=True)
    assert len(list(scratch.iterdir())) == 1


def test_keep_scratch_cleans_successful_items(source, tmp_path):
    scratch = tmp_path / "scratch"
    ingest_groups(
        groups(make_item(source)), tmp_path / "out", scratch, keep_scratch=True
    )
    assert list(scratch.iterdir()) == []


def test_changed_publication_rejected_without_download(source, tmp_path):
    output = tmp_path / "out"
    ingest_groups(groups(make_item(source)), output)
    before = len(source[2])
    new = make_item(source, version="v20261001")
    with pytest.raises(ValueError, match="Source changed"):
        ingest_groups(groups(new), output)
    assert len(source[2]) == before


def test_failed_download_cleans_scratch_and_can_retry(source, tmp_path):
    item = make_item(source)
    source[0].joinpath("tas-v20260914-0.nc").unlink()
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    with pytest.raises(requests.HTTPError):
        ingest_groups(groups(item), output, scratch)
    assert list(scratch.iterdir()) == []
    assert not list(output.glob("*.pending.json"))
    assert not list(output.glob("*.lock"))
    item = make_item(source)
    ingest_groups(groups(item), output, scratch)
    assert manifest(output)["status"] == "complete"


def test_partial_write_blocks_restart(source, tmp_path, monkeypatch):
    item = make_item(source)
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    original = xr.Dataset.to_zarr

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("Simulated interruption")

    with monkeypatch.context() as patch:
        patch.setattr(xr.Dataset, "to_zarr", fail)
        with pytest.raises(OSError, match="Simulated"):
            ingest_groups(groups(item), output, scratch)
    assert list(output.glob("*.pending.json"))
    assert list(scratch.iterdir()) == []
    before = len(source[2])
    with pytest.raises(RuntimeError, match="Incomplete write"):
        ingest_groups(groups(item), output, scratch)
    assert len(source[2]) == before


def test_untracked_store_and_lock_rejected(source, tmp_path):
    item = make_item(source)
    selected = groups(item)
    key = next(iter(selected))
    output = tmp_path / "out"
    output.mkdir()
    lock = output / f"{key}.lock"
    lock.touch()
    with pytest.raises(RuntimeError, match="locked"):
        ingest_groups(selected, output)
    assert lock.exists()
    lock.unlink()
    (output / f"{key}.zarr").mkdir()
    with pytest.raises(ValueError, match="Untracked"):
        ingest_groups(selected, output)


def test_missing_committed_array_rejected(source, tmp_path):
    output = tmp_path / "out"
    selected = groups(make_item(source))
    ingest_groups(selected, output)
    path = next(output.glob("*.zarr")) / "tas" / "zarr.json"
    path.unlink()
    with pytest.warns(UserWarning, match="not recognized"):
        with pytest.raises(ValueError, match="missing"):
            ingest_groups(selected, output)


def test_failed_verification_never_commits(source, tmp_path, monkeypatch):
    from cordex_etl import load

    item = make_item(source)
    output = tmp_path / "out"

    def fail(*args):
        raise ValueError("Verification failed")

    monkeypatch.setattr(load, "assert_values_equal", fail)
    with pytest.raises(ValueError, match="Verification failed"):
        ingest_groups(groups(item), output)
    assert manifest(output)["variables"] == {}
    assert list(output.glob("*.pending.json"))


def test_duplicate_download_filenames_rejected(source, tmp_path):
    item = make_item(source)
    item.add_asset("duplicate", item.assets["data-0"].clone())
    with pytest.raises(ValueError, match="Duplicate asset filenames"):
        download_item_assets(item, tmp_path, asset_keys=["data-0", "duplicate"])


@pytest.mark.parametrize("frequency", ["3hr", "fx"])
def test_unsupported_groups_rejected(source, tmp_path, frequency):
    item = make_item(source, frequency=frequency)
    selected = group_items_by_attributes([item])
    with pytest.raises(ValueError, match="Only mon/day"):
        ingest_groups(selected, tmp_path / "out")


def test_no_data_assets_rejected(source, tmp_path):
    item = make_item(source)
    del item.assets["data-0"]
    with pytest.raises(ValueError, match="No HTTP"):
        ingest_groups(groups(item), tmp_path / "out")


def test_packed_source_preserves_decoded_values(source, tmp_path):
    item = make_item(source)
    path = source[0] / "tas-v20260914-0.nc"
    with xr.open_dataset(path) as ds:
        dataset = ds.load()
    dataset.to_netcdf(
        path,
        encoding={"tas": {"dtype": "int16", "scale_factor": 0.1, "_FillValue": -999}},
    )
    result = ingest_groups(groups(item), tmp_path / "out")
    with xr.open_zarr(next(iter(result.values())), consolidated=False) as actual:
        with xr.open_dataset(path) as expected:
            np.testing.assert_array_equal(actual.tas.values, expected.tas.values)


def test_missing_shared_coordinates_rejected(source, tmp_path):
    tas, pr = make_item(source), make_item(source, "pr")
    path = source[0] / "pr-v20260914-0.nc"
    with xr.open_dataset(path) as ds:
        dataset = ds.drop_vars(["x", "y"]).load()
    dataset.to_netcdf(path)
    output = tmp_path / "out"
    ingest_groups(groups(tas), output)
    with pytest.raises(ValueError, match="lacks shared coordinates"):
        ingest_groups(groups(tas, pr), output)
    assert not list(output.glob("*.pending.json"))


def test_asset_metadata_changes_rejected(source, tmp_path):
    item = make_item(source)
    output = tmp_path / "out"
    ingest_groups(groups(item), output)
    item.assets["data-0"].extra_fields["file:checksum"] = "changed"
    before = len(source[2])
    with pytest.raises(ValueError, match="Source changed"):
        ingest_groups(groups(item), output)
    assert len(source[2]) == before


def test_unknown_chunk_dimension_rejected(source, tmp_path):
    with pytest.raises(ValueError, match="Unknown chunk dimensions"):
        ingest_groups(groups(make_item(source)), tmp_path / "out", chunks={"typo": 12})
    assert not list((tmp_path / "out").glob("*.pending.json"))


def test_download_failure_never_leaves_completed_file(source, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.__enter__.return_value = response

    def broken_stream(**kwargs):
        yield b"partial"
        raise requests.ConnectionError("Interrupted")

    response.iter_content.side_effect = broken_stream
    monkeypatch.setattr("cordex_etl.extract.requests.get", lambda *a, **k: response)
    item = make_item(source)
    output = tmp_path / "download"
    with pytest.raises(requests.ConnectionError, match="Interrupted"):
        download_item_assets(item, output, asset_keys=["data-0"], raise_on_error=True)
    assert list(output.iterdir()) == []


def test_nonstandard_calendar_roundtrip(source, tmp_path):
    import cftime

    tas, pr = make_item(source), make_item(source, "pr")
    for variable in ("tas", "pr"):
        path = source[0] / f"{variable}-v20260914-0.nc"
        with xr.open_dataset(path) as ds:
            dataset = ds.load().assign_coords(
                time=[
                    cftime.Datetime360Day(2001, 1, 16),
                    cftime.Datetime360Day(2001, 2, 16),
                ]
            )
        dataset.to_netcdf(path)
    stores = ingest_groups(groups(tas, pr), tmp_path / "out")
    with xr.open_zarr(next(iter(stores.values())), consolidated=False) as ds:
        assert ds.time.encoding["calendar"] == "360_day"
        assert isinstance(ds.time.values[0], cftime.Datetime360Day)


@pytest.mark.parametrize("times", [[0, 0], [1, 0]])
def test_invalid_time_axis_rejected(source, tmp_path, times):
    item = make_item(source)
    path = source[0] / "tas-v20260914-0.nc"
    with xr.open_dataset(path) as ds:
        dataset = ds.load().assign_coords(time=times)
    dataset.to_netcdf(path)
    with pytest.raises(ValueError, match="not unique/increasing"):
        ingest_groups(groups(item), tmp_path / "out")
    assert not list((tmp_path / "out").glob("*.pending.json"))


def test_static_time_dimension_rejected(source, tmp_path):
    tas, fx = make_item(source), make_item(source, "orog", "fx")
    path = source[0] / "orog-v20260914-0.nc"
    with xr.open_dataset(path) as ds:
        dataset = ds.load().expand_dims(time=[0])
    dataset.to_netcdf(path)
    with pytest.raises(ValueError, match="Static field has a time"):
        ingest_groups(groups(tas, fx), tmp_path / "out")
    assert manifest(tmp_path / "out")["variables"].keys() == {"tas"}


def test_fx_already_present_as_dependency_is_validated_not_overwritten(
    source, tmp_path
):
    tas, fx = make_item(source), make_item(source, "orog", "fx")
    tas_path = source[0] / "tas-v20260914-0.nc"
    with xr.open_dataset(tas_path) as ds:
        dataset = ds.load()
    with xr.open_dataset(source[0] / "orog-v20260914-0.nc") as static:
        dataset["orog"] = static.orog.load()
    dataset.tas.attrs["ancillary_variables"] = "orog"
    dataset.to_netcdf(tas_path)
    output = tmp_path / "out"
    ingest_groups(groups(tas), output)
    store = next(output.glob("*.zarr"))
    before = {p: p.read_bytes() for p in (store / "orog").rglob("*") if p.is_file()}
    ingest_groups(groups(tas, fx), output)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert set(manifest(output)["variables"]) == {"tas", "orog"}
