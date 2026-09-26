from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from functools import wraps
from collections import defaultdict
import os
import sys
import time
import requests

def require_env(name):
    value = os.getenv(name)
    if not value:
        print(f"CRITICAL INITIALIZATION ERROR: {name} environment variable is completely missing!", file=sys.stderr)
        sys.exit(1)
    return value

app = Flask(__name__)
# Session signing key for login cookies — must be a real secret, no insecure default
app.secret_key = require_env("SECRET_KEY")

# Gates the /admin area — a separate password from customer accounts, not a MongoDB user
ADMIN_PASSWORD = require_env("ADMIN_PASSWORD")

# Fallback matches the docker-compose service name; override CATALOG_API_URL for local dev
BACKEND_URL = os.getenv("CATALOG_API_URL", "http://bookstore-backend-service:8000")
CATEGORIES = [
    "Classic Literature",
    "Modern Classics",
    "Fiction",
    "Contemporary Fiction",
    "Poetry",
]

# Every backend call carries this so the backend can reject anything that didn't
# come from the frontend — the backend has no other access control of its own.
backend_session = requests.Session()
backend_session.headers.update({"X-Internal-Token": require_env("INTERNAL_SERVICE_TOKEN")})

# ponytail: in-memory per-process rate limiting on login/register/admin-login —
# resets on restart and doesn't share state across multiple workers/replicas.
# Upgrade to Flask-Limiter + Redis if you scale past one process.
_attempts = defaultdict(list)

def rate_limited(key, limit=5, window=60):
    now = time.time()
    bucket = _attempts[key]
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False

@app.context_processor
def inject_globals():
    # Every template gets the category list and login state without passing them per-route
    return {
        "categories": CATEGORIES,
        "current_user": {
            "id": session.get("user_id"),
            "name": session.get("user_name"),
            "is_admin": bool(session.get("is_admin")),
        },
    }

def backend_get(path):
    """GET a backend API path; returns (json, error_message)."""
    try:
        response = backend_session.get(f"{BACKEND_URL}{path}", timeout=10)
        if response.status_code == 200:
            return response.json(), None
        return None, f"Backend responded with status {response.status_code}"
    except requests.RequestException as e:
        return None, f"Could not reach the catalog service: {e}"

def login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def login_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Login required"}), 401
        return view(*args, **kwargs)
    return wrapped

def admin_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login_page"))
        return view(*args, **kwargs)
    return wrapped

def admin_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "Admin login required"}), 401
        return view(*args, **kwargs)
    return wrapped

def admin_actor_header():
    """Attributes admin actions to whoever is actually in session — the
    password-based super-admin, or a real user account with is_admin=true."""
    name = session.get("admin_name") or ("super-admin" if session.get("is_super_admin") else "admin")
    return {"X-Admin-Actor": name}

# ============================================================================================
# Store Pages
# ============================================================================================
@app.route('/')
@login_required_page
def index():
    books, error = backend_get("/api/books")
    return render_template('index.html', books=books or [], load_error=error)

@app.route('/books/<string:book_id>', methods=['GET'])
@login_required_page
def get_book_page(book_id):
    # Call the backend microservice API instead of querying a database directly
    response = backend_session.get(f"{BACKEND_URL}/api/books/{book_id}")
    if response.status_code == 200:
        book_data = response.json()
        return render_template('book-detail.html', book=book_data)
    return jsonify({"error": "Book page build failed"}), response.status_code

@app.route('/category/<string:category_name>', methods=['GET'])
@login_required_page
def show_category_page(category_name):
    books, error = backend_get(f"/api/books/category/{category_name}")
    return render_template('category.html', category_name=category_name,
                           books=books or [], load_error=error)

@app.route('/login')
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template('login.html', next_url=request.args.get("next", "/"))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route('/cart')
@login_required_page
def cart_page():
    cart, error = backend_get(f"/api/cart/{session['user_id']}")
    return render_template('cart.html', cart=cart, load_error=error)

