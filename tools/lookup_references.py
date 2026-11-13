# -*- coding: utf-8 -*-
"""按主题反查候选文献（带 429 重试与节流），用于核验方案中的作者-年份引用。

OpenAlex 无密钥、有速率限制：每条查询间隔 1.5s，429 时退避重试 3 次。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "hc-agent-citation-check/1.0 (mailto:dev@example.com)"}


def q(params: dict, tries: int = 3) -> dict:
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as exc:
            last = str(exc)
            if "429" in last:
                time.sleep(3 * (i + 1))
                continue
            break
    return {"error": last}


def show(name: str, params: dict, n: int = 4) -> None:
    r = q(params)
    time.sleep(1.5)
    if "error" in r:
        print(f"\n[{name}] 查询失败：{r['error'][:60]}")
        return
    cnt = (r.get("meta") or {}).get("count")
    print(f"\n[{name}] 命中 {cnt}")
    for w in (r.get("results") or [])[:n]:
        aus = ", ".join(a["author"]["display_name"]
                        for a in (w.get("authorships") or [])[:3])
        print(f"  - {w.get('publication_year')} | {(w.get('title') or '')[:66]}")
        print(f"      {aus} | {w.get('doi') or '无 DOI'}")


CASES = [
    ("多层级承载力 multi-level hosting capacity",
     {"filter": "title_and_abstract.search:multi-level hosting capacity,from_publication_date:2020-01-01", "per-page": 5}),
    ("分层评估 hierarchical hosting capacity assessment",
     {"filter": "title_and_abstract.search:hierarchical hosting capacity assessment,from_publication_date:2020-01-01", "per-page": 5}),
    ("输配协同承载力 transmission-distribution hosting capacity",
     {"filter": "title_and_abstract.search:transmission distribution hosting capacity,from_publication_date:2019-01-01", "per-page": 5}),
    ("可行域投影 feasible region projection capacity",
     {"filter": "title_and_abstract.search:feasible region projection distribution network,from_publication_date:2019-01-01", "per-page": 5}),
    ("中文 承载力 层级",
     {"filter": "title_and_abstract.search:承载力 层级,from_publication_date:2021-01-01", "per-page": 5}),
]

if __name__ == "__main__":
    for name, params in CASES:
        show(name, params)
