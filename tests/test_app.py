from datetime import datetime, timedelta

import pytest

from sheets import HEADERS, JST, TodoStore
from tests.conftest import FakeWorksheet


def today(offset=0):
    return (datetime.now(JST).date() + timedelta(days=offset)).isoformat()


def test_空のシートには見出しが書かれる(sheet):
    TodoStore(sheet)
    assert sheet.rows[0] == HEADERS


def test_見出しが違うシートは使わない():
    with pytest.raises(RuntimeError):
        TodoStore(FakeWorksheet([["名前", "点数"]]))


def test_一覧_空のとき(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "まだ、やることがありません" in res.text


def test_登録すると一覧に出てシートに保存される(client, sheet):
    res = client.post("/new", data={"title": "牛乳を買う", "content": "低脂肪", "due_date": "2026-12-01"})
    assert res.status_code == 302
    assert len(sheet.rows) == 2
    row = sheet.rows[1]
    assert row[1:4] == ["牛乳を買う", "低脂肪", "2026-12-01"]
    assert row[4] == row[5]  # 作成日時＝更新日時

    page = client.get("/?saved=added").text
    assert "牛乳を買う" in page and "低脂肪" in page and "登録しました" in page


def test_タイトルなしは登録できない(client, sheet):
    res = client.post("/new", data={"title": "  ", "content": "x", "due_date": ""})
    assert res.status_code == 400
    assert "タイトルを入力してください" in res.text
    assert sheet.rows[1:] == []  # やることは保存されていない


def test_おかしな日付は登録できない(client, sheet):
    res = client.post("/new", data={"title": "a", "content": "", "due_date": "2026-02-30"})
    assert res.status_code == 400
    assert "正しい日付" in res.text
    assert 'value="a"' in res.text  # 入力した内容は消えない


def test_編集すると上書きされ作成日時は変わらない(client, sheet):
    client.post("/new", data={"title": "古い", "content": "", "due_date": ""})
    todo_id, created = sheet.rows[1][0], sheet.rows[1][4]

    page = client.get(f"/todos/{todo_id}/edit")
    assert page.status_code == 200 and 'value="古い"' in page.text

    res = client.post(f"/todos/{todo_id}/edit", data={"title": "新しい", "content": "メモ", "due_date": "2027-01-05"})
    assert res.status_code == 302
    assert len(sheet.rows) == 2
    assert sheet.rows[1][:5] == [todo_id, "新しい", "メモ", "2027-01-05", created]


def test_同じタイトルでも別々に編集できる(client, sheet):
    client.post("/new", data={"title": "同じ", "content": "1つ目", "due_date": ""})
    client.post("/new", data={"title": "同じ", "content": "2つ目", "due_date": ""})
    second_id = sheet.rows[2][0]
    client.post(f"/todos/{second_id}/edit", data={"title": "同じ", "content": "直した", "due_date": ""})
    assert sheet.rows[1][2] == "1つ目"
    assert sheet.rows[2][2] == "直した"


def test_ないやることの編集は404(client):
    assert client.get("/todos/nai/edit").status_code == 404
    assert client.post("/todos/nai/edit", data={"title": "x"}).status_code == 404


def test_一覧は期日が近い順で期日なしは最後(client):
    client.post("/new", data={"title": "期日なし", "content": "", "due_date": ""})
    client.post("/new", data={"title": "来週", "content": "", "due_date": today(7)})
    client.post("/new", data={"title": "明日", "content": "", "due_date": today(1)})
    page = client.get("/").text
    assert page.index("明日") < page.index("来週") < page.index("期日なし")


def test_期限切れと今日に印が付く(client):
    client.post("/new", data={"title": "昨日のこと", "content": "", "due_date": today(-1)})
    client.post("/new", data={"title": "今日のこと", "content": "", "due_date": today(0)})
    page = client.get("/").text
    assert "期限切れ" in page
    assert 'class="tag today"' in page


def test_数式のような入力も文字のまま表示される(client, sheet):
    client.post("/new", data={"title": "=1+1", "content": "<script>alert(1)</script>", "due_date": ""})
    assert sheet.rows[1][1] == "=1+1"
    page = client.get("/").text
    assert "=1+1" in page
    assert "<script>alert(1)</script>" not in page  # HTML として動かない


def test_シートを手で直して日付が崩れても一覧は表示できる(sheet):
    from app import create_app
    store = TodoStore(sheet)
    sheet.rows.append(["abc", "手で足した", "", "来月くらい", "", ""])
    client = create_app(store_factory=lambda: store).test_client()
    res = client.get("/")
    assert res.status_code == 200 and "来月くらい" in res.text
