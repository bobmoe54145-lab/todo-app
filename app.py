"""Todo リストの Web アプリ（Flask）。

ページ：
  /              やることの一覧
  /new           やることの登録
  /todos/<id>/edit  やることの編集
"""

from datetime import date, datetime

from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, url_for

from sheets import JST, TodoStore, open_worksheet

load_dotenv()

TITLE_MAX = 100
CONTENT_MAX = 2000


def parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def validate(form) -> tuple[dict, list[str]]:
    """入力を整えて確かめる。戻り値は（整えた値, エラーメッセージの一覧）。"""
    values = {
        "title": form.get("title", "").strip(),
        "content": form.get("content", "").strip(),
        "due_date": form.get("due_date", "").strip(),
    }
    errors = []
    if not values["title"]:
        errors.append("タイトルを入力してください。")
    elif len(values["title"]) > TITLE_MAX:
        errors.append(f"タイトルは{TITLE_MAX}文字以内にしてください。")
    if len(values["content"]) > CONTENT_MAX:
        errors.append(f"内容は{CONTENT_MAX}文字以内にしてください。")
    if values["due_date"] and parse_date(values["due_date"]) is None:
        errors.append("期日は正しい日付で入力してください。")
    return values, errors


def create_app(store_factory=None) -> Flask:
    """store_factory：保存先を作る関数。テストでは偽物のシートを渡す。"""
    app = Flask(__name__)
    make_store = store_factory or (lambda: TodoStore(open_worksheet()))
    cache = {}

    def store() -> TodoStore:
        # シートへの接続は最初の1回だけ作って使い回す
        if "store" not in cache:
            cache["store"] = make_store()
        return cache["store"]

    @app.template_filter("due_label")
    def due_label(due: str) -> str:
        """期日の見た目：2026-11-20 → 11/20（金）"""
        d = parse_date(due)
        if d is None:
            return due  # シートを手で直して日付の形が崩れていても、そのまま表示する
        week = "月火水木金土日"[d.weekday()]
        text = f"{d.month}/{d.day}（{week}）"
        if d.year != datetime.now(JST).year:
            text = f"{d.year}/" + text
        return text

    @app.template_filter("due_status")
    def due_status(due: str) -> str:
        """期限切れ・今日が期日、を色分けするための目印"""
        d = parse_date(due)
        if d is None:
            return ""
        today = datetime.now(JST).date()
        if d < today:
            return "overdue"
        if d == today:
            return "today"
        return ""

    @app.get("/")
    def index():
        return render_template("index.html", todos=store().list_all())

    @app.route("/new", methods=["GET", "POST"])
    def new():
        if request.method == "POST":
            values, errors = validate(request.form)
            if not errors:
                store().add(**values)
                return redirect(url_for("index", saved="added"))
            return render_template("form.html", mode="new", todo=values, errors=errors), 400
        empty = {"title": "", "content": "", "due_date": ""}
        return render_template("form.html", mode="new", todo=empty, errors=[])

    @app.route("/todos/<todo_id>/edit", methods=["GET", "POST"])
    def edit(todo_id):
        todo = store().get(todo_id)
        if todo is None:
            abort(404)
        if request.method == "POST":
            values, errors = validate(request.form)
            if not errors:
                store().update(todo_id, **values)
                return redirect(url_for("index", saved="updated"))
            values["id"] = todo_id
            return render_template("form.html", mode="edit", todo=values, errors=errors), 400
        return render_template("form.html", mode="edit", todo=todo, errors=[])

    @app.errorhandler(404)
    def not_found(_):
        return render_template("message.html", message="そのやることは見つかりませんでした。"), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template(
            "message.html",
            message="エラーが起きました。少し待ってから、もう一度お試しください。",
        ), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
