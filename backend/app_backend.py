from flask import Flask, jsonify, request
from pymongo import MongoClient
from bson.objectid import ObjectId
from bson.errors import InvalidId
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from google import genai
import os
import sys

app = Flask(__name__)

# Fallback matches the docker-compose service name; override MONGO_URI for local dev
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb-backend:27017/bookstore")
client = MongoClient(MONGO_URI)
db = client['bookstore']

users_col = db['users']
carts_col = db['carts']
orders_col = db['orders']

# One account per email address
try:
    users_col.create_index('email', unique=True)
except Exception as e:
    print(f"WARNING: could not create unique email index: {e}", file=sys.stderr)

# CRITICAL INITIALIZATION ERROR GATEWAY (Project 2 Requirement)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("CRITICAL INITIALIZATION ERROR: GEMINI_API_KEY environment variable is completely missing!", file=sys.stderr)
    sys.exit(1) # Drop exit status code 1 for the CI/CD pipeline gate

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# This backend has no login system of its own — every route below trusts whoever
# calls it. The only gate is this shared token, which only the frontend knows.
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN")
if not INTERNAL_SERVICE_TOKEN:
    print("CRITICAL INITIALIZATION ERROR: INTERNAL_SERVICE_TOKEN environment variable is completely missing!", file=sys.stderr)
    sys.exit(1)

@app.before_request
def check_internal_token():
    if request.headers.get("X-Internal-Token") != INTERNAL_SERVICE_TOKEN:
        return jsonify({"error": "Forbidden"}), 403

FREE_SHIPPING_THRESHOLD = 25.00
SHIPPING_FEE = 4.99

def log_admin_action(action, detail):
    """Audit trail — the frontend attaches who's acting via X-Admin-Actor,
    since this backend has no session/login system of its own."""
    db['audit_log'].insert_one({
        'actor': request.headers.get('X-Admin-Actor', 'unknown'),
        'action': action,
        'detail': detail,
        'created_at': datetime.now(timezone.utc),
    })

# ============================================================================================
# Book Catalog Endpoints
# ============================================================================================
@app.route('/api/books', methods=['GET'])
def get_all_books():
    try:
        books = list(db['books'].find())
        for book in books:
            book['_id'] = str(book['_id'])
        return jsonify(books), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/books/<string:book_id>', methods=['GET'])
