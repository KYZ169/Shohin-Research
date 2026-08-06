"""常時起動Discord Bot(2026-08-05追加)。

worker/beatと同じくdocker-composeで`restart: unless-stopped`管理される、
ゲートウェイへ常時接続し続けるBotプロセスのエントリポイント。

【scripts/verify_live_e2e.pyとの役割の違い(混同防止)】
- `scripts/verify_live_e2e.py`: 収集→DB反映→商品照合→Profit Engine→Opportunity→
  通知判定→Discord送信を1回だけ実行する**使い捨ての単発検証専用**スクリプト。
  送信直後(または`--interaction-wait-seconds`指定時はその秒数後)にBotを切断する。
  役割はそのまま維持し、本モジュールとは別物。
- `app/bot/main.py`(本モジュール): worker/beatと同様に**常時起動し続け**、
  いつ来てもボタン操作(Interaction)を受け付けられる状態を維持する。実際の
  Opportunity検出・通知トリガーの自動化はまだ無く(CLAUDE.md参照、Collectorの
  定期実行タスクはDB反映まで到達していない)、現時点では「ゲートウェイに
  常時接続し続けるBotプロセスそのもの」を提供する。

【--send-test-notificationについて】
起動のたびに自動でテスト通知を送ると、`restart: unless-stopped`による意図しない
再起動(クラッシュループ等)のたびに重複送信されてしまうため、既定では送信しない。
明示的にこのフラグを指定したときのみ、on_ready後に1回だけ
`run_live_e2e_verification`(scripts/verify_live_e2e.pyが使うのと同じCeleryタスク)を
実行し、NotificationService/OpportunityActionViewがこの常駐Bot経由でも
正しく動作することを確認する(2026-08-05、常駐Bot化にあたっての動作確認用)。
"""

import argparse
import asyncio
import time

import discord

from app.config import settings
from app.notification.discord_bot import NotificationService
from app.notification.interaction_view import OpportunityActionView
from app.scheduler.tasks import VERIFICATION_DEFAULT_PRD_ID, run_live_e2e_verification


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-test-notification",
        action="store_true",
        help=(
            "on_ready後に1回だけrun_live_e2e_verificationを実行し、"
            "NotificationService/OpportunityActionViewの動作確認用にテスト通知を送信する"
        ),
    )
    return parser.parse_args()


args = _parse_args()

intents = discord.Intents.default()
client = discord.Client(intents=intents)


async def _send_test_notification() -> None:
    """scripts/verify_live_e2e.pyの_dispatch_and_wait()相当。
    常駐BotのClient(self)をそのままNotificationServiceへ渡す点だけが、
    使い捨てスクリプト側の一回限りのClientとの違い。
    """
    print("[test通知] run_live_e2e_verificationをCeleryへ投入します...")
    async_result = run_live_e2e_verification.delay(VERIFICATION_DEFAULT_PRD_ID)

    started = time.monotonic()
    result: dict | None = None
    while time.monotonic() - started < 60:
        if async_result.ready():
            result = async_result.get()
            break
        await asyncio.sleep(1)

    if result is None:
        print("[test通知][ NG ] 60秒待ってもworkerから結果が返りませんでした。")
        return
    if not result.get("ok") or not result.get("should_send"):
        print(
            "[test通知][ NG ] パイプラインが完走しなかった、またはshould_send=Falseでした: "
            f"{result.get('error', '(dedupe等によりshould_send=False)')}"
        )
        return

    channel_id = int(settings.discord_notify_channel_id)
    event_id = result["ingest"]["release_event_id"]
    opportunity_id = result["opportunity"]["opportunity_id"]
    # 2026-08-05(manual_review_tasks結線): run_live_e2e_verification()側で
    # match_status==NEEDS_REVIEWかつpendingタスク実在の両方を確認済みの場合のみ
    # manual_review_task_idが入っている(app/scheduler/tasks.py参照)。
    manual_review_task_id = result["opportunity"].get("manual_review_task_id")
    # timeout=None: 常駐Botはこの後もずっと動き続けるため、Viewを自動失効させない。
    view = OpportunityActionView(
        event_id=event_id,
        opportunity_id=opportunity_id,
        timeout=None,
        manual_review_task_id=manual_review_task_id,
    )

    service = NotificationService(client)
    message = await service.send_opportunity_notification(channel_id, result["embed"], view=view)
    print(f"[test通知][ OK ] 送信成功: message_id={message.id}")
    print(f"                 {message.jump_url}")
    print("                 時間を気にせずボタンを押してください(このBotは常時起動しています)。")


@client.event
async def on_ready() -> None:
    print(f"[ OK ] Discordゲートウェイへ接続しました(user={client.user}, id={client.user.id})")
    if args.send_test_notification:
        await _send_test_notification()


@client.event
async def on_interaction(interaction: discord.Interaction) -> None:
    custom_id = interaction.data.get("custom_id") if interaction.data else None
    component_type = interaction.data.get("component_type") if interaction.data else None
    print(
        f"[interaction受信] user={interaction.user}({interaction.user.id}) "
        f"custom_id={custom_id!r} component_type={component_type!r}"
    )


if __name__ == "__main__":
    # client.run()はclient.start()と異なりデフォルトのlogging設定
    # (discord.gateway等の内部ログをstdoutへ出力)とSIGINT/SIGTERMでの
    # 正常終了処理を自前で行ってくれるため、常時起動プロセスにはこちらが適する。
    client.run(settings.discord_bot_token)
