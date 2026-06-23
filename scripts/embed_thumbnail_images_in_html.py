from __future__ import annotations

import argparse
import base64
import html as htmlmod
import io
import re
from pathlib import Path

from PIL import Image, ImageOps


def image_to_data_url(path: Path, max_width: int, quality: int) -> str:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert("RGB")

    w, h = im.size
    if w > max_width:
        new_h = int(h * max_width / w)
        im = im.resize((max_width, new_h))

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-html", required=True)
    ap.add_argument("--output-html", required=True)
    ap.add_argument("--max-width", type=int, default=900)
    ap.add_argument("--quality", type=int, default=75)
    args = ap.parse_args()

    src_html = Path(args.input_html)
    out_html = Path(args.output_html)

    text = src_html.read_text(encoding="utf-8")

    embedded = 0
    missing = 0
    skipped_remote = 0

    def repl(match: re.Match) -> str:
        nonlocal embedded, missing, skipped_remote

        before = match.group(1)
        src = htmlmod.unescape(match.group(2))
        after = match.group(3)

        if src.startswith("http://") or src.startswith("https://") or src.startswith("data:"):
            skipped_remote += 1
            return match.group(0)

        img_path = (src_html.parent / src).resolve()
        if not img_path.exists():
            missing += 1
            print("WARN missing image:", src, "->", img_path)
            return match.group(0)

        data_url = image_to_data_url(img_path, args.max_width, args.quality)
        embedded += 1
        print("embed thumb:", src)
        return f'{before}{data_url}{after}'

    new_text = re.sub(
        r'(<img\b[^>]*?\bsrc=")([^"]+)("[^>]*>)',
        repl,
        text,
        flags=re.IGNORECASE,
    )

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(new_text, encoding="utf-8")

    print()
    print("input:", src_html)
    print("output:", out_html)
    print("embedded:", embedded)
    print("missing:", missing)
    print("skipped_remote_or_data:", skipped_remote)
    print("size MB:", out_html.stat().st_size / 1024 / 1024)


if __name__ == "__main__":
    main()
