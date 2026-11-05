"""Дополнения от Claude: викторины и посты.

Их готовит задача по расписанию в приложении Claude на Mac (на подписке) и кладёт
в content/queue.json. Бот ничего не генерирует — только публикует в назначенное время.
Если очередь пустая или задача не сработала, бот работает как обычно.

Проверка очереди перед отправкой в репозиторий:
    python -m bot.extras --validate
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

QUEUE = Path(__file__).resolve().parent.parent / "content" / "queue.json"


def load_queue() -> list[dict]:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        # На GitHub берём свежую версию: задача на Mac могла дописать очередь посреди смены.
        try:
            r = requests.get(f"https://raw.githubusercontent.com/{repo}/main/content/queue.json",
                             timeout=20, headers={"Cache-Control": "no-cache"})
            if r.status_code == 200:
                return r.json().get("items", [])
        except (requests.RequestException, ValueError) as e:
            print(f"[extras] очередь с GitHub не загрузилась: {e}")
    if QUEUE.exists():
        return json.loads(QUEUE.read_text(encoding="utf-8") or "{}").get("items", [])
    return []


def validate(item: dict) -> str | None:
    """Ошибка в элементе очереди или None. Лимиты — как у Telegram."""
    if not str(item.get("id", "")).strip():
        return "нет id"
    try:
        at = datetime.fromisoformat(item.get("publish_at", ""))
    except (TypeError, ValueError):
        return "publish_at не в формате 2026-11-06T11:00:00+03:00"
    if at.tzinfo is None:
        return "в publish_at нет часового пояса (+03:00)"
    kind = item.get("type")
    if kind == "text":
        text = item.get("text", "")
        if not text.strip():
            return "пустой text"
        if len(text) + len(item.get("title", "")) > 3500:
            return "text длиннее 3500 символов"
    elif kind == "quiz":
        q, opts, ok = item.get("question", ""), item.get("options"), item.get("correct")
        if not q.strip() or len(q) > 300:
            return "question пустой или длиннее 300 символов"
        if not isinstance(opts, list) or not 2 <= len(opts) <= 10:
            return "options — список из 2–10 вариантов"
        if any(not isinstance(o, str) or not o.strip() or len(o) > 100 for o in opts):
            return "каждый вариант — непустая строка до 100 символов"
        if len(set(opts)) != len(opts):
            return "варианты повторяются"
        if not isinstance(ok, int) or not 0 <= ok < len(opts):
            return "correct — номер правильного варианта с нуля"
        if len(item.get("explanation", "")) > 200:
            return "explanation длиннее 200 символов"
    else:
        return "type должен быть text или quiz"
    return None


def main() -> None:
    if "--validate" not in sys.argv:
        sys.exit("Использование: python -m bot.extras --validate")
    data = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {"items": []}
    items = data.get("items", [])
    errors = [f"{it.get('id', '?')}: {err}" for it in items if (err := validate(it))]
    ids = [it.get("id") for it in items]
    errors += [f"{i}: id повторяется" for i in {i for i in ids if ids.count(i) > 1}]
    if errors:
        print("Ошибки в content/queue.json:\n" + "\n".join(errors))
        sys.exit(1)
    print(f"OK: {len(items)} элементов в очереди")


if __name__ == "__main__":
    main()
