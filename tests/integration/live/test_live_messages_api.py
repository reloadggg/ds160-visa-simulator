import pytest


@pytest.mark.live_llm
def test_live_messages_api_requests_funding_proof(live_api_client) -> None:
    session_resp = live_api_client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert payload["assistant_message"] == "Please upload funding proof."


@pytest.mark.live_llm
def test_live_messages_api_continues_after_funding_document_upload(
    live_api_client,
) -> None:
    session_resp = live_api_client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
    )
    upload_response = live_api_client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )
    response = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert upload_response.status_code == 202
    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is the purpose of your travel?"
