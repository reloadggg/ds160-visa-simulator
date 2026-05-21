from app.core.settings import settings
from app.services.visa_policy_ingest_service import (
    PolicyKnowledgeIngestService,
    UnsupportedPolicyKnowledgeFileError,
)


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(index), 0.1] for index, _text in enumerate(texts)]


class FakeCollection:
    def __init__(self) -> None:
        self.upserts = []

    def upsert(self, **kwargs) -> None:
        self.upserts.append(kwargs)

    def count(self) -> int:
        return sum(len(item["ids"]) for item in self.upserts)


class FakeChromaClient:
    def __init__(self) -> None:
        self.collections = {}

    def get_or_create_collection(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

    def get_collection(self, name):
        return self.collections[name]

    def list_collections(self):
        return [
            type("CollectionRef", (), {"name": name})()
            for name in self.collections
        ]


def test_policy_ingest_returns_skipped_when_rag_disabled() -> None:
    result = PolicyKnowledgeIngestService(
        embedding_client=FakeEmbeddingClient(),
        chroma_client=FakeChromaClient(),
    ).ingest_upload(
        filename="case.md",
        raw_bytes=b"# Case\nF-1 student interview note.",
    )

    assert result.skipped is True
    assert result.skip_reason == "disabled"
    assert result.chunk_count == 0


def test_policy_ingest_chunks_and_upserts_to_chroma(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    monkeypatch.setattr(settings, "rag_chunk_size", 20)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 5)

    chroma_client = FakeChromaClient()
    result = PolicyKnowledgeIngestService(
        embedding_client=FakeEmbeddingClient(),
        chroma_client=chroma_client,
    ).ingest_upload(
        filename="f1-policy.md",
        raw_bytes=(
            "Students must explain study plans and nonimmigrant intent. " * 12
        ).encode(),
        source_type="third_party_reference",
        title="F-1 Policy Note",
        visa_family="f1",
    )

    collection = chroma_client.collections["us_visa_third_party_reference_v1"]

    assert result.status == "indexed"
    assert result.skipped is False
    assert result.chunk_count >= 2
    assert collection.count() == result.chunk_count
    assert collection.upserts[0]["metadatas"][0]["title"] == "F-1 Policy Note"
    assert collection.upserts[0]["metadatas"][0]["visa_family"] == "f1"


def test_policy_ingest_normalizes_context_metadata(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")

    chroma_client = FakeChromaClient()
    PolicyKnowledgeIngestService(
        embedding_client=FakeEmbeddingClient(),
        chroma_client=chroma_client,
    ).ingest_upload(
        filename="china-policy.md",
        raw_bytes=b"China reciprocity document requirements.",
        source_type="country_reciprocity",
        visa_family="B1_B2",
        country="China",
        post="GUANGZHOU",
    )

    metadata = chroma_client.collections[
        "us_visa_country_reciprocity_v1"
    ].upserts[0]["metadatas"][0]

    assert metadata["visa_family"] == "b1_b2"
    assert metadata["country"] == "china"
    assert metadata["post"] == "guangzhou"


def test_policy_ingest_batches_embedding_requests(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    monkeypatch.setattr(settings, "rag_chunk_size", 200)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 0)
    monkeypatch.setattr(settings, "siliconflow_embedding_batch_size", 2)

    embedding_client = FakeEmbeddingClient()

    result = PolicyKnowledgeIngestService(
        embedding_client=embedding_client,
        chroma_client=FakeChromaClient(),
    ).ingest_upload(
        filename="long-policy.md",
        raw_bytes=("A" * 850).encode(),
    )

    assert result.chunk_count == 5
    assert [len(call) for call in embedding_client.calls] == [2, 2, 1]


def test_policy_ingest_rejects_unsupported_file_type() -> None:
    service = PolicyKnowledgeIngestService(
        embedding_client=FakeEmbeddingClient(),
        chroma_client=FakeChromaClient(),
    )

    try:
        service.ingest_upload(filename="policy.csv", raw_bytes=b"a,b")
    except UnsupportedPolicyKnowledgeFileError as exc:
        assert "Only TXT" in str(exc)
    else:
        raise AssertionError("unsupported RAG upload should fail")


def test_policy_status_marks_empty_index(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")

    payload = PolicyKnowledgeIngestService(
        embedding_client=FakeEmbeddingClient(),
        chroma_client=FakeChromaClient(),
    ).status_payload()

    assert payload["enabled"] is True
    assert payload["ready"] is False
    assert payload["status"] == "index_empty"
    assert payload["skip_reason"] == "index_empty"
    assert len(payload["collections"]) == 4


def test_policy_status_does_not_create_missing_collections(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    chroma_client = FakeChromaClient()
    chroma_client.get_or_create_collection("us_visa_federal_official_v1")

    payload = PolicyKnowledgeIngestService(
        embedding_client=FakeEmbeddingClient(),
        chroma_client=chroma_client,
    ).status_payload()

    assert set(chroma_client.collections) == {"us_visa_federal_official_v1"}
    federal_collection = next(
        collection
        for collection in payload["collections"]
        if collection["source_type"] == "federal_official"
    )
    assert federal_collection["count"] == 0
