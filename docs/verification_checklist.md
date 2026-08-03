# Collector実データ検証チェックリスト

Phase 0で実装した一番くじ・駿河屋・ポケモンセンターオンラインの3 Collectorは、
いずれもFixture(Markdown変換済みテキスト)をもとにしたテキストパターンベースの
`parse()`実装であり、**実サイトの生HTML構造に対しては未検証**(各Collectorのモジュール
docstring参照)。本チェックリストは、本番のConoHa VPS環境で`scripts/verify_collectors.py`
を使って実データ検証を行う際に確認すべき項目をまとめたもの。

## 検証状況(2026-08-03 ラウンド1・タスク17)

ユーザーが本番ConoHa VPS上で実際に取得した生HTML3件(`tests/fixtures/raw_html/`配下)を
もとに、以下の項目は確認・修正済み(詳細はCLAUDE.md 1.5節、各Collectorのモジュール
docstring参照)。以下のチェックリストのうち、このラウンドで確認済みの項目には
`[x]`を付け、末尾に「(ラウンド1で確認済み)」と注記している。未確認のまま残った項目
(bandaispirits.co.jp詳細ページ、駿河屋の鑑定品価格、ポケセンの通常販売ページ構造)は
`[ ]`のままなので、次回以降の検証で優先的に確認すること。

## 事前準備

- [ ] `.env`に本番用の`DATABASE_URL`/`REDIS_URL`等が設定済みであること(docker-composeで
      起動する場合は`.env.example`のままで良いが、念のため`docker compose config`で
      展開結果を確認しておく)
- [ ] VPSから対象サイト(1kuji.com、bandaispirits.co.jp、suruga-ya.jp、
      pokemoncenter-online.com)への疎通があること(`curl -I <URL>`等で事前確認)
- [ ] `scripts/verify_collectors.py`をVPS上のコンテナ内(またはvenv環境)で実行できること
      (`docker compose run --rm api python scripts/verify_collectors.py --all ...`
      あるいはvenv直接実行)

## 実行例

```bash
# 一番くじ + 駿河屋 + on-line.1kuji.comブロック確認をまとめて実行
python scripts/verify_collectors.py --all --suruga-ya-query "一番くじ" \
    --pokemon-center-code <実在する商品コード> > verify_result.json

# bandaispirits.co.jpの商品詳細は、1kuji.comの結果を見てprd_idを手動指定
python scripts/verify_collectors.py --bandaispirits-prd-id <prd_id> >> verify_result.json
```

`verify_result.json`(検証結果のJSON)と、標準エラー出力のログ(OK/NG一覧)の両方を
保存しておくこと。以下のチェック項目はこの2つを見ながら確認する。

---

## 1. 一番くじ(1kuji.com / bandaispirits.co.jp)

- [x] `--ichiban-kuji`が`ok: true`で完了し、`items`が1件以上返っていること
      (ラウンド1で確認済み: `raw_1kuji_top.html`でPICK UP ITEM 15件を正しく抽出。
      修正前は「PICK UP ITEMを1件も抽出できませんでした」で失敗していた)
- [x] `items`内の各`raw_title`が実際の商品名として妥当なテキストになっていること
      (ラウンド1で確認済み: 商品名は`<p class="itemName">`に独立して格納されており、
      日時テキストの除去は不要と判明。詳細はCLAUDE.md 1.5節)
- [x] `extra.store_release_at`/`extra.online_release_at`が実際のページの表記と一致すること
      (ラウンド1で確認済み)
- [x] `extra.fulfillment_type`(STORE_PICKUP/ONLINE_SHIPPING/BOTH)が実際の販売形態と
      一致すること(ラウンド1で確認済み。店頭のみ/オンラインのみ/両方の3パターンとも
      実データで確認)
- [ ] **`--assert-online-1kuji-blocked`が`ok: true`であること。**
      これはコード上のガード(`fetch()`冒頭での`FetchError`即時送出)を確認するものであり
      ネットワークアクセス自体は発生しない。念のため、VPS側のアクセスログ・
      プロキシログ等で`on-line.1kuji.com`への外向きリクエストが実際に0件であることも
      別途確認すること(コードのガードが将来リファクタで壊れていないかの二重チェック)
      ※ラウンド1ではraw HTMLの目視確認のみ実施、VPS側での`--assert-`実行と
      アクセスログの確認はまだ行っていない
