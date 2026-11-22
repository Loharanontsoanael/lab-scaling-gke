# Lab Scaling GKE — point d'entrée unique.  `make help` pour la liste des commandes.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

-include .env
PROJECT_ID ?= $(error PROJECT_ID manquant : copie .env.example en .env)
REGION     ?= europe-west1
ZONE       ?= europe-west1-b
CLUSTER    ?= lab-cluster
OVERLAY    ?= horizontal
# Options Terraform, ex. : make infra TF_ARGS="-var enable_loadgen=true -var enable_github_wif=true"
TF_ARGS    ?=

TF         := terraform -chdir=infra/terraform
VENV       := .venv
PY         := $(CURDIR)/$(VENV)/bin/python
IMAGE_NAME  = $(REGION)-docker.pkg.dev/$(PROJECT_ID)/lab/webapp
TAG        ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)$(shell git diff --quiet HEAD -- app 2>/dev/null || echo -dirty)
PRUNE_FLAGS = --prune -l app.kubernetes.io/part-of=lab-scaling-gke \
              --prune-allowlist=autoscaling/v2/HorizontalPodAutoscaler \
              --prune-allowlist=autoscaling.k8s.io/v1/VerticalPodAutoscaler

export ZONE

##@ Développement local
venv: ## Crée l'environnement Python (app + tests + expériences)
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -r app/requirements-dev.txt -r experiments/requirements.txt

test: ## Lint (ruff) + tests unitaires (pytest)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/pytest -q

run-local: ## Lance l'API en local dans Docker sur :8000
	docker build -t scaling-lab:local app
	docker run --rm -p 8000:8000 -e ALLOW_CRASH=1 --read-only --user 10001 scaling-lab:local