@app.route('/checkout')
@login_required_page
def checkout_page():
    cart, error = backend_get(f"/api/cart/{session['user_id']}")
    if not error and (not cart or not cart.get("items")):
        return redirect(url_for("cart_page"))
    return render_template('checkout.html', cart=cart, load_error=error)

@app.route('/orders')
@login_required_page
def orders_page():
    orders, error = backend_get(f"/api/orders/{session['user_id']}")
    return render_template('orders.html', orders=orders or [], load_error=error)

# ============================================================================================
# Admin Pages — separate password gate, not a customer account
# ============================================================================================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login_page():
    if request.method == 'POST':
        if rate_limited(f"admin-login:{request.remote_addr}"):
            return jsonify({"error": "Too many attempts, try again in a minute"}), 429
        data = request.get_json(silent=True) or {}
        if data.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            session['is_super_admin'] = True
            session['admin_name'] = 'super-admin'
            return jsonify({"ok": True}), 200
        return jsonify({"error": "Incorrect admin password"}), 401
    if session.get('is_admin'):
        return redirect(url_for('admin_page'))
    return render_template('admin_login.html')

@app.route('/admin')
@admin_required_page
def admin_page():
    users, error = backend_get("/api/admin/users")
    return render_template('admin.html', users=users or [], load_error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('is_super_admin', None)
    session.pop('admin_name', None)
    return redirect(url_for('admin_login_page'))

# ============================================================================================
# Admin Proxy — order-count summary and manual stage advancement
# ============================================================================================
@app.route('/api/admin/users', methods=['GET'])
@admin_required_api
def admin_users_proxy():
    try:
        response = backend_session.get(f"{BACKEND_URL}/api/admin/users", timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/orders/<string:user_id>', methods=['GET'])
@admin_required_api
def admin_user_orders_proxy(user_id):
    try:
        response = backend_session.get(f"{BACKEND_URL}/api/orders/{user_id}", timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/orders/<string:order_id>/advance', methods=['POST'])
@admin_required_api
def admin_advance_order_proxy(order_id):
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/admin/orders/{order_id}/advance",
                                  headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/orders/<string:order_id>/revert', methods=['POST'])
@admin_required_api
def admin_revert_order_proxy(order_id):
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/admin/orders/{order_id}/revert",
                                  headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/orders/<string:order_id>/cancel', methods=['POST'])
@admin_required_api
def admin_cancel_order_proxy(order_id):
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/admin/orders/{order_id}/cancel",
                                  headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/books', methods=['GET'])
@admin_required_api
def admin_books_proxy():
    try:
        response = backend_session.get(f"{BACKEND_URL}/api/books", timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/books', methods=['POST'])
@admin_required_api
def admin_create_book_proxy():
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/admin/books",
                                  json=request.get_json(silent=True) or {},
                                  headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/books/<string:book_id>', methods=['PUT'])
@admin_required_api
def admin_update_book_proxy(book_id):
    try:
        response = backend_session.put(f"{BACKEND_URL}/api/admin/books/{book_id}",
                                 json=request.get_json(silent=True) or {},
                                 headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/books/<string:book_id>', methods=['DELETE'])
@admin_required_api
def admin_delete_book_proxy(book_id):
    try:
        response = backend_session.delete(f"{BACKEND_URL}/api/admin/books/{book_id}",
                                    headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/books/<string:book_id>/stock', methods=['PUT'])
@admin_required_api
def admin_update_stock_proxy(book_id):
    try:
        response = backend_session.put(f"{BACKEND_URL}/api/admin/books/{book_id}/stock",
                                 json=request.get_json(silent=True) or {},
                                 headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/users/<string:user_id>/role', methods=['PUT'])
@admin_required_api
def admin_update_role_proxy(user_id):
    try:
        response = backend_session.put(f"{BACKEND_URL}/api/admin/users/{user_id}/role",
                                 json=request.get_json(silent=True) or {},
                                 headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/users/<string:user_id>/status', methods=['PUT'])
@admin_required_api
def admin_update_user_status_proxy(user_id):
    try:
        response = backend_session.put(f"{BACKEND_URL}/api/admin/users/{user_id}/status",
                                 json=request.get_json(silent=True) or {},
                                 headers=admin_actor_header(), timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

@app.route('/api/admin/audit-log', methods=['GET'])
@admin_required_api
def admin_audit_log_proxy():
    try:
        response = backend_session.get(f"{BACKEND_URL}/api/admin/audit-log", timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Admin service unavailable: {e}"}), 502

# ============================================================================================
# Auth Proxy — backend verifies credentials, frontend owns the session cookie
# ============================================================================================
@app.route('/api/auth/<string:mode>', methods=['POST'])
def auth_proxy(mode):
    if mode not in ("login", "register"):
        return jsonify({"error": "Unknown auth action"}), 404
    if rate_limited(f"{mode}:{request.remote_addr}"):
        return jsonify({"error": "Too many attempts, try again in a minute"}), 429
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/auth/{mode}",
                                 json=request.get_json(silent=True) or {}, timeout=10)
        data = response.json()
        if response.status_code in (200, 201) and "user_id" in data and "name" in data:
            session["user_id"] = data["user_id"]
            session["user_name"] = data["name"]
            if data.get("is_admin"):
                session["is_admin"] = True
                session["is_super_admin"] = False
                session["admin_name"] = data["name"]
        return jsonify(data), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Auth service unavailable: {e}"}), 502

# ============================================================================================
# Cart Proxy — user id always comes from the server-side session, never the browser
# ============================================================================================
@app.route('/api/cart', methods=['GET'])
@login_required_api
def cart_get_proxy():
    try:
        response = backend_session.get(f"{BACKEND_URL}/api/cart/{session['user_id']}", timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Cart service unavailable: {e}"}), 502

@app.route('/api/cart', methods=['POST'])
@login_required_api
def cart_add_proxy():
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/cart/{session['user_id']}",
                                 json=request.get_json(silent=True) or {}, timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Cart service unavailable: {e}"}), 502

@app.route('/api/cart/<string:book_id>', methods=['PUT', 'DELETE'])
@login_required_api
def cart_item_proxy(book_id):
    try:
        url = f"{BACKEND_URL}/api/cart/{session['user_id']}/{book_id}"
        if request.method == 'PUT':
            response = backend_session.put(url, json=request.get_json(silent=True) or {}, timeout=10)
        else:
            response = backend_session.delete(url, timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Cart service unavailable: {e}"}), 502

# ============================================================================================
# Order Proxy — simulated payment settles in the backend, order lands in MongoDB
# ============================================================================================
@app.route('/api/orders', methods=['POST'])
@login_required_api
def order_create_proxy():
    try:
        response = backend_session.post(f"{BACKEND_URL}/api/orders/{session['user_id']}",
                                 json=request.get_json(silent=True) or {}, timeout=15)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        return jsonify({"error": f"Order service unavailable: {e}"}), 502

# ============================================================================================
# Frontend Proxy Route for Gemini AI Summary
# ============================================================================================
@app.route('/api/books/<string:book_id>/summary', methods=['GET'])
def frontend_get_book_summary(book_id):
    try:
        # Route the request across the internal Docker mesh to the backend container
        response = backend_session.get(f"{BACKEND_URL}/api/books/{book_id}/summary")

        # Pass the exact JSON payload and status code back to the browser
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": f"Frontend proxy communication failure: {str(e)}"}), 500

# ============================================================================================
# Legacy JSON passthroughs kept for compatibility with any external callers
# ============================================================================================
@app.route('/books', methods=['GET'])
def list_books_proxy():
    response = backend_session.get(f"{BACKEND_URL}/api/books")
    return jsonify(response.json()), response.status_code

@app.route('/api/books/category/<string:category_name>', methods=['GET'])
def frontend_get_books_by_category(category_name):
    try:
        response = backend_session.get(f"{BACKEND_URL}/api/books/category/{category_name}")
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": f"Frontend category proxy communication failure: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
