from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import (
    DuplicateClientMessageIdError,
    SessionTurnRepository,
)


def test_session_repository_create_initializes_interviewer_memory_fields(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turns.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )

    assert record.interviewer_state_json == {}
    assert record.current_focus_json == {}


def test_session_turn_repository_appends_and_lists_turns_in_order(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-order.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        repo = SessionTurnRepository(db)

        first_turn = repo.append_user_turn(
            session_id=session_record.session_id,
            content="我计划今年八月去美国读书。",
            source="chat_completions",
            metadata_json={"message_index": 0},
        )
        second_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="请先说明你的资金来源，并补充对应证明。",
            source="chat_completions",
            metadata_json={"message_index": 1},
        )
        turns = repo.list_session_turns(session_record.session_id)

    assert [turn.turn_id for turn in turns] == [first_turn.turn_id, second_turn.turn_id]
    assert [turn.turn_index for turn in turns] == [1, 2]
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert [turn.content for turn in turns] == [
        "我计划今年八月去美国读书。",
        "请先说明你的资金来源，并补充对应证明。",
    ]
    assert turns[0].metadata_json == {"message_index": 0}
    assert turns[1].metadata_json == {"message_index": 1}


def test_session_turn_repository_finds_client_message_id_and_next_assistant(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-client-id.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        repo = SessionTurnRepository(db)

        user_turn = repo.append_user_turn(
            session_id=session_record.session_id,
            content="我的父母会支付学费。",
            source="user_message",
            metadata_json={"client_message_id": "client-1"},
        )
        assistant_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="第一年费用大约是多少？",
            source="interviewer_runtime_service",
        )

        matched_user = repo.find_user_turn_by_client_message_id(
            session_id=session_record.session_id,
            client_message_id="client-1",
        )
        matched_assistant = repo.next_assistant_turn_after(
            session_id=session_record.session_id,
            user_turn=user_turn,
        )

    assert matched_user is not None
    assert matched_user.turn_id == user_turn.turn_id
    assert matched_user.client_message_id == "client-1"
    assert matched_assistant is not None
    assert matched_assistant.turn_id == assistant_turn.turn_id


def test_session_turn_repository_rejects_duplicate_client_message_id(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-client-id-unique.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        repo = SessionTurnRepository(db)

        repo.append_user_turn(
            session_id=session_record.session_id,
            content="第一条资金说明。",
            source="user_message",
            metadata_json={"client_message_id": "client-unique-1"},
        )

        try:
            repo.append_user_turn(
                session_id=session_record.session_id,
                content="重复发送的资金说明。",
                source="user_message",
                metadata_json={"client_message_id": "client-unique-1"},
            )
        except DuplicateClientMessageIdError as exc:
            assert exc.client_message_id == "client-unique-1"
        else:
            raise AssertionError("expected DuplicateClientMessageIdError")

        turns = repo.list_session_turns(session_record.session_id)

    assert len(turns) == 1
    assert turns[0].content == "第一条资金说明。"


def test_session_turn_repository_keeps_append_order_with_same_time_tick(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-same-tick.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    uuid_hexes = iter(
        [
            "ffffffff000000000000000000000000",
            "00000000000000000000000000000000",
        ]
    )
    monkeypatch.setattr(
        "app.repositories.session_turn_repo.time_ns",
        lambda: 123456789,
    )
    monkeypatch.setattr(
        "app.repositories.session_turn_repo.uuid4",
        lambda: SimpleNamespace(hex=next(uuid_hexes)),
    )

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        repo = SessionTurnRepository(db)

        first_turn = repo.append_user_turn(
            session_id=session_record.session_id,
            content="第一条说明",
            source="chat_completions",
        )
        second_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="第二条追问",
            source="chat_completions",
        )
        turns = repo.list_session_turns(session_record.session_id)

    assert first_turn.turn_id > second_turn.turn_id
    assert [turn.turn_id for turn in turns] == [first_turn.turn_id, second_turn.turn_id]
    assert [turn.turn_index for turn in turns] == [1, 2]


def test_session_turn_repository_retries_when_turn_index_conflicts(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-conflict.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        repo = SessionTurnRepository(db)

        repo.append_user_turn(
            session_id=session_record.session_id,
            content="第一条说明",
            source="chat_completions",
        )

        next_indexes = iter([1, 2])
        monkeypatch.setattr(repo, "_next_turn_index", lambda _session_id: next(next_indexes))

        second_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="第二条追问",
            source="chat_completions",
        )
        turns = repo.list_session_turns(session_record.session_id)

    assert second_turn.turn_index == 2
    assert [turn.turn_index for turn in turns] == [1, 2]


def test_session_turn_repository_retries_without_committing_outer_transaction(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-nested-conflict.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        session_id = session_record.session_id
        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_id,
            content="第一条说明",
            source="chat_completions",
        )

        session_record.phase_state = "interview"
        db.add(session_record)
        next_indexes = iter([1, 2])
        monkeypatch.setattr(repo, "_next_turn_index", lambda _session_id: next(next_indexes))

        second_turn = repo.append_assistant_turn(
            session_id=session_id,
            content="第二条追问",
            source="chat_completions",
            commit=False,
        )
        second_turn_index = second_turn.turn_index
        db.commit()

    with Session(engine) as db:
        saved_session = SessionRepository(db).get(session_id)
        turns = SessionTurnRepository(db).list_session_turns(session_id)

    assert saved_session is not None
    assert saved_session.phase_state == "interview"
    assert second_turn_index == 2
    assert [turn.turn_index for turn in turns] == [1, 2]


def test_session_turn_repository_without_commit_raises_after_stable_conflicts(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-turn-nested-conflict-fail.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "pending_documents"},
        )
        session_id = session_record.session_id
        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_id,
            content="第一条说明",
            source="chat_completions",
        )
        repo.append_assistant_turn(
            session_id=session_id,
            content="第二条追问",
            source="chat_completions",
        )
        repo.append_user_turn(
            session_id=session_id,
            content="第三条说明",
            source="chat_completions",
        )

        monkeypatch.setattr(repo, "_next_turn_index", lambda _session_id: 1)

        try:
            repo.append_assistant_turn(
                session_id=session_id,
                content="第二条追问",
                source="chat_completions",
                commit=False,
            )
        except RuntimeError as exc:
            assert "stable turn_index" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")

        db.rollback()
        turns = repo.list_session_turns(session_id)

    assert [turn.turn_index for turn in turns] == [1, 2, 3]
