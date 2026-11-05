"""Тексты сообщений для канала (HTML-разметка Telegram)."""
from __future__ import annotations

import re
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from .rhl import Conference, Goal, Match, Penalty, Protocol

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
SITUATION = {"рав.": "в равных составах", "бол.": "в большинстве",
             "мен.": "в меньшинстве", "п.в.": "в пустые ворота"}


def _int_safe(s: str) -> int:
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else 0


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def signed(v: str | int) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v)
    return f"+{n}" if n > 0 else str(n)


def day_str(d: datetime | date, weekday: bool = True) -> str:
    s = f"{d.day} {MONTHS[d.month - 1]}"
    return f"{WEEKDAYS[d.weekday()]}, {s}" if weekday else s


def period_name(per: str) -> str:
    per = (per or "").strip()
    if per.isdigit():
        return f"{per}-й период"
    if per.upper().startswith("ОТ"):
        return "овертайм"
    if per.upper().startswith("Б"):
        return "буллиты"
    return per


def period_by_time(t: str) -> str:
    m = re.match(r"(\d+):", t or "")
    if not m:
        return ""
    minute = int(m.group(1))
    return period_name(str(minute // 20 + 1)) if minute < 60 else "овертайм"


class Texts:
    def __init__(self, cfg: dict):
        pl = cfg["player"]
        self.pid = pl["id"]
        self.full = f"{pl['first_name']} {pl['last_name']}"
        self.first = pl["first_name"]
        self.full_gen = pl.get("full_genitive", self.full)
        self.first_gen = pl.get("first_genitive", self.first)
        self.number = pl["number"]
        self.team = cfg["team"]["name"]          # «Кристалл»
        self.team_gen = cfg["team"].get("name_genitive", self.team)
        self.site = cfg["site"].rstrip("/")
        self.msk = ZoneInfo(cfg.get("timezone", "Europe/Moscow"))
        self.extra_tz = [(z["name"], ZoneInfo(z["tz"])) for z in cfg.get("extra_timezones", [])]

    # --- вспомогательное

    def hm(self, k: datetime) -> str:
        """'13:00 мск / 15:00 по Уфе'"""
        parts = [f"{k.astimezone(self.msk):%H:%M} мск"]
        parts += [f"{k.astimezone(tz):%H:%M} по {name}" for name, tz in self.extra_tz]
        return " / ".join(parts)

    def ours(self, team_name: str) -> bool:
        return self.team.lower() in (team_name or "").lower()

    def who(self, pid: int, name: str) -> str:
        return f"<b>{escape(name)}</b>" if pid == self.pid else escape(name)

    def surname(self, pid: int, name: str) -> str:
        """'Данил Нигматуллин' -> 'Нигматуллин' (жирным, если это Данил)."""
        last = escape(name.split()[-1]) if name else ""
        return f"<b>{last}</b>" if pid == self.pid else last

    def scoreline(self, home: str, away: str, score: str) -> str:
        return f"{escape(home)} <b>{escape(score)}</b> {escape(away)}"

    def match_url(self, m: Match, tab: str = "") -> str:
        return f"{self.site}/matchcenter/{m.tournament}/{m.id}/{tab + '/' if tab else ''}"

    def line(self, st: dict) -> str:
        """'2 гола, 1 передача · +/- +3 · бросков 9'"""
        g, a = int(st.get("goals") or 0), int(st.get("a") or 0)
        parts = []
        if g or a:
            bits = []
            if g:
                bits.append(f"{g} {plural(g, 'гол', 'гола', 'голов')}")
            if a:
                bits.append(f"{a} {plural(a, 'передача', 'передачи', 'передач')}")
            parts.append(", ".join(bits))
        else:
            parts.append("без очков")
        if st.get("pm") not in (None, "", "-"):
            parts.append(f"+/- {signed(st['pm'])}")
        if st.get("sog") not in (None, "", "-", "0"):
            parts.append(f"бросков {st['sog']}")
        if st.get("pim") not in (None, "", "-", "0"):
            parts.append(f"штраф {st['pim']} мин")
        return " · ".join(parts)

    # --- матч

    def live(self, m: Match, link: str) -> str:
        k = m.kickoff_dt
        return (
            f"🔴 <b>ПРЯМАЯ ТРАНСЛЯЦИЯ</b>\n"
            f"{escape(m.home)} — {escape(m.away)}\n"
            f"Начало: {self.hm(k)}\n\n"
            f"▶️ <a href=\"{escape(link)}\">Смотреть матч</a>\n"
            f"{link}"
        )

    def reminder(self, m: Match, minutes_left: int, season_line: str = "") -> str:
        k = m.kickoff_dt
        if 110 <= minutes_left <= 130:
            left = "через 2 часа"
        else:
            h, mi = divmod(max(minutes_left, 0), 60)
            left = "через " + (f"{h} ч " if h else "") + f"{mi} мин"
        where = "дома" if self.ours(m.home) else "в гостях"
        text = (f"⏰ <b>Игра начнётся {left}!</b>\n"
                f"{escape(m.home)} — {escape(m.away)} ({where})\n"
                f"Начало: {self.hm(k)}")
        place = re.sub(r"^№\s*\d+\s*", "", m.place or "")
        if place:
            text += f"\n📍 {escape(place)}"
        if season_line:
            text += f"\n\n🏒 {escape(self.first)} в сезоне: {season_line}"
        text += "\n\nСсылку на трансляцию пришлю, как только она появится."
        return text

    def goal(self, m: Match, p: Protocol, g: Goal) -> str:
        home, away = p.home or m.home, p.away or m.away
        scoring_team = home if g.side == "home" else away if g.side == "away" else ""
        ours = self.ours(scoring_team) if scoring_team else None
        is_scorer = g.scorer_id == self.pid
        is_assist = any(pid == self.pid for pid, _ in g.assists)

        if is_scorer:
            head = f"🔥🚨 <b>ГОЛ! {escape(self.first.upper())} ЗАБИВАЕТ!</b> 🔥"
        elif ours:
            head = f"🚨 <b>ГОЛ! «{escape(self.team)}» забивает!</b>"
        elif ours is False:
            head = "😤 Гол в наши ворота"
        else:
            head = "🚨 <b>Гол!</b>"

        when = f"⏱ {escape(g.time)}, {period_name(g.period)}"
        sit = SITUATION.get(g.situation, g.situation)
        if sit and g.situation != "рав.":
            when += f" · {escape(sit)}"
        who = f"🏒 {self.who(g.scorer_id, g.scorer)}"
        if g.assists:
            who += " (" + ", ".join(self.who(pid, n) for pid, n in g.assists) + ")"

        text = f"{head}\n{self.scoreline(home, away, g.score)}\n{when}\n{who}"
        if is_assist:
            text += f"\n\n🅰️ <b>Передача {escape(self.first_gen)}!</b>"
        return text

    def _verdict(self, home: str, away: str, score: str) -> str:
        try:
            h, a = (int(x) for x in score.split(":"))
        except ValueError:
            return ""
        won = (h > a) == self.ours(home)
        return (f"✅ «{escape(self.team)}» побеждает!" if won
                else f"❌ «{escape(self.team)}» уступает")

    def final_whistle(self, m: Match, p: Protocol) -> str:
        home, away = p.home or m.home, p.away or m.away
        extra = f" ({escape(p.score_type)})" if p.score_type else ""
        return (f"🏁 <b>Финальная сирена!</b>\n{self.scoreline(home, away, p.score)}{extra}\n"
                f"{self._verdict(home, away, p.score)}\n\n"
                f"Подробный итог — голы, передачи, статистику {escape(self.first_gen)} — пришлю, "
                "как только лига заполнит протокол: обычно ближе к ночи "
                "(ночью сообщения приходят без звука).")

    # --- живые события по счёту (пока протокол не заполнен)

    def match_started(self, m: Match, p: Protocol) -> str:
        return (f"🏒 <b>Матч начался!</b>\n{escape(p.home or m.home)} — {escape(p.away or m.away)}\n"
                "Голы буду присылать по ходу игры.")

    def live_goal(self, m: Match, p: Protocol, scoring_side: str, score: str) -> str:
        home, away = p.home or m.home, p.away or m.away
        ours = self.ours(home if scoring_side == "home" else away)
        head = (f"🚨 <b>ГОЛ! «{escape(self.team)}» забивает!</b>" if ours else "😤 Гол в наши ворота")
        when = f"\n⏱ {escape(p.status)}" if p.status and not p.finished else ""
        return (f"{head}\n{self.scoreline(home, away, score)}{when}\n"
                "<i>Кто забил и кто отдал передачи — пришлю, как только лига внесёт в протокол.</i>")

    def score_corrected(self, m: Match, p: Protocol) -> str:
        return f"⚠️ Счёт поправили: {self.scoreline(p.home or m.home, p.away or m.away, p.score)}"

    def break_status(self, m: Match, p: Protocol) -> str:
        return f"⏸ <b>{escape(p.status)}</b>\n{self.scoreline(p.home or m.home, p.away or m.away, p.score)}"

    def danil_goal_details(self, m: Match, p: Protocol, g: Goal) -> str:
        who = f"🏒 {self.who(g.scorer_id, g.scorer)}"
        if g.assists:
            who += " (" + ", ".join(self.who(pid, n) for pid, n in g.assists) + ")"
        return (f"🔥 <b>Это был гол {escape(self.first_gen)}!</b>\n"
                f"⏱ {escape(g.time)}, {period_name(g.period)} · счёт {escape(g.score)}\n{who}")

    def late_assist(self, m: Match, p: Protocol, g: Goal) -> str:
        return (f"🅰️ <b>Передача {escape(self.first_gen)}!</b>\n"
                f"⏱ {escape(g.time)}, {period_name(g.period)} · счёт {escape(g.score)}\n"
                f"🏒 {self.who(g.scorer_id, g.scorer)} ("
                + ", ".join(self.who(pid, n) for pid, n in g.assists) + ")")

    def late_goal(self, m: Match, p: Protocol, g: Goal) -> str:
        return (f"🔥 <b>Гол на {escape(g.time)} записали на {escape(self.first_gen)}!</b>\n"
                f"{self.scoreline(p.home or m.home, p.away or m.away, g.score)}")

    def penalty(self, m: Match, p: Protocol, pen: Penalty) -> str:
        home, away = p.home or m.home, p.away or m.away
        per = period_by_time(pen.time)
        return (
            f"⛔ <b>Удаление: {escape(pen.player)}</b> — {escape(pen.minutes)} мин\n"
            f"Нарушение: {escape(pen.reason)}\n"
            f"⏱ {escape(pen.time)}{', ' + per if per else ''}\n"
            f"Счёт: {self.scoreline(home, away, p.score or '0:0')}"
        )

    @staticmethod
    def _n(st: dict, key: str) -> int:
        return _int_safe(st.get(key, "")) * (-1 if str(st.get(key, "")).startswith("-") else 1)

    def _ranked(self, rows: list) -> list:
        key = lambda r: (self._n(r[2], "points"), self._n(r[2], "goals"), self._n(r[2], "sog"))
        return sorted(rows, key=key, reverse=True)

    def rank_of(self, rows: list, field: str) -> int | None:
        me = next((r for r in rows if r[0] == self.pid), None)
        if not me:
            return None
        return 1 + sum(1 for r in rows if self._n(r[2], field) > self._n(me[2], field))

    def forwards_block(self, title: str, rows: list, season: bool, top: int = 5) -> str:
        if not rows:
            return ""
        ranked = self._ranked(rows)
        if not season:
            ranked = [r for r in ranked if self._n(r[2], "points") or r[0] == self.pid] or ranked[:3]
        shown = ranked[:top]
        text = f"\n📊 <b>{title}</b>\n"
        for i, (pid, name, st) in enumerate(ranked, 1):
            if (pid, name, st) not in shown and pid != self.pid:
                continue
            if i > top and pid == self.pid:
                text += "…\n"
            g, a, pts = self._n(st, "goals"), self._n(st, "a"), self._n(st, "points")
            line = f"{i}. {self.surname(pid, name)} — {g}+{a}"
            if season:
                line += f" ({pts} {plural(pts, 'очко', 'очка', 'очков')}, {st.get('matches', '0')} и)"
            line += f", +/- {signed(st.get('pm', '0'))}"
            if st.get("sog") not in (None, "", "-", "0"):
                line += f", бр. {st['sog']}"
            text += line + "\n"
        rp, rg = self.rank_of(rows, "points"), self.rank_of(rows, "goals")
        if rp:
            text += f"{escape(self.first)}: {rp}-й по очкам"
            text += f", {rg}-й по голам" if season else ""
            text += " среди нападающих\n"
        return text

    def result(self, m: Match, p: Protocol, video: str | None, season_fw: list | None = None) -> str:
        home, away = p.home or m.home, p.away or m.away
        extra = f" ({escape(p.score_type)})" if p.score_type else ""
        text = f"🏁 <b>Матч завершён</b>\n{self.scoreline(home, away, p.score)}{extra}\n"
        try:
            h, a = (int(x) for x in p.score.split(":"))
            we_home = self.ours(home)
            won = (h > a) == we_home
            text += (f"✅ «{escape(self.team)}» побеждает!\n" if won
                     else f"❌ «{escape(self.team)}» уступает\n")
        except ValueError:
            pass
        ours_side = "home" if self.ours(home) else "away" if self.ours(away) else ""
        our_goals = [g for g in p.goals if g.side == ours_side]
        if our_goals:
            text += f"\n🏒 <b>Голы «{escape(self.team_gen)}»:</b>\n"
            for g in our_goals:
                line = f"• {escape(g.time)} — {self.surname(g.scorer_id, g.scorer)}"
                if g.assists:
                    line += " (" + ", ".join(self.surname(pid, n) for pid, n in g.assists) + ")"
                if g.situation and g.situation != "рав.":
                    line += f", {escape(SITUATION.get(g.situation, g.situation))}"
                text += line + "\n"
        our_pens = [x for x in p.penalties if x.side == ours_side]
        if our_pens:
            total = sum(_int_safe(x.minutes) for x in our_pens)
            text += f"\n⛔ <b>Удаления «{escape(self.team_gen)}»</b> ({total} мин):\n"
            for x in our_pens:
                who = self.surname(x.player_id, x.player) if x.player_id else "командный штраф"
                text += f"• {escape(x.time)} — {who}, {escape(x.minutes)} мин ({escape(x.reason)})\n"
        text += "\n"
        if p.player_line:
            text += f"⭐ <b>{escape(self.full)}</b>: {self.line(p.player_line)}\n"
            for g in p.goals:
                if g.scorer_id == self.pid:
                    text += f"   • гол на {escape(g.time)} ({period_name(g.period)})\n"
                elif any(pid == self.pid for pid, _ in g.assists):
                    text += (f"   • передача на {escape(g.time)} (забил "
                             f"{escape(g.scorer.split()[-1] if g.scorer else '?')})\n")
        else:
            text += f"{escape(self.first)} в протоколе этого матча не значится.\n"
        text += self.forwards_block(f"Нападающие «{escape(self.team_gen)}» за матч",
                                    p.forwards.get(ours_side, []), season=False)
        if season_fw:
            text += self.forwards_block(f"Нападающие «{escape(self.team_gen)}» за сезон",
                                        season_fw, season=True)
        text += "\n"
        if video:
            text += f"📺 <a href=\"{escape(video)}\">Запись матча</a>\n"
        text += f"📋 <a href=\"{self.match_url(m, 'protocol')}\">Протокол</a>"
        return text

    # --- утренняя сводка

    def daily(self, today: date, season_rows, last_game: dict | None,
              conf: Conference | None, next_match: Match | None, now: datetime,
              season_fw: list | None = None, title: str | None = None) -> str:
        text = f"<b>{title}</b>\n\n" if title else f"☀️ <b>Сводка на {day_str(today)}</b>\n\n"
        text += f"🏒 <b>{escape(self.full)}</b> #{self.number} · «{escape(self.team)}»\n"
        for name, st in season_rows:
            g, a, pts = (int(st.get(k) or 0) for k in ("goals", "a", "points"))
            games = int(st.get("matches") or 0)
            text += f"\n<b>{escape(name)}</b>\n"
            text += f"• Игры: {games}\n"
            text += f"• Очки: {pts} ({g} {plural(g, 'гол', 'гола', 'голов')} + {a} {plural(a, 'передача', 'передачи', 'передач')})\n"
            text += f"• +/-: {signed(st.get('pm', '0'))} · штраф: {st.get('pim', '0')} мин\n"
            if st.get("sog"):
                text += f"• Броски: {st['sog']}"
                if st.get("sogPct"):
                    text += f" (реализация {st['sogPct'].replace('.', ',')}%)"
                text += "\n"
            if st.get("toiAvg") and st["toiAvg"] != "-":
                text += f"• Время на льду: {st['toiAvg']} за игру\n"
            if st.get("foPtc") and st.get("fo") not in ("0", ""):
                text += f"• Вбрасывания: {st['foPtc'].replace('.', ',')}% выигранных\n"

        if season_fw:
            rp, rg = self.rank_of(season_fw, "points"), self.rank_of(season_fw, "goals")
            if rp:
                text += (f"📈 Среди нападающих «{escape(self.team_gen)}»: {rp}-й по очкам, "
                         f"{rg}-й по голам\n")

        if last_game:
            text += (f"\n🕐 Последняя игра — {escape(last_game['date'].rsplit(' ', 1)[0])}:\n"
                     f"{self.scoreline(last_game['home'], last_game['away'], last_game['score'])}\n"
                     f"{escape(self.first)}: {self.line(last_game['stats'])}\n")

        if conf:
            row = next((r for r in conf.rows if self.ours(r.team)), None)
            if row:
                text += (f"\n🏆 «{escape(self.team)}» — {row.pos}-е место в конференции "
                         f"«{escape(conf.name)}» ({row.points} {plural(row.points, 'очко', 'очка', 'очков')}, "
                         f"{row.games} {plural(row.games, 'игра', 'игры', 'игр')})\n")

        if next_match:
            k = next_match.kickoff_dt
            where = "дома" if self.ours(next_match.home) else "в гостях"
            if k.date() == today:
                text += (f"\n🔥 <b>СЕГОДНЯ ИГРА!</b> {self.hm(k)}\n"
                         f"{escape(next_match.home)} — {escape(next_match.away)} ({where})\n"
                         f"Ссылку на трансляцию пришлю, как только она появится.")
            else:
                text += (f"\n📅 Следующая игра: {day_str(k)}, {self.hm(k)}\n"
                         f"{escape(next_match.home)} — {escape(next_match.away)} ({where})")
        return text

    # --- еженедельный пост

    def weekly(self, conf: Conference | None, prev_snap: dict | None, week_games: list[dict],
               upcoming: list[Match], today: date) -> str:
        text = "📊 <b>Итоги недели</b>\n"
        row = None
        if conf:
            text += f"\n<b>Конференция «{escape(conf.name)}»</b> (очки, в скобках — игры)\n"
            for r in conf.rows:
                mine = self.ours(r.team)
                if mine:
                    row = r
                    text += f"👉 <b>{r.pos}. {escape(r.team)} — {r.points} ({r.games})</b>\n"
                else:
                    text += f"{r.pos}. {escape(r.team)} — {r.points} ({r.games})\n"
                if r.playoff_line:
                    text += "┄┄┄┄ черта плей-офф ┄┄┄┄\n"

        notes = self.standings_comments(conf, row, prev_snap, week_games)
        if notes:
            text += "\n💬 <b>Комментарий</b>\n" + "\n".join(f"• {n}" for n in notes) + "\n"

        text += "\n🗓 <b>Календарь на неделю</b>\n"
        if upcoming:
            for m in upcoming:
                k = m.kickoff_dt
                where = "дома" if self.ours(m.home) else "в гостях"
                text += f"• <b>{WEEKDAYS[k.weekday()]} {k:%d.%m}</b>, {self.hm(k)}\n   {escape(m.home)} – {escape(m.away)} ({where})\n"
        else:
            text += "На ближайшей неделе игр нет.\n"
        return text.rstrip()

    def standings_comments(self, conf, row, prev_snap, week_games) -> list[str]:
        notes = []
        if week_games:
            wins = sum(1 for g in week_games if g.get("winner") and self.ours(g[g["winner"]]))
            losses = len(week_games) - wins
            n = len(week_games)
            notes.append(f"За неделю {n} {plural(n, 'матч', 'матча', 'матчей')}: "
                         f"{wins} {plural(wins, 'победа', 'победы', 'побед')}, "
                         f"{losses} {plural(losses, 'поражение', 'поражения', 'поражений')}.")
            g = sum(int(x["stats"].get("goals") or 0) for x in week_games)
            a = sum(int(x["stats"].get("a") or 0) for x in week_games)
            if g or a:
                notes.append(f"{escape(self.first)} за неделю: {g}+{a} "
                             f"({g + a} {plural(g + a, 'очко', 'очка', 'очков')}).")
            else:
                notes.append(f"{escape(self.first)} на этой неделе без очков — ждём на следующей! 💪")
        if not row:
            return notes

        if prev_snap and prev_snap.get("pos"):
            d = prev_snap["pos"] - row.pos
            if d > 0:
                notes.append(f"Поднялись на {d} {plural(d, 'место', 'места', 'мест')} "
                             f"(было {prev_snap['pos']}-е, стало {row.pos}-е). 📈")
            elif d < 0:
                notes.append(f"Опустились на {-d} {plural(-d, 'место', 'места', 'мест')} "
                             f"(было {prev_snap['pos']}-е, стало {row.pos}-е). 📉")
            else:
                notes.append(f"Место не изменилось — {row.pos}-е.")

        rows = conf.rows
        idx = rows.index(row)
        if idx > 0:
            up = rows[idx - 1]
            gap = up.points - row.points
            s = (f"До {up.pos}-го места ({escape(up.team)}) — "
                 f"{gap} {plural(gap, 'очко', 'очка', 'очков')}" if gap else
                 f"С {escape(up.team)} ({up.pos}-е место) — поровну очков")
            if up.games < row.games:
                n = row.games - up.games
                s += f", но у соперника {n} {plural(n, 'игра', 'игры', 'игр')} в запасе"
            notes.append(s + ".")
        else:
            notes.append("Первое место в конференции! 🥇")

        line_idx = next((i for i, r in enumerate(rows) if r.playoff_line), None)
        if line_idx is not None:
            if idx <= line_idx and line_idx + 1 < len(rows):
                below = rows[line_idx + 1]
                gap = row.points - below.points
                notes.append(f"В зоне плей-офф ✅ Отрыв от {below.pos}-го места — "
                             f"{gap} {plural(gap, 'очко', 'очка', 'очков')}.")
            elif idx > line_idx:
                last_in = rows[line_idx]
                gap = last_in.points - row.points
                notes.append(f"До зоны плей-офф ({last_in.pos}-е место) — "
                             f"{gap} {plural(gap, 'очко', 'очка', 'очков')}.")

        if row.form:
            icons = "".join("✅" if f == "W" else "❌" for f in row.form[-5:])
            notes.append(f"Форма в последних играх: {icons}")
        return notes

    # --- лидеры команды и конференции

    LEADER_LISTS = (("points", "🎯 Бомбардиры", ("points", "goals")),
                    ("goals", "🥅 Снайперы", ("goals", "points")),
                    ("a", "🅰️ Ассистенты", ("a", "points")))

    def _leader_line(self, i: int, r: dict, field: str, with_team: bool) -> str:
        name = self.surname(r["id"], r["name"])
        team = f" ({escape(r['team'])})" if with_team else ""
        val = r[field]
        extra = f" ({r['goals']}+{r['a']})" if field == "points" else ""
        return f"{i}. {name}{team} — {val}{extra}"

    def _leader_block(self, rows: list[dict], with_team: bool, top: int) -> tuple[str, dict]:
        text, ranks = "", {}
        for field, title, keys in self.LEADER_LISTS:
            ranked = sorted(rows, key=lambda r: tuple(r[k] for k in keys), reverse=True)
            me = next((r for r in rows if r["id"] == self.pid), None)
            if me:
                ranks[field] = 1 + sum(1 for r in rows if r[field] > me[field])
            text += f"\n{title}\n"
            for i, r in enumerate(ranked[:top], 1):
                text += self._leader_line(i, r, field, with_team) + "\n"
            if me and me not in ranked[:top]:
                text += "…\n" + self._leader_line(ranks[field], me, field, with_team) + "\n"
        return text, ranks

    def leaders(self, team_rows: list[dict], conf_rows: list[dict], conf_name: str, today: date) -> str:
        text = f"🏆 <b>Лидеры на {day_str(today)}</b>\n"
        team_ranks = conf_ranks = {}
        if team_rows:
            block, team_ranks = self._leader_block(team_rows, with_team=False, top=5)
            text += f"\n<b>«{escape(self.team)}»</b>\n" + block
        if conf_rows:
            block, conf_ranks = self._leader_block(conf_rows, with_team=True, top=5)
            text += f"\n<b>Конференция «{escape(conf_name)}»</b>\n" + block
        def fmt(r: dict) -> str:
            names = {"points": "по очкам", "goals": "по голам", "a": "по передачам"}
            return ", ".join(f"{v}-й {names[k]}" for k, v in r.items())
        if team_ranks or conf_ranks:
            text += f"\n⭐ <b>{escape(self.first)}</b>"
            if team_ranks:
                text += f"\nв команде: {fmt(team_ranks)}"
            if conf_ranks:
                text += f"\nв конференции: {fmt(conf_ranks)}"
        return text.rstrip()

    def welcome(self) -> str:
        return (
            f"👋 <b>Привет! Это канал про {escape(self.full_gen)}</b> (#{self.number}, «{escape(self.team)}»).\n\n"
            "Что сюда будет приходить:\n"
            "🔴 ссылка на прямую трансляцию каждого матча\n"
            "🚨 все голы по ходу игры с актуальным счётом\n"
            f"⭐ голы, передачи и удаления {escape(self.first_gen)}\n"
            f"🏁 итог матча и статистика {escape(self.first_gen)}\n"
            "☀️ каждое утро — свежая статистика и ближайшая игра\n"
            "📊 по понедельникам — таблица конференции с комментарием и календарь на неделю"
        )
