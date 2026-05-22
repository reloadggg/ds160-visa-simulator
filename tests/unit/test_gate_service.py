from app.services.gate_service import GateService


def test_default_scenario_package_covers_supported_families() -> None:
    service = GateService()

    assert service.default_scenario_package("b1_b2") == (
        "tourism",
        ["ds160", "passport_bio", "itinerary_or_trip_purpose"],
    )
    assert service.default_scenario_package("f1") == (
        "parent_sponsored",
        ["ds160", "passport_bio", "i20", "admission_letter", "funding_proof"],
    )
    assert service.default_scenario_package("h1b") == (
        "first_time_stamping",
        ["ds160", "passport_bio", "i797", "employer_letter"],
    )
    assert service.default_scenario_package("j1") == (
        "institution_funded",
        ["ds160", "passport_bio", "ds2019", "funding_proof"],
    )
    assert service.default_scenario_package("l1a") == (
        "manager_executive",
        ["ds160", "passport_bio", "i797", "employer_letter"],
    )
    assert service.default_scenario_package("l1b") == (
        "specialized_knowledge",
        ["ds160", "passport_bio", "i797", "employer_letter"],
    )
    assert service.default_scenario_package("m1") == (
        "vocational_program",
        ["ds160", "passport_bio", "school_letter", "funding_proof"],
    )
    assert service.default_scenario_package("o1") == (
        "science_business",
        ["ds160", "passport_bio", "i797", "evidence_of_achievement"],
    )


def test_required_package_without_scenario_uses_family_default() -> None:
    service = GateService()

    assert service.required_package("j1") == [
        "ds160",
        "passport_bio",
        "ds2019",
        "funding_proof",
    ]


def test_required_package_detail_separates_official_and_simulator_evidence() -> None:
    service = GateService()

    assert service.required_package_detail("f1") == {
        "scenario_key": "parent_sponsored",
        "official_pre_interview_required": ["ds160", "passport_bio", "i20"],
        "simulator_recommended_evidence": ["admission_letter", "funding_proof"],
        "required_initial_package": [
            "ds160",
            "passport_bio",
            "i20",
            "admission_letter",
            "funding_proof",
        ],
    }


def test_initial_gate_status_uses_policy_pack_default_scenario() -> None:
    service = GateService()

    assert service.initial_gate_status("j1") == {
        "declared_family": "j1",
        "scenario_key": "institution_funded",
        "status": "pending_documents",
        "required_documents": [
            {
                "document_type": "ds160",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "passport_bio",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "ds2019",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "funding_proof",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
        ],
    }
