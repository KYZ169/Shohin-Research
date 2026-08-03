"""MarketObservation(相場観測値)を既存Productへ照合するパイプライン(タスク13)。

相場データ単独から新規Productを登録することはしない(技術分析11章のProduct Matcherは
「仕入れ側の商品」と「相場側の商品」を同一ロジックで照合する設計だが、相場データにしか
現れない商品を新規登録すると、実体の無い仕入れ機会が生成されてしまうため)。
マッチしない場合はNoneを返し、呼び出し側で「対応する仕入れイベントが無いので
Opportunityを作らない」判断をする。

【マッチ判定の閾値について】
DIFFERENT_PRODUCT(スコア40点未満)のみを「マッチ無し」として除外し、
AUTO_MATCH/HIGH_PROBABILITY_MATCH/NEEDS_REVIEWはいずれもマッチとして扱う。
一見「要確認」相当まで機械的に紐付けるのは誤爆リスクが高いようにも思えるが、
技術分析11.3原則3「高確率一致であっても、初回は必ず通知に要確認フラグを出し、
ユーザーが誤りに気づけるようにする」、およびCLAUDE.md最重要方針2「機会損失より
目視確認を優先する」との整合を優先した。match_statusはOpportunityにそのまま
記録され、Opportunity Scorer(タスク10)がNEEDS_REVIEWに-0.05のペナルティを
自動的に課すため、「マッチしたが確信度は低い」という情報は失われない。
またJAN/型番等の識別子が無い現状のCollector実装(タスク4・6時点)では、商品名の
類似度だけで70点(HIGH_PROBABILITY_MATCH)に届くケースは限定的なため、40点未満
だけを除外するくらいが現実的な閾値になる。
"""

from sqlalchemy.orm import Session

from app.collectors.markets.base import MarketObservation
from app.db.models import Product
from app.domain.enums import MatchStatus
from app.matcher.product_matcher import MatchCandidate, extract_product_attributes
from app.pipeline.ingest import find_best_match

__all__ = ["match_observation_to_product"]


def match_observation_to_product(
    session: Session, observation: MarketObservation
) -> tuple[Product, MatchStatus] | None:
    """MarketObservation.extra["title"](タスク13で追加、app/collectors/markets/suruga_ya.py参照)を
    既存Productと照合する。DIFFERENT_PRODUCT(モジュールdocstring参照)のみマッチ無しとする。
    """
    title = observation.extra.get("title")
    if not title:
        return None

    candidate = MatchCandidate(name=title, attributes=extract_product_attributes(title))
    best_product, best_result = find_best_match(session, candidate)

    if best_product is None or best_result is None:
        return None
    if best_result.status == MatchStatus.DIFFERENT_PRODUCT:
        return None

    return best_product, best_result.status
