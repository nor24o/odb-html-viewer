# ODB++ HTML5 Standalone Board Explorer

A single-file, zero-dependency Python script (`odb_viewer_compiler.py`) that parses any valid ODB++ archive (`.zip`, `.tgz`, `.tar.gz`, or extracted directory tree) and compiles a fully standalone, offline-ready HTML5 Canvas application for high-performance, interactive PCB inspection.

---

## Highlights

- **Zero Non-Standard Dependencies:** Runs entirely on Python's built-in standard library (`zipfile`, `tarfile`, `gzip`, `base64`, `json`, `math`, `re`, `argparse`).
- **Direct Canvas Click Selection:** Click directly on any component courtyard or test point on the PCB to instantly select it, display its detail inspector card, highlight connected nets, and sync with the sidebar list. Hover cursor automatically turns into a pointer with component tooltips.
- **Top Overlay Screen-Space Text Engine:** Renders RefDes labels for all components (passives, ICs, test points) on top of the physical layer stack in screen space. Text is guaranteed never upside-down or mirrored backwards, oriented along the package body axis, and outlined with a crisp dark stroke for 100% contrast without disruptive opaque black background boxes.
- **Performance Optimized (Solid 60 FPS):** Fast spatial chunk tile binning, optimized vector trace rasterization, LOD text culling, and streamlined canvas state changes deliver lag-free panning and zooming even on dense boards with thousands of components.
- **Interactive Multi-Tab Explorer:**
  - **Layers:** Toggle visibility and customize colors for copper, solder mask, silkscreen, and drill layers.
  - **Components List:** Searchable and filterable list of all components with side indicators, packages, and pin counts. Click to focus, zoom, and inspect.
  - **Test Points List:** Dedicated list of all test points (`TP...`) showing coordinates, sides, and connected traces. Click to zoom in close with centered RefDes.
  - **Traces (Nets) List:** Full netlist explorer with pin counts, node chips, and connected test points. Click to highlight connected pins across the board and render dynamic flight-lines.
- **Compact Standalone Packaging:** Generates an isolated `.html` file with all CSS, JavaScript, and delta-encoded board geometry bundled inline. Output file size is under **2.5 MB** even for dense multi-thousand component boards ($1.86\text{ MB}$ for $3,456$ components, $1,224$ test points, $1,284$ nets, and $1\text{M}+$ vertices).
- **Web Worker Background Threading:** Uses an inline Web Worker (via Blob URL) with native `DecompressionStream('gzip')` to decode and decompress board data off the main thread, with an automatic synchronous fallback for local `file:///` protocols.
- **Hardware GPU Coordinate Transform:** Maintains physical millimeter coordinates in memory and transforms the Canvas once per frame via `ctx.setTransform(scale * (mirrorX ? -1 : 1), 0, 0, -scale, panX, panY)`.
- **Frustum & Side Culling:** Pre-bins vector paths into spatial bounding-box chunks; culls chunks, components, pads, and text outside the visible viewport, running at a smooth 60 FPS.

---

## Visual Styling (EDA Palette)

| Layer / Feature | Color | Styling Rules |
| :--- | :--- | :--- |
| **Substrate Body** | `#0a120c` | Deep dark green/black laminate core with native circular arc cutouts and `#e5c07b` outline edge. |
| **Top Copper** | `#29663c` | Lighter emerald green traces and pours; seamless filleted joints and 1-micron delta-encoded polygons. |
| **Bottom Copper** | `#1e3a5f` | Slate navy traces and pours; seamless filleted joints and 1-micron delta-encoded polygons. |
| **Solder Pads & Vias** | `#c49c3e` | Warm Gold / ENIG SMT pads, circular via rings, and exposed test lands. |
| **Component Courtyards** | `#00e5ff` | Electric cyan SMD boundary outlines; clickable for instant selection. |
| **SMT Pads & Pins** | `#ff2222` / `#00e5ff` | **Pin 1** distinctly filled and outlined in **Bright Red** (`#ff2222`); Pin 2+ outlined in Cyan (`#00e5ff`). |
| **Test Points (`TP...`)** | `#e0f7fa` / `#00e5ff` | Strictly circular with light cyan fill and electric cyan border; RefDes centered inside in `#00e5ff`; clickable for instant selection. |
| **Silkscreen Text** | `#d9a738` | Amber gold physical silkscreen overlay printed on substrate beneath component packages. |
| **Component RefDes Overlay** | `#f0f4f8` / `#ffb347` / `#ff3333` | Crisp RefDes centered inside every component courtyard along primary axis, rendered OVER pads and copper with high-contrast outline stroke (no disruptive opaque boxes). |

---

## Interactive Tooling

- **Direct Canvas Selection:** Click any component courtyard or test point on the PCB to select it, open its detail inspector card, and highlight connected net nodes.
- **Top View:** Switches camera to normal orientation, enables Top Copper, Top Solder Mask, Top Silkscreen, and Top Components, while hiding bottom layers.
- **Bottom View:** Flips horizontal axis (`Mirror X`), enables Bottom Copper, Bottom Solder Mask, Bottom Silkscreen, and Bottom Components, while hiding top layers.
- **Fit (F):** Centers and fits board boundaries into viewport with padding.
- **Mirror X (M):** Toggles horizontal mirror.
- **Instant Search & Focus:** Case-insensitive RefDes search with prefix fallback. On match, smoothly centers camera, switches top/bottom view automatically if needed, and highlights with a pulsing red marker ring.
- **Shift+Drag Caliper Tool:** Holding `Shift` and dragging across the board draws a calibrated caliper displaying real-time millimeter $\Delta X$, $\Delta Y$, and direct point-to-point Euclidean distance in the HUD and inline over the line.
- **Level of Detail (LOD):**
  - Silkscreen text suppressed when font on-screen height $< 4.0\text{ px}$.
  - Component in-courtyard RefDes rendered when on-screen font $\ge 6.5\text{ px}$ or when selected.

---

## Installation & Requirements

- **Python 3.8+** (Zero pip packages required).
- Any modern web browser (Chrome, Edge, Firefox, Safari).

---

## Usage

### Basic Compilation
```bash
python odb_viewer_compiler.py path/to/board.zip
```
This automatically parses the primary step (`board_0_0` or `pcb`) and outputs `pcb_viewer.html`.

### Specify Output File & Step
```bash
python odb_viewer_compiler.py path/to/board.tgz -o my_board_viewer.html -s board_0_0
```

### CLI Arguments
```text
positional arguments:
  odb_input            Path to ODB++ .zip, .tgz, .tar.gz, or extracted directory

options:
  -h, --help           Show help message and exit
  -o, --output OUTPUT  Output HTML file path (default: pcb_viewer.html)
  -s, --step STEP      Target step name (defaults to primary step from matrix/matrix)
  --include-inner      Include inner copper signal layers
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
