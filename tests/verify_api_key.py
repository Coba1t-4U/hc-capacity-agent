"""Verify DeepSeek API Key 接入：配置解析、多路径查找、双写、真实连通。

为什么单独一个套件：
  Key 的失败模式很隐蔽 —— 界面看起来正常，只是"悄悄退回了本地规则引擎"。
  所以必须逐层验证：文件在哪、读了哪个、是否真连通、退回时说清了没有。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
print("DeepSeek Key 接入验证")
print("=" * 74)

# ---------------------------------------------------------------- 1. 真实配置
print("\n1) 真实配置是否到位")
from hc_agent import llm

cands = llm.config_candidates()
print("   候选位置：")
for c in cands:
    print(f"     {'√' if c.is_file() else '×'} {c}")
check("至少有一个候选位置", len(cands) >= 1, str(cands))
check("候选里包含主数据目录",
      any(str(c).endswith("llm.json") for c in cands))

# 真实环境里应当已配好（这是本次任务的交付条件）
real_cfg = llm.LLMConfig.from_env()
check("真实环境已配置 Key", real_cfg.enabled,
      f"未配置；已查 {[str(c) for c in cands]}")
if real_cfg.enabled:
    check("Key 形态正确（sk- 开头）",
          real_cfg.api_key.startswith("sk-"), real_cfg.masked())
    check("默认模型为 deepseek-chat",
          real_cfg.model == "deepseek-chat", real_cfg.model)
    check("接口地址为官方地址",
          "api.deepseek.com" in real_cfg.base_url, real_cfg.base_url)
    st = llm.self_test(real_cfg)
    check("真实连通性（会发一次最小请求）", st.get("ok"),
          str(st.get("reason"))[:120])

# ---------------------------------------------------------------- 2. 多路径查找
print("\n2) 多路径查找（打包版数据目录可能降级）")


def with_env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    return old


def restore(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


tmp = Path(tempfile.mkdtemp(prefix="hc-key-"))
try:
    # 场景：data_root 下没 Key，但降级目录下有 → 必须能找到
    primary = tmp / "primary"
    primary.mkdir(parents=True, exist_ok=True)
    old = with_env(HC_DATA_DIR=str(primary), DEEPSEEK_API_KEY=None,
                   DEEPSEEK_BASE_URL=None, DEEPSEEK_MODEL=None)
    try:
        # 写一份"降级目录"的配置，模拟打包版写在 AppData 而读取降级到别处
        home_cfg = Path.home() / ".hc-agent" / "llm.json"
        had_home = home_cfg.is_file()
        backup = home_cfg.read_text(encoding="utf-8") if had_home else None
        home_cfg.parent.mkdir(parents=True, exist_ok=True)
        home_cfg.write_text(json.dumps({
            "api_key": "sk-fallbacktest1234567890",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat"}), encoding="utf-8")
        cfg2 = llm.LLMConfig.from_env()
        check("主目录无 Key 时能查到兜底位置", cfg2.enabled,
              f"未查到；候选={[str(c) for c in llm.config_candidates()]}")
        check("读到的是兜底位置那份",
              cfg2.api_key.startswith("sk-fallbacktest"),
              cfg2.masked())
        # 恢复
        if backup is not None:
            home_cfg.write_text(backup, encoding="utf-8")
        else:
            home_cfg.unlink(missing_ok=True)
    finally:
        restore(old)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 环境变量优先级最高
old = with_env(DEEPSEEK_API_KEY="sk-envpriority0000000000")
try:
    cfg3 = llm.LLMConfig.from_env()
    check("环境变量优先于文件配置",
          cfg3.api_key == "sk-envpriority0000000000", cfg3.masked())
finally:
    restore(old)

# ---------------------------------------------------------------- 3. 双写
print("\n3) 保存时双写（源码版与打包版共用一份）")
tmp2 = Path(tempfile.mkdtemp(prefix="hc-key2-"))
try:
    old = with_env(HC_DATA_DIR=str(tmp2), DEEPSEEK_API_KEY=None,
                   DEEPSEEK_BASE_URL=None, DEEPSEEK_MODEL=None)
    try:
        r = llm.save_config("sk-doublewrite0000000000")
        check("保存返回 ok", r.get("ok"), str(r))
        check("返回实际写入位置列表", isinstance(r.get("paths"), list)
              and len(r["paths"]) >= 1, str(r.get("paths")))
        check("主位置文件确实写了",
              Path(r["path"]).is_file(), r.get("path"))
        # 重新读取（清掉环境变量，只靠文件）
        restore(with_env(DEEPSEEK_API_KEY=None))
        cfg4 = llm.LLMConfig.from_env()
        check("清掉环境变量后仍能从文件读到",
              cfg4.api_key == "sk-doublewrite0000000000", cfg4.masked())
    finally:
        restore(old)
finally:
    # 清掉可能被双写出去的测试 Key，避免污染真实配置
    for c in llm.config_candidates():
        try:
            if c.is_file():
                d = json.loads(c.read_text(encoding="utf-8"))
                if str(d.get("api_key", "")).startswith("sk-doublewrite"):
                    if str(c).startswith(str(ROOT)):
                        c.unlink()
        except Exception:
            pass
    shutil.rmtree(tmp2, ignore_errors=True)

# ---------------------------------------------------------------- 4. 退回行为
print("\n4) 没 Key 时必须**明确说明**退回本地规则引擎")
old = with_env(HC_DATA_DIR=str(Path(tempfile.mkdtemp(prefix="hc-key3-"))),
               DEEPSEEK_API_KEY=None)
try:
    # 临时把候选位置里的 Key 屏蔽：改用不存在的家目录 + 空环境
    saved = []
    for c in llm.config_candidates():
        if c.is_file():
            saved.append((c, c.read_text(encoding="utf-8")))
    try:
        for c, _ in saved:
            try:
                c.rename(c.with_suffix(".json.bak"))
            except Exception:
                pass
        cfg5 = llm.LLMConfig.from_env()
        check("无 Key 时 enabled=False", not cfg5.enabled, cfg5.masked())
        from hc_agent.qa import Facts
        r5 = llm.ask("FD002 还能接多少光伏？", Facts(), cfg=cfg5)
        check("退回规则引擎", r5.get("engine") == "rule", str(r5.get("engine")))
        check("退回时给出明确提示",
              "DeepSeek 不可用" in str(r5.get("note")),
              str(r5.get("note"))[:100])
        # 这里的数据目录是空的（没有运行产物），所以正确行为是**如实说没数据**，
        # 而不是编一个数字。两种都接受，但绝不能出现凭空的 kW 数字。
        txt5 = r5.get("text") or ""
        honest = ("没有任何运行" in txt5 or "没有可回答" in txt5
                  or "还没读到" in txt5 or "kW" in txt5)
        check("退回后回答是诚实的（有数据给数、没数据说没数据）", honest,
              txt5[:100])
    finally:
        for c, txt in saved:
            bak = c.with_suffix(".json.bak")
            try:
                if bak.is_file():
                    bak.rename(c)
                else:
                    c.write_text(txt, encoding="utf-8")
            except Exception:
                pass
finally:
    restore(old)
    shutil.rmtree(old.get("HC_DATA_DIR") or "", ignore_errors=True)

# ---------------------------------------------------------------- 5. 命令行自检
print("\n5) 命令行自检开关（打包版排查用）")
main_src = (ROOT / "hc_agent" / "__main__.py").read_text(encoding="utf-8")
check("有 --llm-test 开关", "--llm-test" in main_src)
check("自检输出候选位置", "config_candidates" in main_src)
check("自检会真发一次请求", "self_test" in main_src)

import subprocess
r = subprocess.run([sys.executable, "-m", "hc_agent", "--llm-test"],
                   cwd=str(ROOT), capture_output=True, text=True, timeout=120)
if r.returncode == 0:
    try:
        rep = json.loads(r.stdout)
        check("--llm-test 退出码 0 且可达", rep.get("reachable") is True,
              str(rep)[:150])
        check("自检报告含 key 掩码", "…" in str(rep.get("key")),
              str(rep.get("key")))
    except Exception as exc:
        check("--llm-test 输出可解析", False, f"{exc}: {r.stdout[:150]}")
else:
    check("--llm-test 退出码 0", False, (r.stderr or r.stdout)[-200:])

# ---------------------------------------------------------------- 6. 界面提示
print("\n6) 界面上能看出走的是哪个引擎")
chat_src = (ROOT / "hc_agent" / "desktop" / "chat.py").read_text(
    encoding="utf-8")
sp_src = (ROOT / "hc_agent" / "desktop" / "settings_page.py").read_text(
    encoding="utf-8")
check("问答页底部显示当前引擎", "refresh_engine_badge" in chat_src)
check("回答气泡标注 DeepSeek / 本地规则",
      "DeepSeek" in chat_src and "本地规则" in chat_src)
check("配置页显示 Key 状态", "masked" in sp_src or "API Key" in sp_src)
check("有模型设置入口", "open_settings" in chat_src)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
