"""商品照合スコアリング(技術分析レポート11章)。

region_resolver.pyと同じ方針で、DBセッションに依存しない純粋関数として実装する。
呼び出し側が「新規アイテム」と「候補となる既存商品」をそれぞれMatchCandidateに
詰め替えて渡す形とし、この関数自体はORM/DBを一切知らない。

【最重要方針(技術分析レポート11.3・CLAUDE.md最重要方針3)】
- 誤って別商品を統合する方が、統合できず個別表示されるより害が大きい。
  減点要素(単位不一致・限定版不一致等)は加点要素より強く効かせる。
- 商品単位の不一致は「強制不一致」であり、他の加点要素がどれだけ高くても
  自動一致(auto_match)にはしない(calc_match_score内でスコア上限をキャップする)。
- AIによる属性抽出(限定版か通常版か・特典有無・単位等の構造化)はスコアの一要素として
  使ってよいが、統合の確定判定はルールベースのスコアだけで行う。本モジュールの
  extract_product_attributes()はキーワードベースのルール実装であり、AI呼び出しは
  行わない(将来的にAIで属性抽出精度を上げる場合も、置き換え先はここに限定する)。
"""

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from app.domain.enums import MatchStatus

__all__ = [
    "ProductAttributes",
    "MatchCandidate",
    "MatchResult",
    "extract_product_attributes",
    "calc_match_score",
    "classify_match_status",
]

# 技術分析レポート11.2の閾値
AUTO_MATCH_THRESHOLD = 90
HIGH_PROBABILITY_MATCH_THRESHOLD = 70
NEEDS_REVIEW_THRESHOLD = 40

# 単位不一致は「強制不一致」のため、どれだけ加点があってもauto_matchの閾値未満に
# クランプする(技術分析レポート11.2: 「単位が異なれば自動一致させない」)。
UNIT_MISMATCH_SCORE_CAP = AUTO_MATCH_THRESHOLD - 1

_UNIT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "box": ("BOX", "ボックス", "box"),
    "pack": ("パック", "pack"),
    "single": ("単品", "バラ売り", "バラ"),
}
_EDITION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "limited": ("限定", "初回限定", "初回版"),
    "regular": ("通常版", "通常"),
}
_BONUS_PRESENT_KEYWORDS = ("特典付き", "特典あり", "おまけ付き")
_BONUS_ABSENT_KEYWORDS = ("特典なし", "特典無し")


@dataclass
class ProductAttributes:
    """商品名から抽出した構造化属性。

    どの属性も「判定できない」場合はNoneとする(不明を理由に誤って
    不一致判定しないため。calc_match_scoreは両側がNoneでない場合のみ比較する)。
    """

    unit: str | None = None  # "box" / "pack" / "single" / None
    edition: str | None = None  # "limited" / "regular" / None
    has_bonus: bool | None = None


@dataclass
class MatchCandidate:
    """スコアリング対象の1レコード分の入力。新規アイテム・既存商品候補の両方で使う。"""

    name: str
    identifiers: dict[str, str] = field(default_factory=dict)  # {"jan": "...", "model": "..."}
    brand: str | None = None
    release_date: date | None = None
    attributes: ProductAttributes = field(default_factory=ProductAttributes)


@dataclass
class MatchResult:
    score: int
    status: MatchStatus
    reasons: list[str]


def _find_keyword(text: str, keyword_map: dict[str, tuple[str, ...]]) -> str | None:
    for label, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords):
            return label
    return None


def extract_product_attributes(product_name: str) -> ProductAttributes:
    """商品名からunit/edition/has_bonusをキーワードベースで抽出する。

    技術分析レポート11.3原則2: ここをAIによる属性抽出に置き換える余地はあるが、
    現時点ではルールベースのキーワード検出のみで実装する。
    """
    unit = _find_keyword(product_name, _UNIT_KEYWORDS)
    edition = _find_keyword(product_name, _EDITION_KEYWORDS)

    has_bonus: bool | None = None
    if any(keyword in product_name for keyword in _BONUS_PRESENT_KEYWORDS):
        has_bonus = True
    elif any(keyword in product_name for keyword in _BONUS_ABSENT_KEYWORDS):
        has_bonus = False

    return ProductAttributes(unit=unit, edition=edition, has_bonus=has_bonus)


def _identifier_match(a: dict[str, str], b: dict[str, str], key: str) -> bool:
    value_a = a.get(key, "").strip()
    value_b = b.get(key, "").strip()
    return bool(value_a) and value_a == value_b


