from __future__ import annotations

import math
import re
from typing import Any


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def is_blank(value: Any) -> bool:
    return clean(value) == ""


def truthy_text(value: Any) -> str:
    text = clean(value)
    return text if text else ""
