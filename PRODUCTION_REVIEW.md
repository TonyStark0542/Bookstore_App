# READ RIGHT — Production Readiness & Security Review

Scope: every file in the repo (`backend/`, `frontend/`, `docker-compose.yml`, both `Dockerfile`s, `Jenkinsfile`, `main.tf`, `deploy_check.sh`, `local_preview_backend.py`), re-checked against the current code. Fixed items have been removed — this file only lists what's still open. Each item is **Problem → Solution**, written from a DevOps angle.

**Severity key:** 🔴 Critical (breaks the app or exposes everything) · 🟠 High (exploitable / will fail under real load) · 🟡 Medium (real weakness, lower blast radius) · ⚪ Low (worth doing, not urgent)

**Fixed since the last pass** (kept out of this list, not re-explained): the backend's MongoDB URI hardcode, the frontend's `BACKEND_URL` hardcode, the backend having no auth of its own (now gated by a shared `INTERNAL_SERVICE_TOKEN`), the guessable default values for `SECRET_KEY`/`ADMIN_PASSWORD`/`INTERNAL_SERVICE_TOKEN` (all now required, no fallback), basic rate limiting on login/register/admin-login, and the frontend Dockerfile/compose port mismatch (both now agree on 5001).

---

## 1. Live bugs — the app will not run correctly as committed

### 🟠 CI/CD reseeds the database unconditionally on every deploy

