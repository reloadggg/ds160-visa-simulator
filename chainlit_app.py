from __future__ import annotations

import os
from pathlib import Path

import chainlit as cl

from app.services.file_service import ALLOWED_UPLOAD_MIME_TYPES
from app.services.file_service import MAX_UPLOAD_SIZE_MB
from app.ui.chainlit_client import ChainlitBackendClient


_FAMILY_OPTIONS = [
    ("f1", "F-1"),
    ("j1", "J-1"),
    ("b1_b2", "B-1/B-2"),
    ("h1b", "H-1B"),
]
_UPLOAD_ACCEPT = list(ALLOWED_UPLOAD_MIME_TYPES)
_INTERVIEW_STATUS_LABELS = {
    "continue_interview": "继续问答",
    "verify_key_issue": "核验关键问题",
    "waiting_key_proof": "等待关键证明",
    "high_risk_review": "高风险复核",
    "simulated_refusal": "模拟拒签",
}
_RISK_LEVEL_LABELS = {
    "none": "无明显风险",
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}
_ALLOWED_NEXT_ACTION_LABELS = {
    "answer_question": "继续回答当前问题",
    "continue_interview": "继续面谈",
    "clarify_key_issue": "补充说明当前关键问题",
    "upload_key_proof": "上传关键证明",
    "explain_missing_proof": "先说明暂时缺少的原因",
    "wait_for_review": "等待进一步复核",
    "review_refusal_result": "查看模拟拒签结果并准备补强",
}
_UPLOAD_FEEDBACK_STATUS_LABELS = {
    "helpful": "上传反馈：已帮助当前主线。",
    "partial_helpful": "上传反馈：部分帮助当前主线。",
    "not_helpful": "上传反馈：对当前主线没有直接帮助。",
}
_UNSPECIFIED_DOCUMENT_TYPE_LABEL = "其他材料 / 暂不指定类型"
_DOCUMENT_TYPE_SELECTION_CANCELLED = object()


def _client() -> ChainlitBackendClient:
    configured_base_url = os.getenv("CHAINLIT_BACKEND_BASE_URL")
    if configured_base_url:
        return ChainlitBackendClient(base_url=configured_base_url)

    from app.main import app as fastapi_app

    return ChainlitBackendClient(app=fastapi_app)


def _map_interview_status(status: str | None) -> str:
    if not status:
        return "状态待确认"
    return _INTERVIEW_STATUS_LABELS.get(status, "状态待确认")


def _map_risk_level(risk_level: str | None) -> str:
    if not risk_level:
        return "风险待确认"
    return _RISK_LEVEL_LABELS.get(risk_level, "风险待确认")


def _map_allowed_next_actions(actions: list[str]) -> str:
    if not actions:
        return "暂无"
    return "、".join(
        _ALLOWED_NEXT_ACTION_LABELS.get(action, "请按当前指引继续操作")
        for action in actions
    )


def _format_user_report(report: dict) -> str:
    lines = [
        f"当前结论：{report.get('outcome_label', '未知')}",
        f"摘要：{report.get('summary', '暂无摘要')}",
        (
            "当前状态："
            f"{_map_interview_status(report.get('interview_status'))}"
            f"（{_map_risk_level(report.get('risk_level'))}）"
        ),
        f"当前关键问题：{report.get('current_key_question') or '暂无'}",
        f"当前关键证明：{report.get('current_key_proof') or '暂无'}",
    ]

    missing_evidence = list(report.get("missing_evidence", []) or [])
    if missing_evidence:
        lines.append(f"缺失材料：{', '.join(missing_evidence)}")

    lines.append(
        "建议动作："
        + _map_allowed_next_actions(list(report.get("allowed_next_actions", []) or []))
    )

    recommendations = list(report.get("recommended_improvements", []) or [])
    if recommendations:
        lines.append("建议：")
        lines.extend(f"- {item}" for item in recommendations)

    return "\n".join(lines)


def _format_internal_report(report: dict) -> str:
    return "内部报告（调试信息）\n" + str(report)


def _save_session_state(
    *,
    session_id: str,
    declared_family: str,
    required_initial_package: list[str],
) -> None:
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("declared_family", declared_family)
    cl.user_session.set("required_initial_package", required_initial_package)
    cl.user_session.set("last_governor_decision", None)
    cl.user_session.set("last_gate_progress", None)
    cl.user_session.set("pending_requested_documents", list(required_initial_package))


def _build_session_actions(pending_requested_documents: list[str]) -> list[cl.Action]:
    actions = [
        cl.Action(
            name="upload_requested_documents",
            payload={},
            label="上传材料",
        ),
        cl.Action(
            name="show_user_report",
            payload={},
            label="查看用户报告",
        ),
        cl.Action(
            name="show_internal_report",
            payload={},
            label="查看内部报告",
        ),
    ]
    return actions


