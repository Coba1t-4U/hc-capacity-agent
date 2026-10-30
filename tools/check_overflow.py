# -*- coding: utf-8 -*-
"""估算文本框是否溢出（不依赖渲染），列出可疑形状。"""
import math
import sys
import unicodedata
from pathlib import Path

from pptx import Presentation

EMU_PT = 12700.0


def text_units(t: str) -> float:
    """中文按 1.0 em，西文按 0.55 em 估算。"""
    u = 0.0
    for ch in t:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            u += 1.0
        elif ch in "　":
            u += 1.0
        else:
            u += 0.55
    return u


def main(path):
    prs = Presentation(str(path))
    bad = []
    for idx, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:
            if not sh.has_text_frame or sh.top is None:
                continue
            txt_all = sh.text_frame.text.strip()
            if not txt_all:
                continue
            w_pt = sh.width / EMU_PT
            h_pt = sh.height / EMU_PT
            tf = sh.text_frame
            inner = w_pt - (tf.margin_left + tf.margin_right) / EMU_PT
            if inner <= 10:
                continue
            need = 0.0
            for p in tf.paragraphs:
                txt = "".join(r.text for r in p.runs)
                sizes = [r.font.size.pt for r in p.runs if r.font.size]
                size = max(sizes) if sizes else 18.0
                ls = p.line_spacing if isinstance(p.line_spacing, float) else 1.2
                units = text_units(txt) if txt else 0.4
                per_line = max(inner / size, 1.0)
                lines = max(1, math.ceil(units / per_line))
                need += lines * size * 1.22 * ls
                if p.space_before:
                    need += p.space_before.pt
            over = need - h_pt
            if over > h_pt * 0.10 and over > 6:
                bad.append((idx, sh.name, round(w_pt), round(h_pt),
                            round(need), round(over), txt_all[:38].replace("\n", " / ")))
    print(f"共发现 {len(bad)} 处疑似溢出（need > box 高度 10%）\n")
    for b in sorted(bad, key=lambda x: -x[5]):
        print(f"  第{b[0]:>2}页 {b[1]:<22} {b[2]:>4}x{b[3]:<4}pt "
              f"需要{b[4]:>5}pt 超出{b[5]:>5}pt  «{b[6]}»")
    return len(bad)


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1]) == 0 else 0)
