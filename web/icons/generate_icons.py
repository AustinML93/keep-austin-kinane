#!/usr/bin/env python3
"""
Generate the PWA icons from the source artwork, using headless Chrome.
No image libraries needed.

⚠️ These are REQUIRED, not decoration. Chrome will not offer "Add to Home
Screen" without at least a 192px and a 512px icon in the manifest, and Android
web push only works once the PWA is installed. An empty icons array silently
costs you the entire notification feature.

Source art is `source-beard-hat.png` — the bearded trucker-cap mark, cropped
from a Gemini-generated sheet Mike picked from. It is only 233px square, so the
512 renders are a ~2.2x upscale and are visibly soft; 192 is a downscale and
stays sharp. Regenerate from a larger original if the 512 ever matters. Two things
it has to survive:

  ANY      launchers draw it as-is, so it keeps its own rounded-square shape.
           The source tile has white page background in its corners, so we clip
           to a rounded rect rather than paint over them.

  MASKABLE launchers crop to a circle. Anything outside the inner ~80% can be
           cut off — which for this mark means the brim of the hat. So the art
           is scaled down onto a full-bleed background instead.

    python3 web/icons/generate_icons.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = Path(__file__).parent
SOURCE = OUT / "source-beard-hat.png"

# Sampled from the artwork itself so the padding is invisible.
TILE_BG = "#1f282d"
BADGE_FG = "#ffffff"


def page(size: int, *, scale: float, radius: str, transparent: bool,
         img_radius: str = "0") -> str:
    bg = "transparent" if transparent else TILE_BG
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;padding:0;width:{size}px;height:{size}px;background:transparent}}
      .card{{width:{size}px;height:{size}px;background:{bg};border-radius:{radius};
             overflow:hidden;display:flex;align-items:center;justify-content:center}}
      /* Overscan: the source tile has its own rounded corners and a dark rim.
         Scaling past the frame and clipping pushes that rim outside the crop,
         so we don't get a ring inside a ring. */
      /* The source tile carries its own rounded corners with WHITE page
         background outside them. Clipping the image to the same radius removes
         those corners; what's left is the tile's own dark field, which blends
         invisibly into the card behind it. Without this the maskable icon has
         four white notches. */
      img{{width:{size * scale}px;height:{size * scale}px;display:block;
           object-fit:cover;flex:none;border-radius:{img_radius}}}
    </style></head><body>
      <div class="card"><img src="file://{SOURCE}"></div>
    </body></html>'''


def badge_page(size: int) -> str:
    """
    Android renders the notification badge as an alpha silhouette, so colour is
    thrown away. A detailed mark turns into a grey blob — this draws a simple
    high-contrast glyph instead.
    """
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;padding:0;width:{size}px;height:{size}px;background:transparent}}
      svg{{width:{size}px;height:{size}px}}
    </style></head><body>
      <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <path d="M20 44 h60 v8 a30 30 0 0 1 -60 0 z" fill="{BADGE_FG}"/>
        <path d="M28 40 a22 22 0 0 1 44 0 z" fill="{BADGE_FG}" opacity=".85"/>
        <path d="M42 26 q6 -7 0 -13" stroke="{BADGE_FG}" stroke-width="6"
              fill="none" stroke-linecap="round"/>
        <path d="M60 26 q5 -6 0 -11" stroke="{BADGE_FG}" stroke-width="6"
              fill="none" stroke-linecap="round"/>
      </svg>
    </body></html>'''


def shoot(html: str, name: str, size: int) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        src = f.name
    dest = OUT / name
    subprocess.run([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--default-background-color=00000000",
        f"--screenshot={dest}", f"--window-size={size},{size}", f"file://{src}",
    ], check=True, capture_output=True)
    print(f"  {name:<22} {dest.stat().st_size:>7,} bytes")


def main() -> int:
    if not Path(CHROME).exists():
        print(f"Chrome not found at {CHROME}")
        return 1
    if not SOURCE.exists():
        print(f"missing source art: {SOURCE}")
        return 1

    print("rendering icons…")
    # 22% radius clips away the source tile's white corners.
    shoot(page(192, scale=1.09, radius="22%", transparent=False), "icon-192.png", 192)
    shoot(page(512, scale=1.09, radius="22%", transparent=False), "icon-512.png", 512)
    # Maskable: full bleed, art pulled inside the circular safe zone.
    shoot(page(512, scale=0.80, radius="0", transparent=False, img_radius="23%"),
          "maskable-512.png", 512)
    shoot(badge_page(96), "badge.png", 96)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
