# 🎤 Démo jury — déroulé complet

> Projet GCP : `lab-scaling-gke-a43f3a` · zone `europe-west1-b` · tout se lance depuis `~/Projects/lab-scaling-gke`

---

## ⏰ Le matin du jury (≈ 1 h 15 au total)

| Quand | Action | Durée |
|---|---|---|
| **T − 2 h** | Activer la facturation (voir ci-dessous) | 10 min |
| **T − 1 h 50** | `make demo-up` (puis on attend, sans rien toucher) | 30–40 min |
| **T − 1 h 10** | `make demo-check` → tout doit être ✔ | 1 min |
| **T − 1 h** | **Répétition** : faire les 3 démos une fois, chronomètre en main | 25 min |
| **T − 30 min** | `make demo-reset`, ouvrir les fenêtres (disposition ci-dessous) | 5 min |
| **Après le jury** | **`make destroy`** (sinon le cluster continue de coûter) | 10 min |

### 1. Facturation (T − 2 h)
1. https://console.cloud.google.com/billing → **Créer un compte de facturation** (essai gratuit : 300 $ de crédit, la carte sert seulement à vérifier l'identité).
2. Associer le projet : https://console.cloud.google.com/billing/projects → `lab-scaling-gke-a43f3a` → *Modifier la facturation*.
3. Vérifier :
   ```bash
   gcloud billing projects describe lab-scaling-gke-a43f3a   # billingEnabled: true
   ```

### 2. Tout lancer (T − 1 h 50)
```bash
cd ~/Projects/lab-scaling-gke
make demo-up
```
Ce qui se passe : API + bucket d'état → Terraform (VPC, NAT, GKE ≈ 10 min) → image (Cloud Build) →
application → Prometheus + Grafana. Ça se termine par `🎉 Plateforme prête en N min`.
En cas d'erreur, voir **Dépannage**. On peut relancer `make demo-up` sans risque : chaque étape reprend là où elle s'est arrêtée.

### 3. Vérifier (T − 1 h 10)
```bash
make demo-check        # 11 vérifications, toutes ✔
```

---

## 🖥️ Disposition de l'écran pendant la démo

| Fenêtre | Commande | Contenu |
|---|---|---|
| **Terminal 1** (grand) | `make demo-watch` | Nœuds, pods (● prêt / ○ démarrage), HPA, CPU réel |
| **Terminal 2** | `make grafana` | Port-forward Grafana (mot de passe affiché) |
| **Terminal 3** | `make loadtest-ui` | Locust → navigateur http://localhost:8089 |
| **Terminal 4** | libre | Pour taper les commandes de démo |
| **Navigateur** | http://localhost:3000 → dashboard **« Scaling Lab — GKE »** | Courbes en temps réel |
| **Navigateur** | http://localhost:8089 | Locust |

Astuce : dans Konsole, Ctrl+Shift+( et Ctrl+Shift+) découpent la fenêtre en panneaux.

---

## 🎬 Script de la démo (≈ 15 min)

### 0. Introduction (2 min) — *README.md, schéma d'architecture*
> « L'application est une petite API FastAPI dans un conteneur Docker. Elle tourne sur un cluster
> Kubernetes managé par Google (GKE) : 3 serveurs au départ. Toute l'infrastructure est décrite en code
> avec Terraform et peut être recréée en une commande. »

Montrer rapidement : `infra/terraform/gke.tf` (autoscaling 2→5 nœuds), `deploy/overlays/horizontal/hpa.yaml`
(cible 60 % CPU, 3→20 pods), `docs/adr/` (les choix justifiés).

### 1. Répartition de charge (1 min) — *terminal 4*
```bash
for i in $(seq 10); do curl -s -H 'Connection: close' $(make -s url)/ ; echo; done
```
> « Une seule IP publique : le load balancer GCP répartit les requêtes. Le champ `pod` change à chaque fois,
> et les 3 pods sont sur 3 nœuds différents (topologySpreadConstraints). »

