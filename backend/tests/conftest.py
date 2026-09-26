import os
import sys
import pathlib
from unittest.mock import MagicMock

import pytest
import mongomock
import pymongo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ["MONGO_URI"] = "mongodb://localhost:27017/bookstore_test"
os.environ["GEMINI_API_KEY"] = "test-gemini-key"
os.environ["INTERNAL_SERVICE_TOKEN"] = "test-internal-token"


@pytest.fixture
def app_module(monkeypatch):
    """Fresh import of app_backend per test, wired to an isolated in-memory Mongo
    and a fake Gemini client so no real network calls happen."""
    monkeypatch.setattr(pymongo, "MongoClient", mongomock.MongoClient)
    import google.genai
    monkeypatch.setattr(google.genai, "Client", lambda **kwargs: MagicMock())

    sys.modules.pop("app_backend", None)
    import app_backend
    yield app_backend
    sys.modules.pop("app_backend", None)


@pytest.fixture
def client(app_module):
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def headers(app_module):
    return {"X-Internal-Token": app_module.INTERNAL_SERVICE_TOKEN}


# Real catalog rows pulled from backend/database_backup/db_backup.archive —
# using the actual titles/prices/stock the app ships with, not invented numbers.
REAL_CATALOG = [
    {"title": "Great Expectations", "author": "Charles Dickens",
     "category": "Classic Literature", "price": 9.99, "stock": 50},
    {"title": "The Adventures of Huckleberry Finn", "author": "Mark Twain",
     "category": "Classic Literature", "price": 7.99, "stock": 45},
    {"title": "To the Lighthouse", "author": "Virginia Woolf",
     "category": "Modern Classics", "price": 8.99, "stock": 30},
    {"title": "War and Peace", "author": "Leo Tolstoy",
     "category": "Fiction", "price": 12.99, "stock": 20},
    {"title": "Crime and Punishment", "author": "Fyodor Dostoevsky",
     "category": "Fiction", "price": 10.99, "stock": 25},
    {"title": "Norwegian Wood", "author": "Haruki Murakami",
     "category": "Contemporary Fiction", "price": 14.99, "stock": 40},
]


@pytest.fixture
def seed_book(app_module):
    """Insert one real catalog book (Huckleberry Finn by default — the exact
    book/price/stock from the reported oversell bug). Pass overrides to change fields."""
    def _seed(**overrides):
        book = dict(REAL_CATALOG[1])  # Huckleberry Finn: $7.99, stock 45
        book["cover_image"] = ""
        book.update(overrides)
        result = app_module.db["books"].insert_one(book)
        book["_id"] = result.inserted_id
        return book
    return _seed


@pytest.fixture
def seed_catalog(app_module):
    """Insert the full real 6-book catalog, keyed by title for easy lookup."""
    books = {}
    for entry in REAL_CATALOG:
        book = dict(entry)
        book["cover_image"] = ""
        result = app_module.db["books"].insert_one(book)
        book["_id"] = result.inserted_id
        books[book["title"]] = book
    return books


@pytest.fixture
def fresh_object_id():
    """A syntactically valid ObjectId guaranteed not to exist in the test DB."""
    from bson import ObjectId
    return str(ObjectId())
