# 在 PyCharm 里打开这个工程

## 一、打开

1. PyCharm → **File → Open** → 选择本文件夹（`配网承载力智能体Agent`）
2. 右下角如提示 *Trust project*，选 **Trust**
3. PyCharm 会自动识别 `pyproject.toml`，并在右下角提示创建虚拟环境 → 选 **OK**

## 二、配置解释器（关键）

本工程的引擎 `OpenDSSDirect.py` 已在本机一份 Python 里装好。最省事的做法是**直接复用那份解释器**：

`Settings → Project → Python Interpreter → Add Interpreter → System Interpreter`

填入：

```
C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
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
  （原工程：`C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project`）
- **没有带 `llm.json`**：里面有 API Key，不能跟着代码走。要启用大模型，
  在界面「配置」里重新填一次 Key，或从原工程复制 `llm.json` 过来。
- 没有带 `dist/`、`release/`（打包产物）和 `hc_agent_ui/`（261 MB 旧版前端）。
- `runs/ACCEPT` 是测试用的黄金运行，**删了部分测试会失败**。
