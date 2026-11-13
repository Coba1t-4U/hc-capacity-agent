# -*- coding: utf-8 -*-
"""用 OpenAlex 真实核验方案中提到的文献（作者+年份+主题）。"""
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "hc-agent-citation-check/1.0 (mailto:dev@example.com)"}


def q(params):
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except Exception as exc:
        return {"error": str(exc)}


def show(name, params):
    r = q(params)
    if "error" in r:
        print(f"  {name}: 失败 {r['error'][:60]}")
        return
    meta = r.get("meta") or {}
    print(f"  {name}: 命中 {meta.get('count')}")
    for w in (r.get("results") or [])[:4]:
        aus = ", ".join(a["author"]["display_name"]
                        for a in (w.get("authorships") or [])[:3])
        print("    -", w.get("publication_year"), "|",
              (w.get("title") or "")[:60], "|", aus, "|", w.get("doi") or "无 DOI")


CASES = [
    ("王守相 2025 承载力", {"filter": "raw_author_name.search:Shouxiang Wang,"
                                     "publication_year:2025", "per-page": 5}),
    ("王守相 hosting capacity", {"filter": "raw_author_name.search:Shouxiang Wang,"
                                          "title_and_abstract.search:hosting capacity",
                                "per-page": 5}),
    ("hosting capacity 2023+", {"filter": "title_and_abstract.search:hosting capacity,"
                                         "from_publication_date:2023-01-01", "per-page": 4}),
    ("distributionally robust hosting capacity", {
        "filter": "title_and_abstract.search:distributionally robust hosting capacity",
        "per-page": 4}),
    ("多层/多电压等级 hosting capacity", {
        "filter": "title_and_abstract.search:multi-level hosting capacity", "per-page": 4}),
]

if __name__ == "__main__":
    for n, p in CASES:
        show(n, p)
