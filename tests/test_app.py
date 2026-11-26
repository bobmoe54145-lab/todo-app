from datetime import date, datetime, timedelta

import pytest

from app import (build_calendar, build_groups, build_review, create_app, deadline_for, line_message, nav_from,
                 next_due, parse_repeat, repeat_text, validate, week_start)
from sheets import HEADERS, JST, PREV_HEADERS, TodoStore, migrate_add_repeat
from tests.conftest import FakeWorksheet

TODAY = datetime.now(JST).date()


def day(offset=0):
    return (TODAY + timedelta(days=offset)).isoformat()


def add(client, title, kind="day", when=None, content="", **extra):
    """kind=day なら when は日付、week なら週の中の1日、month なら YYYY-MM。extra は繰り返しの欄など"""
    data = {"title": title, "content": content, "kind": kind, **extra}
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
    assert sheet.rows[0] == ["ID", "タイトル", "内容", "種類", "期日", "作成日時", "更新日時", "完了日時", "繰り返し", "削除日時"]


def test_見出しが違うシートは使わない():
    with pytest.raises(RuntimeError):
        TodoStore(FakeWorksheet([["名前", "点数"]]))
    with pytest.raises(RuntimeError):  # 移行前の8列のシートも、移行するまで使わない
        TodoStore(FakeWorksheet([PREV_HEADERS]))


def test_繰り返しと削除日時の見出しを足してもデータはそのまま():
    row = ["abc", "買い物", "なす", "日", "2026-11-21", "2026-11-21 22:10:01", "2026-11-21 22:10:01", ""]
    done = ["def", "済んだ", "", "週", "2026-11-28", "x", "y", "2026-11-22 10:00:00"]
    ws = FakeWorksheet([PREV_HEADERS, row, done])
    assert migrate_add_repeat(ws) is True
    assert ws.rows[0] == HEADERS and ws.rows[1] == row and ws.rows[2] == done
    assert migrate_add_repeat(ws) is False  # 2回目は何もしない
    todos = TodoStore(ws).list_active()
    assert [t["title"] for t in todos] == ["買い物"] and todos[0]["repeat"] == "" and todos[0]["deleted_at"] == ""


def test_想定外の見出しは移行しない():
    with pytest.raises(RuntimeError):
        migrate_add_repeat(FakeWorksheet([["名前", "点数"]]))


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
    assert all(r[5] == r[6] and r[7] == "" and r[8] == "" and r[9] == "" for r in sheet.rows[1:])


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


# ---- 繰り返し ----

def test_繰り返しの文字の読み書き():
    for kind, text in [("day", "毎日"), ("day", "毎週 月・金"), ("day", "毎月 25日"), ("day", "毎月 末日"),
                       ("week", "毎週"), ("month", "毎月")]:
        assert repeat_text(kind, parse_repeat(kind, text)) == text
    assert parse_repeat("day", "毎週 月・金") == {"type": "weekly", "dows": {1, 5}}
    assert parse_repeat("day", "") is None and parse_repeat("day", "ときどき") is None
    assert parse_repeat("day", "毎月 31日") is None and parse_repeat("week", "毎日") is None


def test_次の回の期日():
    past = date(2000, 1, 1)  # 「今日」をずっと前にして、期日の次の回をそのまま見る
    fri = date(2026, 11, 27)
    assert next_due("day", {"type": "daily"}, fri, past) == date(2026, 11, 28)
    assert next_due("day", {"type": "weekly", "dows": {1, 5}}, fri, past) == date(2026, 11, 30)  # 金→月
    assert next_due("day", {"type": "weekly", "dows": {5}}, fri, past) == date(2026, 12, 4)
    assert next_due("day", {"type": "monthly", "dom": 25}, fri, past) == date(2026, 12, 25)
    assert next_due("day", {"type": "monthly", "dom": 28}, date(2026, 11, 10), past) == date(2026, 11, 28)
    assert next_due("day", {"type": "monthly", "dom": "end"}, date(2027, 1, 31), past) == date(2027, 2, 28)
    assert next_due("week", {"type": "weekly"}, date(2026, 11, 28), past) == date(2026, 12, 5)
    assert next_due("month", {"type": "monthly"}, date(2026, 11, 30), past) == date(2026, 12, 31)
    assert next_due("month", {"type": "monthly"}, date(2027, 1, 31), past) == date(2027, 2, 28)


def test_期日を過ぎてから完了したら次の回は今日以降():
    today = date(2026, 11, 27)
    assert next_due("day", {"type": "daily"}, date(2026, 11, 20), today) == today
    assert next_due("day", {"type": "weekly", "dows": {1}}, date(2026, 11, 2), today) == date(2026, 11, 30)
    assert next_due("week", {"type": "weekly"}, date(2026, 11, 14), today) == date(2026, 11, 28)


