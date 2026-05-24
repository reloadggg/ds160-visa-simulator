from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import DATABASE_URL, SQLITE_CONNECT_ARGS
from app.evals.graph_replay_eval import GraphReplayEvaluator, GraphReplayFixture
from app.evals.replay_runner import ReplayRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ds160-agent-cli")
    parser.add_argument(
        "--db-url",
        default=DATABASE_URL,
        help="数据库连接串，默认使用应用主库。",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-turn")
    inspect_parser.add_argument("--session-id", required=True)
    inspect_parser.add_argument("--turn-id", required=True)

    replay_parser = subparsers.add_parser("replay-session")
    replay_parser.add_argument("--session-id", required=True)

    graph_fixture_parser = subparsers.add_parser("eval-graph-fixture")
    graph_fixture_parser.add_argument("--fixture", required=True)
    graph_fixture_parser.add_argument("--max-repeated-template", type=int, default=2)

    graph_corpus_parser = subparsers.add_parser("eval-graph-corpus")
    graph_corpus_parser.add_argument("--fixture-dir", default="fixtures/graph_replay")
    graph_corpus_parser.add_argument("--max-repeated-template", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "eval-graph-fixture":
        result = GraphReplayEvaluator().evaluate_fixture_file(
            args.fixture,
            max_repeated_template=args.max_repeated_template,
        )
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.passed else 1
    if args.command == "eval-graph-corpus":
        fixture_dir = Path(args.fixture_dir)
        evaluator = GraphReplayEvaluator()
        results = []
        for path in sorted(fixture_dir.glob("*.json")):
            fixture = GraphReplayFixture.from_file(path)
            result = evaluator.evaluate(
                fixture_id=fixture.fixture_id,
                state=fixture.state,
                events=fixture.events,
                max_repeated_template=args.max_repeated_template,
            )
            expected_passed = bool(fixture.expected.get("should_pass", True))
            expected_failed_checks = set(fixture.expected.get("failed_checks") or [])
            actual_failed_checks = {check.name for check in result.failed_checks}
            matched_expectation = result.passed == expected_passed
            if expected_failed_checks:
                matched_expectation = matched_expectation and expected_failed_checks <= actual_failed_checks
            results.append(
                {
                    **result.model_dump(),
                    "fixture_path": str(path),
                    "expected_passed": expected_passed,
                    "matched_expectation": matched_expectation,
                }
            )
        payload = {
            "fixture_dir": str(fixture_dir),
            "fixture_count": len(results),
            "passed": all(result["matched_expectation"] for result in results),
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 1

    session_factory = _build_session_factory(args.db_url)

    with session_factory() as db:
        runner = ReplayRunner(db)
        if args.command == "inspect-turn":
            payload = runner.inspect_turn(args.session_id, args.turn_id)
        else:
            payload = runner.replay_session(args.session_id)

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _build_session_factory(database_url: str) -> sessionmaker[Session]:
    connect_args = SQLITE_CONNECT_ARGS if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


if __name__ == "__main__":
    raise SystemExit(main())
