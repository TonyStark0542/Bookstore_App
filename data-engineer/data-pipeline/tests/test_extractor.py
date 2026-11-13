"""Bronze extractor tests. mongomock stands in for MongoDB, so no live DB is needed (CI-safe).
Run from data-engineer/data-pipeline:  python -m pytest tests -v
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mongomock
import pyarrow.parquet as pq
import pytest

from extract import mongo_extractor, watermark

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh fake Mongo + temp watermark file + temp bronze dir for every test."""
    db = mongomock.MongoClient()["bookstore"]
    monkeypatch.setattr(mongo_extractor, "MongoClient", lambda *a, **k: db.client)
    monkeypatch.setattr(watermark, "WATERMARK_FILE", str(tmp_path / "watermarks.json"))
    out = tmp_path / "bronze"

    def run(coll="orders", mode="incremental"):
        return mongo_extractor.extract_collection_to_bronze(
            "mongodb://fake", "bookstore", coll, mode=mode, output_base_dir=str(out))

    def files(coll="orders"):
        return sorted(Path(out, coll).rglob("*.parquet"))

    def rows(coll="orders"):
        return [r for f in files(coll) for r in pq.read_table(f).to_pylist()]

    return db, run, files, rows


def order(i, minutes=0, **extra):
    t = T0 + timedelta(minutes=minutes)
    return {"order_no": i, "status": "Confirmed", "created_at": t, "updated_at": t, **extra}


def test_same_day_second_run_does_not_overwrite_first(env):
    db, run, files, rows = env
    db.orders.insert_many([order(i, i) for i in range(1000)])
    assert run() == 1000

    db.orders.insert_one(order(1000, 2000))  # "2 PM" new order
    assert run() == 1

    assert len(files()) == 2                       # two distinct batch files, same day partition
    assert len({f.name for f in files()}) == 2
    assert len(rows()) == 1001                     # morning's 1000 orders survived


def test_rerun_with_no_changes_pulls_nothing_and_writes_nothing(env):
    db, run, files, rows = env
    db.orders.insert_many([order(i, i) for i in range(5)])
    run()
    assert run() == 0
    assert len(files()) == 1 and len(rows()) == 5


def test_updated_existing_order_is_re_extracted_as_new_version(env):
    db, run, files, rows = env
    db.orders.insert_one(order(1, 0))
    run()

    db.orders.update_one({"order_no": 1}, {"$set": {"status": "Delivered", "updated_at": T0 + timedelta(hours=5)}})
    assert run() == 1

    versions = sorted(rows(), key=lambda r: r["updated_at"])
    assert [r["status"] for r in versions] == ["Confirmed", "Delivered"]   # history kept for Silver to dedupe
    assert versions[0]["_id"] == versions[1]["_id"]


def test_doc_without_updated_at_falls_back_to_created_at(env):
    db, run, files, rows = env
    db.orders.insert_one({"order_no": 1, "created_at": T0})   # app-created order: no updated_at
    assert run() == 1
    assert rows()[0]["order_no"] == 1


def test_doc_with_no_timestamps_is_skipped_without_crashing(env):
    db, run, files, rows = env
    db.orders.insert_many([{"order_no": 1}, order(2, 5)])
    assert run() == 1                                          # only the stamped doc; no exception
    assert [r["order_no"] for r in rows()] == [2]


def test_docs_with_missing_optional_fields_are_all_written(env):
    db, run, files, rows = env
    db.orders.insert_many([order(1, 1, items=[{"title": "A", "qty": 1}]), order(2, 2)])   # #2 has no items
    assert run() == 2
    assert {r["order_no"] for r in rows()} == {1, 2}


def test_full_mode_runs_twice_keeps_both_snapshots(env):
    db, run, files, rows = env
    db.books.insert_many([{"title": f"B{i}"} for i in range(10)])
    assert run("books", "full") == 10
    assert run("books", "full") == 10
    assert len(files("books")) == 2 and len(rows("books")) == 20   # Silver dedupes snapshots


def test_watermark_not_advanced_when_write_fails(env, monkeypatch):
    db, run, files, rows = env
    db.orders.insert_one(order(1, 0))
    def boom(*a, **k):
        raise OSError("disk full")

    with monkeypatch.context() as m:                            # only the write fails; env patches stay
        m.setattr(mongo_extractor.pq, "write_table", boom)
        with pytest.raises(OSError):
            run()

    assert watermark.get_last_watermark("orders").year == 1970  # nothing committed -> row will be retried
    assert run() == 1                                           # and the retry picks it up
