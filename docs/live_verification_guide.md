# 実データE2E検証ガイド(タスク19)

Phase 0のタスク1〜18まで完了し、Collectorの実データ検証も一通り終わった段階での、
「実際にDiscordへ通知が届くか」「Celery Beatで定期実行が回るか」を確認するための手順。

ユーザーはPCを持たず、スマホからのリモートSSHでのみ操作する前提でまとめてある
(コマンド数最小・tmuxでSSH切断に強く・長時間の監視は不要)。

## 全体の流れ

1. (初回のみ)tmuxをインストールする
2. (初回のみ)`.env`にDiscord Botのトークン・通知先チャンネルIDを設定する
3. tmuxセッションを開始し、検証スクリプトを1つ実行する
4. Discordアプリで通知が届いたか確認する
5. (任意)worker/beat・tmuxセッションを停止する

---

## 1. tmuxのセットアップ

### インストール(未インストールの場合)

```bash
sudo apt-get update && sudo apt-get install -y tmux
```

### セッションの作成

```bash
tmux new -s verify
```

これで`verify`という名前のtmuxセッションに入る。この中で以降のコマンドを実行すれば、
**SSH接続が途中で切れてもセッションの中身(実行中のスクリプト)は止まらずに残り続ける。**

### 既存セッションへの再接続

SSHが切れた後、もう一度スマホから接続し直した場合:

```bash
tmux attach -t verify
```

### セッションからの一時離脱(処理を止めずに端末だけ抜ける)

tmuxセッション内で `Ctrl-b` を押してから `d` を押す(離しても良い)。
セッション自体は裏で動き続ける。戻るときは上記の`tmux attach -t verify`。

---

## 2. `.env`に設定が必要な項目

`~/Shohin-Research/.env`に以下の2項目を設定する必要がある(**現在は両方とも未設定**)。

### 推奨: 検証用に本番とは別のDiscordサーバー・チャンネルを使う

このスクリプトは実際にDiscordへメッセージを送信する。本番で使う予定のサーバーではなく、
**自分だけが見える検証用のサーバー(または既存サーバー内の検証用チャンネル)を
別途1つ用意しておく**ことを強く推奨する。動作確認用の合成データ(実際の買取相場ではない
プレースホルダー価格)を含む通知が送られるため、他の人が見るチャンネルに混ざると紛らわしい。

サーバーが無ければ、Discordアプリで「サーバーを追加」→「オリジナルの作成」→
「自分と友達のため」で数秒で作れる。

### `DISCORD_BOT_TOKEN`の取得手順

1. スマホのブラウザで https://discord.com/developers/applications を開き、Discordアカウントでログインする
2. 右上(または画面上部)の「**New Application**」をタップし、アプリ名(何でも良い。例: `ShohinResearchBot`)を入力して作成する
3. 左側のメニュー(ハンバーガーメニューの場合あり)から「**Bot**」を選ぶ
4. 「**Reset Token**」(初回は「Add Bot」の場合もある)をタップし、表示されたトークンを**その場でコピーしておく**(このトークンは後から再度表示できない。忘れた場合は再度Reset Tokenが必要)
5. 同じBotページで、下の方にある「**Privileged Gateway Intents**」の3項目
   (PRESENCE INTENT / SERVER MEMBERS INTENT / MESSAGE CONTENT INTENT)は
   **すべてOFFのままで良い**(本検証スクリプトはこれらを使わない)
6. コピーしたトークンを`.env`の`DISCORD_BOT_TOKEN=`の後ろに貼り付ける

```
DISCORD_BOT_TOKEN=（ここに貼り付け）
```

### Botを検証用サーバーに招待する

1. 同じDeveloper Portalの左メニューから「**OAuth2**」→「**URL Generator**」を開く
2. 「SCOPES」で「**bot**」にチェック
3. 下に表示される「BOT PERMISSIONS」で以下にチェック:
   - **View Channels**(チャンネルを見る)
   - **Send Messages**(メッセージを送信)
   - **Embed Links**(埋め込みリンク。通知はEmbed形式のため必須)
