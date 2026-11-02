# -*- coding: utf-8 -*-
"""以《天智5号_配电网潮流计算智能体发布文档_优化版.docx》为模板，
生成《配网承载力智能体发布文档.docx》。

**为什么用"复制模板 + 克隆段落"而不是从空白文档新建**：
模板的字体字号、表格样式、图片宽度、页边距都已经调好且经过人工审阅。
克隆它现成的段落/表格 XML，能保证版式一致；从空白 Document() 新建会丢掉这些。

输出：Desktop\\配网承载力\\配网承载力智能体发布文档.docx
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm

TPL = Path.home() / "Desktop" / "配网承载力" / \
    "天智5号_配电网潮流计算智能体发布文档_优化版.docx"
OUT = Path.home() / "Desktop" / "配网承载力" / "配网承载力智能体发布文档.docx"
SHOTS = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace"
             r"\hc_agent_project\tests\_desktop_shots")

FIG_W = Cm(15.24)          # 与模板三张图同宽


# ------------------------------------------------------------------ 工具

def _insert_before_sectpr(doc: Document, element) -> None:
    """把元素插到节属性（sectPr）之前。

    python-docx 的 body 末尾是 sectPr；直接 append 会把正文排到节属性之后，
    Word 打开时可能报格式修复。
    """
    body = doc.element.body
    sect = body.find(qn('w:sectPr'))
    if sect is None:
        body.append(element)
    else:
        sect.addprevious(element)


def _clear_body(doc: Document) -> None:
    """删掉正文全部内容，只保留节属性（页面设置）。"""
    body = doc.element.body
    for child in list(body):
        if child.tag.endswith("}sectPr"):
            continue
        body.remove(child)


def _find_para(doc: Document, style: str, *, startswith: str = ""):
    for p in doc.paragraphs:
        if p.style.name != style:
            continue
        if startswith and not p.text.strip().startswith(startswith):
            continue
        return p
    return None


def _clone(doc: Document, donor, text: str | None = None,
           runs: tuple[str, ...] | None = None):
    """克隆一段（保留其 run 格式），按需替换文字。

    * ``text`` 单段文字：写进第一个 run，其余 run 删除；
    * ``runs`` 多段文字（如"加粗小标题 + 正文"）：按顺序写进前 N 个 run。
    """
    new_p = copy.deepcopy(donor._p)
    _insert_before_sectpr(doc, new_p)
    para = doc.paragraphs[-1]
    if runs is not None:
        for i, chunk in enumerate(runs):
            if i < len(para.runs):
                para.runs[i].text = chunk
        for r in para.runs[len(runs):]:
            r.text = ""
    elif text is not None:
        if para.runs:
            para.runs[0].text = text
            for r in para.runs[1:]:
                r.text = ""
        else:
            para.add_run(text)
    return para


def _clone_table(doc: Document, donor, rows: list[list[str]]):
    """克隆表格并重写单元格；行数不足时复制最后一行。"""
    tbl = copy.deepcopy(donor._tbl)
    _insert_before_sectpr(doc, tbl)
    table = doc.tables[-1]
    need, have = len(rows), len(table.rows)
    while have < need:
        table._tbl.append(copy.deepcopy(table.rows[-1]._tr))
        have += 1
    for r_idx, row_vals in enumerate(rows):
        # 表格行禁止跨页断开：否则一行会被页面边界切成两半，读起来像缺内容
        tr_pr = table.rows[r_idx]._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))
        cells = table.rows[r_idx].cells
        for c_idx, val in enumerate(row_vals):
            if c_idx < len(cells):
                cells[c_idx].text = val
    return table


def _picture(doc: Document, path: Path, caption: str, donor_caption) -> None:
    doc.add_picture(str(path), width=FIG_W)
    doc.paragraphs[-1].alignment = 1          # 居中
    _clone(doc, donor_caption, text=caption)


# ------------------------------------------------------------------ 正文

def _set_header(doc: Document, text: str) -> None:
    """改页眉文字。模板页眉是固定文字（天智5号…），必须换成本文档的。

    保留原 run 的字体字号，只替换文字；页眉里可能有多个 run（含分页符）。
    """
    for section in doc.sections:
        header = section.header
        hit = False
        for para in header.paragraphs:
            for run in para.runs:
                if run.text.strip():
                    run.text = text if not hit else ''
                    hit = True
        if not hit:
            header.paragraphs[0].add_run(text)


def build() -> Path:
    doc = Document(str(TPL))
    ps = doc.paragraphs

    # ---- 从模板取样：各种版式各留一个"供体"
    d_title, d_sub, d_body = ps[0], ps[1], ps[2]
    d_h1 = _find_para(doc, "Heading 1")
    d_h2 = _find_para(doc, "Heading 2")
    d_lead = None                             # "1.1 xxx。" 加粗小标题 + 正文
    for p in ps:
        if len(p.runs) >= 2 and p.runs[0].bold:
            d_lead = p
            break
    d_cap = None                              # 图注
    for p in ps:
        if p.text.strip().startswith("图1"):
            d_cap = p
            break
    d_t1, d_t2, d_t3 = doc.tables[0], doc.tables[1], doc.tables[2]
    d_t1, d_t2, d_t3 = (copy.deepcopy(d_t1._tbl), copy.deepcopy(d_t2._tbl),
                        copy.deepcopy(d_t3._tbl))

    _clear_body(doc)

    # ================= 封面
    _clone(doc, d_title, text="配网承载力智能体")
    _clone(doc, d_sub, text="自然语言驱动的分布式电源接入承载力评估与依据追溯")
    _clone(doc, d_body, text=(
        "某条 10 kV 馈线还能再接入多少分布式光伏？接入之后，节点电压会不会越限，"
        "线路和配变会不会过载，反送功率和短路电流是否仍在设备能力之内，"
        "这条馈线又该被划为红区、黄区还是绿区？回答这些问题，需要把网架参数、"
        "运行曲线、已受理电源与判据口径拼成一次可复现的计算，并在给出数字的同时"
        "说清它卡在哪一条约束、还差多少裕度。"))
    _clone(doc, d_body, text=(
        "面向这一需求，我们构建了配网承载力智能体。平台以开源版 DeepSeek Harness"
        "（MIT，以下简称 Harness）为底座，把自然语言任务、专业计算核心、"
        "验收矩阵与证据链组织成一次可复核的评估：大模型负责理解与编排，"
        "OpenDSS 与 pandapower 负责数值，报告与哈希链负责依据，"
        "专业人员负责判据与结论的适用性。"))
    _clone(doc, d_body,
           text="智能体按下列分工组织每次评估，语义理解与数值生产在环节上分离：")
    for line in ("大模型：理解需求、拆解任务、选择技能与引擎；",
                 "OpenDSS 与 pandapower：产生并交叉校验全部电气数值；",
                 "报告与证据链：保存计算依据、验收结果与可追溯记录；",
                 "专业人员：核定判据、确认口径、承担结论的最终判断。"):
        _clone(doc, d_body, text=line)

    _clone_table(doc, _tbl_from(d_t1),
                 [["项目", "说明"],
                  ["版本", "V1.0（发布稿）"],
                  ["发布日期", "2026 年 11 月 2 日"],
                  ["适用对象", "10 kV 及以下配电网：馈线、台区、并网点"],
                  ["计算引擎", "OpenDSS 0.9.4（主算，三相不平衡）"
                               "＋ pandapower 3.5.6（交叉校核，正序平衡）"],
                  ["规则包", "HC-RULES@1.0（APPROVAL-2026-002，已批准）"],
                  ["运行环境", "Windows 本机；底座为开源版 DeepSeek Harness（MIT）"]])

    # ================= 01 痛点
    _clone(doc, d_h1, text="01 痛点：为什么需要配网承载力智能体")
    _clone(doc, d_body, text=(
        "承载力的门槛不在潮流求解本身，而在“口径一致、约束齐全、结论可追溯”。"
        "当前实践中，专业人员普遍面临六类负担："))
    _clone(doc, d_lead, runs=(
        "1.1 口径混用，结论无法比较。",
        "  同样叫“电压合格”，国标是 0.90–1.10 pu，项目批准口径可能是 ±7%，"
        "公共连接点与单个用户的限值也不同。口径不随结果一起固化，两次评估的数"
        "字就没法比。本系统把判据写进规则包并绑定审批号与生效日期，换口径必须换凭证，"
        "结论失效会被明确判定。"))
    _clone(doc, d_lead, runs=(
        "1.2 约束取不全，容量偏大。",
        "  只校核电压、不校核热稳与反送，会得出偏乐观的可接入容量。"
        "本系统按五类约束（电压、热稳、反送、不平衡、短路）逐条校核，"
        "取各约束下的最小值作为可开放容量，并记录是哪一条先到限。"))
    _clone(doc, d_lead, runs=(
        "1.3 余量当总量，管理口径与物理口径混淆。",
        "  物理可开放容量是受物理约束的最大可接入量；管理可开放容量还要再叠加"
        "管理裕度与已受理容量。两者混用会直接导致答复意见出错。"
        "本系统对两个口径分别给数，并注明换算系数。"))
    _clone(doc, d_lead, runs=(
        "1.4 逐级叠加靠手算。",
        "  台区、10 kV 线路、主变、区域电网四级中，本级的上限未必是上级的上限，"
        "人工串接容易漏掉上级约束。本版按单算例评估，逐级计算尚未实现，"
        "这一点在报告里如实标注，不代做。"))
    _clone(doc, d_lead, runs=(
        "1.5 结论无法追溯。",
        "  报告里只有一个 kW 数字，追问“卡在哪个节点、裕度多少”时往往答不上来。"
        "本系统为每个结论记录场景、断面、元件、字段与数值，并生成哈希链；"
        "需要说明的是，哈希链只证明记录未被改动，不证明结论正确。"))
    _clone(doc, d_lead, runs=(
        "1.6 工程数值不能交给概率。",
        "  通用大模型可能生成看似合理、实则错误的容量与电压值。"
        "一旦数值由模型生成，结论就失去了可复核性。本系统规定："
        "所有电气数值只来自引擎，模型不产生也不修改任何潮流结果。"))
    _clone(doc, d_body, text=(
        "综上，需要一种把“规则可执行—潮流求解—约束校核—容量搜索—独立复算—"
        "证据留存”组织起来的工具：数值由引擎产生，复算由独立进程执行，"
        "依据随结果一同保留，判据不满足时不出正式结论。"))

    # ================= 02 功能
    _clone(doc, d_h1, text="02 功能介绍：从自然语言任务到专业计算")
    _clone(doc, d_h2, text="2.1 功能总览")
    _clone_table(doc, _tbl_from(d_t2), [
        ["功能模块", "能力说明"],
        ["自然语言任务接入", "通过对话提出“这条馈线还能接多少光伏、卡在哪”等需求，"
                             "智能体拆解为可执行任务"],
        ["多来源输入接入", "输入包（网架表／曲线／规则／任务）、OpenDSS 算例目录、"
                           "压缩包拖入，三种入口统一校验"],
        ["九阶段评估流水线", "冻结输入→校验→建案→基线潮流→约束校核→容量搜索→"
                             "独立复算→报告→封存证据，每阶段留痕"],
        ["容量搜索", "0–3000 kW 区间、精度 10 kW、最多 25 次迭代；"
                     "实测 FD002 用 11 次收敛"],
        ["五类约束校核", "电压、热稳、反送、不平衡、短路；逐条比对规则包限值"],
        ["红黄绿分级", "按可开放容量给出等级与主导限制因素，定位到具体元件与相位"],
        ["独立复算", "在全新目录、全新进程重跑一遍，逐项比对关键量"],
        ["验收矩阵", "9 项指标、四种判定（达标／未达标／无数据／不适用），"
                     "“无数据”不等于通过"],
        ["报告与证据链", "12 章专业报告（含验收指标与业务流覆盖）＋ 哈希链与证据包"]])

    _clone(doc, d_h2, text="2.2 使用方式与核心流程")
    _clone(doc, d_body, text=(
        "用户通过对话提出需求，例如“FD002 还能接多少光伏”“为什么接不了更多”"
        "“最低电压是多少”。智能体先确认算例入口、规则包版本与评估口径，"
        "条件不足时先追问，而不是带着默认假设直接出数。"))
    _clone(doc, d_body, text=(
        "底座使用开源版 DeepSeek Harness，通过自研插件 tool-hc-agent 将七项专业能力"
        "注册为 Harness 工具（引擎状态、容量评估、验收指标、交叉校核、算例分析、"
        "规则问答、业务流状态）。Harness 承担会话、模型推理与工具调度，"
        "Python 侧核心负责数值与产物，两者以单行 JSON 协议通信。"))
    _clone(doc, d_body, text=(
        "理解需求 → 确认算例与口径 → 冻结输入 → 基线潮流 → 约束校核 → "
        "容量搜索 → 独立复算 → 生成报告 → 封存证据"))
    _clone(doc, d_body, text=(
        "运行结果用退出码区分成因：0 成功、2 输入被阻断、3 计算失败、4 验证失败、"
        "5 未过发布门禁、6 被中断、70 看门狗超时。失败不会被写成成功，"
        "缺少产物时判定为“无数据”，不会推出一个达标结论。"))
    _clone(doc, d_body, text=(
        "权限上，智能体只做只读分析与离线仿真；写设备、下发遥控、在线调节分接头、"
        "代签并网许可等动作被列入禁止清单，不属于本系统能力范围。"))
    _picture(doc, SHOTS / "35_title_new.png",
             "图1　配网承载力智能体问答界面（算例分析与依据展示）", d_cap)

    # ================= 03 算例演示
    _clone(doc, d_h1, text="03 算例演示：从容量数字到有依据的结论")
    _clone(doc, d_body, text=(
        "以验收用算例 FD002 为例（5 节点、3 条线路、1 台配变、2 个负荷、2 组光伏）。"
        "基线潮流无越限，容量搜索结果如下："))
    _clone(doc, d_body, text=(
        "物理可开放容量 333.984375 kW；按管理裕度换算后的管理可开放容量 "
        "270.52734375 kW（系数 0.81）。等级为绿。评估共用 11 次迭代，"
        "搜索区间收敛在 333.984375 / 339.84375 kW 之间，精度 10 kW。"))
    _clone(doc, d_body, text=(
        "绑定约束是电压越限，定位到节点 bus-lv-002 的 C 相："
        "接入后电压 1.1008292527209553 pu，上限 1.1000000000 pu，"
        "裕度 −0.000829 pu。也就是说，这条馈线的容量不是被线路或配变卡住的，"
        "而是被末端 C 相电压卡住的 —— 这类结论直接决定整改方向是调压还是增容。"))
    _clone(doc, d_body, text=(
        "反向算例 FD001 给出的是“零”而不是“很小”：受热稳约束限制，"
        "元件 Line.line_ln_lv_001 的负载率达到 136.1645%，"
        "可开放容量为 0.0 kW；该运行基线即有 14 项越限、1 项无数据，等级不为绿。"
        "把这两种情形放在一起，是为了说明“接不了”同样是一个需要证据的结论。"))
    _clone(doc, d_body, text=(
        "另有一组 16 节点合成算例用于压力验证：物理容量 515.625 kW、"
        "管理容量 417.65625 kW，绑定约束为电压越限（bus-lv-002b 的 B 相 "
        "1.1005962 pu），3 个场景 × 12 个时间片共 420 条约束全部通过、"
        "无一项无数据，单次评估耗时约 5.6 秒。"))
    _picture(doc, SHOTS / "33_results_new.png",
             "图2　验收与指标页：九项指标的口径与近期运行总体状况", d_cap)

    # ================= 04 知识
    _clone(doc, d_h1, text="04 让专业知识服务评估过程")
    _clone(doc, d_body, text=(
        "从“这个数是多少”到“这个数意味着什么”，中间隔着判据、口径与工程经验。"
        "本系统把可固化的部分整理成三层：技能卡 17 张（每张对应一类任务的做法与"
        "边界）、技能契约 5 个（规定输入输出与失败处理）、内置知识库 31 篇"
        "（覆盖承载力定义、配网拓扑、新能源涉网要求等）。"))
    _clone(doc, d_body, text=(
        "知识条目区分“已核对”与“待核”：引用标准条款的条目注明出处，"
        "查不到出处的标注待核，不在结论里当依据使用。"
        "DL/T 2041 提出的系统级与设备级承载力区分，是本系统定义口径的直接来源。"))
    _clone(doc, d_body, text=(
        "规则包是知识里最硬的一层：五类约束各自带 standard_ref 与 failure_action，"
        "例如电压 0.90–1.10 pu 引用 GB/T 12325，超限即阻断。"
        "规则包同时带审批号与生效日期，口径变更会在下次评估时被识别为结论失效。"))

    # ================= 05 核验
    _clone(doc, d_h1, text="05 用参考算例与实际操作核验能力")
    _clone(doc, d_body, text=(
        "计算可信度首先落实在每次评估后的独立复算：换一个全新目录、用一个全新进程"
        "重跑同一份冻结输入，逐项比对可开放容量、绑定约束、母线电压与网损。"
        "FD002 的八项比对全部一致，任一不一致即判定验证失败，结论不得发布。"))
    _clone(doc, d_body, text=(
        "其次是验收矩阵：九项指标覆盖评估覆盖度、红黄绿一致率、可开放容量偏差、"
        "跨版本复现、独立复算通过率、原始数据字节保护、可追溯率、适配引擎数、"
        "单台区耗时降幅。判定分四态，缺产物记“无数据”而不是“达标”。"
        "在 FD002 上，5 项达标、3 项因缺少专家标签等条件记为不适用、1 项无数据。"))
    _clone(doc, d_body, text=(
        "第三是双引擎交叉校核：OpenDSS 以三相不平衡方式主算，pandapower 以正序平衡"
        "方式复核，只比量级与极值，不参与容量判定。FD002 两个断面的电压极值差分别为"
        "0.001461 pu 与 0.005540 pu，网损差 0.000179 kW 与 0.219186 kW，均为 match。"))
    _clone(doc, d_body, text=(
        "最后是工程化的自检：二十个套件覆盖算例报告、验收指标、问答路由、"
        "桌面界面、会话运行时、命名规范、打包产物、业务流等，关键套件的检查项"
        "在 30–105 项之间。测试失败会被如实报告，不美化成功率。"))
    _clone(doc, d_body, text=(
        "底座本身也做了验证：在从源码构建的开源版 Harness 上，通过 patch 挂载"
        "tool-hc-agent 插件并发起真实会话，模型调用 hc_engine_status 后返回"
        "“count = 2，OpenDSS 与 pandapower”，说明工具链在底座上是通的，"
        "而不是只在本地脚本里成立。"))

    # ================= 06 效率
    _clone(doc, d_h1, text="06 效率提升：省下的是时间，收回的是专业判断")
    _clone(doc, d_h2, text="6.1 环节对比")
    _clone_table(doc, _tbl_from(d_t3), [
        ["评估环节", "传统方式", "配网承载力智能体"],
        ["数据准备", "人工从营销、PMS、调度、用采导出并手工对齐台账",
         "输入包统一校验（字段、单位、缺失、越界），问题在开算前暴露"],
        ["口径确认", "翻标准、问同事，口径散落在个人经验里",
         "规则包带审批号与生效日期，换口径即换凭证并判定旧结论失效"],
        ["约束建模", "只做电压或只做载流，凭经验取舍",
         "五类约束全部编制为可执行规则，逐条校核并取最小值"],
        ["容量搜索", "手工试算几个点，外推得出结论",
         "0–3000 kW 区间二分搜索，精度 10 kW，单算例约 11 次迭代"],
        ["约束定位", "知道越限，不知道卡在哪一相、裕度多少",
         "定位到元件、相位与字段，给出裕度数值"],
        ["复算与验收", "人工复核，漏检与返工难以避免",
         "全新目录＋全新进程自动复算，九项验收四态判定"],
        ["报告与追溯", "整理 Word，依据与数字对不上",
         "12 章报告＋证据索引＋哈希链，结论可逐项回链"]])

    _clone(doc, d_h2, text="6.2 效率提升的四个维度")
    _clone(doc, d_body, text=(
        "场景处理效率。 传统做法：一次面对一个算例、一种工况，换一个断面重来一遍。"
        "智能体方式：多场景多断面一次批量求解（FD016 一次跑 3 场景 × 12 断面），"
        "取最不利断面作为结论依据。"))
    _clone(doc, d_body, text=(
        "工具操作效率。 传统做法：在表格、脚本、仿真软件之间来回搬数。"
        "智能体方式：输入统一接入、求解与提取自动完成，人只需要确认口径与判据。"))
    _clone(doc, d_body, text=(
        "经验复用效率。 传统做法：口径与整改思路依赖个人，换人换批次质量就波动。"
        "智能体方式：规则、技能卡与整改建议库固化下来，同一问题给出同一套判据。"))
    _clone(doc, d_body, text=(
        "结果复核效率。 传统做法：逐项人工核对，容易被“看起来合理”的数字放过。"
        "智能体方式：独立复算与验收矩阵常开，缺产物就记无数据，不给出未经核验的结论。"))
    _clone(doc, d_body, text=(
        "与四个维度相伴的是角色变化：从逐次操作工具，转向维护口径与验收标准、"
        "处理异常与失败场景、审核关键工程结果。"))
    _clone(doc, d_h2, text="6.3 效率的本质是分工")
    _clone(doc, d_body, text=(
        "智能体想减少的是准备文件、反复试算、拼报告和找依据的负担，"
        "把可固化的部分交给程序；判据的采用、边界条件的确认、结论能不能对外说，"
        "仍然由专业人员把关。效率来自分工，不来自让模型替人做判断。"))

    # ================= 07 范围与计划
    _clone(doc, d_h1, text="07 使用范围与后续计划")
    _clone(doc, d_body, text=(
        "当前版本面向 Windows 本机单人使用：模型推理通过联网服务完成，"
        "OpenDSS 与 pandapower 在本机计算，不连接生产系统、不写入任何现场设备。"
        "适用范围为 10 kV 及以下配电网的馈线、台区与并网点评估。"))
    _clone(doc, d_body, text=(
        "业务链的覆盖情况如实说明：数据准备、约束建模、计算、分级、接入判断、"
        "整改建议与动态重跑已有实现或部分实现；结果发布与公示尚未实现，"
        "逐级（台区→线路→主变→区域）计算尚未实现，谐波、电压波动与继电保护"
        "适应性三类约束尚未纳入，工单流转与答复意见书出具也不在本版范围内。"
        "上述缺口已在报告与问答中标注为“未实现”，不代做。"))
    _clone(doc, d_body, text=(
        "后续计划按优先级推进：一是逐级承载力与上级约束的串接规则；"
        "二是改造措施的定量校核（改完能提升多少、代价多大）；"
        "三是发布口径与对外表格（需依据省公司规范，不自行设定阈值）；"
        "四是变更检测与版本对比，说明“哪次变化导致等级改变”。"))
    _clone(doc, d_body, text=(
        "每一项结论都应当说明所用模型、工况与依据。智能体提供分析辅助，"
        "工程判据的采用和结论的最终判断，由专业人员确认。"))

    _set_header(doc, "配网承载力智能体　V1.0　发布文档")
    doc.save(str(OUT))
    return OUT


def _tbl_from(tbl_element):
    """把预先复制的 tbl XML 包成可传给 _clone_table 的轻量对象。"""
    class _W:
        def __init__(self, tbl):
            self._tbl = tbl
    return _W(tbl_element)


if __name__ == "__main__":
    if not TPL.is_file():
        raise SystemExit(f"模板不存在：{TPL}")
    p = build()
    print(f"已生成：{p}")
    print(f"大小：{p.stat().st_size / 1024:.0f} KB")
