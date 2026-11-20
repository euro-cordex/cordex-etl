from pystac_client import Client
from itertools import product
from collections import defaultdict
from datetime import datetime
import os
import re
import warnings
import requests
import logging
import tempfile
from urllib.parse import urlsplit

_FREQUENCY = "cordex-cmip6:frequency"
_VARIABLE = "cordex-cmip6:variable_id"


def _item_key(item, attrs):
    return tuple(item.properties.get(attr) for attr in attrs)


def _index_fx(items, attrs):
    indexed = defaultdict(dict)
    for item in items:
        indexed[_item_key(item, attrs)][item.id] = item
    return indexed


def _matching_fx(indexed, key):
    items = list(indexed.get(key, {}).values())
    variables = {}
    for item in items:
        variable = item.properties[_VARIABLE]
        if variable in variables:
            raise ValueError(
                f"Ambiguous fx matches for {variable!r} in group {key!r}: "
                f"{variables[variable]!r}, {item.id!r}"
            )
        variables[variable] = item.id
    return items


def _stac_identity(item):
    parts = item.id.split(".")
    if (
        len(parts) != 12
        or parts[0] != "CORDEX-CMIP6"
        or not all(parts)
        or re.fullmatch(r"v[0-9]{8}", parts[-1]) is None
    ):
        raise ValueError(f"Invalid CORDEX-CMIP6 STAC item ID: {item.id!r}")
    try:
        datetime.strptime(parts[-1][1:], "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"Invalid publication version in {item.id!r}") from exc
    for attr, value in ((_FREQUENCY, parts[-3]), (_VARIABLE, parts[-2])):
        if item.properties.get(attr) != value:
            raise ValueError(f"STAC ID and {attr} disagree for {item.id!r}")
    return ".".join(parts[:-2]), parts[-2], parts[-1]


def _latest_stac_items(items):
    candidates = defaultdict(dict)
    for item in items:
        key, variable, version = _stac_identity(item)
        versions = candidates[(key, variable)]
        if version in versions and versions[version].id != item.id:
            raise ValueError(f"Ambiguous publication {version!r} for {key}.{variable}")
        versions[version] = item
    return [versions[max(versions)] for versions in candidates.values()]


def _group_stac_items(items, merge_fx):
    grouped = defaultdict(list)
    fixed = defaultdict(list)
    for item in _latest_stac_items(items):
        key, _, _ = _stac_identity(item)
        if merge_fx and item.properties[_FREQUENCY] == "fx":
            fixed[key.rsplit(".", 1)[0]].append(item)
        else:
            grouped[key].append(item)
    if merge_fx:
        for key, group in grouped.items():
            group.extend(fixed.get(key.rsplit(".", 1)[0], []))
    return grouped


def group_items_by_attributes(items, grouping_attrs=None, merge_fx=False):
    """
    Group CORDEX-CMIP6 STAC items by ID or explicitly specified attributes.

    Parameters
    ----------
    items : iterable
        Iterable of STAC items to group.
    grouping_attrs : list of str, optional
        With None, use string keys derived from STAC IDs by removing the
        variable and publication version, selecting the newest vYYYYMMDD
        publication independently per variable. Invalid IDs raise ValueError.
        An explicit list instead returns attribute tuple keys and preserves
        all temporal publications.
    merge_fx : bool, optional
        Append matching frequency="fx" items to each temporal group. Matching
        uses the STAC identity except frequency by default, or grouping_attrs
        except frequency for custom grouping. No standalone fx groups are
        returned in this mode. This only groups items; it does not merge data.
        Default grouping selects the newest fx publication. Custom grouping
        raises ValueError for distinct matching fx items of the same variable.

    Returns
    -------
    dict
        Dictionary mapping STAC-derived strings (default) or attribute tuples
        (custom grouping) to lists of items.
    """

    if grouping_attrs is None:
        return _group_stac_items(items, merge_fx)
    grouping_attrs = list(grouping_attrs)
    grouped = defaultdict(list)
    fx_items = []
    for item in items:
        if merge_fx and item.properties.get(_FREQUENCY) == "fx":
            fx_items.append(item)
            continue
        key = _item_key(item, grouping_attrs)
        grouped[key].append(item)
    if merge_fx:
        matching_attrs = [attr for attr in grouping_attrs if attr != _FREQUENCY]
        indexed = _index_fx(fx_items, matching_attrs)
        for key, group in grouped.items():
            matching_key = tuple(
                value for attr, value in zip(grouping_attrs, key) if attr != _FREQUENCY
            )
            group.extend(_matching_fx(indexed, matching_key))
    return grouped


def get_items(url=None, collection=None, add_fx=None, **kwargs):
    """
    Query STAC catalog for items matching specified criteria.

    Supports scalar and list values for flexible querying. List values result in
    multiple API calls with results combined and deduplicated.

    Parameters
    ----------
    url : str, optional
        STAC catalog endpoint URL. Defaults to ESGF discovery endpoint.
    collection : str, optional
        STAC collection to search. Defaults to "CORDEX-CMIP6".
    add_fx : list of str or bool, optional
        Also discover the named fixed fields, or all fixed fields when True.
        None, False, or an empty list disables enrichment. The fx search
        replaces variable_id and frequency, preserving all other filters.
        Missing requested fields produce a warning per simulation; True warns
        only if no fx fields match. Checks use the STAC identity excluding
        frequency and select the latest fx publication for each variable.
        Use group_items_by_attributes(..., merge_fx=True) to attach the fields.
        Enabling this option consumes both searches before returning an iterator.
    **kwargs
        CORDEX-CMIP6 query parameters (e.g., variable_id, domain_id, frequency).
        Values can be strings (single) or lists (multiple, creates separate queries).

    Returns
    -------
    iterator
        Iterator of STAC items matching the query criteria.

    Examples
    --------
    Single variable query:

    >>> items = list(get_items(variable_id="tas", domain_id="EUR-12", frequency="mon"))

    Multiple variables (list):

    >>> items = list(get_items(variable_id=["tas", "pr", "sfcWind"], frequency="mon"))

    Mix of single and multiple values:

    >>> items = list(get_items(variable_id=["tas", "pr"], domain_id=["EUR-12", "NAM-25"]))
    """
    if add_fx is not None and not isinstance(add_fx, (bool, list)):
        raise TypeError("add_fx must be None, a bool, or a list of variable names")
    if isinstance(add_fx, list) and any(
        not isinstance(variable, str) or not variable for variable in add_fx
    ):
        raise ValueError("add_fx must contain non-empty variable names")
    if url is None:
        url = "https://discovery.east.esgf.io/"
    if collection is None:
        collection = "CORDEX-CMIP6"

    catalog = Client.open(url)
    if not add_fx:
        return _search_items(catalog, collection, kwargs)

    temporal_items = list(_search_items(catalog, collection, kwargs))
    fx_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key not in ("variable_id", "frequency")
    }
    fx_kwargs["frequency"] = "fx"
    if add_fx is not True:
        fx_kwargs["variable_id"] = add_fx
    fx_items = list(_search_items(catalog, collection, fx_kwargs))
    indexed = defaultdict(list)
    for item in _latest_stac_items(fx_items):
        key, _, _ = _stac_identity(item)
        indexed[key.rsplit(".", 1)[0]].append(item)
    identities = dict.fromkeys(
        _stac_identity(item)[0].rsplit(".", 1)[0]
        for item in temporal_items
        if item.properties.get(_FREQUENCY) != "fx"
    )
    for key in identities:
        matches = indexed.get(key, [])
        available = {item.properties[_VARIABLE] for item in matches}
        missing = sorted(set(add_fx) - available) if add_fx is not True else []
        if missing or (add_fx is True and not matches):
            detail = ", ".join(missing) if missing else "all fx fields"
            warnings.warn(
                f"Missing fx datasets for simulation {key!r}: {detail}",
                UserWarning,
                stacklevel=2,
            )
    return iter({item.id: item for item in temporal_items + fx_items}.values())


