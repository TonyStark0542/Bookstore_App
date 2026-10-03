# Container Hardening, Limits, Healthchecks & Network Isolation

Five problems found in the running stack, each written as: the scenario that exposes it, the fix, the commands to verify it, and — since most of these had more than one valid fix — why this specific approach won over the alternatives.

**Status:** Python tests pass (90 passed). Docker changes are written but not yet built/run — verify with `docker compose up --build` then `docker compose ps`.

---

## Problem 1: the frontend could talk to MongoDB directly, skipping the backend entirely

**The scenario:** `mongo:4.4.18` is running with no username or password set — there's no `MONGO_INITDB_ROOT_*` anywhere in the compose file. The *only* access control on data in this whole stack is the backend's `X-Internal-Token` check, and that check guards the backend's HTTP **routes** — it has nothing to do with the database port itself. All three containers sat on one shared network, `bookstore-mesh`. That means the frontend container — or anything else that ever joined that network — could open a raw connection straight to `mongodb-backend:27017` and read or write the entire catalog, completely bypassing the token check, the backend code, and every route guard, because none of that logic lives at the network layer.

```
Before — one flat network, Mongo reachable by everyone on it:

    ┌──────────┐      ┌──────────┐      ┌──────────┐
    │ frontend │──────│ backend  │──────│  mongo   │
    └──────────┘      └──────────┘      └──────────┘
              all three on "bookstore-mesh"
    frontend ──────────────────────────────▶ mongo   (direct path exists!)

After — two networks, backend bridges them, frontend has no path to mongo:

    ┌──────────┐              ┌──────────┐              ┌──────────┐
    │ frontend │──────────────│ backend  │──────────────│  mongo   │
    └──────────┘              └──────────┘              └──────────┘
       "frontend-net"      "frontend-net" + "db-net"       "db-net"
    frontend ───X (no route)──────────────────────────▶ mongo
```

**The fix:** split the one shared network into two. `db-net` holds only MongoDB and the backend. `frontend-net` holds the backend and the frontend. The backend joins both, since it legitimately needs to reach Mongo. The frontend joins only `frontend-net` — it now has **no route to Mongo at all**, not a permission it lacks, a path that doesn't exist.

```diff
   mongodb-backend:
     networks:
-      - bookstore-mesh
+      - db-net

   bookstore-backend:
     networks:
-      - bookstore-mesh
+      - frontend-net
+      - db-net

   bookstore-frontend:
     networks:
-      - bookstore-mesh
+      - frontend-net

 networks:
-  bookstore-mesh:
-    driver: bridge
+  frontend-net:
+    driver: bridge
+  db-net:
+    driver: bridge
```

**Commands (verify):**
```bash
# Frontend must NOT be able to reach Mongo — this should fail/time out
docker compose exec bookstore-frontend \
  python3 -c "import socket; socket.create_connection(('mongodb-backend', 27017), timeout=3)"

# Backend must still be able to reach Mongo — this should succeed silently
docker compose exec bookstore-backend \
  python3 -c "import socket; socket.create_connection(('mongodb-backend', 27017), timeout=3)"

# Confirm network membership directly
docker network inspect bookstore_app_db-net
docker network inspect bookstore_app_frontend-net
```

**Why this approach, not another one:** there were two realistic options here — add authentication to MongoDB (a username/password, checked on every connection), or remove the frontend's network path to Mongo entirely. Auth would also work, and is worth doing eventually regardless. But network isolation was the faster, more certain fix for *this specific* problem: adding Mongo auth still leaves a shared network where a future container, a misconfigured service, or a debugging session could still attempt a direct connection — it just fails at the credential-check step instead of never having a path at all. Removing the route entirely means there's nothing to misconfigure later; the frontend physically cannot reach port 27017, full stop. Auth is still worth adding as defense-in-depth, but topology was the fix that actually matches the shape of the problem — "who can reach this at all," not "who can authenticate to this."

---

## Problem 2: containers were running as root inside themselves

**The scenario:** both the backend and frontend Dockerfiles never dropped privileges — every process inside each container ran as root, the default if you don't explicitly set otherwise. This doesn't matter on a healthy day. It matters the day the app has a bug that gets exploited: a vulnerability in a Python dependency, a request that triggers unintended file access, anything that lets an attacker execute code inside the container. If that happens while running as root, the attacker has root *inside that container* — full read/write across the filesystem, the ability to install tools, no restriction at all beyond whatever Docker's own container boundary provides.

**The fix:** both Dockerfiles now create a fixed system user (`appuser`/`appgroup`, UID/GID `10001`, no home directory, `nologin` shell) and add `USER 10001:10001` as the last line before `CMD`. Two supporting changes had to come with it: packages moved from `/root/.local` to `/usr/local`, since a non-root user can't read into `/root` at all; and app files are now copied in with `--chown=appuser:appgroup`, since code should be owned by the account that actually runs it, not left owned by root.