def get_single_book(book_id):
    try:
        book = db['books'].find_one({'_id': ObjectId(book_id)})
        if book:
            book['_id'] = str(book['_id'])
            return jsonify(book), 200
        return jsonify({"error": "Book not found"}), 404
    except InvalidId:
        return jsonify({"error": "Invalid book id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/books/<string:book_id>/summary', methods=['GET'])
def get_book_summary(book_id):
    try:
        book = db['books'].find_one({'_id': ObjectId(book_id)})
        if not book:
            return jsonify({"error": "Target book record missing"}), 404

        prompt = f"Summarize {book.get('title')} by {book.get('author')} concisely."
        response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)

        return jsonify({"book_id": book_id, "ai_summary": response.text}), 200
    except InvalidId:
        return jsonify({"error": "Invalid book id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/books/category/<string:category_name>', methods=['GET'])
def get_books_by_category(category_name):
    try:
        books_collection = db['books']
        # Query MongoDB for documents matching the exact category parameter string
        books_cursor = books_collection.find({"category": category_name})
        books_list = []
        for book in books_cursor:
            book['_id'] = str(book['_id'])
            books_list.append(book)
        return jsonify(books_list), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================================
# Authentication Endpoints — users stored in MongoDB with salted password hashes
# ============================================================================================
@app.route('/api/auth/register', methods=['POST'])
def register_user():
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''

        if not name or not email or '@' not in email:
            return jsonify({"error": "A valid name and email address are required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters long"}), 400
        if users_col.find_one({'email': email}):
            return jsonify({"error": "An account with this email already exists"}), 409

        result = users_col.insert_one({
            'name': name,
            'email': email,
            'password_hash': generate_password_hash(password),
            'created_at': datetime.now(timezone.utc),
        })
        return jsonify({"user_id": str(result.inserted_id), "name": name, "email": email}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''

        user = users_col.find_one({'email': email})
        if not user or not check_password_hash(user.get('password_hash', ''), password):
            return jsonify({"error": "Invalid email or password"}), 401
        if user.get('disabled'):
            return jsonify({"error": "This account has been disabled"}), 403

        return jsonify({
            "user_id": str(user['_id']), "name": user['name'], "email": user['email'],
            "is_admin": bool(user.get('is_admin', False)),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================================
# Cart Endpoints — one cart document per user, items joined against the live catalog
# ============================================================================================
def build_cart_payload(user_id):
    cart = carts_col.find_one({'user_id': user_id}) or {}
    items, subtotal = [], 0.0
    for entry in cart.get('items', []):
        try:
            book = db['books'].find_one({'_id': ObjectId(entry['book_id'])})
        except InvalidId:
            book = None
        if not book:
            continue
        price = float(book.get('price', 0))
        qty = int(entry.get('qty', 1))
        items.append({
            'book_id': str(book['_id']),
            'title': book.get('title', 'Unknown title'),
            'author': book.get('author', 'Unknown author'),
            'price': round(price, 2),
            'qty': qty,
            'stock': book.get('stock', 0),
            'cover_image': book.get('cover_image', ''),
            'line_total': round(price * qty, 2),
        })
        subtotal += price * qty
    shipping = 0.0 if (subtotal >= FREE_SHIPPING_THRESHOLD or subtotal == 0) else SHIPPING_FEE
    return {
        'items': items,
        'count': sum(i['qty'] for i in items),
        'subtotal': round(subtotal, 2),
        'shipping': round(shipping, 2),
        'total': round(subtotal + shipping, 2),
    }

@app.route('/api/cart/<string:user_id>', methods=['GET'])
def get_cart(user_id):
    try:
        return jsonify(build_cart_payload(user_id)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cart/<string:user_id>', methods=['POST'])
def add_to_cart(user_id):
    try:
        data = request.get_json(silent=True) or {}
        book_id = data.get('book_id')
        qty = max(1, int(data.get('qty', 1)))
        book = db['books'].find_one({'_id': ObjectId(book_id)})
        if not book:
            return jsonify({"error": "Book not found"}), 404

        # Increment quantity if the book is already in the cart, otherwise append it
        updated = carts_col.update_one(
            {'user_id': user_id, 'items.book_id': book_id},
            {'$inc': {'items.$.qty': qty}}
        )
        if updated.matched_count == 0:
            carts_col.update_one(
                {'user_id': user_id},
                {'$push': {'items': {'book_id': book_id, 'qty': qty}}},
                upsert=True
            )
        return jsonify(build_cart_payload(user_id)), 200
    except (InvalidId, TypeError, ValueError):
        return jsonify({"error": "Invalid book id or quantity"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cart/<string:user_id>/<string:book_id>', methods=['PUT'])
def update_cart_item(user_id, book_id):
    try:
        data = request.get_json(silent=True) or {}
        qty = int(data.get('qty', 1))
        if qty <= 0:
            carts_col.update_one({'user_id': user_id}, {'$pull': {'items': {'book_id': book_id}}})
        else:
            carts_col.update_one(
                {'user_id': user_id, 'items.book_id': book_id},
                {'$set': {'items.$.qty': qty}}
            )
        return jsonify(build_cart_payload(user_id)), 200
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid quantity"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cart/<string:user_id>/<string:book_id>', methods=['DELETE'])
def remove_cart_item(user_id, book_id):
    try:
        carts_col.update_one({'user_id': user_id}, {'$pull': {'items': {'book_id': book_id}}})
        return jsonify(build_cart_payload(user_id)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================================
# Order Endpoints — simulated payment capture, real order records in MongoDB
# ============================================================================================
ORDER_STAGES = ['Confirmed', 'Packed', 'Shipped', 'Delivered']

@app.route('/api/orders/<string:user_id>', methods=['POST'])
def create_order(user_id):
    try:
        data = request.get_json(silent=True) or {}
        payment = data.get('payment') or {}
        method = payment.get('method')
        if method not in ('card', 'upi', 'cod'):
            return jsonify({"error": "Unsupported payment method"}), 400

        cart = build_cart_payload(user_id)
        if not cart['items']:
            return jsonify({"error": "Your cart is empty"}), 400

        for item in cart['items']:
            book = db['books'].find_one({'_id': ObjectId(item['book_id'])})
            available = book.get('stock', 0) if book else 0
            if available < item['qty']:
                return jsonify({"error": f"Only {available} left of {item['title']}"}), 409

        created_at = datetime.now(timezone.utc)
        order_doc = {
            'user_id': user_id,
            'items': [
                {'book_id': i['book_id'], 'title': i['title'], 'author': i['author'],
                 'price': i['price'], 'qty': i['qty'], 'line_total': i['line_total']}
                for i in cart['items']
            ],
            'subtotal': cart['subtotal'],
            'shipping': cart['shipping'],
            'total': cart['total'],
            'payment': {
                'method': method,
                # Only a masked descriptor is ever stored — this is a simulated gateway
                'detail': str(payment.get('detail', ''))[:64],
                'status': 'PENDING' if method == 'cod' else 'PAID',
            },
            'status': ORDER_STAGES[0],
            'stage_index': 0,
            'cancelled': False,
            'created_at': created_at,
        }
        result = orders_col.insert_one(order_doc)
        order_doc['payment']['txn_id'] = f"TXN-{str(result.inserted_id)[-10:].upper()}"
        orders_col.update_one({'_id': result.inserted_id}, {'$set': {'payment.txn_id': order_doc['payment']['txn_id']}})

        # Decrement stock for each purchased title (never below zero)
        for item in cart['items']:
            db['books'].update_one(
                {'_id': ObjectId(item['book_id']), 'stock': {'$gte': item['qty']}},
                {'$inc': {'stock': -item['qty']}}
            )

        # Purchase complete — empty the cart
        carts_col.update_one({'user_id': user_id}, {'$set': {'items': []}})

        return jsonify({
            'order_id': str(result.inserted_id),
            'txn_id': order_doc['payment']['txn_id'],
            'total': cart['total'],
            'payment_status': order_doc['payment']['status'],
        }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/orders/<string:user_id>', methods=['GET'])
def get_user_orders(user_id):
    try:
        orders = []
        for doc in orders_col.find({'user_id': user_id}).sort('created_at', -1):
            created_at = doc['created_at']
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            stage_index = doc.get('stage_index', 0)
            orders.append({
                'order_id': str(doc['_id']),
                'short_id': str(doc['_id'])[-8:].upper(),
                'items': doc.get('items', []),
                'subtotal': doc.get('subtotal', 0),
                'shipping': doc.get('shipping', 0),
                'total': doc.get('total', 0),
                'payment': doc.get('payment', {}),
                'status': 'Cancelled' if doc.get('cancelled') else ORDER_STAGES[stage_index],
                'stage_index': stage_index,
                'cancelled': bool(doc.get('cancelled', False)),
                'stages': ORDER_STAGES,
                'placed_at': created_at.strftime('%b %d, %Y · %H:%M UTC'),
            })
        return jsonify(orders), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================================
# Admin Endpoints — per-user order counts and manual stage advancement
# ============================================================================================
@app.route('/api/admin/users', methods=['GET'])
def admin_list_users():
    try:
        users = []
        for u in users_col.find().sort('created_at', -1):
            user_id = str(u['_id'])
            user_orders = list(orders_col.find({'user_id': user_id}))
            users.append({
                'user_id': user_id,
                'name': u['name'],
                'email': u['email'],
                'order_count': len(user_orders),
                'total_spent': round(sum(o.get('total', 0) for o in user_orders), 2),
                'is_admin': bool(u.get('is_admin', False)),
                'disabled': bool(u.get('disabled', False)),
            })
        return jsonify(users), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/orders/<string:order_id>/advance', methods=['POST'])
def admin_advance_order(order_id):
    try:
        order = orders_col.find_one({'_id': ObjectId(order_id)})
        if not order:
            return jsonify({"error": "Order not found"}), 404
        if order.get('cancelled'):
            return jsonify({"error": "Order is cancelled"}), 400
        stage_index = order.get('stage_index', 0)
        if stage_index >= len(ORDER_STAGES) - 1:
            return jsonify({"error": "Order is already Delivered"}), 400
        stage_index += 1
        orders_col.update_one(
            {'_id': order['_id']},
            {'$set': {'stage_index': stage_index, 'status': ORDER_STAGES[stage_index]}}
        )
        log_admin_action('order_advance', f"order {order_id} -> {ORDER_STAGES[stage_index]}")
        return jsonify({"order_id": order_id, "status": ORDER_STAGES[stage_index], "stage_index": stage_index}), 200
    except InvalidId:
        return jsonify({"error": "Invalid order id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/orders/<string:order_id>/revert', methods=['POST'])
def admin_revert_order(order_id):
    try:
        order = orders_col.find_one({'_id': ObjectId(order_id)})
        if not order:
            return jsonify({"error": "Order not found"}), 404
        if order.get('cancelled'):
            return jsonify({"error": "Cannot revert a cancelled order"}), 400
        stage_index = order.get('stage_index', 0)
        if stage_index <= 0:
            return jsonify({"error": "Order is already at its first stage"}), 400
        stage_index -= 1
        orders_col.update_one(
            {'_id': order['_id']},
            {'$set': {'stage_index': stage_index, 'status': ORDER_STAGES[stage_index]}}
        )
        log_admin_action('order_revert', f"order {order_id} -> {ORDER_STAGES[stage_index]}")
        return jsonify({"order_id": order_id, "status": ORDER_STAGES[stage_index], "stage_index": stage_index}), 200
    except InvalidId:
        return jsonify({"error": "Invalid order id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/orders/<string:order_id>/cancel', methods=['POST'])
def admin_cancel_order(order_id):
    try:
        order = orders_col.find_one({'_id': ObjectId(order_id)})
        if not order:
            return jsonify({"error": "Order not found"}), 404
        if order.get('cancelled'):
            return jsonify({"error": "Order is already cancelled"}), 400
        if order.get('stage_index', 0) >= len(ORDER_STAGES) - 1:
            return jsonify({"error": "Cannot cancel a delivered order"}), 400
        orders_col.update_one({'_id': order['_id']}, {'$set': {'cancelled': True}})
        log_admin_action('order_cancel', f"cancelled order {order_id}")
        return jsonify({"order_id": order_id, "cancelled": True}), 200
    except InvalidId:
        return jsonify({"error": "Invalid order id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/books/<string:book_id>/stock', methods=['PUT'])
def admin_update_stock(book_id):
    try:
        data = request.get_json(silent=True) or {}
        new_stock = int(data.get('stock'))
        if new_stock < 0:
            return jsonify({"error": "Stock cannot be negative"}), 400
        result = db['books'].update_one({'_id': ObjectId(book_id)}, {'$set': {'stock': new_stock}})
        if result.matched_count == 0:
            return jsonify({"error": "Book not found"}), 404
        book = db['books'].find_one({'_id': ObjectId(book_id)})
        log_admin_action('stock_update', f"'{book.get('title')}' -> {new_stock}")
        return jsonify({"book_id": book_id, "title": book.get('title'), "stock": new_stock}), 200
    except (InvalidId, TypeError, ValueError):
        return jsonify({"error": "Invalid book id or stock value"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/books', methods=['POST'])
def admin_create_book():
    try:
        data = request.get_json(silent=True) or {}
        title = (data.get('title') or '').strip()
        author = (data.get('author') or '').strip()
        category = (data.get('category') or '').strip()
        price = float(data.get('price'))
        stock = int(data.get('stock', 0))
        if not title or not author or not category:
            return jsonify({"error": "Title, author, and category are required"}), 400
        if price < 0 or stock < 0:
            return jsonify({"error": "Price and stock cannot be negative"}), 400
        book = {'title': title, 'author': author, 'category': category,
                'price': price, 'stock': stock, 'cover_image': ''}
        result = db['books'].insert_one(book)
        book['_id'] = str(result.inserted_id)
        log_admin_action('book_create', f"added '{title}'")
        return jsonify(book), 201
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid price or stock value"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/books/<string:book_id>', methods=['PUT'])
def admin_update_book(book_id):
    try:
        data = request.get_json(silent=True) or {}
        updates = {}
        for field in ('title', 'author', 'category'):
            if data.get(field):
                updates[field] = str(data[field]).strip()
        if 'price' in data:
            price = float(data['price'])
            if price < 0:
                return jsonify({"error": "Price cannot be negative"}), 400
            updates['price'] = price
        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400
        result = db['books'].update_one({'_id': ObjectId(book_id)}, {'$set': updates})
        if result.matched_count == 0:
            return jsonify({"error": "Book not found"}), 404
        book = db['books'].find_one({'_id': ObjectId(book_id)})
        book['_id'] = str(book['_id'])
        log_admin_action('book_update', f"edited '{book['title']}'")
        return jsonify(book), 200
    except (InvalidId, TypeError, ValueError):
        return jsonify({"error": "Invalid book id or field value"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/books/<string:book_id>', methods=['DELETE'])
def admin_delete_book(book_id):
    try:
        book = db['books'].find_one({'_id': ObjectId(book_id)})
        if not book:
            return jsonify({"error": "Book not found"}), 404
        db['books'].delete_one({'_id': ObjectId(book_id)})
        log_admin_action('book_delete', f"deleted '{book['title']}'")
        return jsonify({"book_id": book_id, "deleted": True}), 200
    except InvalidId:
        return jsonify({"error": "Invalid book id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/users/<string:user_id>/role', methods=['PUT'])
def admin_update_role(user_id):
    try:
        data = request.get_json(silent=True) or {}
        is_admin = bool(data.get('is_admin'))
        result = users_col.update_one({'_id': ObjectId(user_id)}, {'$set': {'is_admin': is_admin}})
        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404
        user = users_col.find_one({'_id': ObjectId(user_id)})
        log_admin_action('role_change', f"{'granted' if is_admin else 'revoked'} admin for {user.get('email')}")
        return jsonify({"user_id": user_id, "email": user.get('email'), "is_admin": is_admin}), 200
    except InvalidId:
        return jsonify({"error": "Invalid user id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/users/<string:user_id>/status', methods=['PUT'])
def admin_update_user_status(user_id):
    try:
        data = request.get_json(silent=True) or {}
        disabled = bool(data.get('disabled'))
        result = users_col.update_one({'_id': ObjectId(user_id)}, {'$set': {'disabled': disabled}})
        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404
        user = users_col.find_one({'_id': ObjectId(user_id)})
        log_admin_action('user_status', f"{'disabled' if disabled else 're-enabled'} {user.get('email')}")
        return jsonify({"user_id": user_id, "email": user.get('email'), "disabled": disabled}), 200
    except InvalidId:
        return jsonify({"error": "Invalid user id"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/audit-log', methods=['GET'])
def admin_get_audit_log():
    try:
        entries = []
        for doc in db['audit_log'].find().sort('created_at', -1).limit(50):
            entries.append({
                'actor': doc.get('actor', 'unknown'),
                'action': doc.get('action'),
                'detail': doc.get('detail'),
                'at': doc['created_at'].strftime('%b %d, %Y · %H:%M UTC'),
            })
        return jsonify(entries), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
