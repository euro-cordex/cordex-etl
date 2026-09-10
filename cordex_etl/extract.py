from pystac_client import Client
from itertools import product
from collections import defaultdict
import os
import requests


def group_items_by_attributes(items, grouping_attrs=None):
    """
    Group STAC items by specified attributes.
    
    Parameters
    ----------
    items : iterable
        Iterable of STAC items to group.
    grouping_attrs : list of str, optional
        List of attribute names to group by. Defaults to standard CORDEX-CMIP6
        attributes: domain_id, institution_id, driving_source_id, 
        driving_experiment_id, driving_variant_label, source_id, version_realization.
    
    Returns
    -------
    dict
        Dictionary mapping tuples of attribute values to lists of items.
    """
    if grouping_attrs is None:
        grouping_attrs = [
            'cordex-cmip6:domain_id',
            'cordex-cmip6:institution_id',
            'cordex-cmip6:driving_source_id',
            'cordex-cmip6:driving_experiment_id',
            'cordex-cmip6:driving_variant_label',
            'cordex-cmip6:source_id',
            'cordex-cmip6:version_realization'
        ]
    grouped = defaultdict(list)
    for item in items:
        key = tuple(item.properties.get(attr) for attr in grouping_attrs)
        grouped[key].append(item)
    return grouped


def get_items(url=None, collection=None, **kwargs):
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
    if url is None:
        url = "https://discovery.east.esgf.io/"
    if collection is None:
        collection = "CORDEX-CMIP6"
    
    catalog = Client.open(url)
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
    # Convert list kwargs to a list of (key, value) pairs for itertools.product
    list_items = [(k, v) for k, vlist in list_kwargs.items() for v in vlist]
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


def download_item_assets(item, output_dir=".", skip_existing=True):
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
    
    for asset_name, asset in item.assets.items():
        url = asset.href
        filename = os.path.basename(url.split("?")[0])  # Remove query params
        filepath = os.path.join(output_dir, filename)
        
        # Check if file already exists
        if skip_existing and os.path.exists(filepath):
            results[asset_name] = filepath
            continue
        
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            results[asset_name] = filepath
        except Exception as e:
            results[asset_name] = f"Error: {str(e)}"
    
    return results