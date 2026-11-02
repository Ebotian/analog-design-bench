from __future__ import annotations

from adb_solve.cache import Cache, hash_inputs


def test_roundtrip_and_namespace(tmp_path):
    cache = Cache(tmp_path, namespace="sim")
    key = hash_inputs("netlist", "deck", "120")
    assert cache.get(key) is None
    cache.put(key, {"status": "ok", "measures": {"v": 1.0}})
    assert cache.get(key) == {"status": "ok", "measures": {"v": 1.0}}
    assert cache.hits == 1 and cache.misses == 1
    assert cache.stats()["hit_rate"] == 0.5

    other = Cache(tmp_path, namespace="check")
    assert other.get(key) is None


def test_disabled_cache_never_reads_or_writes(tmp_path):
    cache = Cache(tmp_path, enabled=False)
    key = hash_inputs("x")
    cache.put(key, {"status": "ok"})
    assert cache.get(key) is None
    assert not any(tmp_path.rglob("*.json"))


def test_key_changes_with_every_part():
    base = hash_inputs("a", "b")
    assert base != hash_inputs("a", "c")
    assert base != hash_inputs("b", "a")
    # Length prefixing prevents "ab"+"c" colliding with "a"+"bc".
    assert hash_inputs("ab", "c") != hash_inputs("a", "bc")
