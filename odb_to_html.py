import argparse
import base64
import gzip
import json
import math
import os
import re
import sys
import tarfile
import zipfile


def read_file_from_source(source, target_suffix, preferred_step=None):
    """Finds and reads a file matching target suffix from zip, tar, or directory."""
    norm_target = target_suffix.replace("\\", "/").lower()
    matches = []

    if os.path.isdir(source):
        for root, _, files in os.walk(source):
            for f in files:
                full_path = os.path.join(root, f)
                rel = os.path.relpath(full_path, source).replace("\\", "/").lower()
                if rel.endswith(norm_target):
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as fp:
                        matches.append((fp.read(), rel))

    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source, "r") as z:
            for name in z.namelist():
                rel = name.replace("\\", "/").lower()
                if rel.endswith(norm_target):
                    with z.open(name) as fp:
                        matches.append((fp.read().decode("utf-8", errors="ignore"), rel))

    elif tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as t:
            for member in t.getmembers():
                rel = member.name.replace("\\", "/").lower()
                if rel.endswith(norm_target):
                    f = t.extractfile(member)
                    if f:
                        matches.append((f.read().decode("utf-8", errors="ignore"), rel))

    if not matches:
        return None, None

    if preferred_step:
        step_str = f"steps/{preferred_step.lower()}/"
        for content, path in matches:
            if step_str in path:
                return content, path

    return matches[0]


def parse_matrix_file(matrix_content):
    """Parses matrix/matrix to identify layer sides and functions."""
    layer_meta = {}
    if not matrix_content:
        return layer_meta

    current_layer = {}
    for line in matrix_content.splitlines():
        line = line.strip()
        if line.startswith("LAYER {") or line.startswith("LAYER{"):
            current_layer = {}
        elif line.startswith("}"):
            if "NAME" in current_layer:
                layer_meta[current_layer["NAME"].lower()] = current_layer
            current_layer = {}
        elif "=" in line:
            parts = line.split("=", 1)
            current_layer[parts[0].strip().upper()] = parts[1].strip().upper()
    return layer_meta


def get_unit_scale_to_mm(text, default_unit="MM"):
    match = re.search(r"UNITS\s*=\s*(\w+)", text, re.IGNORECASE)
    unit = match.group(1).upper() if match else default_unit.upper()
    if "INCH" in unit:
        return 25.4, "INCH"
    elif "MIL" in unit:
        return 0.0254, "MIL"
    elif "MICRON" in unit:
        return 0.001, "MICRON"
    return 1.0, "MM"


def parse_symbol_dim(val_str, unit_name):
    val = float(val_str)
    if "INCH" in unit_name or "MIL" in unit_name:
        return val * 25.4 if val < 0.1 else val * 0.0254
    else:
        return val * 0.001 if val >= 5.0 else val


