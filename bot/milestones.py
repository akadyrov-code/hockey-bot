"""Вехи и рекорды Данила: поводы поздравить после матча.

Считаем по играм сезона до этого матча (список игр на сайте лиги) и по строке Данила
в протоколе — так вехи не зависят от того, обновила ли лига сезонную статистику.
"""
from __future__ import annotations

from .messages import plural

MAX_LINES = 4


def _ga(game: dict) -> tuple[int, int]:
    st = game.get("stats", {})
    return int(st.get("goals") or 0), int(st.get("a") or 0)


def _leading(flags: list[bool]) -> int:
    n = 0
    for f in flags:
        if not f:
            break
        n += 1
    return n


def _longest(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def season_totals(prior: list[dict], goals: int, assists: int) -> dict:
    """Итоги сезона вместе с этим матчем."""
    g = sum(_ga(x)[0] for x in prior) + goals
    a = sum(_ga(x)[1] for x in prior) + assists
    return {"matches": len(prior) + 1, "goals": g, "a": a, "points": g + a}


def find(prior: list[dict], goals: int, assists: int,
         team: str = "", team_wins: int = 0) -> list[str]:
    """prior — игры Данила в сезоне до этого матча, от новых к старым.
    team_wins — сколько матчей подряд выиграла команда, включая этот."""
    pts = goals + assists
    past = [_ga(x) for x in prior]
    g0, a0 = sum(x[0] for x in past), sum(x[1] for x in past)
    out: list[str] = []

    # подвиги в одном матче
    if goals >= 4:
        out.append(f"Покер — {goals} {plural(goals, 'гол', 'гола', 'голов')} за матч!")
    elif goals == 3:
        out.append("Хет-трик!")
    elif goals == 2:
        out.append("Дубль!")
    best = max((g + a for g, a in past), default=0)
    if pts >= 2 and past and pts > best:
        out.append(f"Лучший матч сезона — {pts} {plural(pts, 'очко', 'очка', 'очков')}")
    elif pts >= 3 and pts == best:
        out.append(f"Повторил свой рекорд сезона — {pts} {plural(pts, 'очко', 'очка', 'очков')} за матч")

    # круглые цифры сезона
    if goals and g0 == 0:
        out.append("Первый гол в сезоне!")
    for step, before, now, what in ((5, g0, g0 + goals, "-й гол"),
                                    (10, g0 + a0, g0 + a0 + pts, "-е очко"),
                                    (10, a0, a0 + assists, "-я передача")):
        mark = now // step * step
        if mark and before < mark <= now:
            out.append(f"{mark}{what} в сезоне")

    # серии
    if pts:
        streak = 1 + _leading([g + a > 0 for g, a in past])
        if streak >= 3:
            line = f"Очковая серия — {streak} {plural(streak, 'матч', 'матча', 'матчей')} подряд"
            if streak > _longest([g + a > 0 for g, a in past]):
                line += ", лучшая в сезоне"
            out.append(line)
    if goals:
        streak = 1 + _leading([g > 0 for g, _ in past])
        if streak >= 3:
            out.append(f"Забивает в {streak} матчах подряд")
    if team and team_wins >= 3:
        out.append(f"«{team}» выиграл {team_wins} {plural(team_wins, 'матч', 'матча', 'матчей')} подряд")
    return out[:MAX_LINES]
