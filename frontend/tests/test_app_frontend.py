"""Unit tests for frontend/app_frontend.py — every route, decorator, and helper,
using realistic values (a real user session shape, the app's actual admin
password check, real book payloads) and exact expected results.

The only thing mocked is the HTTP call to the backend microservice — that's a
genuine network boundary, not logic to verify. Everything else (session
gating, rate limiting, admin password check, proxying) runs as real code.

Run with: cd frontend && pytest tests/ -v
"""
from conftest import fake_response


# ============================================================================
# require_env — the startup gate that all three secrets go through
# ============================================================================
def test_require_env_returns_value_when_set(app_module, monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "the-real-value")
    assert app_module.require_env("SOME_TEST_VAR") == "the-real-value"


def test_require_env_exits_with_code_1_when_missing(app_module, monkeypatch):
    monkeypatch.delenv("SOME_MISSING_VAR", raising=False)
    try:
        app_module.require_env("SOME_MISSING_VAR")
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert exc.code == 1


# ============================================================================
# rate_limited — pure sliding-window logic, checked at its exact boundary
# ============================================================================
def test_rate_limited_allows_exactly_5_then_blocks_the_6th(app_module):
    key = "login:203.0.113.5"
    for attempt in range(5):
        assert app_module.rate_limited(key) is False, f"attempt {attempt + 1} should be allowed"
    assert app_module.rate_limited(key) is True


def test_rate_limited_resets_after_the_window_expires(app_module, monkeypatch):
    key = "login:203.0.113.6"
    clock = {"now": 1000.0}
    monkeypatch.setattr(app_module.time, "time", lambda: clock["now"])

    for _ in range(5):
        app_module.rate_limited(key, limit=5, window=60)
    assert app_module.rate_limited(key, limit=5, window=60) is True  # blocked at t=1000

    clock["now"] = 1061.0  # 61s later, window has expired
    assert app_module.rate_limited(key, limit=5, window=60) is False


def test_rate_limited_tracks_separate_keys_independently(app_module):
    assert app_module.rate_limited("admin-login:1.1.1.1") is False
    for _ in range(5):
        app_module.rate_limited("admin-login:1.1.1.1")
    assert app_module.rate_limited("admin-login:1.1.1.1") is True
    # a different IP is a different bucket, unaffected by the one above
    assert app_module.rate_limited("admin-login:2.2.2.2") is False


