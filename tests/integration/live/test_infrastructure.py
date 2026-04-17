from fastapi.testclient import TestClient


def test_live_api_client_fixture_bootstraps_app(
    live_api_client: TestClient,
) -> None:
    response = live_api_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
