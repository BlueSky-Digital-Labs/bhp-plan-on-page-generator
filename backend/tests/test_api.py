def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_status_reports_environment(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "test"}


def test_every_response_has_correlation_and_security_headers(client):
    response = client.get("/api/health")
    assert len(response.headers["x-correlation-id"]) == 32
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_inbound_correlation_id_is_not_trusted(client):
    response = client.get("/api/health", headers={"X-Correlation-ID": "attacker-chosen"})
    assert response.headers["x-correlation-id"] != "attacker-chosen"


def test_docs_available_outside_deployed(client):
    assert client.get("/api/docs").status_code == 200
