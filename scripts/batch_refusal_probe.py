from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
import httpx


SCENARIOS = [
    "school_mismatch_bundle",
    "identity_mismatch_bundle",
    "funding_shortfall_bundle",
    "sponsor_chain_gap_bundle",
    "claim_vs_document_bundle",
]

SCENARIO_SEEDS = {
    "school_mismatch_bundle": (
        "F-1 student case. Applicant Morgan Lee says they will attend New York "
        "University for an MS Computer Science program in Fall 2026, but one "
        "school document should conflict with another school document."
    ),
    "identity_mismatch_bundle": (
        "F-1 student case. Applicant Morgan Lee is a Chinese citizen applying "
        "for MS Computer Science, but the passport number should conflict "
        "between identity documents."
    ),
    "funding_shortfall_bundle": (
        "F-1 student case. Applicant Morgan Lee will study MS Computer Science, "
        "but the available family funds should be clearly below the first-year "
        "I-20 cost."
    ),
    "sponsor_chain_gap_bundle": (
        "F-1 student case. Applicant Morgan Lee relies on parents' business or "
        "equity-transfer proceeds, but the source-of-funds chain should be "
        "incomplete and hard to verify."
    ),
    "claim_vs_document_bundle": (
        "F-1 student case. Applicant Morgan Lee verbally claims self-funding, "
        "but the documents should show parent sponsorship."
    ),
}

FOLLOWUP_ANSWERS = [
    "我选择这个项目是因为它和我的计算机科学背景以及未来回国做 AI 工程产品的计划一致。",
    "我的主要资金来自家庭支持，我会按照材料里的资金证明来解释。",
    "我计划完成学业后回到中国，从事 AI 工程和教育科技产品相关工作。",
    "如果材料之间有不一致，我目前只能按我提交的文件说明，具体以材料为准。",
    "我的学习计划是完成硕士课程，积累工程能力，然后回国发展。",
    "关于资金来源，我理解签证官需要看可核验材料，我会配合说明。",
    "关于学校和项目，我会按 I-20 和录取材料解释。",
    "如果有需要澄清的地方，我可以继续回答。",
]

