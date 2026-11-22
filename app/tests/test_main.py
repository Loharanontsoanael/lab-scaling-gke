import main
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_root_reports_pod_and_node(client):
    body = client.get("/").json()
    assert {"pod", "node", "uptime_s"} <= body.keys()


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").status_code == 200


def test_ready_returns_503_while_shutting_down(client, monkeypatch):
    monkeypatch.setitem(main._state, "shutting_down", True)
    assert client.get("/ready").status_code == 503


def test_cpu_burns_for_requested_time(client):
    body = client.get("/cpu", params={"ms": 50}).json()
    assert body["cpu_ms"] == 50
    assert body["operations"] > 0


@pytest.mark.parametrize("params", [{"ms": 0}, {"ms": 10_000}])
def test_cpu_rejects_out_of_range(client, params):
    assert client.get("/cpu", params=params).status_code == 422


def test_memory_allocates_then_releases(client):
    body = client.get("/memory", params={"mb": 5, "seconds": 0}).json()
    assert body["allocated_mb"] == 5
    assert main._state["allocated"] == 0


def test_memory_rejects_too_large(client):
    assert client.get("/memory", params={"mb": 100_000}).status_code == 422


def test_crash_disabled_by_default(client):
    assert client.get("/crash").status_code == 403


def test_metrics_exposed(client):
    client.get("/")
    text = client.get("/metrics").text
    assert "http_requests_total" in text
    assert "lab_allocated_memory_mb" in text
