"""标准错误码与异常体系。

设计依据：设计文档 8.2（OpenDSS 异常映射为标准错误码）与 17.10（退出码建议）。

退出码契约（17.10 扩展）：
  0 成功 / 2 输入阻断 / 3 计算失败 / 4 复算失败 / 5 未满足发布门禁
  / 6 任务被中断（没来得及下结论）
  以及进程级退出码 70：看门狗判定引擎不可在进程内恢复，交由外层重启。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------- 退出码
EXIT_SUCCESS = 0
EXIT_INPUT_BLOCKED = 2
EXIT_COMPUTE_FAILED = 3
EXIT_VERIFICATION_FAILED = 4
EXIT_RELEASE_GATE_FAILED = 5
# 任务被中断：**没来得及下结论**（进程被杀、引擎中毒退出、用户取消）。
# 与 EXIT_COMPUTE_FAILED 的区别是语义性的：后者表示"执行到了并判定为失败"，
# 本码表示"没来得及判定"。二者混同会让看板把基础设施问题混进业务分布，
# 成功率的分子分母都会被污染（论证见 docs/移植自天枢一号.md）。
EXIT_INTERRUPTED = 6

# 看门狗判定"引擎已无法在进程内恢复"时使用的**进程级**退出码。
# ⚠️ 只有在"外层有重启者"时才有意义 —— 本工程的 Electron 桌面壳监听该码并
# 重启后端。若无重启者而孤立使用，只会让任务静默消失，比不退出更糟。
WATCHDOG_EXIT_CODE = 70

# ------------------------------------------- 引擎错误码（8.2 标准映射）
ERR_DSS_COMPILE_FAILED = "DSS_COMPILE_FAILED"
ERR_DSS_OBJECT_ERROR = "DSS_OBJECT_ERROR"
ERR_DSS_DEPENDENCY_LOAD_FAILED = "DSS_DEPENDENCY_LOAD_FAILED"
ERR_DSS_NOT_CONVERGED = "DSS_NOT_CONVERGED"
ERR_DSS_TIMEOUT = "DSS_TIMEOUT"
ERR_DSS_RESOURCE_EXHAUSTED = "DSS_RESOURCE_EXHAUSTED"
ERR_DSS_PARSE_FAILED = "DSS_PARSE_FAILED"
ERR_DSS_UNKNOWN = "DSS_UNKNOWN"

ENGINE_ERROR_CODES = (
    ERR_DSS_COMPILE_FAILED,
    ERR_DSS_OBJECT_ERROR,
    ERR_DSS_DEPENDENCY_LOAD_FAILED,
    ERR_DSS_NOT_CONVERGED,
    ERR_DSS_TIMEOUT,
    ERR_DSS_RESOURCE_EXHAUSTED,
    ERR_DSS_PARSE_FAILED,
    ERR_DSS_UNKNOWN,
)

# ------------------------------------------------- 输入/规则错误码
ERR_SCHEMA_MISSING_FIELD = "SCHEMA_MISSING_FIELD"
ERR_SCHEMA_TYPE_MISMATCH = "SCHEMA_TYPE_MISMATCH"
ERR_UNIT_INCONSISTENT = "UNIT_INCONSISTENT"
ERR_DUPLICATE_ID = "DUPLICATE_ID"
ERR_FOREIGN_KEY_MISSING = "FOREIGN_KEY_MISSING"
ERR_TOPOLOGY_ISLAND = "TOPOLOGY_ISLAND"
ERR_TOPOLOGY_DISCONNECTED = "TOPOLOGY_DISCONNECTED"
ERR_PHASE_CONFLICT = "PHASE_CONFLICT"
ERR_TIMEZONE_INCONSISTENT = "TIMEZONE_INCONSISTENT"
ERR_TIME_COVERAGE_INSUFFICIENT = "TIME_COVERAGE_INSUFFICIENT"
ERR_VERSION_CONFLICT = "VERSION_CONFLICT"
ERR_RULE_UNSET = "RULE_UNSET"
ERR_RULE_UNAPPROVED = "RULE_UNAPPROVED"
ERR_NODE_NOT_FOUND = "NODE_NOT_FOUND"
ERR_RESERVATION_DUPLICATE = "RESERVATION_DUPLICATE"
ERR_ENGINE_UNSUPPORTED = "ENGINE_UNSUPPORTED"

# 状态枚举：pass / fail / unknown 之外，本系统内部另有 not_evaluated
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"
# 任务被中断：**没来得及判定**。与 fail 的区别见 EXIT_INTERRUPTED 的说明。
# 单列的理由：fail 排查看错误码，interrupted 排查看崩溃日志与最后落盘阶段。
STATUS_INTERRUPTED = "interrupted"

# 红黄绿分级（6.4 / 表 25）
LEVEL_GREEN = "green"
LEVEL_YELLOW = "yellow"
LEVEL_RED = "red"
LEVEL_GREY = "grey"  # 灰色为内部状态，不生成正式红黄绿结论


class HCAgentError(Exception):
    """所有 hc_agent 异常的基类，携带退出码与结构化载荷。"""

    exit_code: int = EXIT_COMPUTE_FAILED
    code: str = ERR_DSS_UNKNOWN

    def __init__(self, message: str, *, code: Optional[str] = None,
                 detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.detail: Dict[str, Any] = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"error_code": self.code, "message": self.message,
                "detail": self.detail, "exit_code": self.exit_code}


class InputBlockedError(HCAgentError):
    """输入阻断：缺字段、拓扑孤岛、版本冲突等（17.10 退出码 2）。

    设计文档 17.1：数据不全、模型能力不足应输出"无法评估"及补充清单，
    不能用估计容量替代计算。
    """

    exit_code = EXIT_INPUT_BLOCKED
    code = ERR_SCHEMA_MISSING_FIELD

    def __init__(self, message: str, *, issues: Optional[list] = None,
                 code: Optional[str] = None) -> None:
        super().__init__(message, code=code, detail={"issues": issues or []})
        self.issues = issues or []


class ComputeFailedError(HCAgentError):
    """计算失败：不收敛、引擎异常、超时（17.10 退出码 3）。"""

    exit_code = EXIT_COMPUTE_FAILED
    code = ERR_DSS_NOT_CONVERGED


class VerificationFailedError(HCAgentError):
    """独立复算失败（17.10 退出码 4）。

    设计文档 9.1-5：验证失败立即阻断发布，生成差异报告并转专业人员处理，
    不允许监督 Agent 自行豁免。
    """

    exit_code = EXIT_VERIFICATION_FAILED
    code = "VERIFICATION_MISMATCH"


class ReleaseGateError(HCAgentError):
    """发布门禁未满足（17.10 退出码 5）。"""

    exit_code = EXIT_RELEASE_GATE_FAILED
    code = "RELEASE_GATE_NOT_MET"


class RuleNotApprovedError(HCAgentError):
    """规则未批准：rules.yaml 中 null 值表示尚待审批而必须阻断的值。

    设计文档 17.9：null 表示尚待审批而必须阻断的值。
    设计文档 16-表 44：未确认项不得由研发人员或大模型自行设定生产默认值。
    """

    exit_code = EXIT_INPUT_BLOCKED
    code = ERR_RULE_UNSET
