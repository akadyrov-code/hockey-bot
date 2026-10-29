"""Память бота между запусками: что уже отправлено. Хранится в state.json в репозитории."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class State(dict):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        if path.exists():
            self.update(json.loads(path.read_text(encoding="utf-8") or "{}"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")

    def match(self, match_id: int) -> dict:
        m = self.setdefault("matches", {}).setdefault(str(match_id), {})
        m.setdefault("live_sent", "")
        m.setdefault("goals", [])
        m.setdefault("penalties", [])
        m.setdefault("result_sent", False)
        return m

    def checkpoint(self) -> None:
        """Сохраняет состояние и (в GitHub Actions) сразу пушит его,
        чтобы при сбое посреди матча не было повторных сообщений."""
        self.save()
        if os.environ.get("STATE_PUSH") != "1":
            return
        cmds = [
            ["git", "add", str(self.path)],
            ["git", "commit", "-q", "-m", "bot: state"],
            ["git", "pull", "-q", "--rebase"],
            ["git", "push", "-q"],
        ]
        for cmd in cmds:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0 and cmd[1] == "commit":
                return  # нечего коммитить
            if res.returncode != 0:
                print(f"[state] {' '.join(cmd)}: {res.stderr.strip()}")
                return
