def test_index_served_from_same_origin(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>spa</title>" in response.text


def test_asset_served(client):
    assert client.get("/app.js").status_code == 200


def test_api_takes_precedence_over_static(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_no_cors_headers_are_emitted(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
