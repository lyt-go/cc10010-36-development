"""通知投递事件链路测试。

覆盖需求中的硬约束：
1. 稳定幂等键与去重：同 SLA+渠道重复提交不新增投递；
2. 多条升级记录一次提交为批次并批量生成事件；
3. 投递尝试/确认回执原子落库，失败后从最后尝试继续；
4. 并发发送同一消息只允许一次成功形成送达结果；
5. 重复回调不新增投递；迟到失败回执与已成功送达冲突时不覆盖结果；
6. 多渠道路由确定性取舍：采纳记录不随回执到达顺序变化，重复通知次数可核对；
7. 快照不可变，SLA 监控只依据快照确认升级，跨模块凭消息编号确认同一次送达；
8. 静默时段结果可核对，且保持旧有渠道配置不变。
"""
from __future__ import annotations

import threading
import unittest
from datetime import datetime

from app.services import notifyevent as nev
from app.services.notifyevent import (
    RECEIPT_DELIVERED,
    RECEIPT_FAILURE,
    STATUS_CONFIRMED,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUPPRESSED,
    NotifyeventService,
    idempotency_key,
)
from app.store import store

TABLES = ["notifyevent", "notifybatch", "notifyreceipt", "notifyeventsnapshot"]


class NotifyeventTestBase(unittest.TestCase):
    def setUp(self) -> None:
        for table in TABLES:
            store.rows(table).clear()
        # 准备两个 SLA 升级记录与两个渠道，渠道字段保持旧有配置口径
        sla_rows = store.rows("slavalidate")
        sla_rows.clear()
        sla_rows.extend([
            {"id": 101, "规则编号": "SLAV-T1", "适用作业": "作业甲", "升级通知组": "值班组X", "status": "已生效"},
            {"id": 102, "规则编号": "SLAV-T2", "适用作业": "作业乙", "升级通知组": "值班组X", "status": "已生效"},
        ])
        chan_rows = store.rows("notifychan")
        chan_rows.clear()
        chan_rows.extend([
            {"id": 201, "渠道编号": "NOTI-T1", "渠道类型": "值班组X", "接收地址": "oncall-x@example",
             "消息模板": "升级模板", "静默时段开始": "", "静默时段结束": ""},
            {"id": 202, "渠道编号": "NOTI-T2", "渠道类型": "值班组X", "接收地址": "值班组X-webhook",
             "消息模板": "升级模板", "静默时段开始": "", "静默时段结束": ""},
        ])
        self.service = NotifyeventService()

    def _make_batch(self, sla_ids=(101,), channel_ids=(201, 202)):
        batch, message = self.service.create_batch(list(sla_ids), list(channel_ids))
        self.assertIsNotNone(batch, message)
        return batch

    def _events(self):
        return store.rows("notifyevent")


class IdempotencyAndBatchTests(NotifyeventTestBase):
    def test_idempotency_key_stable(self):
        self.assertEqual(idempotency_key(101, 201), idempotency_key(101, 201))
        self.assertNotEqual(idempotency_key(101, 201), idempotency_key(101, 202))

    def test_batch_expands_channels_and_dedupes(self):
        batch = self._make_batch(sla_ids=(101, 102), channel_ids=(201, 202))
        # 2 条 SLA × 2 个渠道 = 4 条事件，一个批次
        self.assertEqual(batch["生成事件数"], 4)
        self.assertEqual(len(self._events()), 4)
        self.assertEqual(len(store.rows("notifybatch")), 1)
        keys = [row["幂等键"] for row in self._events()]
        self.assertEqual(len(keys), len(set(keys)))

        # 再次提交同样的多选记录：全部命中幂等键，不新增事件/投递
        again, message = self.service.create_batch([101, 102], [201, 202])
        self.assertEqual(again["生成事件数"], 0)
        self.assertEqual(again["去重跳过数"], 4)
        self.assertEqual(len(self._events()), 4)
        # 复用事件会挂接到新批次，链路可追踪
        self.assertIn(again["批次编号"], self._events()[0]["批次编号列表"])

    def test_empty_selection_rejected(self):
        batch, message = self.service.create_batch([], [201])
        self.assertIsNone(batch)
        self.assertIn("至少选择一条", message)


