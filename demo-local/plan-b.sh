#!/usr/bin/env bash
# PLAN B — démo 100 % locale si GCP est indisponible le jour du jury.
# Docker seul (pas besoin de Compose) : N répliques de l'app + un load balancer nginx.
#
#   demo-local/plan-b.sh up          3 répliques + LB sur http://localhost:8080
#   demo-local/plan-b.sh scale 6     scaling horizontal : passe à 6 répliques
#   demo-local/plan-b.sh cpu 1       scaling vertical à chaud : 1 CPU par réplique (docker update)
#   demo-local/plan-b.sh kill        panne : tue une réplique (redémarrée automatiquement)
#   demo-local/plan-b.sh probe       sonde live (Ctrl+C pour arrêter) : qui répond, et les erreurs
#   demo-local/plan-b.sh status      état + consommation (docker stats)
#   demo-local/plan-b.sh down        tout supprimer
#
# Si ton utilisateur n'est pas dans le groupe docker : DOCKER="sudo docker" demo-local/plan-b.sh up
set -euo pipefail
SELF=$(realpath "$0")
cd "$(dirname "$SELF")"
DOCKER=${DOCKER:-docker}
NET=lab-local
IMG=scaling-lab:local
export DOCKER_BUILDKIT=0 # fonctionne même sans le plugin buildx

replicas() { $DOCKER ps --filter "name=^lab-app-" --format '{{.Names}}' | sort -V; }

start_replica() {
  $DOCKER run -d --name "lab-app-$1" --network $NET --network-alias app \
    --cpus 0.5 --memory 256m --read-only --restart always \
    -e ALLOW_CRASH=1 -e POD_NAME="lab-app-$1" -e NODE_NAME="laptop" $IMG >/dev/null
  echo "  + lab-app-$1"
}

case "${1:-status}" in
  up)
    $DOCKER network inspect $NET >/dev/null 2>&1 || $DOCKER network create $NET >/dev/null
    echo "→ Build de l'image"; $DOCKER build -q -t $IMG ../app
    for i in 1 2 3; do start_replica $i; done
    $DOCKER run -d --name lab-lb --network $NET -p 8080:80 --restart always \
      -v "$PWD/nginx.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.27-alpine >/dev/null
    sleep 3
    echo "✅ http://localhost:8080  — Locust : make loadtest-ui HOST=http://localhost:8080"
    ;;
  scale)
    want=${2:?nombre de répliques}; have=$(replicas | wc -l)
    for ((i = have + 1; i <= want; i++)); do start_replica $i; done
    for ((i = have; i > want; i--)); do $DOCKER rm -f "lab-app-$i" >/dev/null && echo "  - lab-app-$i"; done
    ;;
  cpu)
    for c in $(replicas); do $DOCKER update --cpus "${2:?nb de CPU}" "$c" >/dev/null && echo "  $c → ${2} CPU"; done
    ;;
  kill)
    victim=$(replicas | head -1)
    echo "💥 docker kill $victim"; $DOCKER kill "$victim" >/dev/null
    echo "   (restart=always : Docker le relance ; nginx bascule sur les autres répliques)"
    ;;
  probe)
    while true; do
      r=$(curl -s --max-time 2 -w ' %{http_code}' localhost:8080/ || echo ' 000')
      code=${r##* }; pod=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pod"])' "${r% *}" 2>/dev/null || echo -)
      if [[ $code == 200 ]]; then printf '\e[32m✔\e[0m %s %s\n' "$(date +%T)" "$pod"; else printf '\e[31m✘ %s HTTP %s\e[0m\n' "$(date +%T)" "$code"; fi
      sleep 0.2
    done
    ;;
  status)
    $DOCKER ps --filter "name=^lab-" --format 'table {{.Names}}\t{{.Status}}'
    $DOCKER stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' $(replicas)
    ;;
  down)
    $DOCKER rm -f lab-lb $(replicas) >/dev/null 2>&1 || true
    $DOCKER network rm $NET >/dev/null 2>&1 || true
    echo "✅ supprimé"
    ;;
  *) sed -n '2,14p' "$SELF"; exit 1 ;;
esac
