"""ポケモンセンターオンラインCollector(pokemoncenter-online.com)。

CLAUDE.md 1.4、Fixture(pokemon_center_online_product_20260803.md)の実測結果に基づく:
- pokemoncenter-online.com: Bot対策なし、UTF-8、取得可能。
- 商品詳細ページ(https://www.pokemoncenter-online.com/{商品コード}.html)の
  「各種期間」セクションに、抽選応募受け付け期間/抽選結果発表日/購入および支払い期間/
  商品のお届け時期の4種類が日時付きで記載されている。一番くじ(on-line.1kuji.com)で
  取得できなかった締切・当選発表・購入期限が、このサイトでは全部取れる
  (CLAUDE.md 1.4で一番くじと並ぶ最優先候補に格上げされた情報源)。

【検証状況(2026-08-03、本番ConoHa VPSでの実データ検証により更新)】
本番VPSで実際に取得した生HTML(tests/fixtures/raw_html/raw_pokemon_center.html)で
動作検証済み。「各種期間」のラベル+値の並び(`_value_after_label()`によるテキスト
パターンマッチ)は実データでもそのまま機能し、抽選応募受け付け期間/抽選結果発表日/
購入および支払い期間/商品のお届け時期の4フィールドとも正しく抽出できることを確認した。
一方、価格の抽出だけは実データで失敗することが判明し修正した:
- 価格は`<p class="price default"><span class="txt">5,800<small>円</small></span>
  <small class="sml">税込</small></p>`のように、金額の数字と「円」「税込」が別々の
  (入れ子になった)要素に分かれている。`tree.body.text(deep=True, separator="\n")`で
  フラット化すると「5,800」「円」「税込」がそれぞれ別行になり、1行ずつ検索していた
  従来のPRICE_PATTERNでは一致しなかった(price=Noneのまま失敗)。行単位ではなく
  本文全体(text)に対して、空白文字(改行含む)を許容する形でマッチするよう修正した。
- 在庫状況(品切れ/予約等)のラベルも同様の理由で、価格行を境界とした行範囲検索を
  やめ、本文全体から最初に一致した行を採用するよう単純化した(実データでも
  「品切れ」(在庫ラベル)の後、「各種期間」より前の「予約 / 販売期間」という
  別の文脈で"予約"の語が再度出現するが、最初の一致を採用することで正しく
  区別できることを確認済み)。

【新商品一覧ページの発見・段階1対応(2026-08-06)】
CLAUDE.md 3節・12節のとおり、on-line.1kuji.com商品一覧ページ(タスク18)と同様の
「新商品自動発見」がポケセンには無い状態が長らく未確認のまま残っていた。トップページの
ナビゲーションから新商品一覧`https://www.pokemoncenter-online.com/search/?prefn1=releaseType&prefv1=1&srule=top-new-product`
(Salesforce Commerce Cloud構成、Bot対策なし)を発見し、実際に取得・検証した。

- **ページング**: `<div class="grid-footer" data-page-size="40.0" data-page-number="1.0">`と
  `<select name="page">`(1〜8のoption)から、初期表示は1ページ40件・全8ページの
  「もっと見る」型(無限スクロール相当)であることを確認した。URLの`start`/`sz`
  クエリパラメータで挙動を検証したところ、`start`はどの値を渡しても常に0として
  扱われ、`sz`のみが「累積で何件返すか」を制御する(`sz=40`→41件、`sz=80`→81件、
  `sz=500`→275件でそれ以上増えない=これが実際の全新商品件数)ことを確認した。
  つまり複数ページを順に辿る必要はなく、`sz`を十分大きくした1回のリクエストで
  全件を取得できる。
- **この1URLのみ例外的にホワイトリスト方式で自動収集対象に追加した**(段階1、下記参照)。
  `NEW_PRODUCT_LIST_URL`は上記の検索条件に`start=0&sz=500`を固定で付与した1本の
  定数URLとし、`fetch()`は`/search/`パスについてこの完全一致のみを許可する
  (on-line.1kuji.comと同じホワイトリスト方式、実装仕様書9章/CLAUDE.md 1.1参照)。
  `sz=500`は2026-08-06時点の実件数(275件)に余裕を持たせた固定値であり、将来
  新商品総数がこれを超えた場合は取りこぼしうる(要継続監視、致命的ではない)。
- **段階1のスコープ**: 一覧ページから商品コードを抽出し、`fetch_product()`
  (既存、個別ページのfetch→parse→normalize)を各コードに対して呼び出すところまで。
  Beat Scheduleへの登録・DB反映(ingest_normalized_item()等への結線)は段階2として
  別途対応する(タスク18→20と同じ2段階の進め方)。275件全件を毎回individual fetch
  するのは実行コストが高いため、段階2で「DB未登録の新規コードのみfetchする」等の
  差分化を検討する必要がある(現時点では未対応、このコメントのみ)。

【通常販売ページの構造未確認】
Fixtureは抽選販売商品の1件のみ。Fixture注記2のとおり、通常販売・予約販売ページで
「各種期間」セクション自体が存在しない可能性が高いという推測に基づき、このセクションの
有無でevent_type(LOTTERY/NORMAL_SALE)を判定しているが、通常販売ページの実データによる
裏取りはできていない(要検証)。

【時刻表記についての補足】
Fixture注記1のサンプル正規表現は「時:分」のコロン区切りを想定して書かれていたが、
Fixture本文中の実際の日時表記は「16時00分」のような漢字区切りであり、コロンは
一度も出現しない(サンプル正規表現と本文実測値が食い違っていた)。本モジュールの
PERIOD_RANGE_PATTERN/SINGLE_DATETIME_PATTERNは本文の実測表記(漢字区切り)を採用している。

【商品コード(13桁)とJANコードの関係(2026-08-05、サンプル5件による確認)】
CLAUDE.md 3節の「商品コードがJANコードと一致するかの確認」に対応する。通常販売商品
3件("4521329432069"/"4521329413051"/"4521329338453")は全て"45"始まり、抽選販売商品
2件("9900000006082"/"9900000006808")は全て"99"始まりだった。JAN-13の国コード部の
うち"45"/"49"はGS1 Japanが管理する日本の正規事業者コード範囲であり、"20"〜"29"・"99"は
GS1が店舗外で流通しない「インストアマーキング」用の疑似コードとして予約している範囲
にあたる(参考: GS1 Japan公表のJAN企業コード割当表)。この対応関係に基づき、
`_classify_product_code()`で先頭2桁が"45"/"49"の場合のみ`identifiers["jan"]`として
供給し、それ以外("99"始まり等)は`extra["product_code_type"] = "internal_code"`として
値自体は保持しつつ、JAN一致スコアリング(technical分析レポート11.2、JAN一致+100)の
対象からは外すようにした。
**信頼度についての注意**: 上記の判定はサンプル5件から観測した経験則であり、GS1の
公式文書そのもので裏取りしたものではない。高確率ではあるが100%の保証ではないため、
今後別の先頭2桁パターン(例: 通常販売なのに"45"/"49"以外)が実データで見つかった場合は
本判定ロジックの見直しが必要になる(要継続検証)。
"""