async def _choose_document_type(options: list[str]) -> str | None | object:
    unique_options = list(dict.fromkeys(options))
    if not unique_options:
        return None

    selection = await cl.AskActionMessage(
        content="请先选择这份材料对应的类型",
        actions=[
            cl.Action(
                name="select_document_type",
                payload={"document_type": option},
                label=option,
            )
            for option in unique_options
        ]
        + [
            cl.Action(
                name="select_document_type",
                payload={"document_type": None},
                label=_UNSPECIFIED_DOCUMENT_TYPE_LABEL,
            )
        ],
        timeout=180,
    ).send()
    if not selection:
        return _DOCUMENT_TYPE_SELECTION_CANCELLED
    return selection["payload"]["document_type"]


async def _send_report_actions() -> None:
    pending_requested_documents = list(
        cl.user_session.get("pending_requested_documents", [])
    )
    await cl.Message(
        content=(
            "可继续回答当前问题。若当前最缺材料，可点击“上传材料”或使用输入框附件按钮随时补充 "
            "PDF/PNG/JPG/JPEG，单文件不超过 64MB。"
        ),
        actions=_build_session_actions(pending_requested_documents),
    ).send()


def _build_soft_gate_cta(requested_documents: list[str]) -> str | None:
    if not requested_documents:
        return None
    return f"当前最缺 {requested_documents[0]}，可现在上传，也可继续解释。"


async def _prompt_for_required_files(requested_documents: list[str]) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        return

    client = _client()
    uploaded_any = False
    if not requested_documents:
        files = await cl.AskFileMessage(
            content=(
                "请上传你认为有帮助的材料。"
                "可先不指定类型，系统会尝试自行归类。"
                "仅支持 PDF/PNG/JPG/JPEG，单文件不超过 64MB。"
            ),
            accept=_UPLOAD_ACCEPT,
            max_files=1,
            max_size_mb=MAX_UPLOAD_SIZE_MB,
            timeout=180,
        ).send()
        if files:
            item = files[0]
            raw_bytes = Path(item.path).read_bytes()
            response = await client.upload_file(
                session_id,
                item.name,
                raw_bytes,
                item.type,
                document_type=None,
            )
            await _handle_upload_response(response)
            uploaded_any = True

    for document_type in requested_documents:
        files = await cl.AskFileMessage(
            content=(
                f"请上传材料：{document_type}。"
                "仅支持 PDF/PNG/JPG/JPEG，单文件不超过 64MB。"
            ),
            accept=_UPLOAD_ACCEPT,
            max_files=1,
            max_size_mb=MAX_UPLOAD_SIZE_MB,
            timeout=180,
        ).send()
        if not files:
            continue
        item = files[0]
        raw_bytes = Path(item.path).read_bytes()
        response = await client.upload_file(
            session_id,
            item.name,
            raw_bytes,
            item.type,
            document_type=document_type,
        )
        await _handle_upload_response(response)
        uploaded_any = True

    if uploaded_any:
        await cl.Message(content="材料已接收，可继续回答或继续上传。").send()


def _format_upload_feedback(response: dict) -> str | None:
    main_flow_feedback = response.get("main_flow_feedback") or {}
    feedback_message = (
        main_flow_feedback.get("message") or response.get("feedback_message") or ""
    ).strip()
    candidates = list(response.get("document_type_candidates", []) or [])
    supported_claims = list(response.get("supported_claims", []) or [])
    relevance = response.get("relevance")
    confidence = response.get("confidence")
    extras: list[str] = []
    if candidates:
        extras.append(f"候选类型：{', '.join(candidates)}")
    if relevance:
        extras.append(f"相关性：{relevance}")
    if supported_claims:
        extras.append(f"支持主张：{', '.join(supported_claims)}")
    if isinstance(confidence, (int, float)) and confidence > 0:
        extras.append(f"置信度：{confidence:.2f}")

    if not feedback_message and not extras:
        return None

    status = main_flow_feedback.get("status")
    status_label = _UPLOAD_FEEDBACK_STATUS_LABELS.get(status)
    lines: list[str] = []
    if status_label:
        lines.append(status_label)
    if feedback_message:
        lines.append(feedback_message)
    lines.extend(extras)
    if candidates and response.get("document_type") not in candidates:
        lines.append("如识别类型不准，可在前端下次上传时手动指定类型纠偏。")
    return "\n".join(lines)


async def _handle_upload_response(response: dict) -> None:
    cl.user_session.set(
        "pending_requested_documents",
        list(response.get("requested_documents", []) or []),
    )
    cl.user_session.set("last_gate_progress", response.get("gate_progress"))

    feedback = _format_upload_feedback(response)
    if feedback:
        await cl.Message(content=feedback).send()