**Problem**
This is better than it was (the pipeline no longer runs `docker compose down`, so the Mongo container and its data now survive a normal redeploy), but two gaps remain:
- [docker-compose.yml:9-10](docker-compose.yml#L9-L10) still mounts only `./backend/database_backup:/backup` — there is no volume for Mongo's actual data directory (`/data/db`). If the `mongodb-backend` container is ever removed or recreated for any reason (manual `docker compose down`, a config change that forces recreation, moving hosts), all data is lost with nothing to recover from except the original demo seed.
- [Jenkinsfile:62-63](Jenkinsfile#L62-L63) runs `mongorestore` unconditionally on every single pipeline run:
```groovy
sh 'docker compose up -d'
sh 'sleep 5'
sh 'docker exec -i mongodb-backend mongorestore --archive=/backup/db_backup.archive --gzip'
```
Without `--drop`, this won't overwrite documents that already exist, but it re-attempts inserting the same demo book/category records every deploy — wasted work at best, silently confusing behavior at worst (e.g. if the demo catalog is ever intentionally edited or removed, this step quietly puts it back).

**Solution**
1. Give Mongo a named, persistent volume so data survives container recreation, not just container restarts:
```yaml
services:
  mongodb-backend:
    volumes:
      - mongo-data:/data/db
      - ./backend/database_backup:/backup

volumes:
  mongo-data:
```
2. Make the restore conditional — a one-time seed, not an every-deploy step:
```groovy
sh '''
  COUNT=$(docker exec mongodb-backend mongosh bookstore --quiet --eval "db.books.countDocuments()")
  if [ "$COUNT" = "0" ]; then
    docker exec -i mongodb-backend mongorestore --archive=/backup/db_backup.archive --gzip
  fi
'''
```
3. Longer term: move Mongo off the CI-managed compose stack entirely (managed MongoDB Atlas, or a DB the deploy pipeline never touches).

---

## 2. Security

### 🟠 Flask's development server is the production server

**Problem**
Both Dockerfiles run `flask run` / `python3 app.py` — Werkzeug's dev server, which Flask's own docs say not to use in production.

**Solution**
Add `gunicorn` to both `requirements.txt`, and change both Dockerfile CMDs:
```dockerfile
# backend
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
# frontend
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
```
`-w 4` is a starting point — tune worker count to CPU cores (`2 × cores + 1` is the common gunicorn rule of thumb). Note: once there's more than one worker, the in-memory rate limiter's "fixed since last pass" note applies — each worker gets its own counter.

---

### 🔴 No HTTPS/TLS anywhere, and the firewall opens everything to the internet

**Problem**
[main.tf:21-32](main.tf#L21-L32) opens ports 8080 (Jenkins UI) and 5000 (the app) to `0.0.0.0/0` in plain HTTP. Jenkins — capable of running arbitrary shell commands on your infra — is directly reachable by anyone who finds the IP.

**Solution**
1. Put a reverse proxy in front of everything and terminate TLS there (Caddy auto-manages Let's Encrypt certs with near-zero config, nginx if you want more control):
```
your-domain.com {
    reverse_proxy localhost:5000
}
```
2. In `main.tf`, split the firewall rule so Jenkins is never public:
```hcl
resource "google_compute_firewall" "allow_app_traffic" {
  name          = "allow-bookstore-app"
  allow { protocol = "tcp" ports = ["443"] }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["jenkins-master-node"]
}

resource "google_compute_firewall" "allow_jenkins_admin" {
  name          = "allow-jenkins-ui"
  allow { protocol = "tcp" ports = ["8080"] }
  source_ranges = ["<your-office-or-VPN-CIDR>"]   # never 0.0.0.0/0
  target_tags   = ["jenkins-master-node"]
}
```
3. Lock down SSH (port 22) to the same trusted CIDR — GCP's `default` network's built-in SSH rule allows it from anywhere otherwise.

---

### 🟠 MongoDB runs with zero authentication, on an end-of-life version

**Problem**
[docker-compose.yml:6](docker-compose.yml#L6): `mongo:4.4.18` — EOL since Feb 2024, no security patches. No `--auth`, no credentials anywhere. Any process that can reach the container on the Docker network can read and write the entire database.

**Solution**
```yaml
mongodb-backend:
  image: mongo:7.0
  environment:
    - MONGO_INITDB_ROOT_USERNAME=${MONGO_ROOT_USER}
    - MONGO_INITDB_ROOT_PASSWORD=${MONGO_ROOT_PASSWORD}
  command: ["mongod", "--auth"]
```
Then give the app its own scoped user (not root) and update `MONGO_URI` to include credentials:
```
mongodb://app_user:${MONGO_APP_PASSWORD}@mongodb-backend:27017/bookstore?authSource=admin
```
Test the upgrade against a copy of `db_backup.archive` before touching production data.

---

### 🟠 Jenkins pipeline can leak the Gemini API key to disk in plaintext

**Problem**
[Jenkinsfile:38-46](Jenkinsfile#L38-L46) interpolates a `credentials()` binding directly into a Groovy string, bypassing Jenkins's secret masking, and writes the raw key to a plaintext `.env` file mid-pipeline:
```groovy
sh """
    echo "GEMINI_API_KEY=${GEMINI_KEY_SECRET}" > .env
"""
```

**Solution**
Use `withEnv` consistently (Stage 1 already does this correctly) and never `echo` a secret into a file via string interpolation:
```groovy
withEnv(["GEMINI_API_KEY=${GEMINI_KEY_SECRET}"]) {
    sh 'docker compose up -d'   // compose reads GEMINI_API_KEY straight from the environment
}
```
Since `docker-compose.yml` already declares `- GEMINI_API_KEY` under `environment:` with no default, Compose pulls it from the shell's environment automatically — the `.env` file write in Stage 3 isn't even necessary. Delete that stage.

---

### 🟡 Internal error messages are returned straight to the client

**Problem**
Routes across [app_backend.py](backend/app_backend.py) do `return jsonify({"error": str(e)}), 500`, leaking raw exception text to any caller.

**Solution**
```python
import logging
logging.basicConfig(level=logging.INFO)

except Exception as e:
    app.logger.exception("get_all_books failed")
    return jsonify({"error": "Something went wrong"}), 500
```
Ship container logs to somewhere centralized (see monitoring fix below) so the real detail is still visible to you, just not to the client.

---

### 🟡 No CSRF protection, no security headers

**Problem**
No CSRF tokens on any state-changing endpoint; no CSP, `X-Frame-Options`, `X-Content-Type-Options`, or HSTS header anywhere.

**Solution**
One dependency covers both, no custom header code needed:
```python
from flask_talisman import Talisman
Talisman(app, content_security_policy={'default-src': "'self'"})
```
For CSRF, Flask-WTF's `CSRFProtect(app)` plus including the token in the fetch() calls in `catalog.js`/`checkout.html`/`admin.js` as a header (`X-CSRFToken`).

---

### 🟡 Single shared admin password, no audit trail

**Problem**
One password for anyone who knows it — no way to know which admin advanced which order, no way to revoke one admin without changing it for everyone.

**Solution**
Reuse the existing `users` collection instead of a separate password: add an `is_admin: bool` field, check it at login, and log `session['user_id']` alongside every admin action:
```python
app.logger.info(f"admin={session['user_id']} advanced order={order_id}")
```
Bigger lift than the other items here — reasonable to defer until more than one person needs admin access.

---

### ⚪ User enumeration on registration

**Problem**
`/api/auth/register` returns a distinct 409 confirming an email is already registered ([app_backend.py:119](backend/app_backend.py#L119)).

**Solution**
Return the same generic message either way and let the real "already registered" case surface via a "forgot password" flow instead of a signup error:
```python
return jsonify({"message": "If this email can be registered, check your inbox."}), 202
```
Low priority — only worth doing once the rest of the list is handled.

---

## 3. Reliability & scaling gaps

### 🟡 No transactional integrity around order placement

**Problem**
`create_order()` ([app_backend.py:246-301](backend/app_backend.py#L246-L301)) does three separate writes (insert order, decrement stock, clear cart) with nothing atomic tying them together. A crash mid-sequence leaves inconsistent data — and MongoDB transactions require a replica set, which this deployment doesn't run.

**Solution**
Two-part fix:
1. **Deployment:** run Mongo as a single-node replica set (`mongod --replSet rs0`, then `rs.initiate()` once) — this unlocks transactions with almost no extra ops burden.
2. **Code:** wrap the three writes in a session transaction:
```python
with client.start_session() as s:
    with s.start_transaction():
        orders_col.insert_one(order_doc, session=s)
        for item in cart['items']:
            db['books'].update_one({...}, {...}, session=s)
        carts_col.update_one({...}, {...}, session=s)
```

---

### 🟡 Orders can be placed with insufficient stock, silently

**Problem**
The stock decrement's conditional filter ([app_backend.py:286-289](backend/app_backend.py#L286-L289)) silently no-ops if stock is too low — but `create_order` never checks first, so the order still succeeds.

**Solution**
Check before charging, reject if any item is short:
```python
for item in cart['items']:
    book = db['books'].find_one({'_id': ObjectId(item['book_id'])})
    if book['stock'] < item['qty']:
        return jsonify({"error": f"Only {book['stock']} left of {book['title']}"}), 409
```
Put this check inside the same transaction from the fix above so a race between two simultaneous buyers can't both pass the check and both oversell.

---

### 🟡 `depends_on` doesn't wait for Mongo to actually be ready

**Problem**
[docker-compose.yml:26-27](docker-compose.yml#L26-L27) uses `condition: service_started`, which only waits for the process to start, not for Mongo to accept connections.

**Solution**
```yaml
mongodb-backend:
  healthcheck:
    test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
    interval: 5s
    timeout: 3s
    retries: 5

bookstore-backend:
  depends_on:
    mongodb-backend:
      condition: service_healthy
```

---

### 🟡 No resource limits, no horizontal scaling, single instance of everything

**Problem**
No CPU/memory caps, no replicas, no load balancer — one crash takes the whole service down, one runaway process can starve the host.

**Solution**
Compose-level caps as a floor:
```yaml
deploy:
  resources:
    limits: { cpus: "1.0", memory: 512M }
```
For real horizontal scaling, this is the strongest argument in the whole report for moving to **Kubernetes** (Deployment with `replicas: 3`, a `Service` for load balancing, an `HorizontalPodAutoscaler`) instead of docker-compose, which has no native multi-replica or auto-scaling story.

---

### 🟡 Containers run as root

**Problem**
Neither Dockerfile declares a non-root `USER` — both processes run as root inside their containers.

**Solution**
```dockerfile
RUN useradd -m appuser
USER appuser
```
Add this after the `COPY` steps, before `CMD`, in both Dockerfiles.

---

### ⚪ No monitoring, logging, or alerting stack

**Problem**
No centralized logs, no uptime checks, no error tracking, no metrics.

**Solution**
Smallest useful stack, in order of effort:
1. `docker compose logs -f` → ship to a log drain (even just `docker-compose.yml` `logging:` driver to a file, or Grafana Loki if you want search).
2. Add a `/health` endpoint to both Flask apps; point an uptime checker (UptimeRobot, GCP Cloud Monitoring) at it.
3. `sentry-sdk` in both apps for error tracking — a few lines of init code, immediate value.

---

### ⚪ No automated tests in CI

**Problem**
Jenkins builds and deploys but never runs a test suite — there isn't one in the repo.

**Solution**
Add a `Test` stage before `Parallel Docker Build` in the Jenkinsfile once a `tests/` directory with `pytest` exists:
```groovy
stage('Test') {
    steps { sh 'pip install -r backend/requirements.txt pytest && pytest' }
}
```
Start with the smoke tests already used to validate this app during development (register → cart → checkout → admin advance) — that's most of the coverage that matters here.

---

## 4. Performance & cost concerns

### 🟡 Cover images are base64-encoded and embedded in every API response

**Problem**
`/api/books`, `/api/cart/<id>`, `/api/orders/<id>` all inline full base64 image data — ~33% size overhead versus binary, and no browser/CDN caching since it's never a stable URL.

**Solution**
Store images in object storage (S3, GCS, or even just Nginx serving static files) and return a URL instead of bytes:
```python
book['cover_url'] = f"https://cdn.example.com/covers/{book['_id']}.jpg"
```
Migrate the existing base64 blobs out of Mongo with a one-off script, decoding and re-uploading each.

---

### 🟡 No pagination anywhere

**Problem**
`/api/books` always returns the entire catalog. Fine at 12 books, breaks at scale.

**Solution**
```python
page = int(request.args.get('page', 1))
per_page = 24
books = list(db['books'].find().skip((page-1)*per_page).limit(per_page))
```
Update the frontend's book grid to request pages / add infinite scroll once the catalog actually grows — no need to build this before there's more than a dozen books.

---

### ⚪ Missing indexes

**Problem**
Only `users.email` is indexed. `carts.user_id` and `orders.user_id` get a full collection scan on every request.

**Solution**
```python
carts_col.create_index('user_id')
orders_col.create_index('user_id')
```
Add next to the existing `users_col.create_index('email', unique=True)` call at startup.

---

## 5. Payment flow — explicitly out of scope for real money

**Problem**
This is a **simulated** gateway by design. `create_order` ([app_backend.py:246](backend/app_backend.py#L246)) accepts whatever `payment.method`/`payment.detail` the client sends and always marks `card`/`upi` as `PAID` with no real charge happening.

**Solution**
Fine for a demo, but before a single real transaction: integrate Stripe or Razorpay server-to-server (their SDK creates a PaymentIntent/Order on your backend, the client only ever sees a client-side token, and you verify the charge via their webhook before marking an order `PAID`). Raw card numbers should never reach your own server code — that's the whole point of using a PCI-compliant provider instead of a custom form.

---

## Suggested order of fixes

1. **CI/CD data durability** — add the persistent Mongo volume and guard the `mongorestore` step.
2. **Network** — HTTPS everywhere, Jenkins/SSH restricted to trusted IPs, Mongo auth, fix the Jenkins `.env` leak.
3. **Runtime hardening** — gunicorn instead of the dev server, security headers, CSRF protection.
4. **Order correctness** — stock check before charge, transactions.
5. Everything else (pagination, image hosting, monitoring, tests, Kubernetes/horizontal scaling, admin audit trail) as the product actually grows.
