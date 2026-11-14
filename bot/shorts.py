"""Шортсы с голами Данила: вертикальные ролики из записи трансляции матча.

Запускается задачей по расписанию в приложении Claude на Mac (см. content/SHORTS.md):
Claude смотрит на табло в кадрах трансляции, находит секунду гола и вырезает ролик.
Бот на GitHub ничего не режет — только публикует готовый файл из content/video/.

    python -m bot.shorts pending                      # голы, для которых ещё нет ролика
    python -m bot.shorts sheet 905245 --every 300     # обзор: кадры с табло по всей записи
    python -m bot.shorts sheet 905245 --from 900 --to 1300 --every 25 [--full]
    python -m bot.shorts window 905245 --around 1140  # скачать фрагмент и точный лист кадров
    python -m bot.shorts cut 905245 1 --goal-at 47    # ролик (секунда фрагмента) + проверка
    python -m bot.shorts add 905245 1                 # поставить ролик в очередь бота
    python -m bot.shorts prune                        # убрать ролики, которых нет в очереди

Нужны пакеты из requirements-extras.txt (yt-dlp, imageio-ffmpeg, pillow).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .extras import QUEUE
from .messages import MONTHS, SITUATION, period_name, short_team
from .rhl import RHL, Match

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
VIDEO_DIR = ROOT / "content" / "video"
WORK = Path(tempfile.gettempdir()) / "hockey-shorts"
LOOKBACK_DAYS = 4                 # за сколько дней назад ищем голы без ролика
W, H = 1080, 1920                 # вертикальный кадр


def cfg() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def site_for(c: dict) -> RHL:
    site = RHL(c["site"], c["team"]["id"], c["player"]["id"])
    site.calendar()               # узнаём сезон и турнир
    return site


def short_id(match_id: int, n: int) -> str:
    return f"short-{match_id}-{n}"


def mmss(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clocks(period: str, t: str) -> dict:
    """Время гола в протоколе — от начала матча («24:23»). На табло трансляции часы идут
    внутри периода: либо вперёд («04:23»), либо назад («15:37»)."""
    try:
        m, s = (int(x) for x in t.split(":"))
    except ValueError:
        return {}
    total = m * 60 + s
    per = int(period) if period.isdigit() else 4
    elapsed = total - (per - 1) * 20 * 60
    out = {"clock_counting_up": mmss(elapsed)}
    if per <= 3:
        out["clock_counting_down"] = mmss(20 * 60 - elapsed)
    return out


# ------------------------------------------------------------------ что нарезать

def goals_before(site: RHL, match_id: int) -> int:
    """Сколько голов в сезоне было у Данила до этого матча."""
    games = site.player_games()
    day = next((parse_date(g["date"]) for g in games if g["match_id"] == match_id), None)
    if not day:
        return 0
    return sum(int(g["stats"].get("goals") or 0) for g in games
               if (d := parse_date(g["date"])) and d < day)


def goals_of(site: RHL, c: dict, match: Match) -> list[dict]:
    """Голы Данила в матче: номер по порядку среди его голов, время, счёт, ассистенты."""
    p = site.protocol(match)
    pid, out = c["player"]["id"], []
    before = goals_before(site, match.id)
    for g in p.goals:
        if g.scorer_id != pid:
            continue
        out.append({
            "id": short_id(match.id, len(out) + 1), "match_id": match.id, "n": len(out) + 1,
            "season_goal": before + len(out) + 1,
            "home": p.home, "away": p.away, "period": g.period, "time": g.time,
            "score_after": g.score, "situation": g.situation,
            "assists": [name for _, name in g.assists], **clocks(g.period, g.time),
        })
    return out


def find_match(site: RHL, match_id: int) -> tuple[Match, str]:
    """Матч и его дата («07 ноября 2026») из списка игр Данила."""
    for g in site.player_games():
        if g["match_id"] == match_id:
            return Match(id=match_id, tournament=site.tournament, kickoff="",
                         home=g["home"], away=g["away"]), g["date"]
    sys.exit(f"Матча {match_id} нет в играх Данила")


def parse_date(s: str):
    parts = s.split()
    try:
        return datetime(int(parts[2]), MONTHS.index(parts[1]) + 1, int(parts[0])).date()
    except (IndexError, ValueError):
        return None


def queued_ids() -> set[str]:
    if not QUEUE.exists():
        return set()
    return {it.get("id") for it in json.loads(QUEUE.read_text(encoding="utf-8")).get("items", [])}


def pending(args) -> None:
    c = cfg()
    site = site_for(c)
    today = datetime.now(ZoneInfo(c.get("timezone", "Europe/Moscow"))).date()
    done, out = queued_ids(), []
    for g in site.player_games():
        d = parse_date(g["date"])
        if not d or (today - d).days > args.days or int(g["stats"].get("goals") or 0) == 0:
            continue
        match = Match(id=g["match_id"], tournament=site.tournament, kickoff="",
                      home=g["home"], away=g["away"])
        video = site.video_link(match)
        for goal in goals_of(site, c, match):
            if goal["id"] not in done:
                out.append({**goal, "date": g["date"], "video": video})
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ видео

def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def stream(site: RHL, match: Match) -> dict:
    """Адрес потока 720p и заголовки для ffmpeg. Адрес живёт недолго — кэш на 20 минут."""
    cache = WORK / str(match.id) / "stream.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 20 * 60:
        return json.loads(cache.read_text())
    video = site.video_link(match)
    if not video:
        sys.exit(f"У матча {match.id} нет записи трансляции")
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
        info = y.extract_info(video, download=False)
    fmts = [f for f in info["formats"] if f.get("protocol", "").startswith("m3u8")
            and f.get("acodec") != "none" and (f.get("height") or 0) <= 720]
    if not fmts:
        sys.exit("Не нашёл подходящий поток (m3u8 до 720p со звуком)")
    best = max(fmts, key=lambda f: f.get("height") or 0)
    data = {"url": best["url"], "duration": info.get("duration"), "height": best.get("height"),
            "headers": "".join(f"{k}: {v}\r\n" for k, v in (best.get("http_headers") or {}).items())}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data


def grab(st: dict, t: float, out: Path, vf: str) -> bool:
    out.unlink(missing_ok=True)
    for _ in range(3):                    # сервер с видео иногда отвечает ошибкой
        subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-headers", st["headers"],
                        "-ss", str(t), "-i", st["url"], "-frames:v", "1", "-vf", vf, str(out)],
                       capture_output=True, text=True)
        if out.exists():
            return True
        time.sleep(2)
    return False


def font(size: int, bold: bool = True):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def contact_sheet(tiles: list[tuple[str, Path]], out: Path, cols: int) -> None:
    from PIL import Image, ImageDraw
    imgs = [(label, Image.open(p)) for label, p in tiles if p.exists()]
    if not imgs:
        sys.exit("Не удалось получить ни одного кадра")
    tw, th = imgs[0][1].size
    rows = -(-len(imgs) // cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 30)), "black")
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(imgs):
        x, y = (i % cols) * tw, (i // cols) * (th + 30)
        d.text((x + 8, y + 3), label, font=font(20), fill="#FFD400")
        sheet.paste(im, (x, y + 30))
    sheet.save(out, quality=88)


def sheet(args) -> None:
    c = cfg()
    site = site_for(c)
    match, _ = find_match(site, args.match_id)
    st = stream(site, match)
    if args.at:
        times = [float(x) for x in args.at.split(",")]
    else:
        end = args.to if args.to is not None else (st["duration"] or 9000)
        times = [float(t) for t in range(int(args.frm), int(end) + 1, int(args.every))]
    if len(times) > 40:
        sys.exit(f"Слишком много кадров ({len(times)}), максимум 40 — увеличь --every")
    # обычно табло в левом верхнем углу; --full — весь кадр (если табло в другом месте)
    vf, cols = ("scale=640:-2", 3) if args.full else ("scale=1280:-2,crop=480:150:0:0", 4)
    work = WORK / str(match.id)
    work.mkdir(parents=True, exist_ok=True)
    tiles = [(f"t={t:g} c", work / f"f_{t:g}.jpg") for t in times]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda tp: grab(st, tp[0], tp[1][1], vf), zip(times, tiles)))
    out = work / f"sheet_{times[0]:g}_{times[-1]:g}{'_full' if args.full else ''}.jpg"
    contact_sheet(tiles, out, cols)
    print(f"Длина записи: {st['duration']} c. Лист кадров: {out}\n"
          "Перемотка записи неточна (до ±10 c): точное время ищи командой window.")


def window_paths(match_id: int) -> tuple[Path, Path]:
    work = WORK / str(match_id)
    return work / "window.mp4", work / "window.json"


def window(args) -> None:
    """Скачивает фрагмент записи вокруг примерного места гола и делает лист кадров.

    Запись в VK перематывается неточно (до ±10 c), поэтому точную секунду гола ищем
    в скачанном фрагменте: подписи на листе (w=...) и --goal-at у cut — секунды фрагмента."""
    c = cfg()
    site = site_for(c)
    match, _ = find_match(site, args.match_id)
    st = stream(site, match)
    start = max(int(args.around - args.span / 2), 0)
    video, meta = window_paths(match.id)
    video.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([
        ffmpeg(), "-loglevel", "error", "-y", "-headers", st["headers"],
        "-ss", str(start), "-t", str(args.span), "-i", st["url"],
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k",
        str(video)], capture_output=True, text=True)
    if r.returncode != 0 or not video.exists():
        sys.exit("Не удалось скачать фрагмент:\n" + r.stderr[-1500:])
    meta.write_text(json.dumps({"start": start, "span": args.span}))

    times = [float(t) for t in range(0, int(args.span), int(args.every))]
    vf, cols = ("scale=640:-2", 3) if args.full else ("scale=1280:-2,crop=480:150:0:0", 4)
    tiles = [(f"w={t:g} c", video.parent / f"w_{t:g}.jpg") for t in times]
    for t, (_, path) in zip(times, tiles):
        path.unlink(missing_ok=True)
        subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                        "-frames:v", "1", "-vf", vf, str(path)], capture_output=True)
    out = video.parent / f"window_sheet{'_full' if args.full else ''}.jpg"
    contact_sheet(tiles, out, cols)
    print(f"Фрагмент: {args.span:g} c, начало около {start} c записи. Лист кадров: {out}\n"
          "Подписи w=... — секунды фрагмента; их же передавай в cut --goal-at.")


def title_overlay(c: dict, goal: dict, date: str, out: Path, video_top: int, video_h: int) -> None:
    """Прозрачная картинка с подписями поверх ролика."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (8, 20, 46, 150))          # затемняем размытый фон
    d = ImageDraw.Draw(img)
    d.rectangle((0, video_top, W, video_top + video_h), fill=(0, 0, 0, 0))
    d.rectangle((0, video_top - 8, W, video_top), fill="#E03A3E")
    d.rectangle((0, video_top + video_h, W, video_top + video_h + 8), fill="#E03A3E")
    pl = c["player"]
    white, red, grey = "#FFFFFF", "#FF4B4B", "#D5DBE8"

    def center(y: int, text: str, f, fill=white) -> int:
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, font=f, fill=fill, stroke_width=3, stroke_fill="#0B1E3F")
        return y + f.size + 18

    y = center(video_top - 470, "ГОЛ!", font(200), red)
    y = center(y + 14, f"{pl['first_name']} {pl['last_name']}".upper(), font(66))
    center(y, f"#{pl['number']} · «{c['team']['name']}» · {goal['season_goal']}-й гол в сезоне",
           font(42, False), grey)

    y = video_top + video_h + 70
    hs, as_ = (goal["score_after"].split(":") + ["", ""])[:2]
    y = center(y, f"{short_team(goal['home'])}  {hs} : {as_}  {short_team(goal['away'])}", font(64))
    when = f"{date.lstrip('0')} · {goal['time']}, {period_name(goal['period'])}"
    sit = SITUATION.get(goal["situation"], "")
    if sit and sit != "в равных составах":
        when += f" · {sit}"
    y = center(y + 6, when, font(40, False), grey)
    if goal["assists"]:
        y = center(y + 24, "Передачи", font(34, False), grey)
        for name in goal["assists"]:
            y = center(y - 6, name, font(46))
    img.save(out)


