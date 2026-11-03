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


def test_client_side_routes_load_the_app_on_direct_navigation_or_refresh(client):
    for path in ("/start", "/conversation", "/start/"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<title>spa</title>" in response.text


def test_unknown_api_paths_never_fall_back_to_html(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "<title>spa</title>" not in response.text


def test_missing_assets_are_404_not_the_app_shell(client):
    for path in ("/missing.js", "/assets/missing.css", "/favicon.ico"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"
