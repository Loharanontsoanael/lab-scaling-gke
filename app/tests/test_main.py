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


def test_browser_gets_dashboard_on_root_but_api_clients_get_json(client):
    html = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert html.headers["content-type"].startswith("text/html")
    assert "Projet GCP" in html.text
    assert client.get("/").headers["content-type"] == "application/json"
    assert client.get("/ui").status_code == 200


def test_every_response_identifies_the_pod(client):
    r = client.get("/cpu", params={"ms": 1})
    assert r.headers["X-Pod"] == main.settings.pod
    assert r.headers["X-Node"] == main.settings.node
    assert float(r.headers["X-Uptime"]) >= 0


def test_dashboard_requests_close_the_connection_to_spread_load(client):
    assert client.get("/", headers={"X-Lab-Fresh": "1"}).headers["connection"] == "close"
    assert "connection" not in client.get("/").headers


def test_info_exposes_limits(client):
    body = client.get("/info").json()
    assert body["version"] == main.VERSION
    assert {"allow_crash", "max_cpu_ms", "max_memory_mb"} <= body.keys()


def test_targeted_crash_is_refused_by_other_pods(client, monkeypatch):
    monkeypatch.setattr(main, "settings", main.Settings(pod="pod-a", allow_crash=True))
    r = client.get("/crash", params={"pod": "pod-b"})
    assert r.status_code == 409
    assert r.headers["X-Pod"] == "pod-a"


def test_targeted_crash_accepted_by_the_right_pod(client, monkeypatch):
    monkeypatch.setattr(main, "settings", main.Settings(pod="pod-a", allow_crash=True))
    timers = []
    monkeypatch.setattr(
        main.threading, "Timer", lambda *a, **k: timers.append(a) or type("T", (), {"start": lambda self: None})()
    )
    r = client.get("/crash", params={"pod": "pod-a"})
    assert r.status_code == 200 and r.json()["pod"] == "pod-a"
    assert timers, "le crash doit être programmé"


def test_gcp_metadata_is_empty_outside_google_cloud(monkeypatch):
    monkeypatch.setattr(main, "METADATA_URL", "http://127.0.0.1:9/computeMetadata/v1/")
    assert main.load_gcp_metadata(timeout=0.2) == {}


def test_gcp_metadata_read_from_metadata_server(monkeypatch):
    answers = {"project/project-id": "mon-projet", "instance/zone": "europe-west1-b"}
    monkeypatch.setattr(main, "_metadata", lambda path, timeout: answers.get(path))
    assert main.load_gcp_metadata() == {"project_id": "mon-projet", "zone": "europe-west1-b"}


def test_info_exposes_gcp_block(client):
    assert "gcp" in client.get("/info").json()