# ============================================================================
# Customer session gate (login_required_page / login_required_api)
# ============================================================================
def test_index_redirects_to_login_when_no_session(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login?next=/"


def test_index_renders_when_logged_in(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [])
    resp = logged_in_client.get("/")
    assert resp.status_code == 200


def test_storefront_shows_admin_panel_link_for_promoted_admin(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [])
    with logged_in_client.session_transaction() as sess:
        sess["is_admin"] = True
    resp = logged_in_client.get("/")
    assert b"Admin panel" in resp.data


def test_storefront_hides_admin_panel_link_for_regular_customer(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [])
    resp = logged_in_client.get("/")
    assert b"Admin panel" not in resp.data


def test_cart_page_requires_login(client):
    resp = client.get("/cart")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_cart_api_proxy_requires_login_returns_401_json(client):
    resp = client.get("/api/cart")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Login required"}


def test_login_page_redirects_home_if_already_logged_in(logged_in_client):
    resp = logged_in_client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_logout_clears_the_session(logged_in_client):
    resp = logged_in_client.get("/logout")
    assert resp.status_code == 302
    with logged_in_client.session_transaction() as sess:
        assert "user_id" not in sess
        assert "user_name" not in sess


# ============================================================================
# Admin session gate (admin_required_page / admin_required_api)
# ============================================================================
def test_admin_page_redirects_to_admin_login_when_not_admin(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/login"


def test_admin_api_proxy_requires_admin_returns_401_json(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Admin login required"}


def test_admin_books_proxy_requires_admin(client):
    resp = client.get("/api/admin/books")
    assert resp.status_code == 401


def test_admin_stock_update_proxy_requires_admin(client):
    resp = client.put("/api/admin/books/abc123/stock", json={"stock": 10})
    assert resp.status_code == 401


def test_admin_page_renders_when_logged_in_as_admin(admin_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [])
    resp = admin_client.get("/admin")
    assert resp.status_code == 200


def test_admin_page_renders_real_user_row_with_role_and_status(admin_client, mock_backend):
    """Regression test: the extended admin.html template (Admin/Disabled badges,
    Make-admin/Disable buttons) must not throw a Jinja error on real user data."""
    mock_backend["get"].return_value = fake_response(200, [{
        "user_id": "665f1a2b3c4d5e6f78901234", "name": "pepper", "email": "pepper@gmail.com",
        "order_count": 2, "total_spent": 38.95, "is_admin": True, "disabled": False,
    }])
    resp = admin_client.get("/admin")
    assert resp.status_code == 200
    assert b"pepper@gmail.com" in resp.data
    assert b"Revoke admin" in resp.data
    assert b"Disable" in resp.data


# ============================================================================
# Admin login — checked against the app's real ADMIN_PASSWORD env value
# ============================================================================
def test_admin_login_with_correct_password_sets_session(client, app_module):
    resp = client.post("/admin/login", json={"password": app_module.ADMIN_PASSWORD})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    with client.session_transaction() as sess:
        assert sess["is_admin"] is True


def test_admin_login_with_wrong_password_rejected(client):
    resp = client.post("/admin/login", json={"password": "definitely-wrong"})
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "Incorrect admin password"}
    with client.session_transaction() as sess:
        assert "is_admin" not in sess


def test_admin_login_rate_limited_after_5_wrong_attempts(client):
    for _ in range(5):
        resp = client.post("/admin/login", json={"password": "wrong"})
        assert resp.status_code == 401
    resp = client.post("/admin/login", json={"password": "wrong"})
    assert resp.status_code == 429
    assert resp.get_json() == {"error": "Too many attempts, try again in a minute"}


def test_admin_login_page_redirects_to_panel_if_already_admin(admin_client):
    resp = admin_client.get("/admin/login")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin"


def test_admin_logout_clears_admin_session(admin_client):
    resp = admin_client.get("/admin/logout")
    assert resp.status_code == 302
    with admin_client.session_transaction() as sess:
        assert "is_admin" not in sess


# ============================================================================
# Auth proxy — register/login forwarded to the backend, session set on success
# ============================================================================
def test_auth_proxy_rejects_unknown_mode(client):
    resp = client.post("/api/auth/delete-account", json={})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Unknown auth action"}


def test_auth_proxy_register_success_sets_real_session_values(client, mock_backend):
    mock_backend["post"].return_value = fake_response(201, {
        "user_id": "665f1a2b3c4d5e6f78901234", "name": "pepper", "email": "pepper@gmail.com",
    })
    resp = client.post("/api/auth/register", json={
        "name": "pepper", "email": "pepper@gmail.com", "password": "readright1",
    })
    assert resp.status_code == 201
    with client.session_transaction() as sess:
        assert sess["user_id"] == "665f1a2b3c4d5e6f78901234"
        assert sess["user_name"] == "pepper"

    # request body must be forwarded to the backend untouched
    forwarded_json = mock_backend["post"].call_args.kwargs["json"]
    assert forwarded_json == {"name": "pepper", "email": "pepper@gmail.com", "password": "readright1"}


def test_auth_proxy_login_failure_does_not_set_session(client, mock_backend):
    mock_backend["post"].return_value = fake_response(401, {"error": "Invalid email or password"})
    resp = client.post("/api/auth/login", json={"email": "pepper@gmail.com", "password": "wrong"})
    assert resp.status_code == 401
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_auth_proxy_success_status_with_malformed_body_does_not_crash(client, mock_backend):
    """Regression test: a 200/201 backend reply missing user_id/name must not
    raise an unhandled KeyError — it should just pass the reply through as-is."""
    mock_backend["post"].return_value = fake_response(200, {})
    resp = client.post("/api/auth/login", json={"email": "a@b.com", "password": "x"})
    assert resp.status_code == 200
    assert resp.get_json() == {}
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_auth_proxy_rate_limited_after_5_attempts(client, mock_backend):
    mock_backend["post"].return_value = fake_response(401, {"error": "Invalid email or password"})
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "a@b.com", "password": "x"})
    resp = client.post("/api/auth/login", json={"email": "a@b.com", "password": "x"})
    assert resp.status_code == 429


