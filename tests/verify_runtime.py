"""Verify the ported reliability layer: crashlog, state store, watchdog."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, '.')

from hc_agent.errors import (EXIT_INTERRUPTED, STATUS_INTERRUPTED,
                             WATCHDOG_EXIT_CODE, ERR_DSS_TIMEOUT,
                             ERR_SCHEMA_MISSING_FIELD)
from hc_agent.runtime import crashlog, watchdog
from hc_agent.runtime.state_store import StateStore, write_json_atomic

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 74)
print("1) 原子写 write_json_atomic")
print("=" * 74)
tmp = Path(tempfile.mkdtemp())
p = tmp / "a.json"
write_json_atomic(p, {"x": 1})
check("基本写入", p.exists() and p.read_text(encoding="utf-8").strip().startswith("{"))
check("无残留 .tmp", not (tmp / "a.json.tmp").exists())
src = Path("hc_agent/runtime/state_store.py").read_text(encoding="utf-8")
check("源码含 os.replace", "os.replace" in src)
check("源码含 fsync", "fsync" in src)

print()
print("=" * 74)
print("2) 状态遗言 StateStore")
print("=" * 74)
run = tmp / "run"
st = StateStore(run, task_id="T1", run_id="R1", agent_version="1.0.0")
st.persist("S01_freeze_inputs", status="ok")
st.persist("S04_run_baseline", status="ok")
last = StateStore.read(run / "state.json")
check("state.json 已写", last is not None)
check("记录最后阶段", last and last.get("_persisted_stage") == "S04_run_baseline",
      str(last.get("_persisted_stage") if last else None))
check("含时间戳", bool(last and last.get("_persisted_at_str")))
check("last_stage 可读", StateStore.last_stage(run / "state.json") == "S04_run_baseline")
check("无遗言时返回'无痕迹'", StateStore.last_stage(tmp / "nope.json") == "(无痕迹)")
check("历史文件已追加", (run / "state_history.jsonl").exists())
st.mark_interrupted("引擎卡死", stage="S05")
after = StateStore.read(run / "state.json")
check("可标记 interrupted", after and after.get("status") == STATUS_INTERRUPTED)
check("interrupted 带退出码",
      after and after.get("exit_code") == EXIT_INTERRUPTED)

print()
print("=" * 74)
print("3) 崩溃可观测 crashlog")
print("=" * 74)
crash_path = tmp / "crash.log"
crashlog.reset_for_tests()
check("install 成功", crashlog.install(str(crash_path), 4096) is True)
check("install 幂等", crashlog.install(str(crash_path), 4096) is True)

# 捕获一个真实异常
try:
    raise ValueError("测试异常")
except ValueError:
    crashlog.record(*sys.exc_info(), kind="manual", extra={"stage": "S03"})
text = crash_path.read_text(encoding="utf-8", errors="replace")
check("已落盘", crash_path.exists() and crash_path.stat().st_size > 0)
check("含异常类型", "ValueError" in text)
check("含异常信息", "测试异常" in text)
check("含 traceback", "Traceback" in text or "File" in text)
check("含 extra", "S03" in text)
diag = crashlog.diagnostics()
check("诊断含 records_written", diag["records_written"] >= 1)
check("诊断声明局限", "段错误" in diag["limitation"])

# 钩子必须链回原钩子（绝不吞异常）
called = {"n": 0}
orig = sys.excepthook


def spy(*a):
    called["n"] += 1


crashlog._state["prev_hooks"]["excepthook"] = spy
sys.excepthook(sys.exc_info()[0] or ValueError, ValueError("x"), None)
check("原钩子被调用（不吞异常）", called["n"] == 1, f"called={called['n']}")
sys.excepthook = orig

# 轮转
crashlog.reset_for_tests()
big = tmp / "big.log"
crashlog.install(str(big), 500)
for i in range(30):
    crashlog.record_text("X" * 100, kind="watchdog", extra={"i": i})
check("超限后轮转出 .1", (tmp / "big.log.1").exists())
check("write_errors 有计数", crashlog.diagnostics()["write_errors"] >= 0)
crashlog.reset_for_tests()

print()
print("=" * 74)
print("4) 看门狗 watchdog")
print("=" * 74)
watchdog.reset_for_tests()

# 无重启者时应拒绝安装（默认 require_supervisor=True）
wb = watchdog.install(require_supervisor=True, supervisor_present=False,
                      history_path=str(tmp / "wd.json"))
check("无重启者时拒绝安装", wb is False)
wb2 = watchdog.install(require_supervisor=False,
                       history_path=str(tmp / "wd.json"))
check("显式放弃该要求可安装", wb2 is True)


class HealthyAdapter:
    def probe(self, *, timeout_s=15.0):
        return True


class DeadAdapter:
    def probe(self, *, timeout_s=15.0):
        return False


class NoProbeAdapter:
    pass


exits = []
watchdog.set_exit_hook(lambda code, reason: exits.append((code, reason)))

r = watchdog.note_engine_failure(HealthyAdapter(), "slow case")
check("健康 -> kept-running", r["action"] == "kept-running", str(r))
check("健康时不退出", not exits)

r = watchdog.note_engine_failure(None, "no adapter")
check("无法判定 -> unavailable", r["action"] == "unavailable", str(r))
check("无法判定时不退出", not exits)

r = watchdog.note_engine_failure(NoProbeAdapter(), "shape only")
check("无 probe 接口 -> unavailable", r["action"] == "unavailable", str(r))
check("无 probe 接口时不退出", not exits)

# 错误码过滤：业务失败不应触发复检
check("TIMEOUT 应触发复检", watchdog.is_reportable_error(ERR_DSS_TIMEOUT) is True)
check("输入阻断不触发复检",
      watchdog.is_reportable_error(ERR_SCHEMA_MISSING_FIELD) is False)
check("空码不触发复检", watchdog.is_reportable_error(None) is False)
check("空码不触发复检('')", watchdog.is_reportable_error("") is False)

# 死引擎 -> 触发重启
watchdog.reset_for_tests()
exits.clear()
watchdog.install(require_supervisor=False, history_path=str(tmp / "wd2.json"))
r = watchdog.note_engine_failure(DeadAdapter(), "engine hung")
check("死引擎 -> restart", r["action"] == "restart", str(r))
check("触发退出", len(exits) == 1, f"exits={exits}")
check("退出码为 70", exits and exits[0][0] == WATCHDOG_EXIT_CODE)

# 熔断：连续触发达到上限后不再退出
watchdog.reset_for_tests()
exits.clear()
watchdog.install(require_supervisor=False, history_path=str(tmp / "wd3.json"))
d1 = watchdog.trigger_restart("r1")
d2 = watchdog.trigger_restart("r2")
d3 = watchdog.trigger_restart("r3")
check("前两次退出", d1["exited"] is True and d2["exited"] is True)
check("第三次被熔断", d3["exited"] is False and d3["breaker_open"] is True,
      str(d3))
check("退出次数为 2", len(exits) == 2, f"exits={len(exits)}")
dg = watchdog.diagnostics()
check("诊断含熔断状态", dg["breaker_open"] is True)
check("诊断含真实参数", dg["params"]["breaker_max"] == 2
      and dg["params"]["exit_code"] == WATCHDOG_EXIT_CODE)

# 任务租约
print()
print("=" * 74)
print("5) 任务租约（兜底判据的接缝）")
print("=" * 74)
watchdog.release_task()
info = watchdog.current_task_lock()
check("未持锁时不动作", info["locked"] is False)
watchdog.acquire_task("TASK-X")
info = watchdog.current_task_lock()
check("登记后可查询", info["locked"] is True and info["task_id"] == "TASK-X")
check("含起始时间", isinstance(info["since"], float))
watchdog.release_task()
check("释放后清空", watchdog.current_task_lock()["locked"] is False)

# 卡死检测：把 since 拨到过去
# 必须换一个**全新的历史文件**：section 4 已把熔断打开，而熔断是**跨进程**
# 存在的（真实重启发生在进程之间），reset_for_tests 刻意不重置它。
# 这不是 bug 而是设计——测试必须自己隔离，否则会误判。
exits.clear()
watchdog.reset_for_tests()
watchdog.install(require_supervisor=False,
                 history_path=str(tmp / "wd_stuck.json"))
watchdog.set_exit_hook(lambda code, reason: exits.append((code, reason)))
watchdog.acquire_task("STUCK")
watchdog._current_task["since"] = time.time() - 9999
watchdog._check_once()
check("超阈值 -> 触发重启", len(exits) == 1, f"exits={len(exits)}")
check("熔断未开时确实退出",
      watchdog.diagnostics()["breaker_open"] is False
      or len(exits) == 1, f"exits={len(exits)}")
watchdog.release_task()
exits.clear()
watchdog._check_once()
check("无任务时不误判", not exits)
watchdog._current_task.update({"locked": True, "task_id": "T",
                               "since": None})
exits.clear()
watchdog._check_once()
check("信息残缺时不误判", not exits)
watchdog.release_task()
watchdog.set_exit_hook(None)

# 探针永不抛出
print()
print("=" * 74)
print("6) 探针永不抛出（自愈机制不能反过来搞垮任务）")
print("=" * 74)


class ExplodingAdapter:
    def probe(self, *, timeout_s=15.0):
        raise SystemExit("boom")


watchdog.reset_for_tests()
# 同样需要独立历史文件：熔断是跨进程状态，不随 reset 清空
watchdog.install(require_supervisor=False,
                 history_path=str(tmp / "wd_explode.json"))
exits.clear()
watchdog.set_exit_hook(lambda code, reason: exits.append((code, reason)))
try:
    r = watchdog.note_engine_failure(ExplodingAdapter(), "x")
    check("探针异常被接住且判为不健康",
          r["action"] in ("restart", "breaker-open"), str(r))
    check("探针抛异常应触发退出", len(exits) == 1, f"exits={exits}")
except BaseException as exc:
    check("探针异常被接住", False, f"穿透了: {type(exc).__name__}: {exc}")
watchdog.set_exit_hook(None)

print()
print("=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
