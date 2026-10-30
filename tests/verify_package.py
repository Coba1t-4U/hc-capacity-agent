"""Test the PyInstaller-built binary: engine host protocol + real OpenDSS solve.

This is the only test that matters for packaging -- the binary either loads the
OpenDSS native DLLs from inside the bundle or it does not, and that difference
is invisible until you actually drive a command through the child process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "HC-Agent" / "HC-Agent.exe"

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
print("打包产物验证：HC-Agent.exe")
print("=" * 74)

check("exe 存在", EXE.is_file(), str(EXE))
if not EXE.is_file():
    sys.exit(1)
size = sum(p.stat().st_size for p in (EXE.parent).rglob("*") if p.is_file())
print(f"  产物总大小: {size / 1024 / 1024:.1f} MB")

# ---------------------------------------------------------------- 1. --paths
print("\n1) 只读资源与路径解析（冻结模式）")
r = subprocess.run([str(EXE), "--paths"], capture_output=True, text=True,
                   timeout=120)
try:
    info = json.loads(r.stdout)
except Exception:
    info = {}
check("--paths 返回 JSON", bool(info), (r.stderr or r.stdout)[-300:])
if info:
    check("frozen = true", info.get("frozen") is True, str(info.get("frozen")))
    reg = Path(info.get("skills_registry", ""))
    check("skill_registry.json 随包存在", reg.is_file(), str(reg))
    br = Path(info.get("bundle_root", ""))
    check("bundle_root 在安装目录内", "HC-Agent" in str(br), str(br))
    check("static 随包存在", (br / "hc_agent" / "static" / "index.html").is_file())
    check("rules 随包存在", (br / "rules").is_dir())
    dr = Path(info.get("data_root", ""))
    if os.environ.get("HC_DATA_DIR"):
        # 显式覆盖时按用户指定路径，不再要求"在 AppData 下"
        check("数据目录遵循 HC_DATA_DIR", str(dr) == str(
            Path(os.environ["HC_DATA_DIR"]).resolve()), str(dr))
    else:
        check("数据目录不在程序目录内（可写、可升级）",
              dr != br and "dist" not in str(dr), f"data={dr} bundle={br}")

# ---------------------------------------------------------------- 2. 引擎宿主
print("\n2) 引擎宿主（子进程协议 + 原生 DLL 加载）")
p = subprocess.Popen([str(EXE), "--engine-host"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, bufsize=1,
                     encoding="utf-8", errors="replace",
                     cwd=str(EXE.parent))
try:
    t0 = time.time()
    first = p.stdout.readline()
    boot = time.time() - t0
    try:
        ready = json.loads(first)
    except Exception:
        ready = {}
    check("上报 ready", ready.get("kind") == "ready", first[:200])
    check("就绪耗时 < 60s", boot < 60, f"{boot:.1f}s")
    chan = ready.get("channel") or ""
    check("选中了引擎通道", bool(chan), chan)

    def rpc(cmd, **args):
        """发一条请求并按 tag 取回应，**跳过 progress 等中间消息**。

        子进程在 run_task 期间会持续推 ``{"kind":"progress"}``；只读一行就
        当成最终应答是错的（会把进度行误判为结果，随后 readline 错位）。
        """
        p.stdin.write(json.dumps({"cmd": cmd, "args": args, "tag": cmd}) + "\n")
        p.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < 300:
            line = p.stdout.readline()
            if not line:
                return {"ok": False, "error": "子进程提前关闭 stdout"}
            try:
                msg = json.loads(line)
            except Exception:
                continue          # 非协议行（不该有，由 stdout 纪律保证）
            if msg.get("kind") == "progress":
                continue
            if msg.get("tag") == cmd:
                return msg
        return {"ok": False, "error": f"{cmd} 超时"}

    r = rpc("ping")
    check("ping 应答", r.get("ok") is True, str(r)[:150])

    r = rpc("channels")
    chans = (r.get("channels") or {})
    avail = [k for k, v in chans.items() if v.get("available")]
    check("引擎通道探测可用", r.get("ok") is True and bool(avail), str(avail))

    # 最关键：真正跑一次 OpenDSS，证明 dss_capi.dll 能从包里加载
    r = rpc("run_task",
            input_dir=str(ROOT / "tests" / "golden" / "FD002"),
            output_dir=str(Path(info.get("runs_root", ROOT / "runs")) / "PKG-TEST"))
    check("run_task 成功返回", r.get("ok") is True, str(r)[:300])
    oc = (r.get("outcome") or {})
    if r.get("ok"):
        cap = oc.get("capacity_physical_kw")
        check("真正算出了容量（引擎可用）", cap is not None, str(oc)[:300])
        check("容量值正确（333.984 kW）",
              cap is not None and abs(float(cap) - 333.984375) < 0.01,
              str(cap))
        check("退出码 5（门禁未满足，符合预期）",
              oc.get("exit_code") == 5, str(oc.get("exit_code")))

    rpc("shutdown")
    p.wait(timeout=20)
    check("干净退出", p.returncode == 0, f"rc={p.returncode}")
finally:
    try:
        if p.poll() is None:
            p.kill()
    except Exception:
        pass
    err = p.stderr.read()
if err.strip():
    tail = "\n".join(err.strip().splitlines()[-6:])
    print(f"  (stderr 末尾)\n{tail}")

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
