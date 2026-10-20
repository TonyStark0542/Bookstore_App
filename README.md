# 📚 READ RIGHT — Bookstore Microservices

A three-tier online bookstore (frontend, backend API, MongoDB) with real accounts, a cart, a simulated checkout, and an admin panel to move orders through their delivery stages. Built as a microservices exercise — not a single Flask app with folders, three separate services that only talk to each other over a defined boundary.

## ✨ Key Features

* **Accounts** — register/login with salted password hashing, session-based auth for regular users.
* **Catalog & cart** — browse books, add to cart, checkout.
* **Simulated checkout** — card / UPI / cash-on-delivery, no real money moves, but the full flow (place order → pending → packed → shipped → delivered) is real.
* **Order tracking** — a page that reflects the order's actual current stage.
* **Admin panel** — separate, password-gated area to move orders through their delivery stages.
* **AI-generated book summaries** — backend calls Google Gemini (`google-genai`) to generate summaries on the fly.
* **Service-to-service auth** — frontend and backend share an internal token; the backend doesn't trust a request just because it came from inside the network.
* **Two real deployment paths** — Docker Compose and Kubernetes (`kind`), both documented below.

## 🛠 Tech Stack

* **Backend:** Python, Flask, MongoDB (via PyMongo), Google Gemini (`google-genai`)
* **Frontend:** Python, Flask (server-rendered Jinja templates), vanilla JS, hand-written CSS design system — no frontend framework
* **Infra:** Docker Compose (the original) and Kubernetes via `kind` (ConfigMap/Secret for config, a PVC so Mongo data survives pod restarts, ClusterIP + NodePort services).

```
                 ┌──────────────┐        internal token        ┌──────────────┐
  browser  ───▶  │   frontend   │ ───────────────────────────▶ │   backend    │ ───▶ MongoDB
                 │ Flask, 5001  │ ◀─────────────────────────── │ Flask, 8000  │      + Gemini
                 └──────────────┘                               └──────────────┘
          owns login sessions                         only thing allowed to talk to Mongo/Gemini
```

## 📖 The Story

This started as a single-purpose bookstore demo and grew into a small but complete microservices exercise: a Flask frontend that only renders pages and owns login sessions, a separate Flask backend that is the only thing allowed to talk to MongoDB and Gemini, and MongoDB itself as the data tier — each one a separate Docker container, talking only over an internal network.

**The goal** was to go beyond a static storefront and build the parts that make it feel like a real shop: accounts backed by salted password hashes, a cart, an end-to-end checkout with a *simulated* payment gateway (card / UPI / cash-on-delivery — no real money moves), and an order-tracking page that updates as an order is packed, shipped, and delivered.

**The struggle:** the two services trusting each other correctly turned out to be the hard part. The backend originally had no login system of its own — anything that could reach it on the network could read every user's data. Getting that right meant adding a shared internal token that the frontend attaches to every request and the backend verifies on every single route, instead of relying on "well, nothing else can reach that port" as the only defense.

**Core concepts exercised:** service-to-service auth (internal shared token, not just network isolation), session-based login vs. a separate password-gated admin area, salted password hashing, simulated payment flows, rate limiting, and just generally learning what breaks when you actually try to run a toy project like it's production (secrets, port mismatches, data durability across deploys).

## 🚀 How to Run It

Two ways to stand this up, depending on what you're after. Docker Compose if you just want it running. Kubernetes if you want the closer-to-real-infra version (configmaps, secrets, a persistent volume, services).

### Option A: Docker Compose (the quick way)

