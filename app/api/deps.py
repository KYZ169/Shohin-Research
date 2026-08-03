"""FastAPI依存性: DBセッション取得、APIキー認証(技術分析レポート15章)。

「認証は初期は単一ユーザー前提のAPIキー認証(Bearerトークン、.envで管理)」を
そのまま実装する。settings.api_keyが未設定(開発環境のデフォルト)の場合は
認証をスキップする(要検討: 本番運用では必ずAPI_KEYを設定すること)。
"""

from collections.abc import Generator

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal

__all__ = ["get_db", "verify_api_key"]


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_key:
        return
    if authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
