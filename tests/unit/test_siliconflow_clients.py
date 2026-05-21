import httpx

from app.integrations.siliconflow_embedding_client import SiliconFlowEmbeddingClient
from app.integrations.siliconflow_rerank_client import SiliconFlowRerankClient


def ok_response(payload: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://api.siliconflow.com/v1/test")
    return httpx.Response(200, json=payload, request=request)


def test_embedding_client_omits_dimensions_for_bge_m3(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return ok_response(
            {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    embeddings = SiliconFlowEmbeddingClient(
        base_url="https://api.siliconflow.com/v1",
        api_key="test-key",
        model="BAAI/bge-m3",
        dimensions=1024,
    ).embed(["hello"])

    assert embeddings == [[0.1, 0.2]]
    assert captured["url"] == "https://api.siliconflow.com/v1/embeddings"
    assert "dimensions" not in captured["json"]


def test_embedding_client_sends_dimensions_for_qwen3(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return ok_response({"data": [{"index": 0, "embedding": [0.1]}]})

    monkeypatch.setattr(httpx, "post", fake_post)

    SiliconFlowEmbeddingClient(
        api_key="test-key",
        model="Qwen/Qwen3-Embedding-4B",
        dimensions=1024,
    ).embed(["hello"])

    assert captured["json"]["dimensions"] == 1024


def test_rerank_client_returns_index_score_pairs(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return ok_response(
            {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    ranking = SiliconFlowRerankClient(
        base_url="https://api.siliconflow.com/v1",
        api_key="test-key",
        model="Qwen/Qwen3-Reranker-4B",
    ).rerank(query="visa", documents=["a", "b"], top_n=2)

    assert ranking == [(1, 0.9), (0, 0.2)]
    assert captured["url"] == "https://api.siliconflow.com/v1/rerank"
    assert captured["json"]["return_documents"] is False
