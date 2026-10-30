# -*- coding: utf-8 -*-
"""交付前自检：页数、标题、备注、表格、图片、讲稿完整性。"""
import re
from pathlib import Path

from docx import Document
from pptx import Presentation

BASE = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project\build_ppt")
PPTX = BASE / "Newton迭代法及其收敛性分析.pptx"
DOCX = BASE / "Newton迭代法_逐页讲稿.docx"

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 72)
print("一、PPT 结构")
print("=" * 72)
prs = Presentation(str(PPTX))
check("页数为 35", len(prs.slides) == 35, str(len(prs.slides)))
check("画布 44.24×24.90 cm",
      abs(prs.slide_width / 360000 - 44.24) < 0.01 and
      abs(prs.slide_height / 360000 - 24.90) < 0.01)

titles, notes_ok, tables, pics = [], 0, 0, 0
for i, slide in enumerate(prs.slides, 1):
    txts = [sh.text_frame.text.strip() for sh in slide.shapes
            if sh.has_text_frame and sh.text_frame.text.strip()]
    titles.append(txts[0] if txts else "")
    n = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
    if len(n) > 40:
        notes_ok += 1
    for sh in slide.shapes:
        if sh.has_table:
            tables += 1
        if sh.shape_type == 13:
            pics += 1

check("每页都有标题", all(titles), str([i + 1 for i, t in enumerate(titles) if not t]))
check("标题不重复", len(set(titles)) == len(titles))
check("每页都有备注（>40 字）", notes_ok == 35, f"{notes_ok}/35")
check("表格数 ≥ 6", tables >= 6, str(tables))
check("装饰图 3×35=105", pics == 105, str(pics))
check("首页含主讲人", "陈宇泓" in "".join(titles) or any(
    "陈宇泓" in (sh.text_frame.text if sh.has_text_frame else "")
    for sh in prs.slides[0].shapes))

# 关键内容点
all_text = []
for slide in prs.slides:
    for sh in slide.shapes:
        if sh.has_text_frame:
            all_text.append(sh.text_frame.text)
        if sh.has_table:
            for row in sh.table.rows:
                for c in row.cells:
                    all_text.append(c.text)
body = "\n".join(all_text)
for kw, why in (("6.3.3", "Newton 公式式号"), ("几何意义", "几何意义"),
                ("定理 6.3", "收敛阶判据"), ("平方收敛", "核心结论"),
                ("例 6.7", "教材例题 1"), ("例 6.8", "教材例题 2"),
                ("下山法", "改进一"), ("弦截法", "改进二"),
                ("1.618", "弦截法收敛阶")):
    check(f"含「{kw}」（{why}）", kw in body)

print()
print("=" * 72)
print("二、讲稿 Word")
print("=" * 72)
doc = Document(str(DOCX))
paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
check("讲稿段落数 ≥ 120", len(paras) >= 120, str(len(paras)))
h2 = [p for p in paras if p.startswith("第 ") and "页｜" in p]
check("逐页标题 35 条", len(h2) == 35, str(len(h2)))
nums = [int(re.match(r"第 (\d+) 页", p).group(1)) for p in h2]
check("页码 1–35 且顺序正确", nums == list(range(1, 36)), str(nums[:8]))
check("含使用说明", any("使用说明" in p for p in paras))
check("含时间分配总表", any("时间分配总表" in p for p in paras))
check("表格数 ≥ 2", len(doc.tables) >= 2, str(len(doc.tables)))
total_chars = sum(len(p) for p in paras)
check("讲稿字数 ≥ 9000", total_chars >= 9000, str(total_chars))

# 每页讲稿长度是否均衡（避免某页没内容）
lens = {}
cur = None
for p in paras:
    m = re.match(r"第 (\d+) 页｜", p)
    if m:
        cur = int(m.group(1))
        lens[cur] = 0
    elif cur and not p.startswith("建议用时"):
        lens[cur] = lens.get(cur, 0) + len(p)
short = {k: v for k, v in lens.items() if v < 80}
check("每页讲稿 ≥ 80 字", not short, str(short))

print()
print("=" * 72)
print(f"结果：{ok} 通过 / {fail} 失败")
print("=" * 72)
