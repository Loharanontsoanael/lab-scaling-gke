# ADR 005 — Protocole expérimental

**Statut :** accepté

## Contexte
Un test de charge unique, lancé depuis un PC personnel, mesure surtout la connexion domestique et
le bruit. Les conclusions du rapport doivent être reproductibles et statistiquement défendables.

## Décision
1. **Générateur de charge externe au cluster** (VM `lab-loadgen`, même région) : il ne prend pas de CPU
   aux pods mesurés, et la latence réseau reste faible et stable.
2. **Charge en paliers** (`LoadTestShape`) ; le premier quart de chaque palier est écarté (régime transitoire).
3. **Modèle fermé** (temps de réflexion de 0,5 à 1,5 s) : il permet de vérifier la loi du temps de réponse
   interactif N = X·(R+Z).
4. **3 répétitions**, **entrelacées** entre configurations (A,B,C,A,B,C…) pour limiter le biais temporel ;
   résultats en moyenne ± IC 95 % (loi de Student).
5. **Double point de vue** : client (Locust : débit, p50/p95/p99, erreurs) et cluster (Prometheus :
   pods, nœuds, CPU, throttling, redémarrages).
6. **QoS Guaranteed** (requests = limits) pour les expériences à ressources fixes (E1, E3, E4).
7. Chaque run archive ses métadonnées (SHA git, config, versions, horodatages).

## Conséquences
- ✅ Résultats comparables et traçables ; graphiques et tableaux générés automatiquement.
- ❌ Plus long (E2 ≈ 3 × 22 min) et plus coûteux (VM de charge) qu'un test unique.
