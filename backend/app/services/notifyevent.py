"""通知投递事件业务规则。

把 SLA 升级事件与通知渠道投递数据收敛成可核对的「通知投递事件」，并提供：

- 稳定幂等键：同一 SLA 升级 + 同一渠道只会产生一条投递事件；
- 投递尝试与确认回执：事件与回执在同一把写锁内原子落库，失败可从最后尝试继续；
- 多渠道路由与回执冲突的确定性取舍：同值班组内最早送达的渠道事件为采纳记录，
  其余成功送达保留但标记为冗余，重复通知次数因此可核对、不随回执到达顺序变化；
- 通知投递事件数据快照：不可变深拷贝，SLA 监控只依据快照确认升级，跨模块确认
  结果通过消息编号关联到同一次送达。

当前后端是内存仓库，这里用模块级 RLock 模拟数据库行锁/唯一约束；换成真实数据库时，
幂等键唯一索引、事务与行锁一一对应，业务口径不变。
"""
from __future__ import annotations

import copy
import hashlib
import threading
from datetime import datetime
from typing import Any

from app.services.notifyevent_keys import idempotency_key
from app.store import store

MODULE = "notifyevent"
BATCH_TABLE = "notifybatch"
RECEIPT_TABLE = "notifyreceipt"
SNAPSHOT_TABLE = "notifyeventsnapshot"

# 事件状态
STATUS_PENDING = "待投递"
STATUS_SUPPRESSED = "静默抑制"
STATUS_FAILED = "投递失败"
STATUS_DELIVERED = "已送达"
STATUS_CONFIRMED = "已确认送达"
STATUSES = [STATUS_PENDING, STATUS_SUPPRESSED, STATUS_FAILED, STATUS_DELIVERED, STATUS_CONFIRMED]

# 送达结果
RESULT_PENDING = "等待送达"
RESULT_SUPPRESSED = "静默时段抑制"
RESULT_FAILED = "送达失败"
RESULT_DELIVERED = "送达成功"

# 回执状态
RECEIPT_DELIVERED = "送达确认"
RECEIPT_FAILURE = "失败回执"
RECEIPT_DUPLICATE = "重复回执"
RECEIPT_CONFLICT = "冲突回执"

MAX_ATTEMPTS = 3

