"""discord.py Bot連携(実装仕様書4章 Prompt 7「discord.pyを使用」)。

【重要: このモジュールは未検証】
このサンドボックス環境にはDISCORD_BOT_TOKENが設定されておらず、Discord APIへの
ネットワーク到達性も無い(1kuji.com等と同様、外部ホストへのアクセスが組織ポリシーで
制限されている可能性が高い)。そのため本モジュールは実際にBotを起動・接続して
メッセージを送信する動作を一度も確認できていない。discord.pyの標準的な使い方に
従って実装しているが、本番のConoHa VPS環境で実トークンを使った接続確認が必須。

ボタン/Interaction処理(応募済みにする/ウォッチリスト登録/非表示、実装仕様書14.2)は
CLAUDE.md記載のとおりPoC-5が未実施のため、本モジュールのスコープに含めない。
"""

import discord

__all__ = ["NotificationService"]


class NotificationService:
    """Embed辞書(app.notification.embed_builder.build_embedの戻り値)を
    Discordチャンネルへ送信する薄いラッパー。

    ビジネスロジック(Embed組み立て・dedupe判定)はすべてembed_builder.py/dedupe.pyに
    切り出し済みで、このクラスはdiscord.pyのClientを使った送信のみを担当する。
    """

    def __init__(self, client: discord.Client) -> None:
        self._client = client

    async def send_opportunity_notification(self, channel_id: int, embed_dict: dict) -> discord.Message:
        channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(channel_id)
        embed = discord.Embed.from_dict(_strip_none_values(embed_dict))
        return await channel.send(embed=embed)


def _strip_none_values(embed_dict: dict) -> dict:
    """build_embed()はthumbnail等が無い場合Noneを入れることがあるが、
    discord.Embed.from_dict()はNone値のキーを許容しないため取り除く。"""
    return {key: value for key, value in embed_dict.items() if value is not None}
