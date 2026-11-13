import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Anchors to .../Bookstore_App/data-engineer/data-pipeline/data/watermarks.json
BASE_DIR = Path(__file__).resolve().parents[1]
WATERMARK_FILE = str(BASE_DIR / "data" / "watermarks.json")


def get_last_watermark(collection_name: str) -> datetime:
    """Retrieves the last extracted timestamp for a collection, or epoch 1970 if never run."""
    if not os.path.exists(WATERMARK_FILE):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    with open(WATERMARK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        ts_str = data.get(collection_name)
        if ts_str:
            return datetime.fromisoformat(ts_str)
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def set_watermark(collection_name: str, watermark: datetime) -> None:
    """Atomically persists the new watermark timestamp after a successful Parquet write."""
    os.makedirs(os.path.dirname(WATERMARK_FILE), exist_ok=True)
    data = {}
    if os.path.exists(WATERMARK_FILE):
        with open(WATERMARK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

    data[collection_name] = watermark.isoformat()

    # Write to a temporary file first and rename so an unexpected crash never corrupts the JSON
    temp_file = f"{WATERMARK_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_file, WATERMARK_FILE)