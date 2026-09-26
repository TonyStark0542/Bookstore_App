"""Unit tests for backend/app_backend.py — every route, using the app's real
catalog data (pulled from backend/database_backup/db_backup.archive) and
exact expected numbers, not placeholder values or loose pass/fail checks.

Run with: cd backend && pytest tests/ -v
"""

FREE_SHIPPING_THRESHOLD = 25.00
SHIPPING_FEE = 4.99


def add_to_cart(client, headers, user_id, book, qty):
    return client.post(f"/api/cart/{user_id}", headers=headers, json={
        "book_id": str(book["_id"]), "qty": qty,
    })


# ============================================================================
# Internal service token gate (@app.before_request)
# ============================================================================
def test_missing_token_is_forbidden(client):
    resp = client.get("/api/books")
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "Forbidden"}


def test_wrong_token_is_forbidden(client):
    resp = client.get("/api/books", headers={"X-Internal-Token": "wrong-token"})
    assert resp.status_code == 403


def test_correct_token_passes(client, headers):
    resp = client.get("/api/books", headers=headers)
    assert resp.status_code == 200


# ============================================================================
# Book catalog
# ============================================================================
def test_get_all_books_empty(client, headers):
    resp = client.get("/api/books", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_get_all_books_returns_full_real_catalog(client, headers, seed_catalog):
    resp = client.get("/api/books", headers=headers)
    assert resp.status_code == 200
    books = resp.get_json()
    assert len(books) == 6
    titles = {b["title"] for b in books}
    assert titles == set(seed_catalog.keys())


def test_get_single_book_found_matches_real_record(client, headers, seed_book):
    book = seed_book()  # Huckleberry Finn — $7.99, stock 45
    resp = client.get(f"/api/books/{book['_id']}", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["title"] == "The Adventures of Huckleberry Finn"
    assert data["author"] == "Mark Twain"
    assert data["category"] == "Classic Literature"
    assert data["price"] == 7.99
    assert data["stock"] == 45


def test_get_single_book_not_found(client, headers, fresh_object_id):
    resp = client.get(f"/api/books/{fresh_object_id}", headers=headers)
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Book not found"}


def test_get_single_book_invalid_id_returns_400(client, headers):
    resp = client.get("/api/books/not-a-valid-object-id", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid book id"}


def test_get_book_summary_success_uses_real_title_and_author_in_prompt(client, headers, seed_book, app_module):
    book = seed_book()  # Huckleberry Finn by Mark Twain
    app_module.ai_client.models.generate_content.return_value.text = (
        "Huck Finn escapes his abusive father and rafts down the Mississippi with Jim."
    )
    resp = client.get(f"/api/books/{book['_id']}/summary", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {
        "book_id": str(book["_id"]),
        "ai_summary": "Huck Finn escapes his abusive father and rafts down the Mississippi with Jim.",
    }
    call_kwargs = app_module.ai_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    assert call_kwargs["contents"] == "Summarize The Adventures of Huckleberry Finn by Mark Twain concisely."


def test_get_book_summary_book_not_found(client, headers, fresh_object_id):
    resp = client.get(f"/api/books/{fresh_object_id}/summary", headers=headers)
    assert resp.status_code == 404


def test_get_book_summary_invalid_id_returns_400(client, headers):
    resp = client.get("/api/books/not-a-valid-object-id/summary", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid book id"}


def test_get_books_by_category_returns_only_matching_real_titles(client, headers, seed_catalog):
    resp = client.get("/api/books/category/Fiction", headers=headers)
    assert resp.status_code == 200
    titles = {b["title"] for b in resp.get_json()}
    # Only War and Peace and Crime and Punishment are tagged "Fiction" in the real catalog
    assert titles == {"War and Peace", "Crime and Punishment"}


def test_get_books_by_category_no_match_returns_empty(client, headers, seed_catalog):
    resp = client.get("/api/books/category/Sci-Fi", headers=headers)
    assert resp.get_json() == []


# ============================================================================
# Auth
# ============================================================================
def test_register_success_returns_exact_submitted_values(client, headers):
    resp = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "pepper"
    assert data["email"] == "pepper@gmail.com"
    assert len(data["user_id"]) == 24  # valid ObjectId hex length


def test_register_missing_name_or_email_rejected(client, headers):
    resp = client.post("/api/auth/register", headers=headers, json={
        "name": "", "email": "", "password": "readright1",
    })
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "A valid name and email address are required"}


def test_register_invalid_email_rejected(client, headers):
    resp = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper-at-gmail.com", "password": "readright1",
    })
    assert resp.status_code == 400


def test_register_password_exactly_5_chars_rejected(client, headers):
    # boundary: code requires len(password) >= 6
    resp = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "abcde",
    })
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Password must be at least 6 characters long"}


