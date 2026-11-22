#!/usr/bin/env bash
# Démo du scaling vertical : UN seul pod, petit (250m) puis gros (1 CPU).
# Pas d'autoscaler : on change soi-même les ressources pour comparer à charge égale.
#   scripts/demo-vertical.sh small|big
set -euo pipefail
SIZE=${1:-small}
case "$SIZE" in
  small) CPU=250m;  MEM=256Mi ;;
  big)   CPU=1000m; MEM=512Mi ;;   # au-delà d'1 CPU : aucun gain (1 processus Python = GIL)
  *) echo "Usage : $0 small|big"; exit 1 ;;
esac

if kubectl -n lab get hpa webapp >/dev/null 2>&1; then
  echo "→ Passage en mode fixe (retrait du HPA)"
  make -s deploy OVERLAY=fixed >/dev/null
fi
echo "→ 1 seul pod, ressources : CPU $CPU / RAM $MEM (requests = limits)"
kubectl -n lab scale deployment/webapp --replicas=1 >/dev/null
kubectl -n lab set resources deployment/webapp -c app \
  --requests=cpu=$CPU,memory=$MEM --limits=cpu=$CPU,memory=$MEM >/dev/null
kubectl -n lab rollout status deployment/webapp --timeout=180s
kubectl -n lab get pods -l app.kubernetes.io/name=webapp \
  -o custom-columns='POD:.metadata.name,NŒUD:.spec.nodeName,CPU:.spec.containers[0].resources.limits.cpu,RAM:.spec.containers[0].resources.limits.memory'
echo
echo "👉 Lance la même charge dans Locust (ex. 30 utilisateurs) et compare débit et latence p95 entre small et big."
