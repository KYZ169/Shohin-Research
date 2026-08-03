# Fixture: 駿河屋買取検索結果ページ（一番くじ×雑貨・小物カテゴリ、JANコード確認用）
# URL: https://www.suruga-ya.jp/kaitori/search_buy?category=1027&search_word=&restrict[]=series=一番くじ
# 取得日時: 2026-08-03 (このドキュメント作成時点)
# 取得方法: web_fetch (html_extraction_method=markdown)
# 用途: JANコードが一覧ページの時点で取得可能かどうかの検証（suruga_ya_kaitori_search_20260803.mdの補足）

## 検索結果（該当1件）

| 商品画像 | 種類/タイトル | 発売日/型番/JANコード/管理番号 | 買取価格 | 詳細 |
|---|---|---|---|---|
| (画像, shinaban=608000898001) | 小物(キャラクター) <br>[一番くじ 西尾維新アニメプロジェクト](https://www.suruga-ya.jp/kaitori/kaitori_detail/608000898) | 4983164650440　608000898 | メールにてお見積 | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/608000898) |

## 観察されたパターンの補足事項（suruga_ya_kaitori_search_20260803.mdへの追記）

1. **JANコードは一覧ページの時点で取得可能なケースがある。** 別途詳細ページ(`kaitori_detail/{管理番号}`)への
   追加アクセスをしなくても、`発売日/型番/JANコード/管理番号`の列に13桁の数値としてJANコードが含まれる。
   ```
   4983164650440     608000898
   ```
   → 13桁の数字部分をJANコード、それに続く数字部分を管理番号として正規表現で分離できる:
   ```python
   import re
   JAN_AND_ID_PATTERN = re.compile(r"(\d{13})\s+(\S+)")
   ```

2. **ただし全商品にJANコードがあるわけではない。** `suruga_ya_kaitori_search_20260803.md`で確認した
   トレカ系の商品（`GU630031`等）ではJANコードの記載が見当たらなかった（発売日+管理番号のみ）。
   カテゴリ・商品種別によってJANコードの有無にばらつきがあるため、
   `product_identifiers`へのJANコード登録は「取れたら登録、取れなければ他の識別子で照合」という
   nullable前提の設計のままで問題ない（実装仕様書・技術分析レポートの既存方針と変更なし）。

3. **管理番号の形式が最低2種類確認された。**
   - `GU` + 数字（例: `GU630031`）… トレカ系カテゴリ(`category=50108`等)で確認
   - 数字のみ（例: `608000898`）… 雑貨・小物カテゴリ(`category=1027`)で確認
   → 管理番号を単純な数値型として扱わず、文字列として保持すること
     （`buyback_prices`または`market_observations`側の識別子カラムは`varchar`とする）。

4. **画像URLの`shinaban`パラメータも管理番号と一致する**（例: `shinaban=608000898001`、
   末尾に枝番と思われる`001`が付与されている）。画像URLからも管理番号を抽出できるため、
   テーブル本文のパースに失敗した場合のフォールバックとして使える。