```diff
 FROM python:3.11-slim AS runner
 WORKDIR /app

+RUN groupadd --system --gid 10001 appgroup \
+ && useradd --system --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser
+
-COPY --from=builder /root/.local /root/.local
-COPY app_backend.py ./app.py
-ENV PATH=/root/.local/bin:$PATH
+COPY --from=builder /root/.local /usr/local
+COPY --chown=appuser:appgroup app_backend.py ./app.py

 EXPOSE 8000
+USER 10001:10001
 CMD ["python3", "app.py"]
```

**Commands (verify):**
```bash
# Both should print uid=10001 gid=10001, not uid=0(root)
docker compose exec bookstore-backend id
docker compose exec bookstore-frontend id

# Confirm the process itself isn't root either
docker compose exec bookstore-backend ps -o user,pid,cmd
```

**Why this approach, not another one:** the alternative some teams reach for is a **read-only root filesystem** (`read_only: true` in compose) instead of, or alongside, dropping to a non-root user. That's a legitimate stronger option — it stops an attacker from writing *anything* at all, not just from having root. It wasn't used here because it's a bigger, riskier change to make blind: a read-only filesystem breaks any code path that writes temp files, logs, or caches unless you've audited the app for exactly where it writes and pre-mounted `tmpfs` volumes for those specific paths. Dropping root is the smaller, safer first move that closes the most damage-per-incident for the least risk of breaking something unexpectedly. Read-only root is a real candidate for a follow-up pass once those write paths are actually mapped out — not skipped because it's wrong, skipped because it wasn't verified safe yet.

Numeric UID (`10001:10001`) over a named user was also a deliberate choice, not laziness: it needs no name lookup at container start, and it's what Kubernetes-style `runAsNonRoot` checks expect to see if this ever moves past plain Docker Compose.

---

## Problem 3: the backend could start writing to Mongo before Mongo was actually ready

**The scenario:** `depends_on: mongodb-backend` originally used the default condition, `service_started` — which only means "the Mongo container process has begun," not "Mongo is actually accepting connections yet." There's a real gap between those two moments while Mongo finishes its own startup. The backend calls `create_index('email', unique=True)` on boot, and if that call fires into a Mongo that isn't listening yet, it can **silently time out** — the backend keeps running, looks fine, but the unique-email constraint never actually got created. That's not a crash you'd notice; it's a data-integrity guarantee quietly missing, the kind of gap that only surfaces later as a bug report about duplicate accounts.

```
Before:  mongo (container started) ⇢ backend starts immediately ⇢ frontend starts immediately
                                       (index creation may race Mongo's own boot)

After:   mongo ──▶ [healthy?] ──▶ backend ──▶ [healthy?] ──▶ frontend
              ping succeeds           /health succeeds
```

**The fix:** added a real `healthcheck:` to all three services — Mongo gets pinged with `db.adminCommand('ping')`, the backend and frontend get hit on a `/health` and `/login` endpoint respectively (both cheap, both pre-existing patterns rather than expensive routes). `depends_on` was changed from `service_started` to `service_healthy` for both dependent services, so the real startup order is now Mongo → (wait for healthy) → backend → (wait for healthy) → frontend, every time.

```diff
 # backend/app_backend.py
 @app.before_request
 def check_internal_token():
-    if request.headers.get("X-Internal-Token") != INTERNAL_SERVICE_TOKEN:
+    if request.path != "/health" and request.headers.get("X-Internal-Token") != INTERNAL_SERVICE_TOKEN:
         return jsonify({"error": "Forbidden"}), 403
+
+@app.route('/health', methods=['GET'])
+def health():
+    try:
+        client.admin.command('ping')
+        return jsonify({"status": "ok"}), 200
+    except Exception:
+        return jsonify({"status": "unhealthy"}), 503
```

```diff
 # docker-compose.yml
   mongodb-backend:
+    healthcheck:
+      test: ["CMD", "mongo", "--quiet", "--eval", "db.adminCommand('ping')"]
+      interval: 10s
+      timeout: 5s
+      retries: 5
+      start_period: 20s

   bookstore-backend:
+    healthcheck:
+      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
+      interval: 10s
+      timeout: 5s
+      retries: 5
+      start_period: 15s
     depends_on:
       mongodb-backend:
-        condition: service_started
+        condition: service_healthy

   bookstore-frontend:
+    healthcheck:
+      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/login', timeout=3)"]
+      interval: 10s
+      timeout: 5s
+      retries: 5
+      start_period: 15s
     depends_on:
-      - bookstore-backend
+      bookstore-backend:
+        condition: service_healthy
```

**Commands (verify):**
```bash
# STATUS column should read "healthy" for all three
docker compose ps

# Hit each healthcheck endpoint directly
curl http://localhost:5001/login                       # frontend, published on the host
docker compose exec bookstore-backend \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
docker compose exec mongodb-backend mongo --quiet --eval "db.adminCommand('ping')"

# Watch startup order live from a clean state
docker compose down && docker compose up
```