def test_register_password_exactly_6_chars_accepted(client, headers):
    # boundary: 6 chars is the minimum accepted length
    resp = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "abcdef",
    })
    assert resp.status_code == 201


def test_register_duplicate_email_rejected(client, headers):
    payload = {"name": "pepper", "email": "pepper@gmail.com", "password": "readright1"}
    client.post("/api/auth/register", headers=headers, json=payload)
    resp = client.post("/api/auth/register", headers=headers, json=payload)
    assert resp.status_code == 409
    assert resp.get_json() == {"error": "An account with this email already exists"}


def test_register_email_is_lowercased_and_trimmed(client, headers):
    resp = client.post("/api/auth/register", headers=headers, json={
        "name": "  pepper  ", "email": "  PEPPER@GMAIL.COM  ", "password": "readright1",
    })
    data = resp.get_json()
    assert data["name"] == "pepper"
    assert data["email"] == "pepper@gmail.com"


def test_login_success_returns_same_identity_as_register(client, headers):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    resp = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["user_id"] == reg["user_id"]
    assert data["name"] == "pepper"
    assert data["email"] == "pepper@gmail.com"


def test_login_wrong_password_rejected(client, headers):
    client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    })
    resp = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "wrongpass",
    })
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Invalid email or password"}


def test_login_nonexistent_user_rejected(client, headers):
    resp = client.post("/api/auth/login", headers=headers, json={
        "email": "ghost@gmail.com", "password": "whatever1",
    })
    assert resp.status_code == 401


# ============================================================================
# Cart — exact totals computed against the app's real price/threshold/fee
# ============================================================================
def test_get_cart_empty_totals_are_zero(client, headers):
    resp = client.get("/api/cart/pepper-id", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"items": [], "count": 0, "subtotal": 0, "shipping": 0, "total": 0}


def test_add_to_cart_computes_exact_subtotal(client, headers, seed_book):
    book = seed_book()  # $7.99
    resp = add_to_cart(client, headers, "pepper-id", book, 2)
    data = resp.get_json()
    assert data["count"] == 2
    assert data["subtotal"] == 15.98          # 2 * 7.99
    assert data["shipping"] == SHIPPING_FEE   # 15.98 < 25.00
    assert data["total"] == 20.97             # 15.98 + 4.99


def test_add_to_cart_default_qty_is_one(client, headers, seed_book):
    book = seed_book()
    resp = client.post("/api/cart/pepper-id", headers=headers, json={"book_id": str(book["_id"])})
    assert resp.get_json()["count"] == 1


