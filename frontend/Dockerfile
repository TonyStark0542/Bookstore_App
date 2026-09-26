# STAGE 1: Build dependency wheelhouse
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# STAGE 2: Lightweight production runtime
FROM python:3.11-slim AS runner
WORKDIR /app

# Copy only the compiled packages from stage 1 to keep the image pristine
COPY --from=builder /root/.local /root/.local
COPY app_frontend.py ./app.py
COPY templates/ ./templates/
COPY static/ ./static/

ENV PATH=/root/.local/bin:$PATH
ENV FLASK_APP=app.py

EXPOSE 5001
CMD ["python3", "-m", "flask", "run", "--host=0.0.0.0", "--port=5001"]