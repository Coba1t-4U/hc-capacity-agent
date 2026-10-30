# -*- coding: utf-8 -*-
"""把工程复制成可在 PyCharm 里直接打开的独立工程（放到桌面）。

要点：
  * **不复制 llm.json** —— 里面有 API Key，不能跟着代码走；
  * 不复制 chat.sqlite / dss_cases.json —— 那是用户本机的会话与登记，不是代码；
  * 不复制 dist / build / release / dss_runs / hc_agent_ui（261 MB 前端依赖）；
  * 带上 runs/ACCEPT —— 测试要读黄金运行，不带就跑不起来。
"""
import shutil
from pathlib import Path

SRC = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
DST = Path.home() / "Desktop" / "配网承载力智能体Agent"

#: 整目录复制
DIRS = ["hc_agent", "skills", "rules", "tests", "tools", "docs",
        "knowledge", "packaging", "web", "runs/ACCEPT"]
#: 单文件复制
FILES = ["pyproject.toml", "README.md", "AGENTS.md", "requirements.lock"]

#: hc_agent_ui 只带**源码与启动器**：node_modules(259MB) 与 Electron profile
#: (用户数据) 都不带，否则一个 10MB 的工程会变成 270MB。
UI_DIR = "hc_agent_ui"
UI_ITEMS = ["assets", "package.json", "start-native.bat", "start-desktop.bat",
            "main.js", "preload.js", "launch.js"]

GITIGNORE = """# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
build/
dist/

# 本机数据与密钥（**不要提交**）
llm.json
chat.sqlite*
dss_cases.json
engine_mode.json
logs/

# 运行产物
runs/DESKTOP/
runs/GOLDEN/
dss_runs/
release/

# 工具产物
tests/_desktop_shots/
*.log
"""

ENTRY = '''"""启动桌面版（在 PyCharm 里直接右键 Run 这个文件即可）。

等价于 `python -m hc_agent.desktop.app`。
"""
from hc_agent.desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
'''

REQ = """# 运行必需
OpenDSSDirect.py==0.9.4
openpyxl>=3.1
numpy>=2.0
python-docx>=1.1

# 可选增强（拓扑检查 / YAML / 优化 / 表格）
networkx>=3.0
PyYAML>=6.0
scipy>=1.11
pandas>=2.0
pyarrow>=14.0

# 开发
pytest>=7.0

# 打包
PyInstaller>=6.0

# 第二引擎（交叉校核）。注意：它要求 pandas~=2.3，
# 与上面的 pandas>=2.0 共存时建议装在独立目录（见 README_如何在PyCharm中打开.md）
pandapower>=3.5
"""

GUIDE = """# 在 PyCharm 里打开这个工程

## 一、打开

1. PyCharm → **File → Open** → 选择本文件夹（`配网承载力智能体Agent`）
2. 右下角如提示 *Trust project*，选 **Trust**
3. PyCharm 会自动识别 `pyproject.toml`，并在右下角提示创建虚拟环境 → 选 **OK**

## 二、配置解释器（关键）

本工程的引擎 `OpenDSSDirect.py` 已在本机一份 Python 里装好。最省事的做法是**直接复用那份解释器**：

`Settings → Project → Python Interpreter → Add Interpreter → System Interpreter`

填入：

```
C:\\Users\\Chen\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe
```

> 为什么不用新建 venv：新建 venv 需要重装 OpenDSSDirect.py，而它在当前网络下装不上；
> 复用已装好的解释器可以直接跑。

## 三、运行

| 想做的事 | 怎么点 |
|---|---|
| 启动桌面版界面 | 右键 `启动桌面版.py` → **Run** |
| 跑全部自检 | 右键 `tests/` 里任意 `verify_*.py` → **Run** |
| 跑一次承载力评估 | 见 `README.md` 的「运行」一节 |

## 四、目录说明

```
hc_agent/            主程序包
  desktop/           原生 Tk 界面（15 个模块）
  engines/           计算引擎
    opendss_adapter.py   OpenDSS（主算，三相不平衡）
    pandapower_adapter.py pandapower（交叉校核，正序平衡）
    registry.py          引擎注册表（验收矩阵的"适配引擎数"来源）
    selector.py          输入框里的引擎选择
  io/                输入包读写、报告生成、算例分析
  knowledge/builtin/ 内置知识库（31 篇 md）
skills/              技能卡（17）+ 技能契约（5）+ 通用技能（3）
rules/               规则包模板
tests/               18 个自检套件
tools/               构建/校验/生成脚本
runs/ACCEPT/         黄金运行（测试要用，别删）
docs/                设计说明与实施记录
```

## 五、这个副本的边界

- 它是**快照**：在这里改代码，不会影响你原来那份工程
  （原工程：`C:\\Users\\Chen\\Documents\\deepseek-harness\\default-workspace\\hc_agent_project`）
- **没有带 `llm.json`**：里面有 API Key，不能跟着代码走。要启用大模型，
  在界面「配置」里重新填一次 Key，或从原工程复制 `llm.json` 过来。
- 没有带 `dist/`、`release/`（打包产物）和 `hc_agent_ui/`（261 MB 旧版前端）。
- `runs/ACCEPT` 是测试用的黄金运行，**删了部分测试会失败**。
"""


