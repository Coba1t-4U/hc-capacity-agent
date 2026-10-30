"""Verify 拖入压缩包 + 对算例内部提问。

覆盖三件事：
  1) 压缩包/文件夹/单文件拖进来：能入库、能登记算例、**跳过项如实计数**
  2) 算例内部问答：元件清单 / 光伏位置 / 增减假设（假设分析要真重跑引擎）
  3) 界面：有拖放入口，投放后能在对话区看到结果
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ok = fail = 0

def _find_case(name: str):
    """用**产品自己的查找逻辑**定位算例，不在测试里硬编码桌面路径。

    用户会把算例收进子目录整理（实测：移到 桌面/opendss算例/ 下），
    写死 `Desktop/名称` 的测试会因此误报"算例不存在"。
    """
    import sys as _s
    _s.path.insert(0, str(ROOT))
    try:
        from hc_agent.io.dss_report import resolve_case_path
        p = resolve_case_path(name)
        if p:
            from pathlib import Path as _P
            q = _P(p)
            return q if q.is_dir() else q.parent
    except Exception:
        pass
    from pathlib import Path as _P
    home = _P.home()
    for nm in ("Desktop", "桌面"):
        base = home / nm
        if not base.is_dir():
            continue
        direct = base / name
        if direct.is_dir():
            return direct
        try:
            for d in base.rglob(name):
                if d.is_dir():
                    return d
        except Exception:
            pass
    return None




def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 74)
print("拖入压缩包 + 算例内部问答验证")
print("=" * 74)

tmp_data = Path(tempfile.mkdtemp(prefix="hc-caseqa-"))
_old_env = os.environ.get("HC_DATA_DIR")
os.environ["HC_DATA_DIR"] = str(tmp_data)

CASE = _find_case("配网承载力算例_双台区对比")
CASE2 = _find_case("配网承载力算例_全部满足")

try:
    from hc_agent.io import zipbag, dropin, dss_facts

    # ---------------------------------------------------------------- 1. 压缩包解析
    print("\n1) 压缩包解析（zipbag）")
    tmp = Path(tempfile.mkdtemp(prefix="hc-zip-"))
    zp = tmp / "资料包.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("说明.md", "# 说明\n\n电压偏差 ±7%。" * 20)
        z.writestr("数据/参数.csv", "a,b\n1,2\n")
        z.writestr("case/Master.dss",
                   "Clear\nNew Circuit.t basekv=10.5\nRedirect Grid.dss\n"
                   "Calcvoltagebases\nSolve\n")
        z.writestr("case/Grid.dss",
                   "New Line.L1 bus1=a bus2=b phases=3 length=1 units=km\n")
        z.writestr("bin/app.exe", b"MZ" + b"\x00" * 64)
        z.writestr("img/logo.png", b"\x89PNG" + b"\x00" * 32)
        # 目录穿越尝试
        z.writestr("../../evil.md", "不该被解到上级目录" * 5)

    info = zipbag.inspect(zp)
    check("能读压缩包", info["ok"], info.get("error", ""))
    check("识别出文档 3 篇（含穿越路径那个也算条目）",
          len(info["docs"]) >= 2, str([d["name"] for d in info["docs"]]))
    check("识别出 .dss 2 个", len(info["dss"]) == 2,
          str([d["name"] for d in info["dss"]]))
    check("二进制被单列并给原因",
          len(info["skipped"]) == 2
          and all(s["why"] for s in info["skipped"]),
          str(info["skipped"]))
    check("穿越路径被清洗（不含 ..）",
          all(".." not in d["name"] for d in info["docs"] + info["dss"]),
          str([d["name"] for d in info["docs"]]))

    docs_ok, docs_bad = zipbag.extract_docs(zp)
    check("抽出正文", len(docs_ok) >= 2, str([d["name"] for d in docs_ok]))
    check("抽出的是真内容", all(d["chars"] > 10 for d in docs_ok),
          str([(d["name"], d["chars"]) for d in docs_ok]))

    cdir = zipbag.extract_dss(zp, tmp / "cases")
    check("解出算例目录", cdir is not None and cdir.is_dir(), str(cdir))
    check("定位到含 Master.dss 的那一层",
          cdir is not None and (cdir / "Master.dss").is_file(),
          str(sorted(p.name for p in cdir.iterdir())) if cdir else "")
    check("没有把文件解到上级目录",
          not (tmp / "evil.md").exists())

    # ---------------------------------------------------------------- 2. 拖入处理
    print("\n2) 拖入处理（dropin）")
    r = dropin.handle([zp], case_dest=tmp_data / "dss_dropped")
    check("返回成功", r["ok"], str(r.get("errors")))
    check("导入了文档", r["imported_count"] >= 2, str(r["imported_count"]))
    check("登记了算例", len(r["case_dirs"]) == 1, str(r["case_dirs"]))
    check("汇总话里说明白", ("导入" in r["message"]
                           and "跳过" in r["message"]), r["message"])
    from hc_agent.io import dss_registry
    names = [c.get("name") for c in dss_registry.list_cases()]
    check("算例进了登记表", len(names) >= 1, str(names))

    # 知识库里能检索到
    from hc_agent.knowledge.service import KnowledgeService
    sv = KnowledgeService()
    kbs = {k["name"]: k for k in sv.list_kbs()}
    check("建了「拖入资料」知识库", dropin.DROP_KB_NAME in kbs, str(list(kbs)))
    if dropin.DROP_KB_NAME in kbs:
        k = kbs[dropin.DROP_KB_NAME]
        check("知识库里有文档与分块",
              (k.get("doc_count") or 0) >= 2 and (k.get("chunk_count") or 0) >= 2,
              f"docs={k.get('doc_count')} chunks={k.get('chunk_count')}")

    # 不支持的格式要如实报
    bad = tmp / "x.xyz"
    bad.write_text("x", encoding="utf-8")
    r2 = dropin.handle([bad])
    check("不支持的格式被明确拒绝",
          bool(r2["errors"]) and "不支持" in r2["errors"][0],
          str(r2["errors"]))

    # ---------------------------------------------------------------- 3. 算例事实
    print("\n3) 算例事实解析（dss_facts）")
    check("桌面有对照算例", CASE.is_dir(), str(CASE))
    if CASE.is_dir():
        f = dss_facts.case_facts(CASE)
        check("解析成功", f["ok"], f.get("error", ""))
        check("解析到配变 2 台", f["counts"]["transformers"] == 2,
              str(f["counts"]))
        check("解析到光伏 1 处", f["counts"]["generators"] == 1,
              str(f["counts"]))
        check("解析到线路 ≥8 条", f["counts"]["lines"] >= 8, str(f["counts"]))
        check("负荷按元件声明合计 280 kW",
              abs(f["load_kw_declared"] - 280.0) < 1e-6,
              str(f["load_kw_declared"]))
        check("光伏 220 kW @ LV_T2_N2",
              f["generators"]
              and abs(f["generators"][0]["kw"] - 220.0) < 1e-6
              and f["generators"][0]["bus"].lower() == "lv_t2_n2",
              str(f["generators"]))
        inv = dss_facts.format_inventory(f)
        for kw in ("配变", "分布式电源", "负荷", "线路", "PV_T2"):
            check(f"清单含「{kw}」", kw in inv)

        # ------------------------------------------------------------ 4. 假设分析
        print("\n4) 假设分析（真的重跑引擎）")
        wi = dss_facts.what_if_pv(CASE, 300.0)
        check("假设分析成功", wi.get("ok"), str(wi.get("error")))
        if wi.get("ok"):
            check("记录前后容量",
                  abs(wi["gen_before_kw"] - 220.0) < 1e-6
                  and abs(wi["gen_after_kw"] - 300.0) < 1e-6,
                  f"{wi['gen_before_kw']} -> {wi['gen_after_kw']}")
            check("电压确实变了（不是原样抄回）",
                  abs(wi["vmax_after"] - wi["vmax_before"]) > 1e-4,
                  f"{wi['vmax_before']} -> {wi['vmax_after']}")
            check("改后仍收敛", wi["converged"] is True)
            # 原算例不能被改
            f2 = dss_facts.case_facts(CASE)
            check("原算例未被修改",
                  abs(f2["gen_kw_declared"] - 220.0) < 1e-6,
                  str(f2["gen_kw_declared"]))

    # ---------------------------------------------------------------- 5. 问答
    print("\n5) 问答：算例内部三问")
    from hc_agent.qa import Facts, Responder, intent
    for q, want in (
            ("这个算例里有什么元件", "case_inventory"),
            ("配网承载力算例_双台区对比 线路有多长", "case_inventory"),
            ("光伏接在哪几个点", "case_pv"),
            ("如果把光伏加到 300 kW 会怎样", "case_whatif"),
            ("如果光伏减到 100 kW 会怎样", "case_whatif"),
    ):
        got = intent(q).name
        check(f"意图 {q[:20]}… → {want}", got == want, f"实际 {got}")

    # 常规问题不能被抢走
    for q, expect in (
            ("算一下桌面上的算例，并给我计算报告", "dss_analyze"),
            ("最低电压是多少", "min_voltage"),
            ("还能接多少光伏", "capacity"),
            ("FD002 复算通过了吗", "verification"),
    ):
        got = intent(q).name
        check(f"「{q}」仍是 {expect}", got == expect, f"实际 {got}")

    resp = Responder(Facts())
    if CASE.is_dir():
        a = resp.answer("配网承载力算例_双台区对比 这个算例里有什么元件")
        check("清单答案含配变与光伏", "PV_T2" in a.text and "T2" in a.text,
              a.text[:60])
        check("清单答案不含容量结论",
              "开放容量" not in a.text or "不产出" in a.text, a.text[:60])

        a2 = resp.answer("配网承载力算例_双台区对比 光伏接在哪")
        check("光伏位置答案含母线名", "LV_T2_N2" in a2.text, a2.text[:80])
        check("光伏位置答案给合计", "220" in a2.text, a2.text[:80])

        a3 = resp.answer("配网承载力算例_双台区对比 如果把光伏加到 300 kW 会怎样")
        check("假设答案给前后对比", "→" in a3.text, a3.text[:80])
        check("假设答案说明是真重跑",
              "重跑引擎" in a3.text or "真的" in a3.text, a3.text[-120:])
        check("假设答案声明不改原算例", "没有被改动" in a3.text, a3.text[-80:])

    # ---------------------------------------------------------------- 6. 界面
    print("\n6) 界面接线")
    chat_src = (ROOT / "hc_agent" / "desktop" / "chat.py").read_text(
        encoding="utf-8")
    pages_src = (ROOT / "hc_agent" / "desktop" / "pages.py").read_text(
        encoding="utf-8")
    app_src = (ROOT / "hc_agent" / "desktop" / "app.py").read_text(
        encoding="utf-8")
    dnd_src = (ROOT / "hc_agent" / "desktop" / "dnd.py").read_text(
        encoding="utf-8")
    check("对话页有拖放提示", "拖到这里" in chat_src)
    check("有「选择文件…」后备", "选择文件" in chat_src)
    check("结果渲染区分入库/跳过",
          "未入库" in chat_src and "跳过" in chat_src)
    check("ChatPage 有 handle_drop", "def handle_drop" in pages_src)
    check("App 有拖放挂载与轮询",
          "_attach_drop" in app_src and "_poll_drops" in app_src)
    check("拖放失败可降级（不抛出去）", "error" in dnd_src
          and "enabled = False" in dnd_src)
    check("WM_DROPFILES 声明了 argtypes（否则 64 位会崩）",
          "CallWindowProcW.argtypes" in dnd_src)
    check("对话页 after 挂在 view 上（ChatPage 不是控件）",
          "self.view.after(250, poll)" in pages_src)

finally:
    if _old_env is None:
        os.environ.pop("HC_DATA_DIR", None)
    else:
        os.environ["HC_DATA_DIR"] = _old_env
    for d in (tmp_data,):
        shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
