from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cordex_etl.extract import get_items, group_items_by_attributes


PREFIX = "cordex-cmip6:"


def item(identifier, variable="tas", frequency="mon", publication="v20260914",
         product="DD", **overrides):
    properties = {
        "domain_id": "EUR-12",
        "institution_id": "institution",
        "driving_source_id": "driver",
        "driving_experiment_id": "historical",
        "driving_variant_label": "r1i1p1f1",
        "source_id": "model",
        "version_realization": "v1",
        "variable_id": variable,
        "frequency": frequency,
    }
    properties.update(overrides)
    identity = ".".join(properties[name] for name in [
        "domain_id", "institution_id", "driving_source_id",
        "driving_experiment_id", "driving_variant_label", "source_id",
        "version_realization", "frequency", "variable_id",
    ])
    return SimpleNamespace(
        id=f"CORDEX-CMIP6.{product}.{identity}.{publication}",
        properties={PREFIX + key: value for key, value in properties.items()},
    )


def conditions(cql):
    if cql is None:
        return {}
    nodes = cql["args"] if cql["op"] == "and" else [cql]
    assert all(node["op"] == "=" for node in nodes)
    return {node["args"][0]["property"]: node["args"][1] for node in nodes}


def catalog(monkeypatch, items):
    client = Mock()

    def search(*, collections, filter=None, filter_lang=None):
        query = conditions(filter)
        matching = [
            entry for entry in items
            if all(entry.properties.get(key) == value
                   for key, value in query.items())
        ]
        return SimpleNamespace(get_items=lambda: iter(matching))

    client.search.side_effect = search
    opener = Mock(return_value=client)
    monkeypatch.setattr("cordex_etl.extract.Client.open", opener)
    return client, opener


def test_default_grouping_keeps_fx_separate():
    entries = [item("mon"), item("fx", "orog", "fx")]
    groups = group_items_by_attributes(iter(entries))
    assert list(groups.values()) == [[entries[0]], [entries[1]]]


def test_merge_fx_enriches_each_frequency_without_changing_keys():
    mon, day = item("mon"), item("day", frequency="day")
    fx = item("orog", "orog", "fx")
    unrelated = item("other", "orog", "fx", source_id="other")
    groups = group_items_by_attributes(iter([fx, mon, day, unrelated, fx]), merge_fx=True)
    assert list(groups) == list(group_items_by_attributes([mon, day]))
    assert list(groups.values()) == [[mon, fx], [day, fx]]


@pytest.mark.parametrize("attribute", [
    "domain_id", "institution_id", "driving_source_id",
    "driving_experiment_id", "driving_variant_label", "source_id",
    "version_realization",
])
def test_default_matching_is_strict(attribute):
    temporal = item("mon")
    fx = item("fx", "orog", "fx", **{attribute: "different"})
    assert list(group_items_by_attributes([temporal, fx], merge_fx=True).values()) == [
        [temporal]
    ]


def test_custom_grouping_controls_matching():
    temporal = item("mon")
    fx = item("fx", "orog", "fx", driving_experiment_id="evaluation")
    attrs = [PREFIX + "domain_id", PREFIX + "frequency"]
    groups = group_items_by_attributes([temporal, fx], attrs, merge_fx=True)
    assert groups[("EUR-12", "mon")] == [temporal, fx]


def test_custom_grouping_without_frequency():
    mon, day = item("mon"), item("day", frequency="day")
    fx = item("fx", "orog", "fx")
    groups = group_items_by_attributes(
        [mon, fx, day], [PREFIX + "domain_id"], merge_fx=True,
    )
    assert groups == {("EUR-12",): [mon, day, fx]}


@pytest.mark.parametrize("entries", [[], [item("fx", "orog", "fx")]])
def test_merge_fx_does_not_create_temporal_groups(entries):
    assert not group_items_by_attributes(entries, merge_fx=True)


