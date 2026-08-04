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


async def _send_to_discord(embed_dict: dict, channel_id: int) -> dict:
    import discord

    from app.notification.discord_bot import NotificationService

    send_result: dict = {}

    intents = discord.Intents.default()

    class _OneShotClient(discord.Client):
        async def on_ready(self) -> None:
            try:
                service = NotificationService(self)
                message = await service.send_opportunity_notification(channel_id, embed_dict)
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
            finally:
                await self.close()

    client = _OneShotClient(intents=intents)
    try:
        await asyncio.wait_for(client.start(settings.discord_bot_token), timeout=30)
    except asyncio.TimeoutError:
        send_result.setdefault("ok", False)
        send_result.setdefault(
            "error",
            "on_readyが30秒以内に発火しませんでした。"
            "VPSからDiscordゲートウェイへの疎通、またはBotトークンの有効性を確認してください。",
        )
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

    return send_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prd-id",
        default="kimetsu29",
        help="bandaispirits.co.jpの商品prd_id(既定: kimetsu29。CLAUDE.md 1.6節で動作確認済み)",
    )
    parser.add_argument("--timeout", type=int, default=60, help="Celeryタスクの結果待ちタイムアウト秒(既定: 60)")
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
    send_result = asyncio.run(_send_to_discord(result["embed"], channel_id))

    if send_result.get("ok"):
        print(f"[ OK ] 送信成功: message_id={send_result['message_id']} channel_id={send_result['channel_id']}")
        print(f"        {send_result.get('jump_url', '')}")
    else:
        print(f"[ NG ] 送信失敗: {send_result.get('error')}")

    print()
    print(json.dumps({k: v for k, v in result.items() if k != "embed"}, ensure_ascii=False, indent=2))

    _print_header("最終確認")
    print("Discordのチャンネルを確認してください。")

    return 0 if send_result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
