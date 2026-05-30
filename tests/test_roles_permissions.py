"""Проверки модели ролей coach / admin / athlete."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Admin, Athlete, Base, Coach, SportType
from database.db_utils.users import create_user, get_user_by_telegram_id, get_user_role
from database.db_utils.role_policy import assert_can_assign_role, validate_staff_roles_consistency
from services.permissions import (
    can_edit_athlete,
    has_dual_staff_role,
    is_admin,
    is_coach,
    is_staff,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    st = SportType(name="MMA", display_name="MMA")
    sess.add(st)
    sess.commit()
    yield sess
    sess.close()


def test_is_staff_coach_and_admin(session):
    coach = Coach(telegram_id=1, first_name="C", sport_type="MMA", sport_type_id=1)
    admin = Admin(telegram_id=2, first_name="A")
    assert is_staff(coach)
    assert is_staff(admin)
    assert not is_staff(Athlete(telegram_id=3, full_name="X"))


def test_resolve_admin_before_coach(session):
    tid = 100
    session.add(
        Coach(telegram_id=tid, first_name="Coach", sport_type="MMA", sport_type_id=1)
    )
    session.add(Admin(telegram_id=tid, first_name="Admin"))
    session.commit()
    user = get_user_by_telegram_id(session, tid)
    assert is_admin(user)
    assert validate_staff_roles_consistency(session)


def test_assert_blocks_dual_staff(session):
    tid = 200
    session.add(
        Coach(telegram_id=tid, first_name="C", sport_type="MMA", sport_type_id=1)
    )
    session.commit()
    with pytest.raises(ValueError, match="совмещать"):
        assert_can_assign_role(session, tid, "admin")


def test_can_edit_athlete_coach_own_only(session):
    coach = Coach(id=1, telegram_id=10, first_name="C", sport_type="MMA", sport_type_id=1)
    admin = Admin(id=2, telegram_id=20, first_name="A")
    own = Athlete(id=1, full_name="Own", created_by=1)
    other = Athlete(id=2, full_name="Other", created_by=99)
    assert can_edit_athlete(coach, own)
    assert not can_edit_athlete(coach, other)
    assert can_edit_athlete(admin, other)


def test_create_user_rejects_assistant(session):
    with pytest.raises(ValueError, match="assistant"):
        create_user(session, 1, "u", "f", role="assistant")


def test_has_dual_staff(session):
    tid = 300
    session.add(
        Coach(telegram_id=tid, first_name="C", sport_type="MMA", sport_type_id=1)
    )
    session.add(Admin(telegram_id=tid, first_name="A"))
    session.commit()
    assert has_dual_staff_role(session, tid)


def test_get_user_role_three_roles(session):
    assert get_user_role(Coach(telegram_id=1, sport_type="MMA")) == "coach"
    assert get_user_role(Admin(telegram_id=1)) == "admin"
    assert get_user_role(Athlete(full_name="X")) == "athlete"
