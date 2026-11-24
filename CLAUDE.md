# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 作業ルール

共通ルールは `~/dev-knowledge/standards/` にある。ここにはこのプロジェクト固有のことだけ書く。

## Project Status

スクールの課題1。Python で Todo リストアプリを作り、サーバーで公開する（必須：登録・編集・一覧、項目はタイトル・内容・期日、保存先は Google スプレッドシート）。
最初は課題の最低限だけで公開。その後ユーザーの要望で、完了ボタン（シートに完了日時を残す）・週の帯のカレンダー（カレンダー配置3案から案Bを選んだ）・シートの日本語見出しを追加（2026-11-24）。削除機能はない。

作る順番：
1. アプリ本体と自動テスト（完了）
2. Google のサービスアカウントとシートを用意して、本物のシートで確認（完了）
3. GitHub（公開リポジトリ）に push、Vercel で公開（完了 2026-11-21）
4. 完了ボタン・カレンダー（週の帯＋月表示）・日本語の見出し（完了 2026-11-24）
5. 今日を選んでいるときは期限切れも見出しを分けて上に出す（完了 2026-11-24）
6. 名前を「ToDoリスト」に、デザインを「生成り × えんじ」（配色3案から案1）に。ToDo を「その日／週の中／月の中」の3種類に分けた（完了 2026-11-24）。見本 Artifact：https://claude.ai/artifact/AKH7udCg159mLKD7PRqZ7P

公開先：https://todo-app-ten-chi-73.vercel.app
GitHub：https://github.com/bobmoe54145-lab/todo-app（公開）

## Commands

- `uv sync` — 必要なものを入れる
- `uv run python app.py` — 手元で起動（http://127.0.0.1:5001）
- `uv run pytest` — 自動テスト（偽物のシート `tests/conftest.py` の FakeWorksheet を使う）
- `npx vercel@latest --prod --yes` — 公開

## 動作確認のルール

- 画面の確認は、偽物のシートにサンプルを入れたサーバーで行う（本物のシートを使わない）。
- デザインは生成り × えんじ。えんじ（`--accent`）は「＋ 登録」と期限切れだけに使う。角 2px・影なし・細い線で区切る。
- 本物のシートで確かめるときは、確認用に作ったシートを使い、提出用のシートに確認データを残さない。

## Architecture

- `app.py`：Flask。`create_app(store_factory)` で保存先を差し替えられる（テストは偽物）。`app` は Vercel が読む。
- `sheets.py`：`TodoStore` がシートの読み書き。書き込みは `value_input_option="RAW"`（日付が勝手に変換されない、`=` で始まる入力が数式にならない）。
- 一覧の表示状態は URL のパラメータ（`d` 選んだ日 / `all`、`w` 週、`view=month`、`m` 月）。`nav_from()` で検証し、完了・編集・登録のリンクとフォームに引き継ぐ。カレンダーの組み立ては `build_calendar()`（日曜始まり）。
- `templates/`：`index.html`（カレンダー＋一覧）、`form.html`（登録・編集を共通で使う）、`message.html`（404 など）。
- シートの列：A=ID B=タイトル C=内容 D=種類（日/週/月） E=期日 F=作成日時 G=更新日時 H=完了日時（見出しは日本語の `HEADERS`、コードでは英語の `KEYS`、種類はコードでは day/week/month）。完了は H に日時を書くだけで行は消さない。
- 期日はどの種類でも「この日までに」（週＝その週の土曜、月＝末日）。週は日曜始まり。期日 < 今日 なら期限切れ（種類に関係なく今日の一覧の先頭に出る）。一覧のまとまりは `build_groups()`、登録画面の週・月の選択肢は `form_choices()`。
- 登録画面の日付・週・月の欄の切りかえは CSS の `:has()` だけで行う（JS なし）。
- 列を増やすときは `sheets.HEADERS`/`KEYS`・`add`/`update` の範囲・見出しの範囲（A1:H1）・テンプレート・テストを一緒に直す。
- 2026-11-24 に本物のシートを2回移行した：見出しを英語→日本語、そのあと `migrate_add_kind()` で「種類」の列を D に挿入（どちらも移行前のコピーをバックアップのタブに残した）。
- 鍵：手元は `GOOGLE_SERVICE_ACCOUNT_FILE`（ファイルの場所）、Vercel は `GOOGLE_SERVICE_ACCOUNT_JSON`（中身）。

## ハマりどころ

- 鍵は Google Cloud の「Default Gemini Project」で作ったサービスアカウント（Sheets API はそこで有効）。
- Vercel の環境変数は `vercel env add <名前> production` に標準入力で渡した（鍵は `--sensitive`）。`vercel link` が `.env.local` を作るので `.vercelignore` にも入れた。
- 本番 URL は `todo-app-ten-chi-73.vercel.app`。`todo-app-moe-projects1.vercel.app` は Deployment Protection で 302 になる。

- `.wrap { padding: 0 16px }` が `main { padding-top }` を打ち消していたので `main.wrap` にした。
- ブラウザ拡張でウィンドウを狭くできないので、スマホ幅の確認は幅 375px の iframe に入れたページで行った。
