# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 作業ルール

共通ルールは `~/dev-knowledge/standards/` にある。ここにはこのプロジェクト固有のことだけ書く。

## Project Status

スクールの課題1。Python で Todo リストアプリを作り、サーバーで公開する（必須：登録・編集・一覧、項目はタイトル・内容・期日、保存先は Google スプレッドシート）。
機能は課題の最低限だけ（削除・完了チェックは入れない、とユーザーが決めた）。

作る順番：
1. アプリ本体と自動テスト（完了）
2. Google のサービスアカウントとシートを用意して、本物のシートで確認（完了）
3. GitHub（公開リポジトリ）に push、Vercel で公開（完了 2026-11-21）

公開先：https://todo-app-ten-chi-73.vercel.app
GitHub：https://github.com/bobmoe54145-lab/todo-app（公開）

## Commands

- `uv sync` — 必要なものを入れる
- `uv run python app.py` — 手元で起動（http://127.0.0.1:5001）
- `uv run pytest` — 自動テスト（偽物のシート `tests/conftest.py` の FakeWorksheet を使う）
- `npx vercel@latest --prod --yes` — 公開

## 動作確認のルール

- 画面の確認は、偽物のシートにサンプルを入れたサーバーで行う（本物のシートを使わない）。
- 本物のシートで確かめるときは、確認用に作ったシートを使い、提出用のシートに確認データを残さない。

## Architecture

- `app.py`：Flask。`create_app(store_factory)` で保存先を差し替えられる（テストは偽物）。`app` は Vercel が読む。
- `sheets.py`：`TodoStore` がシートの読み書き。書き込みは `value_input_option="RAW"`（日付が勝手に変換されない、`=` で始まる入力が数式にならない）。
- `templates/`：`index.html`（一覧）、`form.html`（登録・編集を共通で使う）、`message.html`（404 など）。
- 列を増やすときは `sheets.HEADERS`・`TodoStore.update` の範囲（B〜D）・テンプレート・テストを一緒に直す。
- 鍵：手元は `GOOGLE_SERVICE_ACCOUNT_FILE`（ファイルの場所）、Vercel は `GOOGLE_SERVICE_ACCOUNT_JSON`（中身）。

## ハマりどころ

- 鍵は Google Cloud の「Default Gemini Project」で作ったサービスアカウント（Sheets API はそこで有効）。
- Vercel の環境変数は `vercel env add <名前> production` に標準入力で渡した（鍵は `--sensitive`）。`vercel link` が `.env.local` を作るので `.vercelignore` にも入れた。
- 本番 URL は `todo-app-ten-chi-73.vercel.app`。`todo-app-moe-projects1.vercel.app` は Deployment Protection で 302 になる。

- `.wrap { padding: 0 16px }` が `main { padding-top }` を打ち消していたので `main.wrap` にした。
- ブラウザ拡張でウィンドウを狭くできないので、スマホ幅の確認は幅 375px の iframe に入れたページで行った。
