"""Per-store settings: validation, loading from files, and failing closed."""
import json

import pytest

from bot.context import ChatContext
from bot.store_config import (ConfigError, DictSource, JsonDirSource, StoreConfig, StoreConfigs, StoreNotConfigured,
                              valid_store_id)

OK = {"store_name": "Acme Furniture", "allowed_domains": ["www.AcmeFurniture.com"], "vertical": "furniture"}


def test_a_valid_config_is_cleaned_up():
    cfg = StoreConfig.from_dict("acme-1", OK)
    assert cfg.store_name == "Acme Furniture" and cfg.allowed_domains == ("acmefurniture.com",)
    assert cfg.enabled is True and cfg.vertical == "furniture" and cfg.catalog_rules == ()


@pytest.mark.parametrize("change", [
    {"store_name": ""}, {"store_name": "A\nB"}, {"store_name": "x" * 81}, {"store_name": "Acme <<store_name>>"},
    {"vertical": "pizza"}, {"allowed_domains": ["not a host"]}, {"allowed_domains": ["localhost"]},
    {"allowed_domains": "acme.com"}, {"enabled": "yes"}, {"shipping_example": "a\nb"},
    {"catalog_rules": ["x" * 601]}, {"catalog_rules": ["a\nb"]}, {"catalog_rules": ["r"] * 11},
    {"catalog_rules": "one rule as text"}, {"surprise": 1},
])
def test_bad_values_are_refused(change):
    with pytest.raises(ConfigError):
        StoreConfig.from_dict("acme-1", {**OK, **change})


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "../x", "x" * 65, "a\r\nb", None, 5])
def test_store_ids_must_be_simple(bad):
    assert not valid_store_id(bad)
    with pytest.raises(ConfigError):
        StoreConfig(store_uuid=bad, store_name="Acme")


def test_json_files_are_found_by_store_id(tmp_path):
    (tmp_path / "acme-1.json").write_text(json.dumps(OK))
    src = JsonDirSource(tmp_path)
    assert src.get("acme-1").store_name == "Acme Furniture"
    assert src.get("other") is None


def test_a_store_id_cannot_reach_other_files(tmp_path):
    (tmp_path / "secret.json").write_text(json.dumps(OK))
    sub = tmp_path / "stores"
    sub.mkdir()
    assert JsonDirSource(sub).get("../secret") is None


def test_broken_files_raise_a_config_error(tmp_path):
    (tmp_path / "a-1.json").write_text("{not json")
    (tmp_path / "a-2.json").write_text("[1, 2]")
    for sid in ("a-1", "a-2"):
        with pytest.raises(ConfigError):
            JsonDirSource(tmp_path).get(sid)


def test_unknown_and_invalid_stores_are_refused_not_defaulted():
    cfgs = StoreConfigs(DictSource({}))
    for sid in ("nobody", None, "bad id"):
        with pytest.raises(StoreNotConfigured):
            cfgs.for_store(sid)


def test_a_switched_off_store_is_refused():
    off = StoreConfig(store_uuid="acme-1", store_name="Acme", enabled=False)
    with pytest.raises(StoreNotConfigured, match="switched off"):
        StoreConfigs(DictSource({"acme-1": off})).for_store("acme-1")


def test_for_ctx_reads_the_chats_store():
    a = StoreConfig(store_uuid="a-1", store_name="Store A")
    b = StoreConfig(store_uuid="b-1", store_name="Store B")
    cfgs = StoreConfigs(DictSource({"a-1": a, "b-1": b}))
    assert cfgs.for_ctx(ChatContext(store_uuid="a-1")).store_name == "Store A"
    assert cfgs.for_ctx(ChatContext(store_uuid="b-1")).store_name == "Store B"
    with pytest.raises(StoreNotConfigured):
        cfgs.for_ctx(ChatContext())


def test_changes_are_picked_up_after_the_ttl_but_not_before(tmp_path):
    path = tmp_path / "acme-1.json"
    path.write_text(json.dumps({**OK, "store_name": "Old Name"}))
    now = [0.0]
    cfgs = StoreConfigs(JsonDirSource(tmp_path), ttl=60, clock=lambda: now[0])
    assert cfgs.for_store("acme-1").store_name == "Old Name"
    path.write_text(json.dumps({**OK, "store_name": "New Name"}))
    now[0] = 59
    assert cfgs.for_store("acme-1").store_name == "Old Name"
    now[0] = 61
    assert cfgs.for_store("acme-1").store_name == "New Name"


def test_removing_a_store_switches_it_off_after_the_ttl(tmp_path):
    path = tmp_path / "acme-1.json"
    path.write_text(json.dumps(OK))
    now = [0.0]
    cfgs = StoreConfigs(JsonDirSource(tmp_path), ttl=60, clock=lambda: now[0])
    cfgs.for_store("acme-1")
    path.unlink()
    now[0] = 61
    with pytest.raises(StoreNotConfigured):
        cfgs.for_store("acme-1")
