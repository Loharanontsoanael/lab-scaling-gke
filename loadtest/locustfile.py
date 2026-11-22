"""Scénarios de charge Locust.

Interface web (démo)  : locust -f loadtest/locustfile.py --host http://<IP>
Mode expérience       : piloté par experiments/run_experiment.py (headless + CSV)

Variables d'environnement :
  LOAD_PROFILE  mixed (défaut) | cpu | memory     → type de requêtes
  STEPS         ex. "50,100,200,400"              → active une charge en paliers (LoadTestShape)
  STEP_SECONDS  durée de chaque palier (défaut 120 s : laisse le temps au HPA de réagir)
  SPAWN_RATE    utilisateurs ajoutés par seconde (défaut 20)
"""

import os

from locust import HttpUser, LoadTestShape, between, task

PROFILE = os.getenv("LOAD_PROFILE", "mixed")
WEIGHTS = {
    "mixed": {"home": 3, "cpu": 5, "memory": 2},
    "cpu": {"home": 0, "cpu": 1, "memory": 0},
    "memory": {"home": 0, "cpu": 0, "memory": 1},
}[PROFILE]


class WebUser(HttpUser):
    # Modèle « fermé » : chaque utilisateur attend 0,5–1,5 s entre deux requêtes
    wait_time = between(0.5, 1.5)

    @task(WEIGHTS["home"])
    def home(self):
        self.client.get("/", name="/")

    @task(WEIGHTS["cpu"])
    def cpu(self):
        self.client.get("/cpu?ms=50", name="/cpu")

    @task(WEIGHTS["memory"])
    def memory(self):
        self.client.get("/memory?mb=20&seconds=1", name="/memory")


if os.getenv("STEPS"):

    class StepLoad(LoadTestShape):
        """Charge en paliers : chaque palier dure STEP_SECONDS, puis la charge s'arrête."""

        steps = [int(s) for s in os.environ["STEPS"].split(",")]
        step_seconds = int(os.getenv("STEP_SECONDS", "120"))
        spawn_rate = float(os.getenv("SPAWN_RATE", "20"))

        def tick(self):
            idx = int(self.get_run_time() // self.step_seconds)
            if idx >= len(self.steps):
                return None
            return self.steps[idx], self.spawn_rate
