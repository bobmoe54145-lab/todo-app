# やることリスト（Todo アプリ）

やること（タイトル・内容・期日）を登録・編集し、一覧で確認できる Web アプリです。
データは Google スプレッドシートに保存します。

## できること

- やることの登録（タイトルは必須、内容・期日は任意）
- やることの編集
- 一覧表示（期日が近い順。期限切れは赤、今日が期日は黄色で表示）
- スマホでもパソコンでも見やすい画面

## しくみ

| 役割 | 使うもの |
| --- | --- |
| Web アプリ | Python 3.12 + Flask |
| データの保存 | Google スプレッドシート（gspread でシートを読み書き） |
| Google への接続 | サービスアカウント（サーバー用の専用アカウント） |
| 公開先 | Vercel |

スプレッドシートの1枚目のシートを使います。1行目が見出し、2行目から1行がやること1件です。

| id | title | content | due_date | created_at | updated_at |
| --- | --- | --- | --- | --- | --- |
| 区別するための番号 | タイトル | 内容 | 期日（2026-11-20 の形） | 作成日時 | 更新日時 |

空のシートなら、最初に開いたときに見出しが自動で書かれます。

## 準備（最初に1回）

1. Google Cloud でプロジェクトを作り、**Google Sheets API** を有効にする
2. **サービスアカウント**を作り、鍵（JSON）をダウンロードして `credentials/service-account.json` に置く
3. スプレッドシートを作り、サービスアカウントのメールアドレス（`…@…iam.gserviceaccount.com`）に**編集者**で共有する
4. `.env.example` をコピーして `.env` を作り、`SPREADSHEET_ID` を入れる

## 手元で動かす

```sh
uv sync                 # 必要なものを入れる
uv run python app.py    # http://127.0.0.1:5001 で開く
uv run pytest           # 自動テスト（本物のシートは使わない）
```

## 公開（Vercel）

```sh
npx vercel@latest login          # 最初だけ
npx vercel@latest --prod --yes   # 公開
```

Vercel の画面（Settings → Environment Variables）で次の2つを設定し、もう一度公開します。

- `SPREADSHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`（鍵ファイルの中身をまるごと貼る）

## 注意

- ログイン機能はありません。URL を知っている人は誰でも登録・編集できます。
- 鍵ファイル（`credentials/`）と `.env` は GitHub に上げません（`.gitignore` で除外済み）。
