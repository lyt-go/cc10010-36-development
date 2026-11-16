"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None



class EtljobEntry(BaseModel):
    """作业定义明细结构。"""

    field_0: str | None = None  # 作业编号
    field_1: str | None = None  # 作业名称
    field_2: str | None = None  # 源端数据表
    field_3: str | None = None  # 目标数据表
    field_4: str | None = None  # 执行SQL
    field_5: str | None = None  # 调度表达式
    field_6: str | None = None  # 超时分钟
    field_7: str | None = None  # 失败重试次数

class DagEntry(BaseModel):
    """编排图明细结构。"""

    field_0: str | None = None  # 图编号
    field_1: str | None = None  # 图名称
    field_2: str | None = None  # 拓扑结构
    field_3: str | None = None  # 起始节点数
    field_4: str | None = None  # 最大并行度
    field_5: str | None = None  # 失败传播策略
    field_6: str | None = None  # 调度优先级
    field_7: str | None = None  # 生效时间范围

class DependencyEntry(BaseModel):
    """依赖关系明细结构。"""

    field_0: str | None = None  # 关系编号
    field_1: str | None = None  # 上游作业
    field_2: str | None = None  # 下游作业
    field_3: str | None = None  # 依赖类型
    field_4: str | None = None  # 偏移量分钟
    field_5: str | None = None  # 触发条件
    field_6: str | None = None  # 等待超时
    field_7: str | None = None  # 跳过标志

class SourcecheckEntry(BaseModel):
    """校验配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 源端数据表
    field_2: str | None = None  # 行数下限
    field_3: str | None = None  # 行数上限
    field_4: str | None = None  # 空值字段列表
    field_5: str | None = None  # 数据新鲜度分钟
    field_6: str | None = None  # 分区完整性
    field_7: str | None = None  # 阻断级别

class RuntimeEntry(BaseModel):
    """运行记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 作业名称
    field_2: str | None = None  # 开始时间
    field_3: str | None = None  # 结束时间
    field_4: str | None = None  # 执行状态
    field_5: str | None = None  # 读取行数
    field_6: str | None = None  # 写入行数
    field_7: str | None = None  # 错误信息

class RerunEntry(BaseModel):
    """重跑任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 原始运行编号
    field_2: str | None = None  # 重跑区间开始
    field_3: str | None = None  # 重跑区间结束
    field_4: str | None = None  # 重跑原因
    field_5: str | None = None  # 覆盖策略
    field_6: str | None = None  # 依赖级联
    field_7: str | None = None  # 重跑状态

class BaselineEntry(BaseModel):
    """基线定义明细结构。"""

    field_0: str | None = None  # 基线编号
    field_1: str | None = None  # 基线名称
    field_2: str | None = None  # 承诺完成时间
    field_3: str | None = None  # 预警余量分钟
    field_4: str | None = None  # 破线级别
    field_5: str | None = None  # 关联作业列表
    field_6: str | None = None  # 工作日标记
    field_7: str | None = None  # 节假日偏移

class PartitioncheckEntry(BaseModel):
    """检测规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 数据表名
    field_2: str | None = None  # 期望分区数
    field_3: str | None = None  # 分区延迟容忍
    field_4: str | None = None  # 缺失分区告警
    field_5: str | None = None  # 检测间隔分钟
    field_6: str | None = None  # 自动补齐开关
    field_7: str | None = None  # 补齐优先级

