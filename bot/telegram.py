"""Отправка сообщений в Telegram-канал через Bot API."""
from __future__ import annotations

import json
import time

import requests


class Telegram:
    def __init__(self, token: str, chat_id: str | int | None = None, dry_run: bool = False):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.api = f"https://api.telegram.org/bot{token}/"

    def _call(self, method: str, **params) -> dict:
        for attempt in range(5):
            try:
                r = requests.post(self.api + method, json=params, timeout=30)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                print(f"[tg] {method}: {e}")
                time.sleep(5 * (attempt + 1))
                continue
            if data.get("ok"):
                return data
            retry = (data.get("parameters") or {}).get("retry_after")
            if retry:
                time.sleep(int(retry) + 1)
                continue
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        raise RuntimeError(f"Telegram {method}: нет ответа")

    def send(self, text: str, preview: bool = False) -> None:
        if self.dry_run:
            print("\n----- [сообщение в канал] -----\n" + text + "\n-------------------------------")
            return
        self._call(
            "sendMessage",
            chat_id=self.chat_id,
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": not preview},
        )

    def send_photos(self, photos: list[bytes], caption: str) -> None:
        """Альбом до 10 фото, подпись — у первого."""
        for i in range(0, len(photos), 10):
            chunk = photos[i:i + 10]
            if self.dry_run:
                print(f"\n----- [альбом: {len(chunk)} фото] -----\n{caption}\n-----")
                continue
            cap = {"caption": caption, "parse_mode": "HTML"} if i == 0 else {}
            if len(chunk) == 1:                 # альбом в Telegram — минимум 2 фото
                method, files = "sendPhoto", {"photo": ("p.jpg", chunk[0], "image/jpeg")}
                data = {"chat_id": self.chat_id, **cap}
            else:
                media = [{"type": "photo", "media": f"attach://p{j}"} for j in range(len(chunk))]
                media[0].update(cap)
                method = "sendMediaGroup"
                files = {f"p{j}": (f"p{j}.jpg", b, "image/jpeg") for j, b in enumerate(chunk)}
                data = {"chat_id": self.chat_id, "media": json.dumps(media)}
            for attempt in range(3):
                r = requests.post(self.api + method, timeout=120, files=files, data=data)
                data = r.json()
                if data.get("ok"):
                    break
                retry = (data.get("parameters") or {}).get("retry_after")
                if not retry:
                    raise RuntimeError(f"Telegram {method}: {data.get('description')}")
                time.sleep(int(retry) + 1)

    def discover_channel(self) -> tuple[int, str] | None:
        """Ищет канал, куда добавили бота (по последним обновлениям за 24 часа)."""
        data = self._call("getUpdates", allowed_updates=["channel_post", "my_chat_member"])
        found = None
        for upd in data.get("result", []):
            for key in ("channel_post", "my_chat_member", "edited_channel_post"):
                chat = (upd.get(key) or {}).get("chat")
                if chat and chat.get("type") == "channel":
                    found = (chat["id"], chat.get("title", ""))
        return found
