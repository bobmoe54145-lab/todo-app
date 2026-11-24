import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from sheets import TodoStore  # noqa: E402


class FakeWorksheet:
    """gspread の Worksheet のうち、アプリが使う部分だけをまねた偽物（メモリ上の表）。"""

    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]

    def row_values(self, n):
        return list(self.rows[n - 1]) if n <= len(self.rows) else []

    def col_values(self, n):
        return [r[n - 1] if len(r) >= n else "" for r in self.rows]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, values, value_input_option=None, table_range=None):
        assert value_input_option == "RAW"
        self.rows.append([str(v) for v in values])

    def insert_cols(self, values, col=1, value_input_option=None):
        assert value_input_option == "RAW"
        for c_off, column in enumerate(values):
            for r, v in enumerate(column):
                while len(self.rows) <= r:
                    self.rows.append([])
                row = self.rows[r]
                while len(row) < col - 1:
                    row.append("")
                row.insert(col - 1 + c_off, str(v))

    def update(self, values, range_name, value_input_option=None):
        assert value_input_option == "RAW"
        m = re.fullmatch(r"([A-Z])(\d+)(?::([A-Z])(\d+))?", range_name)
        col = ord(m.group(1)) - ord("A")
        row = int(m.group(2)) - 1
        for r_off, line in enumerate(values):
            while len(self.rows) <= row + r_off:
                self.rows.append([])
            target = self.rows[row + r_off]
            for c_off, v in enumerate(line):
                while len(target) <= col + c_off:
                    target.append("")
                target[col + c_off] = str(v)


ME = "me@example.com"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def login_env(monkeypatch):
    """テスト用のログイン設定（本物の値は使わない）"""
    from werkzeug.security import generate_password_hash
    monkeypatch.setenv("ALLOWED_EMAILS", f"{ME}, second@example.com")
    monkeypatch.setenv("LOGIN_PASSWORD_HASH", generate_password_hash(PASSWORD))
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)


class CsrfClient:
    """POST のときに合言葉（csrf_token）を自動で付けるテスト用のブラウザ"""

    def __init__(self, client):
        self._c = client
        with client.session_transaction() as s:
            s["csrf"] = "test-token"

    def post(self, *args, data=None, **kwargs):
        data = dict(data or {})
        data.setdefault("csrf_token", "test-token")
        return self._c.post(*args, data=data, **kwargs)

    def __getattr__(self, name):
        return getattr(self._c, name)


@pytest.fixture
def sheet():
    return FakeWorksheet()


@pytest.fixture
def app(sheet):
    return create_app(store_factory=lambda: TodoStore(sheet), testing=True)


@pytest.fixture
def anon(app):
    """ログインしていない人"""
    return CsrfClient(app.test_client())


@pytest.fixture
def client(app):
    """ログインしている人"""
    c = CsrfClient(app.test_client())
    with c.session_transaction() as s:
        s["user"] = ME
    return c
