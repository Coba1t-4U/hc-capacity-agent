"""Verify the OpenDSS case workflow: 算例 → 问答计算 → 报告 → 打开。

覆盖：
  * 桌面算例本体有效（能编译、能收敛、数字符合预期）
  * 分析结果正确（电压/过载/反向潮流/网损口径）
  * 报告生成（docx + manifest + summary）且内容包含口径声明
  * 用户"添加算例"的登记表：增/查/去重/指代/删除
  * 路径解析优先级（直接路径 → 桌面默认 → 登记表）
  * 问答意图与回答（含 report_path）
  * LLM 工具 analyze_opendss_case
  * 界面接线（添加按钮、报告按钮、打开白名单）
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
print("OpenDSS 算例工作流验证")
print("=" * 74)

CASE = _find_case("配网承载力算例_光伏高渗透") or (Path.home() / "Desktop" / "配网承载力算例_光伏高渗透")
if not CASE.is_dir():
    CASE = Path.home() / "桌面" / "配网承载力算例_光伏高渗透"

# 用独立数据目录，避免污染真实登记表
tmp = Path(tempfile.mkdtemp(prefix="hc-dss-"))
_old = os.environ.get("HC_DATA_DIR")
os.environ["HC_DATA_DIR"] = str(tmp)

try:
    from hc_agent.io import dss_case, dss_registry

    # ---------------------------------------------------------------- 1. 算例本体
    print("\n1) 桌面算例本体")
    check("算例目录存在", CASE.is_dir(), str(CASE))
    dss_files = sorted(CASE.glob("*.dss"))
    check("包含 Master.dss", (CASE / "Master.dss").is_file())
    check(".dss 文件 ≥ 5 个", len(dss_files) >= 5,
          str([p.name for p in dss_files]))
    check("有说明文档", (CASE / "说明.md").is_file())
    check("PV 参数文件存在", (CASE / "PV.dss").is_file())
    pv_txt = (CASE / "PV.dss").read_text(encoding="utf-8")
    check("PV.dss 记录了容量标定", "标定" in pv_txt and "200 kW" in pv_txt,
          "缺少标定说明")

    # ---------------------------------------------------------------- 2. 分析结果
    print("\n2) 分析结果（数字必须与标定一致）")
    a = dss_case.analyze(CASE)
    check("能编译并求解", a.converged, "未收敛")
    check("母线 12 个", a.bus_count == 12, f"{a.bus_count}")
    check("支路 12 条", len(a.branches) == 12, f"{len(a.branches)}")
    check("算例名取目录名",
          a.source_name == CASE.name, a.source_name)
    vmax = a.best_voltage()
    vmin = a.worst_voltage()
    check("最高电压 ≈1.1366 @ lv_t2_n2",
          vmax is not None and abs(vmax.vmin_pu - 1.1366) < 5e-3
          and vmax.name.lower() == "lv_t2_n2",
          f"{vmax.vmin_pu if vmax else None} @ {vmax.name if vmax else None}")
    check("最低电压 ≈1.0084 @ lv_t1_n1",
          vmin is not None and abs(vmin.vmin_pu - 1.0084) < 5e-3,
          f"{vmin.vmin_pu if vmin else None}")
    # 判据统一为 0.90~1.10（规则包批准值）后，只有 LV_T2_N2 真正越上限；
    # LV_T2_N1 的 1.082 在 0.90~1.10 内，不越限。
    viol = a.voltage_violations()
    check("电压越限 1 个母线（判据 0.90~1.10）", len(viol) == 1,
          str([(b.name, round(b.vmin_pu, 4)) for b in viol]))
    check("越限的是 LV_T2_N2",
          bool(viol) and viol[0].name.lower() == "lv_t2_n2",
          str([b.name for b in viol]))
    check("越限判据默认值就是 0.90/1.10",
          (a.DEFAULT_VMIN, a.DEFAULT_VMAX) == (0.90, 1.10),
          str((a.DEFAULT_VMIN, a.DEFAULT_VMAX)))
    ov = a.overloaded()
    check("过载 1 个元件（T2）",
          len(ov) == 1 and ov[0].name.lower() == "t2",
          str([(b.name, b.loading_pct) for b in ov]))
    check("反向潮流：源侧为正", a.source_kw > 0, f"{a.source_kw}")
    check("分布式电源出力 220 kW", abs(a.total_gen_kw - 220) < 0.5,
          f"{a.total_gen_kw}")
    check("负荷 85 kW", abs(a.total_load_kw - 85) < 0.5,
          f"{a.total_load_kw}")
    # 网损必须按能量平衡，不能是 |源侧|-负荷
    balanced = abs(a.losses_kw - (-a.source_kw + a.total_gen_kw
                                  - a.total_load_kw)) < 1e-3
    check("网损按能量平衡计算", balanced, f"{a.losses_kw}")
    check("网损率合理（5%~40%）", 5 < a.losses_pct < 40, f"{a.losses_pct}")
    # 三处判据必须一致（页面/报告/引擎）—— 曾经页面用 0.95~1.05、
    # 报告用 0.95~1.10、引擎用 0.95~1.05，同一次分析会给出不同的越限个数。
    import inspect as _insp
    from hc_agent.io import dss_report as _rep
    sig = _insp.signature(_rep.findings)
    check("报告与引擎的电压判据一致",
          sig.parameters["vmin"].default == a.DEFAULT_VMIN
          and sig.parameters["vmax"].default == a.DEFAULT_VMAX,
          str({k: v.default for k, v in sig.parameters.items()
               if k in ("vmin", "vmax")}))
    check("声明不产出开放容量",
          "不产出开放容量" in str(a.capacity_note), a.capacity_note[:60])

    # ---------------------------------------------------------------- 3. 报告
    print("\n3) 报告生成")
    from hc_agent.io import dss_report
    r = dss_report.analyze_and_report(str(CASE))
    check("分析+出报告成功", r.get("ok"), str(r)[:120])
    rep = Path(r.get("report_path") or "")
    check("报告文件存在", rep.is_file(), str(rep))
    check("报告是 docx 或 md", rep.suffix.lower() in (".docx", ".md"),
          rep.suffix)
    if rep.is_file():
        check("报告非空（>10KB）", rep.stat().st_size > 10_000,
              f"{rep.stat().st_size} 字节")
    man = Path(r.get("manifest_path") or "")
    check("清单存在", man.is_file(), str(man))
    if man.is_file():
        m = json.loads(man.read_text(encoding="utf-8"))
        check("清单含报告路径与哈希",
              bool(m.get("report_path")) and bool(m.get("report_hash")),
              str(list(m)[:4]))
        check("清单含章节说明", len(m.get("sections") or []) >= 5,
              str(len(m.get("sections") or [])))
    summ = r.get("summary") or {}
    check("摘要含结论要点", len(summ.get("findings") or []) >= 3,
          str(len(summ.get("findings") or [])))
    check("摘要标注越限母线",
          len(summ.get("voltage_violations") or []) == 1,
          str(len(summ.get("voltage_violations") or [])))
    check("摘要含口径声明",
          "不产出开放容量" in str(summ.get("capacity_note")),
          str(summ.get("capacity_note"))[:60])

    # 报告内容抽查（docx 解压看正文）
    if rep.suffix.lower() == ".docx":
        try:
            import docx
            doc = docx.Document(str(rep))
            text = "\n".join(p.text for p in doc.paragraphs)
            check("报告含口径声明（第1章 任务与口径）",
                  "任务与口径" in text and "不产出开放容量" in text)
            check("报告含功率平衡关系式",
                  "源侧供给" in text or "能量平衡" in text or "网损" in text)
            check("报告含约束校核小结（哪个先到限）",
                  "约束校核小结" in text and ("先到限" in text or "越限" in text))
            check("报告含独立验证节", "独立验证" in text)
            check("报告含 Evidence 节", "Evidence" in text)
            check("报告含限制与后续建议", "限制与后续建议" in text)
            check("报告含表格明细", len(doc.tables) >= 3,
                  f"{len(doc.tables)} 个表")
        except ImportError:
            print("  (跳过 docx 内容抽查：无 python-docx)")

    # ---------------------------------------------------------------- 4. 登记表
    print("\n4) 用户添加算例（登记表）")
    check("初始为空", dss_registry.list_cases() == [],
          str(dss_registry.list_cases()))
    r1 = dss_registry.add_case(CASE)
    check("添加算例成功", r1.get("ok"), str(r1)[:120])
    check("不是重复添加", r1.get("already") is False)
    cases = dss_registry.list_cases()
    check("列表有 1 个", len(cases) == 1, str(len(cases)))
    c0 = cases[0]
    check("记录主文件路径", Path(c0["master"]).is_file(), c0.get("master"))
    check("统计 .dss 文件数（去重后 6）", c0["files"] == 6, str(c0["files"]))
    check("标记含 Master", c0["has_master"] is True)
    check("状态 alive", c0["alive"] is True)

    r2 = dss_registry.add_case(CASE)
    check("重复添加被识别", r2.get("already") is True, str(r2)[:80])
    check("重复添加不新增条目",
          len(dss_registry.list_cases()) == 1,
          str(len(dss_registry.list_cases())))

    check("按名称查找", (dss_registry.find_case(CASE.name) or {}).get("name")
          == CASE.name)
    check("空串取最近添加",
          (dss_registry.find_case("") or {}).get("name") == CASE.name)
    check("找不到时返回 None",
          dss_registry.find_case("完全不存在的算例xyz") is None)

    # 指代说法
    reg = dss_registry.resolve_in_registry("%s" % "我加的算例")
    check("指代「我加的算例」可解析", bool(reg) and Path(reg).is_file(), reg)

    # ---------------------------------------------------------------- 5. 路径解析
    print("\n5) 路径解析优先级")

    def usable(p: str) -> bool:
        """解析结果是否可用于分析：可以是目录或 .dss，最终要能定位到主文件。"""
        if not p or not Path(p).exists():
            return False
        try:
            return dss_case.resolve_master(p).is_file()
        except Exception:
            return False

    check("空串 → 桌面默认或登记",
          usable(dss_report.resolve_case_path("")),
          dss_report.resolve_case_path(""))
    check("算例名 → 命中",
          usable(dss_report.resolve_case_path(CASE.name)),
          dss_report.resolve_case_path(CASE.name))
    check("绝对路径（目录）→ 命中",
          usable(dss_report.resolve_case_path(str(CASE))),
          dss_report.resolve_case_path(str(CASE)))
    check("绝对路径（Master.dss）→ 命中",
          usable(dss_report.resolve_case_path(
              str(CASE / "Master.dss"))))
    check("不存在 → 空串",
          dss_report.resolve_case_path("不存在的算例xyz") == "")
    check("指代 → 命中登记表",
          usable(dss_report.resolve_case_path("我加的算例")),
          dss_report.resolve_case_path("我加的算例"))

    # ---------------------------------------------------------------- 6. 问答
    print("\n6) 问答：直接算并给报告")
    from hc_agent.qa import Facts, Responder, intent
    for q in ("算一下桌面上的 OpenDSS 算例，并给我计算报告",
              "分析一下我加的算例",
              "帮我算一下 " + CASE.name):
        check(f"意图识别：{q[:16]}…", intent(q).name == "dss_analyze",
              intent(q).name)
    # 常规问题不能被算例意图抢走
    check("「电压越限的判据」不被抢走",
          intent("电压越限的判据是什么").name != "dss_analyze")
    check("「FD002 还能接多少」不被抢走",
          intent("FD002 还能接多少光伏").name != "dss_analyze")

    resp = Responder(Facts())
    ans = resp.answer("算一下我加的算例，并给我计算报告")
    txt = ans.text
    check("回答含算例名", CASE.name in txt, txt[:80])
    check("回答含最高电压", "1.1366" in txt or "1.137" in txt, txt[:200])
    check("回答说明越限数量", "电压越限" in txt and "过载" in txt)
    check("回答带口径声明", "不产出开放容量" in txt)
    check("回答带报告路径且文件存在",
          bool(ans.report) and Path(ans.report).is_file(), ans.report)
    d = ans.to_dict()
    check("to_dict 含 report_path", "report_path" in d, str(list(d)))
    check("报告按钮标签合理",
          (d.get("report_label") or "") == "打开计算报告",
          str(d.get("report_label")))

    # ---------------------------------------------------------------- 7. LLM 工具
    print("\n7) LLM 工具 analyze_opendss_case")
    from hc_agent import llm
    names = [t["function"]["name"] for t in llm.TOOLS]
    check("工具已注册", "analyze_opendss_case" in names, str(names))
    tr = llm.ToolRunner(Facts())
    out = tr.call("analyze_opendss_case", {})
    check("工具调用无错误", "error" not in out, str(out.get("error"))[:100])
    check("工具返回电压最值",
          out.get("max_voltage_pu") is not None, str(out)[:100])
    check("工具返回报告路径",
          Path(str(out.get("report_path") or "")).is_file(),
          str(out.get("report_path")))
    check("工具声明不产出容量",
          "不产出开放容量" in str(out.get("capacity_note")))
    bad = tr.call("analyze_opendss_case", {"path": "不存在的算例xyz"})
    check("不存在的算例如实报错", "error" in bad, str(bad)[:100])

    # ---------------------------------------------------------------- 8. 界面
    print("\n8) 界面接线（原生 Tk）")
    chat_src = (ROOT / "hc_agent" / "desktop" / "chat.py").read_text(
        encoding="utf-8")
    app_src = (ROOT / "hc_agent" / "desktop" / "app.py").read_text(
        encoding="utf-8")
    check("输入区有「添加算例」按钮", "＋ 添加算例" in chat_src)
    check("有添加算例对话框", "open_add_case" in chat_src)
    check("支持选目录与选文件",
          "askdirectory" in chat_src and "askopenfilenames" not in "" or
          "askopenfilename" in chat_src)
    check("有已添加算例快捷条", "rebuild_cases" in chat_src)
    check("气泡支持报告按钮", "report_path" in chat_src
          and "打开计算报告" in chat_src)
    check("应用层有 open_report_path", "def open_report_path" in app_src)
    check("打开报告有扩展名白名单",
          ".docx" in app_src and "allowed" in app_src)
    check("界面不含 HTML/浏览器",
          "<html" not in chat_src and "WebView" not in chat_src)

    # ---------------------------------------------------------------- 9. 维护
    print("\n9) 移除登记")
    rd = dss_registry.remove_case(str(cases[0]["id"]))
    check("按 id 移除成功", rd.get("ok"), str(rd)[:80])
    check("移除后列表为空", dss_registry.list_cases() == [],
          str(dss_registry.list_cases()))
    check("重复移除报错",
          dss_registry.remove_case(str(cases[0]["id"])).get("ok") is False)

finally:
    if _old is None:
        os.environ.pop("HC_DATA_DIR", None)
    else:
        os.environ["HC_DATA_DIR"] = _old
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 验收指标章节
print("\n9) 算例报告含「验收指标（单断面口径）」章节")
try:
    from hc_agent.io import dss_report as _dr
    _rep = _dr.analyze_and_report(str(CASE))
    _doc_path = _rep.get("report_path", "")
    check("报告已生成", Path(_doc_path).is_file(), str(_doc_path))
    if Path(_doc_path).is_file():
        from docx import Document as _Doc
        _d = _Doc(_doc_path)
        _heads = [pp.text.strip() for pp in _d.paragraphs]
        check("有第 9 章「验收指标（单断面口径）」",
              any("验收指标" in h and h.startswith("9.") for h in _heads),
              str([h for h in _heads if "验收" in h])[:80])
        _tbl = None
        for _t in _d.tables:
            _hdr = [c.text.strip() for c in _t.rows[0].cells]
            if "指标" in _hdr and "判定" in _hdr:
                _tbl = _t
                break
        check("找到验收指标表", _tbl is not None)
        if _tbl is not None:
            _rows = [[c.text.strip() for c in r.cells] for r in _tbl.rows[1:]]
            check("9 条指标齐全", len(_rows) == 9, str(len(_rows)))
            _verdicts = {r[3] for r in _rows if len(r) > 3}
            check("判定只用四态",
                  _verdicts <= {"达标", "未达标", "无数据", "不适用"},
                  str(_verdicts))
            # 单断面报告里必须有「不适用」，否则说明在用「达标」盖空缺
            check("如实标出不适用项", "不适用" in _verdicts, str(_verdicts))
            check("引擎数如实记为未达标",
                  any("适配引擎数" in r[0] and r[3] == "未达标" for r in _rows),
                  str([r for r in _rows if "适配引擎" in r[0]])[:100])
        # docx 里不应残留 Markdown 星号
        _leak = [pp.text for pp in _d.paragraphs if "**" in pp.text]
        check("docx 无 Markdown 星号残留", not _leak, str(_leak)[:80])
except Exception as _exc:
    check("验收指标章节校验未抛异常", False,
          f"{type(_exc).__name__}: {_exc}")

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
