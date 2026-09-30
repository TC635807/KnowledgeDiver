"""backend.task — 统一任务服务层。

TaskService 是该包的唯一对外接口，单例模式。
所有任务创建、查询、SSE 流均通过 TaskService.get_instance() 获取。"""

from backend.task.service import TaskService

__all__ = ["TaskService"]
