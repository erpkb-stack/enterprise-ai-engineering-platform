"""Locust workload for AIECC (real requests; run against a non-production environment).

    locust -f scripts/loadtest/locustfile.py --host http://localhost:8100 --headless -u 50 -r 10 -t 2m \
        --csv scripts/loadtest/results/run

Mix: dashboards/listing (cheap reads), hybrid retrieval (CPU + DB), and bounded agent executions (expensive).
Requests are tagged with X-Request-ID; latency percentiles are reported by Locust and also visible in
AIECC's Performance page / Prometheus.
"""

import os
import random

from locust import HttpUser, between, task

PASSWORD = os.environ.get("AIECC_DEMO_PASSWORD", "Aiecc!Demo2026")
QUESTIONS = [
    "How quickly must a Sev2 incident be acknowledged?",
    "What is the refund window?",
    "What timeout does checkout use for the payment gateway?",
    "Which tools require human approval?",
]


class PlatformUser(HttpUser):
    wait_time = between(0.2, 1.0)
    _token: str | None = None  # shared per worker: realistic login rate (auth endpoint is rate limited to 20/min/IP)

    def on_start(self) -> None:
        if PlatformUser._token is None:
            r = self.client.post("/api/v1/auth/login", json={"email": "aieng@aiecc.local", "password": PASSWORD},
                                 name="auth/login")
            PlatformUser._token = r.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {PlatformUser._token}"
        agents = self.client.get("/api/v1/agents?limit=50", name="agents/list").json()["items"]
        self.research = next((a["id"] for a in agents if a["name"] == "Enterprise Research Agent"), None)

    @task(5)
    def list_agents(self) -> None:
        self.client.get("/api/v1/agents?limit=20", name="agents/list")

    @task(3)
    def dashboard(self) -> None:
        self.client.get("/api/v1/dashboard/overview", name="dashboard/overview")

    @task(4)
    def search(self) -> None:
        self.client.post("/api/v1/knowledge/search", json={"query": random.choice(QUESTIONS), "strategy": "hybrid_rerank"},
                         name="knowledge/search")

    @task(1)
    def execute_agent(self) -> None:
        if self.research:
            with self.client.post(f"/api/v1/agents/{self.research}/execute", json={"input": random.choice(QUESTIONS)},
                                  name="agents/execute", catch_response=True) as r:
                if r.status_code == 503:
                    r.success()  # backpressure is expected behaviour under saturation, not an error