loadtest-ui: ## Interface Locust (http://localhost:8089) ; HOST=http://... (défaut : l'IP du cluster)
	$(VENV)/bin/locust -f loadtest/locustfile.py --host $${HOST:-http://$$(kubectl -n lab get svc webapp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')}

##@ Infrastructure (Terraform)
tf-bootstrap: ## Une seule fois : API d'amorçage + bucket GCS de l'état Terraform (versionné)
	gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project $(PROJECT_ID)
	gcloud storage buckets describe gs://$(PROJECT_ID)-tfstate >/dev/null 2>&1 || \
	  gcloud storage buckets create gs://$(PROJECT_ID)-tfstate --project $(PROJECT_ID) --location $(REGION) \
	    --uniform-bucket-level-access --public-access-prevention
	gcloud storage buckets update gs://$(PROJECT_ID)-tfstate --versioning

tf-init: ## terraform init avec l'état distant
	$(TF) init -input=false -reconfigure -backend-config="bucket=$(PROJECT_ID)-tfstate"

infra: ## Crée/met à jour toute l'infra (VPC, GKE, registre, WIF, VM de charge, budget)
	$(TF) plan -input=false -var project_id=$(PROJECT_ID) $(TF_ARGS) -out tfplan
	$(TF) apply tfplan
	$(MAKE) creds

creds: ## Connecte kubectl au cluster
	gcloud container clusters get-credentials $(CLUSTER) --zone $(ZONE) --project $(PROJECT_ID)

##@ Application
image: ## Construit et pousse l'image (tag = SHA git) ; Cloud Build si Docker local indisponible (ou CLOUD=1)
	@if [[ "$(CLOUD)" != 1 ]] && docker info >/dev/null 2>&1; then \
	  gcloud auth configure-docker $(REGION)-docker.pkg.dev --quiet && \
	  docker build -t $(IMAGE_NAME):$(TAG) app && docker push $(IMAGE_NAME):$(TAG); \
	else \
	  echo "→ Build sur Cloud Build (Docker local non utilisé)"; \
	  gcloud builds submit app --tag $(IMAGE_NAME):$(TAG) --project $(PROJECT_ID); \
	fi

deploy: ## Déploie l'overlay OVERLAY=horizontal|vertical|fixed (supprime le HPA/VPA de l'ancien mode)
	@mkdir -p deploy/overlays/_current
	@printf '%s\n' 'apiVersion: kustomize.config.k8s.io/v1beta1' 'kind: Kustomization' \
	  'resources: [../$(OVERLAY)]' 'images:' '  - name: scaling-lab' \
	  '    newName: $(IMAGE_NAME)' '    newTag: "$(TAG)"' > deploy/overlays/_current/kustomization.yaml
	kubectl apply -k deploy/overlays/_current $(PRUNE_FLAGS)
	@kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1 && \
	  kubectl apply -f deploy/monitoring/servicemonitor.yaml || true
	kubectl -n lab rollout status deployment/webapp --timeout=300s
	@echo "✅ $(OVERLAY) déployé → http://$$(kubectl -n lab get svc webapp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"

url: ## Affiche l'URL publique de l'application
	@echo "http://$$(kubectl -n lab get svc webapp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"

status: ## État du cluster : nœuds, pods, autoscalers
	kubectl get nodes -o wide
	kubectl -n lab get deploy,hpa,vpa,pdb,pods -o wide

##@ Monitoring
monitoring: ## Installe Prometheus + Grafana + dashboard
	kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1 || \
	  kubectl -n monitoring create secret generic grafana-admin \
	    --from-literal=admin-user=admin --from-literal=admin-password="$$(openssl rand -base64 18)"
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
	  -n monitoring -f monitoring/values.yaml --wait --timeout 10m
	python3 monitoring/make_dashboard.py
	kubectl apply -f monitoring/dashboard-configmap.yaml -f deploy/monitoring/servicemonitor.yaml

grafana: ## Ouvre Grafana sur http://localhost:3000 (port-forward)
	@echo "Utilisateur : admin   Mot de passe : $$(kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)"
	kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

##@ Expériences
exp-list: ## Liste les scénarios d'expérience
	$(PY) experiments/run_experiment.py --list

exp: ## Lance une expérience : make exp E=e2-horizontal-hpa REPEAT=3 RUNNER=loadgen|local
	cd experiments && $(PY) run_experiment.py $(E) --repeat $${REPEAT:-3} --runner $${RUNNER:-loadgen}

analyze: ## Analyse : make analyze DIR=results/e2-horizontal-hpa/2026...
	cd experiments && $(PY) analyze.py $(abspath $(DIR))

chaos: ## Panne : make chaos MODE=pod|node|vm REPEAT=3
	$(PY) chaos/chaos.py $(MODE) --repeat $${REPEAT:-3}

##@ Démo jury
demo-up: ## TOUT lancer d'un coup (≈ 30-40 min) : bucket, infra, image, app, monitoring
	@start=$$(date +%s); \
	$(MAKE) -s tf-bootstrap tf-init infra image deploy monitoring && \
	echo "🎉 Plateforme prête en $$(( ($$(date +%s) - start) / 60 )) min → make demo-check"

demo-check: ## Vérifie que tout est prêt avant le jury
	@scripts/demo-check.sh

demo-watch: ## Vue live : pods par nœud, HPA, nœuds (à garder ouvert pendant la démo)
	@watch -n2 -t -c scripts/demo-watch.sh

demo-vertical: ## Scaling vertical live : SIZE=small (250m) | big (1 CPU) sur 1 seul pod
	@scripts/demo-vertical.sh $${SIZE:-small}

demo-resize: ## Redimensionne le pod À CHAUD (sans redémarrage) : CPU=1000m
	@scripts/demo-resize.sh $${CPU:-1000m}

demo-reset: ## Revient au mode horizontal (HPA) après la démo verticale
	$(MAKE) -s deploy OVERLAY=horizontal

##@ Nettoyage
destroy: ## ⚠️ Supprime TOUT (load balancers puis infra) pour arrêter la facturation
	@read -rp "Détruire toute l'infrastructure de $(PROJECT_ID) ? (oui/non) " ok && [[ $$ok == oui ]]
	-kubectl delete namespace lab monitoring --wait=true --timeout=300s
	$(TF) destroy -input=false -var project_id=$(PROJECT_ID) $(TF_ARGS) -auto-approve

help:
	@awk 'BEGIN {FS = ":.*##"} /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
	  /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: venv test run-local loadtest-ui tf-bootstrap tf-init infra creds image deploy url status \
        monitoring grafana exp-list exp analyze chaos destroy help \
        demo-up demo-check demo-watch demo-vertical demo-resize demo-reset