class QuietWindowTests(NotifyeventTestBase):
    def test_quiet_window_suppresses_without_touching_channel_config(self):
        channel = store.find("notifychan", 201)
        before = dict(channel)
        # 全天静默：任意时刻都落在静默时段
        channel["静默时段开始"] = "00:00"
        channel["静默时段结束"] = "24:00"
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event = self._events()[0]
        self.assertEqual(event["事件状态"], STATUS_SUPPRESSED)
        self.assertEqual(event["送达结果"], "静默时段抑制")
        self.assertEqual(event["投递尝试次数"], 0)
        # 静默事件不允许发起投递
        _, msg = self.service.send_event(event["id"])
        self.assertIn("静默抑制", msg)
        self.assertEqual(len(store.rows("notifyreceipt")), 0)
        # 旧有渠道配置保持不变
        self.assertEqual(store.find("notifychan", 201), before | {"静默时段开始": "00:00", "静默时段结束": "24:00"})

    def test_quiet_window_across_midnight(self):
        channel = store.find("notifychan", 201)
        channel["静默时段开始"] = "23:00"
        channel["静默时段结束"] = "07:00"
        inside, note = nev._in_quiet_window(channel, datetime(2026, 11, 16, 23, 30))
        self.assertTrue(inside)
        inside_early, _ = nev._in_quiet_window(channel, datetime(2026, 11, 16, 6, 0))
        self.assertTrue(inside_early)
        outside, _ = nev._in_quiet_window(channel, datetime(2026, 11, 16, 12, 0))
        self.assertFalse(outside)


