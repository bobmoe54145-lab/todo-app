"""ログインの設定をする（ターミナルで本人が実行する）。

  uv run python scripts/setup_login.py

聞かれること：
  1. ログインを許可するメールアドレス（あなたの Gmail）
  2. パスワード（画面に表示されない。12文字以上）
  3. Google ログイン用のクライアント ID とシークレット（まだなければ Enter で飛ばせる）

やること：
  - パスワードはハッシュ（元に戻せない形）に変えてから保存する。パスワードそのものはどこにも残さない
  - ログイン用の鍵（SECRET_KEY）を新しく作る（作り直すと、ログイン中の人は一度ログアウトされる）
  - 手元の .env と、Vercel の環境変数（本番）の両方に保存する
  - 保存した値は画面に表示しない
"""

import getpass
import re
import secrets
import subprocess
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def ask_email() -> str:
    while True:
        email = input("1. ログインを許可するメールアドレス: ").strip().lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return email
        print("   メールアドレスの形になっていません。もう一度入力してください。")


def ask_password() -> str:
    while True:
        p1 = getpass.getpass("2. パスワード（12文字以上・画面には出ません）: ")
        if len(p1) < 12:
            print("   12文字以上にしてください。")
            continue
        p2 = getpass.getpass("   確認のため、もう一度: ")
        if p1 != p2:
            print("   1回目と2回目が違います。もう一度入力してください。")
            continue
        return p1


def write_env(values: dict[str, str]) -> None:
    """手元の .env の該当する行を書きかえる（なければ足す）。他の行はそのまま"""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    keys = set(values)
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in keys]
    for k, v in values.items():
        # 単一引用符で囲む（ハッシュの $ が別の意味に読まれないように）
        kept.append(f"{k}='{v}'")
    ENV_FILE.write_text("\n".join(kept) + "\n")
    ENV_FILE.chmod(0o600)


def push_vercel(values: dict[str, str]) -> bool:
    ok = True
    for k, v in values.items():
        subprocess.run(["npx", "-y", "vercel@latest", "env", "rm", k, "production", "-y"],
                       cwd=ROOT, capture_output=True, text=True)
        res = subprocess.run(["npx", "-y", "vercel@latest", "env", "add", k, "production", "--sensitive"],
                             cwd=ROOT, input=v, capture_output=True, text=True)
        print(f"   {k}: {'保存しました' if res.returncode == 0 else '失敗しました'}")
        ok = ok and res.returncode == 0
    return ok


def main() -> int:
    print("ToDoリストのログインを設定します。\n")
    email = ask_email()
    password = ask_password()
    print("3. Google ログイン用の設定（まだ作っていなければ、何も入れずに Enter）")
    client_id = input("   クライアント ID: ").strip()
    client_secret = getpass.getpass("   クライアント シークレット（画面には出ません）: ").strip() if client_id else ""

    values = {
        "ALLOWED_EMAILS": email,
        "LOGIN_PASSWORD_HASH": generate_password_hash(password),
        "SECRET_KEY": secrets.token_hex(32),
    }
    if client_id and client_secret:
        values["GOOGLE_CLIENT_ID"] = client_id
        values["GOOGLE_CLIENT_SECRET"] = client_secret
    del password

    write_env(values)
    print("\n手元の .env に保存しました：", ", ".join(values))
    answer = input("Vercel（公開中のアプリ）にも保存しますか？ [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        print("Vercel に保存しています（1〜2分かかります）…")
        if not push_vercel(values):
            print("一部の保存に失敗しました。もう一度実行してください。")
            return 1
    print("\n完了しました。Claude に「設定できた」と伝えてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
