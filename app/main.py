"""Projet GCP — petite API pour tester la charge, le scaling et la haute disponibilité.

Endpoints :
  GET /                        -> qui m'a répondu ? (pod + nœud) ; un navigateur reçoit le tableau de bord
  GET /ui                      -> tableau de bord web (charge, latence, répartition, chaos)
  GET /info                    -> version, limites et preuve d'hébergement GCP (serveur de métadonnées)
  GET /health                  -> sonde liveness (le processus est vivant)
  GET /ready                   -> sonde readiness (prêt à recevoir du trafic ; faux pendant l'arrêt)
  GET /cpu?ms=100              -> consomme du CPU pendant `ms` millisecondes
  GET /memory?mb=50&seconds=2  -> alloue `mb` Mo de RAM pendant `seconds` secondes
  GET /crash[?pod=nom]         -> tue le processus (démo d'auto-réparation), si ALLOW_CRASH=1
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
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
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


# --- Preuve d'hébergement : serveur de métadonnées GCP (joignable uniquement depuis Google Cloud) ---
METADATA_URL = os.getenv("GCP_METADATA_URL", "http://169.254.169.254/computeMetadata/v1/")
_METADATA_KEYS = {
    "project_id": "project/project-id",
    "zone": "instance/zone",
    "cluster": "instance/attributes/cluster-name",
    "cluster_location": "instance/attributes/cluster-location",
    "machine_type": "instance/machine-type",
    "instance": "instance/hostname",
}
_gcp: dict[str, str] = {}


def _metadata(path: str, timeout: float) -> str | None:
    req = urllib.request.Request(METADATA_URL + path, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL fixe (lien local GCP)
            if r.headers.get("Metadata-Flavor") != "Google":
                return None
            return r.read(256).decode().strip().rsplit("/", 1)[-1] or None  # zones/xx -> xx
    except Exception:  # noqa: BLE001 - hors GCP : pas de métadonnées, c'est normal
        return None


def load_gcp_metadata(timeout: float = 0.5) -> dict[str, str]:
    """Lit projet, zone, cluster… Vide hors de Google Cloud (le premier appel échoue vite)."""
    if _metadata("project/project-id", timeout) is None:
        return {}
    found = {k: v for k, path in _METADATA_KEYS.items() if (v := _metadata(path, timeout))}
    if "instance" in found:
        found["instance"] = found["instance"].split(".")[0]
    return found


def _load_gcp_in_background():
    _gcp.update(load_gcp_metadata())
    log.info("hébergement : %s", f"Google Cloud, projet {_gcp['project_id']}" if _gcp else "hors GCP")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_load_gcp_in_background, daemon=True).start()
    global _previous_sigterm
    if threading.current_thread() is threading.main_thread():
        _previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _on_sigterm)
    log.info("démarrage sur le nœud %s", settings.node)
    yield
    log.info("arrêt terminé")


VERSION = "1.2.0"
UI_HTML = (Path(__file__).with_name("ui.html")).read_text(encoding="utf-8")

app = FastAPI(title="Projet GCP", version=VERSION, lifespan=lifespan)
Instrumentator(excluded_handlers=["/metrics", "/health", "/ready", "/ui", "/info"]).instrument(app).expose(
    app, include_in_schema=False
)


@app.middleware("http")
async def identify_pod(request: Request, call_next):
    """Chaque réponse dit quel pod l'a servie (lu par le tableau de bord pour toutes les routes).
    `X-Lab-Fresh` : on ferme la connexion après la réponse, sinon le keep-alive du navigateur
    collerait toutes ses requêtes au même pod (le Service répartit par connexion TCP, pas par requête)."""
    response = await call_next(request)
    response.headers["X-Pod"] = settings.pod
    response.headers["X-Node"] = settings.node
    response.headers["X-Uptime"] = f"{time.time() - _state['started_at']:.1f}"
    if request.headers.get("x-lab-fresh"):
        response.headers["Connection"] = "close"
    return response


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui():
    return HTMLResponse(UI_HTML, headers={"Cache-Control": "no-cache"})


@app.get("/info")
def info():
    return {
        "version": VERSION,
        "allow_crash": settings.allow_crash,
        "max_cpu_ms": settings.max_cpu_ms,
        "max_memory_mb": settings.max_memory_mb,
        "pod": settings.pod,
        "node": settings.node,
        "gcp": dict(_gcp),
    }


@app.get("/")
def root(request: Request):
    if _wants_html(request):
        return ui()
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
def crash(pod: str | None = Query(None, description="Ne crasher que si ce pod reçoit la requête")):
    """Sans `pod` : le pod qui reçoit la requête s'arrête. Avec `pod` : seul ce pod accepte ;
    les autres répondent 409 et le client réessaie (le Service choisit le pod au hasard)."""
    if not settings.allow_crash:
        raise HTTPException(403, "Crash désactivé (ALLOW_CRASH=0)")
    if pod and pod != settings.pod:
        raise HTTPException(409, f"Requête reçue par {settings.pod}, pas par {pod}")
    log.warning("crash volontaire demandé")
    threading.Timer(0.2, lambda: os._exit(1)).start()
    return {"pod": settings.pod, "message": "Ce pod va s'arrêter dans 0,2 s"}
