"""文件缓存层：POI 搜索结果和交通矩阵按城市缓存，带 TTL。"""
import json
import time
from pathlib import Path
from typing import Any, Optional

from app.config import CACHE_DIR


def _ensure_cache_dir(city: str) -> Path:
    """确保城市缓存目录存在。"""
    d = CACHE_DIR / city
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cache(city: str, key: str, ttl: int) -> Optional[Any]:
    """读取缓存，过期返回 None。"""
    cache_dir = _ensure_cache_dir(city)
    fpath = cache_dir / f"{key}.json"
    if not fpath.exists():
        return None
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) > ttl:
            return None
        return data.get("payload")
    except Exception:
        return None


def set_cache(city: str, key: str, payload: Any) -> None:
    """写入缓存，附加时间戳。"""
    cache_dir = _ensure_cache_dir(city)
    fpath = cache_dir / f"{key}.json"
    data = {"_ts": time.time(), "payload": payload}
    fpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