- [ ] `--bandaispirits-prd-id`を指定した検証で、価格(`price`)と発売日(`start_at`)が
      実際の商品詳細ページの表記と一致すること
      ※ラウンド1ではbandaispirits.co.jpの生HTMLは提供されておらず未確認のまま
      (CLAUDE.md 1.5節・3節参照)
- [ ] Fixture注記どおり無料配布商品(価格が「1回◯◯円」形式でない)がある場合、
      `price`が`null`のまま`extra.price_raw_text`に生テキストが残っていること
      (0円や適当な数値で埋められていないこと)※bandaispirits.co.jp側、未確認
- [ ] 期間表記(「MM月DD日〜MM月DD日」等)の商品がある場合、`start_at`が期間の開始日に
      なっており、`extra.release_date_raw_text`に生テキストが残っていること
      ※bandaispirits.co.jp側、未確認

## 2. 駿河屋(suruga-ya.jp)

- [x] `--suruga-ya`が`ok: true`で完了し、`observations`が1件以上返っていること
      (ラウンド1で確認済み: `raw_suruga_ya_search.html`で20件を正しく抽出。
      修正前は`TypeError`で完全に失敗していた。CLAUDE.md 1.5節参照)
- [x] **JANコード抽出**: `observations[].extra.jan`が、13桁の数値を持つ商品で正しく
      抽出できていること(`suruga_ya_jan_confirmation_20260803.md`で確認した
      「発売日/型番/JANコード/管理番号」列のパターンが実際のページでも機能するか)
      (ラウンド1で確認済み。JANと管理番号が別行(`<br>`区切り)になっていても
      `JAN_AND_ID_PATTERN`の`\s+`が改行をまたいで正しくマッチすることを確認)
- [x] JANコードが無い商品(トレカ系等)で`extra.jan`が`null`のままであり、
      誤った値が入っていないこと(ラウンド1で確認済み)
- [x] **管理番号**: `extra.management_number`が`GU`+数字、数字のみの両方の形式で
      正しく抽出できていること(型に文字列として保持されていること、`int`に
      変換されていないこと)(ラウンド1で確認済み)
- [ ] **鑑定品価格**: 「PSA/GEM MT 10」等の鑑定品価格が併記されている商品で、
      `amount`が無鑑定品の価格になっており、`extra.graded_price`に鑑定品側の
      情報が別途入っていること(無鑑定品と鑑定品の価格を取り違えていないか)
      ※ラウンド1で取得した実データには該当商品が含まれておらず未確認のまま
      (CLAUDE.md 1.5節・3節参照)
- [x] **`[価格上昇中]`タグ**: 実際に該当タグが付いている商品で`extra.trend`が
      `"price_rising"`になっていること。付いていない商品には`extra.trend`
      キー自体が存在しないこと(ラウンド1で確認済み。実際は`[<font color="...">
      <strong>価格上昇中</strong></font>]`のようにfont/strongタグで装飾されているが、
      フラット化後の文字列一致で問題なく検出できた)
- [x] **「メールにてお見積」**: 該当商品で`amount`が`null`、`confidence`が`"D"`、
      `extra.quote_required`が`true`になっており、0円等のダミー値になっていないこと
      (ラウンド1で確認済み)
- [x] 検索結果テーブルの行構造(1行=1商品)が、Fixtureで想定した`<tr>`単位の構造と
      実際のDOMで一致しているか(`observation_count`が明らかに実際の検索結果件数と
      ズレていないか目視で確認)(ラウンド1で確認済み。`<table><tr>`構造の推測自体は
      正しかったが、詳細リンクが相対パスだった点とhref=Noneのアンカー混在が
      原因でエラーになっていた。CLAUDE.md 1.5節参照)

## 3. ポケモンセンターオンライン(pokemoncenter-online.com)

- [x] `--pokemon-center-code`が`ok: true`で完了し、`items`が1件返っていること
      (ラウンド1で確認済み: `raw_pokemon_center.html`で正しく1件抽出。ただし価格は
      修正前は`null`になっていた。CLAUDE.md 1.5節参照)
