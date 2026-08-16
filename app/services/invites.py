"""Invite redemption — role integrity is enforced *before* anything is written.

Security model (mission "Final Security Hardening"):

* an invite link may only ever bind an **unclaimed student row** to a Telegram
  account that has no other identity in the system;
* opening an invite can never create, change or downgrade a role;
* an admin or advisor who taps a student link keeps their account untouched;
* a student cannot hijack another student's link;
* every outcome is audited.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from ..db.models import Role, User


class InviteOutcome(str, enum.Enum):
    LINKED = "linked"                    # success: account bound
    ALREADY_SELF = "already_self"        # same person re-opening their link
    ROLE_CONFLICT = "role_conflict"      # admin/advisor opened a student link
    CROSS_STUDENT = "cross_student"      # another student tried to use it
    ALREADY_LINKED = "already_linked"    # the student is bound to someone else
    EXPIRED = "expired"
    INVALID = "invalid"                  # unknown, malformed, revoked or consumed

    @property
    def ok(self) -> bool:
        return self in (InviteOutcome.LINKED, InviteOutcome.ALREADY_SELF)

    @property
    def audit_action(self) -> str:
        return {
            InviteOutcome.LINKED: "invite.accepted",
            InviteOutcome.ALREADY_SELF: "invite.already_used",
            InviteOutcome.ROLE_CONFLICT: "invite.role_conflict",
            InviteOutcome.CROSS_STUDENT: "invite.rejected",
            InviteOutcome.ALREADY_LINKED: "invite.already_linked",
            InviteOutcome.EXPIRED: "invite.expired",
            InviteOutcome.INVALID: "invite.rejected",
        }[self]


@dataclass(frozen=True)
class InviteResult:
    outcome: InviteOutcome
    student: User | None = None

    @property
    def ok(self) -> bool:
        return self.outcome.ok


PROTECTED_ROLES = (Role.ADMIN, Role.ADVISOR)


def blocks_invite(actor: User | None, is_admin_env: bool = False) -> bool:
    """True when the current account must never be touched by an invite link."""
    if is_admin_env:
        return True
    if actor is None:
        return False
    return actor.role in PROTECTED_ROLES
