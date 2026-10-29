"""Точка входа. Запускается по расписанию (GitHub Actions) каждые 15 минут.

Один запуск:
  1. обновляет календарь «Кристалла»;
  2. если матч скоро или идёт — остаётся «на связи» до конца игры и каждую
     минуту проверяет трансляцию и протокол (голы, удаления, итог);
  3. утром шлёт сводку, по понедельникам — таблицу и календарь;
  4. если задан VK_TOKEN — пересылает новые посты из групп ВК.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from .messages import MONTHS, Texts
from .photos import PhotoFinder
from .rhl import RHL, Match
from .state import State
from .telegram import Telegram
from .vk import VK

ROOT = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def parse_ru_date(s: str) -> date | None:
    """'29 октября 2026' -> date(2026, 10, 29)"""
    parts = s.split()
    try:
        return date(int(parts[2]), MONTHS.index(parts[1]) + 1, int(parts[0]))
    except (IndexError, ValueError):
        return None


class Bot:
    def __init__(self, cfg: dict, state: State, tg: Telegram, site: RHL, vk: VK | None = None,
                 photos: PhotoFinder | None = None, clock=None, sleep=time.sleep):
        self.cfg = cfg
        self.st = state
        self.tg = tg
        self.site = site
        self.vk = vk
        self.photos = photos
        self.tz = ZoneInfo(cfg.get("timezone", "Europe/Moscow"))
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.sleep = sleep
        self.t = Texts(cfg)
        self.started = self.clock()

    def now(self) -> datetime:
        return self.clock().astimezone(self.tz)

    # ------------------------------------------------------------ основной цикл

    def run(self) -> None:
        self.ensure_chat()
        self.refresh_schedule()
        if not self.st.get("initialized"):
            self.bootstrap()

        m = self.active_match()
        if m:
            self.watch(m)
        self.catch_up_results()
        self.safe(self.maybe_daily)
        self.safe(self.maybe_weekly)
        self.safe(self.vk_news)
        self.safe(self.vk_photos)
        self.st.save()

    def safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception:
            log(f"Ошибка в {fn.__name__}:\n{traceback.format_exc()}")
            return None

    def send(self, text: str, preview: bool = False) -> None:
        self.tg.send(text, preview=preview)
        log("Отправлено: " + text.splitlines()[0])

    # ------------------------------------------------------------ канал

    def ensure_chat(self) -> None:
        chat = os.environ.get("TG_CHAT_ID") or self.st.get("chat_id")
        if not chat and not self.tg.dry_run:
            found = self.tg.discover_channel()
            if not found:
                sys.exit("Не нашёл канал. Напиши любое сообщение в канал (бот должен быть "
                         "админом) и запусти workflow ещё раз в течение суток.")
            chat = found[0]
            self.st["chat_id"] = chat
            log(f"Нашёл канал «{found[1]}» ({chat})")
        self.tg.chat_id = chat

    # ------------------------------------------------------------ календарь

    def refresh_schedule(self) -> None:
        sched = self.st.setdefault("schedule", {})
        fresh = self.site.calendar()
        now = self.now()
        fresh_ids = {str(m.id) for m in fresh}
        for m in fresh:
            sched[str(m.id)] = m.to_dict()
        for mid in list(sched):
            k = datetime.fromisoformat(sched[mid]["kickoff"])
            if k < now - timedelta(days=3):
                del sched[mid]                       # старые матчи больше не нужны
                self.st.get("matches", {}).pop(mid, None)
            elif k > now + timedelta(hours=2) and mid not in fresh_ids:
                del sched[mid]                       # матч перенесли или отменили
        log(f"Календарь: {len(fresh)} будущих матчей, сезон {self.site.season}, турнир {self.site.tournament}")

    def matches(self) -> list[Match]:
        ms = [Match(**d) for d in self.st.get("schedule", {}).values()]
        return sorted(ms, key=lambda m: m.kickoff_dt)

    def active_match(self) -> Match | None:
        now = self.now()
        before = timedelta(minutes=self.cfg["watch_before_minutes"])
        after = timedelta(minutes=self.cfg["watch_after_kickoff_minutes"])
        for m in self.matches():
            if self.st.match(m.id)["result_sent"]:
                continue
            if m.kickoff_dt - before <= now <= m.kickoff_dt + after:
                return m
        return None

    def next_match(self) -> Match | None:
        now = self.now()
        for m in self.matches():
            if not self.st.match(m.id)["result_sent"] and m.kickoff_dt > now - timedelta(hours=3):
                return m
        return None

    # ------------------------------------------------------------ матч в реальном времени

    def watch(self, m: Match) -> None:
        ms = self.st.match(m.id)
        kick = m.kickoff_dt
        deadline = min(kick + timedelta(minutes=self.cfg["watch_after_kickoff_minutes"]),
                       self.started + timedelta(hours=5, minutes=30))
        poll = self.cfg.get("poll_seconds", 60)
        log(f"Слежу за матчем {m.home} — {m.away} ({kick:%d.%m %H:%M}) до {deadline:%H:%M}")

        while self.now() < deadline:
            try:
                if not ms["live_sent"]:
                    link = self.site.video_link(m) or self.vk_live()
                    if link:
                        self.send(self.t.live(m, link), preview=True)
                        ms["live_sent"] = link
                        self.st.checkpoint()
                if self.now() >= kick - timedelta(minutes=15):
                    p = self.site.protocol(m)
                    if self.process_protocol(m, p, ms):
                        self.st.checkpoint()
                    if p.finished:
                        self.send_result(m, p, ms)
                        return
            except Exception:
                log(f"Ошибка во время матча:\n{traceback.format_exc()}")
            self.sleep(poll)
        log("Время наблюдения вышло")

    def process_protocol(self, m: Match, p, ms: dict) -> bool:
        """Шлёт новые голы (все) и удаления Данила. Возвращает True, если что-то отправлено."""
        sent = False
        pid = self.cfg["player"]["id"]
        for g in p.goals:
            if g.key in ms["goals"]:
                continue
            ms["goals"].append(g.key)
            self.send(self.t.goal(m, p, g))
            sent = True
        for pen in p.penalties:
            if pen.player_id != pid or pen.key in ms["penalties"]:
                continue
            ms["penalties"].append(pen.key)
            self.send(self.t.penalty(m, p, pen))
            sent = True
        return sent

    def send_result(self, m: Match, p, ms: dict) -> None:
        video = ms.get("live_sent") or self.safe(self.site.video_link, m)
        self.send(self.t.result(m, p, video))
        ms["result_sent"] = True
        self.st.checkpoint()

    def catch_up_results(self) -> None:
        """Итоги матчей, за которыми не удалось проследить (например, GitHub не запустил бота)."""
        now = self.now()
        for m in self.matches():
            ms = self.st.match(m.id)
            if ms["result_sent"] or not (now - timedelta(days=2) < m.kickoff_dt < now - timedelta(hours=2)):
                continue
            p = self.safe(self.site.protocol, m)
            if p and p.finished:
                ms["goals"] = [g.key for g in p.goals]   # голы задним числом не шлём
                self.send_result(m, p, ms)

    # ------------------------------------------------------------ сводки

    def bootstrap(self) -> None:
        """Первый запуск: приветствие и сразу сводки, без старых новостей."""
        log("Первый запуск")
        self.send(self.t.welcome())
        self.st["initialized"] = True
        self.st["force_daily"] = self.st["force_weekly"] = True
        self.safe(self.vk_news, True)
        self.safe(self.vk_photos, True)
        self.st.checkpoint()

    def maybe_daily(self) -> None:
        now = self.now()
        if not self.st.pop("force_daily", False) and (
                now.hour < self.cfg["daily_hour"] or self.st.get("last_daily") == now.date().isoformat()):
            return
        games = self.site.player_games()
        conf = self.our_conference()
        text = self.t.daily(now.date(), self.site.player_season(), games[0] if games else None,
                            conf, self.next_match(), now)
        self.send(text)
        self.st["last_daily"] = now.date().isoformat()
        self.st.checkpoint()

    def maybe_weekly(self) -> None:
        now = self.now()
        week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        if not self.st.pop("force_weekly", False) and (
                now.weekday() != self.cfg["weekly_weekday"] or now.hour < self.cfg["weekly_hour"]
                or self.st.get("last_weekly") == week):
            return
        conf = self.our_conference()
        games = self.site.player_games()
        week_games = [g for g in games
                      if (d := parse_ru_date(g["date"])) and now.date() - timedelta(days=7) <= d < now.date()]
        upcoming = [m for m in self.matches() if now <= m.kickoff_dt < now + timedelta(days=7)]
        self.send(self.t.weekly(conf, self.st.get("standings_snap"), week_games, upcoming, now.date()))
        row = next((r for r in conf.rows if self.t.ours(r.team)), None) if conf else None
        if row:
            self.st["standings_snap"] = {"date": now.date().isoformat(), "pos": row.pos, "points": row.points}
        self.st["last_weekly"] = week
        self.st.checkpoint()

    def our_conference(self):
        for c in self.safe(self.site.conferences) or []:
            if any(r.team_id == self.cfg["team"]["id"] or self.t.ours(r.team) for r in c.rows):
                return c
        return None

    # ------------------------------------------------------------ ВКонтакте

    def vk_news(self, silent: bool = False) -> None:
        if not self.vk:
            return
        vcfg = self.cfg.get("vk", {})
        last = self.st.setdefault("vk_last", {})
        kws = [k.lower() for k in vcfg.get("keywords", [])]
        for domain in vcfg.get("groups", []):
            name, posts = self.vk.wall(domain)
            seen = last.get(domain)
            last[domain] = max([p["id"] for p in posts] + [seen or 0])
            if seen is None or silent:
                continue                    # новую группу целиком не вываливаем
            for post in posts:
                if post["id"] <= seen:
                    continue
                text = post.get("text", "")
                live = VK.live_video(post)
                if not (vcfg.get("all_posts") or live or any(k in text.lower() for k in kws)):
                    continue
                body = text if len(text) < 900 else text[:900].rsplit(" ", 1)[0] + "…"
                head = "🔴 <b>Прямой эфир в ВК</b>" if live else f"📰 <b>{escape(name)}</b>"
                self.send(f"{head}\n\n{escape(body)}\n\n🔗 {live or VK.post_url(post)}", preview=bool(live))

    def vk_photos(self, silent: bool = False) -> None:
        if not self.photos:
            return
        found = self.photos.new_photos(self.st, silent=silent)
        self.st.checkpoint()
        by_album: dict[str, list[dict]] = {}
        for f in found:
            by_album.setdefault(f["album_url"], []).append(f)
        for url, items in by_album.items():
            title = items[0]["album"]
            if items[0]["bytes"] is None:          # без ИИ: просто ссылка на альбом
                n = items[0].get("count") or 0
                self.send(f"📸 <b>Новые фото с матча</b>"
                          + (f"\n«{escape(title)}»" if title else "")
                          + (f" · {n} фото" if n else "")
                          + f"\n{url}", preview=True)
                continue
            caption = (f"📸 <b>{escape(self.t.first)} на фото</b>"
                       + (f" · {escape(title)}" if title else "")
                       + f"\nВесь альбом: {url}")
            self.tg.send_photos([f["bytes"] for f in items], caption)
            log(f"Фото: {len(items)} шт. из {url}")

    def vk_live(self) -> str | None:
        """Во время матча: ищет прямой эфир в группах ВК (если подключён VK)."""
        if not self.vk:
            return None
        for domain in self.cfg.get("vk", {}).get("groups", []):
            try:
                _, posts = self.vk.wall(domain, count=5)
            except Exception as e:
                log(f"VK: {e}")
                continue
            for post in reversed(posts):
                if post.get("date", 0) > self.now().timestamp() - 3 * 3600 and (link := VK.live_video(post)):
                    return link
        return None


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    dry = os.environ.get("DRY_RUN") == "1"
    token = os.environ.get("TG_TOKEN", "")
    if not token and not dry:
        sys.exit("Нет TG_TOKEN. Добавь токен бота в Settings → Secrets → Actions.")
    state = State(ROOT / ("state.dry.json" if dry else "state.json"))
    site = RHL(cfg["site"], cfg["team"]["id"], cfg["player"]["id"])
    vk_token = os.environ.get("VK_TOKEN")
    photos = None
    if vk_token and cfg.get("photos", {}).get("enabled", True):
        photos = PhotoFinder(vk_token, cfg, use_ai=bool(os.environ.get("ANTHROPIC_API_KEY")))
    bot = Bot(cfg, state, Telegram(token, dry_run=dry), site,
              VK(vk_token) if vk_token else None, photos)
    if os.environ.get("FAKE_NOW"):
        fake = datetime.fromisoformat(os.environ["FAKE_NOW"])
        bot.clock = lambda: fake
    bot.run()


if __name__ == "__main__":
    main()
