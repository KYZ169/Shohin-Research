"""実装仕様書14.2のボタン(応募済みにする/ウォッチリスト登録/非表示)。

discord.Interaction経由でFastAPIエンドポイント(POST /lottery-entries、
POST /watchlists、PATCH /opportunities/{id})を叩く(実装仕様書14.2に明記の設計)。

【2026-08-05: 実機検証済み】app/bot/main.py(常時起動Bot)経由で実際にDiscord上から
3ボタンを押して検証した。この過程で、api_base_urlの旧デフォルト値
("http://localhost:8001"、ホスト実行のscripts/verify_live_e2e.py向け)を
botコンテナから使うとhttpx.ConnectErrorになり、例外がinteraction.response呼び出し
前に飛んでDiscord側が「応答しませんでした」になるバグを発見・修正した(docker-compose
内部のサービス名"http://api:8000"へ変更、かつHTTPエラー時も必ずinteraction.responseを
返すようtry/exceptを追加)。単体テスト(tests/unit/test_interaction_view.pyの
「モックしたInteractionオブジェクト」経由での検証)に加え、実際のDiscordボタン押下→
コールバック起動という経路そのものも確認済み。
"""

import discord
import httpx

from app.config import settings

__all__ = ["OpportunityActionView"]


def _connection_error_message(exc: httpx.HTTPError) -> str:
    return f"処理に失敗しました(API接続エラー): {type(exc).__name__}: {exc}"


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
        # 2026-08-05: app/bot/main.py(常時起動Bot、docker-compose上のbotサービス)から
        # 実際に押してみたところ、旧デフォルト値"http://localhost:8001"(ホスト実行時の
        # verify_live_e2e.py向け)ではhttpx.ConnectErrorになり、interaction.responseが
        # 一度も呼ばれずDiscord側は「応答しませんでした」になることを実機で確認した
        # (botコンテナ内のlocalhostはapiコンテナを指さないため)。docker-compose内部の
        # サービス名+コンテナ内部ポートに変更し、こちらを主呼び出し元(常時起動Bot)向けの
        # 既定値とする。ホストで直接実行するscripts/verify_live_e2e.pyは
        # api_base_url="http://localhost:8001"を明示的に渡すことで対応する。
        api_base_url: str = "http://api:8000",
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
        try:
            async with self._make_client() as client:
                response = await client.post(
                    "/lottery-entries",
                    json={"discord_id": str(interaction.user.id), "event_id": self.event_id},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            await interaction.response.send_message(_connection_error_message(exc), ephemeral=True)
            return
        message = "応募済みにしました。" if response.status_code < 300 else "処理に失敗しました。"
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="ウォッチリスト登録", style=discord.ButtonStyle.secondary)
    async def add_to_watchlist(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            async with self._make_client() as client:
                response = await client.post(
                    "/watchlists",
                    json={"discord_id": str(interaction.user.id), "event_id": self.event_id},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            await interaction.response.send_message(_connection_error_message(exc), ephemeral=True)
            return
        message = "ウォッチリストに登録しました。" if response.status_code < 300 else "処理に失敗しました。"
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="非表示", style=discord.ButtonStyle.danger)
    async def hide_opportunity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            async with self._make_client() as client:
                response = await client.patch(
                    f"/opportunities/{self.opportunity_id}",
                    json={"status": "hidden"},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            await interaction.response.send_message(_connection_error_message(exc), ephemeral=True)
            return
        if response.status_code < 300:
            await interaction.response.edit_message(content="非表示にしました。", embed=None, view=None)
        else:
            await interaction.response.send_message("処理に失敗しました。", ephemeral=True)
