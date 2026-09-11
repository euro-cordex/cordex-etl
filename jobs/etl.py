
import argparse

from cordex_etl.extract import get_items, group_items_by_attributes


def main():
	parser = argparse.ArgumentParser(description="Download CORDEX-CMIP6 assets.")
	parser.add_argument("--variable-id", required=True, help="Variable identifier, e.g. tas")
	parser.add_argument("--frequency", required=True, help="Data frequency, e.g. mon")
	parser.add_argument("--domain-id", required=True, help="CORDEX domain identifier e.g. EUR-12")
	args = parser.parse_args()

	items = get_items(
		domain_id=args.domain_id,
		variable_id=args.variable_id,
		frequency=args.frequency,
	)
	groups = group_items_by_attributes(items)
	keys = list(groups.keys())
	datasets = [".".join(key) for key in keys]
	for dataset in datasets:
		print(dataset)


if __name__ == "__main__":
	main()
