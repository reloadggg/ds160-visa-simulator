import pytest


@pytest.fixture(autouse=True)
def disable_multimodal_extraction_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_EXTRACTION_ENABLED", "false")
