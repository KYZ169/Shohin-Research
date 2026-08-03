# Fixture: 駿河屋買取検索結果ページ（おもちゃ・ホビー カテゴリ）
# URL: https://www.suruga-ya.jp/kaitori/search_buy?category=501&search_word=一番くじ
# 取得日時: 2026-08-03 (このドキュメント作成時点)
# 取得方法: web_fetch (html_extraction_method=markdown、生HTMLではなくMarkdown変換済み)
# エンコーディング: UTF-8（1kuji.com/bandaispirits.co.jpと違い文字化けなし）
# 用途: buyback_prices取得ロジック(MarketCollector.parse_observations)の動作確認用データ

## 検索結果テーブル（実際に取得できた形式、抜粋）

| [全てを チェックする] | 商品画像 | 種類/タイトル | 発売日/型番/JANコード/管理番号 | 買取価格 | 詳細/売却カートへ |
|---|---|---|---|---|---|
| | (画像) | 遊戯王/UR/魔法/LIMIT OVER COLLECTION -THE HEROES- <br>[LOCH-JP003[UR]：黒魔導のカーテン](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031) | 2026/02/28 GU630031 | 400円 | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU630031) |
| | (画像) | ポケモンカードゲーム/SAR/水/MEGA 拡張パック ニンジャスピナー <br>[114/083[SAR]：(キラ)メガゲッコウガex](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU634065) | 2026/03/13 GU634065 | 30,000円 【PSA/GEM MT 10】： 60,000円 | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU634065) |
| | (画像) | デュエルマスターズ/SR/光/[DM26-RP1]逆札篇 第1弾 「逆転神VS切札竜」 [**価格上昇中**] <br>[S2/S11[SR]：世界のY チャクラ・デル・フィン](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU683685) | 2026/04/11 GU683685 | 1,800円 | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU683685) |
| | (画像) | 遊戯王/SR/効果モンスター/カオス・オリジンズ <br>[CORI-JP001[SR]：超魔剣士ブラック・カオス](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU699773) | 2026/04/25 GU699773 | メールにてお見積 （[詳しくはこちら](https://www.suruga-ya.jp/man/kaitori/ansin.html)） | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU699773) |
| | (画像) | ONE PIECEカードゲーム/SR/CHARACTER/決戦の刻【OP-16】 [**価格上昇中**] <br>[OP16-056[SR]：Mr.3(ギャルディーノ)](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU745837) | 2026/05/30 GU745837 | 1,500円 | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU745837) |
| | (画像) | ポケモンカードゲーム/AR/超/MEGA 拡張パック ムニキスゼロ <br>[086/080[AR]：(キラ)ピッピ](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU584307) | 2026/01/23 GU584307 | 600円 【PSA/GEM MT 10】： 9,000円 | [詳細](https://www.suruga-ya.jp/kaitori/kaitori_detail/GU584307) |

## 観察されたパターンの補足事項（実装時の注意点）

1. **「種類/タイトル」列は2行構造**: 1行目が「ブランド/レアリティ/属性・弾名」、2行目が
   「型番[レアリティ]：カード名（リンク付き、管理番号を含むURL）」。
   商品名として使うべきは2行目のリンクテキスト（`型番[レアリティ]：カード名`の部分）。
   1行目は`brand`/`series`のヒントとして別途保持するのが良い。

2. **買取価格が固定額でないケースが実在する**（`メールにてお見積`）。
   この場合、価格カラムには金額が入らず「メールにてお見積」というテキストと
   説明ページへのリンクが入る。
   → `parse_observations()`は価格を金額として抽出できない場合、
     `amount=None`とし、`MarketObservation.confidence`は`D`格下げ、
     またはこのレコード自体を「買取価格取得不可」として別扱いにすること。
     **絶対に0円やダミー値を入れてはいけない。**

3. **鑑定品（PSA/GEM MT 10等）の価格が併記されるケースがある**。
   例: `600円 【PSA/GEM MT 10】： 9,000円`
   通常の買取価格（無鑑定品）は最初の金額（この例では600円）。
   鑑定品の価格は付随情報として`extra["graded_price"]`のような形で保持し、
   `amount`には無鑑定品の価格のみを採用すること（実装仕様書のMVPスコープでは
   鑑定品別の扱いは本格対応しない方針のため）。

4. **`[価格上昇中]`というタグがタイトル行に付くことがある**。
   このタグの有無をパースして`extra["trend"] = "price_rising"`のように保持しておくと、
   Opportunity Scorerの「相場上昇」通知条件にそのまま使える。

5. **発売日/型番/管理番号の行は改行なしで連結されている**
   （例: `2026/02/28      GU630031`）。管理番号（`GU`で始まる英数字）は
   正規表現 `r"GU\d+"` で抽出可能。発売日は `r"\d{4}/\d{2}/\d{2}"` で抽出可能。

6. **検索URLの構成要素**（Collector実装で使用）:
   - ベースURL: `https://www.suruga-ya.jp/kaitori/search_buy`
   - `category`: カテゴリID（例: `501`=ホビー全体、`50108`=トレカ・カード類）
   - `search_word`: 検索キーワード（URLエンコード必須、日本語可）
   - 絞り込み: `restrict[]=purchase_hendou=価格上昇中`（価格上昇中のみ）、
     `restrict[]=brand=バンダイ`（メーカー指定）などをクエリに追加可能
   - ページング: `&page=2`のように追加

## 未検証の項目（実装時にTODOとして残すこと）

- 商品詳細ページ（`kaitori_detail/{管理番号}`）自体の構造は未取得。JANコードが
  この詳細ページ側にのみ記載されている可能性があるため、一覧ページだけでJANコードを
  取得できるとは限らない（一覧ページのカラム名に「JANコード」とあるが、
  今回のFixtureのサンプルには実際のJAN値が表示されていない）。
  → JANコード取得が必要な場合は詳細ページへの追加アクセスが必要かどうか、
    本番環境で改めて確認すること。