def parse_symbols(content, unit_name):
    symbols = {}
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("$"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        sym_id = parts[0][1:]
        sym_def = parts[1].lower()

        m_r = re.match(r"^r(\d+(?:\.\d+)?)$", sym_def)
        if m_r:
            symbols[sym_id] = {"type": "circle", "d": parse_symbol_dim(m_r.group(1), unit_name)}
            continue

        m_s = re.match(r"^s(\d+(?:\.\d+)?)$", sym_def)
        if m_s:
            s = parse_symbol_dim(m_s.group(1), unit_name)
            symbols[sym_id] = {"type": "rect", "w": s, "h": s}
            continue

        m_rect = re.match(r"^rect(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$", sym_def)
        if m_rect:
            symbols[sym_id] = {
                "type": "rect",
                "w": parse_symbol_dim(m_rect.group(1), unit_name),
                "h": parse_symbol_dim(m_rect.group(2), unit_name),
            }
            continue

        m_oval = re.match(r"^oval(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$", sym_def)
        if m_oval:
            symbols[sym_id] = {
                "type": "oval",
                "w": parse_symbol_dim(m_oval.group(1), unit_name),
                "h": parse_symbol_dim(m_oval.group(2), unit_name),
            }
            continue

        symbols[sym_id] = {"type": "circle", "d": 0.2}

    return symbols


def parse_layer_features(features_text, allow_texts=False):
    """Extracts lines, native arcs, pads, surface fills, and verified silkscreen text."""
    coord_scale, unit_name = get_unit_scale_to_mm(features_text)
    symbols = parse_symbols(features_text, unit_name)

    lines, arcs, circles, rects, surfaces, texts = [], [], [], [], [], []
    current_surface = []
    current_contour = []

    for raw_line in features_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        toks = line.split()
        cmd = toks[0].upper()

        if cmd == "L" and len(toks) >= 6:
            xs = round(float(toks[1]) * coord_scale, 3)
            ys = round(float(toks[2]) * coord_scale, 3)
            xe = round(float(toks[3]) * coord_scale, 3)
            ye = round(float(toks[4]) * coord_scale, 3)
            sym = symbols.get(toks[5], {"type": "circle", "d": 0.2})
            width = round(sym.get("d", sym.get("w", 0.2)), 3)
            lines.append((xs, ys, xe, ye, width))

        elif cmd == "A" and len(toks) >= 8:
            xs = float(toks[1]) * coord_scale
            ys = float(toks[2]) * coord_scale
            xe = float(toks[3]) * coord_scale
            ye = float(toks[4]) * coord_scale
            xc = float(toks[5]) * coord_scale
            yc = float(toks[6]) * coord_scale
            sym = symbols.get(toks[7], {"type": "circle", "d": 0.2})
            width = round(sym.get("d", sym.get("w", 0.2)), 3)
            cw = 1 if (len(toks) > 10 and toks[10].upper() in ("Y", "CW", "TRUE")) else 0

            r = round((math.hypot(xs - xc, ys - yc) + math.hypot(xe - xc, ye - yc)) / 2.0, 3)
            a_start = round(math.atan2(ys - yc, xs - xc), 4)
            a_end = round(math.atan2(ye - yc, xe - xc), 4)
            arcs.append((round(xc, 3), round(yc, 3), r, a_start, a_end, cw, width))

        elif cmd == "P" and len(toks) >= 4:
            x = round(float(toks[1]) * coord_scale, 3)
            y = round(float(toks[2]) * coord_scale, 3)
            sym = symbols.get(toks[3], {"type": "circle", "d": 0.5})
            angle = float(toks[6]) if len(toks) > 6 and toks[6].replace(".", "", 1).lstrip("-").isdigit() else 0.0

            if sym["type"] == "circle":
                circles.append((x, y, round(sym["d"] / 2.0, 3)))
            else:
                rects.append((x, y, round(sym["w"], 3), round(sym["h"], 3), round(angle, 1)))

        elif cmd == "T" and len(toks) >= 8 and allow_texts:
            try:
                x = round(float(toks[1]) * coord_scale, 3)
                y = round(float(toks[2]) * coord_scale, 3)
                rot = round(float(toks[5]), 1) if len(toks) > 5 and toks[5].replace(".", "", 1).lstrip("-").isdigit() else 0.0
                mir = 1 if (len(toks) > 6 and toks[6].upper() == "Y") else 0
                raw_h = float(toks[8])
                h = round(raw_h * 0.0254, 3) if raw_h > 10.0 else round(raw_h * coord_scale, 3)

                m_txt = re.search(r"['\"](.*?)['\"]", line)
                text_str = m_txt.group(1) if m_txt else toks[-1].strip("';\"")
                if text_str and 0.4 <= h <= 5.0:
                    texts.append([x, y, text_str, h, rot, mir])
            except Exception:
                pass

        elif cmd == "OB" and len(toks) >= 3:
            current_contour = [round(float(toks[1]) * coord_scale, 3), round(float(toks[2]) * coord_scale, 3)]
        elif cmd == "OS" and len(toks) >= 3 and current_contour:
            current_contour.extend([round(float(toks[1]) * coord_scale, 3), round(float(toks[2]) * coord_scale, 3)])
        elif cmd == "OC" and len(toks) >= 6 and current_contour:
            xe = float(toks[1]) * coord_scale
            ye = float(toks[2]) * coord_scale
            xc = float(toks[3]) * coord_scale
            yc = float(toks[4]) * coord_scale
            cw = toks[5].upper() in ("Y", "CW", "TRUE")
            xs, ys = current_contour[-2], current_contour[-1]

            r = (math.hypot(xs - xc, ys - yc) + math.hypot(xe - xc, ye - yc)) / 2.0
            a_start = math.atan2(ys - yc, xs - xc)
            a_end = math.atan2(ye - yc, xe - xc)
            if cw and a_end >= a_start:
                a_end -= 2.0 * math.pi
            elif not cw and a_end <= a_start:
                a_end += 2.0 * math.pi

            for i in range(1, 7):
                ang = a_start + (i / 6.0) * (a_end - a_start)
                current_contour.extend([round(xc + r * math.cos(ang), 3), round(yc + r * math.sin(ang), 3)])

        elif cmd == "OE" and current_contour:
            if len(current_contour) >= 6:
                current_surface.append(current_contour)
            current_contour = []
        elif cmd == "SE":
            if current_surface:
                surfaces.extend(current_surface)
                current_surface = []

    return {
        "lines": lines,
        "arcs": arcs,
        "circles": circles,
        "rects": rects,
        "surfaces": surfaces,
        "texts": texts,
    }


def chunk_layer_geometry(l_data, chunk_size=300):
    all_elements = []
    for l in l_data["lines"]:
        min_x = min(l[0], l[2])
        max_x = max(l[0], l[2])
        min_y = min(l[1], l[3])
        max_y = max(l[1], l[3])
        all_elements.append(("L", l, min_x, min_y, max_x, max_y))

    for a in l_data["arcs"]:
        xc, yc, r = a[0], a[1], a[2]
        all_elements.append(("A", a, xc - r, yc - r, xc + r, yc + r))

    chunks = []
    for i in range(0, len(all_elements), chunk_size):
        sub = all_elements[i : i + chunk_size]
        min_x = min(item[2] for item in sub)
        min_y = min(item[3] for item in sub)
        max_x = max(item[4] for item in sub)
        max_y = max(item[5] for item in sub)

        w_lines = {}
        w_arcs = {}
        for item in sub:
            kind, data = item[0], item[1]
            if kind == "L":
                w = data[4]
                if w not in w_lines:
                    w_lines[w] = []
                w_lines[w].extend(data[:4])
            else:
                w = data[6]
                if w not in w_arcs:
                    w_arcs[w] = []
                w_arcs[w].extend(data[:6])

        chunks.append({
            "b": [round(min_x, 3), round(min_y, 3), round(max_x, 3), round(max_y, 3)],
            "lines": [{"w": w, "pts": pts} for w, pts in w_lines.items()],
            "arcs": [{"w": w, "arcs": a} for w, a in w_arcs.items()],
        })

    flat_circles = []
    for c in l_data["circles"]:
        flat_circles.extend(c)

    flat_rects = []
    for r in l_data["rects"]:
        flat_rects.extend(r)

    return {
        "chunks": chunks,
        "circles": flat_circles,
        "rects": flat_rects,
        "surfaces": l_data["surfaces"],
        "texts": l_data["texts"],
    }


def parse_eda_packages(eda_content):
    """Extracts package boundary dimensions and pins from eda/data."""
    packages = {}
    pkg_list = []
    if not eda_content:
        return packages, pkg_list

    scale, _ = get_unit_scale_to_mm(eda_content)
    current_pkg = None

    for line in eda_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        cmd = toks[0].upper()

        if cmd == "PKG" and len(toks) >= 2:
            name = toks[1]
            w, h, xc, yc = 0.0, 0.0, 0.0, 0.0
            nums = []
            for t in toks[2:]:
                try:
                    nums.append(float(t))
                except ValueError:
                    continue

            if len(nums) == 4:
                xmin, ymin, xmax, ymax = [n * scale for n in nums]
                w = round(abs(xmax - xmin), 3)
                h = round(abs(ymax - ymin), 3)
                xc = round((xmin + xmax) / 2.0, 3)
                yc = round((ymin + ymax) / 2.0, 3)
            elif len(nums) >= 5:
                cand1 = [n * scale for n in nums[:4]]
                cand2 = [n * scale for n in nums[1:5]]
                if cand1[2] > cand1[0] and cand1[3] > cand1[1]:
                    xmin, ymin, xmax, ymax = cand1
                else:
                    xmin, ymin, xmax, ymax = cand2
                w = round(abs(xmax - xmin), 3)
                h = round(abs(ymax - ymin), 3)
                xc = round((xmin + xmax) / 2.0, 3)
                yc = round((ymin + ymax) / 2.0, 3)

            current_pkg = {
                "name": name,
                "w": w,
                "h": h,
                "xc": xc,
                "yc": yc,
                "pins": [],
            }
            packages[name.lower()] = current_pkg
            pkg_list.append(current_pkg)

        elif current_pkg is not None:
            if cmd == "RC" and len(toks) >= 5:
                try:
                    current_pkg["xc"] = round(float(toks[1]) * scale, 3)
                    current_pkg["yc"] = round(float(toks[2]) * scale, 3)
                    current_pkg["w"] = round(float(toks[3]) * scale, 3)
                    current_pkg["h"] = round(float(toks[4]) * scale, 3)
                except Exception:
                    pass
            elif cmd == "BND" and len(toks) >= 5:
                try:
                    xmin = float(toks[1]) * scale
                    ymin = float(toks[2]) * scale
                    xmax = float(toks[3]) * scale
                    ymax = float(toks[4]) * scale
                    current_pkg["w"] = round(abs(xmax - xmin), 3)
                    current_pkg["h"] = round(abs(ymax - ymin), 3)
                except Exception:
                    pass
            elif cmd == "PIN" and len(toks) >= 4:
                p_name = toks[1]
                nums = []
                for t in toks[2:]:
                    try:
                        nums.append(float(t))
                    except ValueError:
                        continue
                if len(nums) >= 2:
                    px = round(nums[0] * scale, 3)
                    py = round(nums[1] * scale, 3)
                    pw = round(nums[2] * scale, 3) if len(nums) >= 4 else 0.5
                    ph = round(nums[3] * scale, 3) if len(nums) >= 4 else 0.5
                    current_pkg["pins"].append({
                        "n": p_name,
                        "x": px,
                        "y": py,
                        "w": pw,
                        "h": ph,
                    })

    # Automatically derive footprint boundaries from outer pin extents if missing or too small
    for pkg in pkg_list:
        if pkg["pins"]:
            xs = [p["x"] for p in pkg["pins"]]
            ys = [p["y"] for p in pkg["pins"]]
            pad_w_max = max(p["w"] for p in pkg["pins"])
            pad_h_max = max(p["h"] for p in pkg["pins"])
            span_x = round(max(xs) - min(xs) + pad_w_max + 0.35, 3)
            span_y = round(max(ys) - min(ys) + pad_h_max + 0.35, 3)
            if pkg["w"] <= 0.2 or span_x > pkg["w"]:
                pkg["w"] = span_x
                pkg["xc"] = round((min(xs) + max(xs)) / 2.0, 3)
            if pkg["h"] <= 0.2 or span_y > pkg["h"]:
                pkg["h"] = span_y
                pkg["yc"] = round((min(ys) + max(ys)) / 2.0, 3)

    return packages, pkg_list


def parse_components_with_packages(comp_content, packages, pkg_list, side="TOP"):
    """Parses placed components and matches them to package boundaries and pins."""
    if not comp_content:
        return []
    coord_scale, _ = get_unit_scale_to_mm(comp_content, default_unit="MM")
    components = []

    for line in comp_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        if toks[0].upper() == "CMP" and len(toks) >= 7:
            try:
                if toks[2].replace(".", "", 1).lstrip("-").isdigit():
                    pkg_ref = toks[1]
                    x = float(toks[2]) * coord_scale
                    y = float(toks[3]) * coord_scale
                    rot = float(toks[4])
                    mir = 1 if toks[5].upper() == "Y" else 0
                    ref = toks[6]
                    part = toks[7] if len(toks) > 7 and not toks[7].startswith(";") else ""
                else:
                    pkg_ref = toks[2]
                    x = float(toks[3]) * coord_scale
                    y = float(toks[4]) * coord_scale
                    rot = float(toks[5])
                    mir = 1 if toks[6].upper() == "Y" else 0
                    ref = toks[7]
                    part = toks[8] if len(toks) > 8 and not toks[8].startswith(";") else ""

                pkg = None
                p_key = pkg_ref.lower()
                if p_key in packages:
                    pkg = packages[p_key]
                elif pkg_ref.isdigit() and int(pkg_ref) < len(pkg_list):
                    pkg = pkg_list[int(pkg_ref)]

                pins = []
                if pkg and pkg["w"] > 0.1 and pkg["h"] > 0.1:
                    pw = pkg["w"]
                    ph = pkg["h"]
                    pins = pkg["pins"]
                else:
                    rf = ref.upper()
                    if rf.startswith("TP"):
                        pw, ph = 1.3, 1.3
                    elif rf.startswith("D") and any(k in rf for k in ("65", "62", "80", "SMC", "SMB")):
                        pw, ph = 6.8, 4.2
                    elif rf.startswith("C") and any(k in rf for k in ("65", "80", "1210", "1206")):
                        pw, ph = 4.5, 3.2
                    elif rf.startswith("1R") or (rf.startswith("R") and "80" in rf):
                        pw, ph = 6.4, 3.2
                    elif rf.startswith("U") or rf.startswith("TR"):
                        pw, ph = 10.0, 7.0
                    elif rf.startswith("R") or rf.startswith("C"):
                        pw, ph = 1.6, 0.8
                    elif rf.startswith("Q") or rf.startswith("T"):
                        pw, ph = 2.9, 1.3
                    else:
                        pw, ph = 2.5, 1.8

                components.append({
                    "ref": ref,
                    "part": part,
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "w": pw,
                    "h": ph,
                    "rot": round(rot, 1),
                    "mir": mir,
                    "side": side,
                    "pins": pins,
                })
            except Exception:
                continue

    return components


def parse_odb_profile(profile_text):
    coord_scale, _ = get_unit_scale_to_mm(profile_text)
    loops, current = [], []

    for raw_line in profile_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        cmd = toks[0].upper()
        if cmd == "OB":
            current = [round(float(toks[1]) * coord_scale, 3), round(float(toks[2]) * coord_scale, 3)]
        elif cmd == "OS" and current:
            current.extend([round(float(toks[1]) * coord_scale, 3), round(float(toks[2]) * coord_scale, 3)])
        elif cmd == "OC" and current:
            xe = float(toks[1]) * coord_scale
            ye = float(toks[2]) * coord_scale
            xc = float(toks[3]) * coord_scale
            yc = float(toks[4]) * coord_scale
            cw = toks[5].upper() in ("Y", "CW", "TRUE")
            xs, ys = current[-2], current[-1]

            r = (math.hypot(xs - xc, ys - yc) + math.hypot(xe - xc, ye - yc)) / 2.0
            a_start = math.atan2(ys - yc, xs - xc)
            a_end = math.atan2(ye - yc, xe - xc)
            if cw and a_end >= a_start:
                a_end -= 2.0 * math.pi
            elif not cw and a_end <= a_start:
                a_end += 2.0 * math.pi

            for i in range(1, 9):
                ang = a_start + (i / 8.0) * (a_end - a_start)
                current.extend([round(xc + r * math.cos(ang), 3), round(yc + r * math.sin(ang), 3)])

        elif cmd == "OE" and current:
            if len(current) >= 6:
                loops.append(current)
            current = []
    return loops


def build_viewer_html(board_data):
    json_bytes = json.dumps(board_data, separators=(",", ":")).encode("utf-8")
    compressed_b64 = base64.b64encode(gzip.compress(json_bytes, compresslevel=9)).decode("ascii")

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Interactive ODB++ Board Viewer</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #06080a;
    color: #abb2bf;
    overflow: hidden;
    height: 100vh;
    display: flex;
  }}
  #canvas-container {{
    flex: 1;
    position: relative;
    height: 100%;
    cursor: crosshair;
  }}
  canvas {{
    width: 100%;
    height: 100%;
    display: block;
  }}
  #sidebar {{
    width: 320px;
    background: #0f1217;
    border-left: 1px solid #1c2027;
    display: flex;
    flex-direction: column;
    z-index: 10;
  }}
  .header {{
    padding: 12px 14px 8px;
    border-bottom: 1px solid #1c2027;
  }}
  .header h1 {{
    font-size: 14px;
    font-weight: 600;
    color: #e5c07b;
  }}
  .header .info {{
    font-size: 11px;
    color: #5c6370;
  }}
  .search-bar {{
    padding: 8px 12px;
    border-bottom: 1px solid #1c2027;
    background: #0b0d11;
    display: flex;
    gap: 6px;
  }}
  .search-bar input {{
    flex: 1;
    background: #151921;
    border: 1px solid #2a303c;
    color: #fff;
    padding: 5px 8px;
    border-radius: 4px;
    font-size: 11px;
    outline: none;
  }}
  .search-bar input:focus {{
    border-color: #61afef;
  }}
  .controls-bar {{
    padding: 8px 12px;
    border-bottom: 1px solid #1c2027;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    background: #0b0d11;
  }}
  button {{
    background: #181d26;
    border: 1px solid #2a303c;
    color: #abb2bf;
    padding: 5px 9px;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    font-weight: 500;
  }}
  button:hover {{
    background: #232936;
    color: #fff;
    border-color: #61afef;
  }}
  .layer-list {{
    flex: 1;
    overflow-y: auto;
    padding: 8px 12px;
  }}
  .section-title {{
    font-size: 10px;
    text-transform: uppercase;
    color: #5c6370;
    font-weight: 700;
    margin: 10px 4px 4px;
  }}
  .layer-row {{
    display: flex;
    align-items: center;
    padding: 5px 8px;
    border-radius: 4px;
    margin-bottom: 3px;
    background: #13171f;
  }}
  .layer-row:hover {{
    background: #1a202b;
  }}
  .layer-row input[type="checkbox"] {{
    margin-right: 8px;
    accent-color: #61afef;
    cursor: pointer;
  }}
  .layer-row input[type="color"] {{
    border: none;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    margin-right: 8px;
    cursor: pointer;
    background: none;
  }}
  .layer-label {{
    flex: 1;
    font-size: 12px;
    color: #d19a66;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .layer-count {{
    font-size: 10px;
    color: #5c6370;
    margin-left: 6px;
  }}
  #hud {{
    position: absolute;
    bottom: 12px;
    left: 12px;
    background: rgba(15, 18, 23, 0.94);
    border: 1px solid #282c34;
    border-radius: 5px;
    padding: 7px 11px;
    font-size: 11px;
    font-family: monospace;
    pointer-events: none;
  }}
  #hud span {{
    color: #61afef;
  }}
  #loader {{
    position: absolute;
    inset: 0;
    background: #06080a;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #e5c07b;
    font-size: 14px;
    z-index: 100;
  }}
