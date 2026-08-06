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

【2026-08-05: manual_review_tasks結線(「照合を確定する」/「別商品として分離」)】
CLAUDE.md 6節で実装済みのマージ処理(POST /manual-review-tasks/{id}/resolve)への
Discordボタン入口を追加した。既存3ボタンとは別枠(row=1)にし、`manual_review_task_id`が
渡された場合のみ動的に追加する(discord.ui.buttonデコレータは全インスタンス共通のため、
「NEEDS_REVIEWのOpportunityにのみ出したい」条件付き表示にはadd_item()による動的追加を
使う)。

表示条件の設計判断(ユーザー確認済み): manual_review_tasksは「仕入れ側
(ingest_normalized_item)のProduct照合」がNEEDS_REVIEWになった時にだけ作成され、
その候補Product IDはOpportunity.product_idと一致する。一方Opportunity.match_status
自体は「相場側(MarketObservation)の独立した照合結果」からセットされるため、
仕入れ側と相場側の照合結果が食い違うケースがあり得る(例: 仕入れ側はNEEDS_REVIEWで
候補Product+manual_review_taskが作られたが、相場側は別のProductにAUTO_MATCHし、
Opportunity.match_statusはNEEDS_REVIEWにならない)。このモジュールはボタンの
表示可否そのものには関与せず(呼び出し側が`manual_review_task_id`の有無で判断する)、
呼び出し側(app/bot/main.py/scripts/verify_live_e2e.py)は「match_status==NEEDS_REVIEW
かつ該当pendingタスクが実在する」の両方を満たす場合のみ`manual_review_task_id`を渡す
方針とした。片方しか満たさない場合(食い違いケース)はボタンを出さない
(CLAUDE.md 3節に既知の制約として記録)。

誤操作防止のため、いずれのボタンも直接は実行せず、まずephemeralな確認メッセージ
(実行/キャンセルの再クリック方式、`_ManualReviewConfirmView`)を挟む。
"""

import discord
import httpx

from app.config import settings

__all__ = ["OpportunityActionView"]


def _connection_error_message(exc: httpx.HTTPError) -> str:
    return f"処理に失敗しました(API接続エラー): {type(exc).__name__}: {exc}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}


class _ManualReviewConfirmView(discord.ui.View):
    """「照合を確定する」/「別商品として分離」の誤操作防止用、再クリック確認View。

    ephemeralな確認メッセージに添付し、「実行する」を押した時点で初めて
    POST /manual-review-tasks/{id}/resolveを叩く。timeout=60で放置時は自動失効させる
    (マージ/分離は重い操作のため、応答不能なボタンを残し続けないようにする)。
    """

    def __init__(
        self,
        manual_review_task_id: str,
        resolution: str,
        api_base_url: str,
        http_client: httpx.AsyncClient | None,
    ) -> None:
        super().__init__(timeout=60)
        self.manual_review_task_id = manual_review_task_id
        self.resolution = resolution
        self.api_base_url = api_base_url
        self._injected_client = http_client

    def _make_client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        return httpx.AsyncClient(base_url=self.api_base_url)

    @discord.ui.button(label="実行する", style=discord.ButtonStyle.danger)
    async def execute(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            async with self._make_client() as client:
                response = await client.post(
                    f"/manual-review-tasks/{self.manual_review_task_id}/resolve",
                    json={"resolution": self.resolution, "resolved_by": f"discord:{interaction.user}"},
                    headers=_headers(),
                )
        except httpx.HTTPError as exc:
            await interaction.response.edit_message(content=_connection_error_message(exc), view=None)
            return

        if response.status_code < 300:
            action_done = "統合(確定)" if self.resolution == "confirm" else "分離(別商品として維持)"
            await interaction.response.edit_message(content=f"照合結果を{action_done}しました。", view=None)
        else:
            detail = ""
            try:
                detail = str(response.json().get("detail", ""))
            except ValueError:
                pass
            await interaction.response.edit_message(
                content=f"処理に失敗しました(status={response.status_code}) {detail}".rstrip(), view=None
            )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="キャンセルしました(何も変更していません)。", view=None)


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
        # 2026-08-05: manual_review_tasks結線。呼び出し側が「match_status==NEEDS_REVIEW
        # かつ該当pendingタスクが実在する」の両方を確認した上でのみ渡す想定
        # (モジュールdocstring「表示条件の設計判断」参照)。Noneなら2ボタンとも追加しない。
        manual_review_task_id: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.event_id = event_id
        self.opportunity_id = opportunity_id
        self.api_base_url = api_base_url
        self._injected_client = http_client
        self.manual_review_task_id = manual_review_task_id

        if manual_review_task_id is not None:
            # @discord.ui.buttonデコレータは全インスタンス共通のボタンにしか使えないため、
            # 「manual_review_task_idがある時だけ」の条件付き追加はadd_item()で行う。
            # row=1にして既存3ボタン(row=0扱い)とは別枠にする。
            confirm_button: discord.ui.Button = discord.ui.Button(
                label="照合を確定する", style=discord.ButtonStyle.success, row=1
            )
            confirm_button.callback = self._prompt_confirm_match
            self.add_item(confirm_button)

            separate_button: discord.ui.Button = discord.ui.Button(
                label="別商品として分離", style=discord.ButtonStyle.secondary, row=1
            )
            separate_button.callback = self._prompt_separate_product
            self.add_item(separate_button)

    async def _prompt_confirm_match(self, interaction: discord.Interaction) -> None:
        await self._send_manual_review_confirmation_prompt(interaction, resolution="confirm")

    async def _prompt_separate_product(self, interaction: discord.Interaction) -> None:
        await self._send_manual_review_confirmation_prompt(interaction, resolution="reject")

    async def _send_manual_review_confirmation_prompt(
        self, interaction: discord.Interaction, resolution: str
    ) -> None:
        action_label = "照合を確定する(候補商品を既存商品へ統合します)" if resolution == "confirm" else "別商品として分離する(統合しません)"
        warning = "統合は元に戻せません。" if resolution == "confirm" else ""
        confirm_view = _ManualReviewConfirmView(
            manual_review_task_id=self.manual_review_task_id,
            resolution=resolution,
            api_base_url=self.api_base_url,
            http_client=self._injected_client,
        )
        await interaction.response.send_message(
            f"本当によろしいですか?\n操作: {action_label}\n{warning}",
            view=confirm_view,
            ephemeral=True,
        )

    def _make_client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        return httpx.AsyncClient(base_url=self.api_base_url)

    def _headers(self) -> dict:
        return _headers()

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