1. Clone the repo, then create a `.env` file in the project root (same folder as `docker-compose.yml`) with the four required variables:
```bash
GEMINI_API_KEY=your-real-gemini-key
INTERNAL_SERVICE_TOKEN=any-long-random-string
SECRET_KEY=any-other-long-random-string
ADMIN_PASSWORD=whatever-you-want-for-the-admin-panel
```
Docker Compose automatically reads `.env` from the project root — no need to `export` anything yourself. (`.env` is already in `.gitignore`, so it's never committed.)

2. Build and start every container:
```bash
docker compose up --build
```

3. First run only — the catalog starts empty until you seed it:
```bash
docker exec -i mongodb-backend mongorestore --archive=/backup/db_backup.archive --gzip
```

Visit **http://localhost:5001**. Log in to the admin panel at `/admin/login`.

**To stop:**
```bash
docker compose down
```

### Option B: Kubernetes (kind)

Same app, but running as actual Deployments/Services on a local multi-node cluster instead of three Compose containers. Config lives in a ConfigMap, secrets in a Secret (not committed), and Mongo gets a real PVC so data survives a pod restart.

1. Create the cluster (1 control-plane + 2 workers, maps `localhost:30005` to the frontend's NodePort):
```bash
kind create cluster --name my-cluster-app --config k8s/multi-node-cluster.yml
```

2. Create `k8s/secrets.yml` yourself first — it's gitignored on purpose, so it's not in the repo. Same four values as the `.env` above, just base64-encoded (or use `stringData:` instead of `data:` if you want to skip the base64 step). Then apply config + secrets:
```bash
kubectl apply -f k8s/secrets.yml -f k8s/configmap.yml
```

3. Seed Mongo's restore data as a ConfigMap (has to be `create`, not `apply` — the backup archive is too big for `apply`'s annotation):
```bash
kubectl create configmap mongo-init \
  --from-file=backend/database_backup/db_backup.archive \
  --from-file=backend/init-restore.sh
```

4. Bring up Mongo, then the app:
```bash
kubectl apply -f k8s/mongo.yml
kubectl apply -f k8s/backend.yml -f k8s/frontend.yml
```

Visit **http://localhost:30005**. Same admin panel, same password — pull it back out if you forget it:
```bash
kubectl get secret secret-app -o jsonpath='{.data.ADMIN_PASSWORD}' | base64 -d
```

**To stop / reset:**
```bash
kubectl delete -f k8s/backend.yml -f k8s/frontend.yml -f k8s/mongo.yml  # deleting mongo.yml also drops the PVC and the data
kind delete cluster --name my-cluster-app
```

## 📌 Project Status

Feature-complete for a demo — browsing, login/register, cart, checkout, order tracking, and an admin panel are all working end-to-end, on both deployment paths. Not production-hardened — see what's still open below.

## 🚧 What's Still Open

Both deployment paths only run on whatever machine you type the command into right now — no HTTPS, no CI/CD, no remote host. Listed roughly in the order it'd make sense to tackle, each with what's wrong and what fixing it looks like:

| # | Issue | Fix |
|---|---|---|
| 1 | No HTTPS/TLS anywhere, and nothing restricts who can reach the admin surface (SSH, the NodePort). | Put a reverse proxy (Caddy/nginx) in front for TLS, and lock those down to your own IP. |
| 2 | MongoDB runs with no authentication, on an end-of-life version (4.4 — the last release that doesn't require a CPU with AVX support). | Give it a real password at minimum; upgrading the version is a separate, bigger job. |
| 3 | Both services run on Flask's built-in dev server, not a production WSGI server. | Swap in gunicorn, and add a basic health-check + smoke test so a bad deploy fails loudly instead of shipping silently. |
| 4 | No transaction safety around placing an order — a crash mid-checkout can leave stock/cart data inconsistent, and stock isn't checked before payment "succeeds." | Wrap the order-placement flow in a transaction / proper stock check. |
| 5 | No CSRF protection or standard security headers (CSP, HSTS, etc.). | Add Flask-WTF (or equivalent) CSRF tokens and a headers middleware. |
| 6 | The k8s manifests have no readiness/liveness probes or resource `requests` — a crashed pod just sits there looking "Running" instead of getting restarted. | Add `/health` (backend) and `/login` (frontend) probes, plus `requests` alongside the existing `limits`. |
| 7 | Everything still runs on a laptop, not a real host. | Move the k8s side to an EC2 box (or any small cloud VM) running `kind` or a lighter distro like k3s — same manifests, just `scp` the gitignored `secrets.yml` over or recreate it with `kubectl create secret generic` on the box. |
| 8 | No CI/CD — every deploy above is manual. | Worth setting up once everything above is solid enough to be worth automating. |