def _search_items(catalog, collection, kwargs):
    all_items = {}  # Deduplicate by item id

    # Separate list and scalar kwargs
    list_kwargs = {k: v for k, v in kwargs.items() if isinstance(v, list)}
    scalar_kwargs = {k: v for k, v in kwargs.items() if not isinstance(v, list)}

    # If no list kwargs, single query
    if not list_kwargs:
        query = {
            f"{collection.lower()}:{key}": {"eq": value}
            for key, value in scalar_kwargs.items()
        }
        search = catalog.search(collections=[collection], query=query)
        return search.get_items()

    # For list kwargs: generate all combinations and query each
    keys = list(list_kwargs.keys())

    # Generate all combinations
    combinations = product(*[list_kwargs[k] for k in keys])

    for combo in combinations:
        query = {f"{collection.lower()}:{k}": {"eq": v} for k, v in zip(keys, combo)}

        # Add scalar kwargs
        for key, value in scalar_kwargs.items():
            query[f"{collection.lower()}:{key}"] = {"eq": value}

        # Execute query
        search = catalog.search(collections=[collection], query=query)
        for item in search.get_items():
            all_items[item.id] = item

    return iter(all_items.values())


def download_item_assets(
    item, output_dir=".", skip_existing=True, asset_keys=None, raise_on_error=False
):
    """
    Download all assets of a STAC item to a directory.

    Parameters
    ----------
    item : pystac.Item
        STAC item to download assets from.
    output_dir : str, optional
        Directory to save downloaded files. Created if it doesn't exist.
        Defaults to current directory.
    skip_existing : bool, optional
        If True, skip files that already exist. Defaults to True.
    asset_keys : iterable of str, optional
        Download only these assets. By default, download all assets.
    raise_on_error : bool, optional
        Raise download errors instead of returning error strings. Downloads
        are saved atomically so failed requests do not leave completed files.

    Returns
    -------
    dict
        Dictionary with download results. Keys are asset names, values are
        either filepath (success) or error message (failure).

    Examples
    --------
    Download all assets from an item:

    >>> items = list(get_items(variable_id="tas", domain_id="EUR-12", frequency="mon"))
    >>> results = download_item_assets(items[0], output_dir="climate_data")
    >>> for asset_name, result in results.items():
    ...     if isinstance(result, str) and result.startswith("climate_data"):
    ...         print(f"Downloaded: {asset_name}")
    """
    os.makedirs(output_dir, exist_ok=True)
    results = {}

    keys = list(item.assets) if asset_keys is None else list(asset_keys)
    filenames = [os.path.basename(urlsplit(item.assets[key].href).path) for key in keys]
    if any(name in ("", ".", "..") for name in filenames):
        raise ValueError(f"Invalid asset filename in item {item.id!r}")
    if len(set(filenames)) != len(filenames):
        raise ValueError(f"Duplicate asset filenames in item {item.id!r}")

    for asset_name, filename in zip(keys, filenames):
        asset = item.assets[asset_name]
        url = asset.href
        filepath = os.path.join(output_dir, filename)

        # Check if file already exists
        if skip_existing and os.path.exists(filepath):
            results[asset_name] = filepath
            continue

        temporary_path = None
        try:
            with requests.get(url, stream=True, timeout=30) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(
                    dir=output_dir, delete=False, suffix=".part"
                ) as f:
                    temporary_path = f.name
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            os.replace(temporary_path, filepath)
            results[asset_name] = filepath
        except (requests.RequestException, OSError) as exc:
            logging.getLogger(__name__).exception(
                "Failed to download asset %s of %s", asset_name, item.id
            )
            if raise_on_error:
                raise
            results[asset_name] = f"Error: {exc}"
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    return results
