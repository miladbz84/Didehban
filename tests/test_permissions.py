from __future__ import annotations

from config import _get_owner_id
from utils.permissions import Role, is_admin, is_higher_or_equal, is_owner, role_rank


def test_role_hierarchy_and_admin_checks():
    assert role_rank(Role.OWNER) > role_rank(Role.ADMIN)
    assert is_higher_or_equal(Role.ADMIN, Role.MODERATOR)
    assert is_admin(Role.ADMIN)
    assert not is_admin(Role.MODERATOR)
    assert is_owner(Role.OWNER)


def test_owner_id_must_be_a_valid_positive_integer():
    assert _get_owner_id("501") == 501
    assert _get_owner_id(" 501 ") == 501
    assert _get_owner_id("0") is None
    assert _get_owner_id("-501") is None
    assert _get_owner_id("invalid") is None
    assert _get_owner_id("9" * 5000) is None
