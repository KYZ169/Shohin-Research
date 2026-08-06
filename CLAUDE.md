# CLAUDE.md — 限定商品アービトラージ通知ツール 実装ブリーフィング

このファイルはClaude Codeが最初に読む前提のプロジェクトブリーフィングです。
詳細設計は同ディレクトリの以下2ファイルを参照してください。Claude Codeはコーディング開始前に必ずこの3ファイルを読むこと。

- `技術分析_限定商品アービトラージツール.md`（全体アーキテクチャ・DB設計・リスク分析）
- `実装仕様書_限定商品アービトラージツール.md`（確定した詳細仕様・Collector実装ガイド・開発用プロンプト集）
- 本ファイル（`CLAUDE.md`）: 実データ検証済みの事実、Phase0の具体的な着手タスク

---

## 0. プロジェクト概要（1分で読める要約）

抽選/予約/限定/再販の商品情報を自動収集し、買取店・フリマ等の相場と比較して利益候補をDiscordに通知する個人用ツール。技術スタックはPython + FastAPI + PostgreSQL + Redis + Celery + discord.py。個人開発・ConoHa VPS上で運用。モジュラーモノリス構成（マイクロサービス化はしない）。

**最重要方針（繰り返し確認済みなので厳守）**
1. **未確定コスト（送料・交通費等）は金額計算に一切含めない。** 確定コストのみで`displayed_profit`を計算し、「この額から◯◯を引いたものが純利益」という注記を必ず添える。減点・按分などの補正は行わない。
2. **機会損失より目視確認を優先する。** fulfillment_type不明、地域不明、信頼度C/D、締切不明など、あらゆる「不明」は非表示にせず、不明である旨を明示して表示・通知する。
3. **AIだけで商品統合を確定しない。** 商品照合はルールベースのスコアリングで行い、AIは属性抽出の補助に留める。
4. **Bot対策サイトは正面突破しない。** 取得できないサイトは「取得不可」として扱い、応募URLへの誘導とユーザーの目視確認で代替する。

---

## 0.5 自律判断の基準（質問すべきこと／自分で判断していいこと）

このプロジェクトは人間(ユーザー)とClaude Code、さらにその間をユーザーが仲介する構成のため、
**質問1回あたりのコストが高い**。すべての曖昧さを質問に回すと開発が進まないので、以下の基準で
「自分で判断して進めてよいもの」と「必ず止めて質問すべきもの」を分ける。

### 必ず止めて質問すべきこと（この2種類だけ）

1. **不可逆な操作、または既存データ・既存実装を破壊しうる判断**
   （例: マイグレーションのdown不可能な変更、既存Fixtureや既存テストの削除、
   本番相当のデータ操作）
2. **金額計算・信頼度モデル・商品照合の確定ロジックに関わる仕様変更**
   （例: 「未確定コストを金額計算に含めない」という原則を崩す変更、
   信頼度A〜Dの定義変更、商品照合の自動一致閾値の変更）
   → これらは技術分析レポート・実装仕様書での協議を経て確定した意思決定であり、
     Claude Codeの実装判断だけで変えてよい範囲を超えるため。

### 自分で判断して進めてよいこと（記録を残しつつ進める）

- タスクの粒度・分割方法（例: 今回のように「地域解決ロジック」と「商品照合スコアリング」の
  スコープが曖昧なとき、ドキュメント間の記述のズレをどちらか一方の解釈で進めること）
- 命名規則、ファイル配置、関数シグネチャの細部
- Fixtureが無い箇所の暫定実装（ただし「要検証」と明記し、根拠の弱さを隠さないこと）
- テストケースの網羅範囲の調整
- ドキュメント間で軽微な矛盾・表記ゆれを見つけた場合の解釈統一

**進め方**: 上記に該当する曖昧さに直面したら、質問で止めるのではなく、
①最も合理的な解釈を選ぶ → ②その解釈と理由をコミットメッセージまたは実装報告に明記する
→ ③実装を進める、という順で対応すること。今回のタスク7のようなスコープの揺れは、
本来この基準で自己解決してよかった事例。ただし報告時には必ず「こう解釈しました」を
一言添え、ユーザー側で後から軌道修正できるようにすること。

---

## 1. 実データ検証済みの事実（PoC実施結果）

以下は実際にfetchして確認済みの一次情報。技術分析レポートの机上評価より優先する。

### 1.1 一番くじ公式（3ドメイン構成）

| ドメイン | 状態 | 実装への反映 |
|---|---|---|
| `1kuji.com` | ✅ Bot対策なし、取得可能 | メインの収集対象。トップページの「PICK UP ITEM」に`店頭販売YYYY年MM月DD日より順次発売予定` / `オンライン販売YYYY年MM月DD日HH:MM より販売開始予定`が構造化されて併記されている。正規表現抽出パターンは実装仕様書9章に実コードあり |
| `bandaispirits.co.jp` | ✅ Bot対策なし、取得可能 | `1kuji.com`の補完・裏取り用。UTF-8、商品詳細ページ(`products/search/detail.php?prd_id=...`)が明確な見出し構造 |
| `on-line.1kuji.com` | ⚠️ ドメイン全体ではなくパス単位で状態が異なる(2026-08-06詳細確認) | 個別詳細・応募ページ(`ProductDetail.aspx`等)は❌Bot対策により直接fetch不可（"Site blocked the request"エラー確認済み）、**自動収集の対象外**のまま。一方、商品一覧ページ(`Form/Product/ProductList.aspx`)は✅Bot対策に阻まれず取得可能と2026-08-06に判明し、**この1URLのみ例外的にホワイトリスト方式で自動収集対象に追加した**(下記参照)。抽選締切・当選発表は個別詳細ページに集中している可能性が高くこちらは引き続き取得しない。`apply_url`のリンクのみ保存し、締切は「公式サイトでご確認ください」表示で代替（実装仕様書9章） |

**JAN/型番の有無（2026-08-05確認済み）**: `bandaispirits.co.jp`の商品詳細ページ(`products/search/detail.php?prd_id=...`)の「商品詳細」セクションにはJAN/型番は一切含まれていない（価格・発売日のみ）。あるのは`prd_id`/`grp_id`という内部識別子のみで、駿河屋等の外部検索に使える標準コードではない。したがって`app/collectors/sources/ichiban_kuji.py`のnormalize()の`identifiers={}`固定は妥当な実装であり変更不要、一番くじ発の商品は今後も商品名+発売日による照合に依存し続ける。**運用上の含意**: 技術分析レポート11章の商品照合スコアリングにおいて、一番くじ発の商品はJAN一致(+100)/型番一致(+80)の恩恵を受けられないことが確定したため、商品名類似度・発売日近似での照合精度が一番くじジャンルでは特に重要になる。

**【2026-08-06・重要な方針転換】on-line.1kuji.comの商品一覧ページ(`ProductList.aspx`)のみ例外的に自動収集対象へ追加、新商品自動発見に活用**: 従来「on-line.1kuji.comは自動収集の対象外」としていたのは、商品詳細・応募ページ(`ProductDetail.aspx`等)がBot対策でブロックされることを根拠にした判定だった。同じドメイン内の商品一覧ページ(`https://on-line.1kuji.com/Form/Product/ProductList.aspx`)を実際に取得した生HTML(`tests/fixtures/raw_html/raw_online_1kuji_productlist.html`)を確認したところ、Bot対策に阻まれず正常に取得でき、確認時点で販売中の全22商品のpid・商品名・価格・販売期間開始日時が構造化されて含まれていることを確認した。p-bandai.jp(1.2節、トップページは取得可・個別商品ページのみAkamai保護)と同様、同一ドメイン内でもパス単位で保護の有無が異なる非対称な構成であり、0節方針4「Bot対策サイトは正面突破しない」はブロックされていないページの利用まで妨げるものではない(ユーザー確認済み)。
- `app/collectors/sources/ichiban_kuji.py`の`fetch()`を、on-line.1kuji.com宛リクエストは`ONLINE_1KUJI_PRODUCT_LIST_URL`との完全一致(ホワイトリスト)のみ許可する形に変更した。クエリパラメータが付加されたバリエーションも含め、それ以外のon-line.1kuji.comパスは引き続き全面禁止。
- 取得するのはpid・商品名・価格・販売期間開始日時のみ。抽選締切・当選発表は引き続き取得できないため`deadline_at`は常にNone(`release_events.deadline_source`は既定値`DeadlineSource.UNKNOWN`が自動適用される、追加実装不要)。**一番くじONLINEは抽選応募制ではなく購入型のオンラインくじという性質のため、そもそも「締切」の概念が存在しない可能性があるが、この点は実データからは確認できていない(要継続検証)。**
- `apply_url`/`product_url`には`ProductDetail.aspx`への絶対URLを保存するのみで、本Collectorがこれをfetchすることは無い(bandaispirits.co.jp連携と同じ既存パターン)。
- `target_urls`に追加したため、Celery Beatの`ichiban-kuji-every-6-hours`スケジュール経由で6時間ごとに自動巡回される(1kuji.comトップページと同じ巡回間隔)。
- テスト13件追加(単体18件中)、実Fixture(22件)・合成HTML(2件、`pid`が数字のみ/`sap_`接頭辞付き両方の形式をカバー)の両方で検証済み。

### 1.2 プレミアムバンダイ（p-bandai.jp）【方針変更あり】

- `p-bandai.jp`のトップページ等は取得可能だが、**エンコーディングがShift_JIS(cp932)**。UTF-8決め打ちで実装すると文字化けするため、Collector実装では`response.encoding = "cp932"`（httpxの場合は`response.text`取得前に明示指定、またはBeautifulSoup/selectolaxのエンコーディング自動検出に頼らず明示指定すること）。
- **`https://p-bandai.jp/deadline_itemlist/`という「締切間近の商品」専用一覧ページの存在は確認したが、実際には`search.p-bandai.jp`と同様にJavaScriptで描画されるSPAであり、通常のfetchでは本文が空で返ることを確認した。** 検索エンジンのスニペット経由でなら「もうすぐ締切」「発送月」等の情報が見えるが、Collectorとして安定運用するには実質的にPlaywright等のJS実行環境が必要になる。技術分析レポート23章の「Playwrightは限定的併用のみ、多用しない」方針と衝突するため、**`deadline_itemlist`の自動収集は断念し、個別商品ページ（cp932対応のみで取得可能）からの収集に方針を変更する。**
- 個別商品ページは`https://p-bandai.jp/item/item-{ID}/`のパターン（cp932の文字化けにより、Fixture化はまだ完了していない。再取得が必要）。
- 海外IPからのアクセス時に`p-bandai.com`の海外向けページへリダイレクトされる挙動を確認。本番のConoHa VPSは国内IPのため通常は問題ないが、Collector実装時にリダイレクト有無を確認するテストを入れること（実装仕様書6章のテスト項目に追加推奨）。
- **優先度の見直し**: 締切情報の取得価値という点では、1.4節のポケモンセンターオンラインの方が上回ることが判明した。プレバンCollectorの着手はポケモンセンターオンラインの後で良い。
- **【2026-08-05・最終結論】個別商品ページもAkamai Bot Manager配下と判明、プレバンCollectorは断念(タスク5)**: 個別商品ページ`https://p-bandai.jp/item/item-{ID}/`を2商品(`item-1000250237`/`item-1000154037`)で実際にfetchして検証した。HTTPステータスは200で返るが、レスポンスヘッダーに`x-bm-agent-version: BM-akamai-agent-1.2.0`・`akamai-grn`・`TS01...`/`TS012...`cookie等のAkamai Bot Manager特有の痕跡があり、本文は`<title></title>`(空)+Boomerang RUM計測スクリプト+難読化されたAkamaiセンサーデータペイロードのみで、商品名・価格等の実データを一切含まないJSチャレンジページだった。2商品分のレスポンスを比較したところ、リクエストごとのランダムトークン以外は完全に同一で、商品ごとの差分も無いことを確認済み。`content-type: text/html; charset=Shift_JIS`自体は事前情報通り確認できたが、中身がチャレンジページである以上デコードしても無意味。JS実行(センサーデータ計算・送信)を経ないと実コンテンツが解放されない構造のため、0節の方針4「Bot対策サイトは正面突破しない」により、ヘッドレスブラウザ等でこのチャレンジを解決する実装は行わない。**プレミアムバンダイCollectorはここで正式に断念する。**

### 1.3 駿河屋買取（suruga-ya.jp）

- ✅ Bot対策なし、UTF-8、取得可能。想定より扱いやすいことを確認。
- 検索エンドポイント: `https://www.suruga-ya.jp/kaitori/search_buy?category={カテゴリID}&search_word={キーワード}`
- 確認済みカテゴリID: `501`=おもちゃ・ホビー全体、`50108`=トレカ・カード類、`50104`=プラモデル、`50102`=フィギュア、`50103`=トレーディングフィギュア、`5010800115`=ワンピースカードゲーム、`501080040`=遊戯王OCG、`501080020`=デュエル・マスターズ（いずれも`50108`配下のサブカテゴリ）、`5010401`=ガンダムプラモデル（`50104`配下のサブカテゴリ）、`20038`=ニンテンドースイッチ（他は`category=501`ページのカテゴリ一覧から機械的に取得可能）
- 検索結果テーブルの構造（1行 = 1商品）:
  ```
  商品画像 | 種類/タイトル（ブランド/レアリティ/属性/弾名 + 型番[レアリティ]:カード名） | 発売日 + 型番 + 管理番号 | 買取価格 | 詳細リンク
  ```
  - 買取価格が固定額でなく**「メールにてお見積」の場合がある** → `buyback_prices.amount`はnullable必須、その場合は`price_type`は買取価格として扱わず信頼度Dまたは対象外とする
  - 「PSA/GEM MT 10」のような鑑定品別価格が併記される商品がある → 初期実装では無鑑定品の価格のみ扱い、鑑定品は`extra`フィールドに保存するだけに留める（Phase2で本格対応）
  - `[価格上昇中]`というタグが商品名の隣に付くことがある → Opportunity Scorerの「相場上昇」判定に直接使える有用なシグナル
