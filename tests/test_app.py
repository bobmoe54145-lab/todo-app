from datetime import date, datetime, timedelta

import pytest

from app import build_calendar, build_groups, deadline_for, nav_from, validate, week_start
from sheets import HEADERS, JST, PREV_HEADERS, TodoStore, migrate_add_kind
from tests.conftest import FakeWorksheet

TODAY = datetime.now(JST).date()


def day(offset=0):
    return (TODAY + timedelta(days=offset)).isoformat()


def add(client, title, kind="day", when=None, content=""):
    """kind=day なら when は日付、week なら週の中の1日、month なら YYYY-MM"""
    data = {"title": title, "content": content, "kind": kind}
    if kind == "day":
        data["day"] = when or day(0)
    elif kind == "week":
        data["week"] = when or day(0)
    else:
        data["month"] = when or TODAY.strftime("%Y-%m")
    return client.post("/new", data=data)


def titles_in(page, start, end=None):
    """ページのうち start〜end の範囲に出てくる文字列"""
    i = page.index(start)
    j = page.index(end, i) if end else len(page)
    return page[i:j]


# ---- シート ----

def test_空のシートには見出しが書かれる(sheet):
    TodoStore(sheet)
    assert sheet.rows[0] == ["ID", "タイトル", "内容", "種類", "期日", "作成日時", "更新日時", "完了日時"]


def test_見出しが違うシートは使わない():
    with pytest.raises(RuntimeError):
        TodoStore(FakeWorksheet([["名前", "点数"]]))


def test_種類の列を入れてもデータはそのまま():
    row = ["abc", "買い物", "なす", "2026-11-21", "2026-11-21 22:10:01", "2026-11-21 22:10:01", ""]
    done = ["def", "済んだ", "", "2026-11-20", "x", "y", "2026-11-22 10:00:00"]
    ws = FakeWorksheet([PREV_HEADERS, row, done])
    assert migrate_add_kind(ws) is True
    assert ws.rows[0] == HEADERS
    assert ws.rows[1] == ["abc", "買い物", "なす", "日", "2026-11-21", "2026-11-21 22:10:01", "2026-11-21 22:10:01", ""]
    assert ws.rows[2][3] == "日" and ws.rows[2][7] == "2026-11-22 10:00:00"
    assert migrate_add_kind(ws) is False  # 2回目は何もしない
    todos = TodoStore(ws).list_active()
    assert [t["title"] for t in todos] == ["買い物"] and todos[0]["kind"] == "day"


def test_想定外の見出しは移行しない():
    with pytest.raises(RuntimeError):
        migrate_add_kind(FakeWorksheet([["名前", "点数"]]))


def test_種類が空の行はその日にやるとして読む(sheet):
    store = TodoStore(sheet)
    sheet.rows.append(["a", "古い行", "", "", "2026-11-21", "", "", ""])
    assert store.list_active()[0]["kind"] == "day"


# ---- 期日の決め方 ----

def test_期日は種類ごとにこの日までの日付():
    tue = date(2026, 11, 24)
    assert deadline_for("day", tue) == tue
    assert deadline_for("week", tue) == date(2026, 11, 28)  # 土曜
    assert deadline_for("month", tue) == date(2026, 11, 30)
    assert deadline_for("month", date(2027, 2, 3)) == date(2027, 2, 28)
    assert deadline_for("month", date(2028, 2, 3)) == date(2028, 2, 29)  # うるう年
    assert deadline_for("month", date(2026, 12, 31)) == date(2026, 12, 31)


def test_入力の確かめ():
    v, e = validate({"title": "a", "kind": "week", "week": "2026-11-22"})
    assert not e and v["kind"] == "week" and v["due_date"] == "2026-11-28"
    v, e = validate({"title": "a", "kind": "month", "month": "2026-12"})
    assert not e and v["due_date"] == "2026-12-31"
    _, e = validate({"title": "a", "kind": "day", "day": ""})
    assert e == ["日付を選んでください。"]
    _, e = validate({"title": "a", "kind": "year"})
    assert e == ["いつやるかを選んでください。"]
    _, e = validate({"title": "a", "kind": "month", "month": "2026-13"})
    assert e == ["月を選んでください。"]


