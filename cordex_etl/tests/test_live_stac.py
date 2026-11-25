import os

import pytest
from pystac_client import Client
from pystac_client.exceptions import APIError

STAC_URL = os.environ.get("ESGF_STAC_URL", "https://discovery.west.esgf.io/")


@pytest.fixture(scope="module")
def catalog():
    try:
        return Client.open(STAC_URL, timeout=30)
    except Exception as err:
        pytest.skip(f"{STAC_URL} not reachable: {err}")


def test_access_one_item(catalog):
    try:
        collection = next(catalog.get_collections())
        item = next(
            catalog.search(collections=[collection.id], max_items=1).items(), None
        )
    except APIError as err:
        pytest.skip(f"STAC API error: {err}")

    assert item is not None, f"collection {collection.id} has no items"
    assert item.id
    assert item.assets
    print(collection.id, item.id, list(item.assets))
