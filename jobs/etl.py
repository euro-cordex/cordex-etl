import argparse
import logging

from cordex_etl.extract import get_items, group_items_by_attributes


def chunk_spec(value):
    try:
        dimension, text = value.split("=", 1)
        size = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use DIM=POSITIVE_INTEGER") from exc
    if not dimension or size <= 0:
        raise argparse.ArgumentTypeError("Use DIM=POSITIVE_INTEGER")
    return dimension, size


def main():
    parser = argparse.ArgumentParser(
        description="Discover or ingest CORDEX-CMIP6 groups."
    )
    parser.add_argument("--variable-id", required=True, nargs="+")
    parser.add_argument("--frequency", required=True, nargs="+")
    parser.add_argument("--domain-id", required=True)
    for name in (
        "driving-experiment-id",
        "driving-source-id",
        "driving-variant-label",
        "source-id",
        "institution-id",
        "version-realization",
    ):
        parser.add_argument(f"--{name}", nargs="+")
    parser.add_argument("--url", help="STAC catalog endpoint")
    parser.add_argument("--collection", help="STAC collection")
    parser.add_argument("--add-fx", nargs="+", help="Fixed field variable names")
    parser.add_argument(
        "--ingest", action="store_true", help="Enable local Zarr ingestion"
    )
    parser.add_argument(
        "--output-dir", help="Local output directory; required with --ingest"
    )
    parser.add_argument("--scratch-dir", help="Temporary source download directory")
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help="Keep the downloaded files of a failed item for debugging",
    )
    parser.add_argument(
        "--chunk", action="append", type=chunk_spec, default=[], metavar="DIM=SIZE"
    )
    args = parser.parse_args()
    if args.ingest and not args.output_dir:
        parser.error("--output-dir is required with --ingest")
    if args.ingest and any(value not in ("mon", "day") for value in args.frequency):
        parser.error("--ingest currently supports only mon/day frequencies")
    if not args.ingest and (
        args.output_dir or args.scratch_dir or args.chunk or args.keep_scratch
    ):
        parser.error("--output-dir, --scratch-dir, --keep-scratch, and --chunk require --ingest")
    logging.basicConfig(level=logging.INFO)
    filters = {
        name: getattr(args, name)
        for name in (
            "domain_id",
            "variable_id",
            "frequency",
            "driving_experiment_id",
            "driving_source_id",
            "driving_variant_label",
            "source_id",
            "institution_id",
            "version_realization",
        )
        if getattr(args, name) is not None
    }
    items = get_items(
        url=args.url, collection=args.collection, add_fx=args.add_fx, **filters
    )
    groups = group_items_by_attributes(items, merge_fx=bool(args.add_fx))
    if args.ingest:
        from cordex_etl.workflow import ingest_groups

        stores = ingest_groups(
            groups,
            args.output_dir,
            scratch_dir=args.scratch_dir,
            chunks=dict(args.chunk) or None,
            keep_scratch=args.keep_scratch,
        )
        for dataset_id, store in stores.items():
            print(f"{dataset_id}\t{store}")
    else:
        for dataset_id in groups:
            print(dataset_id)


if __name__ == "__main__":
    main()