import asyncio
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from app.collectors.base import (
    FetchError,
    NormalizedItem,
    ParseError,
    ParsedItem,
    RawFetchResult,
    SourceCollector,
)
from app.core.time import JST
from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType

PRODUCT_URL_TEMPLATE = "https://www.pokemoncenter-online.com/{code}.html"
PRODUCT_URL_PATTERN = re.compile(r"https://www\.pokemoncenter-online\.com/(?P<code>\d{13})\.html")

# モジュールdocstring「新商品一覧ページの発見・段階1対応(2026-08-06)」参照。
# start=0&sz=500を固定で付与し、1リクエストで新商品全件(検証時点275件)を取得する。
NEW_PRODUCT_LIST_URL = (
    "https://www.pokemoncenter-online.com/search/"
    "?prefn1=releaseType&prefv1=1&srule=top-new-product&start=0&sz=500"
)
# 一覧ページ内の個別商品リンク(相対パス「/{13桁コード}.html」)。
LISTING_PRODUCT_LINK_PATTERN = re.compile(r"^/(?P<code>\d{13})\.html")

# モジュールdocstring「商品コード(13桁)とJANコードの関係」参照。GS1 Japanが管理する
# 日本の正規事業者コード範囲("45"/"49")の先頭2桁のみJANコードとして扱う。
# サンプル5件からの経験則であり、GS1公式文書での裏取りではないため高確率だが
# 100%の保証ではない(要継続検証)。
JAN_ELIGIBLE_PREFIXES = ("45", "49")


