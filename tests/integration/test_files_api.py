from fastapi.testclient import TestClient

from app.main import app


def test_upload_file_creates_document_and_job() -> None:
    client = TestClient(app)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={"file": ("i20.txt", b"SEVIS ID: N1234567890", "text/plain")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_status"] == "uploaded"
    assert payload["job_status"] == "queued"
