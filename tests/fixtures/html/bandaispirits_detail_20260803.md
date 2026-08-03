# Fixture: bandaispirits.co.jp 商品詳細ページ
# URL: https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=ichibankuji&grp_id=9999
# 取得日時: 2026-08-03 (このドキュメント作成時点)
# 取得方法: web_fetch (html_extraction_method=markdown、生HTMLではなくMarkdown変換済み)
# 用途: 商品詳細ページの見出し構造(商品詳細/価格/発売日)のパターン確認用データ

## 実際に取得できたページ構造（該当部分のみ抜粋）

# PRODUCTS INFORMATION商品情報

![一番くじの一番くじ](https://www.bandaispirits.co.jp/images/uploads/product/image/10208/739e6ea0-5cb9-4cb5-b8d9-393030612276.jpg)

# 一番くじの一番くじ

## 商品詳細

価格

イベント会場でアンケートにご回答いただいた方に無料配布

発売日

2024年02月23日(金・祝)～02月24日(土)

- 発売日（予定）は地域・店舗などによって異なる場合がございますのでご了承ください。

## 「一番くじ」カテゴリの新着商品

[](https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=shigureui2&grp_id=9999)

![一番くじ しぐれうい 第2弾（仮）](https://assets.1kuji.com/uploads/product/image/10755/957019e6-89bf-4cfe-96f8-6c7e8ceb3181.webp)

[一番くじ](https://www.bandaispirits.co.jp/products/search/result.php?freeword=&category=4)

一番くじ しぐれうい 第2弾（仮）

1回790円(税10％込)

[](https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=natsume49&grp_id=9999)

![一番くじ 夏目友人帳 ぽかぽか冬のひととき](https://assets.1kuji.com/uploads/product/image/10775/cc79fc77-9235-455f-a3c3-562864758baf.webp)

[一番くじ](https://www.bandaispirits.co.jp/products/search/result.php?freeword=&category=4)

一番くじ 夏目友人帳 ぽかぽか冬のひととき

1回760円(税10％込)

## 観察されたパターンの補足事項（実装時の注意点）

1. 個別商品の詳細ページには「商品詳細」という見出しの下に「価格」「発売日」がラベル+値の順で
   並ぶ構造。ただしこの例（一番くじの一番くじ）は無料配布のイベント景品のため、
   価格が「1回◯◯円」という通常フォーマットになっていない特殊ケース。
   → 「1回◯◯円(税◯％込)」の正規表現マッチに失敗した場合、価格を無理にnullにせず
     「無料配布/その他」の特殊ケースとして扱うか、価格取得失敗としてログに残す設計にすること。

2. 「新着商品」セクションでは、商品名の直後に改行して
   `1回790円(税10％込)` のような価格文字列が続く、一貫したパターンが確認できる。
   → 通常商品の価格抽出は `r"1回([\d,]+)円\(税(\d+)％込\)"` のようなパターンで対応可能と推測される
     （このFixtureの範囲では2件のサンプルのみのため、実サイトでの追加サンプル確認を推奨）。

3. 商品詳細へのリンクは `detail.php?prd_id={id}&grp_id=9999` のクエリパラメータ形式。
   prd_idの例: ichibankuji, shigureui2, natsume49, kusuriya2, eva20
   一覧ページから新着商品のprd_idを収集し、詳細ページを個別に叩く運用が可能。

4. 発売日の記載は「2024年02月23日(金・祝)～02月24日(土)」のように期間表記のケースがある。
   → release_at系のフィールドは単一日付だけでなく期間(開始日・終了日)を持てる設計にするか、
     期間表記の場合は開始日のみを採用するかを決める必要がある（現状の実装仕様書では単一日時想定のため、
     このケースは要確認事項としてTODOに残すこと）。