def test_add_to_cart_increment_existing_item_sums_qty(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = add_to_cart(client, headers, "pepper-id", book, 2)
    data = resp.get_json()
    assert data["count"] == 3
    assert data["subtotal"] == 23.97  # 3 * 7.99


def test_add_to_cart_invalid_book_id_rejected(client, headers):
    resp = client.post("/api/cart/pepper-id", headers=headers, json={
        "book_id": "not-a-valid-id", "qty": 1,
    })
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid book id or quantity"}


def test_add_to_cart_nonexistent_book_rejected(client, headers, fresh_object_id):
    resp = client.post("/api/cart/pepper-id", headers=headers, json={
        "book_id": fresh_object_id, "qty": 1,
    })
    assert resp.status_code == 404


def test_update_cart_item_qty_recomputes_totals(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = client.put(f"/api/cart/pepper-id/{book['_id']}", headers=headers, json={"qty": 4})
    data = resp.get_json()
    assert data["count"] == 4
    assert data["subtotal"] == 31.96          # 4 * 7.99
    assert data["shipping"] == 0              # >= 25.00 -> free shipping
    assert data["total"] == 31.96


def test_update_cart_item_qty_zero_removes_item(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = client.put(f"/api/cart/pepper-id/{book['_id']}", headers=headers, json={"qty": 0})
    assert resp.get_json()["items"] == []


def test_remove_cart_item_zeroes_totals(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 3)
    resp = client.delete(f"/api/cart/pepper-id/{book['_id']}", headers=headers)
    data = resp.get_json()
    assert data["items"] == []
    assert data["subtotal"] == 0
    assert data["shipping"] == 0
    assert data["total"] == 0


def test_cart_shipping_fee_applies_just_below_threshold(client, headers, seed_book):
    book = seed_book()
    resp = add_to_cart(client, headers, "pepper-id", book, 3)  # 23.97 < 25.00
    assert resp.get_json()["shipping"] == SHIPPING_FEE


def test_cart_free_shipping_applies_at_or_above_threshold(client, headers, seed_book):
    book = seed_book()
    resp = add_to_cart(client, headers, "pepper-id", book, 4)  # 31.96 >= 25.00
    assert resp.get_json()["shipping"] == 0


# ============================================================================
# Orders — includes the regression tests for the oversell bug fix
# ============================================================================
def test_create_order_unsupported_payment_method_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "bitcoin"}})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Unsupported payment method"}


def test_create_order_empty_cart_rejected(client, headers):
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Your cart is empty"}


def test_create_order_oversell_is_rejected_stock_untouched(client, headers, seed_book):
    """Exact regression test for the reported bug: 46 Huckleberry Finns ordered
    against 45 in stock must be rejected up front — no charge, no stock change."""
    book = seed_book()  # stock 45, $7.99 — the real numbers from the bug report
    add_to_cart(client, headers, "pepper-id", book, 46)
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "upi"}})

    assert resp.status_code == 409
    assert resp.get_json() == {"error": "Only 45 left of The Adventures of Huckleberry Finn"}

    refreshed = client.get(f"/api/books/{book['_id']}", headers=headers).get_json()
    assert refreshed["stock"] == 45  # untouched, not partially decremented

    assert client.get("/api/orders/pepper-id", headers=headers).get_json() == []  # no phantom order


def test_create_order_at_exact_stock_boundary_succeeds(client, headers, seed_book):
    book = seed_book()  # stock 45
    add_to_cart(client, headers, "pepper-id", book, 45)  # buy every remaining copy
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "card"}})

    assert resp.status_code == 201
    data = resp.get_json()
    assert data["total"] == 359.55  # 45 * 7.99
    assert data["payment_status"] == "PAID"

    refreshed = client.get(f"/api/books/{book['_id']}", headers=headers).get_json()
    assert refreshed["stock"] == 0


def test_create_order_success_decrements_exact_stock_and_clears_cart(client, headers, seed_book):
    book = seed_book()  # stock 45
    add_to_cart(client, headers, "pepper-id", book, 5)
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "card"}})

    assert resp.status_code == 201
    assert resp.get_json()["total"] == 39.95  # 5 * 7.99, >=25 so free shipping

    refreshed = client.get(f"/api/books/{book['_id']}", headers=headers).get_json()
    assert refreshed["stock"] == 40  # 45 - 5

    assert client.get("/api/cart/pepper-id", headers=headers).get_json()["items"] == []


def test_create_order_cod_status_is_pending(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}})
    data = resp.get_json()
    assert data["payment_status"] == "PENDING"
    assert data["total"] == 12.98  # 7.99 + 4.99 shipping


