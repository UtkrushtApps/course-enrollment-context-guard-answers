import argparse
import json

from dotenv import load_dotenv

from app.config import Settings
from app.context_builder import run_turn
from app.llm_client import LLMClient
from app.prompts import SYSTEM_POLICY, render_user_context
from app.retrieval import ScenarioRepository, StateStore


def selfcheck() -> int:
    settings = Settings.from_env()
    repository = ScenarioRepository(settings.fixture_path)
    records = repository.load_all()
    catalog = repository.catalog_records()
    assert records
    assert catalog
    assert SYSTEM_POLICY.strip()
    preview = render_user_context(
        message="catalog check",
        student_id="selfcheck-student",
        pending=None,
        evidence=catalog[:1],
    )
    assert preview.strip()
    print(
        json.dumps(
            {
                "status": "ready",
                "fixture_records": len(records),
                "catalog_records": len(catalog),
            }
        )
    )
    return 0


def execute(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    repository = ScenarioRepository(settings.fixture_path)
    store = StateStore(settings.state_path)
    client = LLMClient(settings)
    result = run_turn(
        session_id=args.session,
        student_id=args.student,
        message=args.message,
        repository=repository,
        store=store,
        client=client,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Course advising context pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("selfcheck")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--session", required=True)
    run_parser.add_argument("--student", required=True)
    run_parser.add_argument("--message", required=True)
    return parser


def main() -> int:
    load_dotenv("/root/task/.env")
    args = build_parser().parse_args()
    if args.command == "selfcheck":
        return selfcheck()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
