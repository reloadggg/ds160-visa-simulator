from __future__ import annotations

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


def _client() -> ChainlitBackendClient:
    return ChainlitBackendClient()


def _format_user_report(report: dict) -> str:
    lines = [
        f"当前结论：{report.get('outcome_label', '未知')}",
        f"摘要：{report.get('summary', '暂无摘要')}",
    ]

    missing_evidence = list(report.get("missing_evidence", []) or [])
    if missing_evidence:
        lines.append(f"缺失材料：{', '.join(missing_evidence)}")

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


async def _choose_document_type(options: list[str]) -> str | None:
    unique_options = list(dict.fromkeys(options))
    if not unique_options:
        return None
    if len(unique_options) == 1:
        return unique_options[0]

    selection = await cl.AskActionMessage(
        content="请先选择这份材料对应的类型",
        actions=[
            cl.Action(
                name="select_document_type",
                payload={"document_type": option},
                label=option,
            )
            for option in unique_options
        ],
        timeout=180,
    ).send()
    if not selection:
        return None
    return selection["payload"]["document_type"]


async def _send_report_actions() -> None:
    pending_requested_documents = list(
        cl.user_session.get("pending_requested_documents", [])
    )
    await cl.Message(
        content=(
            "可随时查看当前报告。若仍缺材料，可点击“上传材料”或使用输入框附件按钮上传 "
            "PDF/PNG/JPG/JPEG，单文件不超过 64MB。"
        ),
        actions=_build_session_actions(pending_requested_documents),
    ).send()


async def _prompt_for_required_files(requested_documents: list[str]) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        return

    client = _client()
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
        feedback_message = response.get("feedback_message")
        if feedback_message:
            await cl.Message(content=feedback_message).send()

    cl.user_session.set("pending_requested_documents", list(requested_documents))
    await cl.Message(content="材料已接收，请继续发送下一条消息。").send()


async def _upload_message_elements(message: cl.Message) -> int:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        return 0

    elements = list(getattr(message, "elements", []) or [])
    if not elements:
        return 0

    uploaded_count = 0
    client = _client()
    pending_requested_documents = list(cl.user_session.get("pending_requested_documents", []))
    required_initial_package = list(cl.user_session.get("required_initial_package", []))
    upload_options = pending_requested_documents or required_initial_package
    for element in elements:
        path = getattr(element, "path", None)
        name = getattr(element, "name", None)
        if not path or not name:
            continue

        raw_bytes = Path(path).read_bytes()
        content_type = getattr(element, "mime", None) or "application/octet-stream"
        document_type = await _choose_document_type(upload_options)
        response = await client.upload_file(
            session_id,
            name,
            raw_bytes,
            content_type,
            document_type,
        )
        feedback_message = response.get("feedback_message")
        if feedback_message:
            await cl.Message(content=feedback_message).send()
        uploaded_count += 1

    return uploaded_count


@cl.action_callback("upload_requested_documents")
async def upload_requested_documents(_action) -> None:
    requested_documents = list(cl.user_session.get("pending_requested_documents", []))
    required_initial_package = list(cl.user_session.get("required_initial_package", []))
    upload_options = requested_documents or required_initial_package
    if not upload_options:
        await cl.Message(content="当前没有可上传的材料类型。").send()
        return
    if requested_documents:
        await _prompt_for_required_files(requested_documents)
        return

    document_type = await _choose_document_type(upload_options)
    if not document_type:
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
            f"必需材料包：{', '.join(required['required_initial_package'])}\n"
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
        await cl.Message(content="材料已接收，请继续发送下一条消息。").send()
        await _send_report_actions()
        return

    previous_pending = cl.user_session.get("pending_requested_documents", [])
    response = await _client().post_message(session_id, message.content)
    cl.user_session.set("last_governor_decision", response["governor_decision"])
    await cl.Message(content=response["assistant_message"]).send()

    requested_documents = list(response.get("requested_documents", []))
    gate_overall_status = (
        response.get("gate_progress", {}) or {}
    ).get("overall_status")
    should_prompt_upload = (
        response["governor_decision"] == "need_more_evidence"
        and bool(requested_documents)
        and requested_documents != previous_pending
        and gate_overall_status != "waiting_for_parse"
    )
    if should_prompt_upload:
        await _prompt_for_required_files(requested_documents)
    else:
        cl.user_session.set("pending_requested_documents", requested_documents)

    await _send_report_actions()
