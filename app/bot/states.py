"""FSM states — every step is resumable; drafts live in the database."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PlanFlow(StatesGroup):
    """IDLE → (ADD_STUDENT) → SELECT_STUDENT → SELECT_WEEK → EDIT_DAY → EDIT_SLOT →
    EDIT_ASSIGNMENTS → PREVIEW → CONFIRM → GENERATE → DONE"""

    select_student = State()
    search_student = State()
    add_student = State()
    edit_student = State()
    link_student = State()
    select_week = State()
    custom_week = State()
    edit_day = State()
    edit_slot = State()
    edit_assignments = State()
    preview = State()
