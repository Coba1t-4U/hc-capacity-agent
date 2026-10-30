"""hc_agent.runtime — 运行时可靠性基础设施。

移植自天枢一号（见 docs/移植自天枢一号.md）：

  * ``crashlog``    —— 崩溃可观测（线程/未捕获异常/析构异常都落盘）
  * ``watchdog``    —— 引擎看门狗与自愈（复检判中毒 + 熔断 + 交外层重启）
  * ``state_store`` —— 状态增量落盘（原子写 + 最后遗言）

设计纪律（三条，全部照搬）：
  1. 观测设施**绝不能成为故障源** —— 写日志失败只计数，不影响主流程。
  2. 探针与决策函数**永不抛出** —— 自愈机制出错不能反过来搞垮任务。
  3. 冻结的判定**要能区分"没来得及判"与"判为失败"** —— 见 errors.EXIT_INTERRUPTED。
"""

from hc_agent.runtime import crashlog, state_store, watchdog

__all__ = ["crashlog", "watchdog", "state_store"]