# 所有写操作共用一把锁：保证「事件 + 回执」原子落库、并发同消息只允许一次成功。
_write_lock = threading.RLock()


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _parse_hour(value: Any) -> float | None:
    """把静默时段解析成 0~24 的小时数；支持纯小时数与 HH:MM 两种写法。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if ":" in text:
            hour_text, minute_text = text.split(":", 1)
            hour = int(hour_text) + int(minute_text) / 60.0
        else:
            hour = float(text)
    except ValueError:
        return None
    if hour < 0 or hour > 24:
        return None
    return hour


def _in_quiet_window(channel: dict[str, Any], at: datetime | None = None) -> tuple[bool, str]:
    """按渠道既有配置判断当前是否落在静默时段，渠道配置本身不做任何修改。"""
    at = at or datetime.now()
    start = _parse_hour(channel.get("静默时段开始"))
    end = _parse_hour(channel.get("静默时段结束"))
    if start is None or end is None:
        return False, "未配置静默时段"
    current = at.hour + at.minute / 60.0
    if start <= end:
        inside = start <= current < end
    else:  # 跨零点，例如 22:00 ~ 07:00
        inside = current >= start or current < end
    if inside:
        return True, f"当前 {at.strftime('%H:%M')} 落在静默时段 {channel.get('静默时段开始')}~{channel.get('静默时段结束')}"
    return False, "不在静默时段"


class NotifyeventService:
    # ------------------------------------------------------------------ 查询
    def list_events(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        group: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("事件编号", ""))
                    or keyword in str(row.get("幂等键", ""))
                    or keyword in str(row.get("消息编号", ""))]
        if status:
            rows = [row for row in rows if row.get("事件状态") == status]
        if group:
            rows = [row for row in rows if group in str(row.get("值班组", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, event_id)

    def list_batches(self) -> list[dict[str, Any]]:
        return store.rows(BATCH_TABLE)

    def list_receipts(self, event_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RECEIPT_TABLE)
        if event_id is not None:
            rows = [row for row in rows if int(row.get("事件ID", 0)) == event_id]
        return rows

    # ------------------------------------------------------------- 批量建事件
    def create_batch(
        self,
        sla_rule_ids: list[int],
        channel_ids: list[int] | None = None,
        *,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """多条升级记录多选一次提交为投递批次，并按渠道展开批量生成投递事件。

        以 (SLA规则, 渠道) 派生稳定幂等键：与已存在的 SLA 升级事件、渠道投递数据
        去重，重复提交只跳过、不新增投递。渠道未指定时沿用该 SLA 升级通知组在渠道
        表中的既有配置（保持旧有渠道配置不变）。
        """
        if not sla_rule_ids:
            return None, "请至少选择一条SLA升级记录"
        # 入参去重，保持顺序，避免同一请求内部重复
        unique_sla: list[int] = []
        for raw in sla_rule_ids:
            try:
                rule_id = int(raw)
            except (TypeError, ValueError):
                return None, f"SLA规则编号非法：{raw!r}"
            if rule_id not in unique_sla:
                unique_sla.append(rule_id)

        with _write_lock:
            batch_id = _next_id(BATCH_TABLE)
            batch_no = f"BATCH-{batch_id:04d}"
            created_events: list[dict[str, Any]] = []
            skipped: list[dict[str, str]] = []
            suppressed: list[dict[str, str]] = []

            for rule_id in unique_sla:
                sla = store.find("slavalidate", rule_id)
                if sla is None:
                    skipped.append({"rule_id": str(rule_id), "reason": "SLA升级记录不存在"})
                    continue
                channels = self._resolve_channels(sla, channel_ids)
                if not channels:
                    skipped.append({"rule_id": str(rule_id), "reason": "升级通知组未匹配到可用通知渠道"})
                    continue
                for channel in channels:
                    key = idempotency_key(rule_id, int(channel["id"]))
                    existed = self._find_by_key(key)
                    if existed is not None:
                        # 与既有投递事件去重：不新增投递，只挂到本批次便于追踪
                        existed.setdefault("批次编号列表", [])
                        if batch_no not in existed["批次编号列表"]:
                            existed["批次编号列表"].append(batch_no)
                        skipped.append({
                            "rule_id": str(rule_id),
                            "channel_id": str(channel["id"]),
                            "reason": f"幂等键 {key} 已存在，复用事件 {existed['事件编号']}",
                        })
                        continue
                    event = self._build_event(batch_no, sla, channel, key, now=now)
                    store.rows(MODULE).append(event)
                    created_events.append(event)
                    if event["事件状态"] == STATUS_SUPPRESSED:
                        suppressed.append({
                            "event_id": str(event["id"]),
                            "reason": event["静默时段结果"],
                        })

            batch = {
                "id": batch_id,
                "批次编号": batch_no,
                "提交时间": _now_ts(),
                "SLA规则ID列表": unique_sla,
                "渠道ID列表": sorted({int(row["渠道ID"]) for row in created_events}) or sorted(channel_ids or []),
                "生成事件数": len(created_events),
                "去重跳过数": len(skipped),
                "静默抑制数": len(suppressed),
                "状态": "已生成",
                "事件ID列表": [int(row["id"]) for row in created_events],
            }
            store.rows(BATCH_TABLE).append(batch)
            message = (
                f"批次 {batch_no} 已提交：生成 {len(created_events)} 条投递事件，"
                f"去重跳过 {len(skipped)} 条，静默抑制 {len(suppressed)} 条"
            )
            return batch, message

    def _resolve_channels(
        self, sla: dict[str, Any], channel_ids: list[int] | None
    ) -> list[dict[str, Any]]:
        """解析一次升级要走的渠道；显式指定时按指定列表，否则沿用通知组既有配置。"""
        channels = store.rows("notifychan")
        if channel_ids:
            wanted = {int(cid) for cid in channel_ids}
            picked = [row for row in channels if int(row["id"]) in wanted]
            return sorted(picked, key=lambda row: int(row["id"]))
        group = str(sla.get("升级通知组", "")).strip()
        if not group:
            return []
        # 旧有渠道配置口径：通知组文本命中渠道类型/接收地址/渠道编号即视为该组渠道
        picked = [
            row for row in channels
            if group in str(row.get("渠道类型", ""))
            or group in str(row.get("接收地址", ""))
            or group in str(row.get("渠道编号", ""))
        ]
        return sorted(picked, key=lambda row: int(row["id"]))

    def _build_event(
        self,
        batch_no: str,
        sla: dict[str, Any],
        channel: dict[str, Any],
        key: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        at = now or datetime.now()
        event_id = _next_id(MODULE)
        inside, quiet_note = _in_quiet_window(channel, at)
        event: dict[str, Any] = {
            "id": event_id,
            "事件编号": f"NEVT-{event_id:04d}",
            "幂等键": key,
            "批次编号": batch_no,
            "批次编号列表": [batch_no],
            "SLA规则ID": int(sla["id"]),
            "规则编号": sla.get("规则编号", ""),
            "适用作业": sla.get("适用作业", ""),
            "值班组": sla.get("升级通知组", ""),
            "渠道ID": int(channel["id"]),
            "渠道编号": channel.get("渠道编号", ""),
            "渠道类型": channel.get("渠道类型", ""),
            "接收地址": channel.get("接收地址", ""),
            "消息编号": f"MSG-{int(sla['id']):04d}-{int(channel['id']):04d}",
            "消息模板": channel.get("消息模板", ""),
            "事件状态": STATUS_PENDING,
            "送达结果": RESULT_PENDING,
            "投递尝试次数": 0,
            "最大尝试次数": MAX_ATTEMPTS,
            "最近尝试时间": None,
            "回执编号": None,
            "回执状态": None,
            "采纳记录": False,
            "冗余送达": False,
            "静默时段结果": quiet_note,
            "快照版本": None,
            "创建时间": at.strftime("%Y-%m-%d %H:%M:%S"),
            "更新时间": at.strftime("%Y-%m-%d %H:%M:%S"),
            "pending": True,
            "abnormal": False,
        }
        if inside:
            # 静默时段不发起投递，直接形成可核对的静默抑制结果
            event["事件状态"] = STATUS_SUPPRESSED
            event["送达结果"] = RESULT_SUPPRESSED
            event["pending"] = False
        return event

    def _find_by_key(self, key: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("幂等键") == key:
                return row
        return None

    # ------------------------------------------------------------- 投递与重试
    def send_event(
        self,
        event_id: int,
        *,
        simulate_failure: bool = False,
        at: datetime | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """按幂等键投递一条事件。

        - 并发发送同一消息只允许一次成功形成送达结果：已送达/已确认的事件重复发送
          直接返回既有结果，不新增尝试、不新增回执；
        - 失败后从最后尝试继续：attempt 次数累加，达到上限前可重试；
        - 事件与确认回执在同一把写锁内原子落库。
        """
        at = at or datetime.now()
        with _write_lock:
            event = store.find(MODULE, event_id)
            if event is None:
                return None, f"投递事件 {event_id} 不存在"
            if event["事件状态"] in (STATUS_DELIVERED, STATUS_CONFIRMED):
                return event, f"消息 {event['消息编号']} 已形成送达结果，重复发送被幂等拦截"
            if event["事件状态"] == STATUS_SUPPRESSED:
                return event, "事件处于静默抑制状态，不发起投递"
            if event["投递尝试次数"] >= event["最大尝试次数"]:
                return event, "已达最大投递尝试次数，请先排查渠道后再重置"

            event["投递尝试次数"] = int(event["投递尝试次数"]) + 1
            event["最近尝试时间"] = at.strftime("%Y-%m-%d %H:%M:%S")
            event["更新时间"] = event["最近尝试时间"]

            if simulate_failure:
                event["事件状态"] = STATUS_FAILED
                event["送达结果"] = RESULT_FAILED
                event["abnormal"] = True
                receipt = self._append_receipt(
                    event, RECEIPT_FAILURE, "渠道模拟返回失败", at=at
                )
                event["回执编号"] = receipt["回执编号"]
                event["回执状态"] = receipt["回执状态"]
                if event["投递尝试次数"] >= event["最大尝试次数"]:
                    event["pending"] = True
                    return event, (
                        f"第 {event['投递尝试次数']} 次投递失败并已记录失败回执，"
                        "达到最大尝试次数，可在修复后从最后尝试继续重试"
                    )
                return event, (
                    f"第 {event['投递尝试次数']} 次投递失败并已原子记录失败回执，"
                    "可从最后尝试继续"
                )

            # 成功路径：送达结果与确认回执一次原子落库
            event["事件状态"] = STATUS_DELIVERED
            event["送达结果"] = RESULT_DELIVERED
            event["pending"] = False
            event["abnormal"] = False
            self._apply_adoption(event)
            receipt = self._append_receipt(
                event, RECEIPT_DELIVERED, "渠道返回送达确认", at=at
            )
            event["回执编号"] = receipt["回执编号"]
            event["回执状态"] = receipt["回执状态"]
            return event, (
                f"消息 {event['消息编号']} 第 {event['投递尝试次数']} 次投递成功，"
                f"送达结果与回执 {receipt['回执编号']} 已原子落库"
                + ("（采纳记录）" if event["采纳记录"] else "（冗余送达）")
            )

    def reset_failed(self, event_id: int) -> tuple[dict[str, Any] | None, str]:
        """排障后把失败事件重置为待投递，允许从最后尝试继续。"""
        with _write_lock:
            event = store.find(MODULE, event_id)
            if event is None:
                return None, f"投递事件 {event_id} 不存在"
            if event["事件状态"] != STATUS_FAILED:
                return event, "仅投递失败的事件允许重置后重试"
            event["事件状态"] = STATUS_PENDING
            event["送达结果"] = RESULT_PENDING
            event["abnormal"] = False
            event["更新时间"] = _now_ts()
            return event, "事件已重置为待投递，将保留已记录尝试次数并从最后尝试继续"

    def _apply_adoption(self, event: dict[str, Any]) -> None:
        """多渠道路由取舍：同一条 SLA 升级在值班组内最早送达的事件为采纳记录。

        采纳按「SLA 升级 × 值班组」划分：不同 SLA 是不同的升级消息，各自有一条采纳
        渠道；同一升级的其余成功送达为冗余。取舍只依赖同组同 SLA 已送达事件的创建
        时间与事件ID，不依赖回执到达先后，因此较晚到达的回执不会改变采纳结果与
        重复通知次数。
        """
        group = str(event.get("值班组", ""))
        sla_id = int(event.get("SLA规则ID", 0))
        success = [
            row for row in store.rows(MODULE)
            if str(row.get("值班组", "")) == group
            and int(row.get("SLA规则ID", 0)) == sla_id
            and row["事件状态"] in (STATUS_DELIVERED, STATUS_CONFIRMED)
        ]
        success.sort(key=lambda row: (str(row.get("创建时间", "")), int(row["id"])))
        primary_id = int(success[0]["id"]) if success else int(event["id"])
        for row in success:
            row["采纳记录"] = int(row["id"]) == primary_id
            row["冗余送达"] = int(row["id"]) != primary_id

    # ------------------------------------------------------------- 确认回执
    def register_receipt(
        self,
        event_id: int,
        receipt_status: str,
        *,
        detail: str = "",
        at: datetime | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """渠道回执回调登记。

        - 重复回调不新增投递：同事件同状态回执已存在时只追加一条标记为「重复回执」
          的审计记录，事件状态与采纳结论保持不变；
        - 与已成功投递冲突的迟到失败回执标记为「冲突回执」，不覆盖送达结果；
        - 回执与事件状态更新在同一把写锁内原子完成。
        """
        at = at or datetime.now()
        if receipt_status not in (RECEIPT_DELIVERED, RECEIPT_FAILURE):
            return None, f"回执状态仅支持 {RECEIPT_DELIVERED} / {RECEIPT_FAILURE}"
        with _write_lock:
            event = store.find(MODULE, event_id)
            if event is None:
                return None, f"投递事件 {event_id} 不存在"

            existing = [
                row for row in store.rows(RECEIPT_TABLE)
                if int(row.get("事件ID", 0)) == int(event_id)
            ]
            effective_status = receipt_status
            if any(row["回执状态"] == receipt_status and row["回执类型"] in (RECEIPT_DELIVERED, RECEIPT_FAILURE)
                   for row in existing):
                effective_status = RECEIPT_DUPLICATE
            elif (
                receipt_status == RECEIPT_FAILURE
                and event["事件状态"] in (STATUS_DELIVERED, STATUS_CONFIRMED)
            ):
                effective_status = RECEIPT_CONFLICT

            receipt = self._append_receipt(
                event,
                effective_status,
                detail or "渠道回调登记",
                at=at,
                callback_status=receipt_status,
            )

            if effective_status == RECEIPT_DELIVERED:
                first_delivery = not existing
                if first_delivery:
                    event["事件状态"] = STATUS_CONFIRMED
                    event["送达结果"] = RESULT_DELIVERED
                    event["pending"] = False
                    event["abnormal"] = False
                    self._apply_adoption(event)
                event["回执编号"] = receipt["回执编号"]
                event["回执状态"] = RECEIPT_DELIVERED
                event["更新时间"] = at.strftime("%Y-%m-%d %H:%M:%S")
                return event, f"回执 {receipt['回执编号']} 已确认消息 {event['消息编号']} 送达"
            if effective_status == RECEIPT_FAILURE:
                event["事件状态"] = STATUS_FAILED
                event["送达结果"] = RESULT_FAILED
                event["abnormal"] = True
                event["回执编号"] = receipt["回执编号"]
                event["回执状态"] = RECEIPT_FAILURE
                event["更新时间"] = at.strftime("%Y-%m-%d %H:%M:%S")
                return event, f"失败回执 {receipt['回执编号']} 已登记，事件可从最后尝试继续重试"
            if effective_status == RECEIPT_DUPLICATE:
                return event, (
                    f"重复回调已登记为 {receipt['回执编号']}（重复回执），"
                    "不新增投递、不变更送达结果"
                )
            return event, (
                f"迟到回执与已成功送达冲突，已登记为 {receipt['回执编号']}（冲突回执），"
                "采纳记录与重复通知次数保持不变"
            )

    def _append_receipt(
        self,
        event: dict[str, Any],
        receipt_type: str,
        detail: str,
        *,
        at: datetime,
        callback_status: str | None = None,
    ) -> dict[str, Any]:
        receipt_id = _next_id(RECEIPT_TABLE)
        receipt = {
            "id": receipt_id,
            "回执编号": f"RECP-{receipt_id:04d}",
            "事件ID": int(event["id"]),
            "事件编号": event["事件编号"],
            "消息编号": event["消息编号"],
            "幂等键": event["幂等键"],
            "渠道ID": event["渠道ID"],
            "回执类型": receipt_type,
            "回执状态": callback_status or (
                RECEIPT_DELIVERED if receipt_type == RECEIPT_DELIVERED
                else RECEIPT_FAILURE if receipt_type == RECEIPT_FAILURE
                else receipt_type
            ),
            "回执详情": detail,
            "登记时间": at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(RECEIPT_TABLE).append(receipt)
        return receipt

    # ------------------------------------------------------------- 快照
    def generate_snapshot(self, remark: str = "") -> tuple[dict[str, Any] | None, str]:
        """生成通知投递事件数据快照：对当前全部事件做不可变深拷贝。"""
        with _write_lock:
            events = store.rows(MODULE)
            if not events:
                return None, "暂无可生成快照的通知投递事件"
            version = _next_id(SNAPSHOT_TABLE)
            version_no = f"SNAP-{version:04d}"
            frozen_events = copy.deepcopy(events)
            for row in frozen_events:
                row["快照版本"] = version_no
            checksum = hashlib.sha256(
                repr(sorted((row["幂等键"], row["事件状态"], row["回执编号"] or "") for row in frozen_events)).encode("utf-8")
            ).hexdigest()[:16]
            snapshot = {
                "id": version,
                "快照版本": version_no,
                "生成时间": _now_ts(),
                "事件总数": len(frozen_events),
                "已送达数": sum(1 for row in frozen_events if row["事件状态"] in (STATUS_DELIVERED, STATUS_CONFIRMED)),
                "失败数": sum(1 for row in frozen_events if row["事件状态"] == STATUS_FAILED),
                "静默抑制数": sum(1 for row in frozen_events if row["事件状态"] == STATUS_SUPPRESSED),
                "待投递数": sum(1 for row in frozen_events if row["事件状态"] == STATUS_PENDING),
                "校验和": checksum.upper(),
                "备注": remark,
                "events": frozen_events,
            }
            store.rows(SNAPSHOT_TABLE).append(snapshot)
            # 回写当前事件的快照版本标记（快照内部仍保留生成时刻的深拷贝）
            for row in events:
                row["快照版本"] = version_no
            return snapshot, f"快照 {version_no} 已生成，固化 {len(frozen_events)} 条投递事件"

    def list_snapshots(self) -> list[dict[str, Any]]:
        """快照清单不含 events 明细，避免列表响应过大。"""
        return [
            {key: value for key, value in row.items() if key != "events"}
            for row in store.rows(SNAPSHOT_TABLE)
        ]

    def get_snapshot(self, version: int) -> dict[str, Any] | None:
        return store.find(SNAPSHOT_TABLE, version)

    def _latest_snapshot(self) -> dict[str, Any] | None:
        rows = store.rows(SNAPSHOT_TABLE)
        if not rows:
            return None
        return max(rows, key=lambda row: int(row["id"]))

    # ------------------------------------------------------------- SLA 核对
    def confirm_sla_escalation(
        self, sla_rule_id: int, *, snapshot_version: int | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """SLA 监控只依据通知投递事件快照核对升级动作。

        跨模块确认结果通过消息编号关联到同一次送达：返回的每条渠道确认都带消息编号、
        幂等键与回执编号，SLA 侧可凭编号确认它来自同一次送达，而不是重复通知。
        """
        with _write_lock:
            sla = store.find("slavalidate", sla_rule_id)
            if sla is None:
                return None, f"SLA规则 {sla_rule_id} 不存在，无法核对升级"
            snapshot = (
                store.find(SNAPSHOT_TABLE, snapshot_version)
                if snapshot_version is not None
                else self._latest_snapshot()
            )
            if snapshot is None:
                return None, "通知投递事件快照尚未生成，SLA监控拒绝确认升级"
            frozen = [
                row for row in snapshot["events"]
                if int(row.get("SLA规则ID", 0)) == int(sla_rule_id)
            ]
            confirmations = []
            for row in sorted(frozen, key=lambda item: int(item["id"])):
                confirmations.append({
                    "事件编号": row["事件编号"],
                    "消息编号": row["消息编号"],
                    "幂等键": row["幂等键"],
                    "渠道编号": row["渠道编号"],
                    "接收地址": row["接收地址"],
                    "事件状态": row["事件状态"],
                    "送达结果": row["送达结果"],
                    "回执编号": row["回执编号"],
                    "静默时段结果": row["静默时段结果"],
                    "采纳记录": row["采纳记录"],
                    "冗余送达": row["冗余送达"],
                    "投递尝试次数": row["投递尝试次数"],
                    "同一次送达": bool(row["回执编号"])
                    and row["事件状态"] in (STATUS_DELIVERED, STATUS_CONFIRMED),
                })
            delivered = [row for row in confirmations if row["同一次送达"]]
            suppressed = [row for row in confirmations if row["事件状态"] == STATUS_SUPPRESSED]
            failed = [row for row in confirmations if row["事件状态"] == STATUS_FAILED]
            adopted = next((row for row in confirmations if row["采纳记录"]), None)
            confirmed = bool(delivered)
            result = {
                "slaRuleId": int(sla_rule_id),
                "规则编号": sla.get("规则编号", ""),
                "适用作业": sla.get("适用作业", ""),
                "值班组": sla.get("升级通知组", ""),
                "快照版本": snapshot["快照版本"],
                "快照校验和": snapshot["校验和"],
                "确认通过": confirmed,
                "渠道确认数": len(confirmations),
                "送达数": len(delivered),
                "静默抑制数": len(suppressed),
                "失败数": len(failed),
                "采纳消息编号": adopted["消息编号"] if adopted else None,
                "采纳回执编号": adopted["回执编号"] if adopted else None,
                "重复通知次数": sum(1 for row in confirmations if row["冗余送达"]),
                "confirmations": confirmations,
            }
            if confirmed:
                message = (
                    f"SLA {sla.get('规则编号', sla_rule_id)} 升级已依据快照 {snapshot['快照版本']} 核对："
                    f"采纳 {result['采纳消息编号']}，冗余送达 {result['重复通知次数']} 条，"
                    f"静默抑制 {len(suppressed)} 条"
                )
            else:
                message = (
                    f"快照 {snapshot['快照版本']} 中没有该SLA的有效送达回执，"
                    "升级未确认（含静默抑制/投递失败/待投递）"
                )
            return result, message