- 絞り込みクエリパラメータ: `restrict[]=brand={メーカー名}`、`restrict[]=purchase_hendou=価格上昇中`、`restrict[]=expensive_purchase=true`（高価買取対象のみ）。**巡回時は`purchase_hendou=価格上昇中`で絞った差分取得を定期実行すると、通知価値の高い案件を効率的に拾える。**
- **JANコードは一覧ページの時点で取得可能なケースがある**（「発売日/型番/JANコード/管理番号」列に13桁の数値として含まれる）。ただし全商品にあるわけではない(トレカ系のFixtureサンプルでは記載なし)ため、`product_identifiers`はnullable前提のままで良い。管理番号の形式は`GU`+数字／数字のみの2種類を確認。詳細はFixture`suruga_ya_jan_confirmation_20260803.md`を参照。
- **【2026-08-05・一般化した知見】駿河屋Collectorはカテゴリ変更のみで他ジャンルにも対応可能と確認済み**: category=5010800115(ワンピースカードゲーム)で本番ConoHa VPSから実際に取得した生HTML(`tests/fixtures/raw_html/raw_suruga_ya_onepiece.html`)を、`app/collectors/markets/suruga_ya.py`のパース処理に一切手を入れずそのまま通しても20件全件を正しく抽出できた。このデータには管理番号が`GU`始まりと`GN`始まりの両方が混在していたが、主経路の`DETAIL_URL_PATTERN`(`[A-Za-z0-9]+`)が接頭辞非依存でURLから直接抽出しているため、`GU`限定の`MANAGEMENT_NUMBER_PATTERN`(通常到達しないテキストフォールバック)を変更する必要は無かった。`[価格上昇中]`タグ・メールにてお見積・confidence B/D判定もすべて既存実装のまま正しく機能。カテゴリIDはコンストラクタ引数(`category`)で既に外部化されており、今回`CATEGORY_ONE_PIECE_CARD = "5010800115"`という定数を1行追加しただけで対応できた。**したがって、今後さらにジャンル(他のトレカ・ホビー系カテゴリ等)を追加する際は、`suruga_ya.py`にカテゴリID定数を1行追加するだけでよく、パース処理側のコード変更は原則不要という設計になっている。**
- **【2026-08-05・型番(型式)の扱い】ガンダムカテゴリ(category=5010401)には「型番」が独立した4要素目として存在し、専用パターンを追加して対応した**: category=5010800115(トレカ)の検証時点では「発売日/型番/JANコード/管理番号」列は最大3要素(型番が独立して現れない)だったが、category=5010401(プラモデル)では4要素すべて埋まるケース(例: `2026/04/25<br>5072030<br>4573102720306<br>603227273`)がある。既存の`JAN_AND_ID_PATTERN`(JAN13桁+直後のトークン)/`MANAGEMENT_NUMBER_PATTERN`(URLベースが主経路)はこの型番をJAN・管理番号と誤認してはいなかった(型番は7桁前後でJANの桁数と衝突しない、管理番号はURLから独立抽出のため)が、型番自体はどのパターンでも捕捉されていなかった。新たに`MODEL_NUMBER_PATTERN`(「発売日の直後・13桁JANの直前」という位置関係で識別。トレカ系の「発売日+JAN+管理番号」3要素ケースでは直後のトークンがJANそのものになるため誤マッチしないことを確認済み)を追加し、`extra["model_number"]`として公開するようにした(20件中19件で抽出、残り1件は型番欄自体が空という実データの仕様)。**`ProductIdentifierType.MODEL`("model")としてのDB永続化(`product_identifiers`テーブルへの書き込み)は未着手。** 現状はJANを含めどの識別子種別についても`product_identifiers`への書き込み処理自体が存在しない(`app/pipeline/market_matching.py`は商品名の類似度のみでマッチングしている)ため、型番だけを先に永続化する場合は商品照合ロジックの拡張を伴う設計判断が必要になる(CLAUDE.md 0.5の「商品照合の確定ロジックに関わる仕様変更」に該当しうるため、着手前に協議推奨)。
- **【2026-08-05・ニンテンドースイッチカテゴリ(`category=20038`)対応、コード変更あり】型番の英数字混在フォーマットはコード変更不要、商品単位混同への対応として`category_text`を新設**: category=20038で取得した実HTML(`tests/fixtures/raw_html/raw_suruga_ya_switch.html`)で2点確認した。
  1. **型番フォーマット**: ガンダムでは型番が数字のみ(例:`5072030`)だったが、ニンテンドースイッチでは`HAC-P-BQPYA`のような英数字+ハイフン混在形式だった。`MODEL_NUMBER_PATTERN`の`(?P<model>\S+)`は元々非空白文字全般にマッチする実装(数字限定ではなかった)だったため、**コード変更無しで実データ20件全件を正しく抽出できることを確認した**(JAN/管理番号との誤認も無し)。
  2. **hardsoft分類軸(商品単位の混同対策)**: ニンテンドースイッチカテゴリには`restrict[]=hardsoft=ソフト|周辺機器|amiibo|本体`という分類軸があり、同じ「Nintendo Switch」というキーワードでもソフトと周辺機器・本体は全く別の商品単位になりうる(技術分析レポート8章で懸念されていた「商品単位の混同」が実際に起こりうるカテゴリ)。既存実装はこの軸を`extra`のどこにも保持していなかったため、全カテゴリ共通で存在する行内の商品種別ラベル(`<div class="category">`、例:ガンダムでは「プラモデル」)を`_category_text()`で抽出し、`extra["category_text"]`として新たに公開するようにした(Switch専用の特殊分岐ではなく、既存の`raw_suruga_ya_gundam.html`等でも同様に機能することをテストで確認済み)。ニンテンドースイッチの実データ20件は全て`category_text == "ニンテンドースイッチソフト"`だった。
  - ~~未確認のまま残る点~~ → **確認済み(2026-08-06)。amiibo(`raw_suruga_ya_switch_amiibo.html`)・本体(`raw_suruga_ya_switch_hardware.html`)の実HTMLで検証した結果、`category_text`はそれぞれ`"amiibo"`・`"ニンテンドースイッチハード"`という、ソフトの`"ニンテンドースイッチソフト"`とは明確に異なる値を正しく返すことを確認した(3種とも20件全件で単一の値、混同無し)。ラベルはhardsoftを正しく反映しており、`_category_text()`自体は商品単位混同対策のデータ源として機能する。ただし、この値をProduct Matcher側で実際に使うかどうかは別課題(下記参照・3節に記録)。**
- **【2026-08-06・一般化した知見の再確認】遊戯王OCG(`category=501080040`)・デュエル・マスターズ(`category=501080020`)でもコード変更無しで動作確認済み**: いずれも`50108`(トレカ・カード類)配下のサブカテゴリで、実HTML(`tests/fixtures/raw_html/raw_suruga_ya_yugioh.html`・`raw_suruga_ya_duelmasters.html`)を`app/collectors/markets/suruga_ya.py`に一切手を入れずそのまま通しても20件ずつ正しく抽出できた。管理番号は両カテゴリとも`GU`始まりのみ、JAN/型番は0件(トレカ系カテゴリ全般の既知の傾向と一致)、`category_text`(`_category_text()`)も個別カード種別名がそのまま入り既存実装のまま機能。`[価格上昇中]`タグ・メールにてお見積・confidence B/D判定も無修正で機能した。`CATEGORY_YUGIOH = "501080040"`/`CATEGORY_DUEL_MASTERS = "501080020"`を1行ずつ追加しただけで対応完了。**TCG系カテゴリはカテゴリID追加のみで拡張できる可能性が高い**: 駿河屋のTCG親カテゴリ(`category=5010800`)には遊戯王・デュエマ以外にも50種類以上のトレーディングカードゲーム(ヴァイスシュヴァルツ、ヴァンガード、デジモンカードゲーム、シャドウバース エボルヴ等)が存在することが分かっている。ワンピースカード・遊戯王OCG・デュエル・マスターズの3カテゴリで同一の構造(GU管理番号・JAN/型番無し・category_textが機能)が繰り返し確認できているため、他のTCGカテゴリも同じ構造であれば`suruga_ya.py`にカテゴリID定数を1行追加するだけで対応できる可能性が高い。実際の需要に応じて追加検討すること(全種類を先回りして網羅する必要は無い)。

### 1.4 ポケモンセンターオンライン（pokemoncenter-online.com）【最重要・優先度見直し対象】

- ✅ Bot対策なし、UTF-8、取得可能。**これまで確認した4サイトの中で最も情報が揃っている。**
- 商品ページURL: `https://www.pokemoncenter-online.com/{商品コード}.html`（商品コードは13桁の数字）
- 商品詳細セクションに`商品コード`/`発売日`/`各種期間`等のラベル+値構造があり、
  **`各種期間`の中に「抽選応募受け付け期間」「抽選結果発表日」「購入および、支払い期間」「商品のお届け時期」が
  すべて日時付きで記載されている。** 一番くじで欠けていた締切・当選発表・購入期限が、ここでは全部取れる。
- 在庫状況（「品切れ」「予約」等のラベル）、価格、購入上限（「お一人様1点限り」）も同じページから取得可能。
- **商品コードとJANコードの関係(2026-08-05確認済み、3節参照)**: 先頭2桁で条件付き一致。`45`/`49`始まり(GS1 Japan管理の正規事業者コード範囲、通常販売商品で確認)はJANコードとして扱えるが、`99`始まり(GS1のインストア専用疑似コード範囲、抽選販売商品で確認)は内部管理コード相当でJAN一致には使えない。サンプル5件からの経験則であり、GS1公式文書での裏取りではないため要継続検証。
- 商品一覧ページ（カテゴリ別・新着別）は未取得。個別ページのURLパターンから商品コードを収集する
  巡回設計が必要（一覧ページの構造確認はPoC-6相当として別途必要）。
- Fixture: `pokemon_center_online_product_20260803.md`（正規表現抽出パターンの詳細もこちらに記載）

**優先度の見直し**: 技術分析レポート3章では「条件付きで可能」評価だったが、実測の結果、
締切・当選発表・購入期限まで揃う情報源はここが唯一である。**MVP対象の仕入れ情報源として、
一番くじと並ぶ最優先候補に格上げする。** 次のCollector実装候補はプレバンより
ポケモンセンターオンラインを優先することを推奨。

### 1.5 本番ConoHa VPSでの実データ検証結果(2026-08-03、タスク17)

タスク4・6・14実装時点ではすべてMarkdown変換済みテキストのFixtureをもとにした
テキストパターンベースの推測実装であり、実際の生HTMLでの動作は未検証だった。
ユーザーが本番ConoHa VPS上で実際にfetchした生HTML(`tests/fixtures/raw_html/`配下、
`raw_1kuji_top.html`/`raw_suruga_ya_search.html`/`raw_pokemon_center.html`)による
検証の結果、以下の問題が実際に発生することを確認し、修正した。

- **一番くじ(1kuji.com)**: `parse()`が「PICK UP ITEMを1件も抽出できませんでした」で
  完全に失敗した。原因は2点:
  1. 商品詳細ページへのリンクは絶対URL(`https://1kuji.com/products/{slug}`)ではなく
     相対パス(`/products/{slug}`)だった。
  2. 「店頭販売」「オンライン販売」というラベルは、日付テキストと同じ要素内に
     連結されているのではなく、`<p class="status shop">`/`<p class="status online">`
     という別要素になっており、日付側の`<p class="date">`にはラベル文言を含まない。
  → `section.pickupCol div.swiper-slide`をCSSセレクタで直接走査し、`p.status`の
  直近クラス(shop/online)を見て後続の`p.date`をどちらの日付として扱うか判定する
  実装に書き換えた(`app/collectors/sources/ichiban_kuji.py`)。実データで
  PICK UP ITEM 15件すべてが正しく抽出できることを確認済み。
  なお、bandaispirits.co.jpの商品詳細ページは今回生HTMLの提供が無く、
  引き続きFixtureベースの未検証状態のまま(同様の問題が起きる可能性がある、要検証)。
- **駿河屋(suruga-ya.jp)**: `parse_observations()`が`TypeError`で失敗した(前回報告の
  `_find_detail_anchor()`バグの実体)。原因は2点:
  1. 詳細ページへのリンクが絶対URLではなく相対パス(`/kaitori/kaitori_detail/{管理番号}`)
     だった(`DETAIL_URL_PATTERN`が一致せず、行内の候補アンカーが1件も見つからない)。
  2. 全選択チェックボックス用のリンク等、`href=""`の`<a>`要素をselectolaxが`None`として
     返すことがあり、`.get("href", "")`では拾いきれず`DETAIL_URL_PATTERN.match(None)`で
     `TypeError`になっていた。
  → `DETAIL_URL_PATTERN`を絶対/相対どちらのURLにも一致するよう修正し、`urljoin()`で
  絶対URLに変換して保持するようにした。`.get("href") or ""`でNoneも吸収するよう修正した
  (`app/collectors/markets/suruga_ya.py`)。JANコード抽出・[価格上昇中]タグ・
  「メールにてお見積」の検出はいずれも実データでそのまま機能することを確認済み。
  ただし鑑定品価格(【PSA/GEM MT 10】等)は今回取得した実データに該当商品が
  含まれておらず未検証のまま(要検証)。
- **ポケモンセンターオンライン**: 「各種期間」の4フィールド(応募期間/結果発表日/
  購入期間/お届け時期)の抽出はラベル+値のテキストパターンマッチのまま実データでも
  正しく機能した。一方、価格だけは`price=None`になる問題があった。原因は、実際のDOMでは
  金額の数字("5,800")と単位("円")・税込表記が`<span class="txt">5,800<small>円</small>
  </span><small class="sml">税込</small>`のように別要素(入れ子)に分かれており、
  行単位でテキストをフラット化すると別の行になってしまうため。
  → 価格の正規表現を、行単位ではなく本文全体に対して空白文字(改行含む)を許容する形で
  検索するよう修正した(`app/collectors/sources/pokemon_center_online.py`)。
  在庫状況ラベルの判定も、価格行を境界にした行範囲検索から、本文全体の最初の一致を
  採用する単純な方式に変更した(実データでも正しく「品切れ」を拾えることを確認済み)。

**3サイトとも、`tests/fixtures/raw_html/`配下の生HTMLをそのまま読み込む統合テストを
各Collectorのテストファイルに追加済み(`test_..._against_raw_html_fixture`)。**
今後DOM構造が変わった場合はこれらのテストが検知する。

### 1.6 本番ConoHa VPSでの実データ検証結果(2026-08-04、タスク18)

