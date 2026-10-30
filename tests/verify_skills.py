"""Verify 协作准则与外部 skill 落地。

  1. AGENTS.md 存在且含两条行为准则（真相优先 / 对练伙伴）
  2. skills/agent/ 下三个 skill 的 frontmatter 合规、正文非空
  3. 三个 skill 的内容与来源对应（不是空壳）
"""
from __future__ import annotations

import re
import sys
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
print("协作准则与 skill 落地验证")
print("=" * 74)

# ---------------------------------------------------------------- 1. AGENTS.md
print("\n1) 协作准则（AGENTS.md）")
p = ROOT / "AGENTS.md"
check("文件存在", p.is_file(), str(p))
if p.is_file():
    t = p.read_text(encoding="utf-8")
    for kw, why in (
            ("真相优先于附和", "准则一标题"),
            ("不要为了顺从或取悦而点头", "原文核心句"),
            ("对练伙伴", "准则二"),
            ("不要为了辩论而辩论", "防抬杠"),
            ("数字不编", "诚实边界"),
            ("待核", "未核实要标注"),
            ("哪一句错了", "指出错误三要素"),
            ("选择权归对方", "不越权边界"),
    ):
        check(f"含「{kw}」（{why}）", kw in t)
    check("篇幅合理（不空洞）", len(t) > 1500, f"{len(t)} 字")
    # 三个 skill 都要被提到
    for s in ("grill-me", "find-skills", "harness-tuning"):
        check(f"准则里提到 {s}", s in t)

# ---------------------------------------------------------------- 1.5 全局准则
print()
print('1.5) 全局准则（DeepSeek Harness 用户级）')
# dsh 的 userGlobalRoots() 会在两个位置找用户级指令：
#   ~/.dsh/AGENTS.md（harness home，优先）与 ~/.agents/AGENTS.md（agents home）
import os
home = Path(os.path.expanduser('~'))
agents_md = home / '.agents' / 'AGENTS.md'
check('~/.agents/AGENTS.md 存在（全局准则已安装）', agents_md.is_file(),
      str(agents_md))
if agents_md.is_file():
    t = agents_md.read_text(encoding='utf-8')
    for kw in ('真相优先于附和', '对练伙伴', '哪一句错了',
               '不编数字', '说清口径', '不越权'):
        check(f'全局准则含「{kw}」', kw in t)
    check('全局准则写明位置与作用范围',
          '.agents' in t and ('所有' in t or '全局' in t))
    check('全局准则说明与项目级的关系',
          '项目' in t and ('优先' in t or '更具体' in t))

# ---------------------------------------------------------------- 2. skills
print("\n2) agent skills 格式")
base = ROOT / "skills" / "agent"
check("skills/agent 目录存在", base.is_dir(), str(base))
want = {"grill-me", "find-skills", "harness-tuning"}
found = {d.name for d in base.iterdir() if d.is_dir()} if base.is_dir() else set()
check("三个 skill 都在", want <= found, str(sorted(found)))

for name in sorted(want):
    f = base / name / "SKILL.md"
    if not f.is_file():
        check(f"{name} 有 SKILL.md", False, str(f))
        continue
    t = f.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", t, re.S)
    check(f"{name} frontmatter 格式正确", m is not None)
    if not m:
        continue
    fm = m.group(1)
    nm = re.search(r"^name:\s*(.+)$", fm, re.M)
    ds = re.search(r"^description:\s*(.+)$", fm, re.M)
    check(f"{name} name 与目录一致",
          bool(nm) and nm.group(1).strip() == name,
          nm.group(1) if nm else "缺 name")
    check(f"{name} 有 description 且够具体",
          bool(ds) and len(ds.group(1).strip()) >= 20,
          f"{len(ds.group(1)) if ds else 0} 字")
    body = t[m.end():].strip()
    check(f"{name} 正文非空", len(body) > 500, f"{len(body)} 字")

# ---------------------------------------------------------------- 3. 内容实质
print("\n3) 内容与来源对应（不是空壳）")

g = (base / "grill-me" / "SKILL.md")
if g.is_file():
    t = g.read_text(encoding="utf-8")
    for kw in ("一次一个问题", "可验收", "待定", "不替用户做决定"):
        check(f"grill-me 含「{kw}」", kw in t)
    # 它是从 mattpocock/skills 的思路改写的，但已适配本工程
    check("grill-me 说明了停止条件", "问够了" in t or "什么时候该停" in t)

f2 = (base / "find-skills" / "SKILL.md")
if f2.is_file():
    t = f2.read_text(encoding="utf-8")
    for kw in ("离线", "技能卡", "找不到就直说", "自建技能"):
        check(f"find-skills 含「{kw}」", kw in t)
    check("find-skills 列了本工程技能", "SK-10" in t or "SK-13" in t)
    check("find-skills 说明与 npx 版的差异",
          "npx" in t and ("离线" in t or "不联网" in t))

h = (base / "harness-tuning" / "SKILL.md")
if h.is_file():
    t = h.read_text(encoding="utf-8")
    for kw in ("八个组件", "Propose", "Probe", "Compose", "护栏", "未验证"):
        check(f"harness-tuning 含「{kw}」", kw in t)
    check("harness-tuning 说明不引入本体",
          "不引入" in t and "Harness" in t)
    check("harness-tuning 守住数字红线", "数字只能来自" in t)

# ---------------------------------------------------------------- 4. 不污染打包
print("\n4) 与打包/测试的关系")
spec = (ROOT / "packaging" / "HC-Agent.spec")
if spec.is_file():
    st = spec.read_text(encoding="utf-8")
    check("skills 整个目录会被打包（含 agent 子目录）",
          '"skills"' in st or "'skills'" in st or "ROOT / \"skills\"" in st)
cards = len(list((ROOT / "skills" / "cards").glob("*.md")))
contracts = len(list((ROOT / "skills" / "contracts").glob("*/skill.json")))
check("技能卡未被破坏（≥17）", cards >= 17, str(cards))
check("技能契约未被破坏（5）", contracts == 5, str(contracts))

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
