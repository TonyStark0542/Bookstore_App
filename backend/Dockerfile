# STAGE 1: Build backend dependencies
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# STAGE 2: Isolated production execution layer
FROM python:3.11-slim AS runner
WORKDIR /app

COPY --from=builder /root/.local /root/.local
COPY app_backend.py ./app.py

ENV PATH=/root/.local/bin:$PATH

# Expose port 8000 for internal microservice communication
EXPOSE 8000
CMD ["python3", "app.py"]