def test_ambiguous_fx_raises():
    entries = [item("mon"), item("fx1", "orog", "fx"), item("fx2", "orog", "fx")]
    entries[-1].id += ".replica"
    with pytest.raises(ValueError, match="Ambiguous fx matches.*orog"):
        group_items_by_attributes(
            entries, [PREFIX + "domain_id", PREFIX + "frequency"], merge_fx=True,
        )


def test_discovery_fetches_selected_fx_and_preserves_filters(monkeypatch):
    mon, day = item("mon"), item("day", frequency="day")
    fx = item("fx", "orog", "fx")
    client, opener = catalog(monkeypatch, [mon, day, fx])
    entries = list(get_items(
        url="https://catalog.example", domain_id="EUR-12",
        driving_experiment_id="historical", variable_id=["tas"],
        frequency=["mon", "day"], add_fx=["orog"],
    ))
    assert entries == [mon, day, fx]
    opener.assert_called_once_with("https://catalog.example")
    queries = [conditions(call.kwargs["filter"]) for call in client.search.call_args_list]
    assert len(queries) == 3
    assert queries[-1] == {
        PREFIX + "domain_id": "EUR-12",
        PREFIX + "driving_experiment_id": "historical",
        PREFIX + "frequency": "fx",
        PREFIX + "variable_id": "orog",
    }
    assert list(group_items_by_attributes(entries, merge_fx=True).values()) == [
        [mon, fx], [day, fx]
    ]


@pytest.mark.filterwarnings("error")
def test_add_fx_true_discovers_all_variables(monkeypatch):
    mon, orog, sftlf = item("mon"), item("orog", "orog", "fx"), item("sftlf", "sftlf", "fx")
    client, _ = catalog(monkeypatch, [mon, orog, sftlf])
    assert list(get_items(variable_id="tas", frequency="mon", add_fx=True)) == [
        mon, orog, sftlf
    ]
    assert PREFIX + "variable_id" not in conditions(
        client.search.call_args.kwargs["filter"]
    )


def test_missing_fields_warn_once_per_simulation(monkeypatch):
    mon, day = item("mon"), item("day", frequency="day")
    fx = item("fx", "orog", "fx")
    catalog(monkeypatch, [mon, day, fx])
    with pytest.warns(UserWarning, match="Missing fx datasets.*sftlf") as caught:
        entries = list(get_items(
            variable_id="tas", frequency=["mon", "day"], add_fx=["orog", "sftlf"],
        ))
    assert len(caught) == 1
    assert entries == [mon, day, fx]


def test_add_fx_true_warns_when_no_exact_match(monkeypatch):
    mon = item("mon")
    unrelated = item("other", "orog", "fx", source_id="other")
    catalog(monkeypatch, [mon, unrelated])
    with pytest.warns(UserWarning, match="all fx fields"):
        entries = list(get_items(variable_id="tas", frequency="mon", add_fx=True))
    assert list(group_items_by_attributes(entries, merge_fx=True).values()) == [[mon]]


def test_discovery_allows_multiple_fx_publications(monkeypatch):
    mon = item("mon")
    old = item("old", "orog", "fx", publication="v20250101")
    new = item("new", "orog", "fx")
    catalog(monkeypatch, [mon, old, new])
    entries = get_items(variable_id="tas", frequency="mon", add_fx=["orog"])
    assert list(group_items_by_attributes(entries, merge_fx=True).values()) == [
        [mon, new]
    ]


def test_discovery_deduplicates_overlapping_searches(monkeypatch):
    mon, fx = item("mon"), item("fx", "orog", "fx")
    catalog(monkeypatch, [mon, fx])
    assert list(get_items(add_fx=["orog", "orog"])) == [mon, fx]


@pytest.mark.filterwarnings("error")
def test_empty_temporal_discovery_does_not_invent_groups(monkeypatch):
    catalog(monkeypatch, [item("fx", "orog", "fx")])
    entries = get_items(variable_id="tas", frequency="mon", add_fx=["orog"])
    assert not group_items_by_attributes(entries, merge_fx=True)