def clip_path(match_id: int, n: int) -> Path:
    return VIDEO_DIR / f"{short_id(match_id, n)}.mp4"


def cut(args) -> None:
    c = cfg()
    site = site_for(c)
    match, date = find_match(site, args.match_id)
    goals = goals_of(site, c, match)
    if not 1 <= args.n <= len(goals):
        sys.exit(f"В матче {args.match_id} у Данила {len(goals)} гол(ов), а запрошен №{args.n}")
    goal = goals[args.n - 1]
    video, meta = window_paths(match.id)
    if not video.exists() or not meta.exists():
        sys.exit("Сначала скачай фрагмент: python -m bot.shorts window <match_id> --around <секунда записи>")
    win = json.loads(meta.read_text())
    start, dur = args.goal_at - args.before, args.before + args.after
    if start < 0 or start + dur > win["span"]:
        around = int(win["start"] + args.goal_at)
        sys.exit(f"Гол слишком близко к краю фрагмента ({win['span']:g} c). Скачай заново: "
                 f"window {match.id} --around {around} — и найди гол на новом листе.")
    work = video.parent

    zoom = max(1.0, min(args.zoom, 1.6))
    video_h = int(W * 9 / 16) // 2 * 2
    video_top = (H - video_h) // 2 - 40
    overlay = work / f"title_{args.n}.png"
    title_overlay(c, goal, date, overlay, video_top, video_h)
    out = clip_path(match.id, args.n)
    out.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        "[0:v]fps=25,split=2[a][b];"
        f"[a]scale=-2:{H},crop={W}:{H},boxblur=30:2,eq=brightness=-0.22[bg];"
        f"[b]crop=iw/{zoom}:ih/{zoom},scale={W}:{video_h}[fg];"
        f"[bg][fg]overlay=0:{video_top}[v1];[v1][1:v]overlay=0:0[v]"
    )
    r = subprocess.run([
        ffmpeg(), "-loglevel", "error", "-y",
        "-ss", str(start), "-t", str(dur), "-i", str(video), "-i", str(overlay),
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "27", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)],
        capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        sys.exit("ffmpeg не смог собрать ролик:\n" + r.stderr[-1500:])

    # лист кадров готового ролика — Claude проверяет, что гол действительно в кадре
    times = [x * 2.5 for x in range(int(dur / 2.5))]
    tiles = [(f"{t:g} c ролика", work / f"check_{args.n}_{t:g}.jpg") for t in times]
    for t, (_, p) in zip(times, tiles):
        subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-ss", str(t), "-i", str(out),
                        "-frames:v", "1", "-vf", "scale=360:-2", str(p)], capture_output=True)
    check = work / f"check_{args.n}.jpg"
    contact_sheet(tiles, check, 5)
    size = out.stat().st_size / 1e6
    print(f"Ролик: {out} ({size:.1f} МБ, {dur:g} c). Лист для проверки: {check}")
    if size > 45:
        print("ВНИМАНИЕ: файл больше 45 МБ — Telegram не примет, сократи ролик")