# ============================================================================
# Cart proxy — user id always injected from the session, never the request body
# ============================================================================
def test_cart_get_proxy_uses_session_user_id_not_client_input(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, {
        "items": [], "count": 0, "subtotal": 0, "shipping": 0, "total": 0,
    })
    resp = logged_in_client.get("/api/cart")
    assert resp.status_code == 200
    called_url = mock_backend["get"].call_args.args[0]
    assert called_url == "http://backend.test/api/cart/user123"  # session's user_id, not spoofable


def test_cart_add_proxy_forwards_body_and_status(logged_in_client, mock_backend):
    real_cart_response = {
        "items": [{"book_id": "b1", "title": "The Adventures of Huckleberry Finn",
                   "qty": 2, "price": 7.99, "line_total": 15.98}],
        "count": 2, "subtotal": 15.98, "shipping": 4.99, "total": 20.97,
    }
    mock_backend["post"].return_value = fake_response(200, real_cart_response)
    resp = logged_in_client.post("/api/cart", json={"book_id": "b1", "qty": 2})
    assert resp.status_code == 200
    assert resp.get_json() == real_cart_response


def test_cart_item_proxy_put_updates_quantity(logged_in_client, mock_backend):
    mock_backend["put"].return_value = fake_response(200, {"count": 5})
    resp = logged_in_client.put("/api/cart/b1", json={"qty": 5})
    assert resp.status_code == 200
    called_url = mock_backend["put"].call_args.args[0]
    assert called_url == "http://backend.test/api/cart/user123/b1"


def test_cart_item_proxy_delete_removes_item(logged_in_client, mock_backend):
    mock_backend["delete"].return_value = fake_response(200, {"items": []})
    resp = logged_in_client.delete("/api/cart/b1")
    assert resp.status_code == 200
    called_url = mock_backend["delete"].call_args.args[0]
    assert called_url == "http://backend.test/api/cart/user123/b1"


def test_cart_proxies_are_all_login_gated(client):
    assert client.post("/api/cart", json={}).status_code == 401
    assert client.put("/api/cart/b1", json={}).status_code == 401
    assert client.delete("/api/cart/b1").status_code == 401


# ============================================================================
# Order proxy
# ============================================================================
def test_order_create_proxy_forwards_session_user_and_payment(logged_in_client, mock_backend):
    mock_backend["post"].return_value = fake_response(201, {
        "order_id": "o1", "txn_id": "TXN-ABCDEF1234", "total": 20.97, "payment_status": "PAID",
    })
    resp = logged_in_client.post("/api/orders", json={"payment": {"method": "card", "detail": "1212"}})
    assert resp.status_code == 201
    assert resp.get_json()["total"] == 20.97
    called_url = mock_backend["post"].call_args.args[0]
    assert called_url == "http://backend.test/api/orders/user123"


def test_order_create_proxy_requires_login(client):
    resp = client.post("/api/orders", json={"payment": {"method": "cod"}})
    assert resp.status_code == 401


def test_order_create_proxy_surfaces_backend_rejection(logged_in_client, mock_backend):
    # e.g. the oversell-stock 409 from the backend must pass through unchanged
    mock_backend["post"].return_value = fake_response(
        409, {"error": "Only 45 left of The Adventures of Huckleberry Finn"})
    resp = logged_in_client.post("/api/orders", json={"payment": {"method": "upi"}})
    assert resp.status_code == 409
    assert resp.get_json() == {"error": "Only 45 left of The Adventures of Huckleberry Finn"}


