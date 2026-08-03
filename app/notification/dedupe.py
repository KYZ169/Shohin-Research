"""重複防止・差分通知ロジック(実装仕様書4.2・4.3、Prompt 7)。

build_dedupe_key()/classify_dedupe()/resolved_cost_items()/build_diff_notification_text()は
純粋関数。Redisへの読み書きが必要なcheck_and_update_dedupe_state()のみRedis
クライアントを受け取るI/O層として分離する
(app/profit/profit_engine.pyのcalc_*() vs save_profit_snapshots()と同じ設計方針)。

【実装仕様書4.3の通知対象絞り込みについて】
fulfillment_type/regionによるフィルタは、タスク7で実装したapp/matcher/region_resolver.py
の`should_include_for_region_filter()`をそのまま再利用する(重複実装しない)。
信頼度(confidence)によるデフォルト除外は実装仕様書4.3で明示的に撤回されているため、
本モジュールには信頼度フィルタは存在しない(=A〜D全て通知対象)。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

__all__ = [
    "build_dedupe_key",
    "DedupeDecision",
    "classify_dedupe",
    "resolved_cost_items",
    "DedupeCheckResult",
    "check_and_update_dedupe_state",
    "build_diff_notification_text",
]

REDIS_KEY_PREFIX = "notification:"


def build_dedupe_key(
    event_id: str,
    shop_id: str | None,  # チェーン単位の場合はNone
    price: Decimal,
    deadline_at: datetime | None,
    excluded_cost_items: list[str],  # 未確定→確定の変化を検知するために含める
) -> str:
    """実装仕様書4.2のdedupeキー生成。

    元の実装例はdeadline_at: datetimeとして`.isoformat()`を直接呼んでいるが、
    実装仕様書1.3の確定事項どおりdeadline_atはnullable運用のため、Noneの場合は
    固定文字列"unknown"を使う(Noneで例外にしない)。
    """
    payload = "|".join(
        [
            event_id,
            shop_id or "national",
            str(price),
            deadline_at.isoformat() if deadline_at is not None else "unknown",
            ",".join(sorted(excluded_cost_items)),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class DedupeDecision(str, Enum):
    NEW = "new"  # 初めて通知するOpportunity
    UPDATED = "updated"  # 既存だが内容(価格/締切/excluded_cost_items等)が変化
    UNCHANGED = "unchanged"  # 既存かつ内容も同一(送信しない)


def classify_dedupe(previous_hash: str | None, current_hash: str) -> DedupeDecision:
    """実装仕様書4.2: 同一hash→送信しない、異なるhash→更新として再送。"""
    if previous_hash is None:
        return DedupeDecision.NEW
    if previous_hash == current_hash:
        return DedupeDecision.UNCHANGED
    return DedupeDecision.UPDATED


def resolved_cost_items(previous_excluded: list[str], current_excluded: list[str]) -> list[str]:
    """前回のexcluded_cost_itemsに含まれ、今回含まれなくなった項目
    (=未確定→確定に変化した項目)を検出する(実装仕様書4.2 差分通知)。"""
    return [item for item in previous_excluded if item not in current_excluded]


@dataclass
class DedupeCheckResult:
    decision: DedupeDecision
    dedupe_key: str
    newly_resolved_cost_items: list[str]


class SupportsHashOps(Protocol):
    """check_and_update_dedupe_state()が要求する最小限のRedisクライアントI/F。
    redis-py の Redis(decode_responses=True) インスタンスを想定する。
    """

    def hgetall(self, name: str) -> dict: ...
    def hset(self, name: str, mapping: dict) -> int: ...


def check_and_update_dedupe_state(
    redis_client: SupportsHashOps,
    event_id: str,
    dedupe_key: str,
    excluded_cost_items: list[str],
) -> DedupeCheckResult:
    """実装仕様書4.2: Redisに`notification:{event_id}`として最新のcontent_hashを保存し、
    新しい通知候補が生成されたらhashを比較して新規/更新/変化なしを判定する。

    redis_clientは`decode_responses=True`で生成されたクライアントを想定する
    (bytesではなくstrでやり取りするため)。
    """
    redis_key = f"{REDIS_KEY_PREFIX}{event_id}"
    stored = redis_client.hgetall(redis_key)

    previous_hash = stored.get("content_hash")
    previous_excluded = json.loads(stored["excluded_cost_items"]) if stored.get("excluded_cost_items") else []

    decision = classify_dedupe(previous_hash, dedupe_key)
    newly_resolved = resolved_cost_items(previous_excluded, excluded_cost_items)

    redis_client.hset(
        redis_key,
        mapping={
            "content_hash": dedupe_key,
            "excluded_cost_items": json.dumps(excluded_cost_items),
        },
    )

    return DedupeCheckResult(decision=decision, dedupe_key=dedupe_key, newly_resolved_cost_items=newly_resolved)


def build_diff_notification_text(
    previous_displayed_profit: Decimal,
    current_displayed_profit: Decimal,
    newly_resolved_cost_items: list[str],
    excluded_cost_item_labels: dict[str, str],
) -> str | None:
    """実装仕様書4.2の差分通知テンプレート例:
    「送料が判明しました。想定利益: ¥1,350 → ¥950(送料¥400反映済み)」

    newly_resolved_cost_itemsが空(未確定→確定の変化が無い通常の更新)ならNoneを
    返す(呼び出し側は通常の更新通知文言にフォールバックする)。

    excluded_cost_item_labelsはExcludedCostItemの識別子(.name)→日本語ラベル(.value)の
    対応表。呼び出し側から渡す設計にしているのは、この関数自体をprofit/notification両方の
    型に依存させずpurely文字列だけで完結させるため。

    元テンプレートの「(送料¥400反映済み)」のような具体的な反映額の表示は、
    本関数の入力に個別費目ごとの金額差分が無いため省略している
    (どの費目が確定したかは明示するが、金額の内訳までは対象外。要検討)。
    """
    if not newly_resolved_cost_items:
        return None

    resolved_labels = "・".join(
        excluded_cost_item_labels.get(name, name) for name in newly_resolved_cost_items
    )
    return (
        f"{resolved_labels}が判明しました。"
        f"想定利益: ¥{previous_displayed_profit:,} → ¥{current_displayed_profit:,}"
    )