def _force_rmtree(p: Path) -> None:
    """删除目录，**先清只读属性**。

    runs/*/snapshots 下的冻结副本是只读的（设计如此），
    直接 rmtree 会在 Windows 上抛 WinError 5。
    """
    import os
    import stat

    if not p.exists():
        return
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                os.chmod(os.path.join(root, f), stat.S_IWRITE)
            except Exception:
                pass
    shutil.rmtree(p, ignore_errors=True)


def main() -> None:
    if DST.exists():
        print(f"目标已存在，先清空：{DST}")
        _force_rmtree(DST)
    DST.mkdir(parents=True)

    copied = 0
    for d in DIRS:
        src = SRC / d
        if not src.is_dir():
            print(f"  跳过（不存在）：{d}")
            continue
        dst = DST / d
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        copied += 1
        print(f"  [目录] {d}")

    for f in FILES:
        src = SRC / f
        if not src.is_file():
            print(f"  跳过（不存在）：{f}")
            continue
        shutil.copy2(src, DST / f)
        print(f"  [文件] {f}")

    # hc_agent_ui：只挑源码与启动器
    ui_src = SRC / UI_DIR
    if ui_src.is_dir():
        ui_dst = DST / UI_DIR
        ui_dst.mkdir(parents=True, exist_ok=True)
        for item in UI_ITEMS:
            sp = ui_src / item
            if not sp.exists():
                continue
            if sp.is_dir():
                shutil.copytree(sp, ui_dst / item, dirs_exist_ok=True)
            else:
                shutil.copy2(sp, ui_dst / item)
        print(f"  [目录] {UI_DIR}（仅源码与启动器，跳过 node_modules / profile）")

    # 清理不该带的东西
    for pat in ("**/__pycache__", "**/*.pyc"):
        for p in DST.glob(pat):
            _force_rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    shots = DST / "tests" / "_desktop_shots"
    if shots.is_dir():
        _force_rmtree(shots)
        print("  [清理] tests/_desktop_shots（截图，不属于代码）")

    # 新增工程级文件
    (DST / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (DST / "启动桌面版.py").write_text(ENTRY, encoding="utf-8")
    (DST / "requirements.txt").write_text(REQ, encoding="utf-8")
    (DST / "README_如何在PyCharm中打开.md").write_text(GUIDE, encoding="utf-8")
    print("  [新增] .gitignore / 启动桌面版.py / requirements.txt / README_如何在PyCharm中打开.md")

    # 引擎库在工程之外（原工作区），副本靠这个文件找到它们。
    # 换机器时只改这两个路径，或设 HC_OPENDSS_PYLIB / HC_PANDAPOWER_PYLIB 覆盖。
    import json as _json

    ws = SRC.parent
    cfg = {
        "opendss": [str(ws / "opendss_demo" / "pylibs")],
        "pandapower": [str(ws / "pylibs_pandapower")],
        "note": ("引擎库体积大且位于工程之外，不随工程复制；"
                 "本文件告诉运行时去哪里找。"),
    }
    (DST / "local_pylibs.json").write_text(
        _json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("  [新增] local_pylibs.json（指向原工作区的引擎库）")

    # 安全检查：确认密钥没被带过去
    for bad in ("llm.json", "chat.sqlite", "chat.sqlite-wal", "dss_cases.json"):
        assert not (DST / bad).exists(), f"不该存在的文件被复制了：{bad}"
    # hc_agent_ui 只带源码与启动器：这两个重目录绝不能出现
    assert not (DST / "hc_agent_ui" / "node_modules").exists(), \
        "node_modules 不该被复制（259MB）"
    assert not (DST / "hc_agent_ui" / "profile").exists(), \
        "Electron profile 是用户数据，不该被复制"

    n = sum(1 for _ in DST.rglob("*") if _.is_file())
    size = sum(p.stat().st_size for p in DST.rglob("*") if p.is_file())
    print(f"\n完成：{DST}")
    print(f"  {n} 个文件，{size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
