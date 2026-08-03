# Collector実データ検証チェックリスト

Phase 0で実装した一番くじ・駿河屋・ポケモンセンターオンラインの3 Collectorは、
いずれもFixture(Markdown変換済みテキスト)をもとにしたテキストパターンベースの
`parse()`実装であり、**実サイトの生HTML構造に対しては未検証**(各Collectorのモジュール
docstring参照)。本チェックリストは、本番のConoHa VPS環境で`scripts/verify_collectors.py`
を使って実データ検証を行う際に確認すべき項目をまとめたもの。

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

- [ ] `--ichiban-kuji`が`ok: true`で完了し、`items`が1件以上返っていること
- [ ] `items`内の各`raw_title`が実際の商品名として妥当なテキストになっていること
      (日時部分の正規表現除去が正しく効いており、商品名に日時の残骸が混入していないか)
- [ ] `extra.store_release_at`/`extra.online_release_at`が実際のページの表記と一致すること
- [ ] `extra.fulfillment_type`(STORE_PICKUP/ONLINE_SHIPPING/BOTH)が実際の販売形態と
      一致すること
- [ ] **`--assert-online-1kuji-blocked`が`ok: true`であること。**
      これはコード上のガード(`fetch()`冒頭での`FetchError`即時送出)を確認するものであり
      ネットワークアクセス自体は発生しない。念のため、VPS側のアクセスログ・
      プロキシログ等で`on-line.1kuji.com`への外向きリクエストが実際に0件であることも
      別途確認すること(コードのガードが将来リファクタで壊れていないかの二重チェック)
- [ ] `--bandaispirits-prd-id`を指定した検証で、価格(`price`)と発売日(`start_at`)が
      実際の商品詳細ページの表記と一致すること
- [ ] Fixture注記どおり無料配布商品(価格が「1回◯◯円」形式でない)がある場合、
      `price`が`null`のまま`extra.price_raw_text`に生テキストが残っていること
      (0円や適当な数値で埋められていないこと)
- [ ] 期間表記(「MM月DD日〜MM月DD日」等)の商品がある場合、`start_at`が期間の開始日に
      なっており、`extra.release_date_raw_text`に生テキストが残っていること

## 2. 駿河屋(suruga-ya.jp)

- [ ] `--suruga-ya`が`ok: true`で完了し、`observations`が1件以上返っていること
- [ ] **JANコード抽出**: `observations[].extra.jan`が、13桁の数値を持つ商品で正しく
      抽出できていること(`suruga_ya_jan_confirmation_20260803.md`で確認した
      「発売日/型番/JANコード/管理番号」列のパターンが実際のページでも機能するか)
- [ ] JANコードが無い商品(トレカ系等)で`extra.jan`が`null`のままであり、
      誤った値が入っていないこと
- [ ] **管理番号**: `extra.management_number`が`GU`+数字、数字のみの両方の形式で
      正しく抽出できていること(型に文字列として保持されていること、`int`に
      変換されていないこと)
- [ ] **鑑定品価格**: 「PSA/GEM MT 10」等の鑑定品価格が併記されている商品で、
      `amount`が無鑑定品の価格になっており、`extra.graded_price`に鑑定品側の
      情報が別途入っていること(無鑑定品と鑑定品の価格を取り違えていないか)
- [ ] **`[価格上昇中]`タグ**: 実際に該当タグが付いている商品で`extra.trend`が
      `"price_rising"`になっていること。付いていない商品には`extra.trend`
      キー自体が存在しないこと
- [ ] **「メールにてお見積」**: 該当商品で`amount`が`null`、`confidence`が`"D"`、
      `extra.quote_required`が`true`になっており、0円等のダミー値になっていないこと
- [ ] 検索結果テーブルの行構造(1行=1商品)が、Fixtureで想定した`<tr>`単位の構造と
      実際のDOMで一致しているか(`observation_count`が明らかに実際の検索結果件数と
      ズレていないか目視で確認)

## 3. ポケモンセンターオンライン(pokemoncenter-online.com)

- [ ] `--pokemon-center-code`が`ok: true`で完了し、`items`が1件返っていること
- [ ] **「各種期間」の日時抽出**: 抽選販売商品のページで以下4項目が実際の表記と
      一致すること
      - `start_at`(抽選応募受け付け期間の開始)
      - `deadline_at`(抽選応募受け付け期間の終了)
      - `announce_at`(抽選結果発表日。「以降」表記の起点であることに注意)
      - `purchase_limit_at`(購入および支払い期間の終了)
      - `extra.delivery_timing_text`(商品のお届け時期、自由記述)
- [ ] **時刻表記の形式**: 実際のページの時刻表記が「16時00分」のような漢字区切りか、
      それとも「16:00」のようなコロン区切りかを確認すること。本実装はFixture本文の
      実測値(漢字区切り)に合わせているため、**もし実サイトがコロン区切りだった場合は
      `PERIOD_RANGE_PATTERN`/`SINGLE_DATETIME_PATTERN`(`app/collectors/sources/
      pokemon_center_online.py`)の修正が必要**(要修正候補として記録すること)
- [ ] 区切り文字(全角「～」か半角「~」か波ダッシュ「〜」か)が実際の期間表記と
      一致していること(Fixture注記1のとおり全角「～」のみ確認済み、他の区切りが
      出てきた場合はパターンに追加が必要)
- [ ] **通常販売ページでのevent_type判定**: 「各種期間」セクションが存在しない
      通常販売・予約販売の商品ページで`event_type`が`normal_sale`になっており、
      `deadline_at`/`announce_at`/`purchase_limit_at`がすべて`null`になっていること
      (本実装は「各種期間セクションの有無」のみで判定しているため、通常販売ページの
      実際の構造によっては誤判定の可能性がある。誤判定していた場合は判定ロジックの
      見直しが必要)
- [ ] `extra.inventory_status`(品切れ/予約/販売中等)が商品名直下のラベルを正しく
      拾えているか(「各種期間」より前に出てくる別の"予約"という語と混同していないか)
- [ ] `extra.product_code`がURLから正しく抽出できていること
- [ ] 商品コードが実際のJANコードと一致するかどうかを、商品パッケージまたは
      他の情報源と突き合わせて確認すること(CLAUDE.md 3節の未確認事項)

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
