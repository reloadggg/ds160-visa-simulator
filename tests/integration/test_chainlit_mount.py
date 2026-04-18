from fastapi.testclient import TestClient

from app.main import app


def test_chainlit_is_mounted_under_ui() -> None:
    with TestClient(app) as client:
        response = client.get("/ui/")

    assert response.status_code in {200, 307}


def test_chainlit_project_settings_expose_upload_accept_and_size() -> None:
    with TestClient(app) as client:
        response = client.get("/ui/project/settings?language=en-US")

    assert response.status_code == 200

    payload = response.json()
    assert payload["features"]["spontaneous_file_upload"]["enabled"] is True
    assert payload["features"]["spontaneous_file_upload"]["accept"] == [
        "application/pdf",
        "image/png",
        "image/jpeg",
    ]
    assert payload["features"]["spontaneous_file_upload"]["max_size_mb"] == 64
