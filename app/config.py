from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://resale_radar:resale_radar@localhost:5432/resale_radar"
    redis_url: str = "redis://localhost:6379/0"
    discord_bot_token: str = ""
    # 通知を送るDiscordチャンネルのID。app/notification/discord_bot.pyの
    # NotificationService.send_opportunity_notification()はchannel_idを
    # 呼び出し側から明示的に渡してもらう設計だが、Phase0ではチャンネルを
    # 複数出し分ける要件が無いため単一チャンネルをSettingsで保持する
    # (scripts/verify_live_e2e.py・app/scheduler/tasks.pyから参照する)。
    # 型はdiscord_bot_tokenと同じくstr(未設定=空文字列)。int型にすると
    # 未設定時の空文字列をpydanticがint変換できずValidationErrorになるため、
    # 数値への変換は利用側(送信直前)で明示的に行う。
    discord_notify_channel_id: str = ""
    # 技術分析レポート15章: 初期は単一ユーザー前提のAPIキー認証(Bearerトークン)
    api_key: str = ""


settings = Settings()