1.5節(タスク17)の時点で未検証のまま残っていた3項目について、ユーザーが本番ConoHa VPS上で
追加の生HTML(`tests/fixtures/raw_html/raw_suruga_ya_graded.html`/
`raw_pokemon_center_normal_sale.html`/`raw_bandaispirits_detail.html`)を取得し、検証した。

- **駿河屋の鑑定品価格【PSA/GEM MT 10】等**: `GRADED_PRICE_PATTERN`(既存実装)は追加の
  修正無しで実データに対しても正しく機能した。鑑定品価格は`<label>`直下のテキストと
  `<font>`要素内の金額という別要素構造だが、間の空白(改行含む)がパターンの`\s*`で
  吸収されるため問題なかった。実データ20件中19件で`extra["graded_price"]`が正しく
  分離抽出され(無鑑定価格と鑑定品価格が別々に取得できることも確認)、残り1件は
  「メールにてお見積」で鑑定品価格自体が併記されていない実データ上正しいケースだった。
  修正不要、検証のみで完了。
- **ポケモンセンターオンラインの通常販売ページ**(「各種期間」セクション無し):
  event_type判定ロジック(既存実装)は追加の修正無しで正しくNORMAL_SALEと判定した。
  抽選ページとDOM構造が異なる(各種期間ブロックが無い)通常販売ページでも、価格・
  商品名・在庫状況(「品切れ」)・購入上限(「お一人様5点限り」)の抽出は全て従来の
  実装のまま機能することを確認した。修正不要、検証のみで完了。
- **bandaispirits.co.jpの商品詳細ページ**(1.5節で「生HTML未提供のため未検証」だった箇所):
  価格・発売日・商品名の抽出(ラベル行+値行のテキストパターンマッチ)は追加の修正無しで
  実データに対しても正しく機能した。一方、**商品画像(image_urls)は実データで誤りが
  判明し修正した**: `tree.css_first("img")`がページ最初の`<img>`(ヘッダーの
  BANDAI SPIRITSロゴ`/assets/img/logo_01.svg`)を拾ってしまい、商品画像ではなかった。
  実際の商品画像は`div.l_productsSlider`内にまとまっている(alt属性が商品名と一致)ため、
  このコンテナ配下の`img`から取得するよう`app/collectors/sources/ichiban_kuji.py`を
  修正した。

**3件とも、該当raw_htmlをそのまま読み込む統合テストを追加済み**
(`test_parse_observations_graded_price_against_raw_html_fixture`/
`test_parse_against_normal_sale_raw_html_fixture`/
`test_parse_bandaispirits_detail_against_raw_html_fixture`)。
ユニットテスト161件全てグリーン(既存158 + 新規3)。

---

## 2. Phase 0 の具体的な着手タスク（進捗管理表）

実装仕様書7章の「開発用プロンプト集」と対応させつつ、1章の実データを踏まえて着手順を確定する。
**このタスク番号がプロジェクト全体の唯一の正となる。**（技術分析レポート17章のロードマップ番号や
実装仕様書のPromptN番号とは意図的に一対一対応させていない。混同しないこと。）

| # | タスク | 対応する詳細仕様 | ステータス |
|---|---|---|---|
| 1 | リポジトリ初期化・Docker Compose | 実装仕様書 Prompt 1 | ✅ 完了 |
| 2 | 基本DBモデル + Alembicマイグレーション | 実装仕様書 Prompt 2・1章 | ✅ 完了 |
| 3 | SourceCollector基底クラス実装 | 実装仕様書 Prompt 3・9章 | ✅ 完了 |
| 4 | 一番くじCollector実装（`1kuji.com`+`bandaispirits.co.jp`、`on-line.1kuji.com`は個別詳細・応募ページのみ除外） | 実装仕様書9章の正規表現パターン | ✅ 完了（トップページは本番VPS生HTMLで検証済み・CSSセレクタベースに書き換え。bandaispirits.co.jp詳細ページはFixtureベースのまま未検証、1.5節参照。⚠️on-line.1kuji.comの除外範囲はタスク18で商品一覧ページのみ例外化） |
| 5 | プレミアムバンダイCollector実装 | 実装仕様書 Prompt 3拡張 | ❌ 断念（Akamai Bot Managerにより個別商品ページもJS実行必須と判明、CLAUDE.mdの方針上ここでは正面突破しない） |
| 6 | 駿河屋Market Collector実装 | 実装仕様書10章・Prompt 4 | ✅ 完了（本番VPS生HTMLで検証済み。相対href・href=None対応の修正済み、1.5節参照。JANコード抽出(タスク15)も実データで確認済み） |
| 7 | 地域解決ロジック実装（`resolve_region()`、`fulfillment_type`フィルタ） | 実装仕様書1.3節、4.3節 | ✅ 完了 |
| 8 | 商品照合スコアリング実装（JAN一致・商品名類似度・単位不一致ペナルティ等） | 技術分析レポート11章 | ✅ 完了（単位不一致は減点ではなく89点上限キャップとして実装、None属性は不一致判定しない。⚠️2026-08-05発覚: スコアリング自体は完了していたが、呼び出し側がproduct_identifiersへの永続化・読み込みを配線しておらず、JAN/型番一致が実質一度も機能していなかった。タスク13/4節参照） |
| 9 | Profit Engine実装（未確定コスト分離ロジック必須） | 実装仕様書2章・Prompt 5 | ✅ 完了（未確定コストは金額計算から完全除外、確認済み） |
| 10 | Opportunity Scorer実装 | 実装仕様書3章・Prompt 6 | ✅ 完了（未確定コストがスコアに影響しないことをinspect.signatureで構造的に保証） |
| 11 | Discord通知実装（締切不明時フォールバック含む） | 実装仕様書4章・Prompt 7 | ✅ 完了（商品照合match_statusの可視化を追加修正済み。地域の要確認とは別フィールドで表示） |
| 12 | Celery Beatでの定期実行結線 | — | ✅ 完了（収集結果のDB反映・Opportunity生成は含まず、Collector起動のみ。詳細はタスク13で対応。⚠️2026-08-05、稼働状況CLI導入時にrun_suruga_ya_price_rising_scanが301未対応で毎回全滅していたバグを発見・修正、4節参照） |
| 13 | E2Eパイプライン実装（収集→商品照合→Profit Engine→Opportunity→dedupe→Embed組み立て） | — | ✅ 完了（Fixtureベースの統合テストのみ、実サイトでの動作は未検証） |
| 14 | ポケモンセンターオンラインCollector実装 | 本ファイル1.4節のFixture・正規表現パターン | ✅ 完了（本番VPS生HTMLで検証済み。各種期間4フィールドはそのまま機能、価格抽出のみ実データ不一致が判明し修正、1.5節参照） |
| 15 | 駿河屋CollectorへJANコード抽出を追加 | 本ファイル1.3節のFixture(`suruga_ya_jan_confirmation_20260803.md`) | ✅ 完了（本番VPS生HTMLでも抽出できることを確認済み） |
| 16 | Discord Bot Interaction実装（応募済み/ウォッチ/非表示ボタン） | 実装仕様書14章 | ✅ 完了（discord.pyのゲートウェイ実接続のみ未検証） |
| 17 | 一番くじ/駿河屋/ポケモンセンターオンラインの本番VPS実データ検証・修正 | `docs/verification_checklist.md`、`scripts/verify_collectors.py` | ✅ 完了（詳細は1.5節参照） |
| 18 | on-line.1kuji.com商品一覧ページ(`ProductList.aspx`)の自動収集追加(新商品自動発見) | 本ファイル1.1節 | ✅ 完了（本番で`worker`/`beat`再起動後、手動トリガーで実機検証済み。success_count=37(1kuji.comトップ15+商品一覧22)、他パスへのアクセス無し、詳細は1.1節参照） |
| 19 | 手動検証専用E2Eタスク(`run_live_e2e_verification`)実装(収集→DB反映→商品照合→Profit Engine→Opportunity→通知判定) | 本ファイル7〜9節 | ✅ 完了（Beat Scheduleには未登録の手動検証専用タスク。合成MarketObservationを使用） |
| 20 | 定期実行タスク(`run_ichiban_kuji_collector`/`run_suruga_ya_price_rising_scan`)のDB反映・Opportunity生成・通知判定への結線 | 本ファイル12節 | ✅ 完了（タスク13・19の既存関数をそのまま呼び出す形で結線。実データで新商品発見→DB反映→Opportunity生成→Discord送信までの一気通貫を実機確認済み。詳細は12節参照） |
| 21 | ポケモンセンターオンライン新商品一覧ページの発見・商品コード自動抽出(段階1、DB反映・Beat Schedule登録は含まず) | 本ファイル13節 | ✅ 完了（段階1のみ。`discover_new_products()`実装、Fixture2種(一覧275件+個別1件)によるテスト5件追加、全て実データ検証済み。段階2(Beat Schedule登録・DB反映)は別タスクとして依頼待ち、詳細は13節参照） |
| 22 | ポケモンセンターオンラインのBeat Schedule登録・DB反映結線(段階2)、駿河屋との突合・通知到達確認、重複防止の実データ比較検証 | 本ファイル14節 | ✅ 完了（`run_pokemon_center_collector`追加・6時間毎で登録、本番実機トリガーでProduct/ReleaseEvent 275件を実際にDB反映(success_count=275, error_count=0)、`run_suruga_ya_price_rising_scan`との突合で実際に1件のOpportunity生成・should_send=Trueまで到達(実Discord送信は保留、ユーザー確認待ち)。JAN識別子ありの270件は商品名が変わってもAUTO_MATCHを維持し一番くじより重複防止が強いことを実データで確認、識別子無し5件は一番くじと同じ限界を引き継ぐことも確認。詳細は14節参照） |
| 23 | 【緊急対応】should_send=TrueのOpportunityを常時起動Botが自動的に拾ってDiscordへ送信する仕組みの実装(タスク20〜22の時点でこれが存在せず、収集→通知という当初からの目的そのものが未達成だった) | 本ファイル15節 | ✅ 完了（`pending_notifications`テーブル新設、`_match_and_score_observation()`がshould_send=True時に1行永続化、常時起動Botが15分毎(`app/notification/dispatcher.py`)にポーリングして送信。タスク22で生成された「MEGA スターターセットex イーブイex構築デッキ」のOpportunityが実際にこの経路でDiscordへ送信されたことをmessage_id取得込みで実機確認済み。重複送信防止(status=sent後は二度と拾われない)もテスト・実機の両方で確認。詳細は15節参照） |

**タスク7・8の分離について**: 当初「タスク7 = Product Matcher実装（地域解決ロジック含む）」と
一つにまとめていたが、実装仕様書Prompt 4が地域解決ロジックのみを指しているのに対し、
技術分析レポート11章の商品照合スコアリング（本来のProduct Matcher）は独立した重みのあるロジックのため、
2つのタスクに分離した。地域解決は「どの地域のイベントか」を決める話、商品照合スコアリングは
「これは同じ商品か」を決める話で、性質が異なる。

**残っているFK未整備箇所（TODO、後続タスクで対応）**:
- `profit_snapshots.channel_id` / `opportunities.best_channel_name`: `sales_channels`テーブル未実装のため文字列暫定運用。実装時にFK化すること。
- `fee_rate`/`fee_fixed=0`固定（駿河屋買取は手数料の概念がないための暫定値）: `fee_rules`テーブル実装時に見直すこと。

### タスク4・5・6着手時のチェックリスト（実装済み分も含め記録用）

- [x] 一番くじ: `on-line.1kuji.com`へのリクエストを一切行わないこと（テストで保証済み）
- [ ] プレバン: HTTPレスポンスのエンコーディングをcp932で明示的デコードすること（未着手）
- [x] 駿河屋: `amount`がnullの買取価格（「メールにてお見積」）を、買取価格ありの信頼度Bとして誤登録しないこと（テストで保証済み）
- [x] 実装済みCollectorとも、取得失敗時に`ParseError`/`FetchError`を正しく送出すること

---

## 3. 未確認のまま残っている項目（次にPoCすべきもの）

