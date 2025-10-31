#!/usr/bin/env python3
"""Utility helpers for maintaining the LVGL UI assets.

This script provides several commands.  Unless overridden with ``--ui-dir`` it
operates on the ``examples/openai-realtime/src/ui`` directory next to this
helper script:

* ``info`` – list the dimensions and color format metadata for each LVGL
  image descriptor in ``src/ui``.  This is handy when preparing replacement
  artwork.
* ``replace`` – convert a PNG or SVG file into a C source file that matches
  the LVGL image descriptor structure used by the firmware.  The generated
  code is intentionally compact to avoid the very large diffs produced by the
  original asset generator.
* ``export`` – decode the LVGL descriptor back into a regular image (PNG or
  JPEG) so the existing artwork can be inspected or tweaked in a graphics
  editor.

Example usage::

    # Show the current asset dimensions
    ./tools/ui_asset_tool.py info

    # Replace listening_A with a new PNG.  The image will be resized to match
    # the original 412x412 resolution.
    ./tools/ui_asset_tool.py replace listening_A artwork/listening_A.png --resize

    # Replace speaking_A using an SVG source (requires cairosvg)
    ./tools/ui_asset_tool.py replace speaking_A artwork/speaking.svg --resize

The script keeps the existing LVGL include guards and attribute macros so the
generated files continue to compile without any manual tweaks.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

try:
    from PIL import Image  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency error message
    # Pillow is only required when replacing assets.  The import is deferred so
    # that ``info`` can run without optional dependencies.
    Image = None  # type: ignore

try:  # Optional dependency for SVG sources
    import cairosvg  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    cairosvg = None


RE_DESCRIPTOR = re.compile(
    r"const\s+lv_img_dsc_t\s+(?P<name>[a-zA-Z0-9_]+)\s*=\s*\{(?P<body>.*?)\};",
    re.S,
)

RE_MAP_ARRAY = re.compile(
    r"const\s+[^=]+\s+(?P<name>[a-zA-Z0-9_]+)_map\s*\[\s*\]\s*=\s*\{(?P<data>.*?)\};",
    re.S,
)

RE_FIELD = re.compile(r"\.header\.(?P<field>[a-z_]+)\s*=\s*(?P<value>[^,]+)")


@dataclass
class AssetInfo:
    name: str
    width: int
    height: int
    color_format: str
    data_size: str
    source_file: Path


def _default_ui_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "ui"


def iter_asset_files(ui_dir: Path) -> Iterable[Path]:
    for path in sorted(ui_dir.glob("*.c")):
        if path.name in {"ui.c"}:
            continue
        yield path


def parse_asset_metadata(path: Path) -> AssetInfo:
    text = path.read_text()
    match = RE_DESCRIPTOR.search(text)
    if not match:
        raise ValueError(f"No lv_img_dsc_t found in {path}")

    fields: Dict[str, str] = {}
    for field_match in RE_FIELD.finditer(match.group("body")):
        fields[field_match.group("field")] = field_match.group("value").strip()

    try:
        width = int(fields["w"], 0)
        height = int(fields["h"], 0)
    except KeyError as exc:
        raise ValueError(f"Missing dimension metadata in {path}") from exc

    return AssetInfo(
        name=match.group("name"),
        width=width,
        height=height,
        color_format=fields.get("cf", "unknown"),
        data_size=fields.get("data_size", "unknown"),
        source_file=path,
    )


def parse_asset_pixels(
    path: Path, expected_name: str | None = None, expected_length: int | None = None
) -> List[int]:
    text = path.read_text()
    map_match = RE_MAP_ARRAY.search(text)
    if not map_match:
        raise ValueError(f"No pixel data array found in {path}")

    array_name = map_match.group("name")
    if expected_name and array_name != expected_name:
        raise ValueError(
            f"Unexpected pixel array name '{array_name}' in {path} (expected {expected_name})"
        )

    raw_values = map_match.group("data")
    segments = re.split(r"^\s*#.*$", raw_values, flags=re.M)
    parse_errors = []
    for segment in segments:
        cleaned = re.sub(r"/\*.*?\*/", " ", segment, flags=re.S)
        tokens = [token.strip() for token in cleaned.replace("\n", " ").split(",") if token.strip()]
        if not tokens:
            continue

        values: List[int] = []
        try:
            for token in tokens:
                values.append(int(token, 0))
        except ValueError as exc:
            parse_errors.append((token, exc))
            continue

        if expected_length is None or len(values) == expected_length:
            return values

    if parse_errors:
        token, exc = parse_errors[0]
        raise ValueError(f"Unrecognised byte literal '{token}' in {path}") from exc

    raise ValueError(f"No pixel data array with the expected length found in {path}")


def command_info(ui_dir: Path) -> None:
    assets = [parse_asset_metadata(path) for path in iter_asset_files(ui_dir)]
    if not assets:
        raise SystemExit(f"No image assets found in {ui_dir}")

    name_width = max(len(asset.name) for asset in assets)
    size_header = "Size"
    print(f"{'Asset':<{name_width}}  {size_header:<9}  Color format        File")
    print("-" * (name_width + 2 + len(size_header) + 2 + 18 + 2 + 4))
    for asset in assets:
        size = f"{asset.width}x{asset.height}"
        print(
            f"{asset.name:<{name_width}}  {size:<9}  {asset.color_format:<18}  {asset.source_file.relative_to(ui_dir.parent.parent)}"
        )


def _require_pillow() -> "Image":
    global Image  # type: ignore
    if Image is None:  # type: ignore
        try:
            from PIL import Image as _Image  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency error
            raise SystemExit(
                "Pillow is required. Install it with 'pip install pillow'"
            ) from exc
        Image = _Image  # type: ignore
    return Image  # type: ignore


def load_image(path: Path):
    ImageCls = _require_pillow()
    suffix = path.suffix.lower()
    if suffix == ".svg":
        if cairosvg is None:
            raise SystemExit(
                "cairosvg is required for SVG sources. Install it with 'pip install cairosvg'."
            )
        png_bytes = cairosvg.svg2png(url=str(path))
        return ImageCls.open(io.BytesIO(png_bytes)).convert("RGBA")

    if suffix not in {".png", ".bmp", ".jpg", ".jpeg"}:
        raise SystemExit(f"Unsupported image format: {path.suffix}")

    return ImageCls.open(path).convert("RGBA")


def format_c_array(data: Sequence[int]) -> str:
    lines: List[str] = []
    for index in range(0, len(data), 12):
        chunk = data[index : index + 12]
        values = ", ".join(f"0x{value:02x}" for value in chunk)
        lines.append(f"    {values},")
    return "\n".join(lines)


FILE_TEMPLATE = """#ifdef __has_include
#if __has_include("lvgl.h")
#ifndef LV_LVGL_H_INCLUDE_SIMPLE
#define LV_LVGL_H_INCLUDE_SIMPLE
#endif
#endif
#endif