</style>
</head>
<body>
  <div id="loader">Decompressing in Background Thread...</div>

  <div id="canvas-container">
    <canvas id="pcbCanvas"></canvas>
    <div id="hud">
      Cursor: <span id="pos-coords">X: 0.00 mm, Y: 0.00 mm</span> | 
      Board: <span id="board-dims">--</span> | 
      Zoom: <span id="zoom-level">100%</span>
      <div id="measure-hud" style="color: #98c379; display: none; margin-top: 3px;">
        Measure Distance: <span id="measure-dist">0.00 mm</span>
      </div>
    </div>
  </div>

  <div id="sidebar">
    <div class="header">
      <h1>ODB++ Fast Viewer</h1>
      <div class="info" id="step-info">Step: --</div>
    </div>
    <div class="search-bar">
      <input type="text" id="comp-search" placeholder="Search Component (e.g. U5101, TR6500_LW, TP8051)...">
      <button id="btn-search-clear">Clear</button>
    </div>
    <div class="controls-bar">
      <button id="btn-fit">Fit (F)</button>
      <button id="btn-top">Top View</button>
      <button id="btn-bot">Bottom View</button>
      <button id="btn-mirror">Mirror X (M)</button>
    </div>
    <div class="layer-list" id="layerList"></div>
  </div>

