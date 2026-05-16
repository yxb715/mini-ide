"""从 emoji 或文字生成 mini-ide 图标

用法：
    poetry run python tools/make_icon.py              # 用默认的 🛠 + 渐变背景
    poetry run python tools/make_icon.py --text MI    # 自定义文字
    poetry run python tools/make_icon.py --emoji 🚀    # 自定义 emoji
    poetry run python tools/make_icon.py --bg "#4aa3ff"  # 自定义底色

产物：
    src/resources/icon.png   (256×256)
    src/resources/icon.ico   (多尺寸 16/32/48/64/128/256)

之后 run.bat / mini-ide.vbs 启动窗口就会显示新图标；
重新跑 build.bat 打包出来的 exe 也会用这个图标。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESOURCES = ROOT / "src" / "resources"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emoji", default="🛠", help="emoji 字符（默认 🛠）")
    parser.add_argument("--text", default="", help="若指定，用文字代替 emoji")
    parser.add_argument("--bg", default="linear:#4aa3ff:#2d4263",
                        help="背景色：纯色用 '#4aa3ff'，渐变用 'linear:起始:结束'")
    parser.add_argument("--fg", default="#ffffff", help="前景色（文字/emoji 颜色）")
    parser.add_argument("--size", type=int, default=256)
    args = parser.parse_args()

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("需要先装 Pillow：poetry install", file=sys.stderr)
        return 1

    RESOURCES.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGBA", (args.size, args.size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景
    if args.bg.startswith("linear:"):
        parts = args.bg.split(":")
        c1 = _hex_to_rgb(parts[1])
        c2 = _hex_to_rgb(parts[2]) if len(parts) > 2 else c1
        # 简易垂直渐变
        for y in range(args.size):
            t = y / (args.size - 1)
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            draw.line([(0, y), (args.size, y)], fill=(r, g, b, 255))
    else:
        draw.rectangle([0, 0, args.size, args.size], fill=_hex_to_rgb(args.bg) + (255,))

    # 圆角遮罩
    mask = Image.new("L", (args.size, args.size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, args.size, args.size],
                                 radius=int(args.size * 0.22), fill=255)
    rounded = Image.new("RGBA", (args.size, args.size), (0, 0, 0, 0))
    rounded.paste(img, (0, 0), mask)
    img = rounded

    # 前景文字/emoji
    content = args.text or args.emoji
    font = _pick_font(int(args.size * 0.55), emoji=not args.text)
    draw = ImageDraw.Draw(img)

    # 测量并居中
    try:
        bbox = draw.textbbox((0, 0), content, font=font, embedded_color=True)
    except TypeError:
        bbox = draw.textbbox((0, 0), content, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (args.size - tw) // 2 - bbox[0]
    y = (args.size - th) // 2 - bbox[1]

    try:
        draw.text((x, y), content, font=font, fill=_hex_to_rgb(args.fg) + (255,),
                  embedded_color=True)
    except TypeError:
        draw.text((x, y), content, font=font, fill=_hex_to_rgb(args.fg) + (255,))

    png_path = RESOURCES / "icon.png"
    ico_path = RESOURCES / "icon.ico"
    img.save(png_path, format="PNG")

    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=ico_sizes)

    print(f"已生成: {png_path}")
    print(f"已生成: {ico_path}")
    print("\n下一步：")
    print("  · 重启 mini-ide（run.bat 或 mini-ide.vbs）→ 窗口标题栏会显示新图标")
    print("  · 重新跑 build.bat → exe 文件本身带新图标")
    return 0


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return tuple(int(s[i:i+2], 16) for i in (0, 2, 4))


def _pick_font(size: int, emoji: bool):
    """找一个能渲染 emoji / 中文的字体"""
    from PIL import ImageFont
    candidates_win = [
        r"C:\Windows\Fonts\seguiemj.ttf",   # Segoe UI Emoji（emoji 彩色）
        r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑（中文）
        r"C:\Windows\Fonts\segoeui.ttf",    # Segoe UI
    ]
    order = candidates_win if emoji else [candidates_win[1], candidates_win[2], candidates_win[0]]
    for p in order:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


if __name__ == "__main__":
    sys.exit(main())
