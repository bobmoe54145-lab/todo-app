"""Todo リストの Web アプリ（Flask）。

ページ：
  /                    一覧（上に1週間の帯 or 月のカレンダー、下にやること）
  /new                 やることの登録
  /todos/<id>/edit     やることの編集
  /todos/<id>/done     完了にする（POST）
  /todos/<id>/undo     完了を元に戻す（POST）

一覧の表示の状態は URL のパラメータで持つ（完了・編集のあとも同じ表示に戻すため）：
  d    選んでいる日（YYYY-MM-DD）。"all" ならすべて表示。ないときは今日
  w    週表示で見ている週の中の1日（YYYY-MM-DD）
  view "month" なら月表示
  m    月表示で見ている月（YYYY-MM）
"""

from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, url_for

from sheets import JST, TodoStore, open_worksheet

load_dotenv()

TITLE_MAX = 100
CONTENT_MAX = 2000
WEEKDAYS = "月火水木金土日"  # date.weekday() の順（月曜=0）


def today_jst() -> date:
    return datetime.now(JST).date()


def parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def parse_month(text: str) -> date | None:
    """"2026-11" → 2026-11-01"""
    return parse_date(f"{text}-01") if text and len(text) == 7 else None


def week_start(d: date) -> date:
    """d を含む週の日曜日"""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def nav_from(args) -> dict:
    """URL のパラメータから、一覧の表示の状態を取り出す（おかしな値は捨てる）"""
    nav = {}
    d = args.get("d", "")
    if d == "all" or parse_date(d):
        nav["d"] = d
    if parse_date(args.get("w", "")):
        nav["w"] = args["w"]
    if args.get("view") == "month":
        nav["view"] = "month"
    if parse_month(args.get("m", "")):
        nav["m"] = args["m"]
    return nav


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


def build_calendar(todos: list[dict], nav: dict, today: date) -> dict:
    """カレンダー（週の帯 or 月）の表示に必要なものをまとめる"""
    selected = None if nav.get("d") == "all" else (parse_date(nav.get("d", "")) or today)
    by_day: dict[str, list[dict]] = {}
    for t in todos:
        if parse_date(t["due_date"]):
            by_day.setdefault(t["due_date"], []).append(t)

    def link(**changes):
        params = {**nav, **changes}
        return url_for("index", **{k: v for k, v in params.items() if v})

    def day_info(d: date, out: bool = False) -> dict:
        key = d.isoformat()
        items = by_day.get(key, [])
        return {
            "date": key,
            "day": d.day,
            "dow": (d.weekday() + 1) % 7,  # 0=日曜 … 6=土曜
            "count": len(items),
            "overdue": bool(items) and d < today,
            "today": d == today,
            "selected": d == selected,
            "out": out,
            # 選んでいる日をもう一度押したら「すべて表示」に戻す
            "url": link(d="all" if d == selected else key),
        }

    if nav.get("view") == "month":
        first = parse_month(nav.get("m", "")) or (selected or today).replace(day=1)
        start = week_start(first)
        next_first = (first + timedelta(days=32)).replace(day=1)
        last = next_first - timedelta(days=1)
        end = week_start(last) + timedelta(days=6)
        days = [day_info(start + timedelta(days=i), out=(start + timedelta(days=i)).month != first.month)
                for i in range((end - start).days + 1)]
        prev_first = (first - timedelta(days=1)).replace(day=1)
        return {
            "mode": "month",
            "label": f"{first.year}年{first.month}月",
            "days": days,
            "prev_url": link(m=prev_first.strftime("%Y-%m")),
            "next_url": link(m=next_first.strftime("%Y-%m")),
            "today_url": url_for("index", view="month"),
            "today_label": "今日",
            "switch_url": link(view="", m="", w=(selected or first).isoformat()),
            "switch_label": "週表示",
            "selected": selected,
        }

    start = week_start(parse_date(nav.get("w", "")) or selected or today)
    end = start + timedelta(days=6)
    return {
        "mode": "week",
        "label": f"{start.month}/{start.day} 〜 {end.month}/{end.day}",
        "days": [day_info(start + timedelta(days=i)) for i in range(7)],
        "prev_url": link(w=(start - timedelta(days=7)).isoformat()),
        "next_url": link(w=(start + timedelta(days=7)).isoformat()),
        "today_url": url_for("index"),
        "today_label": "今週",
        "switch_url": link(view="month", w="", m=(selected or start).strftime("%Y-%m")),
        "switch_label": "月表示",
        "selected": selected,
    }


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
        text = f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"
        if d.year != today_jst().year:
            text = f"{d.year}/" + text
        return text

    @app.template_filter("due_status")
    def due_status(due: str) -> str:
        """期限切れ・今日が期日、を色分けするための目印"""
        d = parse_date(due)
        if d is None:
            return ""
        today = today_jst()
        if d < today:
            return "overdue"
        if d == today:
            return "today"
        return ""

    @app.get("/")
    def index():
        nav = nav_from(request.args)
        todos = store().list_active()
        cal = build_calendar(todos, nav, today_jst())
        if cal["selected"]:
            shown = [t for t in todos if t["due_date"] == cal["selected"].isoformat()]
        else:
            shown = todos
        # 完了した直後なら「元に戻す」を出す
        done_todo = None
        done_id = request.args.get("done")
        if done_id:
            t = store().get(done_id)
            if t and t["done_at"]:
                done_todo = t
        return render_template(
            "index.html", cal=cal, todos=shown, nav=nav, done_todo=done_todo,
            all_url=url_for("index", **{**nav, "d": "all"}),
        )

    @app.route("/new", methods=["GET", "POST"])
    def new():
        nav = nav_from(request.args)
        if request.method == "POST":
            values, errors = validate(request.form)
            if not errors:
                store().add(**values)
                return redirect(url_for("index", **nav, saved="added"))
            return render_template("form.html", mode="new", todo=values, errors=errors, nav=nav), 400
        empty = {"title": "", "content": "", "due_date": ""}
        # 日付を選んでいる状態から登録するときは、その日を期日に入れておく
        if parse_date(nav.get("d", "")):
            empty["due_date"] = nav["d"]
        return render_template("form.html", mode="new", todo=empty, errors=[], nav=nav)

    @app.route("/todos/<todo_id>/edit", methods=["GET", "POST"])
    def edit(todo_id):
        nav = nav_from(request.args)
        todo = store().get(todo_id)
        if todo is None:
            abort(404)
        if request.method == "POST":
            values, errors = validate(request.form)
            if not errors:
                store().update(todo_id, **values)
                return redirect(url_for("index", **nav, saved="updated"))
            values["id"] = todo_id
            return render_template("form.html", mode="edit", todo=values, errors=errors, nav=nav), 400
        return render_template("form.html", mode="edit", todo=todo, errors=[], nav=nav)

    @app.post("/todos/<todo_id>/done")
    def done(todo_id):
        if not store().set_done(todo_id, True):
            abort(404)
        return redirect(url_for("index", **nav_from(request.form), done=todo_id))

    @app.post("/todos/<todo_id>/undo")
    def undo(todo_id):
        if not store().set_done(todo_id, False):
            abort(404)
        return redirect(url_for("index", **nav_from(request.form), saved="undone"))

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
