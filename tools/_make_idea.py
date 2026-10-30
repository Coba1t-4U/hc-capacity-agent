# -*- coding: utf-8 -*-
"""给桌面工程补上 PyCharm 工程配置（.idea + 模块 + 运行配置）。

为什么需要 .idea：只有它，PyCharm 才把这个目录当**工程**（记住解释器、
源码根、运行配置、编码），而不是当普通文件夹打开。

注意：项目解释器按名字引用（`Python 3.12 (hc_agent)`）。
这个名字要落在 PyCharm 的全局 SDK 表里，见 `注册PyCharm解释器.ps1` ——
在 PyCharm **关闭**状态下运行它，否则注册会被 IDE 退出时回写覆盖。
"""
import json
import uuid
from pathlib import Path

PROJ = Path.home() / "Desktop" / "配网承载力智能体Agent"
SDK_NAME = "Python 3.12 (hc_agent)"
PYTHON = (r"C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime"
          r"\dependencies\python\python.exe")
MODULE = "配网承载力智能体Agent"

MISC = f"""<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="Black">
    <option name="sdkName" value="{SDK_NAME}" />
  </component>
  <component name="ProjectRootManager" version="2"
             project-jdk-name="{SDK_NAME}" project-jdk-type="Python SDK" />
</project>
"""

MODULES = f"""<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="ProjectModuleManager">
    <modules>
      <module fileurl="file://$PROJECT_DIR$/{MODULE}.iml"
              filepath="$PROJECT_DIR$/{MODULE}.iml" />
    </modules>
  </component>
</project>
"""

IML = f"""<?xml version="1.0" encoding="UTF-8"?>
<module type="PYTHON_MODULE" version="4">
  <component name="NewModuleRootManager" inherit-compiler-output="true">
    <exclude-output />
    <content url="file://$MODULE_DIR$">
      <sourceFolder url="file://$MODULE_DIR$" isTestSource="false" />
      <excludeFolder url="file://$MODULE_DIR$/runs" />
      <excludeFolder url="file://$MODULE_DIR$/.venv" />
      <excludeFolder url="file://$MODULE_DIR$/__pycache__" />
    </content>
    <orderEntry type="jdk" jdkName="{SDK_NAME}" jdkType="Python SDK" />
    <orderEntry type="sourceFolder" forTests="false" />
  </component>
</module>
"""

ENCODINGS = """<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="Encoding" defaultCharsetForPropertiesFiles="UTF-8"
             addBOMForNewFiles="with NO BOM">
    <file url="PROJECT" charset="UTF-8" />
  </component>
</project>
"""

GITIGNORE = """# PyCharm 本机状态
workspace.xml
shelf/
usage.statistics.xml
httpRequests/
# SDK 表由 IDE 维护
jdk.table.xml
"""

RUN_APP = f"""<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="启动桌面版"
                 type="PythonConfigurationType" factoryName="Python">
    <module name="{MODULE}" />
    <option name="ENV_FILES" value="" />
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <envs>
      <env name="PYTHONUNBUFFERED" value="1" />
    </envs>
    <option name="SDK_HOME" value="" />
    <option name="WORKING_DIRECTORY" value="$PROJECT_DIR$" />
    <option name="IS_MODULE_SDK" value="true" />
    <option name="ADD_CONTENT_ROOTS" value="true" />
    <option name="ADD_SOURCE_ROOTS" value="true" />
    <option name="SCRIPT_NAME" value="$PROJECT_DIR$/启动桌面版.py" />
    <option name="PARAMETERS" value="" />
    <option name="SHOW_COMMAND_LINE" value="false" />
    <option name="EMULATE_TERMINAL" value="false" />
    <option name="MODULE_MODE" value="false" />
    <option name="REDIRECT_INPUT" value="false" />
    <option name="INPUT_FILE" value="" />
    <method v="2" />
  </configuration>
</component>
"""

RUN_ALL = f"""<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="一键自检（全部测试）"
                 type="PythonConfigurationType" factoryName="Python">
    <module name="{MODULE}" />
    <option name="ENV_FILES" value="" />
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <envs>
      <env name="PYTHONUNBUFFERED" value="1" />
    </envs>
    <option name="SDK_HOME" value="" />
    <option name="WORKING_DIRECTORY" value="$PROJECT_DIR$" />
    <option name="IS_MODULE_SDK" value="true" />
    <option name="ADD_CONTENT_ROOTS" value="true" />
    <option name="ADD_SOURCE_ROOTS" value="true" />
    <option name="SCRIPT_NAME" value="$PROJECT_DIR$/一键自检.py" />
    <option name="PARAMETERS" value="" />
    <option name="SHOW_COMMAND_LINE" value="false" />
    <option name="EMULATE_TERMINAL" value="true" />
    <option name="MODULE_MODE" value="false" />
    <option name="REDIRECT_INPUT" value="false" />
    <option name="INPUT_FILE" value="" />
    <method v="2" />
  </configuration>
</component>
"""

RUN_ALL_PY = '''"""一键跑完全部自检（部分套件需要 dist/ 会失败，属预期）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = sorted((ROOT / "tests").glob("*.py"))
SKIP = {"__init__.py"}

total_ok = total_all = 0
failed = []
for t in TESTS:
    if t.name in SKIP:
        continue
    r = subprocess.run([sys.executable, str(t)], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    line = next((ln.strip() for ln in reversed(r.stdout.splitlines())
                 if "结果：" in ln), "")
    print(f"{t.name:<28} exit={r.returncode}  {line}")
    if r.returncode != 0:
        failed.append(t.name)
    import re
    m = re.search(r"(\\d+)\\s*通过", line)
    n = re.search(r"/\\s*(\\d+)\\s*总计", line)
    if m:
        total_ok += int(m.group(1))
    if n:
        total_all += int(n.group(1))

print()
print(f"合计 {total_ok} 通过 / {total_all - total_ok} 失败 / {total_all} 项")
if failed:
    print("失败套件：", "、".join(failed))
raise SystemExit(1 if failed else 0)
'''


def main() -> None:
    if not PROJ.is_dir():
        raise SystemExit(f"工程不存在：{PROJ}")
    idea = PROJ / ".idea"
    (idea / "runConfigurations").mkdir(parents=True, exist_ok=True)

    (idea / "misc.xml").write_text(MISC, encoding="utf-8")
    (idea / "modules.xml").write_text(MODULES, encoding="utf-8")
    (idea / "encodings.xml").write_text(ENCODINGS, encoding="utf-8")
    (idea / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (PROJ / f"{MODULE}.iml").write_text(IML, encoding="utf-8")
    (idea / "runConfigurations" / "启动桌面版.xml").write_text(
        RUN_APP, encoding="utf-8")
    (idea / "runConfigurations" / "一键自检（全部测试）.xml").write_text(
        RUN_ALL, encoding="utf-8")
    (PROJ / "一键自检.py").write_text(RUN_ALL_PY, encoding="utf-8")

    sdk = {"name": SDK_NAME, "python": PYTHON,
           "uuid": str(uuid.uuid4()), "added_at": "auto"}
    (PROJ / ".pycharm_sdk.json").write_text(
        json.dumps(sdk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"已写入 .idea：{idea}")
    for p in sorted(idea.rglob("*")):
        if p.is_file():
            print("  " + str(p.relative_to(PROJ)))
    print("  " + f"{MODULE}.iml")
    print("  一键自检.py")


if __name__ == "__main__":
    main()
