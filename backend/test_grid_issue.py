"""测试九宫格切割是否正常工作"""
from pathlib import Path
from app.services.image import _split_grid, GRID_RC
from PIL import Image, ImageDraw

# 创建一个测试用的3x3大图（带编号）
test_dir = Path("test_grid_debug")
test_dir.mkdir(exist_ok=True)

canvas_size = (2304, 2304)
img = Image.new("RGB", canvas_size, (255, 255, 255))
draw = ImageDraw.Draw(img)

rows, cols = GRID_RC
cw, ch = canvas_size[0] // cols, canvas_size[1] // rows

for i in range(9):
    r, c = i // cols, i % cols
    x0, y0 = c * cw, r * ch
    x1, y1 = (c + 1) * cw, (r + 1) * ch
    # 画格子边框
    draw.rectangle([x0, y0, x1, y1], outline=(200, 200, 200), width=5)
    # 写编号
    text = f"#{i+1}"
    draw.text((x0 + cw//2 - 20, y0 + ch//2 - 20), text, fill=(0, 0, 0))

grid_path = test_dir / "test_grid.png"
img.save(grid_path)
print(f"Created test grid: {grid_path}")

# 测试切割
out_paths = [test_dir / f"split_{i+1}.png" for i in range(9)]
sub_types = ["content"] * 9
durations = [5] * 9

try:
    result = _split_grid(grid_path, out_paths, sub_types, durations, "9:16")
    print(f"\nSplit SUCCESS!")
    print(f"  Returned {len(result)} images")
    for i, r in enumerate(result):
        p = Path(r.path)
        if p.exists():
            print(f"  #{i+1}: {p.name} - {p.stat().st_size} bytes - grid={r.meta.get('grid')}")
        else:
            print(f"  #{i+1}: MISSING")
except Exception as e:
    print(f"\nSplit FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
