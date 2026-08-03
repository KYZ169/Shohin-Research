"""商品照合スコアリング(app/matcher/product_matcher.py)のテスト。

技術分析レポート11章の一致スコア設計・11.1状態遷移・11.3誤統合防止の原則を網羅する。
"""

from datetime import date

from app.domain.enums import MatchStatus
from app.matcher.product_matcher import (
    AUTO_MATCH_THRESHOLD,
    HIGH_PROBABILITY_MATCH_THRESHOLD,
    NEEDS_REVIEW_THRESHOLD,
    MatchCandidate,
    ProductAttributes,
    calc_match_score,
    classify_match_status,
    extract_product_attributes,
)


def _candidate(**overrides) -> MatchCandidate:
    defaults = dict(
        name="一番くじ CUTIE STREET くじを手にする戦いなのです！",
        identifiers={},
        brand=None,
        release_date=None,
        attributes=ProductAttributes(),
    )
    defaults.update(overrides)
    return MatchCandidate(**defaults)


# --- classify_match_status: 閾値境界 ---


def test_classify_match_status_thresholds():
    assert classify_match_status(AUTO_MATCH_THRESHOLD) == MatchStatus.AUTO_MATCH
    assert classify_match_status(AUTO_MATCH_THRESHOLD - 1) == MatchStatus.HIGH_PROBABILITY_MATCH
    assert classify_match_status(HIGH_PROBABILITY_MATCH_THRESHOLD) == MatchStatus.HIGH_PROBABILITY_MATCH
    assert classify_match_status(HIGH_PROBABILITY_MATCH_THRESHOLD - 1) == MatchStatus.NEEDS_REVIEW
    assert classify_match_status(NEEDS_REVIEW_THRESHOLD) == MatchStatus.NEEDS_REVIEW
    assert classify_match_status(NEEDS_REVIEW_THRESHOLD - 1) == MatchStatus.DIFFERENT_PRODUCT
    assert classify_match_status(0) == MatchStatus.DIFFERENT_PRODUCT
    assert classify_match_status(-100) == MatchStatus.DIFFERENT_PRODUCT


def test_classify_match_status_never_returns_manually_confirmed():
    """MANUALLY_CONFIRMEDはスコアから導出されず、呼び出し側(人間の確定操作)が設定する状態。"""
    for score in range(-200, 300, 10):
        assert classify_match_status(score) != MatchStatus.MANUALLY_CONFIRMED


# --- calc_match_score: 加点要素 ---


def test_jan_match_alone_results_in_auto_match():
    """技術分析11.2: JAN/ISBN完全一致があれば即「自動一致」。"""
    new_item = _candidate(name="全く違う商品名", identifiers={"jan": "4901234567890"})
    existing = _candidate(name="別の表記のタイトル", identifiers={"jan": "4901234567890"})

    result = calc_match_score(new_item, existing)

    assert result.status == MatchStatus.AUTO_MATCH
    assert result.score >= AUTO_MATCH_THRESHOLD


def test_model_sku_match_contributes_80_points():
    new_item = _candidate(name="たまごっち", identifiers={"model": "cutiestreet"})
    existing = _candidate(name="ウルトラマン", identifiers={"model": "cutiestreet"})

    result = calc_match_score(new_item, existing)

    assert result.score == 80


def test_isbn_match_is_treated_like_jan():
    new_item = _candidate(name="たまごっち", identifiers={"isbn": "9784001234567"})
    existing = _candidate(name="ウルトラマン", identifiers={"isbn": "9784001234567"})

    result = calc_match_score(new_item, existing)

    assert result.score == 100


def test_identifier_mismatch_contributes_no_points():
    new_item = _candidate(name="たまごっち", identifiers={"jan": "111"})
    existing = _candidate(name="ウルトラマン", identifiers={"jan": "222"})

    result = calc_match_score(new_item, existing)

    assert result.score == 0


def test_name_similarity_score_buckets():
    exact = calc_match_score(_candidate(name="一番くじ CUTIE STREET"), _candidate(name="一番くじ CUTIE STREET"))
    assert exact.score == 40

    unrelated = calc_match_score(_candidate(name="一番くじ CUTIE STREET"), _candidate(name="全く関係ない別商品ですよ"))
    assert unrelated.score == 0


def test_brand_match_contributes_15_points():
    new_item = _candidate(name="X", brand="BANDAI SPIRITS")
    existing = _candidate(name="Y", brand="BANDAI SPIRITS")

    result = calc_match_score(new_item, existing)

    assert result.score == 15


def test_brand_mismatch_contributes_no_points():
    new_item = _candidate(name="X", brand="BANDAI SPIRITS")
    existing = _candidate(name="Y", brand="Takara Tomy")

    result = calc_match_score(new_item, existing)

    assert result.score == 0


