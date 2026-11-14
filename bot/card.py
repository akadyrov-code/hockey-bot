"""Картинка-итог матча: счёт и строка Данила — чтобы переслать родным.

    render(...) -> JPEG в байтах. Рисуем Pillow, шрифт DejaVu лежит в assets/fonts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
W, H = 1080, 1350                  # 4:5; с вехами картинка может быть чуть выше
NAVY_TOP, NAVY_BOTTOM = (9, 24, 56), (20, 48, 107)
WHITE, GREY, RED, GOLD, GREEN = "#FFFFFF", "#B9C4DC", "#E03A3E", "#FFD24A", "#23B26B"


@dataclass
class CardData:
    home: str
    away: str
    score: str                      # «5:7»
    note: str = ""                  # «после овертайма» / «по буллитам»
    won: bool | None = None         # победа нашей команды
    when: str = ""                  # «7 ноября 2026 · Саратов»
    player: str = ""                # «Данил Нигматуллин»
    number: int | str = ""
    line: dict | None = None        # строка Данила в протоколе: goals, a, pm, sog
    details: list[str] = field(default_factory=list)      # «Гол — 04:23, 1-й период»
    milestones: list[str] = field(default_factory=list)
    season: str = ""                # «Сезон: 14 игр · 8 голов · 5 передач · 13 очков»
    logos: tuple[bytes | None, bytes | None] = (None, None)


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def _fit(d, text: str, size: int, max_w: int, bold: bool = True):
    """Шрифт, с которым строка помещается в max_w."""
    while size > 18:
        f = _font(size, bold)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(size, bold)


def _signed(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "0")
    return f"+{n}" if n > 0 else str(n).replace("-", "−")


def render(c: CardData) -> bytes:
    from PIL import Image, ImageDraw

    details, marks = c.details[:3], c.milestones[:3]
    # высота зависит от числа строк: обычно 1080×1350 (4:5), с вехами — чуть выше
    body = 922 if c.line else 850
    season_y = body + len(details) * 50 + (12 if details else 0) + len(marks) * 58 + 18
    h = max(H, season_y + 76)
    season_y = max(season_y, h - 96)

    img = Image.new("RGB", (W, h))
    d = ImageDraw.Draw(img)
    for y in range(h):                                   # фон: тёмно-синий градиент
        k = y / (h - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(NAVY_TOP, NAVY_BOTTOM)))
    d.rectangle((0, 0, W, 14), fill=RED)
    d.rectangle((0, h - 14, W, h), fill=RED)

    def center(y: int, text: str, f, fill=WHITE, cx: int = W // 2) -> None:
        d.text((cx - d.textlength(text, font=f) / 2, y), text, font=f, fill=fill)

    center(50, "ИТОГ МАТЧА", _font(44))
    if c.when:
        center(108, c.when, _fit(d, c.when, 32, W - 120, False), GREY)

    # счёт и команды
    for cx, name, logo in ((210, c.home, c.logos[0]), (W - 210, c.away, c.logos[1])):
        if logo:
            try:
                im = Image.open(BytesIO(logo)).convert("RGBA").resize((190, 190), Image.LANCZOS)
                img.paste(im, (cx - 95, 178), im)
            except Exception:
                pass
        center(386, name, _fit(d, name, 42, 380), WHITE, cx)
    hs, as_ = (c.score.split(":") + ["", ""])[:2]
    center(180, f"{hs}:{as_}", _fit(d, f"{hs}:{as_}", 170, 440))
    if c.note:
        center(374, c.note, _font(30, False), GREY)

    if c.won is not None:
        label, color = ("ПОБЕДА", GREEN) if c.won else ("ПОРАЖЕНИЕ", "#5B6B8C")
        d.rounded_rectangle((W // 2 - 190, 462, W // 2 + 190, 536), radius=37, fill=color)
        center(477, label, _font(36))
    d.line([(90, 582), (W - 90, 582)], fill="#3A5490", width=3)

    # Данил
    name = c.player.upper()
    f = _fit(d, f"{name}  #{c.number}", 54, W - 140)
    nw, tw = d.textlength(name + "  ", font=f), d.textlength(f"{name}  #{c.number}", font=f)
    x = (W - tw) / 2
    d.text((x, 614), name, font=f, fill=WHITE)
    d.text((x + nw, 614), f"#{c.number}", font=f, fill=RED)

    if c.line:
        g, a = int(c.line.get("goals") or 0), int(c.line.get("a") or 0)
        tiles = [("ГОЛЫ", str(g), g > 0), ("ПЕРЕДАЧИ", str(a), a > 0),
                 ("+/−", _signed(c.line.get("pm")), False),
                 ("БРОСКИ", str(c.line.get("sog") or "0"), False)]
        tw_, gap, top = 216, 24, 706
        x0 = (W - (4 * tw_ + 3 * gap)) // 2
        for i, (label, value, hot) in enumerate(tiles):
            x = x0 + i * (tw_ + gap)
            d.rounded_rectangle((x, top, x + tw_, top + 176), radius=22, fill=RED if hot else "#223A73")
            center(top + 18, value, _font(88), WHITE, x + tw_ // 2)
            center(top + 126, label, _font(26, False), WHITE if hot else GREY, x + tw_ // 2)
    else:
        center(740, "в этом матче не играл", _font(40, False), GREY)

    y = body
    for text in details:
        center(y, text, _fit(d, text, 36, W - 120, False), WHITE)
        y += 50
    if details:
        y += 12
    for text in marks:
        line = f"★  {text}"
        center(y, line, _fit(d, line, 40, W - 120), GOLD)
        y += 58

    if c.season:
        center(season_y, c.season, _fit(d, c.season, 32, W - 120, False), GREY)

    buf = BytesIO()
    img.save(buf, "JPEG", quality=93)
    return buf.getvalue()
