from app.cli.main import main


class _FakeRunner:
    def inspect_turn(self, session_id: str, turn_id: str) -> dict:
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "role": "assistant",
        }

    def replay_session(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "turn_count": 2,
            "turns": [],
        }


class _FakeSession:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


def test_cli_main_inspect_turn_outputs_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.main.ReplayRunner", lambda db: _FakeRunner())
    monkeypatch.setattr("app.cli.main._build_session_factory", lambda url: _FakeSession)

    exit_code = main(
        [
            "--db-url",
            "sqlite:///fake.db",
            "inspect-turn",
            "--session-id",
            "sess-1",
            "--turn-id",
            "turn-2",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"session_id": "sess-1"' in captured
    assert '"turn_id": "turn-2"' in captured


def test_cli_main_replay_session_outputs_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.main.ReplayRunner", lambda db: _FakeRunner())
    monkeypatch.setattr("app.cli.main._build_session_factory", lambda url: _FakeSession)

    exit_code = main(
        [
            "replay-session",
            "--session-id",
            "sess-2",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"session_id": "sess-2"' in captured
    assert '"turn_count": 2' in captured
