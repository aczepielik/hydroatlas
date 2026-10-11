"""Tests for registry.build_registry metadata plumbing (WP-META)."""

from hydroatlas import registry

WITH_META = "152210010"
NO_META = "149180140"


def _fake_curated() -> dict:
    return {
        "basin": "Wisła",
        "stations": [
            {"id": WITH_META, "name": "Warszawa", "river": "Wisła", "river_key": "wisla"},
            {"id": NO_META, "name": "Wisła", "river": "Wisła", "river_key": "wisla"},
        ],
        "metadata": {
            WITH_META: {"pz_m_nap": -1.23, "uwagi": ["test note"]},
            NO_META: {},
        },
    }


def test_build_registry_surfaces_pz_m_nap_and_uwagi(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "load_curated", _fake_curated)
    monkeypatch.setattr(registry, "availability", lambda sid: None)
    monkeypatch.setattr(registry, "load_station_list", lambda path=None: {})
    monkeypatch.setattr(registry, "clean_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "refs_dir", lambda: tmp_path)

    stations = registry.build_registry(snapshot={})
    by_id = {s["id"]: s for s in stations}

    with_meta = by_id[WITH_META]
    assert with_meta["pz_m_nap"] == -1.23
    assert with_meta["uwagi"] == ["test note"]

    without = by_id[NO_META]
    assert without["pz_m_nap"] is None
    assert without["uwagi"] is None

    # breaks changes computation, not display — must not be written to stations.json
    for station in stations:
        assert "breaks" not in station
