"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import etljob as router_etljob
from app.routers import dag as router_dag
from app.routers import dependency as router_dependency
from app.routers import sourcecheck as router_sourcecheck
from app.routers import runtime as router_runtime
from app.routers import rerun as router_rerun
from app.routers import baseline as router_baseline
from app.routers import partitioncheck as router_partitioncheck
from app.routers import comparator as router_comparator
from app.routers import metastore as router_metastore
from app.routers import resource as router_resource
from app.routers import slavalidate as router_slavalidate
from app.routers import sqltpl as router_sqltpl
from app.routers import backuprestore as router_backuprestore
from app.routers import logarchive as router_logarchive
from app.routers import notifychan as router_notifychan
from app.routers import notifyevent as router_notifyevent
from app.routers import publishplan as router_publishplan

ROUTERS = [router_etljob, router_dag, router_dependency, router_sourcecheck, router_runtime, router_rerun, router_baseline, router_partitioncheck, router_comparator, router_metastore, router_resource, router_slavalidate, router_sqltpl, router_backuprestore, router_logarchive, router_notifychan, router_notifyevent, router_publishplan]
