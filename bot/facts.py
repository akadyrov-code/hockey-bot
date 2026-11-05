"""Свежие факты для задачи Claude на Mac: всё, на чём можно строить викторины и посты.

    python -m bot.facts > facts.json

Только данные с сайта лиги — чтобы в викторинах не было выдумок.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .rhl import RHL

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "Europe/Moscow"))
    site = RHL(cfg["site"], cfg["team"]["id"], cfg["player"]["id"])
    upcoming = site.calendar()                     # заодно узнаём сезон и турнир

    conf = None
    for c in site.conferences():
        if any(r.team_id == cfg["team"]["id"] for r in c.rows):
            conf = c
    conf_ids = {r.team_id for r in conf.rows} if conf else set()
    league = site.skaters()

    def top(rows, key, n=10):
        return sorted(rows, key=lambda r: (r[key], r["points"]), reverse=True)[:n]

    conf_rows = [r for r in league if r["team_id"] in conf_ids]
    facts = {
        "generated_at": datetime.now(tz).isoformat(timespec="minutes"),
        "player": cfg["player"],
        "team": cfg["team"],
        "player_season": [{"tournament": n, **st} for n, st in site.player_season()],
        "player_games_this_season": site.player_games(),
        "team_skaters_season": sorted(site.skaters(cfg["team"]["id"]),
                                      key=lambda r: r["points"], reverse=True),
        "conference": {
            "name": conf.name if conf else "",
            "standings": [{"pos": r.pos, "team": r.team, "games": r.games, "points": r.points,
                           "wins": r.wins, "goals": r.goals, "form_last5": "".join(r.form[-5:]),
                           "playoff_line_after": r.playoff_line} for r in (conf.rows if conf else [])],
            "leaders_points": top(conf_rows, "points"),
            "leaders_goals": top(conf_rows, "goals"),
            "leaders_assists": top(conf_rows, "a"),
        },
        "league_leaders_points": top(league, "points"),
        "upcoming_matches": [{"kickoff_msk": m.kickoff, "home": m.home, "away": m.away,
                              "place": m.place} for m in upcoming[:8]],
        "league_news_latest": site.news_list()[:8],
    }
    print(json.dumps(facts, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