- ~~ポケモンセンターオンラインの実データ確認~~ → **確認済み(1.4節)。締切・当選発表・購入期限すべて取得可能、優先度を最上位に格上げ**
- ~~駿河屋の商品詳細ページのJAN/型番の取得可否~~ → **確認済み(1.3節・Fixture`suruga_ya_jan_confirmation_20260803.md`)。JANコードは一覧ページの時点で取得可能なケースがある（ただし全商品ではない、nullable前提の設計のままで良い）。管理番号の形式が`GU`+数字／数字のみの2種類確認、文字列型で保持すること**
- ~~プレミアムバンダイの個別商品ページ（`p-bandai.jp/item/item-{ID}/`）のFixture再取得~~ → **確認済み・断念確定(1.2節・タスク5、2026-08-05)。個別商品ページもAkamai Bot Manager配下でJS実行必須と判明、実データは取得不可。プレバンCollectorはここで正式に断念**
- ~~ポケモンセンターオンラインの商品一覧ページ（カテゴリ別・新着別）のURL・構造確認~~ → **確認済み・段階1実装完了(13節、2026-08-06、タスク21)。新商品一覧ページ`/search/?prefn1=releaseType&prefv1=1&srule=top-new-product`を発見、Bot対策なしで取得可能、`sz`パラメータで1リクエストにつき最大275件(2026-08-06時点の全件)を取得できることを確認した**
- ~~ポケモンセンターオンラインの商品コード(13桁)がJANコードと一致するかの確認~~ → **確認済み・先頭2桁による条件付き一致(`app/collectors/sources/pokemon_center_online.py`、2026-08-05)**: サンプル5件で確認。通常販売商品3件(`4521329432069`/`4521329413051`/`4521329338453`)は全て`45`始まり、抽選販売商品2件(`9900000006082`/`9900000006808`)は全て`99`始まりだった。JAN-13の国コード部のうち`45`/`49`はGS1 Japan管理の日本の正規事業者コード範囲、`99`はGS1がインストアマーキング(店舗外で通用しない疑似コード)用に予約している範囲にあたる。この対応関係に基づき、`_classify_product_code()`で先頭2桁が`45`/`49`の場合のみ`identifiers["jan"]`として供給し、それ以外(`99`始まり等)は`extra["product_code_type"] = "internal_code"`として値は保持しつつJAN一致スコアリング(技術分析レポート11.2、JAN一致+100)の対象からは外すようにした。**注意: この判定はサンプル5件からの経験則であり、GS1の公式文書で裏取りしたものではない。高確率だが100%の保証ではなく、今後別の先頭2桁パターンが実データで見つかった場合は判定ロジックの見直しが必要になる(要継続検証)。**
- ~~一番くじ(bandaispirits.co.jp商品詳細ページ)にJAN/型番相当の情報が含まれているか未確認~~ → **確認済み・JAN/型番取得不可、商品名+発売日による照合に依存し続ける(1.1節、2026-08-05)**: bandaispirits.co.jpの商品詳細ページの「商品詳細」セクションには価格・発売日のみで、JAN/型番は一切含まれていない。あるのは`prd_id`/`grp_id`という内部識別子のみで、駿河屋等の外部検索に使える標準コードではない。`app/collectors/sources/ichiban_kuji.py`のnormalize()の`identifiers={}`固定は変更不要。**運用上の含意**: 5節で判明した通り仕入れ側・相場側の双方にJAN/型番が揃わない限りJAN一致ロジックの実効性は限定的だが、一番くじについてはこの前提が今後も恒久的に成立しないことが確定した。技術分析レポート11章の商品照合スコアリングにおいて、一番くじ発の商品はJAN一致(+100)/型番一致(+80)の恩恵を今後も受けられないため、商品名類似度・発売日近似での照合精度が一番くじジャンルでは特に重要になる。
- ~~Discord Bot側のInteraction実装（実装済み。discord.pyのゲートウェイ実接続のみ未検証、タスク16の報告参照）~~ → **確認済み(7節・8節、2026-08-05)。常時起動Bot(`app/bot/main.py`)経由でゲートウェイ実接続・Embed+ボタン付きメッセージ送信を実機確認。ユーザーが実際に3ボタン(応募済み/ウォッチ/非表示)を押し、`lottery_entries`/`watchlists`への行追加・`opportunities.status='hidden'`へのDB反映まで確認済み。途中`api_base_url`の既定値がbotコンテナから到達不能で「応答しませんでした」になるバグを発見・修正(8節参照)**
- **【2026-08-05・新規、別タスクとして記録】Discord操作(応募済みにする/ウォッチリスト登録/非表示/マージ確定API/照合を確定する/別商品として分離)には権限チェックが一切無い**: `app/notification/interaction_view.py`の既存3ボタン、10節で追加した「照合を確定する」/「別商品として分離」の2ボタン、および6節で追加した`POST /manual-review-tasks/{id}/resolve`(照合の確定/棄却=商品Productのマージ)のいずれも、`verify_api_key`(単一ユーザー前提のAPIキー認証)以外のユーザー単位の権限チェックを持たない。個人利用の現段階では実害は小さいが、特にマージ確定操作は後戻りしにくいため、複数ユーザー対応(Phase5)までに必ず対応が必要。
- **【2026-08-05・新規、別タスクとして記録】`OpportunityActionView`のボタンはcustom_idを明示していないため、Botプロセスの再起動を跨いで機能しない**: discord.pyの永続View(`bot.add_view()`+固定`custom_id`)にしていないため、送信済みメッセージのボタンはそれを送った`discord.Client`インスタンスがプロセス内に生き続けている間しか反応しない。`app/bot/main.py`(常時起動Bot)が再起動すると、それ以前に送信済みのメッセージのボタンは全て無反応になる(見た目上はボタンが残ったままなのに押しても反応しない、静かな劣化)。実運用でBotの再起動(デプロイ・クラッシュ等)が発生する前提なら、`custom_id`を`event_id`/`opportunity_id`を埋め込んだ固定文字列にし、`on_ready`等で`bot.add_view()`により永続化する対応が必要。
- ~~一番くじ/駿河屋/ポケモンセンターオンラインの本番VPS実データ検証~~ → **確認済み(1.5節・タスク17)。判明した問題は修正済み**
- ~~bandaispirits.co.jpの商品詳細ページの生HTML取得・検証~~ → **確認済み(1.6節・タスク18)。価格/発売日/商品名は修正不要、商品画像(image_urls)がヘッダーロゴを誤取得していた不具合を発見・修正済み**
- ~~駿河屋の鑑定品価格(【PSA/GEM MT 10】等)の実データでの動作確認~~ → **確認済み(1.6節・タスク18)。既存実装のまま正しく機能、修正不要**
- ~~ポケモンセンターオンラインの通常販売ページ(「各種期間」セクションが無いページ)の実データ確認~~ → **確認済み(1.6節・タスク18)。event_type=NORMAL_SALE判定・価格/商品名/在庫/購入上限の抽出とも既存実装のまま正しく機能、修正不要**
- ~~駿河屋Collectorのニンテンドースイッチカテゴリ(category=20038)対応確認~~ → **確認済み(1.3節、2026-08-05)。型番の英数字混在フォーマット(`HAC-P-BQPYA`)はコード変更不要で抽出できることを確認。hardsoft分類軸(商品単位の混同対策)として`extra["category_text"]`を新設し、既存カテゴリ含め全カテゴリ共通で機能することを確認済み**
- ~~駿河屋Collectorの`category_text`が「周辺機器」「amiibo」「本体」でも実際に区別できる値を返すか未確認~~ → **確認済み(1.3節、2026-08-06)。amiibo/本体の実HTML(`raw_suruga_ya_switch_amiibo.html`/`raw_suruga_ya_switch_hardware.html`)で検証した結果、`category_text`はそれぞれ`"amiibo"`/`"ニンテンドースイッチハード"`と、ソフトの`"ニンテンドースイッチソフト"`から明確に区別できる値を返すことを確認した。「周辺機器」のみ実データ未取得のまま残っているが、amiibo/本体で仕組み自体が正しく機能することは確認済みのため優先度は高くない。**
- **【2026-08-06・新規、別タスクとして記録・着手前に設計相談が必要】`category_text`(商品単位を示す値)がProduct Matcher(商品照合スコアリング)に一切配線されていない**: `_category_text()`はソフト/amiibo/本体を正しく区別できる値を返すことを確認済み(1.3節)だが、`app/matcher/product_matcher.py`の`MatchCandidate`/`ProductAttributes`にはこれに相当するフィールドが存在せず、`calc_match_score()`もcategory_textを一切比較していない。また`Product`モデル(`app/db/models/product.py`)には`category_id`(`categories`テーブルへのFK)という列が既に存在するが、パイプライン全体でどこからも書き込まれず参照もされていない、配線されていない列であることも判明した。**理論的リスク**: ソフト/本体/amiiboのように明確に異なる商品単位の商品でも、商品名が似ていれば(例: シリーズ名を共有する本体とソフト)category_textによる強制減点が一切かからないため、商品名類似度だけで誤ってNEEDS_REVIEW以上のスコアに達しうる(技術分析レポート8章で懸念されていた「商品単位の混同」に相当)。ただし現時点でこれが実際に誤自動一致を引き起こした実例は確認されていない。**対応案**: 既存の`unit_mismatch`と同様の強制不一致パターン(スコアの上限キャップ)を踏襲するなら、(1)`products`に`category_text`相当の新規列を追加するマイグレーション、(2)`app/pipeline/ingest.py`側でProduct作成/確定時に書き込む配線、(3)`app/pipeline/market_matching.py`側で`calc_match_score()`に渡す配線、の3点が必要になる。**この対応はCLAUDE.md 0.5「商品照合の確定ロジックに関わる仕様変更」に該当するため、実際に問題が起きるか具体的な計画ができてから、着手前に必ず設計を相談すること(スキーマ変更と照合ロジックの両方に影響する範囲の大きい変更のため、今回は見送りと判断した)。**
- ~~駿河屋Collectorの遊戯王OCG・デュエル・マスターズカテゴリ対応確認~~ → **確認済み(1.3節、2026-08-06)。いずれも50108配下のトレカ系サブカテゴリで、コード変更無しで動作確認済み(`CATEGORY_YUGIOH`/`CATEGORY_DUEL_MASTERS`を1行ずつ追加しただけで対応完了)。一般化した知見: TCG系カテゴリはカテゴリID追加のみで拡張できる可能性が高い。駿河屋のTCG親カテゴリ(`category=5010800`)には遊戯王・デュエマ以外にも50種類以上のトレーディングカードゲーム(ヴァイスシュヴァルツ、ヴァンガード、デジモンカードゲーム、シャドウバース エボルヴ等)が存在するが、ワンピースカード・遊戯王OCG・デュエル・マスターズの3例で同一構造(GU管理番号・JAN/型番無し・category_textが機能)が繰り返し確認できているため、他のTCGカテゴリも同様の対応で拡張できる可能性が高い。全種類を先回りして網羅する必要は無く、必要に応じて追加検討すればよい**
- ~~on-line.1kuji.comの商品一覧ページ(ProductList.aspx)が取得可能か~~ → **確認済み・自動収集対象に追加(1.1節、2026-08-06)。個別詳細・応募ページはBot対策により引き続き取得不可だが、商品一覧ページのみ例外的に取得可能と判明し、ホワイトリスト方式で新商品自動発見用に`target_urls`へ追加した**
- **【2026-08-06・新規】一番くじONLINEに「締切」の概念が存在するか未確認**: 一番くじONLINEは抽選応募制ではなく購入型のオンラインくじという性質のため、そもそも「応募締切」という概念自体が存在しない可能性がある(1.1節参照)。商品一覧ページ(ProductList.aspx)からは締切に相当する情報が一切取得できていないが、これが「そもそも締切という概念が無いから」なのか「一覧ページには無いだけで個別詳細ページ(Bot対策で取得不可)には存在する」のかは実データからは判別できていない。現状は`deadline_at=None`(`deadline_source=UNKNOWN`)のまま扱っており、動作上の問題は無いが、事実として未確認のまま残っている。
- ~~on-line.1kuji.com商品一覧の収集結果はDBへ一切反映されない~~ → **解消済み(12節、2026-08-06、タスク20)。駿河屋も含め定期実行タスク2つとも同じ構造的欠落だったことを確認した上で、ingest_normalized_item()/match_observation_to_product()/build_and_score_opportunity()/evaluate_notification()への結線を実施し、実データで新商品発見→DB反映→Opportunity生成→Discord送信までの一気通貫を実機確認済み**
- **【2026-08-06・新規、タスク20の実機検証で判明】Product Matcherの一番くじ商品に対する商品名類似度が実用上機能していない**: 駿河屋の一番くじ関連リストは「孫悟空＆ブルマ＆クリリン 潜水艇「一番くじ ドラゴンボール EX 対決!レッドリボン軍」D賞 フィギュア」のような個別景品名(シリーズ名を含むがそれ以外の文字列が多い)で出品される。既存の`_title_similarity()`(`SequenceMatcher`による全体文字列の編集距離ベース)ではシリーズ名一致分のスコアが希釈されてしまい、実データ6商品(ゴジラ MACHINE CHRONICLE/ゴジラ 最恐怪獣王列伝/オーバーロード/ジョジョの奇妙な冒険 STEEL BALL RUN/ウルトラマン 60th Anniversary/ドラゴンボール EX 対決！レッドリボン軍)で確認したところ、いずれもNEEDS_REVIEW閾値(40点)未満(score=0〜5)にしかならなかった。改善するなら「観測タイトルがProduct名を部分文字列として含む場合の加点」等が考えられるが、商品照合の確定ロジックに関わる変更のため着手前に協議が必要(CLAUDE.md 0.5)。
- **【2026-08-06・新規、タスク20の実機検証で判明】`evaluate_notification()`の「最良売却先」表示が仕入れ側Source名になっている**: `app/pipeline/notify.py`の`OpportunityView`構築で`best_channel_name=source.name`となっており、`build_and_score_opportunity()`に渡した実際の売却チャネル名(例:`"suruga_ya"`)が表示に反映されない(仕入先と最良売却先が常に同じ値になる)。今回のタスク(20)とは無関係な既存コードの問題のため修正はしていない。
- ~~【2026-08-06・新規、タスク20の実機検証で判明】Discord自動送信の仕組みが未実装~~ → **緊急対応・解消済み(15節、2026-08-06、タスク23)。`pending_notifications`テーブル新設+常時起動Botの15分毎ポーリング(`app/notification/dispatcher.py`)で解消。これが解消されるまで「収集→通知の自動化」は実質未完成だった(収集はできてもDiscordに一切届かない状態)。**
- **【2026-08-06・新規、テスト用DBが本番と分離されていない設計上のギャップ】専用のテスト用DBが存在せず、`tests/integration/conftest.py`の`db_session`フィクスチャは本番と全く同じ`app.config.settings.database_url`に接続し、ネストしたトランザクション+rollbackのみで隔離している(接続先そのものは本番DBと同一)。この設計自体はSQLAlchemyの標準的なテスト分離パターンであり、`db_session`を経由する限り安全に機能する(実際、`test_incident_recollection_dedup.py`等はこの隔離のもとで本番の実データに対して安全に検証できている)。**しかし12節の事故の本質はまさにこの前提が崩れたケースだった**: `tests/unit/test_scheduler.py`はCeleryタスク関数を直接呼び出す設計で、`db_session`フィクスチャを一切経由せず、タスク内部が独自にDBセッションを開く(アプリ本体と同じ経路で本番へ直接接続する)。そのためrollback保護の外側で本番へ実際に書き込まれた。**今後も同じ構造のリスクが残っている**: `db_session`を経由しないテスト(Celeryタスクや将来追加されるスクリプト類を直接呼ぶテスト全般)は、書き込みが本番へ確定してしまう経路になりうる。タスク20では該当箇所をnetwork層のmonkeypatchで塞いだ(12節参照)が、これは個別対応であり、同種のテストが今後増えるたびに同じ注意が必要になる。**将来的な検討課題**: `docker-compose.yml`にテスト専用DBサービス(例: `test-db`、本番`db`とは別のPostgreSQLコンテナ+別ボリューム)を追加し、テスト実行時は`DATABASE_URL`をそちらに向ける構成にすれば、rollbackに頼らずコンテナレベルで本番データと完全に分離できる。現時点では未着手(この記録のみ、着手はしていない)。
- **【2026-08-06・タスク20の事故データ(Product 32件・ReleaseEvent 36件)の再収集時デデュープ検証】** `tests/integration/test_incident_recollection_dedup.py`を追加し、実際に本番DBへ書き込まれたこの36件のReleaseEvent全件について「もう一度同じページを収集したら」を再現して検証した。全件が`match_or_create_product()`のexact_match経路(商品名の完全一致)でAUTO_MATCHとなり既存のProduct/ReleaseEventへ正しく紐づき、Product/ReleaseEventのいずれも増えないことを確認した(再収集前後で32件/36件のまま)。**同時に判明した限界**: 一番くじ商品は識別子(JAN/型番)を持たず(1.1節)、`Product.release_date`もどこからも書き込まれないため、`calc_match_score()`側の名前類似度による加点は理論上の上限が+40点(`NEEDS_REVIEW_THRESHOLD`と同値)にしかならず、AUTO_MATCH(90点)/HIGH_PROBABILITY_MATCH(70点)には**原理的に届かない**。つまり一番くじ商品の重複防止は事実上`match_or_create_product()`のexact_match(文字列完全一致)経路のみに依存しており、fuzzy matching側はこれを一切補完できない。空白のゆれ自体は`_title_similarity()`が事前に空白を除去するため無害だが、それ以外の表記変動(全角/半角、記号の差、サイト側のtitle文言変更等)でexact_matchが外れた場合、確実にNEEDS_REVIEWへ落ちて重複Productが作られる。改善するなら商品照合の確定ロジックに関わる変更のため、着手前に協議が必要(CLAUDE.md 0.5、282行目の駿河屋照合精度の課題と同根)。

