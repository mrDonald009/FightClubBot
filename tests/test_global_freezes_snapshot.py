"""Тесты на закоммиченный снимок global_freezes (данные приходят с git pull)."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, GlobalFreeze
from tests.fixtures.global_freeze_snapshot_loader import (
    DEFAULT_SNAPSHOT_PATH,
    load_global_freezes_snapshot,
)

pytestmark = pytest.mark.db


@pytest.fixture
def memory_session_empty_gf():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def test_global_freezes_snapshot_file_exists():
    assert DEFAULT_SNAPSHOT_PATH.is_file(), "Снимок должен быть в репозитории для тестов после pull"


def test_load_global_freezes_snapshot_matches_dev_scenario(memory_session_empty_gf):
    s = memory_session_empty_gf
    load_global_freezes_snapshot(s)
    rows = s.query(GlobalFreeze).order_by(GlobalFreeze.id).all()
    assert len(rows) == 3
    assert sum(1 for g in rows if g.is_active) == 1
    active = next(g for g in rows if g.is_active)
    assert active.id == 3
    assert active.title == "Майские"
    assert active.start_date.day == 1 and active.start_date.month == 5


def test_snapshot_path_can_be_overridden(memory_session_empty_gf, tmp_path: Path):
    alt = tmp_path / "mini.json"
    alt.write_text(
        '{"version": 1, "table": "global_freezes", "rows": ['
        '{"id": 10, "title": "X", "start_date": "2026-01-01T00:00:00", '
        '"end_date": "2026-01-02T23:59:59", "is_active": true, '
        '"created_by": 1, "created_at": "2026-01-01T12:00:00"}]}',
        encoding="utf-8",
    )
    load_global_freezes_snapshot(memory_session_empty_gf, path=alt)
    assert memory_session_empty_gf.query(GlobalFreeze).count() == 1
