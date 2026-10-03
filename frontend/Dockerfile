# STAGE 1: Build dependency wheelhouse
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# STAGE 2: Lightweight production runtime
FROM python:3.11-slim AS runner
WORKDIR /app

# 1. Create a system user and group with no login shell and fixed UID/GID
RUN groupadd --system --gid 10001 appgroup \
 && useradd --system --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

# 2. Copy the pre-built packages directly to system library paths
COPY --from=builder /root/.local /usr/local

# 3. Copy application code and assign ownership to appuser
COPY --chown=appuser:appgroup app_frontend.py ./app.py
COPY --chown=appuser:appgroup templates/ ./templates/
COPY --chown=appuser:appgroup static/ ./static/

ENV FLASK_APP=app.py

EXPOSE 5001

# 4. Drop all root privileges
USER 10001:10001

# 5. App now runs completely unprivileged
CMD ["python3", "-m", "flask", "run", "--host=0.0.0.0", "--port=5001"]
