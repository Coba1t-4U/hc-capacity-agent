"""把 DeepSeek API Key 安装到真实配置位置，并做连通性验证。

用法：python tools/install_api_key.py <key>
"""
import sys

sys.path.insert(0, r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")

key = sys.argv[1] if len(sys.argv) > 1 else ""
if not key.startswith("sk-"):
    print("KEY_INVALID: 需要 sk- 开头的 Key")
    raise SystemExit(2)

from hc_agent import llm, paths

print("数据目录:", paths.data_root_safe())
print("配置路径:", llm.config_path())

r = llm.save_config(key)
print("保存:", r)

cfg = llm.LLMConfig.from_env()
print("enabled:", cfg.enabled, "| model:", cfg.model, "| key:", cfg.masked())
st = llm.self_test(cfg)
print("连通性:", st)
raise SystemExit(0 if st.get("ok") else 1)
