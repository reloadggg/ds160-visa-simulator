from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.services.profile_recompute_service import ProfileRecomputeService


def test_recompute_session_promotes_claimed_funding_to_documented(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'profile-recompute.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    profile = ApplicantProfile.minimal("profile-sess-1")
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.CLAIMED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord()

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                )
            )
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id="sess-1",
                    document_id="doc-1",
                    chunk_id="chunk-1",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="parents",
                    excerpt="Parent sponsor bank statement",
                    confidence=1.0,
                    metadata_json={},
                )
            )
            db.commit()

        with testing_session_local() as db:
            profile = ProfileRecomputeService(db).recompute_session("sess-1")
            assert (
                profile.field_states["/funding/primary_source"].state
                == FieldState.DOCUMENTED
            )
            assert (
                profile.field_provenance["/funding/primary_source"].evidence_refs
                == ["evi-1"]
            )

            saved_session = db.get(SessionRecord, "sess-1")
            assert saved_session is not None
            saved_profile = ApplicantProfile.model_validate(saved_session.profile_json)
            assert (
                saved_profile.field_states["/funding/primary_source"].state
                == FieldState.DOCUMENTED
            )
            assert (
                saved_profile.field_provenance["/funding/primary_source"].evidence_refs
                == ["evi-1"]
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_recompute_session_clears_stale_documented_state_without_evidence(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'profile-recompute-stale.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    profile = ApplicantProfile.minimal("profile-sess-1")
    profile.funding["primary_source"] = "self"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.DOCUMENTED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
        evidence_refs=["evi-stale"],
        source_summary="document evidence",
    )

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-1",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                )
            )
            db.commit()

        with testing_session_local() as db:
            profile = ProfileRecomputeService(db).recompute_session("sess-1")
            assert (
                profile.field_states["/funding/primary_source"].state
                == FieldState.UNKNOWN
            )
            assert (
                profile.field_provenance["/funding/primary_source"].evidence_refs
                == []
            )
            assert (
                profile.field_provenance["/funding/primary_source"].source_summary
                is None
            )

            saved_session = db.get(SessionRecord, "sess-1")
            assert saved_session is not None
            saved_profile = ApplicantProfile.model_validate(saved_session.profile_json)
            assert (
                saved_profile.field_states["/funding/primary_source"].state
                == FieldState.UNKNOWN
            )
            assert (
                saved_profile.field_provenance["/funding/primary_source"].evidence_refs
                == []
            )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
