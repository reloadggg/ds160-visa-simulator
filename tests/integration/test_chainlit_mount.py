from fastapi.testclient import TestClient

from app.main import app


def test_chainlit_is_mounted_under_ui() -> None:
    with TestClient(app) as client:
        response = client.get("/ui/")

    assert response.status_code in {200, 307}
