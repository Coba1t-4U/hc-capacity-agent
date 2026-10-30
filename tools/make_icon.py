"""Generate a multi-size .ico for the desktop shortcut.

Uses Pillow (bundled with the DSH Python). Draws a simple, legible mark:
a dark rounded tile with a blue bus bar and a green feeder dropping to a
node -- i.e. "distribution network". Kept deliberately simple so it stays
readable at 16x16.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "hc_agent_ui" / "assets" / "hc_agent.ico"
OUT.parent.mkdir(parents=True, exist_ok=True)

BASE = 256
img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# --- rounded dark tile ---
pad = 8
r = 48
d.rounded_rectangle([pad, pad, BASE - pad, BASE - pad], radius=r,
                    fill=(21, 27, 35, 255), outline=(58, 70, 85, 255), width=6)

# --- bus bar (horizontal, blue) ---
bus_y = 78
d.rounded_rectangle([56, bus_y - 11, BASE - 56, bus_y + 11], radius=11,
                    fill=(88, 166, 255, 255))

# --- feeders dropping from the bus to nodes (green) ---
for x in (92, 164):
    d.rounded_rectangle([x - 7, bus_y, x + 7, 168], radius=7,
                        fill=(63, 185, 80, 255))
# cross feeder
d.rounded_rectangle([92, 161, 164, 175], radius=7,
                    fill=(63, 185, 80, 255))

# --- nodes ---
for cx, cy in ((92, 186), (164, 186), (128, 122)):
    rad = 20
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
              fill=(230, 237, 243, 255), outline=(63, 185, 80, 255), width=6)

# --- incoming source marker (amber) on the bus ---
d.ellipse([64 - 14, bus_y - 14, 64 + 14, bus_y + 14],
          fill=(210, 153, 34, 255))

img.save(OUT, format="ICO",
         sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32),
                (24, 24), (16, 16)])
print("wrote", OUT, OUT.stat().st_size, "bytes")

# quick sanity: reopen and list frames
with Image.open(OUT) as im:
    print("sizes in ico:", sorted(im.ico.sizes()))
