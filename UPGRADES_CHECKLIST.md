# READ RIGHT — Upgrade Checklist

Every open item across the app, grouped by area. `[x]` = already fixed in this
codebase. `[ ]` = still open. Each item names the exact file/line so it's easy
to jump to; several are inherited from `PRODUCTION_REVIEW.md` and repeated
here so this is the single list to work off of.

---

## Backend — Book Catalog

- [x] **Fixed** — `get_single_book` and `get_book_summary` now catch `InvalidId`
      and return `400` for a malformed book ID, matching every other
      ID-taking route. (`backend/app_backend.py`)
- [ ] No pagination on `/api/books` — fine at a dozen books, breaks at scale.
- [ ] Cover images are base64 blobs inline in every response — move to object
      storage / a static URL once the catalog grows past demo size.
- [ ] Internal error messages (`str(e)`) are returned straight to the client on
      every route's `except Exception` — leaks stack detail; log it, return a
      generic message instead.

## Backend — Auth

- [ ] `/api/auth/register` confirms via a `409` that an email is already taken
      (user enumeration). Low priority.
- [ ] No password reset / forgot-password flow.
- [ ] Passwords require only 6 characters, no complexity rule — acceptable for
      a demo, revisit before real users.

## Backend — Cart & Orders

- [x] **Oversell bug fixed** — `create_order` now checks each item's quantity
      against current stock *before* charging/creating the order, and rejects
      with `409` if insufficient. This was the root cause of the Huckleberry
      Finn issue (46 ordered against 45 in stock silently "succeeded" with
      stock left untouched). (`backend/app_backend.py`, `create_order`)
- [ ] **Race condition still open**: two buyers checking out the last copy at
      the same instant could both pass the stock check above. Full fix needs
      a MongoDB transaction, which needs Mongo running as a replica set
      (`mongod --replSet rs0` + `rs.initiate()`).
- [ ] No transactional integrity across the three order-placement writes
      (insert order, decrement stock, clear cart) — a crash mid-sequence
      leaves inconsistent data. Same replica-set prerequisite as above.
- [x] **Order cancel + reverse added** — see Admin upgrades below; also closes
      the "no refund path" gap for a simulated gateway (cancelling is the
      closest equivalent to a refund when no real money moves).

## Admin — current capabilities

What admin can do today, confirmed by reading the code:
1. Log in as **super-admin** via the shared `ADMIN_PASSWORD` (fallback,
   unchanged), or as a **named admin** via a normal user account that has
   `is_admin: true` — every admin action is attributed to whichever identity
   is actually logged in.
2. View every registered user with order count, total spent, admin badge,
   and disabled badge.