def add(args) -> None:
    c = cfg()
    out = clip_path(args.match_id, args.n)
    if not out.exists():
        sys.exit(f"Нет файла {out} — сначала cut")
    site = site_for(c)
    match, date = find_match(site, args.match_id)
    goal = goals_of(site, c, match)[args.n - 1]
    pl = c["player"]
    caption = (f"🎬 Гол {pl.get('first_genitive', pl['first_name'])}! "
               f"{goal['season_goal']}-й в сезоне\n"
               f"{goal['home']} — {goal['away']}, {date.lstrip('0')}\n"
               f"⏱ {goal['time']}, {period_name(goal['period'])} · счёт {goal['score_after']}")
    if goal["assists"]:
        caption += "\n🤝 Передачи: " + ", ".join(goal["assists"])
    tz = ZoneInfo(c.get("timezone", "Europe/Moscow"))
    at = datetime.now(tz).replace(microsecond=0) + timedelta(minutes=args.delay)
    item = {"id": goal["id"], "type": "video", "publish_at": at.isoformat(),
            "video": str(out.relative_to(ROOT)), "caption": caption}
    data = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {"items": []}
    data["items"] = [it for it in data.get("items", []) if it.get("id") != item["id"]] + [item]
    QUEUE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"В очереди: {item['id']}, публикация в {at:%H:%M}")


