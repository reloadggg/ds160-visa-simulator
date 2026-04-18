from __future__ import annotations

from pathlib import Path

import chainlit as cl

from app.ui.chainlit_client import ChainlitBackendClient


_FAMILY_OPTIONS = [
    ("f1", "F-1"),
    ("j1", "J-1"),
    ("b1_b2", "B-1/B-2"),
    ("h1b", "H-1B"),
]


def _client() -> ChainlitBackendClient:
    return ChainlitBackendClient()


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
    cl.user_session.set("pending_requested_documents", [])


async def _send_report_actions() -> None:
    await cl.Message(
        content="可随时查看当前报告。",
        actions=[
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
        ],
    ).send()


async def _prompt_for_required_files(requested_documents: list[str]) -> None:
    files = await cl.AskFileMessage(
        content=(
            "请上传以下材料后继续："
            + ", ".join(requested_documents)
        ),
        accept=["*/*"],
        max_files=max(len(requested_documents), 1),
        max_size_mb=20,
        timeout=180,
    ).send()
    if not files:
        return

    session_id = cl.user_session.get("session_id")
    if not session_id:
        return

    client = _client()
    for item in files:
        raw_bytes = Path(item.path).read_bytes()
        await client.upload_file(session_id, item.name, raw_bytes, item.type)

    cl.user_session.set("pending_requested_documents", list(requested_documents))
    await cl.Message(content="材料已接收，请继续发送下一条消息。").send()


@cl.action_callback("show_user_report")
async def show_user_report(_action) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        await cl.Message(content="当前还没有有效会话。").send()
        return
    report = await _client().get_user_report(session_id)
    await cl.Message(content=report).send()


@cl.action_callback("show_internal_report")
async def show_internal_report(_action) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        await cl.Message(content="当前还没有有效会话。").send()
        return
    report = await _client().get_internal_report(session_id)
    await cl.Message(content=report).send()


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
            f"必需材料包：{', '.join(required['required_initial_package'])}"
        )
    ).send()
    await _send_report_actions()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    session_id = cl.user_session.get("session_id")
    if not session_id:
        await cl.Message(content="当前没有活跃会话，请先重新选择签证家族。").send()
        return

    previous_pending = cl.user_session.get("pending_requested_documents", [])
    response = await _client().post_message(session_id, message.content)
    cl.user_session.set("last_governor_decision", response["governor_decision"])
    await cl.Message(content=response["assistant_message"]).send()

    requested_documents = list(response.get("requested_documents", []))
    should_prompt_upload = (
        response["governor_decision"] == "need_more_evidence"
        and bool(requested_documents)
        and requested_documents != previous_pending
        and "waiting to be parsed" not in response["assistant_message"].lower()
    )
    if should_prompt_upload:
        await _prompt_for_required_files(requested_documents)
    else:
        cl.user_session.set("pending_requested_documents", requested_documents)

    await _send_report_actions()