TERMINAL_DECISIONS = {"simulated_refusal", "high_risk_review"}
TERMINAL_RESULTS = {"refused", "not_passed"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def compact_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body: Any = exc.response.json()
        except Exception:
            body = exc.response.text[:1000]
        return {
            "type": exc.__class__.__name__,
            "status_code": exc.response.status_code,
            "body": body,
        }
    return {"type": exc.__class__.__name__, "message": str(exc)}


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def build_pdf_bytes(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text, fontsize=10)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


def make_seed(scenario: str, index: int) -> str:
    return (
        f"{SCENARIO_SEEDS[scenario]} Case number {index + 1}. "
        "Use realistic names, dates, schools, document text, and field values. "
        "Do not include debug labels, oracle notes, or expected findings in the "
        "visible material text."
    )


def classify(
    *,
    report: dict[str, Any] | None,
    last_decision: str | None,
    turns: list[dict[str, Any]],
    max_turns: int,
) -> str:
    report = report or {}
    interview_status = report.get("interview_status")
    interview_result = report.get("interview_result")
    risk_level = report.get("risk_level")
    if last_decision == "simulated_refusal" or interview_status == "simulated_refusal":
        return "refused"
    if interview_result == "refused":
        return "refused"
    if (
        last_decision in TERMINAL_DECISIONS
        or interview_status in TERMINAL_DECISIONS
        or interview_result in TERMINAL_RESULTS
        or risk_level == "high"
    ):
        return "high_risk_or_not_passed"
    if len(turns) >= max_turns:
        return "not_converged"
    return "in_progress"


def case_documents(scenario: str, case_index: int) -> list[dict[str, str]]:
    suffix = f"{case_index + 1:03d}"
    base_school = "New York University"
    admission_school = base_school
    ds160_passport = f"P12345{suffix}"
    passport_number = ds160_passport
    funding_source = "parents"
    funding_balance = "90000"
    funding_source_detail = "long-term salary income and family savings"

    if scenario == "school_mismatch_bundle":
        admission_school = "Columbia University"
    elif scenario == "identity_mismatch_bundle":
        passport_number = f"P99887{suffix}"
    elif scenario == "funding_shortfall_bundle":
        funding_balance = "9800"
    elif scenario == "sponsor_chain_gap_bundle":
        funding_source_detail = "family company equity transfer proceeds without tax or payment trail"
    elif scenario == "claim_vs_document_bundle":
        funding_source = "parents"

    return [
        {
            "document_type": "ds160",
            "filename": f"{suffix}_ds160.pdf",
            "text": (
                "DS-160 Confirmation and Application Extract\n"
                "Full Name: Morgan Lee\n"
                f"Passport Number: {ds160_passport}\n"
                "Travel Purpose: STUDENT (F1)\n"
                f"School Name: {base_school}\n"
                "Program: MS Computer Science\n"
                f"Funding Source: {funding_source}\n"
            ),
        },
        {
            "document_type": "passport_bio",
            "filename": f"{suffix}_passport.pdf",
            "text": (
                "Passport Biographic Page OCR Text\n"
                "Full Name: Morgan Lee\n"
                f"Passport Number: {passport_number}\n"
                "Nationality: China\n"
            ),
        },
        {
            "document_type": "i20",
            "filename": f"{suffix}_i20.pdf",
            "text": (
                "Form I-20 Certificate of Eligibility\n"
                "SEVIS ID: N1234567890\n"
                f"School Name: {base_school}\n"
                "Program: MS Computer Science\n"
                "First Year Cost Total: USD 68000\n"
            ),
        },
        {
            "document_type": "admission_letter",
            "filename": f"{suffix}_admission.pdf",
            "text": (
                "Graduate Admission Letter\n"
                f"School Name: {admission_school}\n"
                "Program: MS Computer Science\n"
                "Start Term: Fall 2026\n"
            ),
        },
        {
            "document_type": "funding_proof",
            "filename": f"{suffix}_funding.pdf",
            "text": (
                "Bank Statement and Sponsor Letter\n"
                "Full Name: Morgan Lee\n"
                "Primary Source of Support: parents\n"
                f"Available Balance: USD {funding_balance}\n"
                f"Source of Funds: {funding_source_detail}\n"
            ),
        },
        {
            "document_type": "relationship_proof_between_applicant_and_sponsors",
            "filename": f"{suffix}_relationship.pdf",
            "text": (
                "Household Register Relationship Certificate\n"
                "Full Name: Morgan Lee\n"
                "Father: Li Wei\n"
                "Mother: Zhang Min\n"
                "Relationship: Child of Li Wei and Zhang Min\n"
            ),
        },
    ]


def upload_case_documents(
    client: httpx.Client,
    *,
    base_url: str,
    session_id: str,
    scenario: str,
    case_index: int,
) -> list[dict[str, Any]]:
    uploaded: list[dict[str, Any]] = []
    for document in case_documents(scenario, case_index):
        raw_bytes = build_pdf_bytes(document["text"])
        response = client.post(
            f"{base_url}/v1/sessions/{session_id}/files",
            data={"document_type": document["document_type"]},
            files={
                "file": (
                    document["filename"],
                    raw_bytes,
                    "application/pdf",
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        uploaded.append(
            {
                "document_type": document["document_type"],
                "filename": document["filename"],
                "document_id": payload.get("document_id"),
                "job_id": payload.get("job_id"),
                "understanding_status": payload.get("understanding_status"),
            }
        )
    return uploaded


def wait_for_materials(
    client: httpx.Client,
    *,
    base_url: str,
    session_id: str,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = request_json(
            client,
            "GET",
            f"{base_url}/v1/sessions/{session_id}/debug/runtime",
        )
        materials = latest.get("material_understanding") or []
        statuses = {
            item.get("understanding_status")
            for item in materials
            if item.get("understanding_status")
        }
        if len(materials) >= 6 and statuses and statuses <= {"completed", "failed"}:
            return latest
        time.sleep(0.5)
    return latest


def run_uploaded_case(
    client: httpx.Client,
    *,
    base_url: str,
    case_index: int,
    scenario: str,
    max_turns: int,
) -> dict[str, Any]:
    started_at = now_iso()
    session = request_json(
        client,
        "POST",
        f"{base_url}/v1/sessions",
        json={"declared_family": "f1"},
    )
    session_id = session["session_id"]
    uploaded_documents = upload_case_documents(
        client,
        base_url=base_url,
        session_id=session_id,
        scenario=scenario,
        case_index=case_index,
    )
    runtime_after_upload = wait_for_materials(
        client,
        base_url=base_url,
        session_id=session_id,
    )
    materials = runtime_after_upload.get("material_understanding") or []
    material_statuses = [item.get("understanding_status") for item in materials]

    turns: list[dict[str, Any]] = []
    last_decision: str | None = None
    report: dict[str, Any] | None = request_json(
        client,
        "GET",
        f"{base_url}/v1/sessions/{session_id}/reports/user",
    )
    outcome = classify(
        report=report,
        last_decision=last_decision,
        turns=turns,
        max_turns=max_turns,
    )

    for turn_index in range(max_turns):
        if outcome in {"refused", "high_risk_or_not_passed"}:
            break
        answer = answer_for_scenario(scenario, turn_index)
        message = request_json(
            client,
            "POST",
            f"{base_url}/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": answer},
        )
        last_decision = message.get("governor_decision")
        report = request_json(
            client,
            "GET",
            f"{base_url}/v1/sessions/{session_id}/reports/user",
        )
        turns.append(
            {
                "turn_index": turn_index + 1,
                "answer": answer,
                "governor_decision": last_decision,
                "interview_status": report.get("interview_status"),
                "interview_result": report.get("interview_result"),
                "risk_level": report.get("risk_level"),
                "assistant_message": message.get("assistant_message"),
                "current_key_question": report.get("current_key_question"),
            }
        )
        outcome = classify(
            report=report,
            last_decision=last_decision,
            turns=turns,
            max_turns=max_turns,
        )

    final_report = report or {}
    return {
        "case_index": case_index,
        "mode": "upload",
        "scenario": scenario,
        "session_id": session_id,
        "uploaded_documents": uploaded_documents,
        "material_statuses": material_statuses,
        "outcome": outcome,
        "turn_count": len(turns),
        "final_governor_decision": last_decision,
        "final_interview_status": final_report.get("interview_status"),
        "final_interview_result": final_report.get("interview_result"),
        "final_interview_result_label": final_report.get("interview_result_label"),
        "final_risk_level": final_report.get("risk_level"),
        "final_summary": final_report.get("summary"),
        "turns": turns,
        "started_at": started_at,
        "finished_at": now_iso(),
    }


def answer_for_scenario(scenario: str, turn_index: int) -> str:
    if scenario == "claim_vs_document_bundle":
        answers = [
            "我是自己出钱，不是父母资助。",
            "这些钱主要来自我自己的积蓄，不需要父母承担。",
            "如果材料写父母资助，那可能是填错了，但我现在说的是自费。",
        ]
        return answers[turn_index % len(answers)]
    if scenario == "identity_mismatch_bundle":
        answers = [
            "我的护照号码就是我材料里写的那个号码。",
            "我不确定为什么不同文件上号码不一样，但我认为护照页是准的。",
            "DS-160 上如果不同，可能是我填写时看错了。",
        ]
        return answers[turn_index % len(answers)]
    if scenario == "school_mismatch_bundle":
        answers = [
            "我去纽约大学读计算机科学硕士。",
            "录取材料如果写了另一所学校，可能是我同时申请过，但我现在计划去 NYU。",
            "I-20 上的学校应该是最终学校。",
        ]
        return answers[turn_index % len(answers)]
    if scenario == "funding_shortfall_bundle":
        answers = [
            "我的资金应该够第一年费用。",
            "如果银行证明金额不够，我之后可以再补充其他资金。",
            "目前主要就是父母存款支持。",
        ]
        return answers[turn_index % len(answers)]
    if scenario == "sponsor_chain_gap_bundle":
        answers = [
            "资金来自父母公司股权转让收入。",
            "目前我没有带完整的税务和转账链路材料。",
            "我只能说明这是家庭资金，但细节文件还不完整。",
        ]
        return answers[turn_index % len(answers)]
    return FOLLOWUP_ANSWERS[turn_index % len(FOLLOWUP_ANSWERS)]


def run_case(
    client: httpx.Client,
    *,
    base_url: str,
    case_index: int,
    scenario: str,
    max_turns: int,
) -> dict[str, Any]:
    started_at = now_iso()
    session = request_json(
        client,
        "POST",
        f"{base_url}/v1/sessions",
        json={"declared_family": "f1"},
    )
    session_id = session["session_id"]

    bundle = request_json(
        client,
        "POST",
        f"{base_url}/v1/sessions/{session_id}/debug/material-bundles",
        json={
            "scenario": scenario,
            "include_synthetic_user_turns": True,
            "seed_text": make_seed(scenario, case_index),
            "generation_mode": "ai_if_available",
        },
    )

    turns: list[dict[str, Any]] = []
    last_decision = bundle.get("governor_decision")
    report: dict[str, Any] | None = request_json(
        client,
        "GET",
        f"{base_url}/v1/sessions/{session_id}/reports/user",
    )
    outcome = classify(
        report=report,
        last_decision=last_decision,
        turns=turns,
        max_turns=max_turns,
    )

    for turn_index in range(max_turns):
        if outcome in {"refused", "high_risk_or_not_passed"}:
            break
        answer = FOLLOWUP_ANSWERS[turn_index % len(FOLLOWUP_ANSWERS)]
        message = request_json(
            client,
            "POST",
            f"{base_url}/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": answer},
        )
        last_decision = message.get("governor_decision")
        report = request_json(
            client,
            "GET",
            f"{base_url}/v1/sessions/{session_id}/reports/user",
        )
        turns.append(
            {
                "turn_index": turn_index + 1,
                "answer": answer,
                "governor_decision": last_decision,
                "interview_status": report.get("interview_status"),
                "interview_result": report.get("interview_result"),
                "risk_level": report.get("risk_level"),
                "assistant_message": message.get("assistant_message"),
                "current_key_question": report.get("current_key_question"),
            }
        )
        outcome = classify(
            report=report,
            last_decision=last_decision,
            turns=turns,
            max_turns=max_turns,
        )

    final_report = report or {}
    return {
        "case_index": case_index,
        "scenario": scenario,
        "session_id": session_id,
        "bundle_id": bundle.get("bundle_id"),
        "document_count": len(bundle.get("documents") or []),
        "bundle_refresh_error": bundle.get("main_flow_refresh_error"),
        "bundle_governor_decision": bundle.get("governor_decision"),
        "outcome": outcome,
        "turn_count": len(turns),
        "final_governor_decision": last_decision,
        "final_interview_status": final_report.get("interview_status"),
        "final_interview_result": final_report.get("interview_result"),
        "final_interview_result_label": final_report.get("interview_result_label"),
        "final_risk_level": final_report.get("risk_level"),
        "final_summary": final_report.get("summary"),
        "turns": turns,
        "started_at": started_at,
        "finished_at": now_iso(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=int, default=100)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--mode", choices=["ai", "upload"], default="upload")
    parser.add_argument(
        "--output",
        default="artifacts/refusal-probe.jsonl",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    summary: dict[str, int] = {}
    scenario_summary: dict[str, dict[str, int]] = {}
    with httpx.Client(timeout=args.timeout) as client, output.open(
        "w",
        encoding="utf-8",
    ) as fh:
        for index in range(args.cases):
            scenario = SCENARIOS[index % len(SCENARIOS)]
            try:
                if args.mode == "ai":
                    result = run_case(
                        client,
                        base_url=args.base_url.rstrip("/"),
                        case_index=index,
                        scenario=scenario,
                        max_turns=args.max_turns,
                    )
                else:
                    result = run_uploaded_case(
                        client,
                        base_url=args.base_url.rstrip("/"),
                        case_index=index,
                        scenario=scenario,
                        max_turns=args.max_turns,
                    )
            except Exception as exc:
                result = {
                    "case_index": index,
                    "scenario": scenario,
                    "outcome": "error",
                    "error": compact_error(exc),
                    "finished_at": now_iso(),
                }
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            fh.flush()

            outcome = str(result.get("outcome") or "unknown")
            summary[outcome] = summary.get(outcome, 0) + 1
            scenario_bucket = scenario_summary.setdefault(scenario, {})
            scenario_bucket[outcome] = scenario_bucket.get(outcome, 0) + 1
            print(
                json.dumps(
                    {
                        "case": index + 1,
                        "scenario": scenario,
                        "outcome": outcome,
                        "session_id": result.get("session_id"),
                        "turn_count": result.get("turn_count"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            time.sleep(0.1)

    print(
        json.dumps(
            {
                "cases": args.cases,
                "mode": args.mode,
                "max_turns": args.max_turns,
                "output": str(output),
                "summary": summary,
                "scenario_summary": scenario_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
