"""Opportunity Scorer(app/opportunity/opportunity_scorer.py)のテスト。

実装仕様書3章のcalc_score()を構成する各要素(confidence_weight/urgency_weight/
match_penalty/normalize)の分岐を網羅する(実装仕様書 Prompt 6)。
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.core.time import JST
from app.domain.enums import MatchStatus
from app.opportunity.opportunity_scorer import (
    CONFIDENCE_WEIGHTS,
    DEFAULT_PROFIT_NORMALIZATION_CAP,
    DEFAULT_ROI_NORMALIZATION_CAP,
    calc_score,
    normalize,
    urgency_weight,
)

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=JST)


# --- normalize() ---


def test_normalize_zero_value_is_zero():
    assert normalize(Decimal("0"), cap=Decimal("10000")) == 0.0


def test_normalize_at_cap_is_one():
    assert normalize(Decimal("10000"), cap=Decimal("10000")) == 1.0


def test_normalize_beyond_cap_clamps_to_one():
    assert normalize(Decimal("999999"), cap=Decimal("10000")) == 1.0


def test_normalize_negative_value_clamps_to_zero():
    """CLAUDE.md最重要方針2: 損失(マイナス)でもクラッシュせず0として扱う。"""
    assert normalize(Decimal("-500"), cap=Decimal("10000")) == 0.0


def test_normalize_none_value_is_zero():
    """purchase_price=0円等でROIが算出不能(None)の場合、加点0として扱う(除外しない)。"""
    assert normalize(None, cap=Decimal("1.0")) == 0.0


def test_normalize_halfway_value():
    assert normalize(Decimal("5000"), cap=Decimal("10000")) == 0.5


# --- urgency_weight() ---


def test_urgency_weight_is_zero_when_deadline_is_none():
    """実装仕様書1.3: deadline_at IS NULLの場合は緊急度スコアに加点しない。"""
    assert urgency_weight(None, now=NOW) == 0.0


def test_urgency_weight_is_max_when_deadline_already_passed():
    past_deadline = NOW - timedelta(hours=1)
    assert urgency_weight(past_deadline, now=NOW) == 1.0


def test_urgency_weight_is_zero_when_deadline_beyond_window():
    far_deadline = NOW + timedelta(hours=200)  # window(168h)より先
    assert urgency_weight(far_deadline, now=NOW, window_hours=Decimal("168")) == 0.0


def test_urgency_weight_is_halfway_at_half_window():
    halfway_deadline = NOW + timedelta(hours=84)  # window(168h)の半分
    assert urgency_weight(halfway_deadline, now=NOW, window_hours=Decimal("168")) == pytest.approx(0.5)


# --- calc_score(): confidence_weight ---


@pytest.mark.parametrize("confidence,expected_weight", list(CONFIDENCE_WEIGHTS.items()))
def test_calc_score_confidence_weight_branches(confidence, expected_weight):
    """A=1.0, B=0.7, C=0.4, D=0.15の各分岐を網羅する。他の項をすべて0にして分離する。"""
    score = calc_score(
        displayed_profit_standard=Decimal("0"),
        roi=None,
        confidence=confidence,
        deadline_at=None,
        match_status=MatchStatus.AUTO_MATCH,
        now=NOW,
    )

    assert score == pytest.approx(0.25 * expected_weight)


def test_calc_score_raises_for_unknown_confidence():
    with pytest.raises(KeyError):
        calc_score(
            displayed_profit_standard=Decimal("0"),
            roi=None,
            confidence="E",
            deadline_at=None,
            match_status=MatchStatus.AUTO_MATCH,
            now=NOW,
        )


# --- calc_score(): match_penalty ---


def test_calc_score_applies_needs_review_penalty():
    score = calc_score(
        displayed_profit_standard=Decimal("0"),
        roi=None,
        confidence="D",  # confidence_weight=0.15を打ち消して0にする
        deadline_at=None,
        match_status=MatchStatus.NEEDS_REVIEW,
        now=NOW,
    )

    # 0.25*0.15 - 0.05 = -0.0125
    assert score == pytest.approx(0.25 * 0.15 - 0.05)


@pytest.mark.parametrize(
    "match_status",
    [
        MatchStatus.AUTO_MATCH,
        MatchStatus.HIGH_PROBABILITY_MATCH,
        MatchStatus.DIFFERENT_PRODUCT,
        MatchStatus.MANUALLY_CONFIRMED,
    ],
)
def test_calc_score_no_penalty_for_statuses_other_than_needs_review(match_status):
    score = calc_score(
        displayed_profit_standard=Decimal("0"),
        roi=None,
        confidence="D",
        deadline_at=None,
        match_status=match_status,
        now=NOW,
    )

    assert score == pytest.approx(0.25 * 0.15)


# --- calc_score(): 統合(重み付き合計) ---


def test_calc_score_weighted_sum_matches_manual_calculation():
    """技術分析12.5の利益額(900円)・ROI(約32.1%)を用いた統合テスト。"""
    deadline = NOW + timedelta(hours=84)  # urgency=0.5

    score = calc_score(
        displayed_profit_standard=Decimal("900"),
        roi=Decimal("0.3214"),
        confidence="B",
        deadline_at=deadline,
        match_status=MatchStatus.HIGH_PROBABILITY_MATCH,
        now=NOW,
        profit_cap=DEFAULT_PROFIT_NORMALIZATION_CAP,
        roi_cap=DEFAULT_ROI_NORMALIZATION_CAP,
    )

    expected = (
        0.4 * float(Decimal("900") / DEFAULT_PROFIT_NORMALIZATION_CAP)
        + 0.2 * float(Decimal("0.3214") / DEFAULT_ROI_NORMALIZATION_CAP)
        + 0.25 * 0.7
        + 0.1 * 0.5
        - 0.0  # HIGH_PROBABILITY_MATCHはNEEDS_REVIEWではないためペナルティなし
    )
    assert score == pytest.approx(expected)


def test_calc_score_does_not_accept_unconfirmed_cost_flags():
    """実装仕様書3章: 未確定コストはスコア計算に一切含めない。
    calc_score()のシグネチャにhas_unconfirmed_cost/excluded_cost_items相当の
    パラメータが存在しないことをコード上の制約として保証する(呼びようがない)。
    """
    import inspect

    params = inspect.signature(calc_score).parameters
    assert "has_unconfirmed_cost" not in params
    assert "excluded_cost_items" not in params