def _title_similarity(name_a: str, name_b: str) -> float:
    """商品名の類似度(0.0〜1.0)。DBのpg_trgm(task2で有効化済み)は候補の絞り込みに使う想定で、
    ここでのスコア計算自体は標準ライブラリのdifflibによる編集距離ベースの比率を用いる
    (DBセッションに依存しない設計のため)。
    """
    normalized_a = re.sub(r"\s+", "", name_a)
    normalized_b = re.sub(r"\s+", "", name_b)
    if not normalized_a or not normalized_b:
        return 0.0
    return SequenceMatcher(None, normalized_a, normalized_b).ratio()


def _name_similarity_score(similarity: float) -> int:
    """技術分析レポート11.2: 0.9以上で+40、0.7〜0.9で+20、0.5〜0.7で+5。"""
    if similarity >= 0.9:
        return 40
    if similarity >= 0.7:
        return 20
    if similarity >= 0.5:
        return 5
    return 0


def _is_definite_mismatch(value_a: str | None, value_b: str | None) -> bool:
    """両側とも判定できていて、かつ値が異なる場合のみ「不一致」とする。
    どちらか一方でも不明(None)なら、不明を理由に不一致とはしない。
    """
    return value_a is not None and value_b is not None and value_a != value_b


def calc_match_score(new_item: MatchCandidate, existing: MatchCandidate) -> MatchResult:
    """技術分析レポート11.2の一致スコアを計算し、11.1の状態遷移ラベルを付与する。"""
    score = 0
    reasons: list[str] = []

    if _identifier_match(new_item.identifiers, existing.identifiers, "jan") or _identifier_match(
        new_item.identifiers, existing.identifiers, "isbn"
    ):
        score += 100
        reasons.append("JAN/ISBN完全一致(+100)")

    if _identifier_match(new_item.identifiers, existing.identifiers, "model") or _identifier_match(
        new_item.identifiers, existing.identifiers, "sku"
    ):
        score += 80
        reasons.append("型番/SKU完全一致(+80)")

    similarity = _title_similarity(new_item.name, existing.name)
    name_score = _name_similarity_score(similarity)
    if name_score:
        score += name_score
        reasons.append(f"商品名類似度{similarity:.2f}(+{name_score})")

    if new_item.brand and existing.brand and new_item.brand == existing.brand:
        score += 15
        reasons.append("ブランド/シリーズ一致(+15)")

    if new_item.release_date and existing.release_date:
        delta_days = abs((new_item.release_date - existing.release_date).days)
        if delta_days <= 3:
            score += 10
            reasons.append(f"発売日差{delta_days}日以内(+10)")

    unit_mismatch = _is_definite_mismatch(new_item.attributes.unit, existing.attributes.unit)
    if unit_mismatch:
        score -= 100
        reasons.append(
            f"商品単位不一致({new_item.attributes.unit} vs {existing.attributes.unit})(-100・強制不一致)"
        )

    if _is_definite_mismatch(new_item.attributes.edition, existing.attributes.edition):
        score -= 60
        reasons.append(
            f"限定版/通常版不一致({new_item.attributes.edition} vs {existing.attributes.edition})(-60)"
        )

    if (
        new_item.attributes.has_bonus is not None
        and existing.attributes.has_bonus is not None
        and new_item.attributes.has_bonus != existing.attributes.has_bonus
    ):
        score -= 40
        reasons.append("特典有無不一致(-40)")

    if unit_mismatch:
        # 「単位が異なれば自動一致させない」を、他の加点要素の合計に関わらず保証する。
        score = min(score, UNIT_MISMATCH_SCORE_CAP)

    status = classify_match_status(score)
    return MatchResult(score=score, status=status, reasons=reasons)


def classify_match_status(score: int) -> MatchStatus:
    """技術分析レポート11.2の閾値。MANUALLY_CONFIRMEDはスコアから導出されないため
    ここでは返さない(呼び出し側が人間の確定操作を受けて設定する)。
    """
    if score >= AUTO_MATCH_THRESHOLD:
        return MatchStatus.AUTO_MATCH
    if score >= HIGH_PROBABILITY_MATCH_THRESHOLD:
        return MatchStatus.HIGH_PROBABILITY_MATCH
    if score >= NEEDS_REVIEW_THRESHOLD:
        return MatchStatus.NEEDS_REVIEW
    return MatchStatus.DIFFERENT_PRODUCT