# ---- 登録・編集 ----

def test_3つの種類で登録するとシートに種類と期日が入る(client, sheet):
    add(client, "その日", "day", "2026-12-01", "メモ")
    add(client, "週の中", "week", "2026-12-01")
    add(client, "月の中", "month", "2026-12")
    assert [r[1:5] for r in sheet.rows[1:]] == [
        ["その日", "メモ", "日", "2026-12-01"],
        ["週の中", "", "週", "2026-12-05"],
        ["月の中", "", "月", "2026-12-31"],
    ]
    assert all(r[5] == r[6] and r[7] == "" for r in sheet.rows[1:])


def test_タイトルなしは登録できない(client, sheet):
    res = add(client, "  ")
    assert res.status_code == 400 and "タイトルを入力してください" in res.text
    assert sheet.rows[1:] == []


def test_おかしな日付は登録できず入力は消えない(client):
    res = client.post("/new", data={"title": "a", "content": "", "kind": "day", "day": "2026-02-30"})
    assert res.status_code == 400 and "日付を選んでください" in res.text and 'value="a"' in res.text


def test_登録画面は週と月の選択肢があり最初は今週中(client):
    page = client.get("/new").text
    assert 'value="week" checked' in page
    assert "今週　" in page and "来週　" in page and "今月　" in page
    assert f'value="{week_start(TODAY).isoformat()}" selected' in page


def test_日付を選んで登録すると最初はその日になる(client):
    page = client.get(f"/new?d={day(3)}").text
    assert 'value="day" checked' in page and f'value="{day(3)}"' in page


def test_編集で種類を変えられ作成日時は変わらない(client, sheet):
    add(client, "古い", "day", "2026-12-01")
    todo_id, created = sheet.rows[1][0], sheet.rows[1][5]
    page = client.get(f"/todos/{todo_id}/edit").text
    assert 'value="古い"' in page and 'value="day" checked' in page
    res = client.post(f"/todos/{todo_id}/edit", data={"title": "新しい", "content": "", "kind": "month", "month": "2027-01"})
    assert res.status_code == 302
    assert sheet.rows[1][:6] == [todo_id, "新しい", "", "月", "2027-01-31", created]


def test_過去の週の編集画面でもその週が選ばれている(client, sheet):
    old_week = week_start(TODAY) - timedelta(days=21)
    add(client, "先々々週", "week", old_week.isoformat())
    page = client.get(f"/todos/{sheet.rows[1][0]}/edit").text
    assert f'value="{old_week.isoformat()}" selected' in page


def test_同じタイトルでも別々に編集できる(client, sheet):
    add(client, "同じ", content="1つ目")
    add(client, "同じ", content="2つ目")
    client.post(f"/todos/{sheet.rows[2][0]}/edit", data={"title": "同じ", "content": "直した", "kind": "day", "day": day(0)})
    assert sheet.rows[1][2] == "1つ目" and sheet.rows[2][2] == "直した"


def test_ないToDoは404(client):
    assert client.get("/todos/nai/edit").status_code == 404
    assert client.post("/todos/nai/done").status_code == 404
    assert client.post("/todos/nai/undo").status_code == 404


def test_数式のような入力も文字のまま(client, sheet):
    add(client, "=1+1", content="<script>alert(1)</script>")
    assert sheet.rows[1][1] == "=1+1"
    page = client.get("/?d=all").text
    assert "=1+1" in page and "<script>alert(1)</script>" not in page


# ---- 一覧：今日 ----

