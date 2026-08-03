"""実装仕様書14.2のボタン(応募済みにする/ウォッチリスト登録/非表示)。

discord.Interaction経由でFastAPIエンドポイント(POST /lottery-entries、
POST /watchlists、PATCH /opportunities/{id})を叩く(実装仕様書14.2に明記の設計)。

【重要: 未検証】app/notification/discord_bot.py(タスク11)と同様、このサンドボックス
環境にはDiscord Bot Interactionを実際に受信する手段が無い(トークン/ネットワーク
到達性が無い、CLAUDE.md記載のPoC-5は未実施)。本モジュールのボタンコールバックは
「モックしたInteractionオブジェクト」を渡して直接呼び出すことで、正しいAPI
エンドポイント・ペイロードで呼ばれるかを検証している(tests/unit/
test_interaction_view.py参照)。実際のDiscordボタン押下→コールバック起動という
経路そのものは未検証。
"""

import discord
import httpx

from app.config import settings

__all__ = ["OpportunityActionView"]


class OpportunityActionView(discord.ui.View):
    """1件のOpportunity通知に添付するボタン群。

    http_clientを外部から注入できるようにしているのは、本番ではapi_base_urlに
    対する実際のHTTP接続を、テストではhttpx.ASGITransport経由でFastAPIアプリに
    プロセス内接続するためのhttpx.AsyncClientを、それぞれ差し替えられるようにする
    ため(discord接続以外は実際にコード経路を通して検証したいという方針)。
    """

    def __init__(
        self,
        event_id: str,
        opportunity_id: str,
        api_base_url: str = "http://localhost:8000",
        http_client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.event_id = event_id
        self.opportunity_id = opportunity_id
        self.api_base_url = api_base_url
        self._injected_client = http_client

    def _make_client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        return httpx.AsyncClient(base_url=self.api_base_url)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}

    @discord.ui.button(label="応募済みにする", style=discord.ButtonStyle.primary)
    async def mark_applied(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._make_client() as client:
            response = await client.post(
                "/lottery-entries",
                json={"discord_id": str(interaction.user.id), "event_id": self.event_id},
                headers=self._headers(),
            )
        message = "応募済みにしました。" if response.status_code < 300 else "処理に失敗しました。"
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="ウォッチリスト登録", style=discord.ButtonStyle.secondary)
    async def add_to_watchlist(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._make_client() as client:
            response = await client.post(
                "/watchlists",
                json={"discord_id": str(interaction.user.id), "event_id": self.event_id},
                headers=self._headers(),
            )
        message = "ウォッチリストに登録しました。" if response.status_code < 300 else "処理に失敗しました。"
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="非表示", style=discord.ButtonStyle.danger)
    async def hide_opportunity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._make_client() as client:
            response = await client.patch(
                f"/opportunities/{self.opportunity_id}",
                json={"status": "hidden"},
                headers=self._headers(),
            )
        if response.status_code < 300:
            await interaction.response.edit_message(content="非表示にしました。", embed=None, view=None)
        else:
            await interaction.response.send_message("処理に失敗しました。", ephemeral=True)
