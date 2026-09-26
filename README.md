# 📚 READ RIGHT — Bookstore Microservices

A three-tier online bookstore (frontend, backend API, MongoDB) with real accounts, a cart, a simulated checkout, and an admin panel to move orders through their delivery stages.

## 📖 The Story

This started as a single-purpose bookstore demo and grew into a small but complete microservices exercise: a Flask frontend that only renders pages and owns login sessions, a separate Flask backend that is the only thing allowed to talk to MongoDB and Gemini, and MongoDB itself as the data tier — each one a separate Docker container, talking only over an internal network.

**The goal** was to go beyond a static storefront and build the parts that make it feel like a real shop: accounts backed by salted password hashes, a cart, an end-to-end checkout with a *simulated* payment gateway (card / UPI / cash-on-delivery — no real money moves), and an order-tracking page that updates as an order is packed, shipped, and delivered.

**The struggle:** the two services trusting each other correctly turned out to be the hard part. The backend originally had no login system of its own — anything that could reach it on the network could read every user's data. Getting that right meant adding a shared internal token that the frontend attaches to every request and the backend verifies on every single route, instead of relying on "well, nothing else can reach that port" as the only defense.

## 🛠 Tech Stack & Concepts

* **Backend:** Python, Flask, MongoDB (via PyMongo), Google Gemini (`google-genai`) for AI-generated book summaries
* **Frontend:** Python, Flask (server-rendered Jinja templates), vanilla JS, hand-written CSS design system (no frontend framework)
* **Infra:** Docker, Docker Compose — currently how this actually runs. A `Jenkinsfile` and Terraform config for a GCP VM exist in the repo but aren't wired up or in use yet; running `docker compose up --build` locally is the real deployment story right now.
* **Core concepts exercised:** service-to-service auth (internal shared token, not just network isolation), session-based login vs. a separate password-gated admin area, salted password hashing, simulated payment flows, rate limiting, and just generally learning what breaks when you actually try to run a toy project like it's production (secrets, port mismatches, data durability across deploys).

## 📌 Project Status

* **Status:** feature-complete for a demo — browsing, login/register, cart, checkout, order tracking, and an admin panel are all working end-to-end.
* **Not production-hardened yet** — see [PRODUCTION_REVIEW.md](PRODUCTION_REVIEW.md) for the full, honest list of what's still open before this could handle real traffic or real money (HTTPS, a real WSGI server, MongoDB auth, and a few others).

## 🚧 What's Still Open

Tracked in detail in [PRODUCTION_REVIEW.md](PRODUCTION_REVIEW.md) — the short version:

* [ ] No HTTPS/TLS anywhere yet, and the firewall currently allows the Jenkins UI from the entire internet.
* [ ] MongoDB runs with no authentication, on an end-of-life version (4.4 — the last release that runs without requiring a CPU with AVX support).
* [ ] Both services run on Flask's built-in dev server, not a real production WSGI server like gunicorn.
* [ ] No transaction safety around placing an order (a crash mid-checkout can leave stock/cart data inconsistent), and stock isn't checked before payment "succeeds."
* [ ] No CSRF protection or standard security headers (CSP, HSTS, etc.) yet.

## 🚀 How to Run It

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

## 🔭 Improving This Deployment

Right now this only runs as `docker compose up --build` on whatever machine you type that into — there's no CI/CD actually running, no remote host, and no automation beyond the `.env` file. Roughly in the order it'd make sense to tackle:

1. **Wire up the CI/CD that already half-exists.** There's a `Jenkinsfile` and a `main.tf` sitting unused in this repo — get Jenkins actually building and deploying on every push before adding anything else, so changes stop being "run it on my laptop" and start being "push and it deploys."
2. **Put it on a real host, not a laptop.** The Terraform config provisions a GCP VM for exactly this — once Jenkins is live, point it at that VM (or any small cloud box) instead of local Docker.
3. **Add HTTPS and lock down the firewall.** A reverse proxy (Caddy/nginx) for TLS, and restrict any admin surface (Jenkins UI, SSH) to your own IP instead of the whole internet.
4. **Give MongoDB a real password and a persistent volume.** It currently runs with no auth and no durable data directory — fine on a laptop, not fine anywhere else.
5. **Swap Flask's dev server for gunicorn**, and add a basic health-check + smoke test so a bad deploy fails loudly instead of shipping silently.

The full, itemized version of this — with exact file/line references and copy-pasteable fixes — is in [PRODUCTION_REVIEW.md](PRODUCTION_REVIEW.md).
