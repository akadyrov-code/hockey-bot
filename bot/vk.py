"""Посты из групп ВКонтакте. Работает, только если задан VK_TOKEN (сервисный ключ)."""
from __future__ import annotations

import requests

API = "https://api.vk.com/method/"
VERSION = "5.199"


class VK:
    def __init__(self, token: str):
        self.token = token

    def wall(self, domain: str, count: int = 15) -> tuple[str, list[dict]]:
        """Возвращает (название группы, посты от старых к новым)."""
        r = requests.get(API + "wall.get", params={
            "domain": domain, "count": count, "extended": 1,
            "access_token": self.token, "v": VERSION,
        }, timeout=25)
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"VK wall.get {domain}: {data['error'].get('error_msg')}")
        resp = data["response"]
        groups = resp.get("groups") or []
        name = groups[0]["name"] if groups else domain
        posts = sorted(resp.get("items", []), key=lambda p: p["id"])
        return name, posts

    @staticmethod
    def post_url(post: dict) -> str:
        return f"https://vk.com/wall{post['owner_id']}_{post['id']}"

    @staticmethod
    def live_video(post: dict) -> str | None:
        """Ссылка на прямой эфир, если пост содержит live-видео."""
        for att in post.get("attachments", []) + sum(
                (c.get("attachments", []) for c in post.get("copy_history", [])), []):
            v = att.get("video") if att.get("type") == "video" else None
            if v and (v.get("live") or v.get("live_status") in ("started", "upcoming")):
                return f"https://vkvideo.ru/video{v['owner_id']}_{v['id']}"
        return None
