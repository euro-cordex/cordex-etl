from pathlib import Path
import runpy
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "jobs" / "etl.py"


def setup_cli(monkeypatch, options):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--variable-id",
            "tas",
            "pr",
            "--frequency",
            "mon",
            "--domain-id",
            "EUR-12",
            *options,
        ],
    )
    from cordex_etl import extract

    monkeypatch.setattr(extract, "get_items", lambda **kwargs: iter([]))
    selected = {"CORDEX-CMIP6.DD.example.mon": []}
    monkeypatch.setattr(extract, "group_items_by_attributes", lambda *a, **k: selected)
    return selected


def test_discovery_prints_dataset_id_not_characters(monkeypatch, capsys):
    selected = setup_cli(monkeypatch, [])
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert capsys.readouterr().out.strip() == next(iter(selected))


def test_ingestion_cli_wires_output_scratch_and_chunks(monkeypatch, capsys, tmp_path):
    selected = setup_cli(
        monkeypatch,
        [
            "--ingest",
            "--output-dir",
            str(tmp_path),
            "--scratch-dir",
            str(tmp_path / "scratch"),
            "--chunk",
            "time=12",
            "--keep-scratch",
        ],
    )
    from cordex_etl import workflow
    from unittest.mock import Mock

    ingest = Mock(return_value={"dataset": tmp_path / "dataset.zarr"})
    monkeypatch.setattr(workflow, "ingest_groups", ingest)
    runpy.run_path(str(SCRIPT), run_name="__main__")
    ingest.assert_called_once_with(
        selected,
        str(tmp_path),
        scratch_dir=str(tmp_path / "scratch"),
        chunks={"time": 12},
        keep_scratch=True,
    )
    assert "dataset.zarr" in capsys.readouterr().out


@pytest.mark.parametrize(
    "options",
    [
        ["--ingest"],
        ["--output-dir", "unused"],
        ["--ingest", "--output-dir", "unused", "--chunk", "time=0"],
    ],
)
def test_invalid_cli_options_fail(monkeypatch, options):
    setup_cli(monkeypatch, options)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exc.value.code == 2
