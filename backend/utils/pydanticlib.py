from __future__ import annotations

from enum import StrEnum, auto
from string import ascii_letters, ascii_lowercase, ascii_uppercase, digits, punctuation
from typing import Annotated
from unicodedata import normalize

from pydantic import BeforeValidator

from backend.errors import ClientError

USERNAME_MIN_LEN = 4
USERNAME_MAX_LEN = 48
PW_MIN_LEN = 8
PW_MAX_LEN = 1024
PW_MIN_CHAR_TYPE_NUM = 2
URLSAFE_CHARS = ascii_letters + digits + "-_"


class CharType(StrEnum):
    LOWER = auto()
    UPPER = auto()
    DIGIT = auto()
    PUNCT = auto()

    @classmethod
    def get_char_type(cls, s: str) -> CharType | None:
        c_type: dict[CharType, str] = {
            cls.LOWER: ascii_lowercase,
            cls.UPPER: ascii_uppercase,
            cls.DIGIT: digits,
            cls.PUNCT: punctuation,
        }
        for key, value in c_type.items():
            if normalize("NFC", s) in value:
                return key
        return None

    @classmethod
    def get_str_char_types(cls, target_str: str) -> set[CharType | None]:
        return {cls.get_char_type(target_char) for target_char in target_str}


class UserNameValidator(StrEnum):
    SAFE = auto()
    EMPTY = auto()
    TOO_SHORT = auto()
    TOO_LONG = auto()
    FORBIDDEN_CHAR = auto()

    @classmethod
    def is_valid(cls, s: str) -> UserNameValidator:
        if not s:
            return cls.EMPTY
        if len(s) < USERNAME_MIN_LEN:
            return cls.TOO_SHORT
        if len(s) > USERNAME_MAX_LEN:
            return cls.TOO_LONG
        if not all(c in URLSAFE_CHARS for c in s):
            return cls.FORBIDDEN_CHAR
        return cls.SAFE

    @classmethod
    def validate_username(cls, value: str) -> str:
        match cls.is_valid(value):
            case cls.EMPTY:
                ClientError.USERNAME_REQUIRED.raise_()
            case cls.TOO_SHORT:
                ClientError.USERNAME_TOO_SHORT.format_msg(min_len=USERNAME_MIN_LEN, max_len=USERNAME_MAX_LEN).raise_()
            case cls.TOO_LONG:
                ClientError.USERNAME_TOO_LONG.format_msg(min_len=USERNAME_MIN_LEN, max_len=USERNAME_MAX_LEN).raise_()
            case cls.FORBIDDEN_CHAR:
                ClientError.USERNAME_CONTAINS_INVALID_CHAR.raise_()
        return normalize("NFC", value).strip()


class PasswordValidator(StrEnum):
    SAFE = auto()
    EMPTY = auto()
    TOO_SHORT = auto()
    TOO_LONG = auto()
    NEED_MORE_CHAR_TYPE = auto()
    FORBIDDEN_CHAR = auto()

    @classmethod
    def is_valid(cls, s: str) -> PasswordValidator:
        if not s:
            return cls.EMPTY
        if len(s) < PW_MIN_LEN:
            return cls.TOO_SHORT
        if len(s) > PW_MAX_LEN:
            return cls.TOO_LONG

        s_char_type = CharType.get_str_char_types(s)
        if len(s_char_type) < PW_MIN_CHAR_TYPE_NUM:
            return cls.NEED_MORE_CHAR_TYPE
        if not all(s_char_type):
            return cls.FORBIDDEN_CHAR
        return cls.SAFE

    @classmethod
    def validate_password(cls, value: str) -> str:
        match cls.is_valid(value):
            case cls.EMPTY:
                ClientError.PASSWORD_REQUIRED.raise_()
            case cls.TOO_SHORT:
                ClientError.PASSWORD_TOO_SHORT.format_msg(min_len=PW_MIN_LEN, max_len=PW_MAX_LEN).raise_()
            case cls.TOO_LONG:
                ClientError.PASSWORD_TOO_LONG.format_msg(max_len=PW_MAX_LEN).raise_()
            case cls.NEED_MORE_CHAR_TYPE:
                ClientError.PASSWORD_NEED_MORE_CHAR_TYPE.format_msg(min_char_type_num=PW_MIN_CHAR_TYPE_NUM).raise_()
            case cls.FORBIDDEN_CHAR:
                ClientError.PASSWORD_CONTAINS_INVALID_CHAR.raise_()
        return normalize("NFC", value).strip()


UsernameField = Annotated[str, BeforeValidator(UserNameValidator.validate_username)]
PasswordField = Annotated[str, BeforeValidator(PasswordValidator.validate_password)]
