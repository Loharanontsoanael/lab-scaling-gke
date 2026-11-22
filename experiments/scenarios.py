"""Définition des expériences (protocole du rapport).

Chaque scénario = un overlay Kustomize + une ou plusieurs configurations de pods
+ une charge en paliers. Chaque configuration est répétée `--repeat` fois.

Contraintes de dimensionnement : nœud e2-standard-2 ≈ 1,9 vCPU allouables, donc
aucun pod ne demande plus de 1,6 vCPU.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PodConfig:
    label: str
    replicas: int
    cpu: str | None = None  # requests = limits (QoS Guaranteed : mesures reproductibles)
    memory: str | None = None


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    question: str
    overlay: str
    configs: list[PodConfig]
    steps: str  # paliers d'utilisateurs Locust
    step_seconds: int = 120
    profile: str = "mixed"
    cooldown_seconds: int = 240  # observation du scale-down après la charge
    tags: list[str] = field(default_factory=list)


SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario(
            key="e1-saturation",
            title="E1 — Saturation d'un pod unique",
            question="À partir de quelle charge un pod sature-t-il ? (loi de Little, files d'attente)",
            overlay="fixed",
            configs=[PodConfig("1×500m", 1, "500m", "256Mi")],
            steps="5,10,20,30,40,60,80",
            step_seconds=90,
            profile="cpu",
            cooldown_seconds=30,
        ),
        Scenario(
            key="e2-horizontal-hpa",
            title="E2 — Scaling horizontal automatique (HPA + Cluster Autoscaler)",
            question="Combien de temps le HPA met-il à réagir, et la latence revient-elle sous le SLO ?",
            overlay="horizontal",
            configs=[PodConfig("HPA 3→20", 3)],
            steps="50,100,200,300,400",
            step_seconds=180,
            profile="mixed",
            cooldown_seconds=420,
        ),
        Scenario(
            key="e3-scalability-usl",
            title="E3 — Scalabilité : débit maximal selon le nombre de pods (USL)",
            question="Le débit croît-il linéairement avec N ? Quels coefficients α (contention) et β (cohérence) ?",
            overlay="fixed",
            configs=[PodConfig(f"{n} pods", n, "500m", "256Mi") for n in (1, 2, 4, 6, 8, 12)],
            steps="300",
            step_seconds=150,
            profile="cpu",
            cooldown_seconds=30,
        ),
        Scenario(
            key="e4-vertical-vs-horizontal",
            title="E4 — Vertical vs horizontal à budget CPU égal (1,6 vCPU)",
            question="Vaut-il mieux 1 gros pod ou plusieurs petits ? (effet du GIL Python)",
            overlay="fixed",
            configs=[
                PodConfig("1×1600m", 1, "1600m", "1Gi"),
                PodConfig("2×800m", 2, "800m", "512Mi"),
                PodConfig("4×400m", 4, "400m", "256Mi"),
            ],
            steps="50,100,200",
            step_seconds=120,
            profile="cpu",
            cooldown_seconds=30,
        ),
        Scenario(
            key="e5-vertical-vpa",
            title="E5 — Scaling vertical automatique (VPA InPlaceOrRecreate)",
            question="Le VPA ajuste-t-il les ressources, en combien de temps, et avec quelle perturbation ?",
            overlay="vertical",
            configs=[PodConfig("VPA", 3)],
            steps="50,100,150,150,150",
            step_seconds=240,
            profile="mixed",
            cooldown_seconds=120,
        ),
    ]
}
