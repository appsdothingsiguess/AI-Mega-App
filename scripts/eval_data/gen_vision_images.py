#!/usr/bin/env python3
"""Generate synthetic test images for Test 4's vision quality eval.
Ground truth for each image is encoded in the filename / vision_prompts.json,
not detectable from pixels alone (so it's a real check, not a memorized answer).
"""
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

OUT = Path(__file__).parent / "images"
OUT.mkdir(exist_ok=True)

# 1. Object counting: 7 red circles, 3 blue squares scattered
img = Image.new("RGB", (600, 400), "white")
d = ImageDraw.Draw(img)
red_positions = [(40,40),(150,80),(300,50),(450,90),(80,250),(250,300),(500,280)]
blue_positions = [(200,180),(380,200),(520,150)]
for x, y in red_positions:
    d.ellipse([x, y, x+50, y+50], fill="red")
for x, y in blue_positions:
    d.rectangle([x, y, x+50, y+50], fill="blue")
img.save(OUT / "count_shapes.png")

# 2. Spatial relationship: a green triangle, yellow star(circle proxy), purple square in a row
img2 = Image.new("RGB", (600, 200), "white")
d2 = ImageDraw.Draw(img2)
d2.polygon([(80,150),(130,50),(180,150)], fill="green")   # triangle at left
d2.ellipse([280, 60, 340, 120], fill="yellow")             # circle in middle
d2.rectangle([460, 60, 520, 120], fill="purple")           # square at right
img2.save(OUT / "spatial_row.png")

# 3. Text-in-image: render a short sentence as an image (OCR-style read)
img3 = Image.new("RGB", (600, 150), "white")
d3 = ImageDraw.Draw(img3)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
except Exception:
    font = ImageFont.load_default()
d3.text((30, 55), "SHIPMENT CODE: QX-4471-B", fill="black", font=font)
img3.save(OUT / "text_read.png")

# 4. Chart reading: simple bar chart with known values
fig, ax = plt.subplots(figsize=(6, 4))
categories = ["Q1", "Q2", "Q3", "Q4"]
values = [12, 19, 8, 25]
ax.bar(categories, values, color="steelblue")
ax.set_title("Quarterly Widget Sales (thousands)")
for i, v in enumerate(values):
    ax.text(i, v + 0.5, str(v), ha="center")
fig.savefig(OUT / "chart_bars.png", dpi=100)
plt.close(fig)

# 5. Second counting variant with overlapping/harder-to-count objects (9 dots in a cluster)
img5 = Image.new("RGB", (400, 400), "white")
d5 = ImageDraw.Draw(img5)
dot_positions = [(100,100),(140,110),(180,95),(120,150),(160,160),(200,145),(110,200),(150,210),(190,195)]
for x, y in dot_positions:
    d5.ellipse([x, y, x+30, y+30], fill="black")
img5.save(OUT / "count_cluster.png")

print("Generated 5 images in", OUT)
for f in sorted(OUT.glob("*.png")):
    print(" ", f.name)
