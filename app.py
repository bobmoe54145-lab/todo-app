"""ToDo リストの Web アプリ（Flask）。

ページ：
  /                    一覧（上に1週間の帯 or 月のカレンダー、下に ToDo）
  /new                 ToDo の登録
  /todos/<id>/edit     ToDo の編集
  /todos/<id>/done     完了にする（POST）
  /todos/<id>/undo     完了を元に戻す（POST）
  /login               ログイン（auth.py）

ToDo の種類（kind）：
  day   その日にやる。その日だけ出る
  week  その週の中でやる（日曜〜土曜）。その週の毎日、完了するまで出る
  month その月の中でやる。その月の毎日、完了するまで出る
期日（due_date）はどの種類でも「この日までに」の日付（週＝土曜、月＝末日）。
期日を過ぎて完了していないものは、今日の一覧の「期限切れ」に出続ける。

一覧の表示の状態は URL のパラメータで持つ（完了・編集のあとも同じ表示に戻すため）：
  d    選んでいる日（YYYY-MM-DD）。"all" ならすべて表示。ないときは今日
  w    週表示で見ている週の中の1日（YYYY-MM-DD）
  view "month" なら月表示
  m    月表示で見ている月（YYYY-MM）
"""

from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import init_auth
from sheets import JST, TodoStore, open_worksheet

load_dotenv()

TITLE_MAX = 100
CONTENT_MAX = 2000
WEEKDAYS = "月火水木金土日"  # date.weekday() の順（月曜=0）
KINDS = ("day", "week", "month")


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


def week_end(d: date) -> date:
    """d を含む週の土曜日"""
    return week_start(d) + timedelta(days=6)


def month_end(d: date) -> date:
    """d を含む月の末日"""
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def deadline_for(kind: str, d: date) -> date:
    """種類と、その期間の中の1日から、期日（この日までに）を決める"""
    if kind == "week":
        return week_end(d)
    if kind == "month":
        return month_end(d)
    return d


def jp_date(d: date) -> str:
    """2026-11-20 → 11/20（金）"""
    text = f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"
    if d.year != today_jst().year:
        text = f"{d.year}/" + text
    return text


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
    """入力を整えて確かめる。戻り値は（保存する値, エラーメッセージの一覧）。

    フォームの項目：title, content, kind（day/week/month）,
    day（YYYY-MM-DD）, week（その週の日曜 YYYY-MM-DD）, month（YYYY-MM）
    """
    values = {
        "title": form.get("title", "").strip(),
        "content": form.get("content", "").strip(),
        "kind": form.get("kind", "day"),
        "due_date": "",
    }
    errors = []
    if not values["title"]:
        errors.append("タイトルを入力してください。")
    elif len(values["title"]) > TITLE_MAX:
        errors.append(f"タイトルは{TITLE_MAX}文字以内にしてください。")
    if len(values["content"]) > CONTENT_MAX:
        errors.append(f"内容は{CONTENT_MAX}文字以内にしてください。")

    kind = values["kind"]
    if kind not in KINDS:
        errors.append("いつやるかを選んでください。")
        values["kind"] = "day"
    else:
        picked = (
            parse_month(form.get("month", "")) if kind == "month"
            else parse_date(form.get(kind, ""))
        )
        if picked is None:
            errors.append({"day": "日付", "week": "週", "month": "月"}[kind] + "を選んでください。")
        else:
            values["due_date"] = deadline_for(kind, picked).isoformat()
    return values, errors


