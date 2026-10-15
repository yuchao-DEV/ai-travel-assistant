"""从 LLM 回复里稳健地抽取 JSON 对象。

``services/polish.py``（文案润色）和 ``services/nl_parse.py``（自然语言解析）
都需要这一步，抽成公共模块避免两份正则实现走偏。
"""
import json
import re
from typing import Any, Dict, Optional


def extract_json(response: str) -> Optional[Dict[str, Any]]:
    """从 LLM 回复中提取 JSON 对象。

    依次处理：markdown 代码块包裹 → 前后夹杂说明文字 → json 解析失败。
    非对象（数组、字符串等）一律返回 None，调用方无需再做类型判断。
    """
    if not response:
        return None

    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"):
        match = re.search(pattern, response)
        if match:
            response = match.group(1)
            break

    start = response.find("{")
    end = response.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(response[start:end + 1])
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None
