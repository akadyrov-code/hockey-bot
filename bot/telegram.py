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

    def send(self, text: str, preview: bool = False, silent: bool = False) -> None:
        if self.dry_run:
            tag = " (без звука)" if silent else ""
            print(f"\n----- [сообщение в канал{tag}] -----\n" + text + "\n-------------------------------")
            return
        self._call(
            "sendMessage",
            chat_id=self.chat_id,
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": not preview},
            disable_notification=silent,
        )

    def send_quiz(self, question: str, options: list[str], correct: int, explanation: str = "") -> None:
        """Викторина: опрос с правильным ответом (в канале — только анонимный)."""
        if self.dry_run:
            opts = "\n".join(f"  {'✅' if i == correct else '▫️'} {o}" for i, o in enumerate(options))
            print(f"\n----- [викторина] -----\n{question}\n{opts}\n  ℹ️ {explanation}\n-----")
            return
        params = dict(chat_id=self.chat_id, question=question, type="quiz", is_anonymous=True,
                      options=[{"text": o} for o in options], correct_option_id=correct)
        if explanation:
            params["explanation"] = explanation
        self._call("sendPoll", **params)

    def send_poll(self, question: str, options: list[str]) -> None:
        """Обычный опрос (без правильного ответа), анонимный — в каналах иначе нельзя."""
        if self.dry_run:
            print(f"\n----- [опрос] -----\n{question}\n" + "\n".join(f"  ▫️ {o}" for o in options) + "\n-----")
            return
        self._call("sendPoll", chat_id=self.chat_id, question=question, is_anonymous=True,
                   options=[{"text": o} for o in options])

    def send_video(self, video: bytes, caption: str, silent: bool = False) -> None:
        """Вертикальный ролик (шортс) до 50 МБ."""
        if self.dry_run:
            print(f"\n----- [видео: {len(video) / 1e6:.1f} МБ] -----\n{caption}\n-----")
            return
        data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML",
                "supports_streaming": "true", "width": 1080, "height": 1920,
                "disable_notification": "true" if silent else "false"}
        for attempt in range(3):
            r = requests.post(self.api + "sendVideo", timeout=300, data=data,
                              files={"video": ("goal.mp4", video, "video/mp4")})
            resp = r.json()
            if resp.get("ok"):
                return
            retry = (resp.get("parameters") or {}).get("retry_after")
            if not retry:
                raise RuntimeError(f"Telegram sendVideo: {resp.get('description')}")
            time.sleep(int(retry) + 1)
        raise RuntimeError("Telegram sendVideo: нет ответа")

    def send_photos(self, photos: list[bytes], caption: str, silent: bool = False) -> None:
        """Альбом до 10 фото, подпись — у первого."""
        for i in range(0, len(photos), 10):
            chunk = photos[i:i + 10]
            if self.dry_run:
                print(f"\n----- [альбом: {len(chunk)} фото] -----\n{caption}\n-----")
                continue
            cap = {"caption": caption, "parse_mode": "HTML"} if i == 0 else {}
            quiet = {"disable_notification": "true" if silent else "false"}
            if len(chunk) == 1:                 # альбом в Telegram — минимум 2 фото
                method, files = "sendPhoto", {"photo": ("p.jpg", chunk[0], "image/jpeg")}
                data = {"chat_id": self.chat_id, **cap, **quiet}
            else:
                media = [{"type": "photo", "media": f"attach://p{j}"} for j in range(len(chunk))]
                media[0].update(cap)
                method = "sendMediaGroup"
                files = {f"p{j}": (f"p{j}.jpg", b, "image/jpeg") for j, b in enumerate(chunk)}
                data = {"chat_id": self.chat_id, "media": json.dumps(media), **quiet}
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