4. 一番下に生成されたURLをタップ(またはコピーしてブラウザで開く)
5. 招待先の選択画面で、手順0で用意した検証用サーバーを選び、「認証」する

### `DISCORD_NOTIFY_CHANNEL_ID`の取得手順

1. Discordアプリの設定 → 「**詳細設定**」(Advanced) → 「**開発者モード**」をONにする
2. 検証用サーバーの、通知を送りたいチャンネルを長押し(スマホ)またはタップして開くメニューから
   「**チャンネルIDをコピー**」を選ぶ
3. コピーした数字の羅列を`.env`の`DISCORD_NOTIFY_CHANNEL_ID=`の後ろに貼り付ける

```
DISCORD_NOTIFY_CHANNEL_ID=（ここに貼り付け、数字のみ）
```

設定後、`.env`は以下のようになっているはず(値は伏せている):

```
DISCORD_BOT_TOKEN=<トークン>
DISCORD_NOTIFY_CHANNEL_ID=<チャンネルID>
```

---

## 3. 実行

tmuxセッションの中で(上記1参照)、リポジトリのルートに移動して1コマンド:

```bash
cd ~/Shohin-Research
bash scripts/verify_live_e2e.sh
```

このスクリプトが自動で行うこと:

1. `docker compose up -d db redis` でDB/Redisを起動(healthyになるまで待機)
2. `alembic upgrade head` でスキーマを最新化(何度実行しても安全)
3. `docker compose up -d worker beat` でCelery worker/beatをバックグラウンド起動
4. 定期タスク(`run_ichiban_kuji_collector`)を1回だけ手動triggerし、
   Celeryのブローカー接続・タスク実行が実際に機能することを確認
5. `scripts/verify_live_e2e.py` を実行し、
   「bandaispirits.co.jpから実際に商品ページを取得→DBへ反映→商品照合→
   Profit Engine→Opportunity生成→通知判定→Discord送信」を一気通貫で実行
6. 各段階の成否をまとめて表示

実行時間の目安は1〜2分程度(Dockerイメージのビルドが初回のみ発生する場合は+数分)。

**worker/beatはこのスクリプトが終わった後もバックグラウンドで動き続ける。**
これは「Celery Beatで定期実行が回るか」を実際の6時間/12時間サイクルで確認できるように
あえて残す設計(停止したい場合は下記「クリーンアップ手順」を参照)。

### 実行結果の見方

最後に以下のいずれかが表示される:

- `[ OK ] 送信成功: message_id=... channel_id=...` → Discordへ実際に送信できた
- `[ NG ] 送信失敗: ...` → どこで失敗したかの理由が具体的に表示される
  (下記「うまく届かなかった場合」を参照)

いずれの場合も最後に **「Discordのチャンネルを確認してください。」** と表示される。
実際に届いたかどうかの最終判断は、スマホのDiscordアプリで検証用チャンネルを見て行うこと。

### 【重要】通知内容について

送信される通知は、`一番くじ 鬼滅の刃～姉の仇～`という実在の商品を実際に
bandaispirits.co.jpからfetchした実データだが、**買取/転売価格の部分だけは
合成データ(プレースホルダー)** になっている。これは「同じ商品を扱う実在の買取
リストを自動的に見つける仕組み」がまだ無いため([一番くじ側と駿河屋側等の商品対応関係は
未確立](../CLAUDE.md)、要検証項目として残っている)、確実に一連の動作を最後まで確認できる
ようにするための設計。Embed内の「商品照合」欄に「要確認」、仕入先/最良売却先に
「[検証専用]」という表記が出るのはこのため(実際のOpportunity通知と混同しないように
なっている)。

---

## 4. クリーンアップ手順

### worker/beatの停止

