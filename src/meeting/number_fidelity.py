"""译文数字保真校验（2026-09-17）。

**为什么需要**：翻译 prompt 是软约束。实测（temperature=0.0）模型会把
「N million」（N × 10^6）译成「N × 10 万」（只有 N × 10^5）——**少一个数量级**，
而同一份译文里其它换算都对。财务数字错一个数量级是**实质性错误**，不能只靠
prompt 兜。所以这里做代码层校验：翻译完成后抽取两边的"数值量"比对，不一致就
重试一次（并把差异指给模型）。

**注意**：本模块与调用方都**不写入任何真实业务数据**（按 AGENTS.md 的 prompt 规范，
示例一律用通用占位数字）。诊断时用到的真实案例只留在开发记录里。

设计取舍（避免误报）：
- 归一化成**绝对值**再比（`5 million` = `500万` = 5_000_000）；
- 只关心 ≥ 1000 的整数（`36%`、`1.5`、版本号之类不参与，减少噪声）；
- 比**多重集**（顺序无关）；
- 支持中英单位：thousand/million/billion/K/M/B、万/亿/千万/百万。
"""

from __future__ import annotations

import re
from collections import Counter

_UNITS: dict[str, int] = {
    "thousand": 1_000,
    "k": 1_000,
    "万": 10_000,
    "百万": 1_000_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "千万": 10_000_000,
    "亿": 100_000_000,
    "b": 1_000_000_000,
    "billion": 1_000_000_000,
}

# 前后断言只排除 **ASCII** 字母数字（不能用 \w：它匹配中日韩字符，
# 会把「约为500万」里的数字整体挡掉）。
_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(thousand|million|billion|千万|百万|万|亿|[KkMmBb])?"
    r"(?![A-Za-z0-9_])"
)

#: 只把 ≥ 此值的整数当作"需要保真的数量"（滤掉百分比/小数比率等噪声）。
_MIN_SIGNIFICANT = 1000


def extract_quantities(text: str) -> list[int]:
    """抽出文本中所有显著"数值量"（换算成绝对值，升序）。"""
    out: list[int] = []
    for m in _QUANTITY_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        unit = (m.group(2) or "").strip()
        if unit:
            val *= _UNITS.get(unit.lower(), 1)
        if val >= _MIN_SIGNIFICANT and abs(val - round(val)) < 1e-6:
            out.append(int(round(val)))
    return sorted(out)


def missing_quantities(source: str, translation: str) -> list[int]:
    """原文有、译文缺的数量（多重集差）。空 = 通过。"""
    src = Counter(extract_quantities(source))
    dst = Counter(extract_quantities(translation))
    return sorted((src - dst).elements())


def format_hint(missing: list[int]) -> str:
    """给模型的可读提示（用于重试）。"""
    parts = []
    for v in missing[:8]:
        if v >= 100_000_000 and v % 100_000_000 == 0:
            parts.append(f"{v // 100_000_000}亿")
        elif v >= 10_000 and v % 10_000 == 0:
            parts.append(f"{v // 10_000}万")
        else:
            parts.append(f"{v:,}")
    return ", ".join(parts)
