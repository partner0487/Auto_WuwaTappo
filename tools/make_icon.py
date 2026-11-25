"""由 logo 圖（預設 assets/logo.jfif，未收進 repo）產生 App 圖示：去白背景 → 置中成正方形 → 右下角加滑鼠點擊游標。

輸出 assets/icon.png（512px）與 assets/icon.ico（16~256 多尺寸）。
用法：python tools/make_icon.py [logo路徑]
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "assets", "logo.jfif")
OUT_DIR = os.path.join(ROOT, "assets")
SIZE = 512


def remove_white_bg(rgb):
    """從邊界 flood fill 近白色當背景（內部的白色如眼睛高光會保留），邊緣做柔化。"""
    h, w = rgb.shape[:2]
    minc = rgb.min(axis=2)
    near_white = (minc > 228).astype(np.uint8)
    # 從四邊所有近白像素出發的連通區域 = 背景
    n, labels = cv2.connectedComponents(near_white, connectivity=4)
    border = set(np.unique(np.concatenate(
        [labels[0], labels[-1], labels[:, 0], labels[:, -1]]))) - {0}
    bg = np.isin(labels, list(border))
    alpha = np.where(bg, 0, 255).astype(np.float32)
    # 背景交界 2px 內依「離白色多遠」給半透明，消掉 JPEG 白邊
    edge = cv2.dilate(bg.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~bg
    alpha[edge] = np.clip((255 - minc[edge].astype(np.float32)) * 255 / 60, 0, 255)
    # 去掉殘留的小雜點（JPEG 噪點）
    solid = (alpha > 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 15:
            alpha[lab == i] = 0
    rgba = np.dstack([rgb, alpha.astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def fit_square(img, size, margin=0.04):
    img = img.crop(img.getbbox())
    s = size * (1 - 2 * margin) / max(img.size)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    return canvas


def draw_cursor(size):
    """右下角的滑鼠游標＋點擊波紋，用 4 倍解析度畫再縮小（抗鋸齒）。"""
    k = 4
    S = size * k
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u = S / 512 * 0.8  # 以 512 為基準的單位（游標整體縮小 0.8 倍）
    tx, ty = 400 * S / 512, 345 * S / 512  # 游標尖端，放右下角避開臉
    # 經典箭頭游標（相對尖端的座標，512 基準）
    pts = [(0, 0), (0, 170), (40, 132), (70, 200), (102, 186), (72, 120), (126, 120)]
    poly = [(tx + x * u, ty + y * u) for x, y in pts]
    # 陰影
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + 10 * u, y + 10 * u) for x, y in poly], fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8 * u))
    layer.alpha_composite(shadow)
    d.polygon(poly, fill=(255, 255, 255, 255), outline=(25, 25, 35, 255), width=round(13 * u))
    # 點擊波紋：尖端左上的三條短線
    import math
    for ang in (200, 245, 290):
        a = math.radians(ang)
        r0, r1 = 34 * u, 82 * u
        d.line([(tx + r0 * math.cos(a), ty + r0 * math.sin(a)),
                (tx + r1 * math.cos(a), ty + r1 * math.sin(a))],
               fill=(255, 196, 64, 255), width=round(18 * u))
        d.line([(tx + r0 * math.cos(a), ty + r0 * math.sin(a)),
                (tx + r1 * math.cos(a), ty + r1 * math.sin(a))],
               fill=(25, 25, 35, 255), width=round(6 * u))
    return layer.resize((size, size), Image.LANCZOS)


def main():
    if not os.path.exists(SRC):
        sys.exit(f"找不到 logo 原圖：{SRC}（用法：python tools/make_icon.py <圖片路徑>）")
    os.makedirs(OUT_DIR, exist_ok=True)
    rgb = np.array(Image.open(SRC).convert("RGB"))
    logo = fit_square(remove_white_bg(rgb), SIZE)
    icon = logo.copy()
    icon.alpha_composite(draw_cursor(SIZE))
    icon.save(os.path.join(OUT_DIR, "icon.png"))
    icon.save(os.path.join(OUT_DIR, "icon.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("已輸出", OUT_DIR)


if __name__ == "__main__":
    main()
