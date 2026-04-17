import pytest


@pytest.mark.live_llm
def test_live_openai_compat_maps_to_domain_flow(live_api_client) -> None:
    response = live_api_client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "user",
                    "content": "My mother and father will cover all my tuition and living expenses.",
                }
            ],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["role"] == "assistant"
    assert payload["choices"][0]["message"]["content"] == "Please upload funding proof."
    assert payload["metadata"]["phase_state"] == "intake"
