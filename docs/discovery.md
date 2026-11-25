# Discovery and grouping

## Including fixed fields in discovered groups

```python
from cordex_etl.extract import get_items, group_items_by_attributes

items = get_items(
    domain_id="EUR-12",
    variable_id=["tas", "pr"],
    frequency=["mon", "day"],
    add_fx=["orog", "sftlf"],  # True discovers all available fixed fields.
)
groups = group_items_by_attributes(items, merge_fx=True)
```

Searches use a CQL2 `filter` (`filter_lang="cql2-json"`), because the ESGF
servers ignore the STAC `query` extension.

Both options are disabled by default. Discovery searches separately for
`frequency="fx"`, replacing the variable and frequency filters while preserving
all other filters, the endpoint, and the collection. It warns per simulation
about missing requested fields; `add_fx=True` warns only when none match.
Discovery checks use the STAC identity excluding frequency, even with custom
grouping. Discovery returns all publications; default grouping selects the latest.

## Group keys

By default, group keys are strings derived from CORDEX-CMIP6 STAC item IDs by
removing the final variable and publication-version components. For example:

```text
Item:  CORDEX-CMIP6.DD.EUR-12.HCLIMcom-SMHI.MPI-ESM1-2-HR.ssp370.r1i1p1f1.HCLIM43-ALADIN.v1-r1.mon.tas.v20260914
Group: CORDEX-CMIP6.DD.EUR-12.HCLIMcom-SMHI.MPI-ESM1-2-HR.ssp370.r1i1p1f1.HCLIM43-ALADIN.v1-r1.mon
```

Use `list(groups)` directly for dataset IDs; do not join the keys as tuples.
The newest valid `vYYYYMMDD` publication is selected independently per variable,
including fx fields. Invalid IDs, invalid publication dates, conflicting
frequency/variable metadata, or distinct candidates tied for the newest version
raise `ValueError`. Repeated item IDs are deduplicated.

With `merge_fx=True`, fixed fields are appended to each matching temporal group's
item list without changing its key. Matching uses the full STAC-derived identity
except frequency, including the project and product prefixes. No standalone fx
groups are emitted, and an empty temporal search creates no groups. This does
not open or merge the datasets; merging static fields into the data remains a
later step.

Explicit `grouping_attrs` preserves attribute-based grouping with tuple keys and
all temporal publications. In that mode, fx matching uses only the supplied
attributes except frequency, and distinct matching fx items of the same variable
still raise `ValueError`, even if their publication versions differ.

`get_items` returns a one-shot iterator; wrap it in `list(...)` to reuse it.