def test_release_date_within_3_days_contributes_10_points():
    new_item = _candidate(name="X", release_date=date(2026, 8, 4))
    existing = _candidate(name="Y", release_date=date(2026, 8, 1))

    result = calc_match_score(new_item, existing)

    assert result.score == 10


def test_release_date_beyond_3_days_contributes_no_points():
    new_item = _candidate(name="X", release_date=date(2026, 8, 10))
    existing = _candidate(name="Y", release_date=date(2026, 8, 1))

    result = calc_match_score(new_item, existing)

    assert result.score == 0


# --- calc_match_score: 減点要素(誤統合防止) ---


def test_unit_mismatch_forces_score_below_auto_match_even_with_perfect_other_signals():
    """技術分析11.2/11.3: 単位不一致は強制不一致。他の要素が満点でも自動一致にしない。"""
    new_item = _candidate(
        name="一番くじ CUTIE STREET くじを手にする戦いなのです！",
        identifiers={"jan": "123", "model": "cutiestreet"},
        brand="BANDAI SPIRITS",
        release_date=date(2026, 8, 4),
        attributes=ProductAttributes(unit="box"),
    )
    existing = _candidate(
        name="一番くじ CUTIE STREET くじを手にする戦いなのです！",
        identifiers={"jan": "123", "model": "cutiestreet"},
        brand="BANDAI SPIRITS",
        release_date=date(2026, 8, 5),
        attributes=ProductAttributes(unit="single"),
    )

    result = calc_match_score(new_item, existing)

    # 単位以外は満点(100+80+40+15+10=245)だが、単位不一致で自動一致未満にキャップされる
    assert result.status != MatchStatus.AUTO_MATCH
    assert result.score < AUTO_MATCH_THRESHOLD


def test_unit_mismatch_penalty_alone_results_in_different_product():
    new_item = _candidate(name="X", attributes=ProductAttributes(unit="box"))
    existing = _candidate(name="Y", attributes=ProductAttributes(unit="single"))

    result = calc_match_score(new_item, existing)

    assert result.score == -100
    assert result.status == MatchStatus.DIFFERENT_PRODUCT


def test_unit_unknown_on_one_side_does_not_trigger_mismatch_penalty():
    """不明を理由に誤って不一致判定しない(CLAUDE.md最重要方針2と同じ考え方)。"""
    new_item = _candidate(name="X", attributes=ProductAttributes(unit="box"))
    existing = _candidate(name="Y", attributes=ProductAttributes(unit=None))

    result = calc_match_score(new_item, existing)

    assert result.score == 0


def test_edition_mismatch_contributes_minus_60():
    new_item = _candidate(name="X", attributes=ProductAttributes(edition="limited"))
    existing = _candidate(name="Y", attributes=ProductAttributes(edition="regular"))

    result = calc_match_score(new_item, existing)

    assert result.score == -60


def test_bonus_mismatch_contributes_minus_40():
    new_item = _candidate(name="X", attributes=ProductAttributes(has_bonus=True))
    existing = _candidate(name="Y", attributes=ProductAttributes(has_bonus=False))

    result = calc_match_score(new_item, existing)

    assert result.score == -40


def test_bonus_unknown_on_one_side_does_not_trigger_mismatch_penalty():
    new_item = _candidate(name="X", attributes=ProductAttributes(has_bonus=True))
    existing = _candidate(name="Y", attributes=ProductAttributes(has_bonus=None))

    result = calc_match_score(new_item, existing)

    assert result.score == 0


# --- extract_product_attributes: キーワード抽出 ---


def test_extract_product_attributes_detects_unit_keywords():
    assert extract_product_attributes("一番くじ ◯◯ BOX購入特典付き").unit == "box"
    assert extract_product_attributes("◯◯ 1パック").unit == "pack"
    assert extract_product_attributes("◯◯ 単品バラ売り").unit == "single"
    assert extract_product_attributes("◯◯ 通常商品").unit is None


def test_extract_product_attributes_detects_edition_keywords():
    assert extract_product_attributes("◯◯ 初回限定版").edition == "limited"
    assert extract_product_attributes("◯◯ 通常版").edition == "regular"
    assert extract_product_attributes("◯◯ 何も付かない商品名").edition is None


def test_extract_product_attributes_detects_bonus_keywords():
    assert extract_product_attributes("◯◯ 特典付き").has_bonus is True
    assert extract_product_attributes("◯◯ 特典なし").has_bonus is False
    assert extract_product_attributes("◯◯ 言及なし").has_bonus is None


def test_extract_product_attributes_all_none_for_plain_name():
    attrs = extract_product_attributes("一番くじ CUTIE STREET くじを手にする戦いなのです！")

    assert attrs.unit is None
    assert attrs.edition is None
    assert attrs.has_bonus is None
