"""ToDo リストの Web アプリ（Flask）。

ページ：
  /                    一覧（上に1週間の帯 or 月のカレンダー、下に ToDo）
  /new                 ToDo の登録
  /todos/<id>/edit     ToDo の編集
  /todos/<id>/done     完了にする（POST）
  /todos/<id>/undo     完了を元に戻す（POST）
  /todos/<id>/delete   削除する（POST。シートの行は残して削除日時を書く）
  /todos/<id>/restore  削除を元に戻す（POST）
  /review              ふり返り（週ごと・月ごとの完了数のグラフと、終えた ToDo の一覧）
  /cron/line           今日の ToDo を LINE に送る（Vercel Cron が毎朝呼ぶ）
  /sw.js /manifest.webmanifest /offline   ホーム画面に追加（PWA）用

ToDo の種類（kind）：
  day   その日にやる。その日だけ出る
  week  その週の中でやる（日曜〜土曜）。その週の毎日、完了するまで出る
  month その月の中でやる。その月の毎日、完了するまで出る
期日（due_date）はどの種類でも「この日までに」の日付（週＝土曜、月＝末日）。
期日を過ぎて完了していないものは、今日の一覧の「期限切れ」に出続ける。

繰り返し（repeat）：完了すると、次の回を新しい ToDo として作る（終えた回はシートに残る）。
  その日に：毎日／毎週（曜日をいくつでも）／毎月（1〜28日か末日）
  週の中で：毎週　　月の中で：毎月

一覧の表示の状態は URL のパラメータで持つ（完了・編集のあとも同じ表示に戻すため）：
  d    選んでいる日（YYYY-MM-DD）。"all" ならすべて表示。ないときは今日
  w    週表示で見ている週の中の1日（YYYY-MM-DD）
  view "month" なら月表示
  m    月表示で見ている月（YYYY-MM）
"""

import json
import os
import urllib.request
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, abort, make_response, redirect, render_template, request, send_from_directory, url_for

from sheets import JST, TodoStore, open_worksheet

load_dotenv()

TITLE_MAX = 100
CONTENT_MAX = 2000
WEEKDAYS = "月火水木金土日"  # date.weekday() の順（月曜=0）
KINDS = ("day", "week", "month")
DOWS_SUN = "日月火水木金土"  # 日曜=0 の順（繰り返しの曜日で使う）
REPEATS = ("none", "daily", "weekly", "monthly")
APP_URL = "https://todo-app-ten-chi-73.vercel.app"
LINE_MAX = 5000  # LINE の1通の上限（文字）


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


def sun_dow(d: date) -> int:
    """日曜=0 … 土曜=6"""
    return (d.weekday() + 1) % 7


def parse_repeat(kind: str, text: str) -> dict | None:
    """シートの「繰り返し」の文字 → {"type": daily/weekly/monthly, "dows": {0..6}, "dom": 1..28 か "end"}。
    空や読めない文字なら None（繰り返さない）"""
    text = (text or "").strip()
    if kind == "week":
        return {"type": "weekly"} if text == "毎週" else None
    if kind == "month":
        return {"type": "monthly"} if text == "毎月" else None
    if text == "毎日":
        return {"type": "daily"}
    if text.startswith("毎週 "):
        dows = {DOWS_SUN.index(c) for c in text[3:].split("・") if c and c in DOWS_SUN}
        return {"type": "weekly", "dows": dows} if dows else None
    if text == "毎月 末日":
        return {"type": "monthly", "dom": "end"}
    if text.startswith("毎月 ") and text.endswith("日") and text[3:-1].isdigit() and 1 <= int(text[3:-1]) <= 28:
        return {"type": "monthly", "dom": int(text[3:-1])}
    return None


def repeat_text(kind: str, spec: dict | None) -> str:
    """parse_repeat の逆：シートに書く文字"""
    if not spec:
        return ""
    if kind == "week":
        return "毎週"
    if kind == "month":
        return "毎月"
    if spec["type"] == "daily":
        return "毎日"
    if spec["type"] == "weekly":
        return "毎週 " + "・".join(DOWS_SUN[i] for i in sorted(spec["dows"]))
    return "毎月 末日" if spec["dom"] == "end" else f"毎月 {spec['dom']}日"


def next_due(kind: str, spec: dict, due: date, today: date) -> date:
    """繰り返しの次の回の期日。期日を過ぎてから完了したときは、今日以降の最初の回にする"""
    def step(d: date) -> date:
        if kind == "week":
            return d + timedelta(days=7)
        if kind == "month":
            return month_end(month_end(d) + timedelta(days=1))
        if spec["type"] == "daily":
            return d + timedelta(days=1)
        if spec["type"] == "weekly":
            return next(d + timedelta(days=i) for i in range(1, 8) if sun_dow(d + timedelta(days=i)) in spec["dows"])
        # 毎月：同じ月の中でまだ来ていなければその日、来ていれば次の月
        for first in (d.replace(day=1), month_end(d) + timedelta(days=1)):
            x = month_end(first) if spec["dom"] == "end" else first.replace(day=spec["dom"])
            if x > d:
                return x
        raise AssertionError("ここには来ない")

    n = step(due)
    while n < today:
        n = step(n)
    return n


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
        "repeat": "",
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
        repeat, error = read_repeat(form, kind)
        values["repeat"] = repeat
        if error:
            errors.append(error)
    return values, errors