def form_choices(todo: dict, base: date) -> dict:
    """登録・編集画面の「週」「月」の選択肢と、最初に選ばれている値"""
    due = parse_date(todo.get("due_date", ""))
    kind = todo.get("kind", "week")
    # 最初に選ばれている日付・週・月（編集なら今の値、登録なら選んでいた日）
    pick = due if due else base
    selected = {
        "day": pick.isoformat(),
        "week": week_start(pick).isoformat(),
        "month": pick.strftime("%Y-%m"),
    }
    today = today_jst()
    first_week = min(week_start(today), week_start(pick))
    weeks = []
    for i in range(((week_start(pick) - first_week).days // 7) + 12):
        s = first_week + timedelta(days=7 * i)
        prefix = "今週　" if s == week_start(today) else "来週　" if s == week_start(today) + timedelta(days=7) else ""
        weeks.append({"value": s.isoformat(), "label": f"{prefix}{jp_date(s)}〜{jp_date(s + timedelta(days=6))}"})
    first_month = min(today.replace(day=1), pick.replace(day=1))
    months = []
    m = first_month
    while len(months) < 12 or m <= pick.replace(day=1):
        prefix = "今月　" if m == today.replace(day=1) else ""
        if m == month_end(today) + timedelta(days=1):
            prefix = "来月　"
        months.append({"value": m.strftime("%Y-%m"), "label": f"{prefix}{m.year}年{m.month}月"})
        m = month_end(m) + timedelta(days=1)
    return {"kind": kind, "selected": selected, "weeks": weeks, "months": months}


def build_calendar(todos: list[dict], nav: dict, today: date) -> dict:
    """カレンダー（週の帯 or 月）の表示に必要なものをまとめる。
    各日の件数は「その日にやる」ToDo だけを数える。週・月の ToDo は別に数える。"""
    selected = None if nav.get("d") == "all" else (parse_date(nav.get("d", "")) or today)
    by_day: dict[str, int] = {}
    for t in todos:
        if t["kind"] == "day" and parse_date(t["due_date"]):
            by_day[t["due_date"]] = by_day.get(t["due_date"], 0) + 1

    def count(kind: str, due: date) -> int:
        return sum(1 for t in todos if t["kind"] == kind and t["due_date"] == due.isoformat())

    def link(**changes):
        params = {**nav, **changes}
        return url_for("index", **{k: v for k, v in params.items() if v})

    def day_info(d: date, out: bool = False) -> dict:
        key = d.isoformat()
        n = by_day.get(key, 0)
        return {
            "date": key,
            "day": d.day,
            "dow": (d.weekday() + 1) % 7,  # 0=日曜 … 6=土曜
            "count": n,
            "overdue": bool(n) and d < today,
            "today": d == today,
            "selected": d == selected,
            "out": out,
            "label": jp_date(d),
            # 選んでいる日をもう一度押したら「すべて表示」に戻す
            "url": link(d="all" if d == selected else key),
        }

    if nav.get("view") == "month":
        first = parse_month(nav.get("m", "")) or (selected or today).replace(day=1)
        start = week_start(first)
        last = month_end(first)
        end = week_end(last)
        days = [day_info(start + timedelta(days=i), out=(start + timedelta(days=i)).month != first.month)
                for i in range((end - start).days + 1)]
        prev_first = (first - timedelta(days=1)).replace(day=1)
        next_first = last + timedelta(days=1)
        return {
            "mode": "month",
            "label": f"{first.year}.{first.month}",
            "sub": "",
            "periods": [{"label": f"{first.month}月中", "count": count("month", last)}],
            "days": days,
            "prev_url": link(m=prev_first.strftime("%Y-%m")),
            "next_url": link(m=next_first.strftime("%Y-%m")),
            "prev_label": "前の月",
            "next_label": "次の月",
            "today_url": url_for("index", view="month"),
            "today_label": "今日",
            "switch_url": link(view="", m="", w=(selected or first).isoformat()),
            "switch_label": "週表示",
            "selected": selected,
        }

    start = week_start(parse_date(nav.get("w", "")) or selected or today)
    end = start + timedelta(days=6)
    month_ref = selected if selected and start <= selected <= end else start
    return {
        "mode": "week",
        "label": f"{start.month}.{start.day} — {end.month}.{end.day}",
        "sub": str(start.year),
        "periods": [
            {"label": "この週中", "count": count("week", end)},
            {"label": f"{month_ref.month}月中", "count": count("month", month_end(month_ref))},
        ],
        "days": [day_info(start + timedelta(days=i)) for i in range(7)],
        "prev_url": link(w=(start - timedelta(days=7)).isoformat()),
        "next_url": link(w=(start + timedelta(days=7)).isoformat()),
        "prev_label": "前の週",
        "next_label": "次の週",
        "today_url": url_for("index"),
        "today_label": "今週",
        "switch_url": link(view="month", w="", m=(selected or start).strftime("%Y-%m")),
        "switch_label": "月表示",
        "selected": selected,
    }


def build_groups(todos: list[dict], selected: date | None, today: date) -> list[dict]:
    """一覧に出すまとまり（期限切れ・その日・週・月）を作る。todos は期日が近い順。"""
    today_key = today.isoformat()
    overdue = [t for t in todos if parse_date(t["due_date"]) and t["due_date"] < today_key]
    groups = []

    if selected is None:
        # すべて表示
        rest = [t for t in todos if t not in overdue]
        if overdue:
            groups.append({"name": "期限切れ", "sub": "", "items": overdue, "overdue": True})
        for kind, name, sub in (("day", "その日にやる", "日付が近い順"),
                                ("week", "週の中でやる", "週が近い順"),
                                ("month", "月の中でやる", "月が近い順")):
            groups.append({"name": name, "sub": sub, "items": [t for t in rest if t["kind"] == kind],
                           "empty": "ありません"})
        return groups

    is_today = selected == today
    s_week_end, s_month_end = week_end(selected), month_end(selected)
    if is_today and overdue:
        groups.append({"name": "期限切れ", "sub": "", "items": overdue, "overdue": True})

    def pick(kind: str, due: date) -> list[dict]:
        items = [t for t in todos if t["kind"] == kind and t["due_date"] == due.isoformat()]
        # 今日の一覧では、期限切れに出したものは重ねて出さない
        return [t for t in items if t not in overdue] if is_today else items

    this_week = week_start(selected) == week_start(today)
    this_month = selected.replace(day=1) == today.replace(day=1)
    groups.append({"name": "今日やること" if is_today else "この日にやること", "sub": "",
                   "items": pick("day", selected),
                   "empty": "今日やることはありません" if is_today else "この日にやることはありません"})
    ws = week_start(selected)
    groups.append({"name": "今週中" if this_week else "この週中",
                   "sub": f"{ws.month}/{ws.day}〜{s_week_end.month}/{s_week_end.day}",
                   "items": pick("week", s_week_end), "empty": "この週にやることはありません"})
    groups.append({"name": "今月中" if this_month else "この月中", "sub": f"{selected.month}月",
                   "items": pick("month", s_month_end), "empty": "この月にやることはありません"})
    return groups


def create_app(store_factory=None, testing: bool = False) -> Flask:
    """store_factory：保存先を作る関数。テストでは偽物のシートを渡す。"""
    app = Flask(__name__)
    app.config["TESTING"] = testing
    # 公開先（Vercel）は前に中継サーバーがあるので、https や URL を正しく受け取るための設定
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    init_auth(app)
    make_store = store_factory or (lambda: TodoStore(open_worksheet()))
    cache = {}

    def store() -> TodoStore:
        # シートへの接続は最初の1回だけ作って使い回す
        if "store" not in cache:
            cache["store"] = make_store()
        return cache["store"]

    @app.template_filter("due_label")
    def due_label(due: str) -> str:
        d = parse_date(due)
        return jp_date(d) if d else due  # 形が崩れていても、そのまま表示する

    @app.template_filter("when_label")
    def when_label(t: dict) -> str:
        """ToDo のいつまでか：その日＝11/24（火）／週＝11/22〜11/28（土）まで／月＝11月中（11/30（月）まで）"""
        d = parse_date(t["due_date"])
        if d is None:
            return t["due_date"] or "期日なし"
        if t["kind"] == "week":
            s = d - timedelta(days=6)
            return f"{s.month}/{s.day}〜{jp_date(d)}まで"
        if t["kind"] == "month":
            return f"{d.month}月中（{jp_date(d)}まで）"
        return jp_date(d)

    @app.template_filter("is_overdue")
    def is_overdue(t: dict) -> bool:
        d = parse_date(t["due_date"])
        return bool(d) and d < today_jst()

    @app.get("/")
    def index():
        nav = nav_from(request.args)
        todos = store().list_active()
        today = today_jst()
        cal = build_calendar(todos, nav, today)
        groups = build_groups(todos, cal["selected"], today)
        # 完了した直後なら「元に戻す」を出す
        done_todo = None
        done_id = request.args.get("done")
        if done_id:
            t = store().get(done_id)
            if t and t["done_at"]:
                done_todo = t
        return render_template(
            "index.html", cal=cal, groups=groups, nav=nav, done_todo=done_todo,
            all_url=url_for("index", **{**nav, "d": "all"}),
        )

    def base_date(nav: dict) -> date:
        return parse_date(nav.get("d", "")) or today_jst()

    @app.route("/new", methods=["GET", "POST"])
    def new():
        nav = nav_from(request.args)
        if request.method == "POST":
            values, errors = validate(request.form)
            if not errors:
                store().add(**values)
                return redirect(url_for("index", **nav, saved="added"))
            return render_template("form.html", mode="new", todo=values, errors=errors, nav=nav,
                                   choices=form_choices(values, base_date(nav))), 400
        # 日付を選んでいたら「その日」、選んでいなければ「今週中」を最初に選んでおく
        kind = "day" if parse_date(nav.get("d", "")) and nav["d"] != today_jst().isoformat() else "week"
        empty = {"title": "", "content": "", "kind": kind, "due_date": ""}
        return render_template("form.html", mode="new", todo=empty, errors=[], nav=nav,
                               choices=form_choices(empty, base_date(nav)))

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
            if not values["due_date"]:
                values["due_date"] = todo["due_date"]
            return render_template("form.html", mode="edit", todo=values, errors=errors, nav=nav,
                                   choices=form_choices(values, base_date(nav))), 400
        return render_template("form.html", mode="edit", todo=todo, errors=[], nav=nav,
                               choices=form_choices(todo, base_date(nav)))

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
        return render_template("message.html", message="そのToDoは見つかりませんでした。"), 404

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
