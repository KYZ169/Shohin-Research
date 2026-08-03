"""単一ユーザー前提のuser解決ヘルパー(技術分析レポート15章)。

watchlists/lottery_entriesの両ルーターから使う共通処理のため、
どちらかのルーターモジュールに書かず独立させている。
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.time import JST
from app.db.models import User

__all__ = ["get_or_create_user"]


def get_or_create_user(session: Session, discord_id: str) -> User:
    user = session.query(User).filter_by(discord_id=discord_id).one_or_none()
    if user is not None:
        return user

    user = User(discord_id=discord_id, created_at=datetime.now(tz=JST))
    session.add(user)
    session.flush()
    return user