<script>
const payloadB64 = "{compressed_b64}";

let board = null;
const canvas = document.getElementById('pcbCanvas');
const ctx = canvas.getContext('2d');
const posCoords = document.getElementById('pos-coords');
const boardDims = document.getElementById('board-dims');
const zoomLevel = document.getElementById('zoom-level');
const stepInfo = document.getElementById('step-info');
const measureHud = document.getElementById('measure-hud');
const measureDist = document.getElementById('measure-dist');
const compSearch = document.getElementById('comp-search');

let scale = 1.0;
let panX = 0;
let panY = 0;
let mirrorX = false;
let isDragging = false;
let isMeasuring = false;
let dragStartX = 0;
let dragStartY = 0;
let measureP1 = null;
let measureP2 = null;
let searchTarget = null;
let renderRequested = false;

const compConfig = {{
  showPackages: true,
  showTestpoints: true,
  colorPkg: '#00e5ff',
  colorTP: '#00e5ff'
}};

// Background Web Worker Thread (Unblocks UI)
const workerScript = `
self.onmessage = async function(e) {{
  try {{
    const bin = Uint8Array.from(atob(e.data), c => c.charCodeAt(0));
    const ds = new DecompressionStream('gzip');
    const writer = ds.writable.getWriter();
    writer.write(bin);
    writer.close();
    const data = await new Response(ds.readable).json();
    self.postMessage({{ status: 'success', data }});
  }} catch (err) {{
    self.postMessage({{ status: 'error', message: err.message }});
  }}
}};
`;

async function initData() {{
  try {{
    const blob = new Blob([workerScript], {{ type: 'application/javascript' }});
    const worker = new Worker(URL.createObjectURL(blob));

    worker.onmessage = function(e) {{
      if (e.data.status === 'success') {{
        onDataReady(e.data.data);
      }} else {{
        fallbackDecompress();
      }}
      worker.terminate();
    }};

    worker.onerror = function() {{
      fallbackDecompress();
      worker.terminate();
    }};

    worker.postMessage(payloadB64);
  }} catch (e) {{
    fallbackDecompress();
  }}
}}

async function fallbackDecompress() {{
  const bin = Uint8Array.from(atob(payloadB64), c => c.charCodeAt(0));
  const ds = new DecompressionStream('gzip');
  const writer = ds.writable.getWriter();
  writer.write(bin);
  writer.close();
  const data = await new Response(ds.readable).json();
  onDataReady(data);
}}

function onDataReady(data) {{
  board = data;
  stepInfo.textContent = `Active Step: ${{board.step}}`;

  // Precompile Path2D for Board Boundary
  if (board.profile && board.profile.loops) {{
    const p = new Path2D();
    for (const loop of board.profile.loops) {{
      if (loop.length < 4) continue;
      p.moveTo(loop[0], loop[1]);
      for (let i = 2; i < loop.length; i += 2) {{
        p.lineTo(loop[i], loop[i+1]);
      }}
      p.closePath();
    }}
    board.profile.path = p;
  }}

  // Precompile Path2D for each layer chunk
  for (const layer of board.layers) {{
    if (layer.chunks) {{
      for (const chunk of layer.chunks) {{
        if (chunk.lines) {{
          for (const grp of chunk.lines) {{
            const p = new Path2D();
            const pts = grp.pts;
            for (let i = 0; i < pts.length; i += 4) {{
              p.moveTo(pts[i], pts[i+1]);
              p.lineTo(pts[i+2], pts[i+3]);
            }}
            grp.path = p;
          }}
        }}
        if (chunk.arcs) {{
          for (const grp of chunk.arcs) {{
            const p = new Path2D();
            const a = grp.arcs;
            for (let i = 0; i < a.length; i += 6) {{
              p.moveTo(a[i] + a[i+2] * Math.cos(a[i+3]), a[i+1] + a[i+2] * Math.sin(a[i+3]));
              p.arc(a[i], a[i+1], a[i+2], a[i+3], a[i+4], a[i+5] === 1);
            }}
            grp.path = p;
          }}
        }}
      }}
    }}

    if (layer.circles && layer.circles.length > 0) {{
      const p = new Path2D();
      const c = layer.circles;
      for (let i = 0; i < c.length; i += 3) {{
        p.moveTo(c[i] + c[i+2], c[i+1]);
        p.arc(c[i], c[i+1], c[i+2], 0, Math.PI * 2);
      }}
      layer.circlePath = p;
    }}

    if (layer.surfaces && layer.surfaces.length > 0) {{
      const p = new Path2D();
      for (const poly of layer.surfaces) {{
        if (poly.length < 6) continue;
        p.moveTo(poly[0], poly[1]);
        for (let i = 2; i < poly.length; i += 2) {{
          p.lineTo(poly[i], poly[i+1]);
        }}
        p.closePath();
      }}
      layer.surfacePath = p;
    }}
  }}

  document.getElementById('loader').style.display = 'none';
  buildLayerList();
  resize();
  fitBoard();
}}

