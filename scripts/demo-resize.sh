#!/usr/bin/env bash
# Scaling vertical À CHAUD (in-place pod resize, Kubernetes ≥ 1.33) :
# on change le CPU d'un pod en cours d'exécution, SANS le redémarrer.
#   scripts/demo-resize.sh 1000m
set -euo pipefail
CPU=${1:-1000m}
POD=$(kubectl -n lab get pods -l app.kubernetes.io/name=webapp -o jsonpath='{.items[0].metadata.name}')
show() {
  kubectl -n lab get pod "$POD" -o custom-columns='POD:.metadata.name,CPU_DEMANDÉ:.spec.containers[0].resources.requests.cpu,CPU_LIMITE:.spec.containers[0].resources.limits.cpu,CPU_APPLIQUÉ:.status.containerStatuses[0].resources.limits.cpu,REDÉMARRAGES:.status.containerStatuses[0].restartCount'
}
echo "Avant :"; show
echo
# La classe QoS d'un pod ne peut pas changer à chaud :
#   Guaranteed (requests = limits) → on change les deux ; Burstable → seulement la limite.
REQ=$(kubectl -n lab get pod "$POD" -o jsonpath='{.spec.containers[0].resources.requests.cpu}')
LIM=$(kubectl -n lab get pod "$POD" -o jsonpath='{.spec.containers[0].resources.limits.cpu}')
if [[ "$REQ" == "$LIM" ]]; then RES="{\"requests\":{\"cpu\":\"$CPU\"},\"limits\":{\"cpu\":\"$CPU\"}}"
else RES="{\"limits\":{\"cpu\":\"$CPU\"}}"; fi
echo "→ Redimensionnement à chaud de $POD : CPU $CPU"
if ! kubectl -n lab patch pod "$POD" --subresource resize --type merge -p \
  "{\"spec\":{\"containers\":[{\"name\":\"app\",\"resources\":$RES}]}}"; then
  echo "⚠️  Redimensionnement à chaud refusé (version ou QoS) → utiliser : make demo-vertical SIZE=big"
  exit 1
fi
for _ in $(seq 15); do
  applied=$(kubectl -n lab get pod "$POD" -o jsonpath='{.status.containerStatuses[0].resources.limits.cpu}')
  [[ "$applied" == "$CPU" || "$applied" == "${CPU%m}" || "$applied" == "1" && "$CPU" == "1000m" ]] && break
  sleep 2
done
echo; echo "Après :"; show
echo
echo "👉 Même pod, même nom, REDÉMARRAGES = 0 : la limite CPU a changé sans interruption (cgroup modifié à chaud)."