def test_create_order_upi_status_is_paid_immediately(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = client.post("/api/orders/pepper-id", headers=headers, json={
        "payment": {"method": "upi", "detail": "pepper@oksbi"},
    })
    assert resp.get_json()["payment_status"] == "PAID"


def test_create_order_txn_id_is_derived_from_order_id(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    resp = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "card"}})
    data = resp.get_json()
    assert data["txn_id"] == f"TXN-{data['order_id'][-10:].upper()}"


def test_get_user_orders_reflects_real_purchase(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 2)
    client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}})

    resp = client.get("/api/orders/pepper-id", headers=headers)
    orders = resp.get_json()
    assert len(orders) == 1
    order = orders[0]
    assert order["total"] == 20.97  # 2 * 7.99 + 4.99 shipping
    assert order["items"][0]["title"] == "The Adventures of Huckleberry Finn"
    assert order["items"][0]["qty"] == 2
    assert order["status"] == "Confirmed"
    assert order["stage_index"] == 0


def test_get_user_orders_empty_for_unknown_user(client, headers):
    resp = client.get("/api/orders/nobody-yet", headers=headers)
    assert resp.get_json() == []


# ============================================================================
# Admin
# ============================================================================
def test_admin_list_users_sums_multiple_real_orders(client, headers, seed_catalog):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    user_id = reg["user_id"]

    finn = seed_catalog["The Adventures of Huckleberry Finn"]
    war_and_peace = seed_catalog["War and Peace"]

    add_to_cart(client, headers, user_id, finn, 2)
    client.post(f"/api/orders/{user_id}", headers=headers, json={"payment": {"method": "upi"}})  # 20.97

    add_to_cart(client, headers, user_id, war_and_peace, 1)
    client.post(f"/api/orders/{user_id}", headers=headers, json={"payment": {"method": "card"}})  # 17.98

    resp = client.get("/api/admin/users", headers=headers)
    users = resp.get_json()
    assert len(users) == 1
    assert users[0]["name"] == "pepper"
    assert users[0]["order_count"] == 2
    assert users[0]["total_spent"] == 38.95  # 20.97 + 17.98


def test_admin_list_users_zero_orders_for_new_signup(client, headers):
    client.post("/api/auth/register", headers=headers, json={
        "name": "tony", "email": "tony@gmail.com", "password": "readright1",
    })
    resp = client.get("/api/admin/users", headers=headers)
    users = resp.get_json()
    assert users[0]["order_count"] == 0
    assert users[0]["total_spent"] == 0


