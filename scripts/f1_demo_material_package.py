#!/usr/bin/env python3
"""Generate, validate, publish, and clean the F-1 customer demo material package.

This script intentionally uses the existing backend API for validation.  The only
DB-writing path is the explicit ``publish`` command, which promotes a previously
validated uploaded-material session into the existing material package archive so
``GET /v1/material-packages`` and the current import endpoint can list/use it.

Secrets are read from environment variables only and are never written to the
validation artifact.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import re
import shutil
import sys
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fitz
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, JobRecord, SessionRecord, SessionTurnRecord
from app.db.session import DATABASE_URL, SessionLocal
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.gate_service import GateService


DEFAULT_TEMPLATE_ID = "f1_parent_sponsored_demo_nyu_mscs_v1"
TEMPLATE_ID = DEFAULT_TEMPLATE_ID
PACKAGE_ID = "demo-f1-parent-sponsored-nyu-mscs-v1"
PACKAGE_LABEL = "F-1 留学面签演示材料包（NYU MSCS / 父母资助）"
ARCHIVE_SOURCE_REASON = "validated_f1_demo_material_package"
FORBIDDEN_MODEL_VISIBLE_MARKERS = (
    "oracle",
    "expected_finding",
    "debug_bundle",
    "debug material",
    "placeholder",
    "占位符",
    "自洽",
)
REQUIRED_DOCUMENT_TYPES = (
    "ds160",
    "passport_bio",
    "i20",
    "admission_letter",
    "funding_proof",
    "relationship_proof_between_applicant_and_sponsors",
)
TERMINAL_RISK_DECISIONS = {
    "high_risk_review",
    "simulated_refusal",
    "not_passed",
    "refused",
    "refusal",
}
SAFE_CONTINUATION_DECISIONS = {
    "continue_interview",
    "need_more_evidence",
    "verify_key_issue",
    "passed",
}


@dataclass(frozen=True)
class DemoDocumentDefinition:
    document_type: str
    filename: str
    title: str
    body: str
    expected_fields: dict[str, str]


@dataclass(frozen=True)
class DemoTemplateDefinition:
    template_id: str
    package_id: str
    label: str
    visa_family: str
    intent: str
    seed_text: str
    expected_facts: dict[str, str]
    applicant_answers: tuple[str, ...]
    documents: tuple[DemoDocumentDefinition, ...]
    required_document_types: tuple[str, ...] = field(default_factory=tuple)


DEMO_TEMPLATE = DemoTemplateDefinition(
    template_id=TEMPLATE_ID,
    package_id=PACKAGE_ID,
    label=PACKAGE_LABEL,
    visa_family="f1",
    intent="pass_oriented_customer_demo",
    seed_text=(
        "Chinese F-1 applicant Chen Wei, admitted to New York University MS in "
        "Computer Science for Fall 2026. Parents Chen Guoqiang and Li Mei sponsor "
        "tuition and living expenses with documented bank funds. Applicant plans "
        "to return to China after graduation to work in software engineering."
    ),
    expected_facts={
        "/identity/full_name": "Chen Wei",
        "/identity/passport_number": "E12345678",
        "/identity/nationality": "China",
        "/visa_intent/travel_purpose": "F-1 student visa for graduate study",
        "/education/sevis_id": "N0034567890",
        "/education/school_name": "New York University",
        "/education/program_name": "Master of Science in Computer Science",
        "/funding/primary_source": "parents",
        "/funding/sponsor_relationship": "parents",
        "/family/parent_names": "Chen Guoqiang; Li Mei",
        "/education/undergraduate_school": "Shanghai Jiao Tong University",
        "/education/undergraduate_program": "Bachelor of Software Engineering",
        "/career/post_graduation_plan": "Return to China for software engineering work",
    },
    applicant_answers=(
        "Good morning. I am Chen Wei. I am applying for an F-1 visa to study the Master of Science in Computer Science at New York University.",
        "I chose NYU because its computer science program has strong software systems courses, project-based learning, and New York technology industry exposure that fits my graduate study plan.",
        "I studied software engineering at Shanghai Jiao Tong University for my undergraduate degree, so the NYU computer science master's is a direct continuation of my background.",
        "My parents, Chen Guoqiang and Li Mei, will pay my tuition and living expenses. Their bank certificate shows sufficient family funds for the program.",
        "After graduation, I plan to return to China and work in software engineering, using the graduate training from NYU.",
    ),
    documents=(
        DemoDocumentDefinition(
            document_type="ds160",
            filename="01_ds160_confirmation_chen_wei.pdf",
            title="DS-160 Confirmation Page",
            body=(
                "DS-160 Confirmation Page\n"
                "Confirmation Number: AA00CW2026\n"
                "Full name: Chen Wei\n"
                "Date of Birth: 2001-08-16\n"
                "Passport number: E12345678\n"
                "Nationality: China\n"
                "Visa Class: F-1 Academic Student\n"
                "Travel purpose: F-1 student visa for graduate study\n"
                "U.S. School: New York University\n"
                "Program: Master of Science in Computer Science\n"
                "Primary funding source: parents\n"
                "Previous Education: Shanghai Jiao Tong University, Bachelor of Software Engineering\n"
                "Post-graduation plan: Return to China for software engineering work\n"
            ),
            expected_fields={
                "/identity/full_name": "Chen Wei",
                "/identity/passport_number": "E12345678",
                "/visa_intent/travel_purpose": "F-1 student visa for graduate study",
                "/education/undergraduate_school": "Shanghai Jiao Tong University",
                "/education/undergraduate_program": "Bachelor of Software Engineering",
                "/career/post_graduation_plan": "Return to China for software engineering work",
            },
        ),
        DemoDocumentDefinition(
            document_type="passport_bio",
            filename="02_passport_bio_chen_wei.pdf",
            title="Passport Biographic Page",
            body=(
                "Passport Bio Page\n"
                "Full name: Chen Wei\n"
                "Passport number: E12345678\n"
                "Nationality: China\n"
                "Date of Birth: 2001-08-16\n"
                "Place of Birth: Shanghai, China\n"
                "Date of Issue: 2024-03-12\n"
                "Date of Expiry: 2034-03-11\n"
            ),
            expected_fields={
                "/identity/full_name": "Chen Wei",
                "/identity/passport_number": "E12345678",
                "/identity/nationality": "China",
            },
        ),
        DemoDocumentDefinition(
            document_type="i20",
            filename="03_i20_nyu_mscs_chen_wei.pdf",
            title="Form I-20",
            body=(
                "Form I-20 Certificate of Eligibility for Nonimmigrant Student Status\n"
                "SEVIS ID: N0034567890\n"
                "Student Name: Chen Wei\n"
                "Passport number: E12345678\n"
                "School name: New York University\n"
                "School Official Address: 70 Washington Square South, New York, NY 10012\n"
                "Program: Master of Science in Computer Science\n"
                "Education Level: Master's\n"
                "Program Start Date: 2026-09-01\n"
                "Program End Date: 2028-05-20\n"
                "Funding: Family funds from parents\n"
            ),
            expected_fields={
                "/education/sevis_id": "N0034567890",
                "/education/school_name": "New York University",
                "/education/program_name": "Master of Science in Computer Science",
            },
        ),
        DemoDocumentDefinition(
            document_type="admission_letter",
            filename="04_admission_letter_nyu_mscs.pdf",
            title="Admission Letter",
            body=(
                "Admission Letter\n"
                "School name: New York University\n"
                "Applicant: Chen Wei\n"
                "Program: Master of Science in Computer Science\n"
                "Term: Fall 2026\n"
                "Academic Background: Shanghai Jiao Tong University, Bachelor of Software Engineering\n"
                "The admissions committee is pleased to offer admission to Chen Wei "
                "for graduate study in computer science. The applicant should arrive "
                "before orientation and maintain full-time student status.\n"
            ),
            expected_fields={
                "/education/school_name": "New York University",
                "/education/program_name": "Master of Science in Computer Science",
            },
        ),
        DemoDocumentDefinition(
            document_type="funding_proof",
            filename="05_parent_bank_certificate_chen_family.pdf",
            title="Parent Sponsor Bank Certificate",
            body=(
                "Parent Sponsor Bank Statement and Support Letter\n"
                "Applicant: Chen Wei\n"
                "Sponsor Father: Chen Guoqiang\n"
                "Sponsor Mother: Li Mei\n"
                "Relationship: parents\n"
                "Primary funding source: parents\n"
                "This parent sponsor bank statement confirms available family funds "
                "of RMB 980,000 for tuition, fees, and living expenses during Chen Wei's "
                "graduate study at New York University.\n"
                "The parents will cover tuition and living expenses for the F-1 study plan.\n"
            ),
            expected_fields={
                "/funding/primary_source": "parents",
            },
        ),
        DemoDocumentDefinition(
            document_type="relationship_proof_between_applicant_and_sponsors",
            filename="06_parent_relationship_certificate_chen_wei.pdf",
            title="Parent Relationship Certificate",
            body=(
                "Relationship Proof Certificate\n"
                "Full name: Chen Wei\n"
                "Passport number: E12345678\n"
                "Father: Chen Guoqiang\n"
                "Mother: Li Mei\n"
                "Relationship: parents\n"
                "Parent names: Chen Guoqiang; Li Mei\n"
                "This certificate confirms that Chen Guoqiang and Li Mei are the parents "
                "of Chen Wei and may act as financial sponsors for overseas study.\n"
            ),
            expected_fields={
                "/identity/full_name": "Chen Wei",
                "/funding/sponsor_relationship": "parents",
                "/family/parent_names": "Chen Guoqiang; Li Mei",
            },
        ),
    ),
    required_document_types=REQUIRED_DOCUMENT_TYPES,
)

TEMPLATE_REGISTRY: dict[str, DemoTemplateDefinition] = {
    DEMO_TEMPLATE.template_id: DEMO_TEMPLATE,
}


@dataclass(frozen=True)
class CleanupPackagePlan:
    package_id: str
    label: str | None
    source_session_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    turn_ids: tuple[str, ...]

    @property
    def document_count(self) -> int:
        return len(self.document_ids)

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_ids)

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_ids)

    @property
    def turn_count(self) -> int:
        return len(self.turn_ids)


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_template_ids() -> tuple[str, ...]:
    return tuple(sorted(TEMPLATE_REGISTRY))


def lookup_template(template_id: str | None = None) -> DemoTemplateDefinition:
    selected_template_id = template_id or DEFAULT_TEMPLATE_ID
    template = TEMPLATE_REGISTRY.get(selected_template_id)
    if template is None:
        available = ", ".join(list_template_ids())
        raise ValueError(f"unknown template-id {selected_template_id!r}; available templates: {available}")
    return template


def required_document_types_for(template: DemoTemplateDefinition) -> tuple[str, ...]:
    return template.required_document_types or tuple(document.document_type for document in template.documents)


def document_definitions_payload(template: DemoTemplateDefinition | None = None) -> list[dict[str, Any]]:
    selected_template = template or DEMO_TEMPLATE
    return [asdict(document) for document in selected_template.documents]


def template_payload(template: DemoTemplateDefinition | None = None) -> dict[str, Any]:
    selected_template = template or DEMO_TEMPLATE
    return {
        "template_id": selected_template.template_id,
        "package_id": selected_template.package_id,
        "label": selected_template.label,
        "visa_family": selected_template.visa_family,
        "intent": selected_template.intent,
        "seed_text": selected_template.seed_text,
        "expected_facts": dict(selected_template.expected_facts),
        "applicant_answers": list(selected_template.applicant_answers),
        "documents": document_definitions_payload(selected_template),
        "validation": {
            "status": "candidate",
            "last_validated_at": None,
            "last_session_id": None,
        },
    }


def assert_template_contract(template: DemoTemplateDefinition | None = None) -> None:
    selected_template = template or DEMO_TEMPLATE
    required_document_types = required_document_types_for(selected_template)
    document_types = [document.document_type for document in selected_template.documents]
    if tuple(document_types) != required_document_types:
        raise ValueError(
            f"Demo package {selected_template.template_id!r} must contain exactly the "
            f"required document types in order: {required_document_types}. Got: {document_types}"
        )
    filenames = [document.filename for document in selected_template.documents]
    if len(filenames) != len(set(filenames)):
        raise ValueError(f"Demo package {selected_template.template_id!r} filenames must be unique.")
    visible_text = "\n".join(document.body for document in selected_template.documents)
    visible_text_lower = visible_text.lower()
    leaked = [marker for marker in FORBIDDEN_MODEL_VISIBLE_MARKERS if marker in visible_text_lower]
    if leaked:
        raise ValueError(f"Model-visible demo material contains forbidden marker(s): {leaked}")
    if "{{" in visible_text or "}}" in visible_text:
        raise ValueError("Model-visible demo material contains template placeholder braces.")


def render_pdf_bytes(title: str, body: str) -> bytes:
    pdf = fitz.open()
    try:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((54, 54), title, fontsize=16, fontname="helv")
        rect = fitz.Rect(54, 88, 545, 800)
        page.insert_textbox(rect, body, fontsize=10.5, fontname="helv", lineheight=1.25)
        return pdf.tobytes()
    finally:
        pdf.close()


def render_materials(
    output_dir: Path,
    template: DemoTemplateDefinition | None = None,
) -> dict[str, Any]:
    selected_template = template or DEMO_TEMPLATE
    assert_template_contract(selected_template)
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    for document in selected_template.documents:
        pdf_bytes = render_pdf_bytes(document.title, document.body)
        pdf_path = output_dir / document.filename
        pdf_path.write_bytes(pdf_bytes)
        rendered.append(
            {
                "document_type": document.document_type,
                "filename": document.filename,
                "path": str(pdf_path),
                "bytes": len(pdf_bytes),
                "expected_fields": dict(document.expected_fields),
            }
        )
    manifest = {
        "schema_version": "ds160.f1_demo_materials.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "template": template_payload(selected_template),
        "rendered_documents": rendered,
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        text = response.text
        return {"text": text[:2000]}


class ApiRecorder:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        request_summary: dict[str, Any] | None,
        response_payload: Any,
    ) -> None:
        self.entries.append(
            {
                "method": method,
                "path": path,
                "status_code": status_code,
                "request": request_summary or {},
                "response": response_payload,
            }
        )


def api_request(
    client: httpx.Client,
    recorder: ApiRecorder,
    method: str,
    path: str,
    *,
    request_summary: dict[str, Any] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    response = client.request(method, path, **kwargs)
    recorder.record(
        method=method,
        path=path,
        status_code=response.status_code,
        request_summary=request_summary,
        response_payload=_response_payload(response),
    )
    return response


def api_origin_for_base_url(base_url: str) -> str:
    parsed_base_url = urlsplit(base_url.rstrip("/"))
    if parsed_base_url.scheme and parsed_base_url.netloc:
        return f"{parsed_base_url.scheme}://{parsed_base_url.netloc}"
    return base_url.rstrip("/")


def login_if_configured(
    client: httpx.Client,
    recorder: ApiRecorder,
    password_env: str | None,
    *,
    login_path: str,
) -> None:
    if not password_env:
        return
    password = os.getenv(password_env)
    if not password:
        return
    response = api_request(
        client,
        recorder,
        "POST",
        login_path,
        json={"password": password},
        request_summary={
            "login_path": login_path,
            "password_env": password_env,
            "password": "<redacted>",
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"auth login failed with HTTP {response.status_code}; check {password_env} without printing it"
        )


def wait_for_worker_completion(
    client: httpx.Client,
    recorder: ApiRecorder,
    session_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    drain_local_worker: bool,
    template: DemoTemplateDefinition | None = None,
) -> dict[str, Any]:
    deadline = datetime.now(UTC).timestamp() + timeout_seconds
    latest_export: dict[str, Any] = {}
    while datetime.now(UTC).timestamp() < deadline:
        if drain_local_worker:
            drain_local_parse_worker()
        response = api_request(
            client,
            recorder,
            "GET",
            f"/v1/sessions/{session_id}/reports/export",
            request_summary={"purpose": "poll_material_understanding"},
        )
        if response.status_code == 200:
            latest_export = response.json()
            if required_documents_completed(latest_export, template):
                return latest_export
        import time

        time.sleep(poll_seconds)
    return latest_export


def drain_local_parse_worker() -> int:
    from app.workers.parse_worker import ParseWorker

    processed = 0
    with SessionLocal() as db:
        worker = ParseWorker(db)
        while worker.run_once():
            processed += 1
    return processed


def required_documents_completed(
    export_payload: dict[str, Any],
    template: DemoTemplateDefinition | None = None,
) -> bool:
    selected_template = template or DEMO_TEMPLATE
    required_document_types = required_document_types_for(selected_template)
    documents = export_payload.get("documents") if isinstance(export_payload, dict) else None
    if not isinstance(documents, list):
        return False
    status_by_type: dict[str, str] = {}
    for document in documents:
        artifact = document.get("artifact") if isinstance(document, dict) else {}
        if not isinstance(artifact, dict):
            artifact = {}
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        document_type = artifact.get("document_type") or metadata.get("document_type")
        understanding_status = artifact.get("understanding_status")
        status = document.get("status")
        if isinstance(document_type, str):
            status_by_type[document_type] = f"{status}:{understanding_status}"
    return all(status_by_type.get(item) == "parsed:completed" for item in required_document_types)


def validate_run_payload(
    run_payload: dict[str, Any],
    template: DemoTemplateDefinition | None = None,
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    selected_template = template or DEMO_TEMPLATE
    required_document_types = required_document_types_for(selected_template)
    defects: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    uploads = run_payload.get("uploads", [])
    if len(uploads) != len(required_document_types):
        defects.append(
            {
                "code": "upload_count_mismatch",
                "detail": f"expected {len(required_document_types)} uploads, got {len(uploads)}",
            }
        )
    for upload in uploads:
        if upload.get("status_code") != 202:
            defects.append(
                {
                    "code": "upload_failed",
                    "document_type": upload.get("document_type"),
                    "status_code": upload.get("status_code"),
                }
            )
        response = upload.get("response") if isinstance(upload.get("response"), dict) else {}
        if response.get("main_flow_refresh_error"):
            defects.append(
                {
                    "code": "main_flow_refresh_error_after_upload",
                    "document_type": upload.get("document_type"),
                    "detail": response.get("main_flow_refresh_error"),
                }
            )

    export_payload = run_payload.get("export") if isinstance(run_payload.get("export"), dict) else {}
    documents = export_payload.get("documents") if isinstance(export_payload, dict) else []
    documents_by_type: dict[str, dict[str, Any]] = {}
    for document in documents if isinstance(documents, list) else []:
        artifact = document.get("artifact") if isinstance(document, dict) else {}
        if not isinstance(artifact, dict):
            artifact = {}
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        document_type = artifact.get("document_type") or metadata.get("document_type")
        if isinstance(document_type, str):
            documents_by_type[document_type] = document
            status = document.get("status")
            understanding_status = artifact.get("understanding_status")
            if status != "parsed" or understanding_status != "completed":
                defects.append(
                    {
                        "code": "material_not_completed",
                        "document_type": document_type,
                        "status": status,
                        "understanding_status": understanding_status,
                    }
                )
    for required_type in required_document_types:
        if required_type not in documents_by_type:
            defects.append({"code": "missing_exported_document", "document_type": required_type})

    turns = run_payload.get("message_turns", [])
    if len(turns) < 5:
        defects.append({"code": "not_enough_interview_turns", "count": len(turns)})
    assistant_messages: list[str] = []
    for turn in turns:
        if turn.get("status_code") != 200:
            defects.append(
                {
                    "code": "message_turn_failed",
                    "turn_index": turn.get("turn_index"),
                    "status_code": turn.get("status_code"),
                }
            )
            continue
        response = turn.get("response") if isinstance(turn.get("response"), dict) else {}
        decision = str(response.get("governor_decision") or "")
        if decision in TERMINAL_RISK_DECISIONS:
            defects.append(
                {
                    "code": "risk_or_refusal_decision",
                    "turn_index": turn.get("turn_index"),
                    "governor_decision": decision,
                }
            )
        if decision and decision not in SAFE_CONTINUATION_DECISIONS:
            warnings.append(
                {
                    "code": "unexpected_governor_decision",
                    "turn_index": turn.get("turn_index"),
                    "governor_decision": decision,
                }
            )
        if response.get("main_flow_refresh_error"):
            defects.append(
                {
                    "code": "main_flow_refresh_error_after_message",
                    "turn_index": turn.get("turn_index"),
                    "detail": response.get("main_flow_refresh_error"),
                }
            )
        assistant = str(response.get("assistant_message") or "").strip()
        if assistant:
            assistant_messages.append(_normalize_assistant_message(assistant))
    if len(assistant_messages) >= 2 and len(set(assistant_messages)) <= max(1, len(assistant_messages) // 3):
        defects.append(
            {
                "code": "repeated_template_replies",
                "assistant_message_count": len(assistant_messages),
                "unique_normalized_count": len(set(assistant_messages)),
            }
        )

    user_report = run_payload.get("user_report") if isinstance(run_payload.get("user_report"), dict) else {}
    internal_report = run_payload.get("internal_report") if isinstance(run_payload.get("internal_report"), dict) else {}
    user_decision = user_report.get("governor_decision")
    internal_decision = internal_report.get("governor_decision")
    if user_decision and internal_decision and user_decision != internal_decision:
        defects.append(
            {
                "code": "report_decision_drift",
                "user_governor_decision": user_decision,
                "internal_governor_decision": internal_decision,
            }
        )
    if user_decision in TERMINAL_RISK_DECISIONS or internal_decision in TERMINAL_RISK_DECISIONS:
        defects.append(
            {
                "code": "report_terminal_risk_state",
                "user_governor_decision": user_decision,
                "internal_governor_decision": internal_decision,
            }
        )
    runtime = run_payload.get("runtime_debug")
    if isinstance(runtime, dict) and runtime.get("status_code") in {401, 403, 404}:
        warnings.append(
            {
                "code": "runtime_debug_unavailable",
                "status_code": runtime.get("status_code"),
                "detail": "Runtime debug endpoint is gated or disabled; reports/export were still collected.",
            }
        )
    return not defects, defects, warnings


def _normalize_assistant_message(message: str) -> str:
    normalized = re.sub(r"\s+", " ", message).strip().lower()
    return normalized[:240]


def run_api_validation(
    *,
    base_url: str,
    artifact_dir: Path,
    auth_password_env: str | None,
    auth_login_path: str,
    timeout_seconds: float,
    poll_seconds: float,
    drain_local_worker: bool,
    template: DemoTemplateDefinition | None = None,
) -> dict[str, Any]:
    selected_template = template or DEMO_TEMPLATE
    required_document_types = required_document_types_for(selected_template)
    assert_template_contract(selected_template)
    materials_dir = artifact_dir / "materials"
    manifest = render_materials(materials_dir, selected_template)
    recorder = ApiRecorder()
    headers = {"Origin": api_origin_for_base_url(base_url)}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=120.0, headers=headers) as client:
        login_if_configured(
            client,
            recorder,
            auth_password_env,
            login_path=auth_login_path,
        )
        session_response = api_request(
            client,
            recorder,
            "POST",
            "/v1/sessions",
            json={"declared_family": selected_template.visa_family},
            request_summary={"declared_family": selected_template.visa_family},
        )
        session_payload = _response_payload(session_response)
        if session_response.status_code != 201 or not isinstance(session_payload, dict):
            raise RuntimeError(f"session creation failed with HTTP {session_response.status_code}")
        session_id = session_payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("session creation response did not include session_id")

        uploads: list[dict[str, Any]] = []
        for document in selected_template.documents:
            raw_bytes = (materials_dir / document.filename).read_bytes()
            response = api_request(
                client,
                recorder,
                "POST",
                f"/v1/sessions/{session_id}/files",
                data={"document_type": document.document_type},
                files={"file": (document.filename, raw_bytes, "application/pdf")},
                request_summary={
                    "document_type": document.document_type,
                    "filename": document.filename,
                    "bytes": len(raw_bytes),
                },
            )
            uploads.append(
                {
                    "document_type": document.document_type,
                    "filename": document.filename,
                    "status_code": response.status_code,
                    "response": _response_payload(response),
                }
            )

        worker_export = wait_for_worker_completion(
            client,
            recorder,
            session_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            drain_local_worker=drain_local_worker,
            template=selected_template,
        )

        message_turns: list[dict[str, Any]] = []
        for index, answer in enumerate(selected_template.applicant_answers, start=1):
            response = api_request(
                client,
                recorder,
                "POST",
                f"/v1/sessions/{session_id}/messages",
                json={
                    "role": "user",
                    "content": answer,
                    "client_message_id": f"{selected_template.template_id}-{utc_stamp()}-{index}",
                },
                request_summary={"turn_index": index, "answer": answer},
            )
            message_turns.append(
                {
                    "turn_index": index,
                    "answer": answer,
                    "status_code": response.status_code,
                    "response": _response_payload(response),
                }
            )

        runtime_response = api_request(
            client,
            recorder,
            "GET",
            f"/v1/sessions/{session_id}/debug/runtime",
            request_summary={"purpose": "runtime_debug_snapshot"},
        )
        user_report_response = api_request(
            client,
            recorder,
            "GET",
            f"/v1/sessions/{session_id}/reports/user",
            request_summary={"purpose": "user_report"},
        )
        internal_report_response = api_request(
            client,
            recorder,
            "GET",
            f"/v1/sessions/{session_id}/reports/internal",
            request_summary={"purpose": "internal_report"},
        )
        export_response = api_request(
            client,
            recorder,
            "GET",
            f"/v1/sessions/{session_id}/reports/export",
            request_summary={"purpose": "final_export"},
        )
        messages_response = api_request(
            client,
            recorder,
            "GET",
            f"/v1/sessions/{session_id}/messages",
            request_summary={"purpose": "transcript"},
        )

    runtime_payload: Any = _response_payload(runtime_response)
    if runtime_response.status_code != 200:
        runtime_payload = {
            "status_code": runtime_response.status_code,
            "response": runtime_payload,
        }
    run_payload: dict[str, Any] = {
        "schema_version": "ds160.f1_demo_validation_run.v1",
        "validated_at": datetime.now(UTC).isoformat(),
        "template": template_payload(selected_template),
        "session_id": session_id,
        "base_url": base_url.rstrip("/"),
        "manifest": manifest,
        "uploads": uploads,
        "worker_export_before_messages": worker_export,
        "message_turns": message_turns,
        "runtime_debug": runtime_payload,
        "user_report": _response_payload(user_report_response),
        "internal_report": _response_payload(internal_report_response),
        "export": _response_payload(export_response),
        "transcript": _response_payload(messages_response),
        "api_log": recorder.entries,
    }
    passed, defects, warnings = validate_run_payload(run_payload, selected_template)
    run_payload["validation"] = {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "defects": defects,
        "warnings": warnings,
        "criteria": {
            "uploaded_required_pdf_count": len(required_document_types),
            "minimum_message_turns": 5,
            "required_document_types": list(required_document_types),
            "terminal_risk_decisions": sorted(TERMINAL_RISK_DECISIONS),
        },
    }
    write_json(artifact_dir / "run.json", run_payload)
    write_json(artifact_dir / "api-log.json", recorder.entries)
    return run_payload


def archive_document_metadata(document: DocumentRecord) -> dict[str, Any]:
    artifact = dict(document.artifact_json or {})
    metadata = dict(artifact.get("metadata") or {})
    if artifact.get("document_type") and "document_type" not in metadata:
        metadata["document_type"] = artifact.get("document_type")
    return metadata


def is_source_archive_document(document: DocumentRecord) -> bool:
    metadata = archive_document_metadata(document)
    return bool(metadata.get("debug_material_bundle")) and not bool(
        metadata.get("material_package_import")
    )


def document_package_id(document: DocumentRecord) -> str | None:
    metadata = archive_document_metadata(document)
    package_id = metadata.get("synthetic_bundle_id")
    return package_id if isinstance(package_id, str) and package_id.strip() else None


def build_cleanup_plan(db: Session, package_id: str | None = None) -> list[CleanupPackagePlan]:
    source_documents = [
        document
        for document in db.scalars(select(DocumentRecord).order_by(DocumentRecord.document_id))
        if is_source_archive_document(document)
    ]
    grouped: dict[str, list[DocumentRecord]] = {}
    for document in source_documents:
        current_package_id = document_package_id(document)
        if not current_package_id:
            continue
        if package_id and current_package_id != package_id:
            continue
        grouped.setdefault(current_package_id, []).append(document)

    plans: list[CleanupPackagePlan] = []
    for current_package_id, documents in grouped.items():
        document_ids = tuple(document.document_id for document in documents)
        chunk_ids = tuple(
            record.chunk_id
            for record in db.scalars(
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_id.in_(document_ids))
                .order_by(DocumentChunkRecord.chunk_id)
            )
        )
        evidence_ids = tuple(
            record.evidence_id
            for record in db.scalars(
                select(EvidenceItemRecord)
                .where(EvidenceItemRecord.document_id.in_(document_ids))
                .order_by(EvidenceItemRecord.evidence_id)
            )
        )
        source_session_ids = tuple(sorted({document.session_id for document in documents}))
        turn_ids = tuple(
            turn.turn_id
            for turn in db.scalars(
                select(SessionTurnRecord)
                .where(SessionTurnRecord.session_id.in_(source_session_ids))
                .order_by(SessionTurnRecord.turn_id)
            )
            if _turn_belongs_to_source_package(turn, current_package_id)
        )
        first_metadata = archive_document_metadata(documents[0])
        label = first_metadata.get("debug_bundle_scenario_label")
        plans.append(
            CleanupPackagePlan(
                package_id=current_package_id,
                label=label if isinstance(label, str) else None,
                source_session_ids=source_session_ids,
                document_ids=document_ids,
                chunk_ids=chunk_ids,
                evidence_ids=evidence_ids,
                turn_ids=turn_ids,
            )
        )
    return plans


def _turn_belongs_to_source_package(turn: SessionTurnRecord, package_id: str) -> bool:
    metadata = dict(turn.metadata_json or {})
    if metadata.get("material_package_import"):
        return False
    bundle_id = metadata.get("synthetic_bundle_id")
    return bundle_id == package_id


def cleanup_plan_payload(plans: Sequence[CleanupPackagePlan]) -> dict[str, Any]:
    return {
        "schema_version": "ds160.material_archive_cleanup_plan.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "packages": [
            {
                "package_id": plan.package_id,
                "label": plan.label,
                "source_session_ids": list(plan.source_session_ids),
                "document_ids": list(plan.document_ids),
                "chunk_ids": list(plan.chunk_ids),
                "evidence_ids": list(plan.evidence_ids),
                "turn_ids": list(plan.turn_ids),
                "counts": {
                    "documents": plan.document_count,
                    "chunks": plan.chunk_count,
                    "evidence_items": plan.evidence_count,
                    "session_turns": plan.turn_count,
                },
            }
            for plan in plans
        ],
        "safety_note": (
            "Only source archive documents with metadata.debug_material_bundle=true "
            "and metadata.material_package_import!=true are selected. Imported user "
            "materials are intentionally excluded."
        ),
    }


def apply_cleanup_plan(db: Session, plans: Sequence[CleanupPackagePlan]) -> None:
    for plan in plans:
        for evidence_id in plan.evidence_ids:
            record = db.get(EvidenceItemRecord, evidence_id)
            if record is not None:
                db.delete(record)
        for chunk_id in plan.chunk_ids:
            record = db.get(DocumentChunkRecord, chunk_id)
            if record is not None:
                db.delete(record)
        for turn_id in plan.turn_ids:
            record = db.get(SessionTurnRecord, turn_id)
            if record is not None:
                db.delete(record)
        for document_id in plan.document_ids:
            record = db.get(DocumentRecord, document_id)
            if record is not None and is_source_archive_document(record):
                db.delete(record)
    db.commit()


def validate_artifact_passed(artifact_path: Path, *, force: bool) -> dict[str, Any]:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    validation = payload.get("validation") if isinstance(payload, dict) else {}
    if not force and not bool(validation.get("passed")):
        raise RuntimeError(
            "validation artifact is not passed; rerun validate or use --force only for controlled recovery"
        )
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("validation artifact does not contain a session_id")
    return payload


def publish_validated_archive(
    db: Session,
    *,
    validation_artifact: dict[str, Any],
    package_id: str,
    label: str,
    replace: bool,
    template: DemoTemplateDefinition | None = None,
) -> dict[str, Any]:
    selected_template = template or DEMO_TEMPLATE
    required_document_types = required_document_types_for(selected_template)
    assert_template_contract(selected_template)
    session_id = validation_artifact["session_id"]
    existing = build_cleanup_plan(db, package_id=package_id)
    if existing and not replace:
        raise RuntimeError(
            f"package {package_id!r} already exists; use --replace after taking a backup"
        )
    if existing:
        apply_cleanup_plan(db, existing)

    source_session = db.get(SessionRecord, session_id)
    if source_session is None:
        raise RuntimeError(f"validation session not found in current database: {session_id}")
    source_documents = list(
        db.scalars(
            select(DocumentRecord)
            .where(DocumentRecord.session_id == session_id)
            .order_by(DocumentRecord.filename.asc(), DocumentRecord.document_id.asc())
        )
    )
    source_by_type = {_document_type_from_artifact(document): document for document in source_documents}
    missing = [item for item in required_document_types if item not in source_by_type]
    if missing:
        raise RuntimeError(f"validation session is missing required document types: {missing}")

    archive_session = SessionRepository(db).create(
        declared_family=selected_template.visa_family,
        gate_status_json=GateService().initial_gate_status(selected_template.visa_family),
    )
    archive_session.profile_json = {
        "template_id": selected_template.template_id,
        "package_id": package_id,
        "label": label,
        "expected_facts": dict(selected_template.expected_facts),
    }
    db.add(archive_session)
    db.flush()

    created_documents: list[DocumentRecord] = []
    for document_type in required_document_types:
        created_documents.append(
            _copy_validated_document_as_archive_source(
                db,
                source_by_type[document_type],
                template=selected_template,
                archive_session_id=archive_session.session_id,
                package_id=package_id,
                label=label,
                source_validation_session_id=session_id,
                document_type=document_type,
            )
        )
    db.commit()
    return {
        "schema_version": "ds160.material_archive_publish_result.v1",
        "published_at": datetime.now(UTC).isoformat(),
        "package_id": package_id,
        "label": label,
        "template_id": selected_template.template_id,
        "source_validation_session_id": session_id,
        "archive_session_id": archive_session.session_id,
        "document_count": len(created_documents),
        "document_ids": [document.document_id for document in created_documents],
    }


def _document_type_from_artifact(document: DocumentRecord) -> str | None:
    artifact = dict(document.artifact_json or {})
    metadata = dict(artifact.get("metadata") or {})
    value = artifact.get("document_type") or metadata.get("document_type")
    return value if isinstance(value, str) and value else None


def _copy_validated_document_as_archive_source(
    db: Session,
    source_document: DocumentRecord,
    *,
    template: DemoTemplateDefinition,
    archive_session_id: str,
    package_id: str,
    label: str,
    source_validation_session_id: str,
    document_type: str,
) -> DocumentRecord:
    source_chunks = list(
        db.scalars(
            select(DocumentChunkRecord)
            .where(DocumentChunkRecord.document_id == source_document.document_id)
            .order_by(DocumentChunkRecord.ordinal.asc(), DocumentChunkRecord.chunk_id.asc())
        )
    )
    source_evidence = list(
        db.scalars(
            select(EvidenceItemRecord)
            .where(EvidenceItemRecord.document_id == source_document.document_id)
            .order_by(EvidenceItemRecord.evidence_id.asc())
        )
    )
    chunk_id_map = {chunk.chunk_id: f"chunk-{uuid4().hex[:12]}" for chunk in source_chunks}
    evidence_id_map = {item.evidence_id: f"evi-{uuid4().hex[:12]}" for item in source_evidence}
    document = DocumentRepository(db).create_document(
        session_id=archive_session_id,
        filename=source_document.filename,
        raw_bytes=source_document.raw_bytes or b"",
        raw_text=source_document.raw_text or "",
        artifact_json={},
    )
    document.status = source_document.status
    replacements = {
        source_document.document_id: document.document_id,
        source_document.session_id: archive_session_id,
        **chunk_id_map,
        **evidence_id_map,
    }
    artifact = _rewrite_ids(dict(source_document.artifact_json or {}), replacements)
    artifact["document_id"] = document.document_id
    artifact["session_id"] = archive_session_id
    artifact["document_type"] = document_type
    artifact.setdefault("content_type", "application/pdf")
    metadata = dict(artifact.get("metadata") or {})
    metadata.update(
        {
            "debug_material_bundle": True,
            "synthetic_bundle_id": package_id,
            "debug_bundle_scenario": template.template_id,
            "debug_bundle_scenario_label": label,
            "demo_template_id": template.template_id,
            "demo_template_archive_source": True,
            "archive_source_reason": ARCHIVE_SOURCE_REASON,
            "source_validation_session_id": source_validation_session_id,
            "source_document_id": source_document.document_id,
            "document_type": document_type,
        }
    )
    artifact["metadata"] = metadata
    document.artifact_json = artifact

    for chunk in source_chunks:
        db.add(
            DocumentChunkRecord(
                chunk_id=chunk_id_map[chunk.chunk_id],
                document_id=document.document_id,
                session_id=archive_session_id,
                ordinal=chunk.ordinal,
                page_number=chunk.page_number,
                text=chunk.text,
                metadata_json=_archive_child_metadata(
                    chunk.metadata_json,
                    template=template,
                    package_id=package_id,
                    source_validation_session_id=source_validation_session_id,
                ),
            )
        )
    for item in source_evidence:
        db.add(
            EvidenceItemRecord(
                evidence_id=evidence_id_map[item.evidence_id],
                session_id=archive_session_id,
                document_id=document.document_id,
                chunk_id=chunk_id_map.get(item.chunk_id, item.chunk_id),
                evidence_type=item.evidence_type,
                field_path=item.field_path,
                value=item.value,
                excerpt=item.excerpt,
                confidence=item.confidence,
                metadata_json=_archive_child_metadata(
                    item.metadata_json,
                    template=template,
                    package_id=package_id,
                    source_validation_session_id=source_validation_session_id,
                ),
            )
        )
    db.add(document)
    db.flush()
    return document


def _archive_child_metadata(
    metadata_json: dict[str, Any] | None,
    *,
    template: DemoTemplateDefinition | None = None,
    package_id: str,
    source_validation_session_id: str,
) -> dict[str, Any]:
    selected_template = template or DEMO_TEMPLATE
    metadata = dict(metadata_json or {})
    metadata.update(
        {
            "debug_material_bundle": True,
            "synthetic_bundle_id": package_id,
            "demo_template_id": selected_template.template_id,
            "demo_template_archive_source": True,
            "source_validation_session_id": source_validation_session_id,
        }
    )
    return metadata


def _rewrite_ids(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_ids(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_ids(item, replacements) for item in value]
    if isinstance(value, str):
        rewritten = value
        for old, new in replacements.items():
            rewritten = rewritten.replace(old, new)
        return rewritten
    return value


def backup_database_if_requested(*, backup_sqlite: bool, backup_confirmed: bool) -> str | None:
    if DATABASE_URL.startswith("sqlite:///"):
        db_path = Path(DATABASE_URL.replace("sqlite:///", "", 1))
        if not backup_sqlite:
            raise RuntimeError("SQLite DB writes require --backup-sqlite")
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        if not db_path.exists():
            return None
        backup_path = db_path.with_suffix(db_path.suffix + f".{utc_stamp()}.bak")
        shutil.copy2(db_path, backup_path)
        return str(backup_path)
    if not backup_confirmed:
        raise RuntimeError(
            "Non-SQLite DB writes require --backup-confirmed after taking an external backup such as pg_dump"
        )
    return "external-backup-confirmed"


def command_render(args: argparse.Namespace) -> int:
    template = lookup_template(args.template_id)
    manifest = render_materials(Path(args.out), template)
    print(json.dumps({"status": "rendered", "manifest": str(Path(args.out) / "manifest.json"), "documents": manifest["rendered_documents"]}, ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    template = lookup_template(args.template_id)
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else Path("artifacts/f1_demo_validation") / utc_stamp()
    payload = run_api_validation(
        base_url=args.base_url,
        artifact_dir=artifact_dir,
        template=template,
        auth_password_env=args.auth_password_env,
        auth_login_path=args.auth_login_path,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        drain_local_worker=args.drain_local_worker,
    )
    summary = {
        "status": payload["validation"]["status"],
        "session_id": payload["session_id"],
        "artifact": str(artifact_dir / "run.json"),
        "defect_count": len(payload["validation"]["defects"]),
        "warning_count": len(payload["validation"]["warnings"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if payload["validation"]["passed"] else 1


def command_publish(args: argparse.Namespace) -> int:
    template = lookup_template(args.template_id)
    artifact_payload = validate_artifact_passed(Path(args.artifact), force=args.force)
    backup_path = backup_database_if_requested(
        backup_sqlite=args.backup_sqlite,
        backup_confirmed=args.backup_confirmed,
    )
    with SessionLocal() as db:
        result = publish_validated_archive(
            db,
            validation_artifact=artifact_payload,
            template=template,
            package_id=args.package_id or template.package_id,
            label=args.label or template.label,
            replace=args.replace,
        )
    result["backup"] = backup_path
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_cleanup_archives(args: argparse.Namespace) -> int:
    package_id = args.package_id
    if args.template_id:
        template = lookup_template(args.template_id)
        package_id = package_id or template.package_id
    with SessionLocal() as db:
        plans = build_cleanup_plan(db, package_id=package_id)
        payload = cleanup_plan_payload(plans)
        if args.apply:
            backup_path = backup_database_if_requested(
                backup_sqlite=args.backup_sqlite,
                backup_confirmed=args.backup_confirmed,
            )
            apply_cleanup_plan(db, plans)
            payload["applied"] = True
            payload["backup"] = backup_path
        else:
            payload["applied"] = False
    if args.output:
        write_json(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="F-1 customer demo material package tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="render the stable six-document PDF package")
    render.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID, help="template registry id")
    render.add_argument("--out", default="artifacts/f1_demo_materials", help="output directory")
    render.set_defaults(func=command_render)

    validate = subparsers.add_parser("validate", help="run the real API upload/interview/report flow")
    validate.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID, help="template registry id")
    validate.add_argument("--base-url", default="http://127.0.0.1:8000", help="backend API base URL")
    validate.add_argument("--artifact-dir", help="artifact directory; defaults to artifacts/f1_demo_validation/<timestamp>")
    validate.add_argument("--auth-password-env", default="APP_AUTH_PASSWORD", help="env var holding login password/access key; empty disables login")
    validate.add_argument("--auth-login-path", default="/v1/auth/login", help="login endpoint for validation; use /v1/admin/login for admin-cookie production smoke")
    validate.add_argument("--timeout-seconds", type=float, default=180.0, help="max wait for material understanding")
    validate.add_argument("--poll-seconds", type=float, default=2.0, help="poll interval for reports/export")
    validate.add_argument("--drain-local-worker", action="store_true", help="drain local case_understanding jobs using DATABASE_URL while polling")
    validate.set_defaults(func=command_validate)

    publish = subparsers.add_parser("publish", help="promote a passed validation session into the material package archive")
    publish.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID, help="template registry id")
    publish.add_argument("--artifact", required=True, help="run.json from validate")
    publish.add_argument("--package-id", help="archive package id; defaults to the selected template package_id")
    publish.add_argument("--label", help="archive label; defaults to the selected template label")
    publish.add_argument("--replace", action="store_true", help="replace an existing source archive with the same package id")
    publish.add_argument("--force", action="store_true", help="allow publishing a failed artifact; only for controlled recovery")
    publish.add_argument("--backup-sqlite", action="store_true", help="copy SQLite DB before DB write")
    publish.add_argument("--backup-confirmed", action="store_true", help="confirm an external backup exists for non-SQLite DB")
    publish.set_defaults(func=command_publish)

    cleanup = subparsers.add_parser("cleanup-archives", help="dry-run or delete source archive packages only")
    cleanup.add_argument("--template-id", help="limit cleanup to the selected template package_id")
    cleanup.add_argument("--package-id", help="limit cleanup to one package id")
    cleanup.add_argument("--apply", action="store_true", help="perform deletion; default is dry-run")
    cleanup.add_argument("--backup-sqlite", action="store_true", help="copy SQLite DB before deletion")
    cleanup.add_argument("--backup-confirmed", action="store_true", help="confirm an external backup exists for non-SQLite DB")
    cleanup.add_argument("--output", help="write cleanup plan JSON")
    cleanup.set_defaults(func=command_cleanup_archives)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