### 2. Charge et scaling horizontal (5 min) — *Locust + demo-watch + Grafana*
Dans Locust : **150 utilisateurs, spawn rate 10** → *Start*.
> « Chaque utilisateur envoie une requête toutes les ~1 s, et la moitié consomme 50 ms de CPU. »

À commenter pendant que ça monte :
1. **Grafana « CPU par pod »** monte vers 0,5 cœur (= la limite) → **throttling CPU** et latence p95 en hausse.
2. **demo-watch** : la ligne HPA passe au rouge (> 60 %), puis apparaissent des pods ○ → ●.
3. Expliquer la formule du HPA :
   > « replicas = ⌈ replicas actuels × CPU mesuré / CPU cible ⌉. Par exemple 3 × 120 % / 60 % = 6 pods.
   > C'est une boucle de contrôle qui tourne toutes les 15 s. »
4. **Latence p95 qui redescend** quand les nouveaux pods sont prêts.

Ensuite **300 utilisateurs** (*Edit* dans Locust) :
> « Les nœuds sont pleins : des pods restent *en attente* (⏳ dans demo-watch). Le **Cluster Autoscaler**
> crée une nouvelle VM, et environ 1 à 2 minutes plus tard elle apparaît comme 4e nœud. »

→ *Stop* Locust. Mentionner : « le retour à 3 pods prend quelques minutes, c'est voulu (fenêtre de
stabilisation de 120 s, pour éviter l'oscillation). »

### 3. Haute disponibilité (4 min) — *terminal 4*
**Panne d'un pod** (≈ 1 min) :
```bash
make chaos MODE=pod REPEAT=1
```
> « Une sonde envoie 10 requêtes par seconde, chacune sur une nouvelle connexion. Je tue le processus d'un pod.
> Kubernetes le détecte et le redémarre, et pendant ce temps le load balancer envoie le trafic aux autres pods. »
→ Montrer le bilan : disponibilité ≈ 100 %, MTTR en secondes.

**Panne d'un serveur** (≈ 2–3 min) :
```bash
make chaos MODE=node REPEAT=1
```
> « Je retire un serveur entier (drain, comme pour une maintenance). Les pods sont recréés sur les autres
> nœuds, et le PodDisruptionBudget garantit qu'il n'y a jamais plus d'un pod indisponible à la fois. »

*(Optionnel, s'il reste du temps : `make chaos MODE=vm REPEAT=1` supprime brutalement une VM : quelques erreurs
pendant ~40 s de détection, puis GKE recrée la VM. Compter 3 à 5 min.)*

### 4. Scaling vertical (3 min) — *terminal 4 + Locust*
```bash
make demo-vertical SIZE=small        # 1 seul pod, 0,25 CPU
```
Locust : **20 utilisateurs** → p95 élevée, throttling CPU important dans Grafana.
```bash
make demo-resize CPU=1000m           # 1 CPU, À CHAUD
```
> « Même pod, même nom, **0 redémarrage** : Kubernetes modifie le cgroup du conteneur en cours d'exécution
> (in-place resize). La latence chute tout de suite. »

Le point important pour le jury :
> « Mais le vertical a une limite : mon application est **un seul processus Python** (GIL), donc au-delà d'un
> CPU elle ne va pas plus vite. Et c'est toujours une seule machine : si elle tombe, tout tombe.
> Le horizontal n'a pas ces deux limites. »

```bash
make demo-reset                      # retour au mode horizontal
```

### 5. Conclusion (1 min)
- Horizontal : élastique et résilient, mais l'application doit être sans état et il faut un délai de réaction (~1 min, plus 1–2 min si un nœud est ajouté).
- Vertical : simple, à chaud, mais plafonné et sans redondance.
- Bonnes pratiques : IaC, moindre privilège, nœuds privés, NetworkPolicy, sondes, PDB, CI/CD sans clé (`docs/adr/`).
- Suite : expériences automatisées et répétées (`make exp …`) avec analyse statistique (IC 95 %, loi de Little, USL) pour le rapport.

---

## ❓ Questions probables du jury

