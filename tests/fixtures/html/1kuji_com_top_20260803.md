# Fixture: 1kuji.com トップページ
# 取得日時: 2026-08-03 (このドキュメント作成時点)
# 取得方法: web_fetch (html_extraction_method=markdown、生HTMLではなくMarkdown変換済み)
# 用途: PICK UP ITEMセクションのテキストパターン抽出ロジック(STORE_PATTERN/ONLINE_PATTERN)の
#       動作確認用データ。CSSセレクタの検証には使えないので注意。

## PICK UP ITEM セクション (実際に取得できた形式)

[店頭販売2026年08月04日(火)より順次発売予定オンライン販売2026年08月04日(火)17:00より販売開始予定一番くじ CUTIE STREET くじを手にする戦いなのです！](https://1kuji.com/products/cutiestreet)

[店頭販売2026年08月01日(土)より順次発売予定オンライン販売2026年08月03日(月)17:00より販売開始予定一番くじ 日曜劇場「VIVANT」](https://1kuji.com/products/vivant)

[店頭販売2026年08月01日(土)より順次発売予定オンライン販売2026年08月03日(月)11:00より販売開始予定一番くじ 『ミニオンズ＆モンスターズ』](https://1kuji.com/products/minions11)

[店頭販売2026年07月31日(金)より順次発売予定オンライン販売2026年08月03日(月)15:00より販売開始予定一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪](https://1kuji.com/products/kingdom6)

[店頭販売2026年07月31日(金)より順次発売予定オンライン販売2026年07月31日(金)11:00より販売開始予定一番くじ mofusand ～ひんやりくつろぎ時間～](https://1kuji.com/products/mofusand7)

[店頭販売2026年07月30日(木)より順次発売予定オンライン販売2026年07月30日(木)13:00より販売開始予定一番くじ 鬼滅の刃 ～上弦の弐～](https://1kuji.com/products/kimetsu30)

[店頭販売2026年07月25日(土)より順次発売予定オンライン販売2026年08月18日(火)11:00より販売開始予定一番くじ たまごっち～ぐるぐる発見！Tamagotchi Paradise！～](https://1kuji.com/products/tamagotchi5)

[店頭販売2026年07月25日(土)より順次発売予定オンライン販売2026年07月27日(月)17:00より販売開始予定一番くじ ゴジラ 最恐怪獣王列伝](https://1kuji.com/products/godzilla9)

[店頭販売2026年07月25日(土)より順次発売予定オンライン販売2026年07月28日(火)17:00より販売開始予定一番くじ フィンセント・ファン・ゴッホ](https://1kuji.com/products/van-gogh)

[店頭販売2026年07月18日(土)より順次発売予定オンライン販売2026年07月21日(火)11:00より販売開始予定一番くじ 刃牙～巨大なる鼓動～](https://1kuji.com/products/baki3)

[店頭販売2026年07月17日(金)より順次発売予定オンライン販売2026年07月17日(金)11:00より販売開始予定一番くじ オーバーロード](https://1kuji.com/products/overlord)

[店頭販売2026年07月24日(金)より順次発売予定オンライン販売2026年07月15日(水)17:00より販売開始予定一番くじ ウルトラマン 60th Anniversary](https://1kuji.com/products/ultra23)

[オンライン販売2026年06月24日(水)17:00より販売開始予定一番くじ ぷちきゅあ](https://1kuji.com/products/petitcure)

## 観察されたパターンの補足事項（実装時の注意点）

1. リンクテキスト全体が `[店頭販売...オンライン販売...商品名](URL)` という1本の文字列になっている。
   つまり店頭販売文言・オンライン販売文言・商品名の間にスペースや改行の区切り文字が無い連結テキスト。
   parse()実装では、STORE_PATTERN/ONLINE_PATTERNで日時部分を抜き出した後、
   その残り(マッチ部分を除去したテキスト)を商品名として扱う設計にする必要がある。

2. 最後の「ぷちきゅあ」の例のように、店頭販売の記載が無く「オンライン販売」のみのケースが実在する。
   → fulfillment_type判定ロジックで online_shipping のみになるケースとして必ずテストケースに含めること。

3. 商品詳細URLは `https://1kuji.com/products/{slug}` のパターンで一貫している。
   slugの例: cutiestreet, vivant, minions11, kingdom6, mofusand7, kimetsu30,
             tamagotchi5, godzilla9, van-gogh, baki3, overlord, ultra23, petitcure

## ラインナップセクション（発売月別、価格情報なし・画像リンクのみ）

このセクションは画像+リンクのみで、テキスト情報が無いため今回のFixtureには価格等の抽出対象データが
含まれていない。商品一覧ページ (https://1kuji.com/products) や商品詳細ページ側で
価格情報を取得する実装が必要（本Fixtureには詳細ページのデータは含まれていないため、
価格抽出ロジックは別途プレースホルダー/TODOとして実装し、実サイトでの追加検証が必要）。
