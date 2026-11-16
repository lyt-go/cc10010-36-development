"""通知投递事件的跨模块共享工具。

单独成模块是为了让 seed（示例数据）与 service（业务规则）复用同一套稳定幂等键
算法，同时避免 seed → service → store → seed 的循环导入。
"""
from __future__ import annotations

import hashlib


def idempotency_key(sla_rule_id: int, channel_id: int) -> str:
    """稳定幂等键：同一 SLA 升级事件 + 同一渠道，无论提交几次都得到同一个键。"""
    raw = f"sla:{int(sla_rule_id)}|channel:{int(channel_id)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"IDEM-{digest.upper()}"
