"""ログインのしくみ。

- ログインしていないと、ログイン画面以外は開けない（ログイン画面へ移る）
- ログインの方法は2つ：Google アカウント／メールアドレスとパスワード
- 入れるのは ALLOWED_EMAILS（環境変数、カンマ区切り）に書いたアドレスだけ
- パスワードはハッシュ（元に戻せない形）だけを環境変数 LOGIN_PASSWORD_HASH に置く
- ログイン状態は署名付きのクッキー（SECRET_KEY で署名）に 30 日間残す
- フォームの送信には CSRF トークン（ページの外から勝手に送られるのを防ぐ合言葉）を付ける

環境変数：
  SECRET_KEY            クッキーの署名用の鍵（長いランダムな文字列）
  ALLOWED_EMAILS        入れるメールアドレス（カンマ区切り）
  LOGIN_PASSWORD_HASH   パスワードのハッシュ（scripts/setup_login.py で作る）
  GOOGLE_CLIENT_ID      Google ログイン用（なければ Google のボタンを出さない）
  GOOGLE_CLIENT_SECRET  同上
"""

import hmac
import os
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

# ログインしていなくても開けるページ
PUBLIC_ENDPOINTS = {"login", "login_password", "login_google", "google_callback", "static"}


def allowed_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_allowed(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in allowed_emails()


def safe_next(target: str | None) -> str | None:
    """ログイン後に戻るページ。自分のサイトの中のパスだけ受け付ける（よそのサイトへ飛ばされないように）"""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def init_auth(app):
    """Flask アプリにログインのしくみを取り付ける"""
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if not app.config.get("TESTING") and os.environ.get("VERCEL"):
            raise RuntimeError("環境変数 SECRET_KEY が設定されていません。")
        secret = secrets.token_hex(32)  # 手元で動かすとき用（起動し直すとログアウトされる）
    app.config.update(
        SECRET_KEY=secret,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),  # 公開先は https なので https のときだけ送る
    )
    app.jinja_env.globals["csrf_token"] = csrf_token

    google_enabled = bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))
    oauth = None
    if google_enabled:
        from authlib.integrations.flask_client import OAuth
        oauth = OAuth(app)
        oauth.register(
            "google",
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email"},
        )

    @app.before_request
    def require_login():
        # フォームの送信には合言葉が必要
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400)
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not is_allowed(session.get("user")):
            # 許可リストから外れた人もログアウト扱い
            session.pop("user", None)
            next_path = request.full_path if request.method == "GET" else None
            return redirect(url_for("login", next=safe_next(next_path.rstrip("?") if next_path else None)))
        return None

    def log_in(email: str):
        csrf = session.get("csrf")
        session.clear()  # ログインの前後で別のクッキーにする（なりすまし対策）
        if csrf:
            session["csrf"] = csrf
        session["user"] = email.strip().lower()
        session.permanent = True

    @app.get("/login")
    def login():
        if is_allowed(session.get("user")):
            return redirect(url_for("index"))
        return render_template(
            "login.html", google_enabled=google_enabled,
            next=safe_next(request.args.get("next")), email="",
        )

    @app.post("/login")
    def login_password():
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        next_path = safe_next(request.form.get("next"))
        stored = os.environ.get("LOGIN_PASSWORD_HASH", "")
        # アドレスとパスワードのどちらが違うかは教えない
        ok = is_allowed(email) and bool(stored) and check_password_hash(stored, password)
        if not ok:
            flash("メールアドレスかパスワードが違います。")
            return render_template(
                "login.html", google_enabled=google_enabled, next=next_path, email=email,
            ), 401
        log_in(email)
        return redirect(next_path or url_for("index"))

    @app.get("/login/google")
    def login_google():
        if not oauth:
            abort(404)
        session["next"] = safe_next(request.args.get("next"))
        redirect_uri = url_for("google_callback", _external=True)
        return oauth.google.authorize_redirect(redirect_uri, prompt="select_account")

    @app.get("/auth/google/callback")
    def google_callback():
        if not oauth:
            abort(404)
        try:
            token = oauth.google.authorize_access_token()
        except Exception:  # キャンセルした・時間がたった など
            flash("Google でのログインができませんでした。もう一度お試しください。")
            return redirect(url_for("login"))
        info = token.get("userinfo") or {}
        email = info.get("email", "")
        if not info.get("email_verified") or not is_allowed(email):
            flash("このアカウントでは使えません。許可されたアカウントでログインしてください。")
            return redirect(url_for("login"))
        next_path = session.pop("next", None)
        log_in(email)
        return redirect(safe_next(next_path) or url_for("index"))

    @app.post("/logout")
    def logout():
        session.clear()
        flash("ログアウトしました。")
        return redirect(url_for("login"))
