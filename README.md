# PicToBytes

Crop, grade, dither and ship images to the 7-colour e-paper picture frame.

```
python app.py          →  http://localhost:5000
```

## The workflow

1. Drop photos into `original/`.
2. **Scan folder** picks up anything new.
3. Pick an image, drag to position it, scroll to zoom, push the sliders around.
   The dithered result updates live.
4. **Render & save** writes `input/<name>.png` (the dithered 800×480 image) and
   `output/<name>.glds` (the 192000-byte payload).
5. **Deploy** copies the `.glds` into
   `C:/dev/Glados/GLaDOSHomeAssistant/lib/pic_frame_images/`, which is where
   GLaDOS picks its random image from.

**Send to frame** skips all of that and POSTs what you are currently looking at
straight to the ESP32, for checking colours on the real panel before committing.

Settings live in `library.json`, keyed by image name, so `input/` and `output/`
are always reproducible from `original/` plus that file.

## The three things this fixes

**Manual resizing.** Framing is stored as a normalised centre point plus a zoom
factor, so it survives re-rendering and never depends on the source resolution.
The crop always lands on exactly 800×480.

**Dither seams.** Paint.NET dithers in parallel tiles, so quantisation error
never crosses a tile boundary and you get visible edges. `pictobytes/dither.py`
runs one serial Floyd–Steinberg pass across the whole image, first pixel to
last. No seams.

**Too much green.** The panel over-renders green — a region with any green in it
reads as green-dominant. The **Green reduction** slider is the probability that a
pixel which *would* have come out green is pushed to its second-nearest palette
colour instead. The error still diffuses normally, so the picture holds together
and green density falls smoothly:

| Green reduction | green pixels (test image) |
|---|---|
| 0%  | 39% |
| 25% | 36% |
| 50% | 27% |
| 75% | 15% |

The colour bar under the preview shows the live per-colour breakdown, so you can
dial it by number rather than by eye.

## Settings per image

| Control | Effect |
|---|---|
| Zoom / drag | Framing, 1.0 = tightest fit that fills the frame |
| Rotate 90° | For portrait sources |
| Brightness / Contrast / Saturation | Applied after scaling, before dithering |
| Sharpen | Unsharp mask — worth a little, downscaling softens detail |
| Green reduction | See above |
| Floyd–Steinberg | Off = hard colour snapping, better for flat graphics and text |

## Legacy images

The 13 images that only exist as already-dithered PNGs in `input/` (no file in
`original/`) are listed and deployable but locked for editing — their render
*is* their source. Re-rendering one is a no-op and produces a byte-identical
`.glds`. Drop a real original into `original/` under the same name and the entry
unlocks.

## legacy_renders/

The dithered renders from the old Paint.NET workflow, before this tool existed —
reconstructed losslessly from the `.glds` files GLaDOS was already serving. Keep
them as a framing reference: `input/` and `output/` are owned by the app and are
overwritten whenever you render, but these are not touched by anything.

## Configuration

`config.json`, written on first run:

| Key | Default |
|---|---|
| `source_dir` | `original` |
| `render_dir` | `input` |
| `output_dir` | `output` |
| `deploy_dir` | `C:/dev/Glados/GLaDOSHomeAssistant/lib/pic_frame_images` |
| `device_url` | `http://192.168.178.42` |
| `default_green_reduce` | `0.0` — raise it if you always want green pulled back |
| `port` | `5000` |

The server binds `0.0.0.0`, so you can also open it from your phone on the LAN.

## Format

800×480, 4 bits per pixel, two pixels per byte, high nibble first — 192000 bytes.
Colour indices match the panel and GLaDOS' `picture_frame_util.palette_colors`:

| 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| `282828` | `E5E5E5` | `527743` | `545CCE` | `A04E4E` | `FFFF47` | `A8663A` |
| Black | White | Green | Blue | Red | Yellow | Orange |

`byte_me.py` still works and still produces identical output; it is just no
longer part of the loop.

## Layout

```
app.py                 Flask server and HTTP API
pictobytes/palette.py  palette, .glds packing and unpacking
pictobytes/dither.py   serial Floyd–Steinberg with green suppression
pictobytes/pipeline.py crop → scale → grade → dither
pictobytes/library.py  metadata, folder scanning, render, deploy
pictobytes/config.py   config.json
static/                the UI
```