# ============================================================================
# Admin proxies — users, orders, advance, and the new catalog/stock endpoints
# ============================================================================
def test_admin_users_proxy_forwards_real_shape(admin_client, mock_backend):
    real_users = [{"user_id": "u1", "name": "pepper", "email": "pepper@gmail.com",
                   "order_count": 2, "total_spent": 38.95}]
    mock_backend["get"].return_value = fake_response(200, real_users)
    resp = admin_client.get("/api/admin/users")
    assert resp.status_code == 200
    assert resp.get_json() == real_users


def test_admin_user_orders_proxy_forwards(admin_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [{"order_id": "o1", "total": 20.97}])
    resp = admin_client.get("/api/admin/orders/u1")
    assert resp.status_code == 200
    assert resp.get_json()[0]["total"] == 20.97


def test_admin_advance_order_proxy_forwards(admin_client, mock_backend):
    mock_backend["post"].return_value = fake_response(200, {"order_id": "o1", "status": "Packed", "stage_index": 1})
    resp = admin_client.post("/api/admin/orders/o1/advance")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "Packed"


def test_admin_books_proxy_forwards_real_catalog_shape(admin_client, mock_backend):
    real_books = [{"_id": "666fe8d117409f1a8391256d", "title": "The Adventures of Huckleberry Finn",
                   "author": "Mark Twain", "price": 7.99, "stock": 45}]
    mock_backend["get"].return_value = fake_response(200, real_books)
    resp = admin_client.get("/api/admin/books")
    assert resp.status_code == 200
    assert resp.get_json() == real_books


def test_admin_update_stock_proxy_forwards_body_and_result(admin_client, mock_backend):
    mock_backend["put"].return_value = fake_response(200, {
        "book_id": "666fe8d117409f1a8391256d", "title": "The Adventures of Huckleberry Finn", "stock": 100,
    })
    resp = admin_client.put("/api/admin/books/666fe8d117409f1a8391256d/stock", json={"stock": 100})
    assert resp.status_code == 200
    assert resp.get_json()["stock"] == 100

    forwarded_json = mock_backend["put"].call_args.kwargs["json"]
    assert forwarded_json == {"stock": 100}
    called_url = mock_backend["put"].call_args.args[0]
    assert called_url == "http://backend.test/api/admin/books/666fe8d117409f1a8391256d/stock"


def test_admin_update_stock_proxy_surfaces_negative_stock_rejection(admin_client, mock_backend):
    mock_backend["put"].return_value = fake_response(400, {"error": "Stock cannot be negative"})
    resp = admin_client.put("/api/admin/books/666fe8d117409f1a8391256d/stock", json={"stock": -5})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Stock cannot be negative"}


# ============================================================================
# Book pages and the Gemini summary / legacy proxies
# ============================================================================
def test_book_detail_page_renders_with_real_book_payload(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, {
        "_id": "666fe8d117409f1a8391256d", "title": "The Adventures of Huckleberry Finn",
        "author": "Mark Twain", "category": "Classic Literature", "price": 7.99, "stock": 45,
        "cover_image": "",
    })
    resp = logged_in_client.get("/books/666fe8d117409f1a8391256d")
    assert resp.status_code == 200
    assert b"The Adventures of Huckleberry Finn" in resp.data


