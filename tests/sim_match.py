"""Имитация игрового дня на сохранённом протоколе (Краснодар — Кристалл 1:3, 29.10.2026).

    python tests/sim_match.py [через сколько часов после сирены лига заполнит протокол]

Сайт лиги подменён: во время игры он показывает только счёт и стадию, протокол появляется
позже. Проверяем, что бот шлёт всё по порядку и ничего не дублирует. В Telegram не ходит.
"""
from __future__ import annotations

import copy
import gzip
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.main import Bot                      # noqa: E402
from bot.rhl import RHL, Match                # noqa: E402
from bot.state import State                   # noqa: E402
from bot.telegram import Telegram             # noqa: E402

FILL_HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 5
TZ = ZoneInfo("Europe/Moscow")
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
html = gzip.open(ROOT / "demo" / "protocol_905213.html.gz", "rt", encoding="utf-8").read()
FULL = RHL(cfg["site"], 57, 45534).parse_protocol(BeautifulSoup(html, "html.parser"), 905213)

K = datetime(2026, 10, 29, 19, 0, tzinfo=TZ)                     # четверг
MATCH = Match(id=905213, tournament=1432, kickoff=K.isoformat(),
              home="ХК Краснодар", away="МХК Кристалл", place="№ 103 Краснодар")
END = K + timedelta(minutes=121)                # сирена (последний гол — в ту же минуту)
FILLED = END + timedelta(hours=FILL_HOURS)      # лига заполнила протокол
clock = [datetime(2026, 10, 29, 16, 50, tzinfo=TZ)]


def game_minutes(t: str) -> float:
    return int(t.split(":")[0]) + int(t.split(":")[1]) / 60


def row(mid, day, home, away, score, winner, g, a):
    return {"match_id": mid, "date": day, "home": home, "away": away, "score": score,
            "winner": winner, "stats": {"goals": str(g), "a": str(a)}}


PRIOR = [   # игры Данила до этого матча, как на сайте: от новых к старым
    row(9, "28 октября 2026", "ХК Краснодар", "МХК Кристалл", "3:4", "away", 1, 0),
    row(8, "24 октября 2026", "МХК Кристалл", "МХК Ермак", "3:5", "away", 0, 1),
    row(7, "23 октября 2026", "МХК Кристалл", "МХК Ермак", "4:3", "home", 0, 1),
    row(6, "20 октября 2026", "МХК Кристалл", "МХК Рязань-ВДВ", "1:3", "away", 1, 0),
    row(5, "19 октября 2026", "МХК Кристалл", "МХК Рязань-ВДВ", "3:4", "away", 0, 0),
    row(4, "08 октября 2026", "ЭкоНива-Бобров", "МХК Кристалл", "2:3", "away", 1, 1),
    row(3, "07 октября 2026", "ЭкоНива-Бобров", "МХК Кристалл", "5:2", "home", 0, 0),
    row(2, "04 октября 2026", "Протон", "МХК Кристалл", "6:3", "home", 0, 0),
    row(1, "03 октября 2026", "Протон", "МХК Кристалл", "2:1", "home", 0, 0),
]


