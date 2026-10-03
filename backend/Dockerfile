# STAGE 1: Build backend dependencies
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# STAGE 2: Isolated production execution layer
FROM python:3.11-slim AS runner
WORKDIR /app

# 1. Create a system user and group with no login shell and fixed UID/GID
RUN groupadd --system --gid 10001 appgroup \
 && useradd --system --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

# 2. Copy the pre-built packages directly to system library paths
COPY --from=builder /root/.local /usr/local

# 3. Copy application code and assign ownership to appuser
COPY --chown=appuser:appgroup app_backend.py ./app.py

# Expose port 8000 for internal microservice communication
EXPOSE 8000

# 4. Drop all root privileges
USER 10001:10001

# 5. App now runs completely unprivileged
CMD ["python3", "app.py"]