def prune(_args) -> None:
    """Удаляет ролики, которых уже нет в очереди (очередь чистит вечерняя задача)."""
    data = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {"items": []}
    keep = {it.get("video") for it in data.get("items", [])}
    for f in sorted(VIDEO_DIR.glob("*.mp4")):
        if str(f.relative_to(ROOT)) not in keep:
            f.unlink()
            print(f"Удалён {f.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Шортсы с голами")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("pending")
    pe.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="за сколько дней искать голы")
    pe.set_defaults(fn=pending)
    s = sub.add_parser("sheet")
    s.add_argument("match_id", type=int)
    s.add_argument("--at", help="секунды записи через запятую")
    s.add_argument("--from", dest="frm", type=float, default=0)
    s.add_argument("--to", type=float)
    s.add_argument("--every", type=float, default=300)
    s.add_argument("--full", action="store_true", help="весь кадр, а не угол с табло")
    s.set_defaults(fn=sheet)
    w = sub.add_parser("window")
    w.add_argument("match_id", type=int)
    w.add_argument("--around", type=float, required=True, help="примерная секунда записи с голом")
    w.add_argument("--span", type=float, default=90, help="длина фрагмента, c")
    w.add_argument("--every", type=float, default=3)
    w.add_argument("--full", action="store_true", help="весь кадр, а не угол с табло")
    w.set_defaults(fn=window)
    k = sub.add_parser("cut")
    k.add_argument("match_id", type=int)
    k.add_argument("n", type=int, help="номер гола Данила в матче, с 1")
    k.add_argument("--goal-at", type=float, required=True,
                   help="секунда скачанного фрагмента (w=...), когда забит гол")
    k.add_argument("--before", type=float, default=9)
    k.add_argument("--after", type=float, default=16)
    k.add_argument("--zoom", type=float, default=1.0)
    k.set_defaults(fn=cut)
    a = sub.add_parser("add")
    a.add_argument("match_id", type=int)
    a.add_argument("n", type=int)
    a.add_argument("--delay", type=int, default=3, help="через сколько минут опубликовать")
    a.set_defaults(fn=add)
    sub.add_parser("prune").set_defaults(fn=prune)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
