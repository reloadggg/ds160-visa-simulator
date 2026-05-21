from app.core.settings import settings
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService


class FakeEmbeddingClient:
    def embed(self, texts):
        return [[0.1, 0.2]]


class FakeRerankClient:
    def rerank(self, *, query, documents, top_n):
        return [(0, 0.9), (1, 0.2)]


class FakeCollection:
    def query(self, **kwargs):
        return {
            "ids": [["chunk-1", "chunk-2"]],
            "documents": [["Older text", "Official DS-160 text"]],
            "metadatas": [
                [
                    {
                        "source_id": "src-1",
                        "source_type": "federal_official",
                        "title": "Older",
                        "url": "https://example.test/old",
                        "authority_weight": 1.0,
                    },
                    {
                        "source_id": "src-2",
                        "source_type": "federal_official",
                        "title": "DS-160",
                        "url": "https://example.test/ds160",
                        "section_path": "FAQ",
                        "authority_weight": 1.0,
                        "fetched_at": "2026-05-21",
                    },
                ]
            ],
            "distances": [[0.4, 0.1]],
        }


class ContextBoostCollection:
    def __init__(self) -> None:
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {
            "ids": [["federal-chunk", "post-chunk"]],
            "documents": [["Federal DS-160 document list", "UK appointment document list"]],
            "metadatas": [
                [
                    {
                        "source_id": "federal-src",
                        "source_type": "federal_official",
                        "title": "Federal guidance",
                        "url": "https://travel.state.gov/federal",
                        "authority_weight": 1.0,
                    },
                    {
                        "source_id": "uk-src",
                        "source_type": "post_specific",
                        "title": "UK guidance",
                        "url": "https://uk.usembassy.gov/niv",
                        "post": "UK",
                        "authority_weight": 0.9,
                    },
                ]
            ],
            "distances": [[0.1, 0.2]],
        }


class FakeChromaClient:
    def get_or_create_collection(self, name):
        return FakeCollection()


class ContextBoostChromaClient:
    def __init__(self) -> None:
        self.collection = ContextBoostCollection()

    def get_or_create_collection(self, name):
        return self.collection


class EqualScoreRerankClient:
    def rerank(self, *, query, documents, top_n):
        return [(0, 0.9), (1, 0.9)]


def test_policy_retrieval_returns_skipped_when_rag_disabled() -> None:
    result = VisaPolicyRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FakeRerankClient(),
        chroma_client=FakeChromaClient(),
    ).search_policy("ds160")

    assert result.skipped is True
    assert result.skip_reason == "disabled"


def test_policy_retrieval_reranks_and_builds_citations(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    monkeypatch.setattr(settings, "rag_min_final_score", 0.0)
    monkeypatch.setattr(settings, "rag_vector_top_k_per_collection", 2)
    monkeypatch.setattr(settings, "rag_candidate_limit", 2)
    monkeypatch.setattr(settings, "rag_rerank_top_n", 2)

    result = VisaPolicyRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FakeRerankClient(),
        chroma_client=FakeChromaClient(),
    ).search_policy("ds160", source_types=["federal_official"])

    assert result.skipped is False
    assert result.hit_count == 2
    assert result.hits[0].source_id == "src-2"
    assert result.citations[0]["url"] == "https://example.test/ds160"


def test_policy_retrieval_normalizes_context_filter_and_boosts_local_hit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    monkeypatch.setattr(settings, "rag_min_final_score", 0.0)
    monkeypatch.setattr(settings, "rag_vector_top_k_per_collection", 2)
    monkeypatch.setattr(settings, "rag_candidate_limit", 2)
    monkeypatch.setattr(settings, "rag_rerank_top_n", 2)

    chroma_client = ContextBoostChromaClient()
    result = VisaPolicyRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        rerank_client=EqualScoreRerankClient(),
        chroma_client=chroma_client,
    ).search_policy(
        "英国面谈材料",
        post="uk",
        source_types=["post_specific"],
    )

    where_filter = chroma_client.collection.queries[0]["where"]

    assert {"post": {"$in": ["uk", "Uk", ""]}} in where_filter["$and"]
    assert result.hits[0].source_id == "uk-src"
    assert result.hits[0].final_score > result.hits[1].final_score


def test_policy_retrieval_context_filter_keeps_legacy_country_case(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    monkeypatch.setattr(settings, "rag_min_final_score", 0.0)
    monkeypatch.setattr(settings, "rag_vector_top_k_per_collection", 2)
    monkeypatch.setattr(settings, "rag_candidate_limit", 2)
    monkeypatch.setattr(settings, "rag_rerank_top_n", 2)

    chroma_client = ContextBoostChromaClient()
    VisaPolicyRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        rerank_client=EqualScoreRerankClient(),
        chroma_client=chroma_client,
    ).search_policy(
        "中国互惠表",
        country="China",
        source_types=["country_reciprocity"],
    )

    where_filter = chroma_client.collection.queries[0]["where"]

    assert {"country": {"$in": ["China", "china", ""]}} in where_filter["$and"]

    chroma_client.collection.queries.clear()
    VisaPolicyRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        rerank_client=EqualScoreRerankClient(),
        chroma_client=chroma_client,
    ).search_policy(
        "中国互惠表",
        country="china",
        source_types=["country_reciprocity"],
    )

    where_filter = chroma_client.collection.queries[0]["where"]

    assert {"country": {"$in": ["china", "China", ""]}} in where_filter["$and"]