function resize() {{
  canvas.width = canvas.parentElement.clientWidth * window.devicePixelRatio;
  canvas.height = canvas.parentElement.clientHeight * window.devicePixelRatio;
  scheduleRender();
}}
window.addEventListener('resize', resize);

function fitBoard() {{
  if (!board) return;
  const [minX, minY, maxX, maxY] = board.bbox;
  const bW = (maxX - minX) || 50;
  const bH = (maxY - minY) || 50;

  boardDims.textContent = `${{bW.toFixed(2)}} × ${{bH.toFixed(2)}} mm`;

  const pad = 40 * window.devicePixelRatio;
  const availW = canvas.width - pad * 2;
  const availH = canvas.height - pad * 2;

  scale = Math.min(availW / bW, availH / bH);
  const midX = (minX + maxX) / 2;
  const midY = (minY + maxY) / 2;

  panX = canvas.width / 2 - (mirrorX ? -midX : midX) * scale;
  panY = canvas.height / 2 + midY * scale;

  scheduleRender();
}}

function toScreen(x, y) {{
  return [
    (mirrorX ? -x : x) * scale + panX,
    -y * scale + panY
  ];
}}

function toWorld(sx, sy) {{
  return [
    ((sx - panX) / scale) * (mirrorX ? -1 : 1),
    -(sy - panY) / scale
  ];
}}

function scheduleRender() {{
  if (!renderRequested) {{
    renderRequested = true;
    requestAnimationFrame(() => {{
      render();
      renderRequested = false;
    }});
  }}
}}