これらは実装を進めながら随時実データで確認し、本ファイルおよび実装仕様書に追記していく運用とする。

---

## 4. 運用整備タスク(2026-08-05実施)

データ消失・運用停止に直結する項目を優先して着手・完了した。

1. **PostgreSQLの自動バックアップ**: `scripts/backup_db.sh`を追加。`docker compose exec db pg_dump`でコンテナ内のpg_dumpを使う(ホスト側libpqとpostgres:16-alpineのバージョン不一致を避けるため)。gzip圧縮して`/home/user1/backups/shohin-research/`に保存、14日超で自動削除。dbコンテナ未起動時やダンプが空/壊れている場合は本配置前に検知して失敗させる(中途半端なファイルを正常なバックアップとして残さない)。cronに`10 4 * * *`で登録済み(Aqualium側の`backup_all.sh`が4:00のため10分ずらした)。実行テスト済み、28個のCREATE TABLE/COPY文を含む正常なダンプを確認。復元手順はスクリプト内コメント参照。
2. **VPS再起動後の自動復旧**: `docker-compose.yml`の全5サービス(api/db/redis/worker/beat)に`restart: unless-stopped`を追加。`docker.service`は元々systemdで`enabled`済みのため、VPS再起動時はdockerd起動→各コンテナがrestartポリシーに従って自動復旧する。この過程で`api`サービスが実は一度も起動していなかったこと(ホスト8000番が別プロジェクト`stockapp`の127.0.0.1:8000と衝突していたため)が判明し、ホスト側ポートを8001に変更して解消した(dbの55432番と同じ理由の回避策)。dockerデーモン自体の再起動によるフルテストはClaude Codeの権限分類で止められたため未実施(sudo systemctl restart dockerが必要な操作のため)。設定の適用自体(`docker inspect`での`RestartPolicy.Name=unless-stopped`)は全5コンテナで確認済み。
3. **Collector稼働状況の確認用CLIコマンド**: `scripts/collector_status.py`を追加。`collector_runs`テーブル(新規、Alembicマイグレーション`ac68151d8952`)に各Collectorタスクが実行のたびに成否を記録する仕組み(`app/scheduler/run_log.py`)を導入し、CLIはタスクごとの最終実行結果・経過時間・`beat_schedule`から算出した想定間隔に対する遅延の有無を表示する。`--history N`で直近N件の履歴も見られる。実データでOK/NG両方の表示を確認済み。
   - **副次的に発見・修正したバグ**: このCLIの実データ確認中、`run_suruga_ya_price_rising_scan`が実際には**2キーワードとも毎回失敗していた**ことが判明した。原因はhttpxのデフォルト(`follow_redirects=False`)で、`category=501`+`restrict[]=purchase_hendou=価格上昇中`の組み合わせでsuruga-ya.jpがURLエンコード方式正規化のため301を返すことに対応できていなかったため。`app/collectors/markets/suruga_ya.py`・`app/collectors/sources/ichiban_kuji.py`・`app/collectors/sources/pokemon_center_online.py`の3Collector全てで`httpx.AsyncClient(..., follow_redirects=True)`に統一して修正した。定期実行結線(タスク12)は完了扱いだったが、実際には主要な巡回対象の一つが機能していなかったことになる。稼働状況CLIが無ければ気づけなかった不具合。
4. **aiohttpの"Unclosed connector"警告の修正**: `scripts/verify_live_e2e.py`の`_send_to_discord()`で実機再現し、`client.close()`直後にTCPConnectorの内部クリーンアップがイベントループの次のイテレーションで非同期に走るため、`asyncio.run()`のコルーチンが即座に返ると間に合わず警告が出ることを確認した(discord.py+aiohttpの既知のteardownタイミング問題)。`await asyncio.sleep(0.25)`をclose()直後に追加して解消。修正前後で実機再現・警告消失を確認済み。discord.Clientのゲートウェイ接続(`client.start()`)を行っているのはこのスクリプトのみで、他に本番稼働中のDiscord gatewayプロセスは無い(`app/main.py`はFastAPI REST APIのみ)。

## 5. product_identifiers永続化パイプライン実装(2026-08-05)

ガンダムカテゴリ対応(3節)の副産物として、`product_identifiers`テーブルがどの識別子種別(JAN含む)についても一度も書き込まれていないことが判明した。技術分析レポート11.2のJAN一致(+100)/型番一致(+80)スコアリング自体は`app/matcher/product_matcher.py:calc_match_score()`にタスク8の当初から実装済みだったが、呼び出し側(`app/pipeline/ingest.py`・`app/pipeline/market_matching.py`)が(1)新規/マッチ済みProductへの永続化、(2)既存Product候補への読み込み、のいずれも配線していなかったため、**実質的に一度も機能していなかった**(常に空の`{}`同士の比較になっていた)。

- `app/pipeline/ingest.py`に`sync_product_identifiers()`を追加。`product_to_candidate()`が`product_identifiers`を読み込んでcandidateへ積むようにし、`match_or_create_product()`が新規Product作成時・AUTO_MATCH/HIGH_PROBABILITY_MATCH時に識別子を永続化するようにした。
- `app/pipeline/market_matching.py`の`match_observation_to_product()`も、`observation.extra["jan"]`/`extra["model_number"]`をidentifiersとして渡すよう修正(ガンダムカテゴリ対応(3節)で追加した型番もここで初めて実際に使われるようになった)。
- **既存Productへの識別子書き込みは、AUTO_MATCH/HIGH_PROBABILITY_MATCH(スコア70点以上)のときのみに限定した**(ユーザー確認済みの方針)。NEEDS_REVIEW相当の弱いマッチで誤って別商品にJAN/型番を紐付けると、以降の照合が汚染されるリスクがあるため。新規Product作成時は自分自身の識別子を紐付けるだけで他商品を汚染するリスクが無いため、この制限は適用していない。
- `calc_match_score()`本体・`AUTO_MATCH_THRESHOLD`等の閾値定数は一切変更していない。既存のE2Eテスト(`test_collect_to_notification_pipeline_end_to_end`)の「商品名完全一致・識別子なしはNEEDS_REVIEW」というアサーションも無修正のまま通ることを確認済み。
- 実際にJAN一致・型番一致による自動一致が発生することを`tests/integration/test_product_identifier_matching.py`(新規5件)で確認した。うち1件は実Fixture(`raw_suruga_ya_gundam.html`)から`SurugaYaCollector`で実際に抽出したJAN/型番の値を使い、商品名が異なる2件の観測値が同一Productへ自動一致することをend-to-endで検証している。ソース側Collector(一番くじ/ポケセン)は現状JAN/型番を一切抽出しない(`normalize()`が`identifiers={}`固定)ため、両側が有機的に重なる完全な実データシナリオは現時点では作れず、実Fixtureから取れた本物の識別子の値を使いつつ商品名だけ変える構成で検証した(テスト内コメントに明記)。

## 6. manual_review_tasks(要確認キューの手動確定)実装(2026-08-05)

`MatchStatus.MANUALLY_CONFIRMED`が定義以来一度も設定される経路を持たず(enumのdocstringが「将来のmanual_review_tasks運用」と明記したまま放置されていた)、NEEDS_REVIEW(要確認)の照合を人間が確定させても、その時点でJAN/型番がproduct_identifiersへ書き込まれる経路が存在しないことが判明した。5節の永続化パイプラインと合わせて、要確認キューの手動解決機能を実装した。

**設計上の重要な発見**: 「確定」操作は単なるstatus更新ではない。`match_or_create_product()`はNEEDS_REVIEW時点で既に独立した新規Product(candidate)を作成済みであり、この時点で`release_events`/`opportunities`/`product_identifiers`/`watchlists`の4テーブルから外部キー参照されうる。したがって「確定」は、candidate側のこれら4テーブル分のレコードをmatched_product側へ実際に付け替えるマージ処理になる。

- `manual_review_tasks`テーブルを新規作成(id, candidate_product_id, matched_product_id, score, status[pending/confirmed/rejected], created_at, resolved_at, resolved_by)。`match_or_create_product()`がNEEDS_REVIEW判定時、実際に比較した既存候補があれば1行自動作成する。
- `app/pipeline/manual_review.py`の`resolve_manual_review_task()`がマージ本体。以下の設計判断で実装した:
  1. **複数release_eventsの付け替え**: 異なる店舗・日程のrelease_eventsが複数あっても全件付け替える(技術分析9章のイベント/商品分離の設計通り、複数あること自体は問題ない)。ただし`release_events`には`(product_id, shop_id, event_type, start_at)`のUNIQUE制約があるため、matched側に全く同じイベントが既にある場合(同一イベントの重複観測)だけは、`product_identifiers`/`watchlists`と同様に重複を避けてcandidate側を削除する扱いにした(単純な全件付け替えだとIntegrityErrorになることが判明したため、当初案から修正)。
  2. **付け替え漏れの検証**: マージの最後に4テーブル全てに対し、candidate_product_idへの参照が0件であることをアサーションで直接検証する(`_assert_no_remaining_references()`)。
  3. **ロールバック保証**: `session.begin_nested()`(SAVEPOINT)でマージ全体を包み、アサーション失敗を含むあらゆる例外でその範囲だけを確実にロールバックする。`tests/integration/test_manual_review.py::test_merge_failure_rolls_back_partial_reassignment`で、検証部分だけ失敗させても付け替え済みのrelease_event・soft-delete済みのcandidateまで含めて正しく巻き戻ることを実際に確認済み。
  4. **識別子(type,value)/ウォッチ(user_id)が重複するケース**: matched側に既に同じ組み合わせがあればcandidate側を削除するだけにし、重複を作らない。
- 入口はCLI(`scripts/manual_review_cli.py list`/`resolve <task_id> confirm|reject --by <name>`)とWeb API(`GET /manual-review-tasks`・`POST /manual-review-tasks/{id}/resolve`、`app/api/routers/manual_review_tasks.py`)の両方を実装した。Discordゲートウェイ接続が未検証(1.1節・タスク16参照)なマージという後戻りしにくい操作を、いきなり未検証の経路で検証するのを避けるため、Discordボタンはこのタスクのスコープに含めていない(CLI/APIで先に検証してから、既存の`interaction_view.py`と同じ「DiscordボタンがFastAPI経由でDBを操作する」アーキテクチャに沿って追加する想定)。**→ Discordボタン自体は10節で実装・実機検証完了。**
- CLIで実データ(新規Product 2件→NEEDS_REVIEW発生→confirm解決→マージ確認)を実際に動かして動作確認済み。テストはunit 165件+integration 49件(既存37+新規12: マージ処理7件・API層5件)全pass。

**別タスクとして記録(今回のスコープ外)**: Discord操作(応募済み/ウォッチ/非表示/今回追加したマージ確定API)には権限チェックが一切無い。個人利用の現段階では実害は小さいが、複数ユーザー対応(Phase5)までに必ず対応が必要(CLAUDE.md 3節に記録)。

## 7. Discordゲートウェイ実接続・ボタン動作検証の準備(2026-08-05)

タスク16(Discord Bot Interaction実装)は「実装済み」扱いだったが、実際にはボタン付きメッセージが一度も送信されたことが無かったことが判明した。原因は2つ:

1. `NotificationService.send_opportunity_notification()`が`view`引数を受け取らず、常にEmbedのみを送信していた(`OpportunityActionView`を渡す経路自体が無かった)。
2. `scripts/verify_live_e2e.py`の`_OneShotClient`は`on_ready`で送信した直後に`self.close()`しており、Interaction(ボタン押下)を受信する前に切断していた。

以下を修正し、実際にゲートウェイ接続→Embed+ボタン付きメッセージ送信まで実機で確認した(`on_ready`ログでBotのユーザー名/IDが表示され、`message_id`/`jump_url`が返ることを確認済み)。