#if defined(LV_LVGL_H_INCLUDE_SIMPLE)
#include "lvgl.h"
#else
#include "lvgl/lvgl.h"
#endif

#ifndef LV_ATTRIBUTE_MEM_ALIGN
#define LV_ATTRIBUTE_MEM_ALIGN
#endif

#ifndef LV_ATTRIBUTE_LARGE_CONST
#define LV_ATTRIBUTE_LARGE_CONST
#endif

#ifndef LV_ATTRIBUTE_IMG_{macro}
#define LV_ATTRIBUTE_IMG_{macro}
#endif

const LV_ATTRIBUTE_MEM_ALIGN LV_ATTRIBUTE_LARGE_CONST LV_ATTRIBUTE_IMG_{macro} uint8_t {name}_map[] = {{
{data}
}};

const lv_img_dsc_t {name} = {{
    .header.cf = LV_IMG_CF_TRUE_COLOR_ALPHA,
    .header.always_zero = 0,
    .header.w = {width},
    .header.h = {height},
    .data_size = sizeof({name}_map),
    .data = {name}_map,
}};
"""


def command_replace(ui_dir: Path, name: str, image_path: Path, allow_resize: bool) -> None:
    target_file = ui_dir / f"{name}.c"
    if not target_file.exists():
        available = ", ".join(path.stem for path in iter_asset_files(ui_dir))
        raise SystemExit(
            f"Unknown asset '{name}'. Available assets: {available or 'none found'}"
        )

    metadata = parse_asset_metadata(target_file)
    image = load_image(image_path)

    target_size = (metadata.width, metadata.height)
    if image.size != target_size:
        if not allow_resize:
            raise SystemExit(
                f"Input image is {image.size[0]}x{image.size[1]}, expected {target_size[0]}x{target_size[1]}.\n"
                "Use --resize to scale the source automatically."
            )
        image = image.resize(target_size, Image.LANCZOS)

    # LVGL stores TRUE_COLOR_ALPHA pixels in BGRA order on little-endian targets.
    pixel_bytes: List[int] = []
    for pixel in image.getdata():
        r, g, b, a = pixel
        pixel_bytes.extend([b, g, r, a])

    macro_name = name.upper()
    array_literal = format_c_array(pixel_bytes)
    file_content = FILE_TEMPLATE.format(
        macro=macro_name,
        name=name,
        data=array_literal,
        width=target_size[0],
        height=target_size[1],
    )

    target_file.write_text(file_content + "\n")
    print(f"Updated {target_file.relative_to(ui_dir.parent.parent)}")


def command_export(ui_dir: Path, name: str, output_path: Path | None, image_format: str | None) -> None:
    target_file = ui_dir / f"{name}.c"
    if not target_file.exists():
        available = ", ".join(path.stem for path in iter_asset_files(ui_dir))
        raise SystemExit(
            f"Unknown asset '{name}'. Available assets: {available or 'none found'}"
        )

    metadata = parse_asset_metadata(target_file)
    expected_len = metadata.width * metadata.height * 4
    pixels = parse_asset_pixels(
        target_file, expected_name=name, expected_length=expected_len
    )

    ImageCls = _require_pillow()
    image = ImageCls.new("RGBA", (metadata.width, metadata.height))
    rgba_pixels = []
    for index in range(0, len(pixels), 4):
        b, g, r, a = pixels[index : index + 4]
        rgba_pixels.append((r, g, b, a))
    image.putdata(rgba_pixels)

    if output_path is None:
        extension = (image_format or "png").lower()
        output_path = Path(f"{name}.{extension}")
    else:
        extension = output_path.suffix.lstrip(".").lower()
        if not extension:
            extension = image_format or "png"
            output_path = output_path.with_suffix(f".{extension}")

    save_format = (image_format or extension or "png").lower()
    if save_format in {"jpg", "jpeg"}:
        image_to_save = image.convert("RGB")
    else:
        if save_format not in {"png", "jpg", "jpeg"}:
            raise SystemExit(
                "Unsupported export format. Choose from: png, jpg, jpeg"
            )
        image_to_save = image

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pillow_format = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG"}[save_format]
    image_to_save.save(output_path, format=pillow_format)
    print(
        f"Wrote {output_path} ({metadata.width}x{metadata.height} {save_format.upper()} exported from {target_file.relative_to(ui_dir.parent.parent)})"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ui-dir",
        default=str(_default_ui_dir()),
        help="Path to the directory that contains the LVGL asset .c files (default: %(default)s)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    info_parser = subparsers.add_parser("info", help="List metadata about the existing assets")
    info_parser.set_defaults(func=lambda args: command_info(Path(args.ui_dir)))

    replace_parser = subparsers.add_parser(
        "replace",
        help="Replace an asset with a new PNG or SVG source",
    )
    replace_parser.add_argument("name", help="Asset name (e.g. listening_A)")
    replace_parser.add_argument("image", type=Path, help="Path to the replacement image")
    replace_parser.add_argument(
        "--resize",
        action="store_true",
        help="Automatically resize the source image to the expected asset dimensions",
    )
    replace_parser.set_defaults(
        func=lambda args: command_replace(
            Path(args.ui_dir), args.name, args.image, allow_resize=args.resize
        )
    )

    export_parser = subparsers.add_parser(
        "export",
        help="Convert an existing asset descriptor back into an image file",
    )
    export_parser.add_argument("name", help="Asset name (e.g. listening_A)")
    export_parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="Optional output file. Defaults to NAME.<format> in the working directory.",
    )
    export_parser.add_argument(
        "--format",
        choices=["png", "jpg", "jpeg"],
        help="Image format to export (defaults to png, or inferred from the output extension)",
    )
    export_parser.set_defaults(
        func=lambda args: command_export(
            Path(args.ui_dir), args.name, args.output, args.format
        )
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
