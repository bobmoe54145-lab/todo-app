from tests.conftest import ME, PASSWORD


def login(anon, email=ME, password=PASSWORD, next_path=None):
    data = {"email": email, "password": password}
    if next_path:
        data["next"] = next_path
    return anon.post("/login", data=data)


def test_ログインしていないとログイン画面に移る(anon):
    for path in ["/", "/new", "/?d=all", "/todos/abc/edit"]:
        res = anon.get(path)
        assert res.status_code == 302 and "/login" in res.location, path


def test_ログインしていないと登録も完了もできない(anon, sheet):
    res = anon.post("/new", data={"title": "x", "kind": "day", "day": "2026-12-01"})
    assert res.status_code == 302 and "/login" in res.location
    assert anon.post("/todos/abc/done").status_code == 302
    assert sheet.rows == []  # シートにも触っていない


def test_ログイン画面が出る(anon):
    page = anon.get("/login").text
    assert "ログイン" in page and 'name="password"' in page
    assert "Google でログイン" not in page  # Google の設定がないときはボタンを出さない


def test_正しいメールとパスワードでログインできる(anon):
    res = login(anon)
    assert res.status_code == 302 and res.location == "/"
    assert anon.get("/").status_code == 200


def test_メールは大文字小文字を区別しない(anon):
    assert login(anon, email="Me@Example.com").status_code == 302


def test_パスワードが違うとログインできない(anon):
    res = login(anon, password="wrong-password")
    assert res.status_code == 401 and "メールアドレスかパスワードが違います" in res.text
    assert anon.get("/").status_code == 302


def test_許可されていないメールはパスワードが合ってもログインできない(anon):
    res = login(anon, email="stranger@example.com")
    assert res.status_code == 401
    assert anon.get("/").status_code == 302


def test_パスワードのハッシュが未設定ならログインできない(anon, monkeypatch):
    monkeypatch.setenv("LOGIN_PASSWORD_HASH", "")
    assert login(anon, password="").status_code == 401


def test_ログイン後は元のページに戻る(anon):
    res = anon.get("/?d=all")
    assert "next=" in res.location
    res = login(anon, next_path="/?d=all")
    assert res.location == "/?d=all"


def test_よそのサイトへは戻さない(anon):
    for bad in ["https://evil.example/", "//evil.example/", "evil"]:
        assert login(anon, next_path=bad).location == "/"
        anon.post("/logout")
        with anon.session_transaction() as sess:  # 本物ではログイン画面を開いたときに合言葉が新しく作られる
            sess["csrf"] = "test-token"


def test_合言葉のない送信は受け付けない(client, sheet):
    res = client._c.post("/new", data={"title": "x", "kind": "day", "day": "2026-12-01"})
    assert res.status_code == 400
    res = client._c.post("/new", data={"title": "x", "kind": "day", "day": "2026-12-01", "csrf_token": "wrong"})
    assert res.status_code == 400
    assert sheet.rows[1:] == []


def test_ログアウトするとまたログインが必要(client):
    assert client.get("/").status_code == 200
    res = client.post("/logout")
    assert "/login" in res.location
    assert client.get("/").status_code == 302


def test_許可リストから外れたらログアウト扱い(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "someone-else@example.com")
    assert client.get("/").status_code == 302


def test_ログイン中はログアウトボタンが出る(client):
    assert "ログアウト" in client.get("/").text


def test_ログインのクッキーは30日で安全な設定(app):
    assert app.config["PERMANENT_SESSION_LIFETIME"].days == 30
    assert app.config["SESSION_COOKIE_HTTPONLY"] and app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_公開先でSECRET_KEYがないと起動しない(monkeypatch, sheet):
    import pytest
    from app import create_app
    from sheets import TodoStore
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError):
        create_app(store_factory=lambda: TodoStore(sheet))


def test_Googleの設定があるとボタンが出る(monkeypatch, sheet):
    from app import create_app
    from sheets import TodoStore
    from tests.conftest import CsrfClient
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "dummy.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "dummy")
    c = CsrfClient(create_app(store_factory=lambda: TodoStore(sheet), testing=True).test_client())
    assert "Google でログイン" in c.get("/login").text
    res = c.get("/login/google")
    assert res.status_code == 302 and res.location.startswith("https://accounts.google.com/")
    assert "redirect_uri=http%3A%2F%2Flocalhost%2Fauth%2Fgoogle%2Fcallback" in res.location