3. **Grant or revoke admin rights** on any user account.
4. **Disable or re-enable** a user account (a disabled account can't log in).
5. Expand a user to see full order history (items, payment, fulfillment stage).
6. Advance an order one stage: `Confirmed → Packed → Shipped → Delivered`.
7. **Move an order back one stage** (undo a fat-fingered advance).
8. **Cancel an order** (blocked once it's already `Delivered`).
9. **Full catalog CRUD** — add a new book, edit title/author/category/price,
   delete a book, and set stock directly (with a low-stock pill under 5).
10. **Bulk stock update via CSV** (`title,stock` per line).
11. Search/filter the users list and the books list by typed text.
12. View a running **activity log** of every admin action, who did it, and when.
13. Log out.

## Admin — upgrades (all implemented)

- [x] **Per-admin identity + audit trail** — `ADMIN_PASSWORD` stays as a
      super-admin fallback; any user account can be flagged `is_admin: true`
      by an existing admin (self-service via "Make admin" in the panel — no
      bootstrap script needed since the super-admin password already exists).
      Every admin-changing action carries an `X-Admin-Actor` header from the
      frontend session and is written to a new `audit_log` collection, shown
      on `/admin` as "Activity log". (`backend/app_backend.py:log_admin_action`,
      `admin_get_audit_log`, `admin_update_role`; `frontend/app_frontend.py:
      admin_actor_header`)
- [x] **Catalog CRUD** — `POST/PUT/DELETE /api/admin/books[/<id>]` plus an
      "Add book" form and inline edit/delete on every row in the admin panel.
- [x] **Order cancel + reverse** — `POST /api/admin/orders/<id>/cancel` and
      `/revert`; a cancelled order can't be advanced/reverted, and a
      delivered order can't be cancelled.
- [x] **Low-stock alerting** — a "Low stock" pill appears on any book under 5
      in the Catalog & stock list (client-side, same threshold the storefront
      already uses).
- [x] **Bulk stock import via CSV** — a file input on `/admin` parses
      `title,stock` lines and reuses the existing per-book stock endpoint for
      each match (no new backend endpoint needed).
- [x] **User management** — disable/enable via `PUT
      /api/admin/users/<id>/status`; a disabled account gets `403` on login.
- [x] **Search/filter** — plain client-side text filters over the users and
      books lists (real backend pagination deliberately skipped — current
      catalog/user counts don't need it yet; revisit if that changes).

## Frontend — Security

- [ ] No CSRF protection on any state-changing endpoint.
- [ ] No security headers (CSP, `X-Frame-Options`, `X-Content-Type-Options`,
      HSTS). One dependency (`flask-talisman`) covers all of them.
- [x] **Fixed** — `auth_proxy` now only sets the session when the backend's
      reply actually contains `user_id` and `name`; a malformed 200/201 no
      longer crashes with a `KeyError`. (`frontend/app_frontend.py`)
- [ ] Rate limiting is in-process/in-memory (`_attempts` dict) — resets on
      restart, doesn't share state once gunicorn runs multiple workers.
      Upgrade to Flask-Limiter + Redis when scaling past one process.

## Infra / Docker / CI

- [x] **Mongo data persistence** — named `mongo-data:/data/db` volume added,
      so container recreation no longer wipes the database.
- [x] **Auto-seeding on first run** — `backend/init-restore.sh` mounted into
      `/docker-entrypoint-initdb.d/` restores the demo catalog automatically
      on a genuinely empty database, replacing the manual `docker exec
      mongorestore` step.
- [ ] Jenkinsfile still runs `mongorestore` unconditionally every deploy
      (redundant now that the entrypoint hook handles first-run seeding) —
      safe to delete that pipeline stage.
- [ ] Flask's dev server (`flask run` / `python3 app.py`) is the production
      server in both Dockerfiles — swap for `gunicorn`.
- [ ] No HTTPS/TLS anywhere; `main.tf` opens Jenkins (8080) and the app (5000)
      to `0.0.0.0/0`. Put a reverse proxy (Caddy/nginx) in front, restrict
      Jenkins/SSH to a trusted CIDR.
- [ ] MongoDB runs with no auth, on EOL `mongo:4.4.18`. Upgrade to `mongo:7.0`
      with `--auth` and a scoped app user, not root.
- [ ] Jenkinsfile writes the Gemini key to a plaintext `.env` file via string
      interpolation, bypassing Jenkins secret masking — use `withEnv`
      consistently and delete that stage (Compose already reads
      `GEMINI_API_KEY` from the shell environment).
- [ ] `depends_on: condition: service_started` for Mongo only waits for the
      process to start, not for it to accept connections — add a healthcheck
      and switch to `condition: service_healthy`.
- [ ] Containers run as root in both Dockerfiles — add a non-root `USER`.
- [ ] No resource limits, no replicas — one crash takes down the whole
      service.

## Testing / Observability

- [x] **Unit test suites added** for both services, covering every route,
      using the app's real catalog data (pulled from
      `backend/database_backup/db_backup.archive`) and exact expected values
      rather than loose pass/fail checks:
      - `backend/tests/test_app_backend.py` — 89 tests (catalog, auth, cart
        math, order placement incl. the oversell regression test, admin
        identity/role/status, order cancel/revert, catalog CRUD, audit log).
      - `frontend/tests/test_app_frontend.py` — 67 tests (session gating,
        rate limiting, admin login incl. super-admin + promoted-admin
        identity, every proxy route, page rendering).
      - Run with `cd backend && pytest tests/ -v` and
        `cd frontend && pytest tests/ -v` (needs `requirements-test.txt`
        installed alongside each service's own `requirements.txt`).
- [ ] Not yet in CI — add a `Test` stage to the Jenkinsfile before the build
      stage now that both suites exist.
- [ ] No centralized logging, uptime checks, or error tracking. Smallest
      useful stack: ship container logs somewhere searchable, add a
      `/health` endpoint to both Flask apps, wire in `sentry-sdk`.

---

## Suggested order of attack

1. ~~The two `500`→`400` ID-handling fixes and the `auth_proxy` `KeyError`
   guard~~ — done.
2. ~~Admin identity, audit trail, catalog CRUD, order cancel/revert, low-stock
   pill, CSV bulk import, user disable/enable, search filters~~ — done.
3. Wire the test suites into Jenkins as a real `Test` stage.
4. Replica-set Mongo — unlocks both the race-condition fix and order
   transactions in one infra change.
5. Everything else (TLS, gunicorn, security headers, real pagination if the
   catalog/user base grows) as the app actually grows toward real traffic.
