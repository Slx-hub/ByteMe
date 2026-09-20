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
It waits for the panel to be ready first — see below.

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

**Too much green.** Two things go wrong at once. The panel over-renders green,
so any area containing green pixels reads as green-dominant — and the nominal
green `#527743` is a dark desaturated olive, so the matcher also reaches for it
to represent ordinary midtones and shadows.

**Green reduction** fixes both by telling the matcher green *looks* more
saturated than its nominal value, sliding it towards fully saturated. Green then
stops winning for neutral midtones, and where the image really is green each
pixel counts for more, so fewer are needed. The error diffuses against the same
adjusted colour, so neighbours compensate coherently:

| Green reduction | green pixels (test image) |
|---|---|
| 0%   | 24% |
| 40%  | 13% |
| 70%  | 9%  |
| 100% | 7%  |

What does *not* work — and was the first thing tried — is substituting
individual green pixels for their runner-up. The green-biased error left behind
diffuses outward, turns the neighbours green, and sprays a low-density green
speckle across the entire image. The pixel count says it improved; looking at it
says the opposite.

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

## Images with no original

An image that exists only as an already-dithered PNG in `input/`, with no file
in `original/`, is listed and deployable but locked for editing — its render
*is* its source, so there is nothing to re-crop or re-grade from. Drop a real
photo into `original/` under the same name and the entry unlocks on the next
scan.

Matching is purely by filename stem. A restored photo saved under a different
name creates a *new* entry instead of unlocking the old one, which is usually
what you want: re-frame the new one and delete the stale render.

## Talking to the frame

The panel takes ~30s to refresh and then rests for 2 minutes, and the firmware
refuses every refresh path until that has elapsed. So **Send to frame** and
**Clear display** check `GET /status` first and only upload when the panel says
`ready`. Posting blindly into the rest window gets a `503` whose body is easy to
misread as success.

The top bar shows the panel's state with a live countdown, and both buttons are
disabled while it is not ready, so you can see the wait rather than fail into it.

| Panel says | You get |
|---|---|
| `ready` | The upload proceeds |
| `busy` | *The panel is mid-refresh. That takes about 30 seconds.* |
| `resting` | *The panel is resting after its last refresh. Ready again in 1m 21s.* |
| `503` on upload | Lost a race against the rest period — retry shortly |
| `422` on upload | Short upload; **the panel is left untouched** |
| no answer | *Cannot reach the frame at … / did not answer in time* |

Firmware endpoints (`Arduino-Collection/smart_picture_frame/wifi`):

| Endpoint | Method | Purpose |
|---|---|---|
| `/status` | GET | Panel state plus diagnostics |
| `/clear?color=N` | GET | Clear to one palette colour |
| `/image` | POST | One full 192000 byte frame |

## legacy_renders/

Dithered renders whose source photo no longer exists anywhere — reconstructed
losslessly from the `.glds` files GLaDOS was already serving. Currently
`christmas_18` and `molly_tired`. They are the only surviving copy of those two
pictures, so nothing in the app writes to this folder.

To put one back on the frame, copy its `.glds` from the GLaDOS folder, or
re-dither the PNG. A dithered image stores tone as local dot density, so a
gaussian blur of about 1.0 recovers a usable continuous-tone version that can be
re-graded and re-dithered — capped at 800×480, with no extra detail to recover.

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
pictobytes/device.py   the frame: readiness checks and upload
pictobytes/config.py   config.json
static/                the UI
```