function render() {{
  if (!board) return;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Compute World Viewport Box for Frustum Culling
  const vP1 = toWorld(0, canvas.height);
  const vP2 = toWorld(canvas.width, 0);
  const vMinX = Math.min(vP1[0], vP2[0]);
  const vMaxX = Math.max(vP1[0], vP2[0]);
  const vMinY = Math.min(vP1[1], vP2[1]);
  const vMaxY = Math.max(vP1[1], vP2[1]);

  // Set Hardware Transform (World mm to Screen)
  ctx.setTransform(
    mirrorX ? -scale : scale,
    0,
    0,
    -scale,
    panX,
    panY
  );

  // 1. Draw Substrate Laminate (PCB Dark Core)
  if (board.profile && board.profile.path) {{
    ctx.fillStyle = '#0a120c';
    ctx.fill(board.profile.path);
  }}

  // 2. Hardware-Accelerated Vector Layers (with Frustum Culling)
  ctx.lineCap = 'square';
  for (const layer of board.layers) {{
    if (!layer.visible) continue;
    // Side culling
    if ((layer.side === 'TOP' && mirrorX) || (layer.side === 'BOTTOM' && !mirrorX)) continue;

    ctx.strokeStyle = layer.color;
    ctx.fillStyle = layer.color;

    // Copper Surfaces / Ground Pours
    if (layer.surfacePath) {{
      ctx.globalAlpha = 0.65;
      ctx.fill(layer.surfacePath);
      ctx.globalAlpha = 1.0;
    }}

    // Spatial Chunk Culled Tracks & Arcs
    if (layer.chunks) {{
      for (const chunk of layer.chunks) {{
        const b = chunk.b;
        if (b[0] > vMaxX || b[2] < vMinX || b[1] > vMaxY || b[3] < vMinY) continue;

        if (chunk.lines) {{
          for (const grp of chunk.lines) {{
            ctx.lineWidth = Math.max(grp.w, 0.75 / scale);
            ctx.stroke(grp.path);
          }}
        }}
        if (chunk.arcs) {{
          for (const grp of chunk.arcs) {{
            ctx.lineWidth = Math.max(grp.w, 0.75 / scale);
            ctx.stroke(grp.path);
          }}
        }}
      }}
    }}

    // Circular Gold Pads & Vias
    if (layer.circlePath) {{
      ctx.fill(layer.circlePath);
    }}

    // Rectangular Gold Pads
    if (layer.rects && layer.rects.length > 0) {{
      const r = layer.rects;
      for (let i = 0; i < r.length; i += 5) {{
        const x = r[i], y = r[i+1], w = r[i+2], h = r[i+3], ang = r[i+4];
        if (x + w < vMinX || x - w > vMaxX || y + h < vMinY || y - h > vMaxY) continue;
        if (ang === 0) {{
          ctx.fillRect(x - w / 2, y - h / 2, w, h);
        }} else {{
          ctx.save();
          ctx.translate(x, y);
          ctx.rotate(ang * (Math.PI / 180));
          ctx.fillRect(-w / 2, -h / 2, w, h);
          ctx.restore();
        }}
      }}
    }}
  }}

  // 3. Component Footprints (Cyan Courtyard + Red Pin 1) & Circular Test Points
  if (board.components && board.components.length > 0) {{
    for (const cmp of board.components) {{
      const isTop = cmp.side === 'TOP';
      // Side Culling
      if ((isTop && mirrorX) || (!isTop && !mirrorX)) continue;
      // Frustum Culling
      if (cmp.x + cmp.w < vMinX || cmp.x - cmp.w > vMaxX || cmp.y + cmp.h < vMinY || cmp.y - cmp.h > vMaxY) continue;

      const isTP = cmp.ref.toUpperCase().startsWith('TP');
      const isTarget = searchTarget && cmp.ref.toLowerCase() === searchTarget.toLowerCase();

      ctx.save();
      ctx.translate(cmp.x, cmp.y);

      if (isTP) {{
        // ROUND LIGHT-FILLED TEST POINT (NO PLUS MARKERS)
        if (compConfig.showTestpoints) {{
          const r = Math.max(cmp.w, cmp.h, 1.25) / 2.0;

          // Light Fill Inside Circle
          ctx.fillStyle = isTarget ? '#ff2222' : '#e0f7fa';
          ctx.beginPath();
          ctx.arc(0, 0, r, 0, Math.PI * 2);
          ctx.fill();

          // Clean Circular Border
          ctx.strokeStyle = isTarget ? '#ff2222' : compConfig.colorTP;
          ctx.lineWidth = Math.max(isTarget ? 0.22 : 0.12, 1.2 / scale);
          ctx.stroke();

          // Centered Test Point Name Inside Circle (with LOD)
          const screenD = r * 2 * scale;
          if (screenD >= 18 || isTarget) {{
            ctx.save();
            ctx.scale(mirrorX ? -1 : 1, -1);
            const maxFontPx = Math.min((screenD * 0.85) / (cmp.ref.length * 0.6), screenD * 0.38);
            if (maxFontPx >= 7.5 || isTarget) {{
              ctx.font = `bold ${{Math.max(Math.min(maxFontPx, 15), 7.5)}}px monospace`;
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              ctx.fillStyle = isTarget ? '#ffffff' : '#00363a';
              ctx.fillText(cmp.ref, 0, 0);
            }}
            ctx.restore();
          }}
        }}
      }} else if (compConfig.showPackages) {{
        // SMT COMPONENT COURTYARD & PINS
        ctx.rotate(cmp.rot * (Math.PI / 180));

        // Courtyard Outline
        ctx.strokeStyle = isTarget ? '#ff2222' : compConfig.colorPkg;
        ctx.lineWidth = Math.max(isTarget ? 0.25 : 0.12, 1.2 / scale);
        ctx.strokeRect(-cmp.w / 2, -cmp.h / 2, cmp.w, cmp.h);

        // Draw Pins: Pin 1 in RED, Pin 2+ in CYAN
        if (cmp.pins && cmp.pins.length > 0) {{
          for (const pin of cmp.pins) {{
            const isPin1 = pin.n === '1' || pin.n.toLowerCase() === 'a' || pin.n === '+';
            ctx.fillStyle = isPin1 ? 'rgba(255, 34, 34, 0.8)' : 'rgba(0, 229, 255, 0.4)';
            ctx.strokeStyle = isPin1 ? '#ff2222' : '#00e5ff';
            ctx.lineWidth = Math.max(0.08, 0.8 / scale);
            ctx.fillRect(pin.x - pin.w / 2, pin.y - pin.h / 2, pin.w, pin.h);
            ctx.strokeRect(pin.x - pin.w / 2, pin.y - pin.h / 2, pin.w, pin.h);
          }}
        }} else {{
          // Synthesize pads for 2-pin passives (Pin 1 Left/Top in Red)
          const pw = cmp.w >= cmp.h ? Math.min(cmp.w * 0.28, 1.2) : cmp.w * 0.8;
          const ph = cmp.w >= cmp.h ? cmp.h * 0.8 : Math.min(cmp.h * 0.28, 1.2);
          ctx.lineWidth = Math.max(0.08, 0.8 / scale);

          if (cmp.w >= cmp.h) {{
            ctx.fillStyle = 'rgba(255, 34, 34, 0.75)';
            ctx.strokeStyle = '#ff2222';
            ctx.fillRect(-cmp.w / 2 + 0.05, -ph / 2, pw, ph);
            ctx.strokeRect(-cmp.w / 2 + 0.05, -ph / 2, pw, ph);

            ctx.fillStyle = 'rgba(0, 229, 255, 0.4)';
            ctx.strokeStyle = '#00e5ff';
            ctx.fillRect(cmp.w / 2 - pw - 0.05, -ph / 2, pw, ph);
            ctx.strokeRect(cmp.w / 2 - pw - 0.05, -ph / 2, pw, ph);
          }} else {{
            ctx.fillStyle = 'rgba(255, 34, 34, 0.75)';
            ctx.strokeStyle = '#ff2222';
            ctx.fillRect(-pw / 2, cmp.h / 2 - ph - 0.05, pw, ph);
            ctx.strokeRect(-pw / 2, cmp.h / 2 - ph - 0.05, pw, ph);

            ctx.fillStyle = 'rgba(0, 229, 255, 0.4)';
            ctx.strokeStyle = '#00e5ff';
            ctx.fillRect(-pw / 2, -cmp.h / 2 + 0.05, pw, ph);
            ctx.strokeRect(-pw / 2, -cmp.h / 2 + 0.05, pw, ph);
          }}
        }}

        // ONLY RENDER LABEL IF IT IS THE ACTIVE SEARCH TARGET
        if (isTarget) {{
          ctx.save();
          ctx.scale(1, -1);
          ctx.font = 'bold 16px monospace';
          ctx.fillStyle = '#ff2222';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(cmp.ref, 0, 0);
          ctx.restore();
        }}
      }}

      ctx.restore();
    }}
  }}

  // 4. Board Boundary Edge
  if (board.profile && board.profile.visible && board.profile.path) {{
    ctx.strokeStyle = board.profile.color;
    ctx.lineWidth = Math.max(0.25, 1.5 / scale);
    ctx.stroke(board.profile.path);
  }}

  // 5. Silkscreen Text Layer (Top/Bottom Overlay)
  ctx.restore();
  for (const layer of board.layers) {{
    if (!layer.visible || !layer.texts || layer.texts.length === 0) continue;
    if ((layer.side === 'TOP' && mirrorX) || (layer.side === 'BOTTOM' && !mirrorX)) continue;
    ctx.fillStyle = layer.color;

    for (const t of layer.texts) {{
      const x = t[0], y = t[1], text = t[2], h = t[3], rot = t[4], mir = t[5];
      if (x < vMinX || x > vMaxX || y < vMinY || y > vMaxY) continue;
      if (h * scale < 4.0) continue; // LOD threshold: skip unreadable micro text when zoomed out

      const [sx, sy] = toScreen(x, y);
      ctx.save();
      ctx.translate(sx, sy);

      let ang = -rot * (Math.PI / 180);
      if (mirrorX) ang = Math.PI - ang;
      ctx.rotate(ang);
      if (mir) ctx.scale(-1, 1);

      const FONT_RES = 64;
      ctx.scale(scale / FONT_RES, scale / FONT_RES);
      ctx.font = `600 ${{Math.round(h * FONT_RES)}}px -apple-system, sans-serif`;
      ctx.textBaseline = 'bottom';
      ctx.fillText(text, 0, 0);
      ctx.restore();
    }}
  }}

  // 6. Shift + Measure Tool Overlay
  if (measureP1 && measureP2) {{
    const [p1x, p1y] = toScreen(measureP1[0], measureP1[1]);
    const [p2x, p2y] = toScreen(measureP2[0], measureP2[1]);

    ctx.save();
    ctx.strokeStyle = '#98c379';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(p1x, p1y);
    ctx.lineTo(p2x, p2y);
    ctx.stroke();

    ctx.fillStyle = '#98c379';
    ctx.beginPath();
    ctx.arc(p1x, p1y, 4, 0, Math.PI * 2);
    ctx.arc(p2x, p2y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }}

  zoomLevel.textContent = `${{Math.round((scale / window.devicePixelRatio) * 100)}}%`;
}}

function buildLayerList() {{
  const list = document.getElementById('layerList');
  list.innerHTML = '';

  if (board.profile) {{
    const row = document.createElement('div');
    row.className = 'layer-row';
    row.innerHTML = `
      <input type="checkbox" id="chk-prof" ${{board.profile.visible ? 'checked' : ''}}>
      <input type="color" id="col-prof" value="${{board.profile.color}}">
      <span class="layer-label">Board Outline</span>
      <span class="layer-count">${{board.profile.loops.length}}</span>
    `;
    row.querySelector('#chk-prof').addEventListener('change', e => {{
      board.profile.visible = e.target.checked;
      scheduleRender();
    }});
    row.querySelector('#col-prof').addEventListener('input', e => {{
      board.profile.color = e.target.value;
      scheduleRender();
    }});
    list.appendChild(row);
  }}

  const compSec = document.createElement('div');
  compSec.className = 'section-title';
  compSec.textContent = 'Component Overlays';
  list.appendChild(compSec);

  const pkgRow = document.createElement('div');
  pkgRow.className = 'layer-row';
  pkgRow.innerHTML = `
    <input type="checkbox" id="chk-pkg" ${{compConfig.showPackages ? 'checked' : ''}}>
    <input type="color" id="col-pkg" value="${{compConfig.colorPkg}}">
    <span class="layer-label">SMD Packages & Pins</span>
    <span class="layer-count">${{board.components?.length || 0}}</span>
  `;
  pkgRow.querySelector('#chk-pkg').addEventListener('change', e => {{
    compConfig.showPackages = e.target.checked;
    scheduleRender();
  }});
  pkgRow.querySelector('#col-pkg').addEventListener('input', e => {{
    compConfig.colorPkg = e.target.value;
    scheduleRender();
  }});
  list.appendChild(pkgRow);

  const tpRow = document.createElement('div');
  tpRow.className = 'layer-row';
  tpRow.innerHTML = `
    <input type="checkbox" id="chk-tp" ${{compConfig.showTestpoints ? 'checked' : ''}}>
    <input type="color" id="col-tp" value="${{compConfig.colorTP}}">
    <span class="layer-label">Round Test Points & Names</span>
  `;
  tpRow.querySelector('#chk-tp').addEventListener('change', e => {{
    compConfig.showTestpoints = e.target.checked;
    scheduleRender();
  }});
  tpRow.querySelector('#col-tp').addEventListener('input', e => {{
    compConfig.colorTP = e.target.value;
    scheduleRender();
  }});
  list.appendChild(tpRow);

  const laySec = document.createElement('div');
  laySec.className = 'section-title';
  laySec.textContent = 'PCB Layers';
  list.appendChild(laySec);

  board.layers.forEach((layer, idx) => {{
    const row = document.createElement('div');
    row.className = 'layer-row';
    row.innerHTML = `
      <input type="checkbox" id="chk-${{idx}}" ${{layer.visible ? 'checked' : ''}}>
      <input type="color" id="col-${{idx}}" value="${{layer.color}}">
      <span class="layer-label">${{layer.label || layer.name}}</span>
      <span class="layer-count">${{layer.itemCount || 0}}</span>
    `;
    row.querySelector(`#chk-${{idx}}`).addEventListener('change', e => {{
      layer.visible = e.target.checked;
      scheduleRender();
    }});
    row.querySelector(`#col-${{idx}}`).addEventListener('input', e => {{
      layer.color = e.target.value;
      scheduleRender();
    }});
    list.appendChild(row);
  }});
}}

// Top / Bottom View Presets
document.getElementById('btn-top').addEventListener('click', () => {{
  mirrorX = false;
  board.layers.forEach(l => {{
    if (l.side === 'TOP') {{
      l.visible = (l.type === 'SIGNAL' || l.type === 'SOLDER_MASK');
    }} else if (l.side === 'BOTTOM' || l.side === 'INNER') {{
      l.visible = false;
    }}
  }});

  buildLayerList();
  fitBoard();
}});

document.getElementById('btn-bot').addEventListener('click', () => {{
  mirrorX = true;
  board.layers.forEach(l => {{
    if (l.side === 'BOTTOM') {{
      l.visible = (l.type === 'SIGNAL' || l.type === 'SOLDER_MASK');
    }} else if (l.side === 'TOP' || l.side === 'INNER') {{
      l.visible = false;
    }}
  }});

  buildLayerList();
  fitBoard();
}});

// Search & Focus on Any Component or Test Point
compSearch.addEventListener('input', e => {{
  searchTarget = e.target.value.trim();
  if (searchTarget && board.components) {{
    const match = board.components.find(c => c.ref.toLowerCase() === searchTarget.toLowerCase());
    if (match) {{
      mirrorX = (match.side === 'BOTTOM');
      buildLayerList();

      const [sx, sy] = toScreen(match.x, match.y);
      panX += (canvas.width / 2 - sx);
      panY += (canvas.height / 2 - sy);
    }}
  }}
  scheduleRender();
}});

document.getElementById('btn-search-clear').addEventListener('click', () => {{
  compSearch.value = '';
  searchTarget = null;
  scheduleRender();
}});

// Canvas Pan & Zoom
canvas.addEventListener('mousedown', e => {{
  const mx = e.clientX * window.devicePixelRatio;
  const my = e.clientY * window.devicePixelRatio;

  if (e.shiftKey) {{
    isMeasuring = true;
    measureP1 = toWorld(mx, my);
    measureP2 = measureP1;
    measureHud.style.display = 'block';
  }} else {{
    isDragging = true;
    dragStartX = mx - panX;
    dragStartY = my - panY;
  }}
}});

window.addEventListener('mouseup', () => {{
  isDragging = false;
  isMeasuring = false;
}});

canvas.addEventListener('mousemove', e => {{
  const mx = e.clientX * window.devicePixelRatio;
  const my = e.clientY * window.devicePixelRatio;

  if (isDragging) {{
    panX = mx - dragStartX;
    panY = my - dragStartY;
    scheduleRender();
  }} else if (isMeasuring) {{
    measureP2 = toWorld(mx, my);
    const d = Math.hypot(measureP2[0] - measureP1[0], measureP2[1] - measureP1[1]);
    measureDist.textContent = `${{d.toFixed(3)}} mm`;
    scheduleRender();
  }}

  const [wx, wy] = toWorld(mx, my);
  posCoords.textContent = `X: ${{wx.toFixed(3)}} mm, Y: ${{wy.toFixed(3)}} mm`;
}});

canvas.addEventListener('wheel', e => {{
  e.preventDefault();
  const mx = e.clientX * window.devicePixelRatio;
  const my = e.clientY * window.devicePixelRatio;

  const [wx, wy] = toWorld(mx, my);
  const factor = e.deltaY < 0 ? 1.15 : 0.85;
  scale *= factor;

  panX = mx - (mirrorX ? -wx : wx) * scale;
  panY = my + wy * scale;
  scheduleRender();
}}, {{ passive: false }});

document.getElementById('btn-fit').addEventListener('click', fitBoard);
document.getElementById('btn-mirror').addEventListener('click', () => {{
  mirrorX = !mirrorX;
  fitBoard();
}});

window.addEventListener('keydown', e => {{
  if (e.key === 'f' || e.key === 'F') fitBoard();
  if (e.key === 'm' || e.key === 'M') {{
    mirrorX = !mirrorX;
    fitBoard();
  }}
  if (e.key === 'Escape') {{
    measureP1 = null;
    measureP2 = null;
    measureHud.style.display = 'none';
    scheduleRender();
  }}
}});

initData();
</script>
</body>
</html>"""
    return html_template


def main():
    parser = argparse.ArgumentParser(description="Generate ultra-fast, compact HTML PCB viewer from ODB++.")
    parser.add_argument("odb_input", help="Path to ODB++ .zip, .tgz, or extracted directory")
    parser.add_argument("-o", "--output", default="pcb_viewer.html", help="Output HTML file")
    parser.add_argument("-s", "--step", default=None, help="Target step name")
    args = parser.parse_args()

    # Step Detection
    step = args.step
    if not step:
        for candidate in ("board_0_0", "pcb", "1976156-03"):
            prof, _ = read_file_from_source(args.odb_input, "profile", preferred_step=candidate)
            if prof:
                step = candidate
                break
    if not step:
        step = "pcb"

    print(f"Loading ODB++ Step: {step}")

    # 1. Parse Matrix Stackup
    matrix_content, _ = read_file_from_source(args.odb_input, "matrix/matrix")
    matrix_meta = parse_matrix_file(matrix_content)

    # 2. Parse Profile (Outline)
    profile_content, _ = read_file_from_source(args.odb_input, "profile", preferred_step=step)
    if not profile_content:
        profile_content, _ = read_file_from_source(args.odb_input, "layers/outline/features", preferred_step=step)
    outline_loops = parse_odb_profile(profile_content) if profile_content else []

    # 3. Parse EDA Package Definitions
    eda_content, _ = read_file_from_source(args.odb_input, "eda/data", preferred_step=step)
    packages, pkg_list = parse_eda_packages(eda_content)

    # 4. Parse Placed Components with Pins
    comp_top_raw, _ = read_file_from_source(args.odb_input, "comp_+_top/components", preferred_step=step)
    comp_bot_raw, _ = read_file_from_source(args.odb_input, "comp_+_bot/components", preferred_step=step)
    components = (
        parse_components_with_packages(comp_top_raw, packages, pkg_list, side="TOP") +
        parse_components_with_packages(comp_bot_raw, packages, pkg_list, side="BOTTOM")
    )
    print(f"Extracted {len(components)} component footprints with pins and boundaries.")

    # 5. Extract Feature Layers
    norm_step = f"steps/{step.lower()}/layers/"
    avail_layers = []
    if zipfile.is_zipfile(args.odb_input):
        with zipfile.ZipFile(args.odb_input, "r") as z:
            for name in z.namelist():
                nl = name.replace("\\", "/").lower()
                if nl.endswith("/features") and norm_step in nl:
                    parts = nl.split(norm_step)[1].split("/")
                    if len(parts) >= 2:
                        avail_layers.append(parts[0])
    avail_layers = sorted(list(set(avail_layers)))

    parsed_layers = []
    min_x, min_y, max_x, max_y = float("inf"), float("inf"), float("-inf"), float("-inf")

    for loop in outline_loops:
        for i in range(0, len(loop), 2):
            min_x, min_y = min(min_x, loop[i]), min(min_y, loop[i+1])
            max_x, max_y = max(max_x, loop[i]), max(max_y, loop[i+1])

    for l_name in avail_layers:
        nl = l_name.lower()
        if nl in ("all", "profile", "paneloutline", "frame", "header"):
            continue

        meta = matrix_meta.get(nl, {})
        side = meta.get("SIDE", "")
        l_type = meta.get("TYPE", "")

        # Side Heuristics (Altium & Mentor)
        if not side:
            if "top" in nl or nl == "_tpm":
                side = "TOP"
            elif "bottom" in nl or "bot" in nl or nl == "_bpm":
                side = "BOTTOM"
            elif "mid" in nl or "inner" in nl:
                side = "INNER"
            else:
                side = "TOP"

        # Type Heuristics
        if not l_type:
            if "silk" in nl or "overlay" in nl:
                l_type = "SILK_SCREEN"
            elif "mask" in nl or "solder" in nl:
                l_type = "SOLDER_MASK"
            elif "paste" in nl:
                l_type = "PASTE_MASK"
            elif "drill" in nl or "rout" in nl:
                l_type = "DRILL"
            elif "layer" in nl or "signal" in nl or "top" in nl or "bottom" in nl:
                l_type = "SIGNAL"
            else:
                l_type = "DOCUMENT"

        features_data, _ = read_file_from_source(args.odb_input, f"layers/{l_name}/features", preferred_step=step)
        if not features_data:
            continue

        # Strictly ignore text in non-silkscreen layers
        allow_texts = (l_type == "SILK_SCREEN")
        l_data = parse_layer_features(features_data, allow_texts=allow_texts)
        item_count = (
            len(l_data["lines"]) +
            len(l_data["arcs"]) +
            len(l_data["circles"]) +
            len(l_data["rects"]) +
            len(l_data["surfaces"])
        )
        if item_count == 0 and len(l_data["texts"]) == 0:
            continue

        # Color Palette matching EDA Standards
        is_visible = False
        color = "#808080"
        order = 50

        if l_type == "SIGNAL" and side == "TOP":
            color = "#29663c"  # THE LIGHTER GREEN (Pours & Traces)
            is_visible = True
            order = 30
        elif l_type == "SIGNAL" and side == "BOTTOM":
            color = "#1e3a5f"  # Slate Blue Copper
            is_visible = False
            order = 10
        elif l_type == "SOLDER_MASK" and side == "TOP":
            color = "#c49c3e"  # Gold / Brass Solder Pads
            is_visible = True
            order = 35
        elif l_type == "SOLDER_MASK" and side == "BOTTOM":
            color = "#c49c3e"  # Gold Solder Pads (Bottom)
            is_visible = False
            order = 15
        elif l_type == "SILK_SCREEN" and side == "TOP":
            color = "#d9a738"  # Amber Gold Silk
            is_visible = False
            order = 60
        elif l_type == "SILK_SCREEN" and side == "BOTTOM":
            color = "#8fa370"
            is_visible = False
            order = 20
        elif l_type == "DRILL":
            color = "#bfa45a"
            is_visible = False
            order = 80

        chunked = chunk_layer_geometry(l_data)

        parsed_layers.append({
            "name": l_name,
            "label": l_name.replace("_", " ").title(),
            "side": side,
            "type": l_type,
            "color": color,
            "visible": is_visible,
            "order": order,
            "itemCount": item_count,
            "chunks": chunked["chunks"],
            "circles": chunked["circles"],
            "rects": chunked["rects"],
            "surfaces": chunked["surfaces"],
            "texts": chunked["texts"],
        })

    parsed_layers.sort(key=lambda x: x["order"])

    if min_x == float("inf"):
        min_x, min_y, max_x, max_y = 0.0, 0.0, 100.0, 100.0

    board_payload = {
        "step": step,
        "bbox": [round(min_x, 3), round(min_y, 3), round(max_x, 3), round(max_y, 3)],
        "profile": {
            "color": "#e5c07b",
            "visible": True,
            "loops": outline_loops,
        },
        "components": components,
        "layers": parsed_layers,
    }

    html_out = build_viewer_html(board_payload)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_out)

    size_mb = os.path.getsize(args.output) / (1024 * 1024)
    print(f"Generated viewer: {os.path.abspath(args.output)} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()