def test_book_detail_page_propagates_backend_404(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(404, {"error": "Book not found"})
    resp = logged_in_client.get("/books/nonexistent")
    assert resp.status_code == 404


def test_category_page_renders(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [
        {"_id": "1", "title": "War and Peace", "author": "Leo Tolstoy", "price": 12.99, "stock": 20},
    ])
    resp = logged_in_client.get("/category/Fiction")
    assert resp.status_code == 200
    assert b"War and Peace" in resp.data


def test_frontend_book_summary_proxy_is_not_login_gated(client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, {
        "book_id": "666fe8d117409f1a8391256d",
        "ai_summary": "Huck Finn escapes his abusive father and rafts down the Mississippi with Jim.",
    })
    resp = client.get("/api/books/666fe8d117409f1a8391256d/summary")
    assert resp.status_code == 200
    assert "Huck Finn" in resp.get_json()["ai_summary"]


def test_legacy_list_books_proxy(client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [{"title": "Norwegian Wood"}])
    resp = client.get("/books")
    assert resp.status_code == 200
    assert resp.get_json() == [{"title": "Norwegian Wood"}]


def test_legacy_category_proxy(client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [])
    resp = client.get("/api/books/category/Poetry")
    assert resp.status_code == 200


# ============================================================================
# Checkout / orders pages
# ============================================================================
def test_checkout_redirects_to_cart_when_cart_is_empty(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, {"items": []})
    resp = logged_in_client.get("/checkout")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/cart"


def test_checkout_renders_when_cart_has_real_items(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, {
        "items": [{"book_id": "b1", "title": "The Adventures of Huckleberry Finn",
                   "qty": 2, "line_total": 15.98}],
        "subtotal": 15.98, "shipping": 4.99, "total": 20.97,
    })
    resp = logged_in_client.get("/checkout")
    assert resp.status_code == 200
    assert b"20.97" in resp.data


# ============================================================================
# Admin identity — regular users promoted to admin, super-admin fallback
# ============================================================================
def test_regular_user_login_with_is_admin_grants_admin_session(client, mock_backend):
    mock_backend["post"].return_value = fake_response(200, {
        "user_id": "u1", "name": "pepper", "email": "pepper@gmail.com", "is_admin": True,
    })
    resp = client.post("/api/auth/login", json={"email": "pepper@gmail.com", "password": "readright1"})
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["is_admin"] is True
        assert sess["is_super_admin"] is False
        assert sess["admin_name"] == "pepper"


def test_regular_user_login_without_is_admin_stays_non_admin(client, mock_backend):
    mock_backend["post"].return_value = fake_response(200, {
        "user_id": "u2", "name": "tony", "email": "tony@gmail.com", "is_admin": False,
    })
    client.post("/api/auth/login", json={"email": "tony@gmail.com", "password": "readright1"})
    with client.session_transaction() as sess:
        assert "is_admin" not in sess


def test_super_admin_password_login_sets_actor_label(client, app_module):
    resp = client.post("/admin/login", json={"password": app_module.ADMIN_PASSWORD})
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["is_admin"] is True
        assert sess["is_super_admin"] is True
        assert sess["admin_name"] == "super-admin"


def test_admin_logout_clears_all_admin_session_keys(admin_client):
    with admin_client.session_transaction() as sess:
        sess["is_super_admin"] = True
        sess["admin_name"] = "super-admin"
    resp = admin_client.get("/admin/logout")
    assert resp.status_code == 302
    with admin_client.session_transaction() as sess:
        assert "is_admin" not in sess
        assert "is_super_admin" not in sess
        assert "admin_name" not in sess


def test_disabled_account_login_returns_403_through_proxy(client, mock_backend):
    mock_backend["post"].return_value = fake_response(403, {"error": "This account has been disabled"})
    resp = client.post("/api/auth/login", json={"email": "pepper@gmail.com", "password": "readright1"})
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "This account has been disabled"}


# ============================================================================
# Admin proxies — role, status, cancel/revert order, catalog CRUD, audit log
# ============================================================================
def test_admin_role_proxy_forwards_and_sends_actor_header(admin_client, mock_backend):
    mock_backend["put"].return_value = fake_response(200, {"user_id": "u1", "email": "a@b.com", "is_admin": True})
    resp = admin_client.put("/api/admin/users/u1/role", json={"is_admin": True})
    assert resp.status_code == 200
    assert resp.get_json()["is_admin"] is True
    assert "X-Admin-Actor" in mock_backend["put"].call_args.kwargs["headers"]


