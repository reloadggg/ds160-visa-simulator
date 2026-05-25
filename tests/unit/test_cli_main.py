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


def test_cli_main_eval_graph_fixture_outputs_result(capsys) -> None:
    exit_code = main(
        [
            "eval-graph-fixture",
            "--fixture",
            "fixtures/graph_replay/school_mismatch_where.json",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"fixture_id": "school-mismatch-where"' in captured
    assert '"passed": true' in captured


def test_cli_main_eval_graph_corpus_matches_expected_failures(capsys) -> None:
    exit_code = main(
        [
            "eval-graph-corpus",
            "--fixture-dir",
            "fixtures/graph_replay",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert '"fixture_count": 10' in captured
    assert '"fixture_id": "no-material-chat-starts"' in captured
    assert '"fixture_id": "repeated-template-failure"' in captured
    assert '"expected_passed": false' in captured
    assert '"matched_expectation": true' in captured