def test_admin_advance_order_walks_through_every_real_stage(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    order_id = order["order_id"]

    for expected_stage, expected_index in [("Packed", 1), ("Shipped", 2), ("Delivered", 3)]:
        resp = client.post(f"/api/admin/orders/{order_id}/advance", headers=headers)
        data = resp.get_json()
        assert data["status"] == expected_stage
        assert data["stage_index"] == expected_index


def test_admin_advance_order_already_delivered_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    for _ in range(3):
        client.post(f"/api/admin/orders/{order['order_id']}/advance", headers=headers)

    resp = client.post(f"/api/admin/orders/{order['order_id']}/advance", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Order is already Delivered"}


def test_admin_advance_order_not_found(client, headers, fresh_object_id):
    resp = client.post(f"/api/admin/orders/{fresh_object_id}/advance", headers=headers)
    assert resp.status_code == 404


def test_admin_advance_order_invalid_id_rejected(client, headers):
    resp = client.post("/api/admin/orders/not-an-id/advance", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid order id"}


def test_admin_update_stock_restocks_to_exact_value(client, headers, seed_catalog):
    great_expectations = seed_catalog["Great Expectations"]  # real stock: 50
    resp = client.put(f"/api/admin/books/{great_expectations['_id']}/stock",
                       headers=headers, json={"stock": 75})
    assert resp.status_code == 200
    assert resp.get_json() == {
        "book_id": str(great_expectations["_id"]),
        "title": "Great Expectations",
        "stock": 75,
    }
    refreshed = client.get(f"/api/books/{great_expectations['_id']}", headers=headers).get_json()
    assert refreshed["stock"] == 75


def test_admin_update_stock_can_decrease_to_exact_value(client, headers, seed_catalog):
    great_expectations = seed_catalog["Great Expectations"]
    resp = client.put(f"/api/admin/books/{great_expectations['_id']}/stock",
                       headers=headers, json={"stock": 5})
    assert resp.get_json()["stock"] == 5


def test_admin_update_stock_zero_is_allowed(client, headers, seed_book):
    book = seed_book()
    resp = client.put(f"/api/admin/books/{book['_id']}/stock", headers=headers, json={"stock": 0})
    assert resp.status_code == 200
    assert resp.get_json()["stock"] == 0


def test_admin_update_stock_negative_rejected(client, headers, seed_book):
    book = seed_book()
    resp = client.put(f"/api/admin/books/{book['_id']}/stock", headers=headers, json={"stock": -1})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Stock cannot be negative"}
    # unchanged
    refreshed = client.get(f"/api/books/{book['_id']}", headers=headers).get_json()
    assert refreshed["stock"] == 45


def test_admin_update_stock_book_not_found(client, headers, fresh_object_id):
    resp = client.put(f"/api/admin/books/{fresh_object_id}/stock", headers=headers, json={"stock": 5})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Book not found"}


def test_admin_update_stock_missing_field_rejected(client, headers, seed_book):
    book = seed_book()
    resp = client.put(f"/api/admin/books/{book['_id']}/stock", headers=headers, json={})
    assert resp.status_code == 400


def test_admin_update_stock_non_numeric_rejected(client, headers, seed_book):
    book = seed_book()
    resp = client.put(f"/api/admin/books/{book['_id']}/stock", headers=headers, json={"stock": "a lot"})
    assert resp.status_code == 400


def test_admin_update_stock_invalid_book_id_rejected(client, headers):
    resp = client.put("/api/admin/books/not-an-id/stock", headers=headers, json={"stock": 5})
    assert resp.status_code == 400


# ============================================================================
# Admin identity — is_admin / disabled on login and the admin users listing
# ============================================================================
def test_login_is_admin_false_by_default(client, headers):
    client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    })
    resp = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    })
    assert resp.get_json()["is_admin"] is False


def test_login_reflects_promoted_admin_flag(client, headers, app_module):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    app_module.users_col.update_one({"email": "pepper@gmail.com"}, {"$set": {"is_admin": True}})

    resp = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    })
    assert resp.get_json()["is_admin"] is True


def test_login_disabled_account_rejected_with_403(client, headers, app_module):
    client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    })
    app_module.users_col.update_one({"email": "pepper@gmail.com"}, {"$set": {"disabled": True}})

    resp = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    })
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "This account has been disabled"}


def test_admin_list_users_includes_role_and_status_flags(client, headers, app_module):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    app_module.users_col.update_one({"email": "pepper@gmail.com"}, {"$set": {"is_admin": True}})

    resp = client.get("/api/admin/users", headers=headers)
    user = resp.get_json()[0]
    assert user["is_admin"] is True
    assert user["disabled"] is False


# ============================================================================
# Admin — role promotion/revocation
# ============================================================================
def test_admin_update_role_grants_admin(client, headers):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    resp = client.put(f"/api/admin/users/{reg['user_id']}/role", headers=headers, json={"is_admin": True})
    assert resp.status_code == 200
    assert resp.get_json() == {"user_id": reg["user_id"], "email": "pepper@gmail.com", "is_admin": True}

    login = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    assert login["is_admin"] is True


def test_admin_update_role_revokes_admin(client, headers):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    client.put(f"/api/admin/users/{reg['user_id']}/role", headers=headers, json={"is_admin": True})
    resp = client.put(f"/api/admin/users/{reg['user_id']}/role", headers=headers, json={"is_admin": False})
    assert resp.get_json()["is_admin"] is False