class FakeSite:
    season, tournament = "2026-2027", 1432

    def calendar(self):
        return [MATCH] if clock[0] < K else []

    def stats_ready(self) -> bool:
        return clock[0] > FILLED + timedelta(minutes=30)

    def player_season(self):
        g, a, n = (5, 4, 10) if self.stats_ready() else (3, 3, 9)
        return [("26/27 Рег. чемпионат", {"matches": str(n), "goals": str(g), "a": str(a), "points": str(g + a)})]

    def player_games(self):
        this = [row(905213, "29 октября 2026", MATCH.home, MATCH.away, "1:3", "away", 2, 1)]
        return (this if self.stats_ready() else []) + PRIOR

    def conferences(self):
        return []

    def team_forwards_season(self):
        return FULL.forwards["away"]

    def news_list(self):
        return []

    def video_link(self, _m):
        return "https://vkvideo.ru/video-1_2"

    def protocol(self, _m):
        p, t = copy.deepcopy(FULL), clock[0]
        mins = (t - K).total_seconds() / 60 * 0.5          # игровая минута
        seen = [g for g in FULL.goals if game_minutes(g.time) <= mins]
        if t >= FILLED:
            p.finished, p.status = True, ""
            return p
        p.goals, p.penalties, p.player_line, p.forwards = [], [], None, {}
        p.score = seen[-1].score if seen else "0:0"
        if t >= END:
            p.finished, p.status, p.score = True, "", FULL.score
        else:
            p.finished = False
            per = min(int(mins // 20) + 1, 3)
            p.status = "" if mins < 0 else f"{per}-й перерыв" if mins % 20 > 19 and per < 3 else f"{per}-й период"
        return p


class Tape(Telegram):
    """Вместо Telegram — лента: (время, вид, первая строка, без звука)."""
    def __init__(self):
        super().__init__("x", dry_run=True)
        self.log = []

    def send(self, text, preview=False, silent=False):
        self.log.append((clock[0], "text", text, silent))

    def send_photos(self, photos, caption, silent=False):
        self.log.append((clock[0], "photo", caption, silent))
        (Path(tempfile.gettempdir()) / "sim_card.jpg").write_bytes(photos[0])


st = State(Path(tempfile.gettempdir()) / "hockey-sim-state.json")
st.clear()
st.update(initialized=True, chat_id=1, last_weekly="2026-W44", last_leaders="2026-10-29", league_news_last=1)
tape = Tape()
for _ in range(2 + int((FILL_HOURS + 10) * 60 // 330)):       # смены по 5,5 часа, до середины следующего дня
    Bot(cfg, st, tape, FakeSite(), clock=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)), loop_minutes=330).run()

print("\n===== лента канала =====")
for at, kind, text, silent in tape.log:
    head = text.splitlines()[0][:70]
    extra = " | " + " | ".join(text.splitlines()[1:4]) if kind == "photo" else ""
    print(f"{at:%d.%m %H:%M} {'🔕' if silent else '🔔'} [{kind}] {head}{extra}")


def count(needle: str, kind: str = "text") -> int:
    return sum(1 for _, k, text, _ in tape.log if k == kind and needle in text.splitlines()[0])


def when(needle: str):
    return next(at for at, _, text, _ in tape.log if needle in text)


checks = {
    "напоминание за 2 часа — одно": count("Игра начнётся") == 1,
    "ссылка на трансляцию — одна": count("ПРЯМАЯ ТРАНСЛЯЦИЯ") == 1,
    "матч начался — один раз": count("Матч начался") == 1,
    "голы по счёту — все 4": count("ГОЛ!") + count("Гол в наши ворота") == len(FULL.goals),
    "финальная сирена — одна, после последнего гола": count("Финальная сирена") == 1
        and max(at for at, _, t, _ in tape.log if "ГОЛ!" in t or "Гол в наши" in t) <= when("Финальная сирена"),
    "картинка-итог — одна, с вехами": count("🏁", "photo") == 1
        and all(x in next(t for _, k, t, _ in tape.log if k == "photo")
                for x in ("Дубль!", "Лучший матч сезона — 3 очка", "5-й гол в сезоне", "Очковая серия — 5 матчей подряд")),
    "подробный итог — один, после заполнения протокола": count("Матч завершён") == 1 and when("Матч завершён") >= FILLED,
    "сводка после матча — одна, после итога": count("Сводка после матча") == 1
        and when("Сводка после матча") > when("Матч завершён"),
    "сводка в 12:00 — только в день матча": count("Сводка на") == 1,
    "ночью — без звука, днём — со звуком": all(silent == (at.hour >= 23 or at.hour < 8) for at, _, _, silent in tape.log),
}
print("\n===== проверки =====")
for name, ok in checks.items():
    print("✅" if ok else "❌", name)
sys.exit(0 if all(checks.values()) else 1)
