# Lab Scaling GKE

**Charge, scaling horizontal/vertical et haute disponibilité sur Kubernetes (GKE)**
Projet de Master 2 — Grid & Cluster Computing

Une API web minimale (FastAPI, conteneurisée avec Docker) est déployée sur un cluster GKE. On la soumet
à des charges contrôlées pour **mesurer** l'impact sur le CPU, la RAM et la latence, puis comparer
quantitativement le **scaling horizontal** (HPA + Cluster Autoscaler), le **scaling vertical**
(VPA, redimensionnement à budget égal) et la **résilience** face aux pannes (pod, nœud, VM).

> 🎤 **Démo jury : voir [`docs/DEMO.md`](docs/DEMO.md)** — `make demo-up` puis `make demo-check`.

## Architecture

```
                         ┌──────────────────────── VPC lab-vpc (europe-west1) ───────────────────────┐
 GitHub Actions ──OIDC──►│  Artifact Registry          Cluster GKE Standard (nœuds privés, Cloud NAT) │
 (CI + déploiement)      │                             ┌──────────┬──────────┬──────────┐            │
                         │  VM lab-loadgen ──HTTP──►LB─►│ nœud 1   │ nœud 2   │ nœud 3   │ … (2→5)    │
                         │  (Locust, SSH via IAP)       │ pods app │ pods app │ pods app │ (3→20)     │
                         │                              └──────────┴──────────┴──────────┘            │
                         │                              HPA · VPA · PDB · NetworkPolicy               │
                         │                              Prometheus + Grafana (non exposé)             │
                         └────────────────────────────────────────────────────────────────────────────┘
```

| Dossier | Contenu |
|---|---|
| `app/` | API FastAPI, tests pytest, Dockerfile multi-stage non-root |
| `infra/terraform/` | Toute l'infra GCP en code : VPC, NAT, GKE, node pool, registre, WIF GitHub, VM de charge, budget |
| `deploy/base`, `deploy/overlays/` | Manifestes Kustomize ; overlays `horizontal` (HPA), `vertical` (VPA), `fixed` (sans autoscaler) |
| `monitoring/` | kube-prometheus-stack (Helm) + dashboard Grafana généré |
| `loadtest/` | Scénarios Locust (profils `mixed`/`cpu`/`memory`, charge en paliers) |
| `experiments/` | Orchestrateur d'expériences reproductibles + analyse statistique (IC 95 %, USL, Little) |
| `chaos/` | Injection de pannes et mesure de disponibilité / MTTR |
| `docs/` | Décisions d'architecture (ADR), plan du rapport |
| `.github/workflows/` | CI (lint, tests, kubeconform, terraform validate, scan Trivy) + déploiement par WIF |

## API

| Endpoint | Rôle |
|---|---|
| `GET /` | Pod et nœud qui ont répondu (visualise la répartition de charge) |
| `GET /cpu?ms=50` | Charge CPU pure pendant `ms` ms |
| `GET /memory?mb=20&seconds=1` | Alloue réellement `mb` Mo pendant `seconds` s |
| `GET /crash` | Termine le processus (expériences de chaos) |
| `GET /health` · `GET /ready` | Sondes liveness · readiness (readiness = 503 pendant l'arrêt propre) |
| `GET /metrics` | Métriques Prometheus (débit, latence par histogramme, mémoire allouée) |

## Démarrage

### 1. Outils (CachyOS / Arch)
```bash
sudo pacman -S --needed docker docker-buildx terraform helm kubectl openssl
sudo systemctl enable --now docker && sudo usermod -aG docker $USER   # puis se reconnecter
gcloud components install gke-gcloud-auth-plugin
```

### 2. Connexion et configuration
```bash
gcloud auth login
gcloud auth application-default login         # identifiants utilisés par Terraform
cp .env.example .env                          # renseigner PROJECT_ID
make venv && make test                        # environnement Python + tests
```

### 3. Infrastructure et déploiement
```bash
make tf-bootstrap tf-init     # une seule fois : bucket d'état Terraform
make infra                    # ≈ 10 min : VPC, GKE, registre, WIF, VM de charge
make image                    # build + push (CLOUD=1 pour builder sur Cloud Build)
make deploy                   # overlay horizontal par défaut → affiche l'URL
make monitoring               # Prometheus + Grafana
make grafana                  # http://localhost:3000 (mot de passe affiché)
```

### 4. Expériences (protocole du rapport)
La VM de charge est désactivée par défaut (inutile pour la démo) ; pour les expériences :
```bash
make infra TF_ARGS="-var enable_loadgen=true"
make exp-list
make exp E=e1-saturation              # saturation d'un pod
make exp E=e2-horizontal-hpa          # réactivité du HPA + Cluster Autoscaler
make exp E=e3-scalability-usl         # débit vs N pods → ajustement USL
make exp E=e4-vertical-vs-horizontal  # 1×1600m vs 2×800m vs 4×400m
make exp E=e5-vertical-vpa            # VPA InPlaceOrRecreate
make chaos MODE=pod|node|vm           # disponibilité et MTTR
make analyze DIR=results/<scénario>/<horodatage>   # → summary.md + figures/
```
Chaque expérience est répétée 3 fois (ordre entrelacé), la charge vient de la VM `lab-loadgen`
(hors du cluster, même région), et les métriques Prometheus sont archivées avec les CSV Locust.

### 5. Démo en direct
```bash
make deploy OVERLAY=horizontal && make grafana     # terminal 1 : dashboard « Scaling Lab — GKE »
watch -n2 make -s status                           # terminal 2
make loadtest-ui                                   # terminal 3 : http://localhost:8089
```

### 6. ⚠️ Toujours détruire après une session
```bash
make destroy
```
Ordre de grandeur : 3 × e2-standard-2 + VM e2-standard-4 + NAT + LB ≈ **0,45 $/h**
(le plan de contrôle d'un cluster zonal est couvert par le crédit gratuit GKE).

## CI/CD
- **CI** (chaque push/PR) : ruff, pytest, `kustomize build` + kubeconform, `terraform fmt/validate`,
  build Docker + test de fumée + scan Trivy.
- **Deploy** (manuel) : authentification **Workload Identity Federation** (aucune clé JSON stockée),
  image taguée par SHA, `kubectl apply -k --prune`. Configuration : voir l'en-tête de `deploy.yml`.

## Bonnes pratiques appliquées
Voir [`docs/adr/`](docs/adr/) pour la justification de chaque choix.
Infrastructure as Code · état distant versionné · nœuds privés + NAT · SSH via IAP · comptes de service
dédiés à moindre privilège · Workload Identity · Pod Security `restricted` · NetworkPolicy deny-by-default ·
conteneur non-root en lecture seule · sondes startup/readiness/liveness distinctes · arrêt propre ·
PDB · topology spread · HPA et VPA jamais sur la même métrique · tags d'image immuables ·
Grafana non exposé · alerte budget · Dependabot.