| Question | Réponse courte |
|---|---|
| Pourquoi pas HPA + VPA ensemble ? | Ils agiraient sur la même métrique et se contrediraient (le VPA augmente les requests → le % baisse → le HPA retire des pods…). Ici le VPA est en mode *Off* (recommandations) avec le HPA. |
| Pourquoi 60 % de cible ? | Garder une marge pour absorber le pic pendant la minute qu'il faut pour ajouter des pods. |
| Cluster ou grid ? | Cluster : machines homogènes, même datacenter, un seul ordonnanceur. Le grid fédère des ressources hétérogènes de plusieurs organisations (WLCG, BOINC). Le multi-cluster serait un pas vers le grid. |
| Que se passe-t-il si toute la zone tombe ? | Le cluster est zonal : ce n'est pas couvert. En production, on utiliserait un cluster régional sur 3 zones (voir ADR 001). |
| Pourquoi GKE Standard et pas Autopilot ? | Pour voir et casser des nœuds pendant la démo ; Autopilot masque les nœuds. |
| Requests vs limits ? | Requests = réservation (placement des pods, base du % du HPA). Limits = plafond : CPU bridé (throttling), RAM dépassée = pod tué (OOMKill). |
| Comment est géré l'accès GitHub → GCP ? | Workload Identity Federation : jetons OIDC de courte durée, aucune clé stockée. |
| Coût ? | ≈ 0,30 $/h pour la démo ; tout est détruit après avec `make destroy`. |

---

## 🛠️ Dépannage

| Symptôme | Solution |
|---|---|
| `billing account ... not found` / `BILLING_DISABLED` | La facturation n'est pas liée au projet (étape 1), ou il faut attendre 1–2 min que ce soit propagé, puis relancer `make demo-up`. |
| `API ... has not been used ... or it is disabled` | Attendre 1–2 min (propagation) puis relancer `make demo-up`. |
| Terraform : `Error acquiring the state lock` | Un `make` précédent a été interrompu : `terraform -chdir=infra/terraform force-unlock <ID affiché>`. |
| `gcloud builds submit` refusé (permission) | `gcloud projects add-iam-policy-binding lab-scaling-gke-a43f3a --member=serviceAccount:$(gcloud projects describe lab-scaling-gke-a43f3a --format='value(projectNumber)')-compute@developer.gserviceaccount.com --role=roles/artifactregistry.writer` puis `make image deploy`. |
| Pods en `ImagePullBackOff` | L'image n'existe pas avec ce tag : `make image deploy`. |
| IP du load balancer `<pending>` | Normal pendant 1–2 min. |
| HPA `<unknown>` dans demo-check | metrics-server démarre : attendre 1–2 min. |
| Grafana sans données | `kubectl apply -f deploy/monitoring/servicemonitor.yaml`, attendre 30 s. |
| `make demo-resize` refusé | Utiliser `make demo-vertical SIZE=big` (même message, mais avec redémarrage du pod). |
| **GCP ne marche pas du tout** | **Plan B local**, ci-dessous. |

### Plan B — démo 100 % locale (Docker)
```bash
DOCKER="sudo docker" demo-local/plan-b.sh up     # 3 répliques + LB → http://localhost:8080
make loadtest-ui HOST=http://localhost:8080      # charge
DOCKER="sudo docker" demo-local/plan-b.sh scale 6   # horizontal
DOCKER="sudo docker" demo-local/plan-b.sh cpu 1     # vertical à chaud
demo-local/plan-b.sh probe                          # sonde (autre terminal)
DOCKER="sudo docker" demo-local/plan-b.sh kill      # panne → redémarrage auto
DOCKER="sudo docker" demo-local/plan-b.sh down
```
(Sans `sudo` si ton utilisateur est dans le groupe `docker`.) Montre les mêmes concepts, sans autoscaling automatique.

---

## 🧹 Après le jury — obligatoire
```bash
make destroy      # répondre « oui »
gcloud compute instances list --project lab-scaling-gke-a43f3a   # doit être vide
```
