#!/usr/bin/env bash
# Démo dans Google Cloud Shell (gratuit, sans compte de facturation) :
# un vrai cluster Kubernetes (minikube) sur la VM Cloud Shell, avec HPA et metrics-server.
#
#   scripts/cloudshell.sh up          crée le cluster, construit l'image, déploie, ouvre le port 8080
#   scripts/cloudshell.sh watch       vue live : pods, HPA, CPU (garder ouvert)
#   scripts/cloudshell.sh load        Locust → « Aperçu sur le Web » port 8089
#   scripts/cloudshell.sh panne-serveur  HAUTE DISPO : coupe brutalement un serveur du cluster
#   scripts/cloudshell.sh panne-pod      tue un pod (auto-réparation)
#   scripts/cloudshell.sh vertical small|big   1 seul pod, 0,25 CPU ou 1 CPU
#   scripts/cloudshell.sh resize 1000m         redimensionnement À CHAUD (sans redémarrage)
#   scripts/cloudshell.sh reset       retour au mode horizontal (HPA)
#   scripts/cloudshell.sh down        supprime le cluster
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VENV="$HOME/.lab-venv"   # Python de Cloud Shell géré par le système → environnement dédié
PY="$VENV/bin/python"
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# NodePort sur le nœud minikube : le trafic est réparti entre tous les pods (pas de port-forward,
# qui n'enverrait tout qu'à un seul pod et casserait pendant la démo de panne).
app_url() { echo "http://$(minikube ip):30080"; }

wait_app() {
  for _ in $(seq 30); do curl -fs "$(app_url)/health" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "⚠️ l'application ne répond pas sur $(app_url)"
}

deploy() {
  kubectl apply -k deploy/overlays/cloudshell \
    --prune -l app.kubernetes.io/part-of=lab-scaling-gke \
    --prune-allowlist=autoscaling/v2/HorizontalPodAutoscaler
  kubectl -n lab rollout status deployment/webapp --timeout=180s
}

case "${1:-help}" in
  up)
    step "1/5 Cluster Kubernetes de 2 serveurs (minikube) sur la VM Cloud Shell"
    minikube status >/dev/null 2>&1 || minikube start --nodes=2 --cpus=2 --memory=1800mb
    kubectl get nodes
    step "2/5 metrics-server (nécessaire au HPA et à kubectl top)"
    minikube addons enable metrics-server
    step "3/5 Construction de l'image Docker dans le cluster"
    minikube image build --all -t scaling-lab:local app   # --all : sur chaque serveur
    step "4/5 Déploiement (Deployment, Service, PDB, NetworkPolicy, HPA)"
    deploy
    step "5/5 Accès à l'application + Locust"
    wait_app
    [[ -x "$PY" ]] || python3 -m venv "$VENV"
    "$PY" -m pip install -q locust requests
    for _ in 1 2 3 4; do curl -s -H "Connection: close" "$(app_url)/"; echo; done
    echo -e "\n✅ Prêt ! Ouvre un 2e onglet Cloud Shell (+) : scripts/cloudshell.sh watch"
    ;;
  watch)
    watch -n2 -t -c scripts/demo-watch.sh
    ;;
  load)
    echo "👉 Locust : bouton « Aperçu sur le Web » (en haut à droite) → Changer le port → 8089"
    echo "   Host déjà rempli : $(app_url)"
    "$PY" -m locust -f loadtest/locustfile.py --host "$(app_url)" --web-port 8089
    ;;
  panne-serveur)
    echo "Serveurs et pods avant la panne :"
    kubectl -n lab get pods -o wide | awk '{print $1, $2, $3, $7}' | column -t
    TARGET_URL="$(app_url)/" "$PY" chaos/chaos.py mknode --repeat 1 --baseline 10 --observe 15
    ;;
  panne-pod)
    TARGET_URL="$(app_url)/" "$PY" chaos/chaos.py pod --repeat 1 --observe 15
    ;;
  vertical)
    case "${2:-small}" in small) CPU=250m MEM=192Mi ;; big) CPU=1000m MEM=384Mi ;; *) echo "small|big"; exit 1 ;; esac
    kubectl -n lab delete hpa webapp --ignore-not-found >/dev/null
    kubectl -n lab scale deployment/webapp --replicas=1 >/dev/null
    kubectl -n lab set resources deployment/webapp -c app \
      --requests=cpu=$CPU,memory=$MEM --limits=cpu=$CPU,memory=$MEM >/dev/null
    kubectl -n lab rollout status deployment/webapp --timeout=120s
    wait_app
    kubectl -n lab get pods -o custom-columns='POD:.metadata.name,CPU:.spec.containers[0].resources.limits.cpu,RAM:.spec.containers[0].resources.limits.memory'
    echo "👉 Mode vertical : 1 pod à $CPU. Lance la même charge dans Locust (20 utilisateurs) et compare la latence."
    ;;
  resize)
    scripts/demo-resize.sh "${2:-1000m}"
    ;;
  reset)
    deploy
    echo "✅ Retour au mode horizontal (HPA 2 → 8 pods)"
    ;;
  down)
    minikube delete
    ;;
  *) sed -n '2,13p' "$0" ;;
esac
