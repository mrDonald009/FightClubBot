"""Агрегатор сценарных тестов добавления спортсмена.

Сами тест-кейсы разнесены по модулям в `tests/add_athlete_flow/`
для упрощения поддержки и навигации.
"""

from tests.add_athlete_flow.basic_steps import *  # noqa: F401,F403
from tests.add_athlete_flow.calendar_and_shift import *  # noqa: F401,F403
from tests.add_athlete_flow.finalize_paths import *  # noqa: F401,F403
from tests.add_athlete_flow.start_access import *  # noqa: F401,F403
from tests.add_athlete_flow.validation_steps import *  # noqa: F401,F403
