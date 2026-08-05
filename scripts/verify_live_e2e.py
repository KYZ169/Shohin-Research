#!/usr/bin/env python3
"""実データE2E検証スクリプト(タスク19)。

「収集(bandaispirits.co.jp実fetch)→DB反映→商品照合→Profit Engine→Opportunity生成→
通知判定→Discord送信」の一連が本番ConoHa VPS上で実際に動くかを、人が最後に
Discordアプリで確認するところまで自動化する。

【設計】
1. app.scheduler.tasks.run_live_e2e_verification をCeleryへ.delay()で投入する
   (worker/beatが正しく起動・ブローカーに接続できているかも同時に確認するため、
   このスクリプト内で直接pipeline関数を呼ばず、必ずCelery経由にしている)。
2. worker側の実行結果(dict、JSON化可能)をポーリングで受け取る。
   収集〜通知判定までの各段階の成否がこの中に入っている。
3. should_send=Trueの場合のみ、このスクリプト自身のプロセスでdiscord.Clientを
   起動してembedを送信する(discord.pyの非同期ゲートウェイ接続はCeleryタスクの
   同期実行モデルと相性が悪いため、意図的にCeleryの外で行う。
   app/scheduler/tasks.pyのrun_live_e2e_verification docstring参照)。
4. 各段階の成否をまとめて標準出力に表示し、最後に「Discordのチャンネルを
   確認してください」を出力する(実際に届いたかどうかの最終確認はユーザーが
   スマホのDiscordアプリで行うため)。

使い方:
    venv/bin/python3 scripts/verify_live_e2e.py
    venv/bin/python3 scripts/verify_live_e2e.py --prd-id kimetsu29 --timeout 60

前提: docker composeでdb/redisが起動済み、Celery worker/beatが起動済みであること
(scripts/verify_live_e2e.sh が一式まとめて行う。単体でも使えるようにこのスクリプトは
独立して動く)。

【app/bot/main.py(常時起動Bot)との役割の違い(2026-08-05追加、混同防止)】
本スクリプトは**使い捨ての単発検証専用**のまま維持する。1回だけパイプラインを
実行してDiscordへ送信し、送信直後(または`--interaction-wait-seconds`指定時は
その秒数後)にBotを切断する設計は変更しない。

ボタン(応募済み/ウォッチ/非表示)を「時間を気にせず」検証したい場合は、本スクリプトの
`--interaction-wait-seconds`で無理に長時間待つのではなく、`app/bot/main.py`
(worker/beatと同様にdocker-composeで`restart: unless-stopped`常時稼働するBot)を
使うこと。`docs/live_verification_guide.md`「6. ボタンの実接続検証」参照。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402

STAGE_LABELS = {
    "fetch_bandaispirits_detail": "① bandaispirits.co.jp 実データ収集",
    "ingest": "② DB反映(Product/ReleaseEvent)",
    "synthetic_market_observation": "③ 相場データ(検証用の合成値)",
    "match": "④ 商品照合(Product Matcher)",
    "opportunity": "⑤ Profit Engine / Opportunity生成",
    "notify_decision": "⑥ 通知判定(dedupe/Embed組み立て)",
}


def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def _check_env() -> list[str]:
    problems = []
    if not settings.discord_bot_token:
        problems.append(
            "DISCORD_BOT_TOKEN が .env に設定されていません。"
            "docs/live_verification_guide.md の手順でBotトークンを取得し、.envに設定してください。"
        )
    if not settings.discord_notify_channel_id:
        problems.append(
            "DISCORD_NOTIFY_CHANNEL_ID が .env に設定されていません。"
            "docs/live_verification_guide.md の手順で通知先チャンネルIDを取得し、.envに設定してください。"
        )
    else:
        try:
            int(settings.discord_notify_channel_id)
        except ValueError:
            problems.append(
                f"DISCORD_NOTIFY_CHANNEL_ID の値が数字ではありません: {settings.discord_notify_channel_id!r}"
            )
    return problems


def _dispatch_and_wait(prd_id: str, timeout: int) -> dict:
    from app.scheduler.tasks import run_live_e2e_verification

    print(f"Celeryへタスクを投入します(app.scheduler.tasks.run_live_e2e_verification, prd_id={prd_id})...")
    async_result = run_live_e2e_verification.delay(prd_id)
    print(f"  task_id = {async_result.id}")
    print(f"  workerからの結果を待機します(最大{timeout}秒)...")

    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if async_result.ready():
            return async_result.get()
        time.sleep(1)

    raise TimeoutError(
        f"{timeout}秒待ってもworkerから結果が返りませんでした。"
        "Celery workerが起動しているか(scripts/verify_live_e2e.sh のログ)を確認してください。"
    )


def _print_stage_summary(result: dict) -> None:
    _print_header("パイプライン各段階の結果")
    for key, label in STAGE_LABELS.items():
        stage_result = result.get(key)
        if stage_result is None:
            print(f"[ - ] {label}: (未実施)")
            continue
        ok = stage_result.get("ok")
        mark = "[ OK ]" if ok else "[ NG ]"
        print(f"{mark} {label}")
        for k, v in stage_result.items():
            if k == "ok":
                continue
            print(f"        {k}: {v}")

    if not result.get("ok"):
        print()
        print(f"最終段階({result.get('stage')})で失敗しました: {result.get('error')}")


async def _send_to_discord(
    embed_dict: dict,
    channel_id: int,
    event_id: str,
    opportunity_id: str,
    interaction_wait_seconds: int,
) -> dict:
    """Embedを送信し、OpportunityActionView(応募済み/ウォッチ/非表示ボタン、
    app/notification/interaction_view.py)を添付する。interaction_wait_seconds>0の
    場合、送信後もクライアントを閉じずに待機し、その間に届いたInteraction
    (ボタン押下)をon_interactionでログ出力する
    (2026-08-05: ゲートウェイ実接続・ボタン動作検証のため追加。それまでは
    on_ready直後にcloseする一回限りの接続で、Embedのみ送信しView自体
    一度も添付されたことが無かった)。
    """
    import discord

    from app.notification.discord_bot import NotificationService
    from app.notification.interaction_view import OpportunityActionView

    send_result: dict = {}
    ready_event = asyncio.Event()
    interaction_count = 0

    intents = discord.Intents.default()

    class _VerificationClient(discord.Client):
        async def on_ready(self) -> None:
            print(f"  [ OK ] Discordゲートウェイへ接続しました(on_ready発火、user={self.user}, id={self.user.id})")
            ready_event.set()

        async def on_interaction(self, interaction: discord.Interaction) -> None:
            nonlocal interaction_count
            interaction_count += 1
            custom_id = interaction.data.get("custom_id") if interaction.data else None
            component_type = interaction.data.get("component_type") if interaction.data else None
            print(
                f"  [interaction受信 #{interaction_count}] "
                f"user={interaction.user}({interaction.user.id}) "
                f"custom_id={custom_id!r} component_type={component_type!r}"
            )

    client = _VerificationClient(intents=intents)
    start_task = asyncio.ensure_future(client.start(settings.discord_bot_token))

    try:
        ready_waiter = asyncio.ensure_future(ready_event.wait())
        done, _pending = await asyncio.wait({ready_waiter, start_task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)

        if start_task in done and start_task.exception() is not None:
            raise start_task.exception()

        if ready_waiter not in done:
            ready_waiter.cancel()
            send_result["ok"] = False
            send_result["error"] = (
                "on_readyが30秒以内に発火しませんでした。"
                "VPSからDiscordゲートウェイへの疎通、またはBotトークンの有効性を確認してください。"
            )
        else:
            try:
                service = NotificationService(client)
                view = OpportunityActionView(
                    event_id=event_id, opportunity_id=opportunity_id, timeout=interaction_wait_seconds or None
                )
                message = await service.send_opportunity_notification(channel_id, embed_dict, view=view)
                send_result["ok"] = True
                send_result["message_id"] = message.id
                send_result["channel_id"] = message.channel.id
                send_result["jump_url"] = message.jump_url
            except discord.Forbidden as exc:
                send_result["ok"] = False
                send_result["error"] = (
                    f"権限不足(discord.Forbidden): {exc}. "
                    "Botがそのチャンネルを見る/メッセージを送る権限を持っているか確認してください。"
                )
            except discord.NotFound as exc:
                send_result["ok"] = False
                send_result["error"] = (
                    f"チャンネルが見つかりません(discord.NotFound): {exc}. "
                    "DISCORD_NOTIFY_CHANNEL_ID が正しいか確認してください。"
                )
            except Exception as exc:  # noqa: BLE001 - 検証スクリプトなので極力全部拾って報告する
                send_result["ok"] = False
                send_result["error"] = f"{type(exc).__name__}: {exc}"

            if send_result.get("ok") and interaction_wait_seconds > 0:
                print()
                print(
                    f"  ボタンを{interaction_wait_seconds}秒待ちます。"
                    "Discordアプリで実際に「応募済みにする」「ウォッチリスト登録」「非表示」を押してみてください..."
                )
                await asyncio.sleep(interaction_wait_seconds)
                print(f"  待機終了。受信したInteraction数: {interaction_count}")
                send_result["interaction_count"] = interaction_count
    except discord.LoginFailure as exc:
        send_result["ok"] = False
        send_result["error"] = f"ログイン失敗(discord.LoginFailure): {exc}. DISCORD_BOT_TOKENが正しいか確認してください。"
    except discord.PrivilegedIntentsRequired as exc:
        send_result["ok"] = False
        send_result["error"] = (
            f"特権Intentが必要と判定されました({exc})。"
            "本スクリプトはdiscord.Intents.default()(非特権)のみ使うため通常は発生しないはずですが、"
            "発生した場合はDeveloper Portal → Bot → Privileged Gateway Intentsの設定を確認してください。"
        )
    finally:
        # LoginFailure/PrivilegedIntentsRequiredはゲートウェイ接続前に発生するため、
        # 内部のaiohttpセッションが開いたままだと"Unclosed connector"警告が出る。
        # 既にcloseされていても再度closeはno-opなので無条件に呼んでよい。
        if not client.is_closed():
            await client.close()
        if not start_task.done():
            start_task.cancel()
        try:
            await start_task
        except (asyncio.CancelledError, discord.LoginFailure, discord.PrivilegedIntentsRequired, Exception):
            pass
        # close()を呼んだ直後でもTCPConnectorの内部クリーンアップはイベントループの
        # 次のイテレーションで非同期に走るため、close()直後にasyncio.run()のコルーチンが
        # 即座に返ってしまうと間に合わず"Unclosed connector"ResourceWarningが出ることを
        # 実機再現で確認した(discord.py+aiohttpのteardownタイミングに起因する既知の挙動)。
        # 1イベントループぶんの猶予を与えることで解消する。
        await asyncio.sleep(0.25)

    return send_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prd-id",
        default="kimetsu29",
        help="bandaispirits.co.jpの商品prd_id(既定: kimetsu29。CLAUDE.md 1.6節で動作確認済み)",
    )
    parser.add_argument("--timeout", type=int, default=60, help="Celeryタスクの結果待ちタイムアウト秒(既定: 60)")
    parser.add_argument(
        "--interaction-wait-seconds",
        type=int,
        default=0,
        help=(
            "0より大きい場合、Embed送信後もその秒数だけBotの接続を維持し、"
            "応募済み/ウォッチ/非表示ボタンの押下(Interaction)を受信してログ表示する"
            "(2026-08-05追加、ゲートウェイ実接続・ボタン動作検証用。既定は0=送信後すぐ切断)"
        ),
    )
    args = parser.parse_args()

    _print_header("実データE2E検証(収集 → DB → 商品照合 → Profit Engine → Opportunity → Discord通知)")

    env_problems = _check_env()
    if env_problems:
        print("以下の設定が不足しているため、Discord送信の検証はできません:")
        for p in env_problems:
            print(f"  - {p}")
        print()
        print(
            "(収集〜Opportunity生成〜通知判定までは.envの設定に関わらず実行できるため、"
            "そこまでは検証を続行します)"
        )

    try:
        result = _dispatch_and_wait(args.prd_id, args.timeout)
    except TimeoutError as exc:
        print(f"\n[ NG ] {exc}")
        return 1

    _print_stage_summary(result)

    if not result.get("ok"):
        print("\n収集〜通知判定のパイプラインが途中で失敗したため、Discordへの送信は行いません。")
        return 1

    if not result.get("should_send"):
        print(
            "\nパイプラインは正常に完走しましたが、dedupe判定によりshould_send=Falseでした"
            "(前回と全く同じ内容だった場合に起こります。通常はrun_live_e2e_verification側で"
            "毎回わずかに金額を変えているため発生しないはずです。再実行してみてください)。"
        )
        return 1

    if env_problems:
        print("\n上記の設定不足のため、Discordへの実送信はスキップしました。")
        print(".envを設定してから再度実行してください。")
        return 1

    _print_header("Discordへ送信します")
    channel_id = int(settings.discord_notify_channel_id)
    event_id = result["ingest"]["release_event_id"]
    opportunity_id = result["opportunity"]["opportunity_id"]
    send_result = asyncio.run(
        _send_to_discord(result["embed"], channel_id, event_id, opportunity_id, args.interaction_wait_seconds)
    )

    if send_result.get("ok"):
        print(f"[ OK ] 送信成功: message_id={send_result['message_id']} channel_id={send_result['channel_id']}")
        print(f"        {send_result.get('jump_url', '')}")
        if "interaction_count" in send_result:
            print(f"        受信したInteraction数: {send_result['interaction_count']}")
    else:
        print(f"[ NG ] 送信失敗: {send_result.get('error')}")

    print()
    print(json.dumps({k: v for k, v in result.items() if k != "embed"}, ensure_ascii=False, indent=2))

    _print_header("最終確認")
    print("Discordのチャンネルを確認してください。")

    return 0 if send_result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
