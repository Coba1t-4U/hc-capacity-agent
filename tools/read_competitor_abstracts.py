# -*- coding: utf-8 -*-
"""读取竞争文献摘要（OpenAlex），判断其是否覆盖本方案的三个空白。

关注三点：
  1) 是否给出"边界层面的代数表达"（而非仅求解流程分层）
  2) 是否假设"下层可行域给定"
  3) 投影对象是"运行可行域"还是"容量/承载力边界"
"""
import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "hc-agent-lit-review/1.0 (mailto:dev@example.com)"}

DOIS = [
    ("Hu 2026 输配协同 PV 承载力", "10.1016/j.ijepes.2026.112074"),
    ("Duan 2021 输配一体化并行承载力", "10.1109/pesgm46819.2021.9638067"),
    ("Yang 2026 可行域投影（输配）", "10.1109/epsic70071.2026.11590289"),
    ("Tan 2026 分层最大承载力", "10.1109/iceaai68945.2026.11442460"),
]


def get(doi: str, tries: int = 3) -> dict:
    url = "https://api.openalex.org/works/doi:" + urllib.parse.quote(doi)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as exc:
            if "429" in str(exc):
                time.sleep(3 * (i + 1))
                continue
            return {"error": str(exc)}
    return {"error": "retry exhausted"}


def abstract(w: dict) -> str:
    inv = w.get("abstract_inverted_index")
    if not inv:
        return "(OpenAlex 无摘要)"
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


if __name__ == "__main__":
    for name, doi in DOIS:
        w = get(doi)
        time.sleep(1.5)
        if "error" in w:
            print(f"\n=== {name} ===\n  取回失败：{w['error'][:60]}")
            continue
        au = ", ".join(a["author"]["display_name"]
                       for a in (w.get("authorships") or [])[:4])
        print(f"\n=== {name} ===")
        print(f"  {w.get('publication_year')} | {w.get('title')}")
        print(f"  作者: {au}")
        print(f"  期刊: {(w.get('primary_location') or {}).get('source', {}).get('display_name')}")
        txt = abstract(w)
        print("  摘要: " + (txt[:700] if txt else "(无)"))
