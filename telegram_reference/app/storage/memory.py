from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class UserProfile:
    user_id: int
    phone: Optional[str] = None
    chosen_name: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    is_registered: bool = False


_USERS: Dict[int, UserProfile] = {}


def get_user(user_id: int) -> UserProfile:
    user = _USERS.get(user_id)
    if user is None:
        user = UserProfile(user_id=user_id)
        _USERS[user_id] = user
    return user


def reset_user(user_id: int) -> None:
    _USERS.pop(user_id, None)


def is_registered(user_id: int) -> bool:
    return get_user(user_id).is_registered


def set_phone(user_id: int, phone: str) -> None:
    get_user(user_id).phone = phone


def set_chosen_name(user_id: int, name: str) -> None:
    get_user(user_id).chosen_name = name


def set_birth_date(user_id: int, birth_date: str) -> None:
    get_user(user_id).birth_date = birth_date


def set_gender(user_id: int, gender: str) -> None:
    get_user(user_id).gender = gender


def set_registered(user_id: int, value: bool = True) -> None:
    get_user(user_id).is_registered = value