**Why this approach, not another one:** the other common fix for this exact race is a **retry loop inside the application code itself** — have `create_index` catch the failure and retry with backoff until Mongo responds. That's a valid pattern and plenty of production systems use it. It wasn't chosen here because it pushes infrastructure-level sequencing into application code, meaning every service that depends on Mongo would need to implement its own retry logic, and it's easy to get the backoff/timeout tuning subtly wrong per-service. A compose-level healthcheck solves the ordering problem **once**, declaratively, for every service that depends on it, without touching application logic at all. The honest limitation, worth being upfront about: Compose only uses health status to control *startup order* — it doesn't restart a container that goes unhealthy later on its own. This fix guarantees correct sequencing at boot; it isn't an ongoing self-healing mechanism, and framing it as one would be overselling what it actually does.

---

## Problem 4: one container could exhaust resources and starve the other two

**The scenario:** none of the three services had a CPU or memory ceiling. MongoDB in particular is known to cache aggressively into however much RAM it can see — given no limit, it will use as much as the host offers. A traffic spike, a memory leak in the backend, or just Mongo's normal caching behavior growing over time could all consume enough resources to slow down or crash the *other* containers sharing the same host, even though those other containers did nothing wrong themselves.

**The fix:** added explicit CPU and memory limits to all three services in `docker-compose.yml` — Mongo at 2 CPU/2GB (sized generously because of its caching behavior), backend at 1 CPU/1GB, frontend at 1 CPU/512MB (it only renders templates and proxies requests, genuinely the lightest of the three). A container that exceeds its memory ceiling gets OOM-killed and restarted automatically via the existing `restart: always`.

```diff
   mongodb-backend:
     restart: always
+    cpus: 2
+    mem_limit: 2g

   bookstore-backend:
     restart: always
+    cpus: 1
+    mem_limit: 1g

   bookstore-frontend:
     restart: always
+    cpus: 1
+    mem_limit: 512m
```

**Commands (verify):**
```bash
# Live usage against each limit
docker stats --no-stream

# Confirm the limits actually took effect on each container
docker inspect mongodb-backend --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'
docker inspect bookstore-backend-service --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'
docker inspect bookstore-frontend-service --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'
```

**Why this approach, not another one:** the alternative is relying on the host machine's own overall resource limits (or Docker Desktop's global cap) and hoping nothing runs away far enough to matter. That's not really a fix, it's an absence of one — it means the *first* symptom of a problem is everything on the host degrading at once, with no signal pointing at which container caused it. Per-service limits turn "the whole host got slow, good luck" into "container X got OOM-killed and restarted, check container X" — the failure becomes isolated and diagnosable instead of a shared, host-wide mystery.

---

## Problem 5: the backup mount gave the container unnecessary write access

**The scenario:** the host's `./backend/database_backup` folder was mounted into the container without a read-only flag: `./backend/database_backup:/backup`. The only thing this mount is actually used for is running `mongorestore --archive=/backup/db_backup.archive` — a read-only operation from the container's side. But without `:ro`, the container technically had *write* access to that host folder too, for no functional reason at all. (Worth noting: `init-restore.sh`'s own mount already had `:ro` set correctly — this line was simply the one that got missed when the mounts were first written.)

**The fix:** one line — added `:ro` to the mount.
```diff
-      - ./backend/database_backup:/backup
+      - ./backend/database_backup:/backup:ro
```

**Commands (verify):**
```bash
# Should now fail with "Read-only file system"
docker compose exec mongodb-backend touch /backup/test.txt

# Restore itself must still work exactly as before
docker exec -i mongodb-backend mongorestore --archive=/backup/db_backup.archive --gzip
```

**Why this approach, not another one:** there isn't really a competing approach here — this is about matching the permission to the actual usage rather than picking between two designs. The only "alternative" would be leaving it writable, which has no upside: nothing in the stack ever writes to this path, so the only thing extra write access could do is make an accidental or compromised write inside the container capable of corrupting a real file on the host. Removing a permission nobody uses is close to a free fix, which is exactly why it's the smallest problem on this list.

---

## Tried and reverted

`cap_drop: [ALL]` + `security_opt: no-new-privileges` on the backend/frontend services — added after misreading "drop all root privileges" as a request at the compose level. The actual intent was the Dockerfile `USER` change (Problem 2 above), so this was reverted before any further work was done on it. Still a legitimate idea for later — see below.

## Not done yet (raised, not requested)

- Capability dropping (`cap_drop`/`no-new-privileges`) on the backend/frontend containers — the compose-level counterpart to Problem 2's fix, worth doing once verified it doesn't break anything the app actually needs.
- Secrets as files or a real secrets manager, instead of plain environment variables.
- `mongodb-backend` still runs as the official image's default user — it handles its own root→user drop internally, and changing that needs separate testing rather than being bundled in here.
- Jenkinsfile, Terraform, and frontend code/templates — untouched in this pass.