def _classify_product_code(product_code: str | None) -> str | None:
    """商品コードがJANコードとして扱えるかを先頭2桁で判定する。

    "45"/"49"始まり(GS1 Japan管理の正規事業者コード範囲)は"jan"、
    それ以外("99"始まり等、GS1がインストアマーキング用に予約している疑似コード範囲)は
    "internal_code"を返す。後者はJAN一致スコアリング(technical分析レポート11.2)の
    対象にしない(=identifiersへは供給しない)ための印であり、値自体はextraに
    保持したままにする(モジュールdocstring参照。サンプル5件からの経験則、要継続検証)。
    """
    if product_code is None:
        return None
    return "jan" if product_code[:2] in JAN_ELIGIBLE_PREFIXES else "internal_code"

# Fixture注記: 「5,800円 税込」のような表記。実データでは金額と「円」「税込」が
# 別要素に分かれ、フラット化すると間に改行が入るため、\sで改行も許容する
# (本文全体に対してsearchする。行単位のマッチはしない)。
PRICE_PATTERN = re.compile(r"(?P<amount>[\d,]+)\s*円\s*税込")
# Fixture注記: 「お一人様 1点限り」のような表記。
PURCHASE_LIMIT_COUNT_PATTERN = re.compile(r"お一人様\s*(?P<count>\d+)点限り")

# Fixture注記3: 品切れ/予約等のステータスラベル。この1件のFixtureだけでは
# バリエーションを網羅できていないため、要検証。
INVENTORY_STATUS_KEYWORDS = ["品切れ", "予約", "販売中"]

# Fixture注記1: 「各種期間」内の各項目はラベル行(「・」始まり)の直後に日時が続く形式。
APPLY_PERIOD_LABEL = "・抽選応募受け付け期間"
ANNOUNCE_DATE_LABEL = "・抽選結果発表日"
PURCHASE_PERIOD_LABEL = "・購入および、支払い期間"
DELIVERY_TIMING_LABEL = "・商品のお届け時期"

# 区切り文字は全角「～」での確認のみだが、半角「~」「〜」も念のため許容する
# (Fixture注記1: 「実際の区切り文字は本番HTML取得時に要再確認」)。
_TILDE = r"[~〜～]"
# Fixture注記1のサンプル正規表現は「時:分」のコロン区切りを想定していたが、
# 実際のFixture本文は「16時00分」のような漢字区切りであり、コロン表記は出現しない
# (サンプル正規表現とFixture本文の不一致。本文の実測値を優先する)。
PERIOD_RANGE_PATTERN = re.compile(
    r"(?P<sy>\d{4})年(?P<smo>\d{1,2})月(?P<sd>\d{1,2})日.*?(?P<sh>\d{1,2})時(?P<smi>\d{2})分"
    rf"{_TILDE}"
    r"(?P<ey>\d{4})年(?P<emo>\d{1,2})月(?P<ed>\d{1,2})日.*?(?P<eh>\d{1,2})時(?P<emi>\d{2})分"
)
# 抽選結果発表日は単一時刻+「以降」(起点であり終了時刻ではない、Fixture注記1)。
SINGLE_DATETIME_PATTERN = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日.*?(?P<h>\d{1,2})時(?P<mi>\d{2})分"
)


def _value_after_label(lines: list[str], label: str) -> str | None:
    try:
        idx = lines.index(label)
    except ValueError:
        return None
    return lines[idx + 1] if idx + 1 < len(lines) else None


def _datetime_from_match(match: re.Match, prefix: str = "") -> datetime:
    return datetime(
        int(match[f"{prefix}y"]),
        int(match[f"{prefix}mo"]),
        int(match[f"{prefix}d"]),
        int(match[f"{prefix}h"]),
        int(match[f"{prefix}mi"]),
        tzinfo=JST,
    )


