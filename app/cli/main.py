from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import DATABASE_URL, SQLITE_CONNECT_ARGS
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
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