def test_今日の一覧は期限切れ_今日_今週中_今月中の順(client):
    add(client, "きのうの用事", "day", day(-1))
    add(client, "今日の用事", "day", day(0))
    add(client, "明日の用事", "day", day(1))
    add(client, "今週の用事", "week", day(0))
    add(client, "今月の用事", "month")
    page = client.get("/").text
    assert page.index("期限切れ") < page.index("きのうの用事") < page.index("今日やること") < page.index("今日の用事") \
        < page.index("今週中") < page.index("今週の用事") < page.index("今月中") < page.index("今月の用事")
    assert "明日の用事" not in page


def test_週と月のToDoはその期間の毎日に出る(client):
    s = week_start(TODAY)
    add(client, "今週の用事", "week", s.isoformat())
    add(client, "今月の用事", "month")
    for i in range(7):
        d = s + timedelta(days=i)
        page = client.get(f"/?d={d.isoformat()}").text
        assert "今週の用事" in page, d
        assert ("今月の用事" in page) == (d.month == TODAY.month), d
    # 次の週には出ない
    assert "今週の用事" not in client.get(f"/?d={(s + timedelta(days=7)).isoformat()}").text


def test_終わらなかった週と月のToDoは期限切れに出続ける(client):
    last_week = week_start(TODAY) - timedelta(days=7)
    last_month = (TODAY.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    add(client, "先週の用事", "week", last_week.isoformat())
    add(client, "先月の用事", "month", last_month)
    page = client.get("/").text
    head = titles_in(page, "期限切れ", "今日やること")
    assert "先週の用事" in head and "先月の用事" in head


def test_期限切れがなければ見出しも出ない(client):
    add(client, "今日の用事")
    page = client.get("/").text
    assert 'class="group-head overdue"' not in page


def test_他の日を選んだときは期限切れを出さない(client):
    add(client, "きのうの用事", "day", day(-1))
    add(client, "明日の用事", "day", day(1))
    page = client.get(f"/?d={day(1)}").text
    assert "明日の用事" in page and "きのうの用事" not in page
    assert "この日にやること" in page


def test_すべて表示は種類ごとに分かれる(client):
    add(client, "きのうの用事", "day", day(-1))
    add(client, "来週の用事", "week", day(7))
    add(client, "明日の用事", "day", day(1))
    add(client, "来月の用事", "month", (TODAY.replace(day=28) + timedelta(days=5)).strftime("%Y-%m"))
    page = titles_in(client.get("/?d=all").text, "すべてのToDo")
    assert page.index("きのうの用事") < page.index("その日にやる") < page.index("明日の用事") \
        < page.index("週の中でやる") < page.index("来週の用事") < page.index("月の中でやる") < page.index("来月の用事")


# ---- カレンダー ----

def with_ctx(fn):
    from app import create_app
    app = create_app(store_factory=lambda: TodoStore(FakeWorksheet()))
    with app.test_request_context("/"):
        fn()


def test_週の帯の件数はその日にやるToDoだけ_週と月は別に数える():
    def check():
        t = date(2026, 11, 24)
        todos = [
            {"kind": "day", "due_date": "2026-11-23"}, {"kind": "day", "due_date": "2026-11-23"},
            {"kind": "day", "due_date": "2026-11-25"},
            {"kind": "week", "due_date": "2026-11-28"}, {"kind": "week", "due_date": "2026-11-28"},
            {"kind": "month", "due_date": "2026-11-30"},
        ]
        cal = build_calendar(todos, {}, t)
        assert cal["label"] == "11.22 — 11.28"
        days = {d["date"]: d for d in cal["days"]}
        assert days["2026-11-23"]["count"] == 2 and days["2026-11-23"]["overdue"]
        assert days["2026-11-25"]["count"] == 1 and days["2026-11-28"]["count"] == 0
        assert cal["periods"] == [{"label": "この週中", "count": 2}, {"label": "11月中", "count": 1}]
        assert days["2026-11-24"]["today"] and days["2026-11-24"]["selected"]
        assert "d=all" in days["2026-11-24"]["url"]
    with_ctx(check)


def test_月表示は月の前後を含めて週単位で並ぶ():
    def check():
        cal = build_calendar([], {"view": "month", "m": "2026-11"}, date(2026, 11, 24))
        assert cal["label"] == "2026.11"
        assert cal["days"][0]["date"] == "2026-11-01" and cal["days"][-1]["date"] == "2026-12-05"
        assert cal["days"][-1]["out"]
        feb = build_calendar([], {"view": "month", "m": "2027-02"}, date(2026, 11, 24))
        assert feb["days"][0]["date"] == "2027-01-31" and len(feb["days"]) % 7 == 0
    with_ctx(check)


def test_おかしなパラメータは捨てる():
    assert nav_from({"d": "x", "w": "2026-13-01", "view": "year", "m": "abc"}) == {}
    assert nav_from({"d": "all", "view": "month", "m": "2026-11"}) == {"d": "all", "view": "month", "m": "2026-11"}


def test_週の移動と月表示(client):
    page = client.get("/?w=2026-12-02").text
    assert "11.29 — 12.5" in page and "月表示" in page
    page = client.get("/?view=month&m=2026-12").text
    assert "2026.12" in page and "週表示" in page and "12月中" in page


def test_まとまりの作り方_月をまたぐ週():
    # 2026-12-01（火）を選ぶ：週は 11/29〜12/5、月は 12月
    todos = [
        {"id": "w", "kind": "week", "due_date": "2026-12-05"},
        {"id": "m11", "kind": "month", "due_date": "2026-11-30"},
        {"id": "m12", "kind": "month", "due_date": "2026-12-31"},
    ]
    groups = build_groups(todos, date(2026, 12, 1), date(2026, 11, 24))
    names = {g["name"]: [t["id"] for t in g["items"]] for g in groups}
    assert names["この週中"] == ["w"] and names["この月中"] == ["m12"]


# ---- 完了 ----

def test_完了すると一覧から消えシートには完了日時が残る(client, sheet):
    add(client, "今週の用事", "week")
    todo_id = sheet.rows[1][0]
    res = client.post(f"/todos/{todo_id}/done", data={"d": day(0)})
    assert f"d={day(0)}" in res.location and f"done={todo_id}" in res.location
    assert sheet.rows[1][7] != "" and len(sheet.rows) == 2
    page = client.get(res.location).text
    assert "「今週の用事」を完了しました" in page and "元に戻す" in page
    assert '<h3 class="title">今週の用事</h3>' not in page


def test_元に戻すと一覧に戻る(client, sheet):
    add(client, "買い物")
    todo_id = sheet.rows[1][0]
    client.post(f"/todos/{todo_id}/done")
    res = client.post(f"/todos/{todo_id}/undo", data={"d": "all"})
    assert sheet.rows[1][7] == "" and "d=all" in res.location
    assert '<h3 class="title">買い物</h3>' in client.get(res.location).text


def test_編集のあとも見ていた表示に戻る(client, sheet):
    add(client, "A", "day", "2026-12-24")
    todo_id = sheet.rows[1][0]
    assert "d=2026-12-24" in client.get(f"/todos/{todo_id}/edit?d=2026-12-24&w=2026-12-24").text
    res = client.post(f"/todos/{todo_id}/edit?d=2026-12-24&w=2026-12-24",
                      data={"title": "A2", "content": "", "kind": "day", "day": "2026-12-24"})
    assert "d=2026-12-24" in res.location and "w=2026-12-24" in res.location


def test_名前はToDoリスト(client):
    page = client.get("/").text
    assert "<title>ToDoリスト</title>" in page and "やることリスト" not in page


def test_シートを手で直して日付が崩れても一覧は表示できる(sheet):
    from app import create_app
    store = TodoStore(sheet)
    sheet.rows.append(["abc", "手で足した", "", "日", "来月くらい", "", "", ""])
    res = create_app(store_factory=lambda: store).test_client().get("/?d=all")
    assert res.status_code == 200 and "来月くらい" in res.text