def read_repeat(form, kind: str) -> tuple[str, str]:
    """フォームの繰り返しの欄 → （シートに書く文字, エラー）。
    項目：repeat（none/daily/weekly/monthly）、dow（曜日 0〜6、いくつでも）、dom（1〜28 か end）"""
    choice = form.get("repeat", "none")
    if choice not in REPEATS or choice == "none":
        return "", ""
    if kind != "day":
        # 週の中で → 毎週、月の中で → 毎月 だけ選べる
        return (repeat_text(kind, {"type": choice}), "") if choice == {"week": "weekly", "month": "monthly"}[kind] else ("", "")
    if choice == "daily":
        return "毎日", ""
    if choice == "weekly":
        values = form.getlist("dow") if hasattr(form, "getlist") else form.get("dow", [])
        dows = {int(v) for v in values if str(v).isdigit() and 0 <= int(v) <= 6}
        if not dows:
            return "", "繰り返す曜日を選んでください。"
        return repeat_text("day", {"type": "weekly", "dows": dows}), ""
    dom = form.get("dom", "")
    if dom == "end":
        return "毎月 末日", ""
    if dom.isdigit() and 1 <= int(dom) <= 28:
        return f"毎月 {int(dom)}日", ""
    return "", "繰り返す日にちを選んでください。"


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
    spec = parse_repeat(kind, todo.get("repeat", "")) or {}
    repeat = {
        "type": spec.get("type", "none"),
        "dows": spec.get("dows") or {sun_dow(pick)},
        "dom": str(spec.get("dom") or min(pick.day, 28)),
    }
    return {"kind": kind, "selected": selected, "weeks": weeks, "months": months, "repeat": repeat,
            "doms": [str(i) for i in range(1, 29)]}


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


def done_date(t: dict) -> date | None:
    """完了日時（"2026-11-24 10:00:00"）の日付"""
    return parse_date((t.get("done_at") or "")[:10])


def is_late(t: dict) -> bool:
    """期日を過ぎてから完了したか"""
    d, due = done_date(t), parse_date(t["due_date"])
    return bool(d and due and d > due)


