"""SLA监控接口：维护SLA规则，覆盖提交规则评审、生效规则、关闭规则等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.notifyevent import NotifyeventService
from app.services.slavalidate import SlavalidateService

router = APIRouter(prefix="/api/slavalidate", tags=["SLA监控"])

service = SlavalidateService()
notifyevent_service = NotifyeventService()

LIST_FIELDS = ["规则编号", "适用作业", "完成截止时间", "预警提前分钟", "超时动作", "升级通知组", "每日频次", "容忍天数"]
STATUSES = ["草稿", "规则评审中", "已生效", "已关闭"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、规则评审中、已生效、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤SLA监控列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条SLA规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"SLA规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条SLA规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="SLA规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条SLA规则执行提交规则评审、生效规则、关闭规则；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


class SnapshotConfirmPayload(BaseModel):
    """SLA监控核对升级：只依据通知投递事件快照，不直接读渠道实时数据。"""

    snapshotVersion: int | None = None


@router.post("/{entry_id}/confirm-delivery", response_model=ActionResult)
def confirm_delivery(entry_id: int, payload: SnapshotConfirmPayload) -> ActionResult:
    """读取通知投递事件快照核对该SLA的异常升级消息、送达结果与静默时段结果。

    确认结果中的每条渠道记录都带消息编号与回执编号，保证跨模块确认来自同一次送达。
    """
    result, message = notifyevent_service.confirm_sla_escalation(
        entry_id, snapshot_version=payload.snapshotVersion
    )
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=bool(result["确认通过"]), message=message, entry=result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出SLA监控清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "slavalidate", "total": total, "items": items}