- `app/notification/discord_bot.py`: `send_opportunity_notification(channel_id, embed_dict, view=None)`にview引数を追加。
- `scripts/verify_live_e2e.py`: `--interaction-wait-seconds N`オプションを追加。N>0のとき、送信後もN秒間Botの接続を維持し、その間に届いたInteractionを`on_interaction`でログ出力する(custom_id・押した人・component_typeを表示)。`client.start()`を`asyncio.ensure_future`でタスク化し、`on_ready`発火とstart_task自体の異常終了(LoginFailure等)を`asyncio.wait(..., return_when=FIRST_COMPLETED)`で競合させる構成に変更(以前の「`client.start()`全体を単純に30秒でtimeout`」という書き方だと、待機時間を延ばした際に接続自体が30秒で強制終了してしまうため)。
- 実際に`--interaction-wait-seconds 0`(疎通確認のみ)・`15`(待機ロジック確認)の両方で実行し、正常終了・"Unclosed connector"警告の再発無しを確認済み。ボタンを実際に押しての検証(応募済み/ウォッチ/非表示それぞれの反応・DB反映確認)はユーザー自身がスマホのDiscordアプリで行う手順として`docs/live_verification_guide.md`「6. ボタンの実接続検証」に整備した(ボタンのコールバックがFastAPI(`api`サービス、`http://localhost:8001`)を叩くため、db/redis/worker/beatに加えて`api`サービスも起動しておく必要がある点を明記)。
- 全体テスト(unit 165 + integration 42)は無影響で全pass。

## 8. 常時起動Discord Bot(`app/bot/main.py`)追加(2026-08-05)

7節の`--interaction-wait-seconds`方式は「時間内にボタンを押さないといけない」という運用上の制約が大きいとの指摘を受け、代わりにworker/beatと同様の常時起動Botサービスを追加した。

- `app/bot/main.py`を新規作成。`discord.Client`を`client.run()`(`client.start()`ではなく)で起動する常時稼働プロセス。`client.run()`はdiscord.py側のデフォルトlogging設定とSIGINT/SIGTERMでの正常終了処理を自動で行うため、長時間稼働プロセスにはこちらが適する(7節で`client.start()`側に必要だった手動のタスク管理・タイムアウト競合処理が不要になる)。
- `docker-compose.yml`に`bot`サービスを追加(`build: .`・`restart: unless-stopped`・`env_file: .env`、api/worker/beatと同じ構成)。**`command: python -u -m app.bot.main`にする必要がある(`python -u app/bot/main.py`ではない)**: 後者はスクリプト自身のディレクトリがsys.path[0]になり`ModuleNotFoundError: No module named 'app'`でクラッシュループすることを実機で確認した(`restart: unless-stopped`のため気づかないうちに再起動を繰り返す状態になりかけた)。`-m app.bot.main`(モジュール実行)にすることでカレントディレクトリ(`/app`、Dockerfileの`WORKDIR`)がsys.path[0]になり解決した。
- `--send-test-notification`フラグを追加。指定時のみ`on_ready`後に1回`run_live_e2e_verification`(scripts/verify_live_e2e.pyと同じCeleryタスク)を実行し、その常駐Botの`Client`自身で`NotificationService`/`OpportunityActionView`を使って送信する。既定では送信しない(`restart: unless-stopped`によるクラッシュループ時の重複送信を防ぐため)。
- 実機で`--send-test-notification`付きでビルド・起動し、ゲートウェイ接続(discord.py本来のログ+自前のon_readyログ両方で確認)→テスト通知送信(message_id取得)→コンテナが再起動せず安定稼働、まで確認済み。
- **発見**: `OpportunityActionView`のボタンはcustom_idを明示していないため、Botプロセスの再起動を跨ぐと送信済みメッセージのボタンが無反応になる(3節に別タスクとして記録)。このため、テスト送信後は検証が終わるまで`--send-test-notification`付きの状態のままBotを再起動しないよう運用する必要がある(`docs/live_verification_guide.md`「6-A」に明記)。
- `scripts/verify_live_e2e.py`は使い捨ての単発検証専用のまま維持し、docstringに`app/bot/main.py`との役割の違いを明記した(混同防止、ユーザー指定)。
- `docs/live_verification_guide.md`「6. ボタンの実接続検証」を、6-A(常時起動Bot、推奨)/6-B(使い捨てスクリプト、代替)の2本立てに再構成した。
- 全体テスト(unit 165 + integration 42)無影響、全pass。

## 9. Discord Interaction実機検証 完了(2026-08-05)

8節の修正後、常時起動Bot経由でユーザーが実際にDiscordアプリから3ボタンを押し、以下を確認した。

- 「応募済みにする」→ ephemeral応答表示、`lottery_entries`に1行(`result=pending`)
- 「ウォッチリスト登録」→ ephemeral応答表示、`watchlists`に1行
- 「非表示」→ メッセージ書き換え、`opportunities.status='hidden'`

タスク16(Discord Bot Interaction実装)は、ゲートウェイ実接続・ボタン押下→FastAPI→DB反映までの経路を全てエンドツーエンドで実機確認済みとなった。

## 10. manual_review_tasksのDiscordボタン結線・実機検証完了(2026-08-05)

6節でCLI/API側の実装は完了していたマージ確定処理(`resolve_manual_review_task()`)に、Discordボタンからの入口を追加した。「照合を確定する」/「別商品として分離」の2ボタンを既存3ボタン(応募済み/ウォッチ/非表示)とは別枠(row=1)にし、`manual_review_task_id`が渡された場合のみ動的に追加する構成にした(`app/notification/interaction_view.py`)。

**ボタン表示条件の設計判断(ユーザー確認済み)**: `manual_review_tasks`は「仕入れ側(`ingest_normalized_item`)のProduct照合」がNEEDS_REVIEWになった時にだけ作成され、その候補Product IDは`Opportunity.product_id`と一致する。一方`Opportunity.match_status`自体は「相場側(MarketObservation)の独立した照合結果」からセットされるため、仕入れ側と相場側の照合結果が食い違うケースがあり得る(例: 仕入れ側はNEEDS_REVIEWで候補Product+manual_review_taskが作られたが、相場側は別のProductにAUTO_MATCHし、`Opportunity.match_status`はNEEDS_REVIEWにならない/その逆)。表示条件は**「`match_status==NEEDS_REVIEW`かつ該当pendingタスクが実在する」の両方を満たす場合のみ**とした(`app/scheduler/tasks.py:run_live_e2e_verification()`が両方をチェックした上で`manual_review_task_id`を結果に含める)。片方しか満たさないケースはボタンを出さない(取りこぼしうる既知の制約として記録)。

**誤操作防止**: いずれのボタンも直接は実行せず、まずephemeralな確認メッセージ(`_ManualReviewConfirmView`、「実行する」/「キャンセル」の再クリック方式、timeout=60)を挟む。

**実装中に発見したギャップ(CLI/APIも含め未実装だった)**: `resolve_manual_review_task()`はManualReviewTask.statusの更新のみ行い、`Opportunity.match_status`自体は一度も更新していなかった(`MatchStatus.MANUALLY_CONFIRMED`は定義時から「呼び出し側が設定する状態」と明記されていたが、呼び出し側の実装が無かった)。`app/pipeline/manual_review.py`に`_update_opportunity_match_status()`を追加し、confirmではreassign前のcandidate配下Opportunity全件をMANUALLY_CONFIRMEDへ、rejectではDIFFERENT_PRODUCTへ更新するようにした(同一SAVEPOINT内のためロールバックとも整合、`test_merge_failure_rolls_back_partial_reassignment`で確認済み)。これによりCLI/API経由の解決でも今後は`Opportunity.match_status`が正しく更新されるようになった。

**テスト**: unit/integration合わせて既存230件全pass。`tests/integration/test_manual_review.py`にOpportunity.match_status更新の確認(confirm/reject/ロールバック)を追加、`tests/integration/test_interaction_view.py`にボタン表示条件・確認ダイアログ・実行・キャンセル・二重解決時の409ハンドリングのテストを追加(12件)。

**実機検証(常時起動Bot経由、ユーザーが実際にDiscordアプリでボタン押下)**: 3パターンを実データで確認した。
1. **識別子無しでconfirm**: 「照合を確定する」→確認→「実行する」→ `manual_review_tasks.status=confirmed`、`opportunities.match_status=manually_confirmed`、`opportunities.product_id`がmatched側へ付け替え、candidate Productがsoft-delete。同じタスクへの2回目のクリックは`409`となり、ephemeralに失敗表示されることも確認(二重解決防止)。
2. **JAN付きでconfirm**: 同上に加え、`product_identifiers(type=jan, value=...)`がcandidateからmatched Product側へ実際に付け替わることを確認(要件「この時点でJAN/型番がproduct_identifiersへ書き込まれることを確認」に対応)。
3. **reject(別商品として分離)**: 「別商品として分離」→確認→「実行する」→ `manual_review_tasks.status=rejected`、`opportunities.match_status=different_product`、`product_id`は変更されず(マージ処理を一切呼んでいないことを確認)、candidate Productもsoft-deleteされない。

検証用に作成したProduct/Opportunity等(`一番くじ 鬼滅の刃～姉の仇～ B賞/C賞/D賞`、`[検証専用]`/`[検証専用2]`とラベル付けしたSource等)は、7節以前の`verification_synthetic`と同じ運用方針(実データ扱いのまま残す)に倣い、本番DBに残したままにしている。

**別タスクとして記録(今回のスコープ外)**: 6節で記録した「Discord操作には権限チェックが一切無い」は、今回追加した2ボタン(「照合を確定する」/「別商品として分離」)にも同様に当てはまる。マージ確定操作はもともと最も後戻りしにくい操作として記録済みだが、今回の実装でも`verify_api_key`(単一ユーザー前提)以外のユーザー単位の権限チェックは追加していない。複数ユーザー対応(Phase5)までに必ず対応が必要(3節に記録)。

## 11. on-line.1kuji.com商品一覧ページの自動収集追加・本番実機検証完了(2026-08-06、タスク18)

1.1節の通り、on-line.1kuji.comは商品詳細・応募ページのみBot対策で取得不可と判明し、商品一覧ページ(`ProductList.aspx`)を`app/collectors/sources/ichiban_kuji.py`の`target_urls`へホワイトリスト方式で追加した(実装詳細は1.1節参照)。ユーザーの指示により`worker`/`beat`コンテナを再起動して本番へ反映し、手動トリガーで実機検証した。

**デプロイ**: `docker compose restart worker beat`を実行。両コンテナとも正常に再接続(Redisブローカー接続・Celery Beatスケジューラ起動をログで確認)。

**実機検証結果**: `worker`コンテナ内で`run_ichiban_kuji_collector()`を直接呼び出し、6時間を待たず即座に実行した。

1. **新規発見件数**: `success_count=37`(内訳: 1kuji.comトップページ15件 + on-line.1kuji.com商品一覧22件、`error_count=0`、`collector_runs`テーブルにも記録済み)。1kuji.comトップページの15件と商品一覧の22件を商品名で突き合わせたところ、完全一致で重複していたのは5件(「一番くじ 進撃の巨人 ～選択と結果～」「一番くじ ゴジラ 最恐怪獣王列伝」「一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪」「一番くじ 『ミニオンズ＆モンスターズ』」「一番くじ ぷちきゅあ」)。したがって商品一覧ページ由来の**純粋な新規発見は17件**(1kuji.comのPICK UP ITEM(注目商品のみ抜粋)には出てこない商品を多数カバーできることを確認)。
2. **アクセス範囲の確認**: 実行結果`error_count=0`(=ホワイトリストガードが一度も発火しなかった、想定外URLへのアクセス試行が無かったことを意味する)に加え、コード上`self.fetch()`の呼び出し箇所は`fetch_bandaispirits_detail()`内(bandaispirits.co.jp向け)の1箇所のみで、`run()`(基底クラス)が`target_urls`(`ICHIBAN_KUJI_TOP_URL`・`ONLINE_1KUJI_PRODUCT_LIST_URL`の2つのみ、いずれもモジュール定数でハードコード)以外のURLでfetchを呼ぶ経路が構造的に存在しないことをコードレビューで確認した。`ProductList.aspx`以外のon-line.1kuji.comパスへのアクセスは発生していない。

**【重要・未実装であることが判明】この収集タスクはDBへ一切反映しない**: `run_ichiban_kuji_collector()`(Celeryタスク)は`collector.run()`(fetch→parse→normalize→validate)の実行結果を`collector_runs`テーブルへログ記録するのみで、`ingest_normalized_item()`を呼んでおらず、`products`/`release_events`への書き込みを一切行わない(タスク12の設計時点からの既存スコープ外判断であり、今回の変更による新たな制約ではない。1kuji.comトップページも従来から同様)。そのため、実行前後で`products`/`release_events`のレコード数に変化は無い(4件のまま、実行前後で確認済み)。新商品を実際にDBへ反映してOpportunity生成まで繋げるには、タスク13相当の「収集結果をDBへ反映するパイプライン」への結線が別途必要(現状は手動でのfetch_bandaispirits_detail()呼び出し等に依存)。

**運用上の含意**: 現時点では「新商品の自動発見」は収集ログ(`collector_runs`・アプリケーションログ)としてのみ可視化され、Discord通知やOpportunity生成には自動的には繋がらない。この収集結果を実際に活用する(例: 新規発見したpidを起点にbandaispirits.co.jp等で価格裏取りする、DBへ反映するパイプラインに繋ぐ)には、別タスクとして設計・実装が必要(CLAUDE.md 3節に記録)。**→ この欠落は12節で解消した。**

## 12. 定期実行タスクのDB反映・Opportunity生成・通知判定への結線(2026-08-06、タスク20)

11節で判明した「収集タスクはDBへ一切反映しない」問題について、駿河屋(`run_suruga_ya_price_rising_scan`)・ポケモンセンターオンラインの状況もユーザー指示で調査した。

**調査結果(着手前に報告済み)**: 一番くじだけの問題ではなく、Beat Scheduleに登録されている2タスク(`run_ichiban_kuji_collector`=6時間毎、`run_suruga_ya_price_rising_scan`=12時間毎)がいずれも同じ構造(`collector_runs`へのログ記録のみ)だった。タスク12設計時点での意図的なスコープ外判断であり、実際にDB反映→通知まで一気通貫するのはタスク19の`run_live_e2e_verification`(手動検証専用、Beat未登録)のみだった。ポケモンセンターオンラインは別の理由(商品一覧ページ未確認)でそもそもBeatに未登録(3節に記載済みの既知の制約、今回新たに判明したものではない)。

**設計方針**: ユーザー指示通り、タスク13・19で実装済みの`ingest_normalized_item()`/`match_observation_to_product()`/`build_and_score_opportunity()`/`evaluate_notification()`はいずれも変更せずそのまま呼び出す形にした。新規ロジックはオーケストレーション(ループ処理)のみ。
- `run_ichiban_kuji_collector`: `collector.run()`への依存をやめ(内部でNormalizedItemを呼び出し側へ返さない設計のため)、fetch→parse→normalizeを直接ループしてNormalizedItemを取得し、`ingest_normalized_item()`でDBへ反映するようにした。一番くじ側は相場データを持たないため、Opportunity生成・通知判定はここでは行わない。
- `run_suruga_ya_price_rising_scan`: 観測した買取価格を`match_observation_to_product()`で**既存**Productと照合し、一致すれば紐づく各release_events(価格確定済みのもの全件)について`build_and_score_opportunity()`/`evaluate_notification()`を実行するようにした(新規ヘルパー`_match_and_score_observation()`)。仕入れ側と相場側が独立に収集される以上、両者が自然に出会う結合点は「相場側が既存Productと照合できた時点」であり、一番くじCollector自身が駿河屋を検索しにいく設計にはしなかった(そのような設計は新商品発見直後は相場データが存在せずほぼ空振りになる上、依存関係が逆転し複雑になるため)。
- Discord送信は引き続きタスクの中では行わない(タスク19と同じ理由、同期Celeryタスクと非同期discord.pyゲートウェイの相性問題)。should_send=Trueの結果は`pending_notifications`(embed含む)としてタスクの返り値に含める。
- `_pending_manual_review_task_id()`を共通ヘルパーとして抽出し、`run_live_e2e_verification`(既存動作は変更せず、重複コードの置き換えのみ)でも使うようにした。

**実装中に発見・修正したテスト分離バグ**: `tests/unit/test_scheduler.py`は従来「サンドボックスは1kuji.com/suruga-ya.jpへのネットワークアクセスがブロックされている」という環境依存の前提でFetchErrorの発生を検証していたが、実際にはホスト環境から外部アクセスが到達可能な場合があることが判明した。ingest配線追加により、この網羅されていなかった「たまたま到達できる」経路が本番DBへ実データを書き込んでしまう実害を伴うようになったため(実際に発生した。下記参照)、`fetch()`/`search()`をmonkeypatchして常にFetchErrorを発生させる決定的なテストに書き換えた。またこれに伴い、`run_ichiban_kuji_collector`/`run_suruga_ya_price_rising_scan`とも「取得できたアイテムが1件も無い場合はDBセッションを開かない」ガードを追加した(無駄なSource行作成を避ける意図もある)。

**実装中に発生した実害と対処**: 上記バグ修正前に`tests/unit/test_scheduler.py`を実行した際、実際に1kuji.com/on-line.1kuji.comへ到達し、本番DBへ実データ(Source「一番くじ公式」・Product 32件・ReleaseEvent 36件)が書き込まれた。これは意図しない副作用だったが、データ自体は本番が本来持つべき正しい内容(実在の商品情報)だったため削除はせず、タスク20の実データによる動作証跡としてそのまま残している。ただしこれにより`tests/integration/test_e2e_pipeline.py`が独自に使っていた`collector_key="ichiban_kuji"`という値が本番の実Sourceと衝突しUNIQUE制約違反を起こしたため、テスト側の値を`"test_ichiban_kuji_e2e"`に変更した(本番側の値は`run_ichiban_kuji_collector`が実際に使う値のため変更していない)。

**実機検証結果(本番、`worker`/`beat`再起動後)**:
- `run_ichiban_kuji_collector`を手動トリガーし、Product 32件・ReleaseEvent 36〜40件が実際にDBへ反映されることを確認した(1kuji.comトップページ15件+商品一覧22件、重複分は同一Productへ統合)。
- `run_suruga_ya_price_rising_scan`を手動トリガーしたところ、既定の巡回キーワード(「一番くじ」「ポケモンカード」、`purchase_hendou=価格上昇中`フィルタ)では新規Opportunityは0件だった。個別に「一番くじ ゴジラ MACHINE CHRONICLE」等6商品名で駿河屋を直接検索したところ、いずれも実際の買取価格観測は取得できたが、駿河屋側の商品タイトルは「孫悟空＆ブルマ＆クリリン 潜水艇「一番くじ ドラゴンボール EX 対決!レッドリボン軍」D賞 フィギュア」のような個別景品名(シリーズ名を含むがそれ以外の文字列が多い)であり、既存のProduct Matcher(`SequenceMatcher`による全体文字列の編集距離ベース類似度)ではシリーズ名一致分のスコアが埋もれてNEEDS_REVIEW閾値(40点)に届かないことを実データで確認した(score=0〜5)。**これは今回の結線作業とは独立した、Product Matcherの一番くじ商品に対する既存の照合精度の限界であり、新たな既知の課題として3節に記録した。**
- 上記の理由で自然な自動マッチは発生しなかったため、実際の買取価格観測(価格・URL・信頼度は全て実データ)のtitleのみ検証目的でシリーズ名(Product名)と完全一致させ、`_match_and_score_observation()`を直接呼び出して結線そのものの動作を検証した。結果、`opportunities`テーブルへ実際に1件反映(`match_status=needs_review`、score=0.49)、`evaluate_notification()`がshould_send=Trueと判定し、実際にDiscordへEmbed+ボタン付きメッセージを送信できることを確認した(message_id取得済み)。
- **副産物として発見した既存バグ**: `app/pipeline/notify.py:evaluate_notification()`の`OpportunityView`構築で、「最良売却先」欄が`best_channel_name=source.name`(仕入れ側のSource名)になっており、`build_and_score_opportunity()`に渡した実際の売却チャネル名(`channel_name`引数、例:`"suruga_ya"`)が表示に反映されない。今回のタスクとは無関係な既存コードの問題のため修正はしていない(3節に記録)。

**別タスクとして記録(今回のスコープ外)**:
- ~~Discord自動送信の仕組みが未実装~~ → **緊急対応・解消済み(15節、2026-08-06、タスク23)**
- Product Matcherの一番くじ商品(個別景品タイトル)に対する照合精度: 上記の通り、シリーズ名一致だけでは現状スコアが伸びない。改善するなら例えば「観測タイトルがProduct名を部分文字列として含む場合の加点」等の見直しが考えられるが、商品照合の確定ロジックに関わる変更のため、着手前に協議が必要(CLAUDE.md 0.5)。
- `evaluate_notification()`の「最良売却先」表示バグ。

**テスト**: unit/integration合わせて243件全pass。`tests/unit/test_scheduler.py`をネットワーク・DB非依存の決定的なテストに書き換え、`tests/integration/test_e2e_pipeline.py`のcollector_key衝突を解消した。

## 13. タスク20の事故データの再収集検証・テストDB分離設計の記録・ポケセン新商品一覧ページ発見(段階1)(2026-08-06、タスク21)

12節の事故・既知課題を受けて、ユーザー依頼により以下4点に対応した。

### 13.1 タスク20の事故データ(Product 32件・ReleaseEvent 36件)の再収集時デデュープ検証

`tests/integration/test_incident_recollection_dedup.py`を新規追加し、実際に本番DBへ書き込まれたこの36件のReleaseEvent全件について「もう一度同じページを収集したら」を`db_session`フィクスチャ(ネストしたトランザクション+rollback、conftest.py)の中で再現して検証した(本番の実データに対して安全に検証できる設計、13.2節参照)。

- 全件が`match_or_create_product()`のexact_match経路(商品名の完全一致)でAUTO_MATCHとなり既存のProduct/ReleaseEventへ正しく紐づき、Product/ReleaseEventのいずれも増えないことを確認した(再収集前後で32件/36件のまま)。
- **同時に判明した限界**: 一番くじ商品は識別子(JAN/型番)を持たず(1.1節)、`Product.release_date`もどこからも書き込まれないため、`calc_match_score()`側の名前類似度による加点は理論上の上限が+40点(`NEEDS_REVIEW_THRESHOLD`と同値)にしかならず、AUTO_MATCH(90点)/HIGH_PROBABILITY_MATCH(70点)には**原理的に届かない**。つまり一番くじ商品の重複防止は事実上`match_or_create_product()`のexact_match(文字列完全一致)経路のみに依存しており、fuzzy matching側はこれを一切補完できない。空白のゆれ自体は`_title_similarity()`が事前に空白を除去するため無害だが、それ以外の表記変動(全角/半角、記号の差、サイト側のtitle文言変更等)でexact_matchが外れた場合、確実にNEEDS_REVIEWへ落ちて重複Productが作られる。この限界を`test_recollecting_with_whitespace_variation_relies_entirely_on_exact_match_path`として現状の実際の挙動を記録する回帰テストの形で残した(「直すべきバグ」としては扱わず、商品照合の確定ロジックに関わる変更のため着手前に協議が必要、CLAUDE.md 0.5・282行目の駿河屋照合精度の課題と同根)。

### 13.2 テスト用DBが本番と分離されていない設計上のギャップ(記録のみ、対応はしていない)

専用のテスト用DBが存在せず、`tests/integration/conftest.py`の`db_session`フィクスチャは本番と全く同じ`app.config.settings.database_url`に接続し、ネストしたトランザクション+rollbackのみで隔離している(接続先そのものは本番DBと同一)。この設計自体はSQLAlchemyの標準的なテスト分離パターンであり、`db_session`を経由する限り安全に機能する(13.1節の検証はこの隔離のもとで安全に行えている)。

**12節の事故の本質はまさにこの前提が崩れたケースだった**: `tests/unit/test_scheduler.py`はCeleryタスク関数を直接呼び出す設計で、`db_session`フィクスチャを一切経由せず、タスク内部が独自にDBセッションを開く(アプリ本体と同じ経路で本番へ直接接続する)。そのためrollback保護の外側で本番へ実際に書き込まれた。**今後も同じ構造のリスクが残っている**: `db_session`を経由しないテスト(Celeryタスクや将来追加されるスクリプト類を直接呼ぶテスト全般)は、書き込みが本番へ確定してしまう経路になりうる。タスク20では該当箇所をnetwork層のmonkeypatchで塞いだ(12節参照)が、これは個別対応であり、同種のテストが今後増えるたびに同じ注意が必要になる。

**将来的な検討課題**: `docker-compose.yml`にテスト専用DBサービス(例: `test-db`、本番`db`とは別のPostgreSQLコンテナ+別ボリューム)を追加し、テスト実行時は`DATABASE_URL`をそちらに向ける構成にすれば、rollbackに頼らずコンテナレベルで本番データと完全に分離できる。現時点では未着手(この記録のみ)。

### 13.3 ポケモンセンターオンライン新商品一覧ページの発見・段階1実装

3節の「商品一覧ページ未確認」が長らく未調査のまま残っていたため、実際にトップページのナビゲーションから調査した。詳細な検証結果・設計判断は`app/collectors/sources/pokemon_center_online.py`のモジュールdocstring「新商品一覧ページの発見・段階1対応(2026-08-06)」に記載済み。要点のみここに記録する。

- 新商品一覧ページ`https://www.pokemoncenter-online.com/search/?prefn1=releaseType&prefv1=1&srule=top-new-product`を発見。Bot対策なし、Salesforce Commerce Cloud構成。
- **ページング調査結果(ユーザーからの確認依頼への回答)**: 初期表示は1ページ40件・全8ページの「もっと見る」型(`<div class="grid-footer" data-page-size="40.0">`+`<select name="page">`)。ただし実際にcrawler観点で使う場合、`start`パラメータは常に0として扱われ、`sz`パラメータのみが「累積で何件返すか」を制御する(`sz=500`→275件でそれ以上増えない=2026-08-06時点の全新商品件数)ことを確認した。**41件で全件ではなく、275件が実際の全件だった。** 複数ページを順に辿る必要はなく、`sz`を十分大きくした1回のリクエストで全件を取得できるため、`NEW_PRODUCT_LIST_URL`定数に`start=0&sz=500`を固定で付与した1本のURLとして実装した(ユーザー確認前の自己判断、CLAUDE.md 0.5の「タスクの粒度」相当の解釈統一として記録)。`sz=500`は現在件数に余裕を持たせた固定値であり、将来275件を超えた場合は取りこぼしうる(致命的ではないが要継続監視)。
- **段階1のスコープ(ユーザー指示通り、ここで止めた)**: `target_urls`への追加(ホワイトリスト方式、`fetch()`が`/search/`配下はこの1URLの完全一致のみ許可)、一覧ページからの商品コード抽出(`extract_new_product_codes()`)、各コードに対する個別ページの取得・パース・正規化(`discover_new_products()`が既存の`fetch_product()`を再利用)までを実装した。**Beat Scheduleへの登録・DB反映(`ingest_normalized_item()`等への結線)は行っていない。**
- **基底クラスとの整合性についての設計判断**: `SourceCollector.run()`は「1URL=1回のfetchでParsedItemが得られる」単層設計だが、ポケセンの新商品一覧ページには「各種期間」(締切・当選発表・購入期限、このCollectorの存在意義そのもの)が無く、商品ごとに個別ページへの追加fetchが必要(2段階)。そのため`parse()`はこのURLに対して意図的に空リストを返し(誤ってrun()経由で不完全なParsedItemが生成されるのを防ぐ)、実際の発見・取得は新設した`discover_new_products()`で行う設計にした。タスク20で`run_ichiban_kuji_collector`が`collector.run()`に頼らず専用ループを組んだ(12節)のと同じ考え方であり、段階2でBeat Schedule結線する際も同様の専用ループを想定している。
- **実データでの検証**: 実際に取得した生HTML(`tests/fixtures/raw_html/raw_pokemon_center_new_product_list.html`、275件)・個別ページ1件(`raw_pokemon_center_new_product_list_sample_detail.html`、一覧発見分のコード`4521329413075`)をFixtureとして追加し、既存の`parse()`にコード変更無しで実データが正しく通ることを確認した上でテストを書いた。新規テスト5件追加(unit 29件中)、`fetch()`のホワイトリストガード・`extract_new_product_codes()`の275件抽出・`discover_new_products()`のオーケストレーション(モックしたfetch()で実通信無しに検証)を含む。全体テストは250件(unit+integration)全pass。
- **段階2として別途依頼予定(このタスクでは対応しない)**: Beat Scheduleへの登録、`ingest_normalized_item()`等へのDB反映結線。275件を毎回individual fetchするのは実行コストが高いため、段階2では「DB未登録の新規コードのみfetchする」等の差分化の検討も必要(モジュールdocstring参照、現時点では未対応)。

## 14. ポケモンセンターオンラインのBeat Schedule登録・DB反映結線(段階2)・実機検証(2026-08-06、タスク22)

13.3節の段階1を受けて、ユーザー指示によりBeat Schedule登録・DB反映結線・実機検証まで進めた。

### 14.1 実装内容

- `app/scheduler/celery_app.py`の`beat_schedule`に`pokemon-center-online-every-6-hours`(6時間毎、一番くじと同じ間隔)を追加した。
- `app/scheduler/tasks.py`に`run_pokemon_center_collector()`を追加した。設計は`run_ichiban_kuji_collector()`(タスク20)を完全に踏襲する: `discover_new_products()`(段階1)の結果を`ingest_normalized_item()`(タスク13、変更無し)でDBへ反映するのみで、Opportunity生成・通知判定はここでは行わない(ポケセンも仕入れ側(SourceCollector)で相場データを持たないため、相場側`run_suruga_ya_price_rising_scan`が`match_observation_to_product()`で突き合わせた時点で自然に行われる、一番くじと同じ結合点)。
- **実装中に発見・修正した堅牢性の欠落**: 段階1の`discover_new_products()`は275件中1件でもFetchError/ParseErrorが起きると全体が失敗する設計だった(個別ページのtry/exceptが無かった)。275件規模の巡回では一部の商品が終売等で個別に失敗することが実運用で起こりうるため、コードごとにtry/exceptで捕捉し処理を継続する形に修正した(戻り値を`list[NormalizedItem]`から`(list[NormalizedItem], list[str])`のタプルに変更)。単体テスト2件(全件成功/一部失敗で継続)で確認済み。
- `tests/unit/test_scheduler.py`の`_block_real_network_access`(autouse fixture)に`PokemonCenterOnlineCollector.fetch`のmonkeypatchを追加した(12節の事故の教訓、CLAUDE.md 13.2節参照。新しいCollectorをtest_scheduler.pyで検証する際は必ずこのガードに追加すること)。

### 14.2 本番実機検証結果

`worker`/`beat`コンテナを再起動して反映後、`worker`コンテナ内で`run_pokemon_center_collector()`を直接呼び出した。

- **DB反映**: `success_count=275, error_count=0, ingested_count=275`。Source「ポケモンセンターオンライン」・Product 275件・ReleaseEvent 275件が実際にDBへ反映されたことを確認した(全件、失敗0件)。うち270件は商品コード先頭2桁が45/49でJAN識別子が`product_identifiers`へ永続化され、残り5件(Nintendo Switch本体同梱版等、"99"始まり等)はJANを持たない。
- **駿河屋との突合・通知到達確認**: 続けて`run_suruga_ya_price_rising_scan()`を手動トリガーした(既存の巡回キーワードに元々「ポケモンカード」が含まれていたため、コード変更無しでポケセン産のProductとも自然にマッチする設計になっていた)。実際に1件のOpportunityが生成され(「【抽選販売】ポケモンカードゲーム MEGA スターターセットex イーブイex構築デッキ」、仕入価格¥1,800)、`evaluate_notification()`が`should_send=True`と判定、Discord Embed(dict)が正しく組み立てられるところまで実データで確認した。**実際のDiscordへの送信(message_id取得)は今回行っていない**(3節に記録済みの「Discord自動送信の仕組みが未実装」という既存の制約と同じ理由で、常時起動Botが`pending_notifications`を自動で拾って送るルートはまだ無い。手動でscripts/verify_live_e2e.py相当の送信を行うかはユーザー判断待ち)。
  - この1件のEmbedでも既知バグ(3節「`evaluate_notification()`の最良売却先表示バグ」)が同様に再現することを確認した:「最良売却先」欄が仕入先と同じ「ポケモンセンターオンライン」になっている(修正はしていない、既存の記録通り)。
  - 想定利益は¥-500(マイナス)だった。これは実際の駿河屋買取価格が仕入価格を下回る組み合わせがたまたま拾われたことによる実データであり、Profit Engineの不具合ではない。
- **重複防止の実データ比較検証(①との対比、ユーザー依頼)**: `tests/integration/test_pokemon_center_recollection_dedup.py`を新規追加し、実データ275件に対して3パターンを検証した。
  1. 275件全件の同一内容再収集がAUTO_MATCH/HIGH_PROBABILITY_MATCHとなり、Product/ReleaseEventとも増えないことを確認(①の一番くじ32件検証と同じ形)。
  2. JAN識別子を持つ商品(270/275件)は、商品名を全く別物に変えてもJAN一致(+100)によりAUTO_MATCHを維持することを確認した。**一番くじ(識別子なし、商品名が変わると即NEEDS_REVIEW)と比べて明確に重複防止が強い。**
  3. 一方、識別子を持たない5件(Nintendo Switch本体同梱版等)は、全角スペース1文字程度の軽微な表記ゆれでも一番くじと全く同じくNEEDS_REVIEWへ落ちることを確認した。**「ポケセンは常に一番くじより重複防止が強い」は正確ではなく、実際には「JAN識別子を取得できた商品(270/275件、98%)に限り強い」というのが正確な理解。**

### 14.3 テスト

unit/integration合わせて257件全pass(既存250 + `test_scheduler.py`拡張3件 + `discover_new_products()`堅牢性2件 + `test_pokemon_center_recollection_dedup.py`3件)。

### 14.4 別タスクとして記録(今回のスコープ外)

- 275件全件を毎回individual fetchする実行コスト(13.3節から継続): 差分化の検討は未着手。
- ~~Discord自動送信の仕組みが未実装~~ → **緊急対応・解消済み(15節、2026-08-06、タスク23)。このセクションで生成された「MEGA スターターセットex イーブイex構築デッキ」のOpportunityが、実際にこの仕組みで送信されたことを確認済み。**
- `evaluate_notification()`の「最良売却先」表示バグ(3節に記録済み、ポケセンのデータでも再現を確認しただけで修正はしていない)。

## 15. 【緊急対応】pending_notifications自動配信の実装(2026-08-06、タスク23)

### 15.1 発覚した欠落の重大性

タスク20〜22で一番くじ/駿河屋/ポケセンの収集→DB反映→商品照合→Opportunity生成→通知判定(`evaluate_notification()`)までは実際に動作することを確認していたが、**`should_send=True`と判定された結果はCeleryタスクの戻り値(dict)に含まれるだけで、これを自動的に拾ってDiscordへ実際に送信する仕組みがどこにも存在しなかった**。常時起動Bot(`app/bot/main.py`)は`--send-test-notification`起動時に1回送るのみで、収集タスクの結果を定期的に取得する経路を持っていなかった。

これは「収集」「照合」「利益計算」「通知要否判定」という個々の要素がいくら正しく動いていても、**最終的にユーザーの手元(Discord)に情報が届かなければ、このツール全体の目的(収集→通知)が達成されない**という意味で、他の未解決課題(駿河屋の照合精度限界、最良売却先表示バグ等)とは性質が異なる致命的な欠落だった。ユーザー指摘により発覚し、最優先で緊急対応した。**この欠落が解消されるまで、一番くじ・駿河屋・ポケセンの自動収集(タスク20〜22)は「動いてはいるが実際には何の役にも立っていない」状態だった。**

### 15.2 設計

- **新テーブル`pending_notifications`**(`app/db/models/pending_notification.py`、マイグレーション`7b788e3dd59e`): `evaluate_notification()`がshould_send=Trueと判定した時点の`embed`(JSONB)・`opportunity_id`・`release_event_id`・`channel_id`・`manual_review_task_id`・`dedupe_key`(監査用)を保存する送信キュー。status(`pending`/`sent`/`failed`)・`attempt_count`・`last_error`で送信状態を追跡する。
- **書き込み側**: `_match_and_score_observation()`(app/scheduler/tasks.py、駿河屋スキャンから呼ばれる)がshould_send=Trueの都度1行作成する。一番くじ/ポケセンの収集タスクはOpportunity生成自体を行わない設計(12節・14節参照)のため、書き込み箇所はここ1箇所のみ。
- **読み出し・送信側**: `app/notification/dispatcher.py:dispatch_pending_notifications()`が、status=pendingの行を作成日時の古い順に送信する。send_fn(非同期callable)を呼び出し側から注入する設計にし、実際のDiscordゲートウェイ接続を持たないテストからも検証できるようにした(`app/notification/interaction_view.py`のhttp_client注入と同じ考え方)。

**常時起動Bot側のポーリング(Celery Beatではなくこちらを選んだ理由)**: ユーザーからは「Celery Beatに新タスクとして追加するか、常時起動Bot側で定期チェックするか、実装しやすい方で構わない」と選択の余地を与えられたが、後者を選んだ。理由: `OpportunityActionView`(応募済み/ウォッチ/非表示ボタン等)はcustom_idを永続化しておらず、送信元の`discord.Client`インスタンスが生き続けている間しかボタンが反応しない(3節に既知の制約として記録済み)。Celery Beatタスクが毎回使い捨てのdiscord.Client(`scripts/verify_live_e2e.py`の`_OneShotClient`と同じパターン)で送信→即切断する設計だと、送信直後にボタンが恒久的に無反応になり、タスク9・10で実機確認済みのボタン操作(応募済みにする/ウォッチ/非表示/照合確定/別商品分離)が機能しなくなる。既に常時接続し続けている`app/bot/main.py`が自分自身のイベントループ内で`discord.ext.tasks.loop(minutes=15)`により定期ポーリングする設計にすることで、この問題を避けた。

**ポーリング間隔(15分、ユーザー指定)**: 一番くじ/ポケセン(6時間毎)・駿河屋(12時間毎)という収集タスク自体の巡回間隔とは意図的に独立させている。収集が完了してから通知が届くまでの遅延を「次の収集サイクルまで」ではなく「次のポーリングサイクル(最大15分)まで」に抑えることで、収集できても通知側の取りこぼしで半日近く遅れる機会損失を避ける(ユーザー指定の設計意図)。

**session.commit()の扱い(重要な設計判断)**: `dispatch_pending_notifications()`は行単位で`session.commit()`する(他の多くのモジュールが`session.flush()`のみで呼び出し側にcommitを委ねているのとは意図的に異なる)。理由: 複数行をまとめて処理する際、後続の行の送信で例外が起きてバッチ全体がロールバックされると、「Discordへは実際に送信済みだがDBにはpendingのまま残ってしまう」行が発生し、次回ポーリングで再送(重複送信)してしまう。1メッセージ送信成功の直後に確定させることで、この重複送信を防いでいる。

### 15.3 実装中に検証した安全性(SQLAlchemyのトランザクション境界)

`dispatch_pending_notifications()`が内部で`session.commit()`を呼ぶ設計のため、これを`tests/integration/conftest.py`の`db_session`フィクスチャ(ネストしたトランザクション+rollbackで隔離)経由でテストして安全か(=commit()がrollback保護を突き破って本番へ実際に書き込んでしまわないか)を、実装前に実験して確認した。`connection.begin()`で開始した外側のトランザクションに対し、`sessionmaker(bind=connection)`で作られたセッションの`commit()`は、**別の独立したDB接続からはコミット直後であっても一切見えない**(=DBレベルの実コミットにはならず、外側のトランザクションの範囲内にとどまる)ことを確認した。これにより、`dispatch_pending_notifications()`の内部commitを含む一連の処理を、本番を汚染するリスク無く`db_session`フィクスチャでテストできることが分かった(12節・13.2節の事故の教訓を踏まえた確認)。

### 15.4 テスト

- `tests/integration/test_dispatcher.py`(新規6件): 送信成功時のstatus更新、**重複送信防止の直接確認(1回目のポーリングで送信済みになった行は2回目のポーリングで再送されない)**、送信済み/失敗確定済みの行はスキップされること、1件の失敗が残りの処理をブロックしないこと、max_attempts到達でstatus=failedになりそれ以降二度と拾われないこと、作成日時の古い順に処理されること。
- `tests/integration/test_pending_notification_creation.py`(新規1件): `_match_and_score_observation()`がshould_send=Trueの際に実際に`pending_notifications`へ1行作成すること、同一内容の再評価(dedupe判定でUNCHANGED)では2件目が積まれないこと(Redis側のdedupeとDB側の配信キューの整合性)。
- 全体テストは264件全pass。

### 15.5 本番実機検証(2026-08-06)

タスク22で生成済みだった「【抽選販売】ポケモンカードゲーム MEGA スターターセットex イーブイex構築デッキ」のOpportunity(pending_notificationsテーブル新設前に生成されたため、そのままでは配信キューに存在しない)を使い、実際にこの仕組みで送信されるところまで確認した。

1. `worker`コンテナを再起動(新しい`tasks.py`を反映)。
2. 該当release_eventのRedis dedupeキー(`notification:{release_event_id}`)を削除し、「未通知」の状態に戻した。
3. `run_suruga_ya_price_rising_scan()`を再実行し、`pending_notifications`へ実際に1行(status=pending)作成されたことをDBで確認した。
4. `bot`コンテナを再起動。`discord.ext.tasks.loop`は`.start()`直後に即座に1回目を実行する仕様のため、15分待たずに`on_ready`後すぐにポーリングが走った。ログに`[通知配信] checked=1 sent=1 failed=0 errors=[]`が出力され、該当行のstatusが`sent`・`discord_message_id`に実際のメッセージID(`1534818839080992860`)が入ったことをDBで確認した。**実際にDiscordへ送信された。**

**その他の観察事項(参考記録、今回のスコープ外)**: この検証中、`bot`コンテナの再起動のたびに`--send-test-notification`相当の動作(`run_live_e2e_verification`によるテスト通知送信)も毎回発生していることに気づいた。現在の`docker-compose.yml`の`bot`サービスの`command`は`python -u -m app.bot.main`(フラグ無し)だが、実際に稼働中のコンテナは`docker compose restart`ではなく過去に`--send-test-notification`付きで作成されたままの可能性がある(`restart`はコンテナ作成時のcommandを再利用し、compose.ymlの現在の内容を再読み込みしない)。8節の設計意図(「`restart: unless-stopped`によるクラッシュループ時の重複送信を防ぐため、既定では送信しない」)に照らすと、意図せず毎回テスト通知が飛ぶ状態になっている可能性がある。今回のタスクとは無関係のため確認・修正はしていないが、次にBotコンテナに触れる際は`docker compose up -d bot`(再作成)で現在のcompose.ymlのcommandに揃えることを検討する価値がある。

### 15.6 別タスクとして記録(今回のスコープ外)

- 上記「bot再起動のたびにテスト通知が飛んでいる可能性」の確認・是正。
- `pending_notifications`の運用監視(`failed`件数の可視化。`scripts/collector_status.py`相当のCLIは今回作っていない)。