class ComparatorEntry(BaseModel):
    """比对任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 源表名
    field_2: str | None = None  # 目标表名
    field_3: str | None = None  # 比对字段
    field_4: str | None = None  # 比对方式
    field_5: str | None = None  # 差异阈值
    field_6: str | None = None  # 采样比例
    field_7: str | None = None  # 产出差异报告

class MetastoreEntry(BaseModel):
    """元数据表明细结构。"""

    field_0: str | None = None  # 表编号
    field_1: str | None = None  # 表名
    field_2: str | None = None  # 所属数仓层
    field_3: str | None = None  # 字段数量
    field_4: str | None = None  # 分区字段
    field_5: str | None = None  # 主键字段
    field_6: str | None = None  # 数据量级
    field_7: str | None = None  # 最近更新时间

class ResourceEntry(BaseModel):
    """资源池明细结构。"""

    field_0: str | None = None  # 池编号
    field_1: str | None = None  # 池名称
    field_2: str | None = None  # 最大并发数
    field_3: str | None = None  # 内存上限MB
    field_4: str | None = None  # CPU核心数
    field_5: str | None = None  # 队列策略
    field_6: str | None = None  # 抢占开关
    field_7: str | None = None  # 作业白名单

class SlavalidateEntry(BaseModel):
    """SLA规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 适用作业
    field_2: str | None = None  # 完成截止时间
    field_3: str | None = None  # 预警提前分钟
    field_4: str | None = None  # 超时动作
    field_5: str | None = None  # 升级通知组
    field_6: str | None = None  # 每日频次
    field_7: str | None = None  # 容忍天数

class SqltplEntry(BaseModel):
    """模板定义明细结构。"""

    field_0: str | None = None  # 模板编号
    field_1: str | None = None  # 模板名称
    field_2: str | None = None  # 模板类型
    field_3: str | None = None  # SQL片段
    field_4: str | None = None  # 参数占位符
    field_5: str | None = None  # 预期输出列
    field_6: str | None = None  # 参数默认值
    field_7: str | None = None  # 模板版本

class BackuprestoreEntry(BaseModel):
    """备份策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 数据表名
    field_2: str | None = None  # 备份周期
    field_3: str | None = None  # 保留天数
    field_4: str | None = None  # 备份存储位置
    field_5: str | None = None  # 压缩方式
    field_6: str | None = None  # 加密开关
    field_7: str | None = None  # 最近备份时间

class LogarchiveEntry(BaseModel):
    """归档策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 日志类型
    field_2: str | None = None  # 保留天数
    field_3: str | None = None  # 归档存储
    field_4: str | None = None  # 压缩级别
    field_5: str | None = None  # 分区归档
    field_6: str | None = None  # 清理前备份
    field_7: str | None = None  # 执行时间窗口

class NotifychanEntry(BaseModel):
    """渠道配置明细结构。"""

    field_0: str | None = None  # 渠道编号
    field_1: str | None = None  # 渠道类型
    field_2: str | None = None  # 接收地址
    field_3: str | None = None  # 消息模板
    field_4: str | None = None  # 发送频率
    field_5: str | None = None  # 静默时段开始
    field_6: str | None = None  # 静默时段结束
    field_7: str | None = None  # 消息优先级

class PublishplanEntry(BaseModel):
    """发布单明细结构。"""

    field_0: str | None = None  # 发布编号
    field_1: str | None = None  # 发布内容
    field_2: str | None = None  # 变更说明
    field_3: str | None = None  # 回滚方案
    field_4: str | None = None  # 发布窗口开始
    field_5: str | None = None  # 发布窗口结束
    field_6: str | None = None  # 审批人
    field_7: str | None = None  # 发布状态


class NotifyeventEntry(BaseModel):
    """通知投递事件明细结构。"""

    field_0: str | None = None  # 事件编号
    field_1: str | None = None  # 幂等键
    field_2: str | None = None  # 批次编号
    field_3: str | None = None  # 规则编号
    field_4: str | None = None  # 值班组
    field_5: str | None = None  # 渠道编号
    field_6: str | None = None  # 消息编号
    field_7: str | None = None  # 事件状态
    field_8: str | None = None  # 送达结果
    field_9: int | None = None  # 投递尝试次数
    field_10: str | None = None  # 回执编号
    field_11: str | None = None  # 静默时段结果
    field_12: str | None = None  # 快照版本
