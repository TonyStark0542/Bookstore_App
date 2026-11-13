"""Seed synthetic users + orders into the app's Mongo, matching the app's real schema.
Idempotent: only deletes docs it created (seeded=True). Real users/orders are untouched."""
import os, random
from datetime import datetime, timedelta, timezone
from faker import Faker
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27018")  # compose maps 27018->27017
STAGES = ["Confirmed", "Packed", "Shipped", "Delivered"]
N_USERS, N_ORDERS, DAYS = 50, 1000, 60

fake, now = Faker(), datetime.now(timezone.utc)
db = MongoClient(MONGO_URI)["bookstore"]
books = list(db.books.find())
assert books, "No books in Mongo"
# 5 categories -> uneven popularity so category analytics aren't flat
cats = [c["name"] for c in db.categories.find()]
pop = {"Fiction": 5, "Classic Literature": 4, "Contemporary Fiction": 3, "Modern Classics": 2, "Poetry": 1}
weights = [pop.get(b["category"], 1) for b in books]
print(f"{len(books)} books across {len(cats)} categories")

db.users.delete_many({"seeded": True})
db.orders.delete_many({"seeded": True})

users = []
for _ in range(N_USERS):
    t = now - timedelta(days=random.randint(DAYS, 90), hours=random.randint(0, 23))
    users.append({"name": fake.name(), "email": fake.unique.email(), "password_hash": "x",
                  "city": fake.city(), "created_at": t, "updated_at": t, "seeded": True})
user_ids = [str(i) for i in db.users.insert_many(users).inserted_ids]

orders = []
for _ in range(N_ORDERS):
    created = now - timedelta(seconds=random.randint(0, DAYS * 86400))
    items = []
    picked, n = {}, random.randint(1, 4)
    while len(picked) < n:
        b = random.choices(books, weights)[0]
        picked[b["_id"]] = b
    for b in picked.values():
        qty = random.randint(1, 3)
        items.append({"book_id": str(b["_id"]), "title": b["title"], "author": b["author"], "category": b["category"],
                      "price": b["price"], "qty": qty, "line_total": round(b["price"] * qty, 2)})
    subtotal = round(sum(i["line_total"] for i in items), 2)
    shipping = 0.0 if subtotal >= 25 else 4.99
    cancelled = random.random() < 0.08
    stage = random.randint(0, 3)
    pay = random.choice(["card", "upi", "cod"])
    updated = min(created + timedelta(hours=12 * stage + random.randint(0, 11)), now)
    orders.append({
        "user_id": random.choice(user_ids), "items": items, "subtotal": subtotal,
        "shipping": shipping, "total": round(subtotal + shipping, 2),
        "payment": {"method": pay, "detail": "", "status": "PENDING" if pay == "cod" and stage < 3 else "PAID"},
        "status": "Cancelled" if cancelled else STAGES[stage], "stage_index": stage,
        "cancelled": cancelled, "created_at": created, "updated_at": updated, "seeded": True})
db.orders.insert_many(orders)
print(f"books={len(books)} users+={N_USERS} orders+={N_ORDERS}")