def test_繰り返しの入力():
    v, e = validate(MultiDict(title="a", kind="day", day="2026-11-27", repeat="weekly", dow=["5", "1"]))
    assert not e and v["repeat"] == "毎週 月・金"
    v, e = validate(MultiDict(title="a", kind="day", day="2026-11-27", repeat="monthly", dom="end"))
    assert not e and v["repeat"] == "毎月 末日"
    _, e = validate(MultiDict(title="a", kind="day", day="2026-11-27", repeat="weekly"))
    assert e == ["繰り返す曜日を選んでください。"]
    _, e = validate(MultiDict(title="a", kind="day", day="2026-11-27", repeat="monthly", dom="31"))
    assert e == ["繰り返す日にちを選んでください。"]
    v, e = validate(MultiDict(title="a", kind="week", week="2026-11-22", repeat="weekly"))
    assert not e and v["repeat"] == "毎週"
    # 週の中でに「毎日」は選べない（見えない欄が残っていても、繰り返さない）
    v, e = validate(MultiDict(title="a", kind="week", week="2026-11-22", repeat="daily"))
    assert not e and v["repeat"] == ""


class MultiDict(dict):
    """フォームのように、同じ名前の欄を複数持てる（dow=["1","5"]）"""
    def __init__(self, **kw):
        super().__init__(kw)

    def getlist(self, k):
        v = self.get(k, [])
        return v if isinstance(v, list) else [v]


def test_繰り返しを完了すると次の回ができる(client, sheet):
    add(client, "ゴミ出し", "day", day(0), repeat="daily")
    first = sheet.rows[1][0]
    assert sheet.rows[1][8] == "毎日"
    res = client.post(f"/todos/{first}/done")
    assert len(sheet.rows) == 3 and sheet.rows[1][7] != ""
    nxt = sheet.rows[2]
    assert nxt[1] == "ゴミ出し" and nxt[3] == "日" and nxt[4] == day(1) and nxt[8] == "毎日" and nxt[7] == ""
    assert f"next={nxt[0]}" in res.location
    page = client.get(res.location).text
    assert "「ゴミ出し」を完了しました。次は" in page and f'name="next" value="{nxt[0]}"' in page
    # 2回押しても次の回は1つだけ
    client.post(f"/todos/{first}/done")
    assert len(sheet.rows) == 3


def test_繰り返しの完了を元に戻すと次の回も消える(client, sheet):
    add(client, "ゴミ出し", "day", day(0), repeat="daily")
    first = sheet.rows[1][0]
    res = client.post(f"/todos/{first}/done")
    nxt = sheet.rows[2][0]
    client.post(f"/todos/{first}/undo", data={"next": nxt})
    assert sheet.rows[1][7] == "" and sheet.rows[2][9] != ""
    page = client.get("/?d=all").text
    assert page.count('<h3 class="title">ゴミ出し</h3>') == 1


def test_一覧と編集画面に繰り返しが出る(client, sheet):
    add(client, "ゴミ出し", "day", day(0), repeat="weekly", dow=["1", "5"])
    assert "↻ 毎週 月・金" in client.get("/").text
    page = client.get(f"/todos/{sheet.rows[1][0]}/edit").text
    assert 'name="repeat" value="weekly" checked' in page
    assert 'name="dow" value="1" checked' in page and 'name="dow" value="5" checked' in page
    assert 'name="dow" value="2" checked' not in page


# ---- 削除 ----

def test_削除すると一覧から消えシートには削除日時が残る(client, sheet):
    add(client, "冬服を整理する", "week")
    todo_id = sheet.rows[1][0]
    assert "このToDoを削除" in client.get(f"/todos/{todo_id}/edit").text
    assert "このToDoを削除" not in client.get("/new").text
    res = client.post(f"/todos/{todo_id}/delete", data={"d": "all"})
    assert "d=all" in res.location and len(sheet.rows) == 2 and sheet.rows[1][9] != ""
    page = client.get(res.location).text
    assert "「冬服を整理する」を削除しました" in page and '<h3 class="title">冬服を整理する</h3>' not in page
    assert client.get(f"/todos/{todo_id}/edit").status_code == 404
    assert client.post(f"/todos/{todo_id}/done").status_code == 404


