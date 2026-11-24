from datetime import date, datetime, timedelta

import pytest

from app import build_calendar, nav_from, week_start
from sheets import HEADERS, JST, OLD_HEADERS, TodoStore, migrate_headers
from tests.conftest import FakeWorksheet


def today(offset=0):
    return (datetime.now(JST).date() + timedelta(days=offset)).isoformat()


def add(client, title, due="", content=""):
    return client.post("/new", data={"title": title, "content": content, "due_date": due})


# ---- シート ----

def test_空のシートには日本語の見出しが書かれる(sheet):
    TodoStore(sheet)
    assert sheet.rows[0] == ["ID", "タイトル", "内容", "期日", "作成日時", "更新日時", "完了日時"]


def test_見出しが違うシートは使わない():
    with pytest.raises(RuntimeError):
        TodoStore(FakeWorksheet([["名前", "点数"]]))


def test_英語の見出しを日本語に移行してもデータはそのまま():
    row = ["abc", "買い物", "なす", "2026-11-21", "2026-11-21 22:10:01", "2026-11-21 22:10:01"]
    ws = FakeWorksheet([OLD_HEADERS, row])
    assert migrate_headers(ws) is True
    assert ws.rows[0] == HEADERS
    assert ws.rows[1] == row
    assert migrate_headers(ws) is False  # 2回目は何もしない
    assert TodoStore(ws).list_active()[0]["title"] == "買い物"


def test_想定外の見出しは移行しない():
    with pytest.raises(RuntimeError):
        migrate_headers(FakeWorksheet([["名前", "点数"]]))


# ---- 登録・編集 ----

def test_登録するとシートに保存される(client, sheet):
    res = add(client, "牛乳を買う", "2026-12-01", "低脂肪")
    assert res.status_code == 302
    row = sheet.rows[1]
    assert row[1:4] == ["牛乳を買う", "低脂肪", "2026-12-01"]
    assert row[4] == row[5] and row[6] == ""


def test_タイトルなしは登録できない(client, sheet):
    res = add(client, "  ", content="x")
    assert res.status_code == 400
    assert "タイトルを入力してください" in res.text
    assert sheet.rows[1:] == []


def test_おかしな日付は登録できない(client):
    res = add(client, "a", "2026-02-30")
    assert res.status_code == 400
    assert "正しい日付" in res.text
    assert 'value="a"' in res.text


def test_編集すると上書きされ作成日時は変わらない(client, sheet):
    add(client, "古い")
    todo_id, created = sheet.rows[1][0], sheet.rows[1][4]
    assert 'value="古い"' in client.get(f"/todos/{todo_id}/edit").text
    res = client.post(f"/todos/{todo_id}/edit", data={"title": "新しい", "content": "メモ", "due_date": "2027-01-05"})
    assert res.status_code == 302
    assert sheet.rows[1][:5] == [todo_id, "新しい", "メモ", "2027-01-05", created]


def test_同じタイトルでも別々に編集できる(client, sheet):
    add(client, "同じ", content="1つ目")
    add(client, "同じ", content="2つ目")
    client.post(f"/todos/{sheet.rows[2][0]}/edit", data={"title": "同じ", "content": "直した", "due_date": ""})
    assert sheet.rows[1][2] == "1つ目" and sheet.rows[2][2] == "直した"


def test_ないやることは404(client):
    assert client.get("/todos/nai/edit").status_code == 404
    assert client.post("/todos/nai/done").status_code == 404
    assert client.post("/todos/nai/undo").status_code == 404


def test_数式のような入力も文字のまま(client, sheet):
    add(client, "=1+1", content="<script>alert(1)</script>")
    assert sheet.rows[1][1] == "=1+1"
    page = client.get("/?d=all").text
    assert "=1+1" in page and "<script>alert(1)</script>" not in page


# ---- 一覧とカレンダー ----

def test_最初は今日が選ばれていて今日のやることだけ出る(client):
    add(client, "今日のこと", today(0))
    add(client, "明日のこと", today(1))
    page = client.get("/").text
    assert "今日のこと" in page and "明日のこと" not in page
    assert 'aria-current="date"' in page


def test_すべて表示は期日が近い順で期日なしは最後(client):
    add(client, "期日なし")
    add(client, "来週", today(7))
    add(client, "明日", today(1))
    page = client.get("/?d=all").text
    assert "すべてのやること" in page
    assert page.index("明日") < page.index("来週") < page.index("期日なし")


def test_日付を選ぶとその日だけ出る(client):
    add(client, "A", "2026-12-24")
    add(client, "B", "2026-12-25")
    page = client.get("/?d=2026-12-24").text
    assert "12/24（木）のやること" in page
    assert ">A<" in page and ">B<" not in page


