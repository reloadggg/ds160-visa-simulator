import pytest

from app.domain.contracts import ApplicantProfile
from app.services.extractor_service import ExtractorService


@pytest.mark.live_llm
def test_live_extractor_maps_parent_funding_without_parent_keyword() -> None:
    profile = ApplicantProfile.minimal("profile-live-extractor-1")

    updated = ExtractorService().apply_message(
        profile,
        "My mother and father will cover all my tuition and living expenses.",
    )

    assert updated.funding["primary_source"] == "parents"
    assert updated.field_states["/funding/primary_source"].state.value == "claimed"
    assert updated.field_provenance["/funding/primary_source"].evidence_refs == []


@pytest.mark.live_llm
def test_live_extractor_keeps_unknown_when_funding_not_decided() -> None:
    profile = ApplicantProfile.minimal("profile-live-extractor-2")

    updated = ExtractorService().apply_message(
        profile,
        "I have not decided who will pay yet.",
    )

    assert updated.field_states["/funding/primary_source"].state.value == "unknown"
    assert "primary_source" not in updated.funding
