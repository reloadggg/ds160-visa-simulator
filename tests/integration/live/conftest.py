from collections.abc import Generator

from fastapi.testclient import TestClient
import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.model_factory import AgentModelFactory
from app.core.settings import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

LIVE_LLM_MARKER = "live_llm: 标记需要真实 LLM 配置与网络调用的集成测试"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", LIVE_LLM_MARKER)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del config

    if settings.run_live_llm_tests:
        return

    skip_live_llm = pytest.mark.skip(
        reason='未启用 live_llm 测试；设置 RUN_LIVE_LLM_TESTS=1 后重试',
    )
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip_live_llm)


@pytest.fixture()
def live_db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'live-llm.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def live_api_client(live_db_session_factory) -> Generator[TestClient, None, None]:
    previous_override = app.dependency_overrides.get(get_db)

    def override_get_db() -> Generator[Session, None, None]:
        db = live_db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override


@pytest.fixture()
def live_expected_runtime_model():
    factory = AgentModelFactory()

    def _resolve(module_key: str, stage_key: str) -> str | None:
        return factory.registry.get(module_key, stage_key).get("model")

    return _resolve


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


@pytest.fixture()
def live_build_pdf_bytes():
    return build_pdf_bytes
