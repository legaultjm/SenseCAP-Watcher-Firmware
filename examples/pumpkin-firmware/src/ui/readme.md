## Updating the animated face assets

The listening/speaking face animations are compiled into the firmware as LVGL
image descriptors under `src/ui`.  A helper script is available to inspect the
current assets, regenerate them from PNG/SVG files, and export the existing
artwork without producing huge diffs.

```sh
# List the size and color format of every asset (requires no extra dependencies)
./tools/ui_asset_tool.py info

# Replace an asset.  Pillow is required (`pip install pillow`).
# Add `--resize` to scale the source image to the expected size automatically.
./tools/ui_asset_tool.py replace listening_A path/to/pumpkin.png --resize

# SVG sources can be used when cairosvg is installed (`pip install cairosvg`).
./tools/ui_asset_tool.py replace speaking_A path/to/pumpkin.svg --resize

# Export an existing asset to a regular image file (png or jpg).
./tools/ui_asset_tool.py export listening_A exported/listening_A.png
```

The existing assets are 412×412 px for the listening frames and 413×412 px for
the speaking frames.  The `info` command prints the exact dimensions in case you
need to double-check before exporting new artwork.
