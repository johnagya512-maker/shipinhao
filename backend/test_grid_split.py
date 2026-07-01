"""测试九宫格切割逻辑 - 用于诊断切割错误问题"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# 模拟代码中的常量
GRID_RC = (3, 3)
WIDTH, HEIGHT = 1080, 1920

def create_test_grid(output_path: Path, canvas_size=(2304, 2304)):
    """创建一个带编号的测试九宫格图，用于验证切割是否正确"""
    img = Image.new("RGB", canvas_size, (255, 255, 255))
    draw = ImageDraw.Draw(img)

    rows, cols = GRID_RC
    cw, ch = canvas_size[0] // cols, canvas_size[1] // rows

    # 画网格线（白色，24像素宽）
    line_width = 24
    for i in range(1, cols):
        x = i * cw
        draw.rectangle([x - line_width//2, 0, x + line_width//2, canvas_size[1]], fill=(255, 255, 255))
    for i in range(1, rows):
        y = i * ch
        draw.rectangle([0, y - line_width//2, canvas_size[0], y + line_width//2], fill=(255, 255, 255))

    # 填充每个格子不同的颜色和编号
    colors = [
        (255, 200, 200),  # 浅红
        (200, 255, 200),  # 浅绿
        (200, 200, 255),  # 浅蓝
        (255, 255, 200),  # 浅黄
        (255, 200, 255),  # 浅紫
        (200, 255, 255),  # 浅青
        (255, 220, 180),  # 浅橙
        (220, 180, 255),  # 浅紫蓝
        (180, 255, 220),  # 浅青绿
    ]

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 200)
    except:
        font = ImageFont.load_default()

    for idx in range(9):
        r, c = idx // cols, idx % cols
        x0, y0 = c * cw, r * ch
        x1, y1 = (c + 1) * cw, (r + 1) * ch

        # 填充颜色（避开边缘的白线）
        margin = line_width
        draw.rectangle([x0 + margin, y0 + margin, x1 - margin, y1 - margin], fill=colors[idx])

        # 画编号
        text = f"#{idx+1}"
        # 使用 textbbox 获取文本大小
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        text_x = x0 + (cw - text_w) // 2
        text_y = y0 + (ch - text_h) // 2
        draw.text((text_x, text_y), text, fill=(0, 0, 0), font=font)

    img.save(output_path, "PNG")
    print(f"测试九宫格图已创建: {output_path}")
    print(f"画布尺寸: {canvas_size}, 每格: {cw}x{ch}")


def split_grid_test(grid_path: Path, output_dir: Path):
    """使用代码中的切割逻辑切割测试图"""
    img = Image.open(grid_path).convert("RGB")
    GW, GH = img.size
    rows, cols = GRID_RC
    cw, ch = GW // cols, GH // rows

    # 10% inset（和代码中一样）
    inset_x = int(cw * 0.10)
    inset_y = int(ch * 0.10)

    print(f"\n切割参数:")
    print(f"  大图尺寸: {GW}x{GH}")
    print(f"  每格: {cw}x{ch}")
    print(f"  inset: {inset_x}x{inset_y} (10%)")

    output_dir.mkdir(parents=True, exist_ok=True)

    for i in range(9):
        r, c = i // cols, i % cols
        x0, y0 = c * cw + inset_x, r * ch + inset_y
        x1, y1 = c * cw + cw - inset_x, r * ch + ch - inset_y

        cell = img.crop((x0, y0, x1, y1))

        # 缩放到目标尺寸（模拟代码中的 _normalize）
        dst_ratio = WIDTH / HEIGHT  # 9:16 = 0.5625
        src_ratio = cell.width / cell.height

        if src_ratio > dst_ratio:
            # 格子比目标更宽：裁掉左右
            tw = int(cell.height * dst_ratio)
            left = (cell.width - tw) // 2
            cell = cell.crop((left, 0, left + tw, cell.height))
        elif src_ratio < dst_ratio:
            # 格子比目标更高：裁掉上下
            th = int(cell.width / dst_ratio)
            top = (cell.height - th) // 2
            cell = cell.crop((0, top, cell.width, top + th))

        cell = cell.resize((WIDTH, HEIGHT), Image.LANCZOS)

        out_path = output_dir / f"cell_{i+1}.png"
        cell.save(out_path, "PNG")
        print(f"  格子 {i+1}: 裁剪区域 ({x0},{y0})-({x1},{y1}) -> {out_path.name}")


if __name__ == "__main__":
    test_dir = Path("storage/_test_grid")
    test_dir.mkdir(parents=True, exist_ok=True)

    # 创建测试九宫格
    test_grid_path = test_dir / "test_grid_3x3.png"
    create_test_grid(test_grid_path, (2304, 2304))

    # 切割测试
    output_dir = test_dir / "split_output"
    split_grid_test(test_grid_path, output_dir)

    print(f"\n测试完成！")
    print(f"  原始测试图: {test_grid_path}")
    print(f"  切割结果: {output_dir}")
    print(f"\n请查看切割结果，每张图应该显示对应的编号(#1-#9)")
    print(f"如果编号错位，说明切割逻辑有问题")
