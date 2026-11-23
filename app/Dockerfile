# syntax=docker/dockerfile:1
# --- Étape 1 : construire les dépendances dans un venv isolé ---
FROM python:3.15-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# --- Étape 2 : image finale minimale, sans outils de build ---
FROM python:3.15-slim
LABEL org.opencontainers.image.title="scaling-lab" \
      org.opencontainers.image.description="API de test de charge et de scaling (M2 Grid & Cluster)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY main.py ui.html ./
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser
USER 10001
EXPOSE 8000
# 1 processus par conteneur : on scale en ajoutant des pods, pas des workers.
# --timeout-graceful-shutdown : laisse finir les requêtes en cours après SIGTERM.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-graceful-shutdown", "20"]