検証が終わって定期実行の確認も済んだら、以下でCelery worker/beatを停止できる
(db/redisは残しておいても問題ない):

```bash
cd ~/Shohin-Research
docker compose stop worker beat
```

完全に消したい場合(コンテナ自体を削除):

```bash
docker compose down
```

(`down`はdb/redisのコンテナも含めて全部停止・削除する。データは`postgres_data`という
Docker Volumeに残るため、次回`docker compose up -d db redis`すればデータは引き継がれる。
Volumeごと消したい場合のみ`docker compose down -v`。**`-v`は本番データも消えるため、
Phase0の検証目的以外では使わないこと。**)

### tmuxセッションの終了

tmuxセッションの中で:

```bash
exit
```

または、セッション名を指定して外から終了させる:

```bash
tmux kill-session -t verify
```

---

## 5. うまく通知が届かなかった場合のチェックポイント

`scripts/verify_live_e2e.py`は失敗の種類ごとに具体的なメッセージを表示するが、
まず確認すべき代表的な詰まりどころを以下にまとめる。

| 症状 | 原因の可能性 | 確認方法 |
|---|---|---|
| `discord.LoginFailure` / ログイン失敗 | `DISCORD_BOT_TOKEN`が間違っている・コピーミス・Reset Tokenで無効化された旧トークンのまま | Developer Portal → Bot → Reset Tokenで再発行し、`.env`を更新 |
| `discord.NotFound` / チャンネルが見つからない | `DISCORD_NOTIFY_CHANNEL_ID`が間違っている、または開発者モードでコピーしたのが別の種類のID(サーバーIDやメッセージID)になっている | Discordアプリでチャンネルを開発者モードから開き直して再度コピー |
| `discord.Forbidden` / 権限不足 | Botをサーバーに招待した際に`Send Messages`/`Embed Links`/`View Channels`の権限を付け忘れた、またはそのチャンネルだけ個別に権限が制限されている | 「2. Botを検証用サーバーに招待する」を参照し、OAuth2 URLを権限を付け直して再生成→再招待。または該当チャンネルの権限設定を確認 |
| `on_readyが30秒以内に発火しない`(タイムアウト) | VPSからDiscordのゲートウェイ(`gateway.discord.gg`)への疎通が無い、またはトークンはあっているがネットワーク経路の問題 | `curl -I https://discord.com` 等でVPSからDiscordへの疎通を確認 |
| `パイプラインが途中で失敗`(①〜⑥のいずれかがNG) | bandaispirits.co.jp側のページ構造変更、またはVPSからの疎通不良 | 表示された段階名とエラーメッセージを確認。①(実データ収集)で失敗する場合は`curl -A "Mozilla/5.0" https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=kimetsu29&grp_id=9999`が200を返すか確認 |
| `should_send=False`(dedupe判定でunchanged) | 直前の実行と全く同じ内容だったため送信をスキップした(想定内。通常は毎回わずかに金額を変えているため起きないはずだが、同一ミリ秒での連続実行等では起こりうる) | もう一度`bash scripts/verify_live_e2e.sh`を実行する |
| workerがタスクを拾わない(タイムアウト) | `docker compose up -d worker beat`が失敗している、またはRedisブローカーに接続できていない | `docker compose logs worker` でエラーが出ていないか確認。`Connected to redis://redis:6379/0`のログがあるか |
| `docker compose up -d db`が`address already in use`で失敗 | ホストの5432番ポートが別プロセス(本VPSの場合、他プロジェクト用のネイティブPostgreSQL)に使われている | 本リポジトリの`docker-compose.yml`は既にホスト側ポートを`55432`にずらして対応済み。それでも失敗する場合は`ss -tlnp \| grep 55432`で衝突が無いか確認 |

上記で解決しない場合は、`verify_logs/`配下に各段階のログが保存されているので、
`summary_<日時>.log`を確認すると全体の流れを追いやすい。
