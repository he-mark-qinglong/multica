import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import dataclasses
import json

import pytest

from _shared.strategy_kit.signal_bus import (
    BusConfig,
    Signal,
    SignalBus,
    load_spill,
)


# ---------------------------------------------------------------------------
# publish / get basics
# ---------------------------------------------------------------------------

def test_publish_returns_stamped_immutable_signal():
    bus = SignalBus()
    sig = bus.publish("BTC", "regime", "risk_on", ts=100.0, publisher="regime_bot")
    assert isinstance(sig, Signal)
    assert sig.version == 1
    assert sig.key == ("BTC", "regime")
    with pytest.raises(dataclasses.FrozenInstanceError):
        sig.value = "risk_off"  # type: ignore[misc]


def test_get_returns_latest_valid_value():
    bus = SignalBus()
    bus.publish("BTC", "regime", "chop", ts=100.0)
    bus.publish("BTC", "regime", "trend", ts=110.0)
    assert bus.get_value("BTC", "regime", now=111.0) == "trend"
    assert bus.get("BTC", "regime", now=111.0).version == 2


def test_get_missing_key_returns_none():
    bus = SignalBus()
    assert bus.get("ETH", "regime", now=0.0) is None
    assert bus.get_value("ETH", "regime", now=0.0, default="flat") == "flat"


# ---------------------------------------------------------------------------
# TTL / expiry
# ---------------------------------------------------------------------------

def test_expired_signal_invisible_but_newer_valid_visible():
    bus = SignalBus()
    bus.publish("BTC", "toxicity", 0.9, ts=100.0, ttl=10.0)
    assert bus.get_value("BTC", "toxicity", now=110.0) == 0.9   # inclusive end
    assert bus.get_value("BTC", "toxicity", now=110.1) is None  # expired


def test_expired_newer_falls_back_to_valid_older():
    bus = SignalBus()
    bus.publish("BTC", "regime", "risk_on", ts=100.0)            # no ttl
    bus.publish("BTC", "regime", "halt", ts=120.0, ttl=5.0)      # short-lived
    assert bus.get_value("BTC", "regime", now=122.0) == "halt"
    # after 'halt' expires, subscriber sees the still-valid older signal
    assert bus.get_value("BTC", "regime", now=126.0) == "risk_on"


def test_ttl_none_never_expires():
    bus = SignalBus()
    bus.publish("MKT", "session", "asia", ts=0.0, ttl=None)
    assert bus.get_value("MKT", "session", now=1e12) == "asia"


# ---------------------------------------------------------------------------
# versioning
# ---------------------------------------------------------------------------

def test_versions_monotonic_across_keys():
    bus = SignalBus()
    a = bus.publish("BTC", "regime", 1, ts=0.0)
    b = bus.publish("ETH", "toxicity", 2, ts=0.0)
    c = bus.publish("BTC", "regime", 3, ts=1.0)
    assert (a.version, b.version, c.version) == (1, 2, 3)
    assert bus.current_version == 3


def test_get_since_watermark():
    bus = SignalBus()
    s1 = bus.publish("BTC", "regime", "a", ts=0.0)
    assert bus.get_since("BTC", "regime", now=1.0, min_version=s1.version) is None
    s2 = bus.publish("BTC", "regime", "b", ts=2.0)
    got = bus.get_since("BTC", "regime", now=3.0, min_version=s1.version)
    assert got is not None and got.version == s2.version and got.value == "b"


# ---------------------------------------------------------------------------
# multiple subscribers / keys isolation
# ---------------------------------------------------------------------------

def test_multiple_subscribers_pull_same_signal_independently():
    bus = SignalBus()
    bus.publish("BTC", "regime", "risk_on", ts=100.0, ttl=50.0)
    # two independent subscribers, different read times
    sub_a = bus.get_value("BTC", "regime", now=101.0)
    sub_b = bus.get_value("BTC", "regime", now=149.0)
    sub_c = bus.get_value("BTC", "regime", now=151.0)
    assert sub_a == sub_b == "risk_on"
    assert sub_c is None  # expired for the late reader only


def test_keys_isolated_by_symbol_and_type():
    bus = SignalBus()
    bus.publish("BTC", "regime", "up", ts=0.0)
    bus.publish("ETH", "regime", "down", ts=0.0)
    bus.publish("BTC", "toxicity", 0.1, ts=0.0)
    assert bus.get_value("BTC", "regime", now=1.0) == "up"
    assert bus.get_value("ETH", "regime", now=1.0) == "down"
    assert bus.get_value("BTC", "toxicity", now=1.0) == 0.1
    assert set(bus.keys()) == {("BTC", "regime"), ("ETH", "regime"),
                               ("BTC", "toxicity")}


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def test_history_newest_n_and_window_cap():
    bus = SignalBus(BusConfig(history_size=3))
    for i in range(5):
        bus.publish("BTC", "px", i, ts=float(i))
    hist = bus.history("BTC", "px", n=10)
    assert [s.value for s in hist] == [4, 3, 2]  # capped at history_size
    assert [s.value for s in bus.history("BTC", "px", n=2)] == [4, 3]


def test_history_with_now_filters_expired():
    bus = SignalBus()
    bus.publish("BTC", "r", "old", ts=0.0, ttl=5.0)
    bus.publish("BTC", "r", "new", ts=10.0, ttl=5.0)
    assert [s.value for s in bus.history("BTC", "r", n=5)] == ["new", "old"]
    assert [s.value for s in bus.history("BTC", "r", n=5, now=12.0)] == ["new"]


# ---------------------------------------------------------------------------
# jsonl spill (cross-process)
# ---------------------------------------------------------------------------

def test_spill_roundtrip_rebuilds_state(tmp_path):
    path = str(tmp_path / "bus.jsonl")
    bus = SignalBus(BusConfig(spill_path=path))
    bus.publish("BTC", "regime", "risk_on", ts=100.0, publisher="a")
    bus.publish("BTC", "regime", "halt", ts=120.0, ttl=5.0, publisher="b")

    lines = (tmp_path / "bus.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["value"] == "risk_on"

    # full replay (audit): versions and values preserved
    clone = load_spill(path)
    assert clone.current_version == 2
    assert clone.get_value("BTC", "regime", now=121.0) == "halt"
    assert clone.get_value("BTC", "regime", now=126.0) == "risk_on"

    # state snapshot at now: expired signal dropped at load time
    snap = load_spill(path, now=126.0)
    assert snap.get_value("BTC", "regime", now=126.0) == "risk_on"
    assert [s.value for s in snap.history("BTC", "regime", n=5)] == ["risk_on"]


def test_load_spill_missing_file_gives_empty_bus(tmp_path):
    bus = load_spill(str(tmp_path / "nope.jsonl"))
    assert bus.current_version == 0
    assert bus.keys() == []


# ---------------------------------------------------------------------------
# config validation
# ---------------------------------------------------------------------------

def test_config_rejects_bad_history_size():
    with pytest.raises(ValueError):
        BusConfig(history_size=0)
