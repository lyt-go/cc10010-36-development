"""通知投递事件接口。

围绕 SLA 升级 → 投递批次 → 投递事件 → 确认回执 → 快照 → SLA 核对 的完整链路，
路由层只做参数编排，幂等、去重、原子落库与冲突取舍都在 service 内。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, PageResult
from app.services.notifyevent import (
    RECEIPT_DELIVERED,
    RECEIPT_FAILURE,
    NotifyeventService,
)

router = APIRouter(prefix="/api/notifyevent", tags=["通知投递事件"])

service = NotifyeventService()

LIST_FIELDS = [
    "事件编号", "幂等键", "批次编号", "规则编号", "值班组", "渠道编号",
    "消息编号", "事件状态", "送达结果", "投递尝试次数", "回执编号",
    "采纳记录", "冗余送达", "静默时段结果", "快照版本",
]
STATUSES = ["待投递", "静默抑制", "投递失败", "已送达", "已确认送达"]


class BatchPayload(BaseModel):
    """多条升级记录多选一次提交为投递批次。"""

    slaRuleIds: list[int] = Field(default_factory=list)
    channelIds: list[int] | None = None


class SendPayload(BaseModel):
    """投递动作入参；simulateFailure 用于演示失败后从最后尝试继续。"""

    simulateFailure: bool = False


class ReceiptPayload(BaseModel):
    """渠道确认回执回调入参。"""

    receiptStatus: str = Field(description=f"{RECEIPT_DELIVERED} 或 {RECEIPT_FAILURE}")
    detail: str | None = None


class SnapshotPayload(BaseModel):
    remark: str | None = None


class ConfirmPayload(BaseModel):
    """SLA 监控核对升级；不传 snapshotVersion 时使用最新快照。"""

    slaRuleId: int
    snapshotVersion: int | None = None


@router.get("/batches", response_model=dict)
def list_batches() -> dict[str, Any]:
    """投递批次列表。"""
    items = service.list_batches()
    return {"items": items, "total": len(items)}


@router.get("/snapshots", response_model=dict)
def list_snapshots() -> dict[str, Any]:
    """通知投递事件数据快照列表（不含事件明细）。"""
    items = service.list_snapshots()
    return {"items": items, "total": len(items)}


@router.get("/snapshots/{version}", response_model=dict)
def get_snapshot(version: int) -> dict[str, Any]:
    """读取单个快照的完整固化事件明细。"""
    snapshot = service.get_snapshot(version)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"快照版本 {version} 不存在")
    return snapshot


@router.get("/receipts", response_model=dict)
def list_receipts(event_id: int | None = Query(default=None)) -> dict[str, Any]:
    """确认回执列表，可按事件过滤。"""
    items = service.list_receipts(event_id)
    return {"items": items, "total": len(items)}


@router.get("", response_model=PageResult[dict])
def list_events(
    keyword: str | None = Query(default=None, description="按事件编号/幂等键/消息编号检索"),
    status: str | None = Query(default=None, description="待投递、静默抑制、投递失败、已送达、已确认送达"),
    group: str | None = Query(default=None, description="按值班组过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """通知投递事件分页查询；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_events(keyword=keyword, status=status, group=group, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_events() -> dict[str, Any]:
    """导出通知投递事件全量清单。"""
    items, total = service.list_events(page=1, size=10000)
    return {"module": "notifyevent", "total": total, "items": items}


@router.get("/{event_id}", response_model=dict)
def get_event(event_id: int) -> dict:
    """读取单条投递事件明细；不存在时给出可读的错误说明。"""
    event = service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"投递事件 {event_id} 不存在")
    return event


@router.post("/batches", response_model=ActionResult)
def create_batch(payload: BatchPayload) -> ActionResult:
    """多条SLA升级记录多选一次提交为投递批次，批量生成事件并按幂等键去重。"""
    batch, message = service.create_batch(payload.slaRuleIds, payload.channelIds)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/snapshots", response_model=ActionResult)
def generate_snapshot(payload: SnapshotPayload) -> ActionResult:
    """生成通知投递事件数据快照，SLA 监控随后只依据快照确认升级。"""
    snapshot, message = service.generate_snapshot(remark=payload.remark or "")
    if snapshot is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={k: v for k, v in snapshot.items() if k != "events"})


@router.post("/sla/confirm", response_model=ActionResult)
def confirm_sla(payload: ConfirmPayload) -> ActionResult:
    """SLA 监控读取快照后核对升级动作，跨模块确认结果来自同一次送达。"""
    result, message = service.confirm_sla_escalation(
        payload.slaRuleId,
        snapshot_version=payload.snapshotVersion,
    )
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=bool(result["确认通过"]), message=message, entry=result)


@router.post("/{event_id}/send", response_model=ActionResult)
def send_event(event_id: int, payload: SendPayload) -> ActionResult:
    """按幂等键投递；重复发送被拦截，失败后可从最后尝试继续。"""
    event, message = service.send_event(event_id, simulate_failure=payload.simulateFailure)
    if event is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=event["事件状态"] != "投递失败", message=message, entry=event)


@router.post("/{event_id}/receipts", response_model=ActionResult)
def register_receipt(event_id: int, payload: ReceiptPayload) -> ActionResult:
    """登记渠道确认回执；重复回调不新增投递，冲突回执不覆盖已成功送达结果。"""
    if payload.receiptStatus not in (RECEIPT_DELIVERED, RECEIPT_FAILURE):
        return ActionResult(
            ok=False,
            message=f"回执状态仅支持 {RECEIPT_DELIVERED} / {RECEIPT_FAILURE}",
        )
    event, message = service.register_receipt(
        event_id, payload.receiptStatus, detail=payload.detail or ""
    )
    if event is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=event)


@router.post("/{event_id}/reset", response_model=ActionResult)
def reset_failed(event_id: int) -> ActionResult:
    """排障后重置失败事件，保留尝试次数并从最后尝试继续。"""
    event, message = service.reset_failed(event_id)
    if event is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=event)