def test_admin_update_role_user_not_found(client, headers, fresh_object_id):
    resp = client.put(f"/api/admin/users/{fresh_object_id}/role", headers=headers, json={"is_admin": True})
    assert resp.status_code == 404


def test_admin_update_role_invalid_user_id_rejected(client, headers):
    resp = client.put("/api/admin/users/not-an-id/role", headers=headers, json={"is_admin": True})
    assert resp.status_code == 400


# ============================================================================
# Admin — disable/enable a user account
# ============================================================================
def test_admin_disable_user_blocks_future_login(client, headers):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    resp = client.put(f"/api/admin/users/{reg['user_id']}/status", headers=headers, json={"disabled": True})
    assert resp.status_code == 200
    assert resp.get_json() == {"user_id": reg["user_id"], "email": "pepper@gmail.com", "disabled": True}

    login = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    })
    assert login.status_code == 403


def test_admin_enable_user_restores_login(client, headers):
    reg = client.post("/api/auth/register", headers=headers, json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    }).get_json()
    client.put(f"/api/admin/users/{reg['user_id']}/status", headers=headers, json={"disabled": True})
    client.put(f"/api/admin/users/{reg['user_id']}/status", headers=headers, json={"disabled": False})

    login = client.post("/api/auth/login", headers=headers, json={
        "email": "pepper@gmail.com", "password": "readright1",
    })
    assert login.status_code == 200


def test_admin_update_status_user_not_found(client, headers, fresh_object_id):
    resp = client.put(f"/api/admin/users/{fresh_object_id}/status", headers=headers, json={"disabled": True})
    assert resp.status_code == 404