class DeliveryAndRetryTests(NotifyeventTestBase):
    def test_failure_then_resume_from_last_attempt(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event = self._events()[0]

        ok_event, msg = self.service.send_event(event["id"], simulate_failure=True)
        self.assertEqual(ok_event["事件状态"], STATUS_FAILED)
        self.assertEqual(ok_event["投递尝试次数"], 1)
        # 失败回执与事件在同一次调用内一起落库
        receipts = self.service.list_receipts(event["id"])
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["回执类型"], "失败回执")

        # 排障后重置，再投递成功：尝试次数从最后尝试继续累加，不重新计数
        reset_event, _ = self.service.reset_failed(event["id"])
        self.assertEqual(reset_event["事件状态"], STATUS_PENDING)
        delivered, _ = self.service.send_event(event["id"])
        self.assertEqual(delivered["事件状态"], STATUS_DELIVERED)
        self.assertEqual(delivered["投递尝试次数"], 2)
        self.assertEqual(delivered["送达结果"], "送达成功")
        receipts = self.service.list_receipts(event["id"])
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[-1]["回执类型"], RECEIPT_DELIVERED)

    def test_max_attempts_cap(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event = self._events()[0]
        for _ in range(3):
            self.service.send_event(event["id"], simulate_failure=True)
        blocked, msg = self.service.send_event(event["id"], simulate_failure=True)
        self.assertEqual(blocked["投递尝试次数"], 3)
        self.assertIn("最大投递尝试次数", msg)

    def test_duplicate_send_blocked_without_new_attempt_or_receipt(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event = self._events()[0]
        self.service.send_event(event["id"])
        receipts_before = len(self.service.list_receipts(event["id"]))
        again, msg = self.service.send_event(event["id"])
        self.assertIn("幂等拦截", msg)
        self.assertEqual(again["投递尝试次数"], 1)
        self.assertEqual(len(self.service.list_receipts(event["id"])), receipts_before)

    def test_concurrent_send_single_success(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event_id = self._events()[0]["id"]
        barrier = threading.Barrier(8)
        outcomes: list[str] = []
        outcomes_lock = threading.Lock()

        def worker():
            barrier.wait()
            ev, _ = self.service.send_event(event_id)
            with outcomes_lock:
                outcomes.append(ev["事件状态"])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        event = self.service.get_event(event_id)
        # 只形成一次送达结果：尝试次数为 1，送达回执只有 1 条
        self.assertEqual(event["投递尝试次数"], 1)
        self.assertEqual(event["事件状态"], STATUS_DELIVERED)
        delivery_receipts = [
            r for r in self.service.list_receipts(event_id)
            if r["回执类型"] == RECEIPT_DELIVERED
        ]
        self.assertEqual(len(delivery_receipts), 1)


class ReceiptConflictTests(NotifyeventTestBase):
    def test_duplicate_callback_does_not_add_delivery(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event_id = self._events()[0]["id"]
        self.service.send_event(event_id)
        before = len(store.rows("notifyreceipt"))

        ev, msg = self.service.register_receipt(event_id, RECEIPT_DELIVERED, detail="渠道重推")
        self.assertIn("重复回执", msg)
        self.assertEqual(ev["事件状态"], STATUS_DELIVERED)
        # 只多一条审计型重复回执，且不新增投递尝试
        self.assertEqual(len(store.rows("notifyreceipt")), before + 1)
        self.assertEqual(ev["投递尝试次数"], 1)
        self.assertEqual(store.rows("notifyreceipt")[-1]["回执类型"], "重复回执")

    def test_late_failure_receipt_conflicts_keeps_delivery(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        event_id = self._events()[0]["id"]
        self.service.send_event(event_id)
        adopted_before = self.service.get_event(event_id)["采纳记录"]

        ev, msg = self.service.register_receipt(event_id, RECEIPT_FAILURE, detail="迟到的失败回调")
        self.assertIn("冲突回执", msg)
        # 已成功的送达结果不被覆盖
        self.assertEqual(ev["事件状态"], STATUS_DELIVERED)
        self.assertEqual(ev["送达结果"], "送达成功")
        self.assertEqual(store.rows("notifyreceipt")[-1]["回执类型"], "冲突回执")
        self.assertEqual(ev["采纳记录"], adopted_before)


class MultiChannelAdoptionTests(NotifyeventTestBase):
    def test_adoption_stable_against_receipt_order(self):
        # 同值班组X：SLA101 走两个渠道，事件1先创建、事件2后创建
        self._make_batch(sla_ids=(101,), channel_ids=(201, 202))
        first, second = sorted(self._events(), key=lambda row: row["id"])
        # 较晚创建的渠道回执先到达，再到较早渠道的回执
        self.service.send_event(second["id"])
        self.service.send_event(first["id"])
        first = self.service.get_event(first["id"])
        second = self.service.get_event(second["id"])
        # 采纳与回执到达顺序无关，只取决于创建顺序
        self.assertTrue(first["采纳记录"])
        self.assertFalse(first["冗余送达"])
        self.assertTrue(second["冗余送达"])
        self.assertFalse(second["采纳记录"])

        # 再补一个同组 SLA102 的送达，采纳结论仍稳定，重复通知次数可核对
        self._make_batch(sla_ids=(102,), channel_ids=(201, 202))
        for row in sorted(self._events(), key=lambda row: row["id"]):
            if row["事件状态"] == STATUS_PENDING:
                self.service.send_event(row["id"])
        adopted = [row for row in self._events() if row["采纳记录"]]
        redundant = [row for row in self._events() if row["冗余送达"]]
        self.assertEqual(len(adopted), 2)  # 每个 SLA 各自的最早送达
        self.assertEqual(len(redundant), 2)
        # 迟到冲突回执不改变采纳
        self.service.register_receipt(first["id"], RECEIPT_FAILURE, detail="迟到回执")
        self.assertTrue(self.service.get_event(first["id"])["采纳记录"])


class SnapshotAndSlaConfirmTests(NotifyeventTestBase):
    def test_snapshot_immutable_and_sla_confirms_only_on_snapshot(self):
        self._make_batch(sla_ids=(101, 102), channel_ids=(201, 202))
        for row in self._events():
            if row["渠道ID"] == 201:
                self.service.send_event(row["id"])
            else:
                self.service.send_event(row["id"], simulate_failure=True)

        # 快照未生成时，SLA 监控拒绝确认
        result, message = self.service.confirm_sla_escalation(101)
        self.assertIsNone(result)
        self.assertIn("快照尚未生成", message)

        snapshot, _ = self.service.generate_snapshot(remark="核对前快照")
        frozen_before = [dict(row) for row in snapshot["events"]]

        # 快照生成后再改动在线事件，快照内容与校验口径不变
        target = self._events()[0]
        self.service.register_receipt(target["id"], RECEIPT_DELIVERED)
        self.assertEqual(
            [(row["幂等键"], row["事件状态"]) for row in snapshot["events"]],
            [(row["幂等键"], row["事件状态"]) for row in frozen_before],
        )

        # SLA 只依据快照核对：确认结果带消息编号/回执编号，可证明来自同一次送达
        result, message = self.service.confirm_sla_escalation(101)
        self.assertIsNotNone(result)
        self.assertTrue(result["确认通过"])
        self.assertEqual(result["快照版本"], snapshot["快照版本"])
        self.assertEqual(result["送达数"], 1)
        self.assertEqual(result["失败数"], 1)
        self.assertEqual(result["重复通知次数"], 0)
        confirmation = result["confirmations"][0]
        self.assertTrue(confirmation["同一次送达"])
        self.assertTrue(confirmation["消息编号"].startswith("MSG-"))
        self.assertTrue(confirmation["回执编号"].startswith("RECP-"))

        # 指定历史快照版本时，核对结果不受之后在线数据变化影响
        pinned, _ = self.service.confirm_sla_escalation(101, snapshot_version=snapshot["id"])
        self.assertEqual(pinned["快照校验和"], snapshot["校验和"])

    def test_confirm_includes_quiet_window_result(self):
        store.find("notifychan", 202)["静默时段开始"] = "00:00"
        store.find("notifychan", 202)["静默时段结束"] = "24:00"
        self._make_batch(sla_ids=(101,), channel_ids=(201, 202))
        self.service.send_event(self._events()[0]["id"])
        snapshot, _ = self.service.generate_snapshot()
        result, _ = self.service.confirm_sla_escalation(101)
        self.assertEqual(result["静默抑制数"], 1)
        quiet = next(c for c in result["confirmations"] if c["事件状态"] == STATUS_SUPPRESSED)
        self.assertIn("静默时段", quiet["静默时段结果"])
        self.assertFalse(quiet["同一次送达"])

    def test_confirm_rejects_unknown_sla(self):
        self._make_batch(sla_ids=(101,), channel_ids=(201,))
        self.service.generate_snapshot()
        result, message = self.service.confirm_sla_escalation(999)
        self.assertIsNone(result)
        self.assertIn("不存在", message)

    def test_seed_snapshot_demo_data_consistent(self):
        """初始化示例数据自身可以跑通 SLA 核对（在清库前的默认数据上验证）。"""
        # 重新载入一份带种子数据的 store 视图
        import copy
        from app.seed import SEED_ROWS
        for table in TABLES + ["slavalidate"]:
            store.rows(table).clear()
            store.rows(table).extend(copy.deepcopy(row) for row in SEED_ROWS[table])
        result, message = self.service.confirm_sla_escalation(1)
        self.assertIsNotNone(result, message)
        self.assertTrue(result["确认通过"])
        self.assertGreaterEqual(result["重复通知次数"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