- [x] **「各種期間」の日時抽出**: 抽選販売商品のページで以下4項目が実際の表記と
      一致すること(ラウンド1で確認済み。ラベル+値のテキストパターンマッチは
      修正なしでそのまま機能した)
      - `start_at`(抽選応募受け付け期間の開始)
      - `deadline_at`(抽選応募受け付け期間の終了)
      - `announce_at`(抽選結果発表日。「以降」表記の起点であることに注意)
      - `purchase_limit_at`(購入および支払い期間の終了)
      - `extra.delivery_timing_text`(商品のお届け時期、自由記述)
- [x] **時刻表記の形式**: 実際のページの時刻表記が「16時00分」のような漢字区切りか、
      それとも「16:00」のようなコロン区切りかを確認すること。
      → **ラウンド1で確認済み: 実データも「16時00分」の漢字区切りだった**
      (実装済みのPERIOD_RANGE_PATTERN/SINGLE_DATETIME_PATTERNのまま、修正不要)
- [x] 区切り文字(全角「～」か半角「~」か波ダッシュ「〜」か)が実際の期間表記と
      一致していること(ラウンド1で確認済み: 実データも全角「～」だった)
- [ ] **通常販売ページでのevent_type判定**: 「各種期間」セクションが存在しない
      通常販売・予約販売の商品ページで`event_type`が`normal_sale`になっており、
      `deadline_at`/`announce_at`/`purchase_limit_at`がすべて`null`になっていること
      (本実装は「各種期間セクションの有無」のみで判定しているため、通常販売ページの
      実際の構造によっては誤判定の可能性がある。誤判定していた場合は判定ロジックの
      見直しが必要)※ラウンド1で提供された生HTMLは抽選販売ページ1件のみのため、
      通常販売ページでの実データ確認はまだできていない(CLAUDE.md 3節参照)
- [x] `extra.inventory_status`(品切れ/予約/販売中等)が商品名直下のラベルを正しく
      拾えているか(「各種期間」より前に出てくる別の"予約"という語と混同していないか)
      (ラウンド1で確認済み。実データでも同様に"予約"の語が2箇所に出現するが、
      最初に一致した行(品切れ)を採用する実装に変更して正しく区別できることを確認)
- [x] `extra.product_code`がURLから正しく抽出できていること(ラウンド1で確認済み)
- [ ] 商品コードが実際のJANコードと一致するかどうかを、商品パッケージまたは
      他の情報源と突き合わせて確認すること(CLAUDE.md 3節の未確認事項。ラウンド1では未確認)
- [x] **(ラウンド1で新規発見・修正済み)価格抽出**: `price`が実際のページの金額と
      一致すること。ラウンド1では、金額の数字("5,800")と「円」「税込」が別要素
      (入れ子の`<small>`)に分かれておりフラット化すると別行になるため、行単位で
      検索していた従来のPRICE_PATTERNでは一致せず`price=null`になっていた
      (このチェック項目自体は元のチェックリストに無かった見落としであり、
      ラウンド1の検証で偶然発見された)。本文全体に対して空白(改行含む)を許容する
      形でマッチするよう修正済み。

## 4. 3サイト共通: Fixture取得時からの仕様変更の有無

- [ ] 各サイトのHTTPステータスコード(`status_code`)が200であること
      (リダイレクト・エラーページに変わっていないか)
- [ ] `verify_result.json`の`items`/`observations`の内容を目視し、Fixture作成時
      (2026-08-03時点)と比べて明らかに構造が変わっていないか
      (ラベル文言の変更、セクションの追加・削除、日時フォーマットの変更等)
- [ ] 想定外の構造変化があった場合は`ParseError`が正しく送出されているか
      (サイレントに空リストや欠損データを返していないか)
- [ ] プレミアムバンダイ(p-bandai.jp)は本チェックリストの対象外(タスク5は保留中、
      CLAUDE.md 1.2節参照)

## 5. 検証後の記録

- [ ] 上記チェックの結果(OK/NG、修正が必要だった項目)をCLAUDE.md
      「1. 実データ検証済みの事実」および「3. 未確認のまま残っている項目」に反映する
- [ ] 修正が必要と判明したパターン(正規表現・ラベル文言等)がある場合は、
      該当Collectorのモジュールdocstring・コメントの「検証状況に関する重要な注意」を
      「本番環境で実データ検証済み」に更新し、修正内容をコミットメッセージに明記する
- [ ] `verify_result.json`は本番の実データそのものを含むため、そのままリポジトリに
      コミットしない(個人情報は含まれない想定だが、念のため`.gitignore`対象とする、
      または検証メモのみを転記してファイル自体は破棄する)
