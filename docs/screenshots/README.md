# Screenshot captures

These PNGs show the application's real widgets with synthetic sample data. They contain no personal library, authentication details, or remote video URLs. Album artwork is a code-drawn fixture; video mode uses a generated moving test frame. The OBS dock uses the standard Qt Fusion style with a neutral dark palette. The terminal screenshot uses Textual's SVG export, converted to PNG.

The source captures run independently of the user's player and OBS scene collection. PNG metadata is stripped before publication.

## Terminal interface

Requires the project's Python environment and `rsvg-convert` (librsvg).

```sh
.venv/bin/python scripts/capture_terminal.py
```

## Album-art widget

The existing renderer test captures the same Qt card implementation used in the native OBS plugin.

```sh
cmake -S obs -B build/obs
cmake --build build/obs --parallel
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=generic QT_STYLE_OVERRIDE=Fusion \
  build/obs/ytmusic-overlay-test "$PWD/build/readme-artwork"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i 'color=c=0x20232b:s=360x520' -i build/readme-artwork.png \
  -filter_complex '[0:v][1:v]overlay=format=auto' -frames:v 1 docs/screenshots/widget-artwork.png
```

## Video widget and OBS controls

The native smoke harness loads the actual plugin into an isolated libobs process. It supplies sample state and generated JPEG video frames over a private test socket. Its optional fifth argument saves a capture of the actual Qt control dock.

On a Wayland desktop, resolve the real compositor's socket before changing the test runtime directory:

```sh
capture_runtime=$(mktemp -d)
capture_display="$WAYLAND_DISPLAY"
case "$capture_display" in
  /*) ;;
  *) capture_display="$XDG_RUNTIME_DIR/$capture_display" ;;
esac
XDG_RUNTIME_DIR="$capture_runtime" WAYLAND_DISPLAY="$capture_display" \
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=generic QT_STYLE_OVERRIDE=Fusion \
  build/obs/ytmusic-obs-smoke "$PWD/build/obs/ytmusic-obs.so" \
  "$PWD/build/readme-video.png" video "$PWD/docs/screenshots/obs-controls.png"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i 'color=c=0x20232b:s=360x398' -i build/readme-video.png \
  -filter_complex '[0:v][1:v]overlay=format=auto' -frames:v 1 docs/screenshots/widget-video.png
```

Artwork and video widget captures are composited over a dark background so their transparent shadows and glow render consistently in the README. The overlay itself stays transparent in OBS.

Before committing recaptured assets, inspect all four images and strip metadata with ImageMagick:

```sh
magick docs/screenshots/terminal-interface.png -strip docs/screenshots/terminal-interface.png
magick docs/screenshots/widget-artwork.png -strip docs/screenshots/widget-artwork.png
magick docs/screenshots/widget-video.png -strip docs/screenshots/widget-video.png
magick docs/screenshots/obs-controls.png -strip docs/screenshots/obs-controls.png
```