class PokemonCenterOnlineCollector(SourceCollector):
    """ポケモンセンターオンライン商品詳細ページCollector。

    商品コードが判明している個別ページはfetch_product()で都度取得する
    (従来からの運用)。加えて、新商品一覧ページ(NEW_PRODUCT_LIST_URL、モジュール
    docstring「新商品一覧ページの発見・段階1対応」参照)から商品コードを自動発見する
    discover_new_products()を段階1として追加した。

    【target_urls/parse()とdiscover_new_products()の役割分担(重要)】
    基底クラスSourceCollector.run()は「1URL=1回のfetch→そのraw HTMLをparse()に
    渡せばParsedItemが得られる」という単層の設計(1kuji.com/on-line.1kuji.com/
    suruga-ya.jpはこれで成立する)だが、ポケセンの新商品一覧ページには「各種期間」
    (締切・当選発表・購入期限、このCollectorの存在意義そのもの)が含まれておらず、
    一覧の各商品ごとに個別ページへの追加fetchが必要になる(2段階)。そのため
    target_urlsにはNEW_PRODUCT_LIST_URLを含める(fetch()のホワイトリスト判定・
    「この一覧ページも扱う」という宣言のため)が、parse()はこのURLに対しては
    意図的に空リストを返す(run()を誤って使うと「各種期間」を含まない不完全な
    ParsedItemが生成されてしまうのを避けるため)。一覧ページからの実際の商品発見は
    discover_new_products()を呼ぶこと。タスク20(app/scheduler/tasks.py)が
    collector.run()に頼らず独自ループでingest_normalized_item()へ結線したのと
    同じ考え方(段階2でBeat Schedule結線する際も同様の専用ループを想定)。
    """

    source_name = "pokemon_center_online"
    supported_event_types = [SupportedEventType.LOTTERY, SupportedEventType.NORMAL_SALE]
    target_urls: list[str] = [NEW_PRODUCT_LIST_URL]
    # 要検証: 巡回頻度を裏付ける実データ根拠がないため、一番くじCollectorと同程度の暫定値とする。
    default_interval_seconds = 3 * 60 * 60

    async def fetch(self, target_url: str) -> RawFetchResult:
        # ホワイトリスト形式: /search/配下はNEW_PRODUCT_LIST_URLの完全一致のみ許可する
        # (on-line.1kuji.comと同じ方針、app/collectors/sources/ichiban_kuji.py参照)。
        # 個別商品ページ(/{13桁コード}.html)はfetch_product()が組み立てる既存の
        # 許可対象のため、この判定の対象外(従来通り無制限)。
        parsed_path = target_url.split("pokemoncenter-online.com", 1)[-1]
        if parsed_path.startswith("/search/") and target_url != NEW_PRODUCT_LIST_URL:
            raise FetchError(
                "pokemoncenter-online.comの/search/配下への自動アクセスは方針により禁止されています"
                f"(許可されているのは{NEW_PRODUCT_LIST_URL}のみ)"
            )

        try:
            # follow_redirects=True: app/collectors/markets/suruga_ya.pyと同じ理由
            # (2026-08-05、Collector稼働状況CLI導入時にsuruga-ya.jpの301で発覚)。
            # httpxはデフォルトでリダイレクトを追わないため統一して有効化する。
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(target_url)
        except httpx.HTTPError as exc:
            raise FetchError(f"{target_url} の取得に失敗しました: {exc}") from exc

        if response.status_code != 200:
            raise FetchError(f"{target_url} が{response.status_code}を返しました")

        return RawFetchResult(
            url=target_url,
            status_code=response.status_code,
            html=response.text,
            fetched_at=datetime.now(tz=JST),
        )

    def parse(self, raw: RawFetchResult) -> list[ParsedItem]:
        if not raw.html:
            raise ParseError(f"{raw.url}: html本文が空です")

        if raw.url == NEW_PRODUCT_LIST_URL:
            # クラスdocstring参照: 一覧ページには「各種期間」が無く、単層のrun()経由では
            # 不完全なParsedItemしか作れないため、意図的に空を返す
            # (discover_new_products()を使うこと)。
            return []

        return [self._parse_product_detail(raw)]

    def extract_new_product_codes(self, raw: RawFetchResult) -> list[str]:
        """新商品一覧ページ(NEW_PRODUCT_LIST_URL)のraw HTMLから、個別商品ページへの
        リンク(相対パス「/{13桁コード}.html」)を商品コードとして抽出する
        (出現順、重複除去済み)。"""
        if not raw.html:
            raise ParseError(f"{raw.url}: html本文が空です")

        tree = HTMLParser(raw.html)
        codes: list[str] = []
        seen: set[str] = set()
        for anchor in tree.css("a"):
            href = anchor.attributes.get("href") or ""
            absolute = urljoin(raw.url, href)
            match = PRODUCT_URL_PATTERN.match(absolute) or LISTING_PRODUCT_LINK_PATTERN.match(href)
            if match is None:
                continue
            code = match["code"]
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)

        if not codes:
            raise ParseError(f"{raw.url}: 商品コードを1件も抽出できませんでした(構造変更の可能性)")
        return codes

    async def discover_new_products(self) -> tuple[list[NormalizedItem], list[str]]:
        """新商品一覧ページから商品コードを収集し、各商品の個別ページを
        fetch_product()(既存の実装、fetch→parse→normalize)で取得・正規化する
        (段階1で追加。段階2(2026-08-06)でapp/scheduler/tasks.pyから呼ばれる
        正式なBeat Schedule結線先になった)。

        個別ページの取得は商品ごとに独立して失敗しうる(275件規模の巡回のため、
        1件のFetchError/ParseErrorで全体を失敗させたくない。一番くじCollectorの
        target_urlsループ(app/scheduler/tasks.py:run_ichiban_kuji_collector)と
        同じ考え方)。失敗したコードは(code, エラーメッセージ)としてerrorsに積み、
        残りのコードの処理を継続する。戻り値は(正常に取得できたNormalizedItemの
        リスト, エラーメッセージのリスト)。

        275件(2026-08-06時点)全件を毎回individual fetchするのはレート制限
        (rate_limit())込みで実行時間が長くなるため、将来的には「DB未登録の
        新規コードのみfetchする」等の差分化を検討する余地がある(現時点では未対応)。
        """
        listing_raw = await self.fetch(NEW_PRODUCT_LIST_URL)
        codes = self.extract_new_product_codes(listing_raw)

        items: list[NormalizedItem] = []
        errors: list[str] = []
        for index, code in enumerate(codes):
            if index > 0:
                await asyncio.sleep(self.rate_limit())
            try:
                items.append(await self.fetch_product(code))
            except (FetchError, ParseError) as exc:
                errors.append(f"{code}: {exc}")
        return items, errors

    def _parse_product_detail(self, raw: RawFetchResult) -> ParsedItem:
        tree = HTMLParser(raw.html)
        text = tree.body.text(deep=True, separator="\n") if tree.body else raw.html
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        title_node = tree.css_first("h1")
        title = title_node.text(strip=True) if title_node is not None else ""
        if not title:
            raise ParseError(f"{raw.url}: 商品タイトル(h1)を抽出できませんでした(構造変更の可能性)")

        price: Decimal | None = None
        price_match = PRICE_PATTERN.search(text)
        if price_match:
            try:
                price = Decimal(price_match["amount"].replace(",", ""))
            except InvalidOperation:
                price = None

        # 実データ確認済み(raw_pokemon_center.html): 在庫状況ラベルは商品名の直後、
        # 価格より前に出現する。「予約」は「各種期間」より前の別の文脈
        # (「予約 / 販売期間 ...」)でも再度出現するが、最初に一致した行を
        # 採用することで在庫状況ラベルの方を正しく拾える。
        inventory_status: str | None = None
        for line in lines:
            if line in INVENTORY_STATUS_KEYWORDS:
                inventory_status = line
                break

        purchase_limit_count: int | None = None
        limit_match = PURCHASE_LIMIT_COUNT_PATTERN.search(text)
        if limit_match:
            purchase_limit_count = int(limit_match["count"])

        deadline_at: datetime | None = None
        application_start_at: datetime | None = None
        apply_period_text = _value_after_label(lines, APPLY_PERIOD_LABEL)
        if apply_period_text is not None:
            range_match = PERIOD_RANGE_PATTERN.search(apply_period_text)
            if range_match:
                application_start_at = _datetime_from_match(range_match, "s")
                deadline_at = _datetime_from_match(range_match, "e")

        announce_at: datetime | None = None
        announce_text = _value_after_label(lines, ANNOUNCE_DATE_LABEL)
        if announce_text is not None:
            announce_match = SINGLE_DATETIME_PATTERN.search(announce_text)
            if announce_match:
                # Fixture注記1: 「以降」表記のため単一時刻は起点。終了時刻ではない。
                announce_at = _datetime_from_match(announce_match)

        purchase_limit_at: datetime | None = None
        purchase_start_at: datetime | None = None
        purchase_period_text = _value_after_label(lines, PURCHASE_PERIOD_LABEL)
        if purchase_period_text is not None:
            range_match = PERIOD_RANGE_PATTERN.search(purchase_period_text)
            if range_match:
                purchase_start_at = _datetime_from_match(range_match, "s")
                purchase_limit_at = _datetime_from_match(range_match, "e")

        delivery_timing_text = _value_after_label(lines, DELIVERY_TIMING_LABEL)

        # Fixture注記2: 「各種期間」の抽選応募受け付け期間ブロックの有無で
        # event_type=lotteryかどうかを一次判定する(通常販売ページの実データ未確認のため要検証)。
        event_type = SupportedEventType.LOTTERY if apply_period_text is not None else SupportedEventType.NORMAL_SALE

        code_match = PRODUCT_URL_PATTERN.match(raw.url)
        product_code = code_match["code"] if code_match else None

        image_urls: list[str] = []
        first_img = tree.css_first("img")
        if first_img is not None:
            src = first_img.attributes.get("src")
            if src:
                image_urls.append(src)  # 実データ未確認のため暫定実装、要検証

        extra: dict = {
            "product_code": product_code,
            # "jan"(GS1正規コード範囲)/"internal_code"(インストア専用疑似コード範囲)/
            # None(商品コード自体が取れなかった)。モジュールdocstring参照。
            "product_code_type": _classify_product_code(product_code),
            "inventory_status": inventory_status,
            "purchase_limit_count": purchase_limit_count,
            "application_start_at": application_start_at,
            "purchase_start_at": purchase_start_at,
            # Fixture注記5: ページ上部の「お届け予定日」欄と表記ゆれがあるため、
            # 「各種期間」内の「商品のお届け時期」の生テキストをそのまま保持する。
            "delivery_timing_text": delivery_timing_text,
        }

        return ParsedItem(
            raw_title=title,
            price=price,
            image_urls=image_urls,
            event_type=event_type,
            start_at=application_start_at,
            deadline_at=deadline_at,
            announce_at=announce_at,
            purchase_limit_at=purchase_limit_at,
            apply_url=raw.url,
            product_url=raw.url,
            shop_name=None,
            extra=extra,
        )

    def normalize(self, items: list[ParsedItem]) -> list[NormalizedItem]:
        normalized: list[NormalizedItem] = []
        for item in items:
            # 締切・当選発表・購入期限まで確認できるlotteryは信頼度A、
            # それ以外(各種期間セクションが無い=通常販売と推定)はichiban_kujiと同程度のB。
            confidence_hint = "A" if item.event_type == SupportedEventType.LOTTERY else "B"

            # モジュールdocstring「商品コード(13桁)とJANコードの関係」参照。
            # "45"/"49"始まりのみJANコードとして供給し、"internal_code"判定のものは
            # JAN一致スコアリングの対象外にする(identifiersへ含めない)。
            identifiers: dict[str, str] = {}
            product_code = item.extra.get("product_code")
            if item.extra.get("product_code_type") == "jan" and product_code:
                identifiers["jan"] = product_code

            normalized.append(
                NormalizedItem(
                    product_name=item.raw_title,
                    identifiers=identifiers,
                    category_hint="ポケモンカードゲーム",
                    brand_hint="ポケモン",
                    parsed=item,
                    confidence_hint=confidence_hint,
                    fulfillment_type=FulfillmentType.ONLINE_SHIPPING,
                    region_override=None,
                    region_display_text=None,
                    # 通販サイトであり店舗情報を持たないため常にunknown(=全国扱い)
                    region_source=RegionSource.UNKNOWN,
                )
            )
        return normalized

    async def fetch_product(self, product_code: str) -> NormalizedItem:
        """商品コードを指定して個別ページを取得・パース・正規化する。

        商品コードが外部で判明している場合の個別呼び出しに加え、
        discover_new_products()(クラスdocstring参照)からも一覧ページ発見分の
        各コードに対して呼ばれる。
        """
        url = PRODUCT_URL_TEMPLATE.format(code=product_code)
        raw = await self.fetch(url)
        parsed_items = self.parse(raw)
        return self.normalize(parsed_items)[0]