def test_discovery_errors_propagate(monkeypatch):
    client, _ = catalog(monkeypatch, [item("mon")])
    client.search.side_effect = RuntimeError("Catalog unavailable")
    with pytest.raises(RuntimeError, match="Catalog unavailable"):
        get_items(variable_id="tas", frequency="mon", add_fx=True)


@pytest.mark.parametrize("add_fx", [None, False, []])
def test_disabled_enrichment_preserves_single_query(monkeypatch, add_fx):
    mon = item("mon")
    client, _ = catalog(monkeypatch, [mon])
    assert list(get_items(variable_id="tas", frequency="mon", add_fx=add_fx)) == [mon]
    client.search.assert_called_once()


@pytest.mark.parametrize("add_fx", ["orog", 1, [None], [""]])
def test_invalid_add_fx_is_explicit(monkeypatch, add_fx):
    _, opener = catalog(monkeypatch, [])
    with pytest.raises((TypeError, ValueError), match="add_fx"):
        get_items(add_fx=add_fx)
    opener.assert_not_called()


def test_default_key_is_stable_stac_prefix():
    tas = item("tas")
    pr = item("pr", "pr", publication="v20250101")
    expected = (
        "CORDEX-CMIP6.DD.EUR-12.institution.driver.historical."
        "r1i1p1f1.model.v1.mon"
    )
    assert group_items_by_attributes([tas, pr]) == {expected: [tas, pr]}


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_publication_selected_per_variable(reverse):
    old = item("old", publication="v20250101")
    new = item("new")
    pr = item("pr", "pr", publication="v20240101")
    old_fx = item("old_fx", "orog", "fx", publication="v20250101")
    new_fx = item("new_fx", "orog", "fx")
    entries = [old, new, pr, old_fx, new_fx, new]
    if reverse:
        entries.reverse()
    groups = group_items_by_attributes(entries, merge_fx=True)
    assert len(groups) == 1
    result = next(iter(groups.values()))
    assert {entry.id for entry in result} == {new.id, pr.id, new_fx.id}
    assert len(result) == 3


def test_stac_prefix_separates_groups_and_fx():
    mon = item("mon")
    other = item("other", product="OTHER")
    fx = item("fx", "orog", "fx")
    groups = group_items_by_attributes([mon, other, fx], merge_fx=True)
    assert list(groups.values()) == [[mon, fx], [other]]


def test_custom_grouping_preserves_temporal_versions_and_non_stac_ids():
    old = item("old", publication="v20250101")
    new = item("new")
    new.id = "arbitrary-custom-id"
    groups = group_items_by_attributes([old, new], [PREFIX + "domain_id"])
    assert groups == {("EUR-12",): [old, new]}


@pytest.mark.parametrize("identifier", [
    "not-a-stac-id",
    "OTHER.DD.EUR-12.inst.driver.hist.member.model.v1.mon.tas.v20260101",
    "CORDEX-CMIP6.DD.EUR-12.inst.driver.hist.member.model.v1.mon.tas.latest",
    "CORDEX-CMIP6.DD.EUR-12.inst.driver.hist.member.model.v1.mon.tas.v20260230",
    "CORDEX-CMIP6..EUR-12.inst.driver.hist.member.model.v1.mon.tas.v20260101",
])
def test_invalid_stac_ids_raise(identifier):
    entry = item("bad")
    entry.id = identifier
    with pytest.raises(ValueError, match="Invalid"):
        group_items_by_attributes([entry])


@pytest.mark.parametrize("attribute", ["frequency", "variable_id"])
def test_stac_metadata_disagreement_raises(attribute):
    entry = item("bad")
    entry.properties[PREFIX + attribute] = "different"
    with pytest.raises(ValueError, match="disagree"):
        group_items_by_attributes([entry])


def test_search_uses_cql2_filter(monkeypatch):
    client, _ = catalog(monkeypatch, [])
    list(get_items(domain_id="EUR-12", frequency="mon"))
    kwargs = client.search.call_args.kwargs
    assert "query" not in kwargs
    assert kwargs["filter_lang"] == "cql2-json"
    assert kwargs["filter"]["op"] == "and"