def test_週の帯は日曜始まりで件数と期限切れが分かる():
    t = date(2026, 11, 24)  # 火曜
    todos = [{"due_date": "2026-11-23"}, {"due_date": "2026-11-23"}, {"due_date": "2026-11-25"}]
    with_app_context(lambda: _check_week(todos, t))


def _check_week(todos, t):
    cal = build_calendar(todos, {}, t)
    assert cal["mode"] == "week" and cal["label"] == "11/22 〜 11/28"
    days = {d["date"]: d for d in cal["days"]}
    assert days["2026-11-23"]["count"] == 2 and days["2026-11-23"]["overdue"]
    assert days["2026-11-25"]["count"] == 1 and not days["2026-11-25"]["overdue"]
    assert days["2026-11-24"]["today"] and days["2026-11-24"]["selected"]
    assert "d=all" in days["2026-11-24"]["url"]  # 選んでいる日をもう一度押すとすべて表示


def test_月表示は月の前後を含めて週単位で並ぶ():
    def check():
        cal = build_calendar([], {"view": "month", "m": "2026-11"}, date(2026, 11, 24))
        assert cal["label"] == "2026年11月"
        assert cal["days"][0]["date"] == "2026-11-01"  # 11/1 は日曜
        assert cal["days"][-1]["date"] == "2026-12-05"
        assert cal["days"][-1]["out"]
        feb = build_calendar([], {"view": "month", "m": "2027-02"}, date(2026, 11, 24))
        assert feb["days"][0]["date"] == "2027-01-31" and len(feb["days"]) % 7 == 0
    with_app_context(check)


def test_週の始まりは日曜():
    assert week_start(date(2026, 11, 22)) == date(2026, 11, 22)  # 日曜
    assert week_start(date(2026, 11, 28)) == date(2026, 11, 22)  # 土曜


def test_おかしなパラメータは捨てる():
    assert nav_from({"d": "x", "w": "2026-13-01", "view": "year", "m": "abc"}) == {}
    assert nav_from({"d": "all", "view": "month", "m": "2026-11"}) == {"d": "all", "view": "month", "m": "2026-11"}


def test_週を移動するリンクと月表示が出る(client):
    page = client.get("/?w=2026-12-02").text
    assert "11/29 〜 12/5" in page
    assert "月表示" in page
    page = client.get("/?view=month&m=2026-12").text
    assert "2026年12月" in page and "週表示" in page


# ---- 完了 ----

def test_完了すると一覧から消えシートには完了日時が残る(client, sheet):
    add(client, "買い物", today(0))
    todo_id = sheet.rows[1][0]
    res = client.post(f"/todos/{todo_id}/done", data={"d": today(0)})
    assert res.status_code == 302
    assert f"d={today(0)}" in res.location and f"done={todo_id}" in res.location
    assert sheet.rows[1][6] != ""  # 完了日時
    assert len(sheet.rows) == 2  # 行は消えない
    page = client.get(res.location).text
    assert "「買い物」を完了しました" in page and "元に戻す" in page
    assert '<h3 class="title">買い物</h3>' not in page


def test_元に戻すと一覧に戻る(client, sheet):
    add(client, "買い物", today(0))
    todo_id = sheet.rows[1][0]
    client.post(f"/todos/{todo_id}/done")
    res = client.post(f"/todos/{todo_id}/undo", data={"d": "all"})
    assert sheet.rows[1][6] == ""
    assert "d=all" in res.location
    assert '<h3 class="title">買い物</h3>' in client.get(res.location).text


def test_編集のあとも見ていた表示に戻る(client, sheet):
    add(client, "A", "2026-12-24")
    todo_id = sheet.rows[1][0]
    page = client.get(f"/todos/{todo_id}/edit?d=2026-12-24&w=2026-12-24").text
    assert "d=2026-12-24" in page
    res = client.post(f"/todos/{todo_id}/edit?d=2026-12-24&w=2026-12-24", data={"title": "A2", "content": "", "due_date": "2026-12-24"})
    assert "d=2026-12-24" in res.location and "w=2026-12-24" in res.location


def test_日付を選んで登録すると期日が入っている(client):
    assert 'value="2026-12-24"' in client.get("/new?d=2026-12-24").text


def test_シートを手で直して日付が崩れても一覧は表示できる(sheet):
    from app import create_app
    store = TodoStore(sheet)
    sheet.rows.append(["abc", "手で足した", "", "来月くらい", "", "", ""])
    res = create_app(store_factory=lambda: store).test_client().get("/?d=all")
    assert res.status_code == 200 and "来月くらい" in res.text


def with_app_context(fn):
    from app import create_app
    app = create_app(store_factory=lambda: TodoStore(FakeWorksheet()))
    with app.test_request_context("/"):
        fn()