def test_admin_role_proxy_requires_admin(client):
    resp = client.put("/api/admin/users/u1/role", json={"is_admin": True})
    assert resp.status_code == 401


def test_admin_status_proxy_forwards(admin_client, mock_backend):
    mock_backend["put"].return_value = fake_response(200, {"user_id": "u1", "email": "a@b.com", "disabled": True})
    resp = admin_client.put("/api/admin/users/u1/status", json={"disabled": True})
    assert resp.status_code == 200
    assert resp.get_json()["disabled"] is True


def test_admin_cancel_order_proxy_forwards(admin_client, mock_backend):
    mock_backend["post"].return_value = fake_response(200, {"order_id": "o1", "cancelled": True})
    resp = admin_client.post("/api/admin/orders/o1/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["cancelled"] is True


def test_admin_revert_order_proxy_forwards(admin_client, mock_backend):
    mock_backend["post"].return_value = fake_response(200, {"order_id": "o1", "status": "Confirmed", "stage_index": 0})
    resp = admin_client.post("/api/admin/orders/o1/revert")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "Confirmed"


def test_admin_create_book_proxy_forwards(admin_client, mock_backend):
    mock_backend["post"].return_value = fake_response(201, {
        "_id": "b1", "title": "The Hobbit", "author": "J.R.R. Tolkien",
        "category": "Fiction", "price": 11.99, "stock": 20,
    })
    resp = admin_client.post("/api/admin/books", json={
        "title": "The Hobbit", "author": "J.R.R. Tolkien",
        "category": "Fiction", "price": 11.99, "stock": 20,
    })
    assert resp.status_code == 201
    assert resp.get_json()["title"] == "The Hobbit"


def test_admin_create_book_proxy_requires_admin(client):
    resp = client.post("/api/admin/books", json={})
    assert resp.status_code == 401


def test_admin_update_book_proxy_forwards(admin_client, mock_backend):
    mock_backend["put"].return_value = fake_response(200, {"_id": "b1", "title": "New Title", "price": 6.99})
    resp = admin_client.put("/api/admin/books/b1", json={"price": 6.99})
    assert resp.status_code == 200
    assert resp.get_json()["price"] == 6.99


def test_admin_delete_book_proxy_forwards(admin_client, mock_backend):
    mock_backend["delete"].return_value = fake_response(200, {"book_id": "b1", "deleted": True})
    resp = admin_client.delete("/api/admin/books/b1")
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] is True


def test_admin_delete_book_proxy_requires_admin(client):
    resp = client.delete("/api/admin/books/b1")
    assert resp.status_code == 401


def test_admin_audit_log_proxy_forwards(admin_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [
        {"actor": "pepper", "action": "stock_update", "detail": "'Great Expectations' -> 75", "at": "Sep 20, 2026 · 16:00 UTC"},
    ])
    resp = admin_client.get("/api/admin/audit-log")
    assert resp.status_code == 200
    assert resp.get_json()[0]["actor"] == "pepper"


def test_admin_audit_log_proxy_requires_admin(client):
    resp = client.get("/api/admin/audit-log")
    assert resp.status_code == 401


def test_orders_page_renders_real_order_history(logged_in_client, mock_backend):
    mock_backend["get"].return_value = fake_response(200, [{
        "order_id": "o1", "short_id": "B7398DC3", "items": [], "subtotal": 15.98,
        "shipping": 4.99, "total": 20.97, "payment": {"method": "upi", "status": "PAID", "txn_id": "TXN-B7398DC3"},
        "status": "Delivered", "stage_index": 3, "stages": ["Confirmed", "Packed", "Shipped", "Delivered"],
        "placed_at": "Sep 20, 2026 · 15:43 UTC",
    }])
    resp = logged_in_client.get("/orders")
    assert resp.status_code == 200
    assert b"B7398DC3" in resp.data
