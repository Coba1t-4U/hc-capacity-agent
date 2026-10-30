# -*- coding: utf-8 -*-
"""把桌面工程的解释器注册进 PyCharm 的全局 SDK 表。

为什么单独做这一步：`.idea/misc.xml` 里写的是 SDK **名字**，而名字必须
存在于 PyCharm 的全局 `jdk.table.xml` 里，否则打开工程会提示"解释器无效"。

**必须在 PyCharm 关闭时运行**：它在退出时会回写自己的内存配置，运行中写入会被覆盖。

用法：
    python tools\\_register_pycharm_sdk.py --dry-run   # 只在临时副本上验证逻辑
    python tools\\_register_pycharm_sdk.py             # 真正写入（会先备份）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

CONFIG = Path.home() / "AppData/Roaming/JetBrains/PyCharm2025.3/options"
JDK_TABLE = CONFIG / "jdk.table.xml"
TRUSTED = CONFIG / "trusted-paths.xml"

PROJ = Path.home() / "Desktop" / "配网承载力智能体Agent"
SDK_NAME = "Python 3.12 (hc_agent)"
PYTHON = Path(r"C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime"
              r"\dependencies\python\python.exe")
PY_HOME = PYTHON.parent


def _root_url(p: Path) -> str:
    return "file://" + str(p).replace("\\", "/")


def _sdk_xml() -> str:
    uuid4 = str(uuid.uuid4())
    roots = [
        PY_HOME / "DLLs", PY_HOME / "Lib", PY_HOME,
        PY_HOME / "Lib" / "site-packages",
    ]
    inner = "\n".join(
        f'                <root url="{_root_url(r)}" type="simple" />'
        for r in roots if r.exists())
    inner += ('\n                <root url="file://$APPLICATION_HOME_DIR$/plugins/'
              'python-ce/helpers/typeshed/stdlib" type="simple" />')
    return f"""      <jdk version="2">
        <name value="{SDK_NAME}" />
        <type value="Python SDK" />
        <version value="Python 3.12.14" />
        <homePath value="{PYTHON}" />
        <roots>
          <classPath>
            <root type="composite">
{inner}
            </root>
          </classPath>
          <sourcePath>
            <root type="composite" />
          </sourcePath>
        </roots>
        <additional SDK_UUID="{uuid4}">
          <setting name="FLAVOR_ID" value="UnknownSdkFlavor" />
          <setting name="FLAVOR_DATA" value="{{}}" />
        </additional>
      </jdk>
"""


def _inject_jdk(src: Path) -> str:
    """在 ProjectJdkTable 里插入 SDK（已存在则跳过）。"""
    text = src.read_text(encoding="utf-8") if src.is_file() else (
        '<application>\n  <component name="ProjectJdkTable">\n'
        '  </component>\n</application>\n')
    if SDK_NAME in text:
        return text
    marker = "</component>"
    i = text.find(marker)
    if i < 0:
        raise SystemExit("jdk.table.xml 结构异常：找不到 </component>")
    return text[:i] + _sdk_xml() + text[i:]


def _inject_trusted(src: Path) -> str:
    text = src.read_text(encoding="utf-8") if src.is_file() else (
        '<application>\n  <component name="Trusted.Paths">\n'
        '    <option name="TRUSTED_PROJECT_PATHS">\n      <map>\n'
        '      </map>\n    </option>\n  </component>\n</application>\n')
    key = str(PROJ)
    if key in text:
        return text
    probe = ('<entry key="' + key + '" value="true" />')
    for anchor in ("</map>", "</option>", "</component>"):
        i = text.find(anchor)
        if i >= 0:
            return text[:i] + "        " + probe + "\n      " + text[i:]
    return text


def _backup(p: Path) -> Path | None:
    if not p.is_file():
        return None
    b = p.with_suffix(p.suffix + f".bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(p, b)
    return b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="只在临时副本上演练，不动真实配置")
    args = ap.parse_args()

    if not PYTHON.is_file():
        print(f"[失败] 解释器不存在：{PYTHON}")
        return 2

    if args.dry_run:
        tmp = Path.home() / "AppData/Local/Temp" / f"dsh-ide-dryrun-{uuid.uuid4().hex[:6]}"
        tmp.mkdir(parents=True, exist_ok=True)
        for name, inject in (("jdk.table.xml", _inject_jdk),
                             ("trusted-paths.xml", _inject_trusted)):
            src = CONFIG / name
            out = tmp / name
            out.write_text(inject(src), encoding="utf-8")
            try:
                ET.parse(out)
                print(f"  [dry-run] {name}: 注入后 XML 合法")
            except ET.ParseError as exc:
                print(f"  [dry-run] {name}: XML 非法 -> {exc}")
                return 2
        print(f"  [dry-run] 副本目录：{tmp}")
        txt = (tmp / "jdk.table.xml").read_text(encoding="utf-8")
        print(f"  [dry-run] 含 SDK 名：{SDK_NAME in txt}")
        print(f"  [dry-run] 含解释器路径：{str(PYTHON) in txt}")
        return 0

    # 运行中检测：PyCharm 在跑就别写，否则退出时会被覆盖
    import subprocess
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pycharm64.exe"],
                           capture_output=True, text=True, timeout=20)
        if "pycharm64.exe" in (r.stdout or "").lower():
            print("[中止] PyCharm 正在运行。请先关闭 PyCharm 再运行本脚本，"
                  "否则写入会被 IDE 退出时覆盖。")
            return 3
    except Exception:
        pass

    for name, inject in (("jdk.table.xml", _inject_jdk),
                         ("trusted-paths.xml", _inject_trusted)):
        p = CONFIG / name
        bak = _backup(p)
        new = inject(p)
        hdr = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               if not new.lstrip().startswith("<?xml") else "")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(hdr + new, encoding="utf-8")
        ET.parse(p)          # 写完立刻验回
        print(f"  {name}: 已写入"
              + (f"（备份 {bak.name}）" if bak else "（新建）"))

    print()
    print(f"完成。工程：{PROJ}")
    print(f"  解释器：{SDK_NAME} -> {PYTHON}")
    print("  现在重新打开 PyCharm 的该工程，解释器应已是上面这个。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
