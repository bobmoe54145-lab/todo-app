"""Google スプレッドシートにやることを保存・読み込みする部分。

シートの1行目は見出し（HEADERS）、2行目から1行＝ToDo 1件。
列の順番：A=ID B=タイトル C=内容 D=種類 E=期日 F=作成日時 G=更新日時 H=完了日時

種類：日＝その日にやる／週＝その週の中でやる（日曜〜土曜）／月＝その月の中でやる
期日：どの種類でも「この日までにやる」日付（日＝その日、週＝その週の土曜、月＝その月の末日）
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

# シートに表示する見出し（日本語）と、プログラムの中で使う名前（英語）。順番は同じ
HEADERS = ["ID", "タイトル", "内容", "種類", "期日", "作成日時", "更新日時", "完了日時"]
KEYS = ["id", "title", "content", "kind", "due_date", "created_at", "updated_at", "done_at"]
# 以前の見出し（移行用）。種類の列がなかった
PREV_HEADERS = ["ID", "タイトル", "内容", "期日", "作成日時", "更新日時", "完了日時"]

# 種類：プログラムの中の名前 ⇔ シートに書く文字
KIND_TO_SHEET = {"day": "日", "week": "週", "month": "月"}
SHEET_TO_KIND = {v: k for k, v in KIND_TO_SHEET.items()}

JST = timezone(timedelta(hours=9))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def to_todo(row: list[str]) -> dict:
    todo = dict(zip(KEYS, row + [""] * (len(KEYS) - len(row))))
    # 種類が空や想定外なら「その日にやる」として扱う
    todo["kind"] = SHEET_TO_KIND.get(todo["kind"], "day")
    return todo


class TodoStore:
    """やることの保存先。worksheet は gspread の Worksheet（テストでは偽物）。"""

    def __init__(self, worksheet):
        self.ws = worksheet
        self._ensure_headers()

    def _ensure_headers(self):
        # 空のシートなら見出しを書く。見出しが違うシートは事故を防ぐため使わない
        first_row = self.ws.row_values(1)
        if not first_row:
            self.ws.update(values=[HEADERS], range_name="A1:H1", value_input_option="RAW")
        elif first_row[: len(HEADERS)] != HEADERS:
            raise RuntimeError(
                f"シートの1行目が想定と違います: {first_row}（想定: {HEADERS}）。"
                "新しい空のシートを使ってください。"
            )

    def _all(self) -> list[dict]:
        todos = [to_todo(row) for row in self.ws.get_all_values()[1:]]
        return [t for t in todos if t["id"]]

    def list_active(self) -> list[dict]:
        """完了していないやること。期日が近い順、期日なしは最後、同じ期日は登録が古い順"""
        todos = [t for t in self._all() if not t["done_at"]]
        return sorted(todos, key=lambda t: (t["due_date"] == "", t["due_date"], t["created_at"]))

    def _row_number(self, todo_id: str) -> int | None:
        ids = self.ws.col_values(1)
        for i, value in enumerate(ids[1:], start=2):
            if value == todo_id:
                return i
        return None

    def get(self, todo_id: str) -> dict | None:
        row = self._row_number(todo_id)
        if row is None:
            return None
        return to_todo(self.ws.row_values(row))

    def add(self, title: str, content: str, kind: str, due_date: str) -> str:
        todo_id = uuid.uuid4().hex[:12]
        now = now_jst()
        # RAW：入力をそのまま文字として保存（"=" で始まる文字が数式にならない、日付の形が変わらない）
        self.ws.append_row(
            [todo_id, title, content, KIND_TO_SHEET[kind], due_date, now, now, ""],
            value_input_option="RAW",
            table_range="A1",
        )
        return todo_id

    def update(self, todo_id: str, title: str, content: str, kind: str, due_date: str) -> bool:
        row = self._row_number(todo_id)
        if row is None:
            return False
        self.ws.update(
            values=[[title, content, KIND_TO_SHEET[kind], due_date]],
            range_name=f"B{row}:E{row}",
            value_input_option="RAW",
        )
        self.ws.update(values=[[now_jst()]], range_name=f"G{row}", value_input_option="RAW")
        return True

    def set_done(self, todo_id: str, done: bool) -> bool:
        """完了にする（完了日時を書く）／元に戻す（完了日時を消す）。行は消さない"""
        row = self._row_number(todo_id)
        if row is None:
            return False
        value = now_jst() if done else ""
        self.ws.update(values=[[value]], range_name=f"H{row}", value_input_option="RAW")
        return True


def migrate_add_kind(worksheet) -> bool:
    """「種類」の列がない以前の形のシートに、内容と期日の間へ「種類」の列を入れる。

    今ある行はすべて「日」（その日にやる）にする。入れたら True、すでに新しい形なら False。
    どちらでもなければエラー（よく分からないシートは書きかえない）。
    """
    first_row = worksheet.row_values(1)
    if first_row[: len(HEADERS)] == HEADERS:
        return False
    if first_row[: len(PREV_HEADERS)] != PREV_HEADERS:
        raise RuntimeError(f"見出しが想定と違うので書きかえません: {first_row}")
    n_rows = len(worksheet.get_all_values())
    column = [["種類"]] + [["日"] for _ in range(n_rows - 1)]
    worksheet.insert_cols([[v[0] for v in column]], col=4, value_input_option="RAW")
    return True


def open_worksheet():
    """環境変数の設定を使って、本物のスプレッドシートを開く。"""
    import gspread
    from google.oauth2.service_account import Credentials

    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("環境変数 SPREADSHEET_ID が設定されていません。")

    # 公開先（Vercel）では鍵の中身を、手元では鍵ファイルの場所を環境変数で渡す
    key_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if key_json:
        creds = Credentials.from_service_account_info(json.loads(key_json), scopes=SCOPES)
    elif key_file:
        creds = Credentials.from_service_account_file(key_file, scopes=SCOPES)
    else:
        raise RuntimeError(
            "環境変数 GOOGLE_SERVICE_ACCOUNT_JSON か GOOGLE_SERVICE_ACCOUNT_FILE を設定してください。"
        )

    client = gspread.authorize(creds)
    return client.open_by_key(spreadsheet_id).sheet1
