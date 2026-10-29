"""Парсер сайта Российской хоккейной лиги (rhl.fhr.ru).

Все нужные данные отдаются сервером в обычном HTML, поэтому хватает
requests + BeautifulSoup. Если сайт поменяет вёрстку — чинить здесь.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MATCH_HREF = re.compile(r"/matchcenter/(\d+)/(\d+)/")
PLAYER_HREF = re.compile(r"/players/(\d+)/")


def _text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _int(s: str, default: int = 0) -> int:
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else default


def _person_name(node) -> str:
    """'Нигматуллин<br/>Данил' -> 'Данил Нигматуллин'."""
    parts = [p.strip() for p in node.get_text("\n").split("\n") if p.strip()]
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0]}"
    return parts[0] if parts else ""


def _params(row, prefix: str) -> dict[str, str]:
    """Собирает ячейки вида <td class="...{prefix}--goals">5</td> в словарь."""
    out = {}
    for td in row.find_all("td"):
        for cls in td.get("class", []):
            if cls.startswith(prefix + "--"):
                out[cls[len(prefix) + 2:]] = _text(td)
    return out


# ---------------------------------------------------------------- модели

@dataclass
class Match:
    id: int
    tournament: int
    kickoff: str            # ISO с часовым поясом, например 2026-11-01T13:00:00+03:00
    home: str
    away: str
    place: str = ""         # «№ 112 Ростов-на-Дону»

    @property
    def kickoff_dt(self) -> datetime:
        return datetime.fromisoformat(self.kickoff)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Goal:
    period: str
    time: str
    score: str              # счёт после гола, хозяева:гости
    situation: str          # рав./бол./мен.
    scorer_id: int
    scorer: str
    assists: list[tuple[int, str]] = field(default_factory=list)
    side: str = ""          # home / away — кто забил

    @property
    def key(self) -> str:
        return f"{self.period}|{self.time}"


@dataclass
class Penalty:
    time: str
    player_id: int
    player: str
    minutes: str
    reason: str
    side: str

    @property
    def key(self) -> str:
        return f"{self.time}|{self.player_id}|{self.reason}"


@dataclass
class Protocol:
    home: str = ""
    away: str = ""
    score: str = ""
    finished: bool = False
    score_type: str = ""    # ОТ / Б
    goals: list[Goal] = field(default_factory=list)
    penalties: list[Penalty] = field(default_factory=list)
    player_line: dict[str, str] | None = None


@dataclass
class StandingRow:
    pos: int
    team: str
    team_id: int
    games: int
    points: int
    wins: int
    goals: str
    form: list[str]          # 'W' / 'L' от старых к новым
    playoff_line: bool       # после этой строки — черта плей-офф


@dataclass
class Conference:
    name: str
    rows: list[StandingRow]


# ---------------------------------------------------------------- клиент

class RHL:
    def __init__(self, base: str, team_id: int, player_id: int):
        self.base = base.rstrip("/")
        self.team_id = team_id
        self.player_id = player_id
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.season = ""        # 2026-2027, определяется по календарю
        self.tournament = 0

    def url(self, path: str) -> str:
        return self.base + path

    def get(self, path: str) -> BeautifulSoup:
        last = None
        for attempt in range(3):
            try:
                r = self.s.get(self.url(path), timeout=25)
                if r.status_code == 404:
                    raise LookupError(f"Нет страницы {path}")
                r.raise_for_status()
                r.encoding = "utf-8"
                return BeautifulSoup(r.text, "html.parser")
            except requests.RequestException as e:
                last = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"Не удалось загрузить {path}: {last}")

    # --- календарь

    def calendar(self) -> list[Match]:
        return self.parse_calendar(self.get(f"/teams/{self.team_id}/calendar/"))

    def parse_calendar(self, soup: BeautifulSoup) -> list[Match]:
        opt = soup.select_one("#filter-seasons option[selected]")
        if opt:
            self.season = opt.get("value", "")
        t = soup.select_one("#filter-tournaments .switcher__item--selected")
        if t and t.get("data-value", "").isdigit():
            self.tournament = int(t["data-value"])

        matches = []
        for card in soup.select("article.match-card"):
            a = card.select_one("a.match-card__body")
            tm = card.select_one("time.match-card__date")
            if not a or not tm:
                continue
            m = MATCH_HREF.search(a.get("href", ""))
            names = [_text(n) for n in card.select(".match-card-team__name")]
            if not m or len(names) < 2:
                continue
            place = _text(card.select_one(".match-card__header .desktop-tablet-content"))
            place = re.sub(r"^Неделя \d+,\s*", "", place)
            matches.append(Match(
                id=int(m.group(2)), tournament=int(m.group(1)),
                kickoff=tm.get("datetime", ""), home=names[0], away=names[1],
                place=place,
            ))
        return matches

    # --- трансляция

    def video_link(self, match: Match) -> str | None:
        try:
            soup = self.get(f"/matchcenter/{match.tournament}/{match.id}/video/")
        except LookupError:
            return None                     # видео у матча нет (или его убрали)
        return self.parse_video(soup)

    @staticmethod
    def parse_video(soup: BeautifulSoup) -> str | None:
        frame = soup.select_one(".matchcenter-video iframe[src]")
        if not frame:
            return None
        src = frame["src"]
        q = parse_qs(urlparse(src).query)
        if "vkvideo" in src or "vk.com" in src or "vk.ru" in src:
            if q.get("oid") and q.get("id"):
                return f"https://vkvideo.ru/video{q['oid'][0]}_{q['id'][0]}"
        return src

    # --- протокол матча (обновляется во время игры)

    def protocol(self, match: Match) -> Protocol:
        soup = self.get(f"/matchcenter/{match.tournament}/{match.id}/protocol/")
        return self.parse_protocol(soup, match.id)

    def parse_protocol(self, soup: BeautifulSoup, match_id: int) -> Protocol:
        p = Protocol()
        hero = soup.select_one(".matchcenter-hero")
        if hero:
            names = hero.select(".matchcenter-hero__team-name")
            if len(names) >= 2:
                p.home, p.away = _text(names[0]), _text(names[1])
            p.score = _text(hero.select_one(".matchcenter-hero__score-main"))
            p.finished = any("--winner-" in c for c in hero.get("class", []))

        # Лента матчей в шапке сайта: статус и «ОТ»/«Б».
        for feed in soup.select("a.matches-feed__match"):
            m = MATCH_HREF.search(feed.get("href", ""))
            if m and int(m.group(2)) == match_id:
                if "matches-feed__match--status-finished" in feed.get("class", []):
                    p.finished = True
                p.score_type = _text(feed.select_one(".matches-feed__match-scores-type"))
                break

        # Голы
        prev_h, prev_a = 0, 0
        for row in soup.select(".matchcenter-protocol-goal-scored tbody tr"):
            prm = _params(row, "matchcenter-protocol-goal-scored__table-col-param")
            if "time" not in prm:
                continue
            people = []
            for cell in row.select("td.matchcenter-protocol-goal-scored__table-col-player-name"):
                a = cell.select_one("a.person-name-with-meta__name")
                if a:
                    pid = PLAYER_HREF.search(a.get("href", ""))
                    people.append((int(pid.group(1)) if pid else 0, _person_name(a)))
            if not people:
                continue
            h, a_ = (_int(x) for x in (prm.get("score", "0:0").split(":") + ["0"])[:2])
            side = "home" if h > prev_h else "away" if a_ > prev_a else ""
            prev_h, prev_a = h, a_
            p.goals.append(Goal(
                period=prm.get("per", ""), time=prm.get("time", ""),
                score=prm.get("score", ""), situation=prm.get("state", ""),
                scorer_id=people[0][0], scorer=people[0][1], assists=people[1:],
                side=side,
            ))

        # Удаления
        for group in soup.select(".matchcenter-protocol-penalties__group"):
            classes = " ".join(group.get("class", []))
            side = "home" if "--home" in classes else "away"
            for row in group.select("tr.matchcenter-protocol-penalties__group-table-row"):
                a = row.select_one("a.person-name-with-meta__name")
                pid = PLAYER_HREF.search(a.get("href", "")) if a else None
                p.penalties.append(Penalty(
                    time=_text(row.select_one(".matchcenter-protocol-penalties__group-table__time")),
                    player_id=int(pid.group(1)) if pid else 0,
                    player=_person_name(a) if a else "команда",
                    minutes=_text(row.select_one(".matchcenter-protocol-penalties__group-table-col-min")),
                    reason=_text(row.select_one(".matchcenter-protocol-penalties__group-table-penalty")),
                    side=side,
                ))

        # Строка игрока в статистике матча
        for row in soup.select(".matchcenter-protocol-players-stats tbody tr"):
            a = row.select_one("a.person-name-with-meta__name")
            if a and f"/players/{self.player_id}/" in a.get("href", ""):
                p.player_line = _params(row, "matchcenter-protocol-players-stats__table-col-param")
                break
        return p

    # --- игрок

    def player_season(self) -> list[tuple[str, dict[str, str]]]:
        """Строки статистики текущего сезона: [('26/27 Рег. чемпионат', {...}), ...]."""
        soup = self.get(f"/players/{self.player_id}/")
        return self.parse_player_season(soup)

    def parse_player_season(self, soup: BeautifulSoup) -> list[tuple[str, dict[str, str]]]:
        short = ""
        if self.season and "-" in self.season:
            a, b = self.season.split("-")
            short = f"{a[2:]}/{b[2:]}"
        out, seen = [], set()
        for row in soup.select("tr"):
            name = _text(row.select_one(".player-profile-stats__table-tournament-name"))
            if not name or (short and not name.startswith(short)) or name in seen:
                continue
            seen.add(name)
            out.append((name, _params(row, "player-profile-stats__table-col-param")))
        return out

    def player_games(self) -> list[dict]:
        soup = self.get(f"/players/{self.player_id}/games/")
        return self.parse_player_games(soup)

    @staticmethod
    def parse_player_games(soup: BeautifulSoup) -> list[dict]:
        games = []
        for row in soup.select("tr"):
            score = row.select_one(".player-profile-matches__table-col-score a")
            if not score:
                continue
            m = MATCH_HREF.search(score.get("href", ""))
            teams = row.select(".player-profile-matches__table-team")
            winner = ""
            for side, t in zip(("home", "away"), teams):
                if "player-profile-matches__table-team--win" in t.get("class", []):
                    winner = side
            games.append({
                "winner": winner,
                "match_id": int(m.group(2)) if m else 0,
                "date": _text(row.select_one(".player-profile-matches__table-col-date")),
                "home": _text(teams[0]) if teams else "",
                "away": _text(teams[1]) if len(teams) > 1 else "",
                "score": _text(score),
                "stats": _params(row, "player-profile-matches__table-col-param"),
            })
        return games

    # --- новости и обзоры лиги

    def news_list(self) -> list[dict]:
        soup = self.get("/news/")
        out = []
        for a in soup.select("a.material-card__body[href^='/news/']"):
            m = re.search(r"/news/(\d+)/", a.get("href", ""))
            name = a.select_one(".material-card__name")
            tm = a.select_one("time")
            if m and name:
                out.append({"id": int(m.group(1)), "url": self.url(m.group(0)), "title": _text(name),
                            "date": tm.get("datetime", "") if tm else ""})
        return out

    def news_text(self, url: str) -> str:
        soup = self.get(urlparse(url).path)
        return _text(soup.select_one(".news-detail__text"))

    # --- турнирная таблица по конференциям

    def conferences(self) -> list[Conference]:
        path = "/standings/regular/conference/"
        if self.season and self.tournament:
            path += f"season/{self.season}/tournament/{self.tournament}/"
        return self.parse_conferences(self.get(path))

    @staticmethod
    def parse_conferences(soup: BeautifulSoup) -> list[Conference]:
        out = []
        for table in soup.select("table"):
            rows = []
            for tr in table.select("tr"):
                classes = tr.get("class") or []
                if any("underline-playoff" in c for c in classes):
                    # отдельная пустая строка-черта: зона плей-офф заканчивается на предыдущей
                    if rows:
                        rows[-1].playoff_line = True
                    continue
                if not any(c.startswith("tourtable-regular__table-row") for c in classes):
                    continue
                name_a = tr.select_one(".tourtable-regular__table-team-name")
                if not name_a:
                    continue
                q = parse_qs(urlparse(name_a.get("href", "")).query)
                prm = _params(tr, "tourtable-regular__table-col--param")
                form = []
                for img in tr.select(".team-match-form__finished_matches img"):
                    src = img.get("src", "")
                    form.append("W" if "win" in src else "L")
                rows.append(StandingRow(
                    pos=_int(_text(tr.select_one(".tourtable-regular__table-position"))),
                    team=_text(name_a),
                    team_id=_int(q.get("teamId", ["0"])[0]),
                    games=_int(prm.get("matches", "")),
                    points=_int(prm.get("points", "")),
                    wins=_int(prm.get("wins", "")),
                    goals=prm.get("gfga", ""),
                    form=form,
                    playoff_line=False,
                ))
            if not rows:
                continue
            # Название конференции — ближайший текст «...» перед таблицей.
            name = ""
            for prev in table.find_all_previous(string=re.compile(r"«[^»]+»")):
                name = re.search(r"«([^»]+)»", prev).group(1)
                break
            out.append(Conference(name=name, rows=rows))
        return out