async def _upload_message_elements(message: cl.Message) -> int:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        return 0

    elements = list(getattr(message, "elements", []) or [])
    if not elements:
        return 0

    uploaded_count = 0
    client = _client()
    for element in elements:
        path = getattr(element, "path", None)
        name = getattr(element, "name", None)
        if not path or not name:
            continue

        raw_bytes = Path(path).read_bytes()
        content_type = getattr(element, "mime", None) or "application/octet-stream"
        pending_requested_documents = list(
            cl.user_session.get("pending_requested_documents", [])
        )
        required_initial_package = list(
            cl.user_session.get("required_initial_package", [])
        )
        upload_options = pending_requested_documents or required_initial_package
        if upload_options:
            document_type = await _choose_document_type(upload_options)
            if document_type is _DOCUMENT_TYPE_SELECTION_CANCELLED:
                continue
        else:
            document_type = None
        response = await client.upload_file(
            session_id,
            name,
            raw_bytes,
            content_type,
            document_type,
        )
        await _handle_upload_response(response)
        uploaded_count += 1

    return uploaded_count


@cl.action_callback("upload_requested_documents")
async def upload_requested_documents(_action) -> None:
    requested_documents = list(cl.user_session.get("pending_requested_documents", []))
    required_initial_package = list(cl.user_session.get("required_initial_package", []))
    upload_options = requested_documents or required_initial_package
    if not upload_options:
        await _prompt_for_required_files([])
        return
    if requested_documents:
        await _prompt_for_required_files(requested_documents)
        return

    document_type = await _choose_document_type(upload_options)
    if document_type is _DOCUMENT_TYPE_SELECTION_CANCELLED:
        return
    if document_type is None:
        await _prompt_for_required_files([])
        return
    await _prompt_for_required_files([document_type])


@cl.action_callback("show_user_report")
async def show_user_report(_action) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        await cl.Message(content="当前还没有有效会话。").send()
        return
    report = await _client().get_user_report(session_id)
    await cl.Message(content=_format_user_report(report)).send()


@cl.action_callback("show_internal_report")
async def show_internal_report(_action) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        await cl.Message(content="当前还没有有效会话。").send()
        return
    report = await _client().get_internal_report(session_id)
    await cl.Message(content=_format_internal_report(report)).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    await cl.Message(
        content="欢迎使用 DS-160 模拟器。请先选择签证家族。"
    ).send()
    selection = await cl.AskActionMessage(
        content="选择签证家族",
        actions=[
            cl.Action(
                name="select_family",
                payload={"declared_family": family},
                label=label,
            )
            for family, label in _FAMILY_OPTIONS
        ],
        timeout=180,
    ).send()
    if not selection:
        await cl.Message(content="未选择签证家族，本次会话已结束。").send()
        return

    declared_family = selection["payload"]["declared_family"]
    client = _client()
    session = await client.create_session(declared_family)
    required = await client.get_required_package(session["session_id"])
    _save_session_state(
        session_id=session["session_id"],
        declared_family=declared_family,
        required_initial_package=required["required_initial_package"],
    )
    await cl.Message(
        content=(
            f"已创建 {declared_family} 会话。\n"
            f"当前建议优先准备：{', '.join(required['required_initial_package'])}\n"
            "你可以先开始回答，也可以随时上传材料补充主线。\n"
            "上传支持 PDF/PNG/JPG/JPEG，单文件不超过 64MB。"
        )
    ).send()
    await _send_report_actions()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        await cl.Message(content="当前没有活跃会话，请先重新选择签证家族。").send()
        return

    uploaded_count = await _upload_message_elements(message)
    if uploaded_count and not message.content.strip():
        await cl.Message(content="材料已接收，可继续回答或继续上传。").send()
        await _send_report_actions()
        return
    if not uploaded_count and getattr(message, "elements", None) and not message.content.strip():
        return

    response = await _client().post_message(session_id, message.content)
    cl.user_session.set("last_governor_decision", response["governor_decision"])
    cl.user_session.set("last_gate_progress", response.get("gate_progress"))
    await cl.Message(content=response["assistant_message"]).send()

    requested_documents = list(response.get("requested_documents", []))
    gate_overall_status = (
        response.get("gate_progress", {}) or {}
    ).get("overall_status")
    should_send_soft_gate_cta = (
        response["governor_decision"] == "need_more_evidence"
        and bool(requested_documents)
        and gate_overall_status != "waiting_for_parse"
    )
    cl.user_session.set("pending_requested_documents", requested_documents)
    if should_send_soft_gate_cta:
        cta_message = _build_soft_gate_cta(requested_documents)
        if cta_message:
            await cl.Message(content=cta_message).send()

    await _send_report_actions()