def build_review(todos: list[dict], view: str, p: str, today: date) -> dict:
    """ふり返りページ：週（日曜始まり）か月ごとに数える。todos は削除していないもの全部（完了も含む）。

    期日までに完了：その期間に完了し、完了した日が期日以前のもの（グラフの墨色）
    期日を過ぎた　：期日がその期間にあり、期日までに完了しなかったもの（グラフのえんじ）
    残っている　　：期日がその期間にあり、まだ完了していないもの
    """
    if view == "month":
        n_bars = 6
        current = today.replace(day=1)
        sel = parse_month(p) or current

        def shift(d: date, k: int) -> date:
            for _ in range(abs(k)):
                d = (month_end(d) + timedelta(days=1)) if k > 0 else (d - timedelta(days=1)).replace(day=1)
            return d

        def span(d: date) -> tuple[date, date]:
            return d, month_end(d)

        def key(d: date) -> str:
            return d.strftime("%Y-%m")

        def short(d: date) -> str:
            return f"{d.month}月"

        def title(d: date) -> str:
            return f"{d.year}年{d.month}月" + ("（今月）" if d == current else "")
    else:
        view = "week"
        n_bars = 8
        current = week_start(today)
        sel = week_start(parse_date(p) or today)

        def shift(d: date, k: int) -> date:
            return d + timedelta(days=7 * k)

        def span(d: date) -> tuple[date, date]:
            return d, d + timedelta(days=6)

        def key(d: date) -> str:
            return d.isoformat()

        def short(d: date) -> str:
            return f"{d.month}/{d.day}"

        def title(d: date) -> str:
            e = d + timedelta(days=6)
            return f"{jp_date(d)}〜{jp_date(e)}" + ("（今週）" if d == current else "")

    def inside(d: date | None, s: date, e: date) -> bool:
        return bool(d) and s <= d <= e

    def counts(start: date) -> dict:
        s, e = span(start)
        done_in = [t for t in todos if inside(done_date(t), s, e)]
        due_in = [t for t in todos if inside(parse_date(t["due_date"]), s, e)]
        return {
            "done": len(done_in),
            "ontime": sum(1 for t in done_in if not is_late(t)),
            "over": sum(1 for t in due_in if parse_date(t["due_date"]) < today and (not t["done_at"] or is_late(t))),
            "open": sum(1 for t in due_in if not t["done_at"]),
            "done_items": sorted(done_in, key=lambda t: t["done_at"], reverse=True),
        }

    # グラフの範囲：今の期間までの n_bars 本。選んだ期間がそれより前なら、選んだ期間までにする
    last = sel if sel < shift(current, -(n_bars - 1)) or sel > current else current
    starts = [shift(last, k) for k in range(-(n_bars - 1), 1)]
    rows = [{"start": s, **counts(s)} for s in starts]
    # 目盛りは 1・2・5・10・20… のきりのいい刻みで、5本以内にする
    peak = max([1] + [r["ontime"] + r["over"] for r in rows])
    step = next(k * 10 ** e for e in range(6) for k in (1, 2, 5) if k * 10 ** e * 5 >= peak)
    top = -(-peak // step) * step
    # グラフの形（SVG の座標）
    W, H, left, bottom, upper = 420, 180, 22, 20, 14
    bw = (W - left) / n_bars

    def y(v: float) -> float:
        return round(H - bottom - v / top * (H - bottom - upper), 1)

    bars = []
    for i, r in enumerate(rows):
        x = round(left + i * bw + bw * 0.22, 1)
        w = round(bw * 0.56, 1)
        total = r["ontime"] + r["over"]
        bars.append({
            "label": short(r["start"]), "ontime": r["ontime"], "over": r["over"], "total": total,
            "selected": r["start"] == sel, "url": url_for("review", view=view, p=key(r["start"])),
            "hit_x": round(left + i * bw, 1), "hit_w": round(bw, 1), "x": x, "w": w, "cx": round(x + w / 2, 1),
            "y_ontime": y(r["ontime"]), "h_ontime": round(y(0) - y(r["ontime"]), 1),
            "y_over": y(total), "h_over": round(y(r["ontime"]) - y(total), 1),
            "aria": f"{short(r['start'])}：期日までに完了 {r['ontime']}件、期日を過ぎた {r['over']}件",
        })
    ticks = [{"v": v, "y": y(v)} for v in range(0, top + 1, step)]

    picked = counts(sel)
    by_day: list[dict] = []
    for t in picked["done_items"]:
        d = done_date(t)
        if not by_day or by_day[-1]["date"] != d:
            by_day.append({"date": d, "label": jp_date(d), "items": []})
        by_day[-1]["items"].append(t)
    return {
        "view": view, "title": title(sel), "is_current": sel == current,
        "summary": {"done": picked["done"], "over": picked["over"], "open": picked["open"]},
        "bars": bars, "ticks": ticks, "W": W, "H": H, "left": left, "base_y": y(0), "label_y": H - 5,
        "days": by_day,
        "prev_url": url_for("review", view=view, p=key(shift(sel, -1))),
        "next_url": url_for("review", view=view, p=key(shift(sel, 1))) if sel < current else None,
        "now_url": url_for("review", view=view),
        "week_url": url_for("review", view="week"), "month_url": url_for("review", view="month"),
    }


def line_message(todos: list[dict], today: date, app_url: str = APP_URL) -> str:
    """毎朝 LINE に送る文：期限切れ・今日・今週中・今月中。todos は完了していないもの（期日が近い順）"""
    head = f"おはようございます。\n{today.month}月{today.day}日（{WEEKDAYS[today.weekday()]}）"
    names = {"期限切れ": "期限切れ", "今日やること": "今日", "今週中": "今週中", "今月中": "今月中"}
    parts = []
    for g in build_groups(todos, today, today):
        if not g["items"]:
            continue
        lines = [f"■ {names.get(g['name'], g['name'])} {len(g['items'])}件"]
        for t in g["items"]:
            line = "・" + t["title"]
            if g.get("overdue"):
                due = parse_date(t["due_date"])
                line += f"（{due.month}/{due.day}まで）"
            if t.get("repeat"):
                line += " ↻"
            lines.append(line)
        parts.append("\n".join(lines))
    if not parts:
        text = f"{head}のToDoはありません。"
    else:
        text = f"{head}のToDoです。\n\n" + "\n\n".join(parts)
    tail = f"\n\n{app_url}"
    if len(text) + len(tail) > LINE_MAX:
        text = text[: LINE_MAX - len(tail) - 2] + "\n…"
    return text + tail


def send_line(text: str) -> None:
    """LINE の公式アカウントから自分にメッセージを送る（Messaging API のプッシュメッセージ）"""
    body = json.dumps({"to": os.environ["LINE_USER_ID"], "messages": [{"type": "text", "text": text}]})
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        res.read()


def create_app(store_factory=None, line_sender=None) -> Flask:
    """store_factory：保存先を作る関数。テストでは偽物のシートを渡す。
    line_sender：LINE に送る関数。テストでは送ったふりをする関数を渡す。"""
    app = Flask(__name__)
    make_store = store_factory or (lambda: TodoStore(open_worksheet()))
    send = line_sender or send_line
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

    @app.template_filter("is_late")
    def is_late_filter(t: dict) -> bool:
        return is_late(t)

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
        # 完了・削除した直後なら「元に戻す」を出す
        done_todo = next_todo = deleted_todo = None
        if request.args.get("done"):
            t = store().get(request.args["done"])
            if t and t["done_at"]:
                done_todo = t
                if request.args.get("next"):
                    n = store().get(request.args["next"])
                    if n and not n["done_at"] and not n["deleted_at"]:
                        next_todo = n
        if request.args.get("deleted"):
            t = store().get(request.args["deleted"])
            if t and t["deleted_at"]:
                deleted_todo = t
        return render_template(
            "index.html", cal=cal, groups=groups, nav=nav, done_todo=done_todo, next_todo=next_todo,
            deleted_todo=deleted_todo, all_url=url_for("index", **{**nav, "d": "all"}),
        )

    def live(todo_id: str) -> dict:
        """削除していない ToDo。なければ 404"""
        todo = store().get(todo_id)
        if todo is None or todo["deleted_at"]:
            abort(404)
        return todo

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
        empty = {"title": "", "content": "", "kind": kind, "due_date": "", "repeat": ""}
        return render_template("form.html", mode="new", todo=empty, errors=[], nav=nav,
                               choices=form_choices(empty, base_date(nav)))

    @app.route("/todos/<todo_id>/edit", methods=["GET", "POST"])
    def edit(todo_id):
        nav = nav_from(request.args)
        todo = live(todo_id)
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
        todo = live(todo_id)
        next_id = None
        if not todo["done_at"]:  # 2回押しても次の回を2つ作らない
            store().set_done(todo_id, True)
            spec = parse_repeat(todo["kind"], todo["repeat"])
            due = parse_date(todo["due_date"])
            if spec and due:
                next_id = store().add(
                    title=todo["title"], content=todo["content"], kind=todo["kind"],
                    due_date=next_due(todo["kind"], spec, due, today_jst()).isoformat(), repeat=todo["repeat"],
                )
        params = {**nav_from(request.form), "done": todo_id}
        if next_id:
            params["next"] = next_id
        return redirect(url_for("index", **params))

    @app.post("/todos/<todo_id>/undo")
    def undo(todo_id):
        todo = live(todo_id)
        store().set_done(todo_id, False)
        # 繰り返しで作った次の回も取り消す（まだ手をつけていないときだけ）
        n = store().get(request.form.get("next", "")) if request.form.get("next") else None
        if n and n["title"] == todo["title"] and n["repeat"] == todo["repeat"] and not n["done_at"]:
            store().set_deleted(n["id"], True)
        return redirect(url_for("index", **nav_from(request.form), saved="undone"))

    @app.post("/todos/<todo_id>/delete")
    def delete(todo_id):
        live(todo_id)
        store().set_deleted(todo_id, True)
        return redirect(url_for("index", **nav_from(request.form), deleted=todo_id))

    @app.post("/todos/<todo_id>/restore")
    def restore(todo_id):
        if not store().set_deleted(todo_id, False):
            abort(404)
        return redirect(url_for("index", **nav_from(request.form), saved="restored"))

    @app.get("/review")
    def review():
        view = "month" if request.args.get("view") == "month" else "week"
        data = build_review(store().list_all(), view, request.args.get("p", ""), today_jst())
        return render_template("review.html", r=data, nav={})

    @app.get("/cron/line")
    def cron_line():
        # Vercel Cron は環境変数 CRON_SECRET の値を「Authorization: Bearer …」で付けて呼ぶ。合わなければ断る
        secret = os.environ.get("CRON_SECRET")
        if not secret or request.headers.get("Authorization") != f"Bearer {secret}":
            abort(401)
        if not (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") and os.environ.get("LINE_USER_ID")):
            return "LINE の設定（LINE_CHANNEL_ACCESS_TOKEN・LINE_USER_ID）がありません。", 503
        send(line_message(store().list_active(), today_jst()))
        return "送りました。"

    # ---- ホーム画面に追加（PWA） ----
    @app.get("/sw.js")
    def service_worker():
        # サービスワーカーはサイトの一番上（/）から配ると、全部のページで働く
        res = make_response(send_from_directory(app.static_folder, "sw.js", mimetype="text/javascript"))
        res.headers["Cache-Control"] = "no-cache"
        return res

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/offline")
    def offline():
        return render_template("offline.html")

    @app.errorhandler(401)
    def unauthorized(_):
        return "認証が必要です。", 401

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
