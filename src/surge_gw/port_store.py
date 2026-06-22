from __future__ import annotations

import json
import os
import tempfile


def load(path: str) -> dict[str, int]:
    """读 port-map.json;不存在返回空映射(首次运行)。"""
    try:
        with open(path, encoding="utf-8") as f:
            return {k: int(v) for k, v in json.load(f).items()}
    except FileNotFoundError:
        return {}


def save(path: str, mapping: dict[str, int]) -> None:
    """原子写:同名节点跨刷新保端口依赖这份持久化,写一半会让端口漂移。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mapping, f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
