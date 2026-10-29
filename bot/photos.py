"""Фото с матчей из альбомов ВК: оставляем только те, где виден игрок с номером 90.

Номер на свитере ищет модель Claude (лица в шлемах почти не видно).
Нужны VK_TOKEN и ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import base64
import json
import time

import anthropic
import requests

from .vk import API, VERSION

SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Номера фото, на которых виден нужный игрок",
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}


def _vk(token: str, method: str, **params) -> dict:
    params.update(access_token=token, v=VERSION)
    for attempt in range(3):
        data = requests.get(API + method, params=params, timeout=25).json()
        err = data.get("error")
        if not err:
            return data["response"]
        if err.get("error_code") == 6:          # слишком много запросов в секунду
            time.sleep(1 + attempt)
            continue
        raise RuntimeError(f"VK {method}: {err.get('error_msg')}")
    raise RuntimeError(f"VK {method}: лимит запросов")


def best_size(photo: dict, max_side: int = 1280) -> str:
    sizes = [s for s in photo.get("sizes", []) if s.get("url")]
    fit = [s for s in sizes if max(s.get("width", 0), s.get("height", 0)) <= max_side] or sizes
    return max(fit, key=lambda s: s.get("width", 0) * s.get("height", 0))["url"]


class PhotoFinder:
    def __init__(self, vk_token: str, cfg: dict):
        self.token = vk_token
        self.cfg = cfg
        self.pcfg = cfg.get("photos", {})
        self.client = anthropic.Anthropic()     # ключ из ANTHROPIC_API_KEY

    # --- ВК

    def group_id(self, domain: str) -> int:
        resp = _vk(self.token, "groups.getById", group_id=domain)
        groups = resp.get("groups", resp) if isinstance(resp, dict) else resp
        return int(groups[0]["id"])

    def albums(self, gid: int) -> list[dict]:
        return _vk(self.token, "photos.getAlbums", owner_id=-gid, count=20).get("items", [])

    def album_photos(self, gid: int, album_id: int, count: int = 200) -> list[dict]:
        return _vk(self.token, "photos.get", owner_id=-gid, album_id=album_id,
                   rev=1, count=count).get("items", [])

    # --- распознавание

    def prompt(self) -> str:
        pl = self.cfg["player"]
        hint = self.pcfg.get("jersey_hint", "")
        return (
            f"Это фотографии с хоккейного матча команды «{self.cfg['team']['name']}». "
            f"Найди фото, где хорошо виден игрок «{self.cfg['team']['name']}» с номером "
            f"{pl['number']} на свитере, шлеме или штанах ({pl['first_name']} {pl['last_name']}). "
            + (f"{hint} " if hint else "")
            + "Если номер виден только частично, но по контексту это точно он — тоже включай. "
              "Если номер не различить — не включай. Игрок соперника с тем же номером не подходит. "
              "Верни номера подходящих фото."
        )

    def classify(self, images: list[bytes]) -> list[int]:
        """Возвращает индексы (с 0) фото, где есть игрок."""
        content = []
        for i, img in enumerate(images, 1):
            content.append({"type": "text", "text": f"Фото {i}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(img).decode()}})
        content.append({"type": "text", "text": self.prompt()})
        try:
            resp = self.client.beta.messages.create(
                model=self.pcfg.get("model", "claude-opus-5-5"),
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.RateLimitError:
            time.sleep(30)
            return self.classify(images)
        except anthropic.APIStatusError as e:
            print(f"[photos] Claude API: {e.status_code} {e.message}")
            return []
        except anthropic.APIConnectionError as e:
            print(f"[photos] Claude API недоступен: {e}")
            return []
        if resp.stop_reason == "refusal":
            return []
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        nums = json.loads(text).get("matches", [])
        return sorted({n - 1 for n in nums if 1 <= n <= len(images)})

    # --- основной шаг

    def new_photos(self, state: dict, silent: bool = False) -> list[dict]:
        """Ищет новые фото и возвращает подходящие: [{'bytes', 'album', 'album_url'}]."""
        seen = state.setdefault("photos_seen", {})       # album_id -> макс. id фото
        max_check = self.pcfg.get("max_per_run", 150)
        batch = self.pcfg.get("batch", 10)
        fresh_days = self.pcfg.get("fresh_days", 4)
        found, checked = [], 0
        for domain in self.cfg.get("vk", {}).get("groups", []):
            gid = self.group_id(domain)
            for album in self.albums(gid):
                aid = str(album["id"])
                key = f"{gid}_{aid}"
                last = seen.get(key)
                stale = album.get("updated", 0) < time.time() - fresh_days * 86400
                if silent or (last is None and stale):
                    # первый запуск или старый альбом: запоминаем, но не разбираем
                    top = self.album_photos(gid, album["id"], count=1)
                    seen[key] = max([p["id"] for p in top] + [last or 0])
                    continue
                if stale:
                    continue                                 # альбом давно не обновлялся
                photos = [p for p in self.album_photos(gid, album["id"])
                          if p["id"] > (last or 0) and p.get("date", 0) > time.time() - fresh_days * 86400]
                if not photos:
                    continue
                photos = sorted(photos, key=lambda p: p["id"])[: max(0, max_check - checked)]
                for i in range(0, len(photos), batch):
                    chunk = photos[i:i + batch]
                    imgs = []
                    for p in chunk:
                        try:
                            imgs.append(requests.get(best_size(p), timeout=30).content)
                        except requests.RequestException:
                            imgs.append(b"")
                    ok = [(p, b) for p, b in zip(chunk, imgs) if b]
                    hits = self.classify([b for _, b in ok]) if ok else []
                    for j in hits:
                        found.append({"bytes": ok[j][1], "album": album.get("title", ""),
                                      "album_url": f"https://vk.com/album-{gid}_{aid}"})
                    checked += len(chunk)
                    seen[key] = max(seen.get(key) or 0, max(p["id"] for p in chunk))
                if checked >= max_check:
                    return found
        return found
