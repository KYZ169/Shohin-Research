"""Opportunity Scorer(実装仕様書3章・Prompt 6、技術分析レポート13.3)。

score = w1 * normalize(displayed_profit_standard)
      + w2 * normalize(roi)
      + w3 * confidence_weight(confidence)   # A=1.0, B=0.7, C=0.4, D=0.15
      + w4 * urgency_weight(deadline_at)      # 締切が近いほど高スコア
      - w5 * penalty(match_status == 要確認)

初期値: w1=0.4, w2=0.2, w3=0.25, w4=0.1, w5=0.05(技術分析13.3、実装仕様書3章と一致)。

【重要】未確定コスト(has_unconfirmed_cost/excluded_cost_items)はこの式に一切含めない
(実装仕様書3章に明記)。表示上のフラグとしてのみ独立して扱う。calc_score()の引数にも
含めていない。

【normalize()/urgency_weight()の実装方針に関する注記】
技術分析13.3・実装仕様書3章のいずれにも、normalize()の正規化方式や
urgency_weight()の減衰カーブの具体的な計算式は示されていない
(「初期値は仮置きとし、通知結果と実際の売買結果を蓄積後に調整する運用を推奨」との
記載のみ)。本実装では以下の暫定ルールを採用する。CLAUDE.md 0.5「自分で判断して
進めてよいこと」に該当する暫定実装のため、要検証と明記した上で進める。

- normalize(value, cap): 0..capの線形正規化(cap以上で1.0、0未満で0.0にクランプ)。
  cap値(利益10,000円・ROI 100%)は暫定値。実データ蓄積後に見直すことを前提とする。
- urgency_weight(deadline_at, now, window_hours): 残り時間がwindow_hours(初期値7日)
  以内なら締切に近いほど線形に1.0へ近づく。deadline_atがNoneの場合は
  実装仕様書1.3の指示どおり0.0(緊急度に加点しない。除外はしない)。
"""

from datetime import datetime
from decimal import Decimal

from app.core.time import JST
from app.domain.enums import MatchStatus

__all__ = [
    "PROFIT_WEIGHT",
    "ROI_WEIGHT",
    "CONFIDENCE_WEIGHT_FACTOR",
    "URGENCY_WEIGHT_FACTOR",
    "NEEDS_REVIEW_PENALTY",
    "CONFIDENCE_WEIGHTS",
    "DEFAULT_PROFIT_NORMALIZATION_CAP",
    "DEFAULT_ROI_NORMALIZATION_CAP",
    "DEFAULT_URGENCY_WINDOW_HOURS",
    "normalize",
    "urgency_weight",
    "calc_score",
]

# 技術分析13.3・実装仕様書3章の初期値
PROFIT_WEIGHT = 0.4
ROI_WEIGHT = 0.2
CONFIDENCE_WEIGHT_FACTOR = 0.25
URGENCY_WEIGHT_FACTOR = 0.1
NEEDS_REVIEW_PENALTY = 0.05

CONFIDENCE_WEIGHTS: dict[str, float] = {"A": 1.0, "B": 0.7, "C": 0.4, "D": 0.15}

# 要検証(モジュールdocstring参照): 実データ蓄積後に調整する前提の暫定値
DEFAULT_PROFIT_NORMALIZATION_CAP = Decimal("10000")
DEFAULT_ROI_NORMALIZATION_CAP = Decimal("1.0")
DEFAULT_URGENCY_WINDOW_HOURS = Decimal("168")  # 7日


def normalize(value: Decimal | None, cap: Decimal) -> float:
    """0..capの範囲を0.0〜1.0に線形正規化する(cap以上は1.0、負値は0.0にクランプ)。

    valueがNone(算出不能。例: purchase_price=0でROIが定義できない場合)は0.0を返す
    (除外はしない。単にその項目の加点が無いだけ)。
    """
    if value is None or cap <= 0:
        return 0.0
    ratio = value / cap
    return float(max(Decimal("0"), min(Decimal("1"), ratio)))


def urgency_weight(
    deadline_at: datetime | None,
    now: datetime | None = None,
    window_hours: Decimal = DEFAULT_URGENCY_WINDOW_HOURS,
) -> float:
    """締切が近いほど高スコア(0.0〜1.0)。

    実装仕様書1.3: deadline_at IS NULLの場合は締切ベースの緊急度スコアに加点しない
    (0.0を返す。除外はしない、表示はする)。
    """
    if deadline_at is None:
        return 0.0
    if window_hours <= 0:
        return 0.0

    resolved_now = now or datetime.now(tz=JST)
    remaining_hours = Decimal((deadline_at - resolved_now).total_seconds()) / Decimal(3600)

    if remaining_hours <= 0:
        return 1.0  # 締切を過ぎている/今まさに締切の場合は最大緊急度として扱う
    if remaining_hours >= window_hours:
        return 0.0
    return float(Decimal("1") - remaining_hours / window_hours)


def calc_score(
    displayed_profit_standard: Decimal,
    roi: Decimal | None,
    confidence: str,
    deadline_at: datetime | None,
    match_status: MatchStatus,
    *,
    now: datetime | None = None,
    profit_cap: Decimal = DEFAULT_PROFIT_NORMALIZATION_CAP,
    roi_cap: Decimal = DEFAULT_ROI_NORMALIZATION_CAP,
    urgency_window_hours: Decimal = DEFAULT_URGENCY_WINDOW_HOURS,
) -> float:
    """実装仕様書3章のOpportunityスコアを計算する。

    confidenceは"A"/"B"/"C"/"D"のいずれか(NormalizedItem.confidence_hint /
    MarketObservation.confidenceと同じ表記)。未知の値を渡すとKeyErrorになる
    (信頼度モデル自体はConfidenceModel側の責務であり、ここでは検証しない)。
    """
    confidence_weight = CONFIDENCE_WEIGHTS[confidence]
    urgency = urgency_weight(deadline_at, now=now, window_hours=urgency_window_hours)
    match_penalty = NEEDS_REVIEW_PENALTY if match_status == MatchStatus.NEEDS_REVIEW else 0.0

    return (
        PROFIT_WEIGHT * normalize(displayed_profit_standard, profit_cap)
        + ROI_WEIGHT * normalize(roi, roi_cap)
        + CONFIDENCE_WEIGHT_FACTOR * confidence_weight
        + URGENCY_WEIGHT_FACTOR * urgency
        - match_penalty
    )
