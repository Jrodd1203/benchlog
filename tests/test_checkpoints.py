"""Tests for the checkpoint store."""
import pytest
from benchlog.core.checkpoints import CheckpointStore


def test_empty_by_default(tmp_path):
    store = CheckpointStore(tmp_path)
    assert store.load() == []
    assert store.by_sha() == {}


def test_set_and_retrieve(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.set("abc123", "LED working", "reads 3.3 V")
    assert cp.sha == "abc123"
    assert cp.label == "LED working"
    assert cp.note == "reads 3.3 V"
    assert store.by_sha()["abc123"].label == "LED working"


def test_set_updates_existing(tmp_path):
    store = CheckpointStore(tmp_path)
    store.set("abc123", "old label")
    store.set("abc123", "new label", "new note")
    by_sha = store.by_sha()
    assert len(by_sha) == 1
    assert by_sha["abc123"].label == "new label"
    assert by_sha["abc123"].note == "new note"


def test_multiple_checkpoints_stored(tmp_path):
    store = CheckpointStore(tmp_path)
    store.set("sha1", "Step 1")
    store.set("sha2", "Step 2")
    store.set("sha3", "Step 3")
    by_sha = store.by_sha()
    assert set(by_sha.keys()) == {"sha1", "sha2", "sha3"}


def test_remove(tmp_path):
    store = CheckpointStore(tmp_path)
    store.set("sha1", "Step 1")
    store.set("sha2", "Step 2")
    store.remove("sha1")
    by_sha = store.by_sha()
    assert "sha1" not in by_sha
    assert "sha2" in by_sha


def test_remove_nonexistent_is_noop(tmp_path):
    store = CheckpointStore(tmp_path)
    store.set("sha1", "Step 1")
    store.remove("does-not-exist")
    assert len(store.load()) == 1


def test_persists_across_instances(tmp_path):
    store1 = CheckpointStore(tmp_path)
    store1.set("abc", "Persisted step", "with note")
    store2 = CheckpointStore(tmp_path)
    assert store2.by_sha()["abc"].note == "with note"
