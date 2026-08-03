from enum import Enum

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_cls: type[Enum], name: str) -> SAEnum:
    """PostgreSQL ENUM型のラベルをPythonEnumの.value(小文字snake_case)に合わせる。

    デフォルトのSQLAlchemy Enumはメンバー名(大文字)をDBラベルにするため、
    server_defaultやアプリ側で使う.value文字列と食い違う。values_callableで統一する。
    """
    return SAEnum(enum_cls, name=name, values_callable=lambda obj: [e.value for e in obj])
