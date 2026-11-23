#!/usr/bin/env bash
# UNE commande le jour du jury, dès que le PC a internet (wifi) :
#   scripts/jury.sh      → reconnecte kubectl, vérifie, lance Locust (localhost:8089),
#                          ouvre l'appli + Locust dans Firefox et la vue live dans Konsole
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/google-cloud-sdk/bin:$PATH"
set -a; source .env; set +a
ok() { printf '  \033[32m✔\033[0m %s\n' "$*"; }
ko() { printf '  \033[31m✘\033[0m %s\n' "$*"; }

echo "1) Internet…"
until curl -s -m5 -o /dev/null https://www.google.com; do echo "   pas encore d'internet, j'attends (wifi ?)"; sleep 3; done
ok "internet OK"

echo "2) Connexion au cluster GKE…"
gcloud container clusters get-credentials lab-cluster --zone "$ZONE" --project "$PROJECT_ID" >/dev/null 2>&1 \
  && ok "kubectl connecté" || ko "get-credentials a échoué (gcloud auth login ?)"
kubectl get nodes --no-headers 2>/dev/null | awk '{print "     serveur", $1, $2}'

echo "3) Application…"
IP=$(kubectl -n lab get svc webapp -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
URL="http://${IP:-34.78.213.10}"
curl -fs -m5 "$URL/health" >/dev/null && ok "l'appli répond : $URL" || ko "l'appli ne répond pas sur $URL"
kubectl -n lab get pods --no-headers 2>/dev/null | awk '{print "     pod", $1, $2, $3}'

echo "4) Locust (charge) sur http://localhost:8089…"
if ! curl -s -m2 -o /dev/null http://localhost:8089; then
  setsid nohup .venv/bin/locust -f loadtest/locustfile.py --host "$URL" --web-port 8089 >/tmp/locust-jury.log 2>&1 &
  for _ in $(seq 15); do curl -s -m1 -o /dev/null http://localhost:8089 && break; sleep 1; done
fi
curl -s -m2 -o /dev/null http://localhost:8089 && ok "Locust prêt : http://localhost:8089" || ko "Locust ne démarre pas (voir /tmp/locust-jury.log)"

echo "5) Ouverture des fenêtres…"
firefox --new-tab "$URL" --new-tab http://localhost:8089 >/dev/null 2>&1 &
konsole --workdir "$PWD" -e bash -c 'export PATH=$HOME/.local/google-cloud-sdk/bin:$PATH; watch -n2 -t -c scripts/demo-watch.sh' >/dev/null 2>&1 &
ok "Firefox (appli + Locust) et Konsole (vue live) ouverts"

cat <<TXT

🎯 Commandes de la démo (dans ce terminal) :
   Panne d'un SERVEUR (haute dispo) : .venv/bin/python chaos/chaos.py vm --repeat 1
   Panne d'un pod                   : .venv/bin/python chaos/chaos.py pod --repeat 1
   Scaling vertical                 : make demo-vertical SIZE=small   puis SIZE=big
   Retour horizontal (HPA)          : make deploy OVERLAY=horizontal
⚠️ Après le jury : make destroy
TXT
