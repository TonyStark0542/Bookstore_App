import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from pymongo import MongoClient

# Allows running both as a module (pytest/CI) and directly via CLI without VS Code import errors
try:
    from extract.watermark import get_last_watermark, set_watermark
except ImportError:
    from watermark import get_last_watermark, set_watermark

# Anchors to .../Bookstore_App/data-engineer/data-pipeline/data/bronze
BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BRONZE_DIR = str(BASE_DIR / "data" / "bronze")


def extract_collection_to_bronze(
    mongo_uri: str,
    db_name: str,
    collection_name: str,
    mode: str = "incremental",
    output_base_dir: str = DEFAULT_BRONZE_DIR,
) -> int:
    """
    Extracts documents from MongoDB and lands them as Snappy-compressed,
    date-partitioned Parquet files in the Bronze layer.
    """
    client = MongoClient(mongo_uri)
    coll = client[db_name][collection_name]

    last_watermark = get_last_watermark(collection_name)

    if mode == "full":
        # Full load for small static reference collections lacking timestamps (books, categories)
        records = list(coll.find({}))
    else:
        # Incremental load: check updated_at, or fall back to created_at if updated_at is absent
        query = {
            "$or": [
                {"updated_at": {"$gt": last_watermark}},
                {"updated_at": {"$exists": False}, "created_at": {"$gt": last_watermark}},
            ]
        }
        records = list(coll.find(query).sort("updated_at", 1))

    if not records:
        print(f"[{collection_name}] ({mode}) 0 new records since {last_watermark.isoformat()}")
        return 0

    max_watermark = last_watermark
    cleaned_records = []

    for doc in records:
        # Convert proprietary BSON ObjectId to string so PyArrow can serialize it
        doc["_id"] = str(doc["_id"])

        # Extract the latest timestamp available on the document and enforce UTC
        doc_ts = doc.get("updated_at") or doc.get("created_at")
        if doc_ts:
            if doc_ts.tzinfo is None:
                doc_ts = doc_ts.replace(tzinfo=timezone.utc)
            if doc_ts > max_watermark:
                max_watermark = doc_ts

        cleaned_records.append(doc)

    # Stamp current UTC time for full-load collections that carry zero timestamp fields
    if mode == "full" and max_watermark == last_watermark:
        max_watermark = datetime.now(timezone.utc)

    # Convert row-oriented dicts into a columnar Arrow Table
    table = pa.Table.from_pylist(cleaned_records)

    # Create Hive-style date partitions (year=YYYY/month=MM/day=DD)
    now = datetime.now(timezone.utc)
    partition_path = os.path.join(
        output_base_dir,
        collection_name,
        f"year={now.year}",
        f"month={now.month:02d}",
        f"day={now.day:02d}",
    )
    os.makedirs(partition_path, exist_ok=True)

    # Unique name per run: later runs add files to the day's partition instead of overwriting earlier ones
    file_path = os.path.join(partition_path, f"batch_{now:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}.parquet")
    pq.write_table(table, file_path, compression="SNAPPY")

    # Commit watermark ONLY after the Parquet file is safely written to disk
    set_watermark(collection_name, max_watermark)
    print(f"[{collection_name}] ({mode}) Extracted {len(records)} records -> {file_path}")
    return len(records)


if __name__ == "__main__":
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27018")
    DB_NAME = "bookstore"

    # Small reference collections -> Full load
    extract_collection_to_bronze(MONGO_URI, DB_NAME, "books", mode="full")
    extract_collection_to_bronze(MONGO_URI, DB_NAME, "categories", mode="full")

    # Growing transactional collections -> Incremental watermark load
    extract_collection_to_bronze(MONGO_URI, DB_NAME, "users", mode="incremental")
    extract_collection_to_bronze(MONGO_URI, DB_NAME, "orders", mode="incremental")