# ============================================================================
# Admin — order cancel / revert
# ============================================================================
def test_admin_cancel_order_marks_cancelled(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()

    resp = client.post(f"/api/admin/orders/{order['order_id']}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"order_id": order["order_id"], "cancelled": True}

    listed = client.get("/api/orders/pepper-id", headers=headers).get_json()[0]
    assert listed["status"] == "Cancelled"
    assert listed["cancelled"] is True


def test_admin_cancel_order_twice_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    client.post(f"/api/admin/orders/{order['order_id']}/cancel", headers=headers)

    resp = client.post(f"/api/admin/orders/{order['order_id']}/cancel", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Order is already cancelled"}


def test_admin_cancel_delivered_order_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    for _ in range(3):
        client.post(f"/api/admin/orders/{order['order_id']}/advance", headers=headers)

    resp = client.post(f"/api/admin/orders/{order['order_id']}/cancel", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Cannot cancel a delivered order"}


def test_admin_cancel_order_not_found(client, headers, fresh_object_id):
    resp = client.post(f"/api/admin/orders/{fresh_object_id}/cancel", headers=headers)
    assert resp.status_code == 404


def test_admin_advance_cancelled_order_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    client.post(f"/api/admin/orders/{order['order_id']}/cancel", headers=headers)

    resp = client.post(f"/api/admin/orders/{order['order_id']}/advance", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Order is cancelled"}


def test_admin_revert_order_moves_back_one_stage(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    client.post(f"/api/admin/orders/{order['order_id']}/advance", headers=headers)  # -> Packed

    resp = client.post(f"/api/admin/orders/{order['order_id']}/revert", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"order_id": order["order_id"], "status": "Confirmed", "stage_index": 0}


def test_admin_revert_order_at_first_stage_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()

    resp = client.post(f"/api/admin/orders/{order['order_id']}/revert", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Order is already at its first stage"}


def test_admin_revert_cancelled_order_rejected(client, headers, seed_book):
    book = seed_book()
    add_to_cart(client, headers, "pepper-id", book, 1)
    order = client.post("/api/orders/pepper-id", headers=headers, json={"payment": {"method": "cod"}}).get_json()
    client.post(f"/api/admin/orders/{order['order_id']}/advance", headers=headers)
    client.post(f"/api/admin/orders/{order['order_id']}/cancel", headers=headers)

    resp = client.post(f"/api/admin/orders/{order['order_id']}/revert", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Cannot revert a cancelled order"}


def test_admin_revert_order_not_found(client, headers, fresh_object_id):
    resp = client.post(f"/api/admin/orders/{fresh_object_id}/revert", headers=headers)
    assert resp.status_code == 404


# ============================================================================
# Admin — catalog CRUD
# ============================================================================
def test_admin_create_book_success(client, headers):
    resp = client.post("/api/admin/books", headers=headers, json={
        "title": "The Hobbit", "author": "J.R.R. Tolkien",
        "category": "Fiction", "price": 11.99, "stock": 20,
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["title"] == "The Hobbit"
    assert data["price"] == 11.99
    assert data["stock"] == 20
    assert len(data["_id"]) == 24

    fetched = client.get(f"/api/books/{data['_id']}", headers=headers).get_json()
    assert fetched["title"] == "The Hobbit"


def test_admin_create_book_missing_fields_rejected(client, headers):
    resp = client.post("/api/admin/books", headers=headers, json={
        "title": "", "author": "Someone", "category": "Fiction", "price": 9.99,
    })
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Title, author, and category are required"}


def test_admin_create_book_negative_price_rejected(client, headers):
    resp = client.post("/api/admin/books", headers=headers, json={
        "title": "Bad Book", "author": "Someone", "category": "Fiction", "price": -5, "stock": 1,
    })
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Price and stock cannot be negative"}


def test_admin_update_book_edits_only_given_fields(client, headers, seed_catalog):
    finn = seed_catalog["The Adventures of Huckleberry Finn"]
    resp = client.put(f"/api/admin/books/{finn['_id']}", headers=headers, json={"price": 6.99})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["price"] == 6.99
    assert data["title"] == "The Adventures of Huckleberry Finn"  # untouched
    assert data["author"] == "Mark Twain"  # untouched


def test_admin_update_book_no_fields_rejected(client, headers, seed_book):
    book = seed_book()
    resp = client.put(f"/api/admin/books/{book['_id']}", headers=headers, json={})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "No valid fields to update"}


def test_admin_update_book_not_found(client, headers, fresh_object_id):
    resp = client.put(f"/api/admin/books/{fresh_object_id}", headers=headers, json={"title": "New Title"})
    assert resp.status_code == 404


def test_admin_delete_book_removes_it(client, headers, seed_book):
    book = seed_book()
    resp = client.delete(f"/api/admin/books/{book['_id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"book_id": str(book["_id"]), "deleted": True}

    assert client.get(f"/api/books/{book['_id']}", headers=headers).status_code == 404


def test_admin_delete_book_not_found(client, headers, fresh_object_id):
    resp = client.delete(f"/api/admin/books/{fresh_object_id}", headers=headers)
    assert resp.status_code == 404


# ============================================================================
# Admin — audit log
# ============================================================================
def test_admin_action_is_recorded_in_audit_log_with_actor(client, headers, seed_book):
    book = seed_book()
    actor_headers = dict(headers, **{"X-Admin-Actor": "pepper"})
    client.put(f"/api/admin/books/{book['_id']}/stock", headers=actor_headers, json={"stock": 100})

    resp = client.get("/api/admin/audit-log", headers=headers)
    assert resp.status_code == 200
    entries = resp.get_json()
    assert entries[0]["actor"] == "pepper"
    assert entries[0]["action"] == "stock_update"
    assert "100" in entries[0]["detail"]


def test_admin_action_without_actor_header_logs_unknown(client, headers, seed_book):
    book = seed_book()
    client.put(f"/api/admin/books/{book['_id']}/stock", headers=headers, json={"stock": 30})
    entries = client.get("/api/admin/audit-log", headers=headers).get_json()
    assert entries[0]["actor"] == "unknown"


def test_admin_audit_log_empty_by_default(client, headers):
    resp = client.get("/api/admin/audit-log", headers=headers)
    assert resp.get_json() == []