def test_削除を元に戻せる(client, sheet):
    add(client, "買い物")
    todo_id = sheet.rows[1][0]
    client.post(f"/todos/{todo_id}/delete")
    client.post(f"/todos/{todo_id}/restore")
    assert sheet.rows[1][9] == ""
    assert '<h3 class="title">買い物</h3>' in client.get("/").text
    assert client.post("/todos/nai/restore").status_code == 404
    assert client.post("/todos/nai/delete").status_code == 404


# ---- ふり返り ----

def todo(id, due, done="", kind="day", deleted=""):
    return {"id": id, "title": id, "content": "", "kind": kind, "due_date": due, "created_at": "",
            "updated_at": "", "done_at": done, "repeat": "", "deleted_at": deleted}


def test_ふり返りの数え方():
    today = date(2026, 11, 26)  # 木曜。今週は 11/22〜11/28
    todos = [
        todo("期日どおり", "2026-11-24", "2026-11-23 10:00:00"),
        todo("遅れて完了", "2026-11-20", "2026-11-23 10:00:00"),   # 先週の期日 → 今週に遅れて完了
        todo("まだ・期限切れ", "2026-11-25"),
        todo("まだ・これから", "2026-11-27"),
        todo("先週に完了", "2026-11-21", "2026-11-21 09:00:00"),
    ]
    def check():
        r = build_review(todos, "week", "", today)
        assert r["summary"] == {"done": 2, "over": 1, "open": 2}
        assert r["is_current"] and len(r["bars"]) == 8 and r["bars"][-1]["selected"]
        assert (r["bars"][-1]["ontime"], r["bars"][-1]["over"]) == (1, 1)
        assert (r["bars"][-2]["ontime"], r["bars"][-2]["over"]) == (1, 1)  # 先週：期日どおり1、遅れた1
        assert [t["id"] for d in r["days"] for t in d["items"]] == ["期日どおり", "遅れて完了"]
        last = build_review(todos, "week", "2026-11-18", today)
        assert last["summary"] == {"done": 1, "over": 1, "open": 0} and last["bars"][-2]["selected"]
        m = build_review(todos, "month", "2026-11", today)
        assert m["summary"] == {"done": 3, "over": 2, "open": 2} and len(m["bars"]) == 6
    with_ctx(check)


def test_ふり返りページ(client, sheet):
    add(client, "済んだ用事", "day", day(0))
    add(client, "消した用事", "day", day(0))
    client.post(f"/todos/{sheet.rows[1][0]}/done")
    client.post(f"/todos/{sheet.rows[2][0]}/delete")
    page = client.get("/review").text
    assert "ふり返り" in page and "済んだ用事" in page and "消した用事" not in page
    assert "<svg" in page and "期日までに完了" in page
    assert client.get("/review?view=month&p=2026-13").status_code == 200
    assert client.get("/review?p=xyz").status_code == 200


# ---- LINE ----

def test_LINEの文():
    today = date(2026, 11, 27)
    todos = [todo("レポート", "2026-11-25"), todo("ゴミ出し", "2026-11-27"), todo("冬服", "2026-11-28", kind="week"),
             todo("年賀状", "2026-11-30", kind="month")]
    todos[1]["repeat"] = "毎日"
    text = line_message(todos, today, "https://example.test")
    assert text.startswith("おはようございます。\n11月27日（金）のToDoです。")
    assert "■ 期限切れ 1件\n・レポート（11/25まで）" in text
    assert "■ 今日 1件\n・ゴミ出し ↻" in text and "■ 今週中 1件" in text and "■ 今月中 1件" in text
    assert text.endswith("\n\nhttps://example.test")
    assert "のToDoはありません" in line_message([], today)
    long = line_message([todo("あ" * 100 + str(i), "2026-11-27") for i in range(100)], today)
    assert len(long) <= 5000


def test_LINEの通知は合言葉がないと送らない(sheet, monkeypatch):
    sent = []
    app = create_app(store_factory=lambda: TodoStore(sheet), line_sender=sent.append)
    c = app.test_client()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert c.get("/cron/line").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    assert c.get("/cron/line").status_code == 401
    assert c.get("/cron/line", headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    assert c.get("/cron/line", headers={"Authorization": "Bearer s3cret"}).status_code == 503
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "t")
    monkeypatch.setenv("LINE_USER_ID", "U1")
    assert c.get("/cron/line", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert len(sent) == 1 and "おはようございます" in sent[0]


# ---- ホーム画面に追加 ----

def test_PWAのファイル(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.content_type and sw.headers["Cache-Control"] == "no-cache"
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json["name"] == "ToDoリスト"
    for icon in m.json["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert "つながっていません" in client.get("/offline").text
    page = client.get("/").text
    assert 'rel="manifest"' in page and "apple-touch-icon" in page and "serviceWorker" in page
