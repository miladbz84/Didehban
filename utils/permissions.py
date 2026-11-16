from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"


ROLE_HIERARCHY = {
    Role.OWNER: 3,
    Role.ADMIN: 2,
    Role.MODERATOR: 1,
}


def role_rank(role: str | Role) -> int:
    if isinstance(role, str):
        role = Role(role.upper())
    return ROLE_HIERARCHY.get(role, 0)


def is_higher_or_equal(role: str | Role, minimum: str | Role) -> bool:
    return role_rank(role) >= role_rank(minimum)


def is_owner(role: str | Role | None) -> bool:
    return role == Role.OWNER if role else False


def is_admin(role: str | Role | None) -> bool:
    if not role:
        return False
    return role_rank(role) >= role_rank(Role.ADMIN)


def is_moderator(role: str | Role | None) -> bool:
    if not role:
        return False
    return role_rank(role) >= role_rank(Role.MODERATOR)
