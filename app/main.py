"""Scaling Lab — petite API pour tester la charge, le scaling et la haute disponibilité.

Endpoints :
  GET /                        -> qui m'a répondu ? (pod + nœud)
  GET /health                  -> sonde liveness (le processus est vivant)
  GET /ready                   -> sonde readiness (prêt à recevoir du trafic ; faux pendant l'arrêt)
  GET /cpu?ms=100              -> consomme du CPU pendant `ms` millisecondes
  GET /memory?mb=50&seconds=2  -> alloue `mb` Mo de RAM pendant `seconds` secondes
  GET /crash                   -> tue le processus (démo d'auto-réparation), si ALLOW_CRASH=1
  GET /metrics                 -> métriques Prometheus

Configuration (variables d'environnement, principe 12-factor) :
  POD_NAME, NODE_NAME, ALLOW_CRASH, MAX_CPU_MS, MAX_MEMORY_MB, LOG_LEVEL
"""

from __future__ import annotations

import logging
import math
import os
import signal
import socket
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Query, Response
from prometheus_client import Gauge
from prometheus_fastapi_instrumentator import Instrumentator


@dataclass(frozen=True)
class Settings:
    pod: str = field(default_factory=lambda: os.getenv("POD_NAME", socket.gethostname()))
    node: str = field(default_factory=lambda: os.getenv("NODE_NAME", "local"))
    allow_crash: bool = field(default_factory=lambda: os.getenv("ALLOW_CRASH", "0") == "1")
    max_cpu_ms: int = field(default_factory=lambda: int(os.getenv("MAX_CPU_MS", "5000")))
    max_memory_mb: int = field(default_factory=lambda: int(os.getenv("MAX_MEMORY_MB", "1024")))


settings = Settings()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='{"time":"%(asctime)s","level":"%(levelname)s","pod":"' + settings.pod + '","msg":"%(message)s"}',
)
log = logging.getLogger("scaling-lab")

ALLOCATED_MB = Gauge("lab_allocated_memory_mb", "Mémoire allouée volontairement par /memory")
_state = {"allocated": 0, "shutting_down": False, "started_at": time.time()}
_lock = threading.Lock()


def _on_sigterm(signum, frame):  # pragma: no cover - testé en conditions réelles (kubectl delete pod)
    """Kubernetes envoie SIGTERM avant d'arrêter un pod : on se déclare « non prêt »
    pour que le load balancer arrête de nous envoyer du trafic, puis uvicorn termine
    les requêtes en cours (arrêt propre)."""
    _state["shutting_down"] = True
    log.info("SIGTERM reçu : readiness=false, arrêt propre en cours")
    if callable(_previous_sigterm):
        _previous_sigterm(signum, frame)


_previous_sigterm = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _previous_sigterm
    if threading.current_thread() is threading.main_thread():
        _previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _on_sigterm)
    log.info("démarrage sur le nœud %s", settings.node)
    yield
    log.info("arrêt terminé")


app = FastAPI(title="Scaling Lab", version="1.1.0", lifespan=lifespan)
Instrumentator(excluded_handlers=["/metrics", "/health", "/ready"]).instrument(app).expose(app, include_in_schema=False)


@app.get("/")
def root():
    return {
        "message": "Bonjour depuis le cluster !",
        "pod": settings.pod,
        "node": settings.node,
        "uptime_s": round(time.time() - _state["started_at"], 1),
    }


@app.get("/health")
def health():
    return {"status": "ok", "pod": settings.pod}


@app.get("/ready")
def ready(response: Response):
    if _state["shutting_down"]:
        response.status_code = 503
        return {"status": "shutting_down", "pod": settings.pod}
    return {"status": "ready", "pod": settings.pod}


def burn_cpu(ms: int) -> int:
    """Travail CPU pur pendant `ms` ms. Retourne le nombre d'opérations effectuées."""
    end = time.perf_counter() + ms / 1000
    ops = 0
    while time.perf_counter() < end:
        for i in range(1000):
            math.sqrt(i * 12345.6789)
        ops += 1000
    return ops


@app.get("/cpu")
def cpu(ms: int = Query(100, ge=1)):
    if ms > settings.max_cpu_ms:
        raise HTTPException(422, f"ms doit être ≤ {settings.max_cpu_ms}")
    ops = burn_cpu(ms)
    return {"pod": settings.pod, "node": settings.node, "cpu_ms": ms, "operations": ops}


@app.get("/memory")
def memory(mb: int = Query(50, ge=1), seconds: float = Query(2, ge=0, le=60)):
    """Alloue réellement `mb` Mo (chaque page est touchée) puis les libère après `seconds` s."""
    if mb > settings.max_memory_mb:
        raise HTTPException(422, f"mb doit être ≤ {settings.max_memory_mb}")
    block = bytearray(mb * 1024 * 1024)
    for i in range(0, len(block), 4096):  # forcer l'allocation physique des pages
        block[i] = 1
    with _lock:
        _state["allocated"] += mb
        ALLOCATED_MB.set(_state["allocated"])
    try:
        time.sleep(seconds)
    finally:
        del block
        with _lock:
            _state["allocated"] -= mb
            ALLOCATED_MB.set(_state["allocated"])
    return {"pod": settings.pod, "node": settings.node, "allocated_mb": mb, "held_s": seconds}


@app.get("/crash")
def crash():
    if not settings.allow_crash:
        raise HTTPException(403, "Crash désactivé (ALLOW_CRASH=0)")
    log.warning("crash volontaire demandé")
    threading.Timer(0.2, lambda: os._exit(1)).start()
    return {"pod": settings.pod, "message": "Ce pod va s'arrêter dans 0,2 s"}
