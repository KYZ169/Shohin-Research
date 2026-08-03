"""integrationテスト用のDB接続フィクスチャ。

alembic upgrade head 済みのDB(app.config.settings.database_url)に接続し、
各テストをネストしたトランザクション内で実行してロールバックすることで、
テストが実DBに副作用を残さないようにする。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings


@pytest.fixture()
def db_session():
    engine = create_engine(settings.database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
