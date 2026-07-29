#!/usr/bin/env python3
"""
Generate the PWA icons with headless Chrome. No image libraries needed.

⚠️ These are REQUIRED, not decoration. Chrome will not offer "Add to Home
Screen" without at least a 192px and a 512px icon in the manifest, and without
the app installed to the home screen Android web push does not work. An empty
icons array silently costs you the entire notification feature.

The mark is a smoker dome — Uncle BBQ, unexplained, as it should be.

    python3 web/icons/generate_icons.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = Path(__file__).parent

BG = "#141210"
HOT = "#FFA216"
EMBER = "#E2694F"


def art(scale: float = 1.0, mono: bool = False) -> str:
    """
    The mark, drawn in a 100x100 box. `scale` shrinks it for maskable icons,
    which get cropped to a circle by the launcher — anything outside the inner
    ~80% can be cut off.
    """
    body = HOT if not mono else "#FFFFFF"
    lid = EMBER if not mono else "#FFFFFF"
    return f'''
    <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
      <g transform="translate(50 50) scale({scale}) translate(-50 -50)">
        <!-- smoke -->
        <path d="M38 20 q6 -7 0 -13 q-6 -6 0 -12" stroke="{lid}" stroke-width="4"
              fill="none" stroke-linecap="round" opacity="{0.9 if mono else 0.75}"/>
        <path d="M56 22 q5 -6 0 -11" stroke="{lid}" stroke-width="4"
              fill="none" stroke-linecap="round" opacity="{0.9 if mono else 0.5}"/>
        <!-- dome -->
        <path d="M18 56 a32 32 0 0 1 64 0 z" fill="{lid}"/>
        <!-- body -->
        <rect x="16" y="56" width="68" height="9" rx="4.5" fill="{body}"/>
        <path d="M24 65 h52 l-7 14 h-38 z" fill="{body}"/>
        <!-- legs -->
        <path d="M31 79 l-7 12 M69 79 l7 12" stroke="{body}" stroke-width="6"
              stroke-linecap="round" fill="none"/>
      </g>
    </svg>'''


def page(size: int, scale: float, transparent: bool, mono: bool) -> str:
    bg = "transparent" if transparent else BG
    radius = "22%" if not transparent else "0"
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;padding:0;width:{size}px;height:{size}px;background:transparent}}
      .card{{width:{size}px;height:{size}px;background:{bg};border-radius:{radius};
             display:flex;align-items:center;justify-content:center}}
      svg{{width:{size}px;height:{size}px}}
    </style></head><body><div class="card">{art(scale, mono)}</div></body></html>'''


def render(name: str, size: int, scale: float = 1.0, transparent: bool = False,
           mono: bool = False) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(page(size, scale, transparent, mono))
        src = f.name
    dest = OUT / name
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
        "--default-background-color=00000000",
        f"--screenshot={dest}", f"--window-size={size},{size}", f"file://{src}",
    ], check=True, capture_output=True)
    print(f"  {name}  {dest.stat().st_size:,} bytes")


def main() -> int:
    if not Path(CHROME).exists():
        print(f"Chrome not found at {CHROME}")
        return 1
    print("rendering icons…")
    render("icon-192.png", 192)
    render("icon-512.png", 512)
    # Maskable: launchers crop to a circle, so keep the art inside the safe zone.
    render("maskable-512.png", 512, scale=0.68)
    # Notification badge: Android renders it as a white silhouette.
    render("badge.png", 96, scale=0.9, transparent=True, mono=True)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
