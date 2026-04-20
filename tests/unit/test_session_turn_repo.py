from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository


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
