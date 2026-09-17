"""FSM states — every step is resumable; drafts live in the database."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PlanFlow(StatesGroup):
    """IDLE → (ADD_STUDENT) → SELECT_STUDENT → SELECT_WEEK → EDIT_DAY → EDIT_SLOT →
    EDIT_ASSIGNMENTS → PREVIEW → CONFIRM → GENERATE → DONE

    A "custom week" is entered through `range_start`/`range_end` (a real date
    range), never through a separate state: the legacy single-date `custom_week`
    state and its handler were removed together with a duplicated block that
    silently shadowed the range flow.
    """

    select_student = State()
    search_student = State()
    add_student = State()
    edit_student = State()
    link_student = State()
    select_week = State()
    range_start = State()
    range_end = State()
    edit_day = State()
    edit_slot = State()
    edit_assignments = State()
    preview = State()
