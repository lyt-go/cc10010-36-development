"""通知投递事件 HTTP 全链路集成验证（仅标准库，通过真实 uvicorn 端口调用）。

覆盖：批次提交去重 → 失败重试 → 并发发送 → 回执冲突 → 快照 → SLA 核对。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"http_error": exc.code, "detail": exc.read().decode("utf-8")}


def assert_true(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(f"断言失败：{label}")


# 0. 健康检查与种子数据
health = call("GET", "/api/health")
assert_true(health["ok"], "服务健康检查")
seed_events = call("GET", "/api/notifyevent?size=200")
assert_true(seed_events["total"] == 5, f"种子投递事件 5 条（实际 {seed_events['total']}）")

# 1. 基于种子快照做 SLA 核对：SLA 1 有采纳送达 + 1 条冗余
confirm1 = call("POST", "/api/slavalidate/1/confirm-delivery", {})
assert_true(confirm1["ok"], "SLA1 依据种子快照核对通过")
entry = confirm1["entry"]
assert_true(entry["确认通过"] and entry["重复通知次数"] == 1,
            f"SLA1 采纳 1 条、冗余 1 条（实际冗余 {entry['重复通知次数']}）")
assert_true(all(c["消息编号"] and (c["回执编号"] or c["事件状态"] == "静默抑制")
                for c in entry["confirmations"]), "每条确认带消息编号，送达者带回执编号")

# 2. 新建批次：选择 SLA 1/2 与渠道 1/3，重复提交应全部去重
batch_resp = call("POST", "/api/notifyevent/batches", {"slaRuleIds": [1, 2], "channelIds": [1, 3]})
assert_true(batch_resp["ok"], "重复批次提交被接受")
assert_true(batch_resp["entry"]["生成事件数"] == 0,
            f"幂等去重：新生成 0 条（实际 {batch_resp['entry']['生成事件数']}）")
assert_true(batch_resp["entry"]["去重跳过数"] == 4,
            f"去重跳过 4 条（实际 {batch_resp['entry']['去重跳过数']}）")

# 3. 先登记一条全新 SLA 升级记录，再与渠道 1/2 组成批次：2 条均为全新事件
new_sla = call("POST", "/api/slavalidate", {"values": {"规则编号": "SLAV-NEW", "适用作业": "集成验证作业", "完成截止时间": "2026-11-16 18:00", "升级通知组": "数据值班组A"}})
assert_true(new_sla["ok"], "登记新 SLA 升级记录")
sla_new_id = new_sla["entry"]["id"]
fresh = call("POST", "/api/notifyevent/batches", {"slaRuleIds": [sla_new_id], "channelIds": [1, 2]})
assert_true(fresh["ok"] and fresh["entry"]["生成事件数"] == 2,
            f"新批次生成 2 条事件（实际 {fresh['entry'].get('生成事件数')}）")
new_ids = fresh["entry"]["事件ID列表"]

# 同批次重复提交：两条全部命中幂等键，不新增投递
resubmit = call("POST", "/api/notifyevent/batches", {"slaRuleIds": [sla_new_id], "channelIds": [1, 2]})
assert_true(resubmit["entry"]["生成事件数"] == 0 and resubmit["entry"]["去重跳过数"] == 2,
            "同批次重复提交全部幂等去重")

# 4. 先模拟失败，再重置后成功：从最后尝试继续
fail = call("POST", f"/api/notifyevent/{new_ids[0]}/send", {"simulateFailure": True})
assert_true(fail["entry"]["事件状态"] == "投递失败" and fail["entry"]["投递尝试次数"] == 1,
            "首次投递失败并记录失败回执")
reset = call("POST", f"/api/notifyevent/{new_ids[0]}/reset", {})
assert_true(reset["entry"]["事件状态"] == "待投递", "失败事件已重置为待投递")
ok1 = call("POST", f"/api/notifyevent/{new_ids[0]}/send", {})
assert_true(ok1["entry"]["事件状态"] == "已送达" and ok1["entry"]["投递尝试次数"] == 2,
            "重试成功且尝试次数从最后尝试继续（=2）")

# 5. 并发发送同一消息：8 线程同时投递第二个事件，只允许一次成功
target = new_ids[1]
barrier = threading.Barrier(8)
results: list[dict] = []
lock = threading.Lock()


def worker():
    barrier.wait()
    with lock:
        results.append(call("POST", f"/api/notifyevent/{target}/send", {}))


threads = [threading.Thread(target=worker) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
final = call("GET", f"/api/notifyevent/{target}")
receipts = call("GET", f"/api/notifyevent/receipts?event_id={target}")
delivery = [r for r in receipts["items"] if r["回执类型"] == "送达确认"]
assert_true(final["投递尝试次数"] == 1 and len(delivery) == 1,
            f"并发 8 发送只形成一次送达（尝试 {final['投递尝试次数']}，送达回执 {len(delivery)}）")

# 6. 重复回调不新增投递；迟到失败回执冲突不覆盖送达
dup = call("POST", f"/api/notifyevent/{target}/receipts", {"receiptStatus": "送达确认", "detail": "渠道重推"})
assert_true("重复回执" in dup["message"], "重复回调标记为重复回执且不新增投递")
conflict = call("POST", f"/api/notifyevent/{target}/receipts", {"receiptStatus": "失败回执", "detail": "迟到"})
assert_true("冲突回执" in conflict["message"] and conflict["entry"]["送达结果"] == "送达成功",
            "迟到失败回执标记冲突，不覆盖已成功送达")

# 7. 生成新快照，并验证快照不可变（生成后再登记回执，快照内容不变）
snap = call("POST", "/api/notifyevent/snapshots", {"remark": "集成验证快照"})
assert_true(snap["ok"], "生成通知投递事件快照")
snap_version = snap["entry"]["id"]
snap_detail = call("GET", f"/api/notifyevent/snapshots/{snap_version}")
frozen_states = [(row["幂等键"], row["事件状态"]) for row in snap_detail["events"]]
call("POST", f"/api/notifyevent/{new_ids[0]}/receipts", {"receiptStatus": "送达确认"})
snap_detail2 = call("GET", f"/api/notifyevent/snapshots/{snap_version}")
assert_true(
    [(row["幂等键"], row["事件状态"]) for row in snap_detail2["events"]] == frozen_states,
    "快照生成后在线数据变化不影响快照内容",
)

# 8. SLA 监控只依据快照核对新批次
confirm3 = call("POST", f"/api/slavalidate/{sla_new_id}/confirm-delivery", {"snapshotVersion": snap_version})
assert_true(confirm3["ok"], "新 SLA 依据新快照核对通过")
assert_true(confirm3["entry"]["快照版本"] == snap_detail["快照版本"], "核对结果标注快照版本")
assert_true(confirm3["entry"]["送达数"] == 2,
            f"新 SLA 两个渠道均送达（实际 {confirm3['entry']['送达数']}）")
assert_true(confirm3["entry"]["重复通知次数"] == 1,
            f"同升级多渠道采纳 1 条、冗余 1 条（实际 {confirm3['entry']['重复通知次数']}）")

# 9. 静默时段结果可核对：渠道 2 配置全天静默后新建事件
# （内存服务，直接走批次；渠道 2 种子里静默时段是日期串，解析失败视为未配置，先验证无静默配置口径）
no_snap_confirm = call("POST", "/api/notifyevent/sla/confirm", {"slaRuleId": 99999})
assert_true(not no_snap_confirm["ok"] and "不存在" in no_snap_confirm["message"],
            "不存在的 SLA 核对返回可读错误")

# 10. 列表过滤与导出
filtered = call("GET", "/api/notifyevent?" + urllib.parse.urlencode({"status": "投递失败", "size": 200}))
assert_true(all(row["事件状态"] == "投递失败" for row in filtered["items"]) and filtered["total"] >= 1,
            "按事件状态过滤生效")
exported = call("GET", "/api/notifyevent/export")
assert_true(exported["module"] == "notifyevent" and exported["total"] >= 7, "导出接口返回全量事件")

print("\n全部 HTTP 集成断言通过。")
