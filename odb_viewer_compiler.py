#!/usr/bin/env python3
"""
Autonomous ODB++ to PCB Investigator-Grade HTML5 Standalone Viewer Compiler.
Single-file script with zero external dependencies (pure Python standard library).
Parses any valid ODB++ archive (.zip, .tgz, .tar.gz, or extracted directory)
and compiles a self-contained, offline-ready, high-performance HTML5 Canvas viewer.
"""

import argparse
import base64
import gzip
import json
import math
import os
import re
import struct
import sys
import tarfile
import zipfile


def read_file_from_source(source, target_suffix, preferred_step=None):
    """Finds and reads a file matching target suffix from zip, tar, or directory.
    
    If preferred_step is specified, strictly prefers matches containing 'steps/<preferred_step>/'.
    """
    norm_target = target_suffix.replace("\\", "/").lower()
    matches = []

    if os.path.isdir(source):
        for root, _, files in os.walk(source):
            for f in files:
                full_path = os.path.join(root, f)
                rel = os.path.relpath(full_path, source).replace("\\", "/").lower()
                if rel.endswith(norm_target):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as fp:
                            matches.append((fp.read(), rel))
                    except Exception:
                        pass

    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source, "r") as z:
            for name in z.namelist():
                rel = name.replace("\\", "/").lower()
                if rel.endswith(norm_target):
                    try:
                        with z.open(name) as fp:
                            matches.append((fp.read().decode("utf-8", errors="ignore"), rel))
                    except Exception:
                        pass

    elif tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as t:
            for member in t.getmembers():
                rel = member.name.replace("\\", "/").lower()
                if rel.endswith(norm_target):
                    try:
                        f = t.extractfile(member)
                        if f:
                            matches.append((f.read().decode("utf-8", errors="ignore"), rel))
                    except Exception:
                        pass

    if not matches:
        return None, None

    if preferred_step:
        step_str = f"steps/{preferred_step.lower()}/"
        for content, path in matches:
            if step_str in path:
                return content, path

    return matches[0]


def get_available_steps(source):
    """Detects all available steps in the ODB++ source and determines the primary step."""
    steps = set()
    first_col_step = None

    # Check matrix/matrix first for STEP blocks
    matrix_content, _ = read_file_from_source(source, "matrix/matrix")
    if matrix_content:
        in_step = False
        cur_step = {}
        for line in matrix_content.splitlines():
            line = line.strip()
            if line.startswith("STEP {") or line.startswith("STEP{"):
                in_step = True
                cur_step = {}
            elif line.startswith("}"):
                if in_step and "NAME" in cur_step:
                    name = cur_step["NAME"]
                    steps.add(name)
                    if cur_step.get("COL") == "1" and not first_col_step:
                        first_col_step = name
                in_step = False
                cur_step = {}
            elif in_step and "=" in line:
                k, v = line.split("=", 1)
                cur_step[k.strip().upper()] = v.strip().upper()

    # Discover steps from file paths
    all_names = []
    if os.path.isdir(source):
        for root, _, files in os.walk(source):
            for f in files:
                all_names.append(os.path.relpath(os.path.join(root, f), source).replace("\\", "/"))
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source, "r") as z:
            all_names = z.namelist()
    elif tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as t:
            all_names = [m.name for m in t.getmembers()]

    for name in all_names:
        nl = name.replace("\\", "/").lower()
        if "steps/" in nl:
            parts = nl.split("steps/")[1].split("/")
            if len(parts) > 1 and parts[0]:
                steps.add(parts[0])

    step_list = sorted(list(steps))
    # Pick primary step: COL=1, or candidates board_0_0/pcb, or first containing eda/components/profile
    if first_col_step and first_col_step.lower() in [s.lower() for s in step_list]:
        for s in step_list:
            if s.lower() == first_col_step.lower():
                return s, step_list

    for pref in ("board_0_0", "pcb", "board", "unit"):
        for s in step_list:
            if s.lower() == pref:
                return s, step_list

    # Find step with profile or eda/data
    for s in step_list:
        p, _ = read_file_from_source(source, "profile", preferred_step=s)
        if p:
            return s, step_list

    return (step_list[0] if step_list else "pcb"), step_list


def parse_matrix_file(matrix_content):
    """Parses matrix/matrix to identify layer sides, types, and physical stackup."""
    layer_meta = {}
    if not matrix_content:
        return layer_meta

    current_layer = {}
    layers_in_order = []
    for line in matrix_content.splitlines():
        line = line.strip()
        if line.startswith("LAYER {") or line.startswith("LAYER{"):
            current_layer = {}
        elif line.startswith("}"):
            if "NAME" in current_layer:
                layers_in_order.append(current_layer)
            current_layer = {}
        elif "=" in line:
            parts = line.split("=", 1)
            current_layer[parts[0].strip().upper()] = parts[1].strip().upper()

    # Determine stackup context and signal boundaries
    board_signal_layers = []
    for lay in layers_in_order:
        ctx = lay.get("CONTEXT", "BOARD")
        ltype = lay.get("TYPE", "")
        if ctx == "BOARD" and ltype == "SIGNAL":
            board_signal_layers.append(lay)

    top_signal_row = int(board_signal_layers[0].get("ROW", 0)) if board_signal_layers else 0
    bot_signal_row = int(board_signal_layers[-1].get("ROW", 9999)) if board_signal_layers else 9999

    for lay in layers_in_order:
        name = lay.get("NAME", "").lower()
        if not name:
            continue
        row = int(lay.get("ROW", 0))
        ctx = lay.get("CONTEXT", "BOARD")
        ltype = lay.get("TYPE", "")
        side = lay.get("SIDE", "")

        if not side:
            if ctx == "BOARD":
                if ltype == "SIGNAL":
                    if row == top_signal_row:
                        side = "TOP"
                    elif row == bot_signal_row:
                        side = "BOTTOM"
                    else:
                        side = "INNER"
                elif row <= top_signal_row:
                    side = "TOP"
                elif row >= bot_signal_row:
                    side = "BOTTOM"
                else:
                    side = "INNER"
            else:
                # Name heuristics
                if any(k in name for k in ("top", "_tpm", "_tsm", "assembly_top", "silk_top", "overlay_top")):
                    side = "TOP"
                elif any(k in name for k in ("bot", "_bpm", "_bsm", "assembly_bottom", "silk_bot", "overlay_bot")):
                    side = "BOTTOM"
                elif any(k in name for k in ("mid", "inner", "layer_")):
                    side = "INNER"
                else:
                    side = "TOP"

        lay["COMPUTED_SIDE"] = side
        layer_meta[name] = lay

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
    if "INCH" in unit_name:
        return val * 25.4 if val < 0.1 else val * 0.0254
    elif "MIL" in unit_name:
        return val * 0.0254
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


def simplify_contour(pts, tol=0.025):
    """Simplifies polyline coordinates by removing collinear points within spatial distance tolerance."""
    if len(pts) <= 6:
        return pts
    res = [pts[0], pts[1]]
    tol_sq = tol * tol
    for i in range(2, len(pts) - 2, 2):
        x, y = pts[i], pts[i+1]
        dx = x - res[-2]
        dy = y - res[-1]
        if dx * dx + dy * dy >= tol_sq:
            res.extend([x, y])
    res.extend([pts[-2], pts[-1]])
    return res


def delta_encode_contour(pts, scale_factor=100):
    """Converts a flat list of mm coordinates [x0, y0, x1, y1, ...] into integer delta list.
    
    [lx, ly, dx1, dy1, dx2, dy2, ...] where 1 unit = 0.01 mm (10 microns).
    """
    if len(pts) < 4:
        return []
    lx = int(round(pts[0] * scale_factor))
    ly = int(round(pts[1] * scale_factor))
    encoded = [lx, ly]
    for i in range(2, len(pts), 2):
        cx = int(round(pts[i] * scale_factor))
        cy = int(round(pts[i+1] * scale_factor))
        encoded.extend([cx - lx, cy - ly])
        lx, ly = cx, cy
    return encoded


def parse_layer_features(features_text, is_silkscreen=False):
    """Extracts lines, native arcs, pads, surface fills, and verified silkscreen text."""
    coord_scale, unit_name = get_unit_scale_to_mm(features_text)
    symbols = parse_symbols(features_text, unit_name)

    lines, arcs, circles, rects, surfaces, texts = [], [], [], [], [], []
    current_contour = []

    # Decimation tolerance: fine 0.025 mm for silkscreen font glyphs, 0.035 mm for copper pours
    simplify_tol = 0.025 if is_silkscreen else 0.035

    for raw_line in features_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        toks = line.split()
        cmd = toks[0].upper()

        if cmd == "L" and len(toks) >= 6:
            xs = round(float(toks[1]) * coord_scale, 2)
            ys = round(float(toks[2]) * coord_scale, 2)
            xe = round(float(toks[3]) * coord_scale, 2)
            ye = round(float(toks[4]) * coord_scale, 2)
            sym = symbols.get(toks[5], {"type": "circle", "d": 0.2})
            width = round(sym.get("d", sym.get("w", 0.2)), 2)
            lines.append((xs, ys, xe, ye, width))

        elif cmd == "A" and len(toks) >= 8:
            xs = float(toks[1]) * coord_scale
            ys = float(toks[2]) * coord_scale
            xe = float(toks[3]) * coord_scale
            ye = float(toks[4]) * coord_scale
            xc = float(toks[5]) * coord_scale
            yc = float(toks[6]) * coord_scale
            sym = symbols.get(toks[7], {"type": "circle", "d": 0.2})
            width = round(sym.get("d", sym.get("w", 0.2)), 2)
            cw = 1 if (len(toks) > 10 and toks[10].upper() in ("Y", "CW", "TRUE")) else 0

            r = round((math.hypot(xs - xc, ys - yc) + math.hypot(xe - xc, ye - yc)) / 2.0, 3)
            a_start = round(math.atan2(ys - yc, xs - xc), 4)
            a_end = round(math.atan2(ye - yc, xe - xc), 4)
            arcs.append((round(xc, 2), round(yc, 2), r, a_start, a_end, cw, width))

        elif cmd == "P" and len(toks) >= 4:
            x = round(float(toks[1]) * coord_scale, 2)
            y = round(float(toks[2]) * coord_scale, 2)
            sym = symbols.get(toks[3], {"type": "circle", "d": 0.5})
            angle = float(toks[6]) if len(toks) > 6 and toks[6].replace(".", "", 1).lstrip("-").isdigit() else 0.0

            if sym["type"] == "circle":
                circles.extend([x, y, round(sym["d"] / 2.0, 2)])
            else:
                rects.extend([x, y, round(sym["w"], 2), round(sym["h"], 2), round(angle, 1)])

        elif cmd == "T" and len(toks) >= 8 and is_silkscreen:
            # Suppress T records on copper signal layers, extract only on verified silkscreen
            try:
                x = round(float(toks[1]) * coord_scale, 2)
                y = round(float(toks[2]) * coord_scale, 2)
                rot = round(float(toks[5]), 1) if len(toks) > 5 and toks[5].replace(".", "", 1).lstrip("-").isdigit() else 0.0
                mir = 1 if (len(toks) > 6 and toks[6].upper() == "Y") else 0
                raw_h = float(toks[8])
                h = round(raw_h * 0.0254, 3) if raw_h > 10.0 else round(raw_h * coord_scale, 3)

                m_txt = re.search(r"['\"](.*?)['\"]", line)
                text_str = m_txt.group(1) if m_txt else toks[-1].strip("';\"")
                if text_str and 0.35 <= h <= 6.0:
                    texts.append([x, y, text_str, h, rot, mir])
            except Exception:
                pass

        elif cmd == "OB" and len(toks) >= 3:
            current_contour = [float(toks[1]) * coord_scale, float(toks[2]) * coord_scale]

        elif cmd == "OS" and len(toks) >= 3 and current_contour:
            current_contour.extend([float(toks[1]) * coord_scale, float(toks[2]) * coord_scale])

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

            steps_arc = 6
            for i in range(1, steps_arc + 1):
                ang = a_start + (i / float(steps_arc)) * (a_end - a_start)
                current_contour.extend([xc + r * math.cos(ang), yc + r * math.sin(ang)])

        elif cmd == "OE" and current_contour:
            if len(current_contour) >= 6:
                simplified = simplify_contour(current_contour, simplify_tol)
                delta_encoded = delta_encode_contour(simplified, 100)
                if delta_encoded:
                    surfaces.append(delta_encoded)
            current_contour = []

    return {
        "lines": lines,
        "arcs": arcs,
        "circles": circles,
        "rects": rects,
        "surfaces": surfaces,
        "texts": texts,
    }


def chunk_layer_geometry(l_data, chunk_size=350):
    """Partitions lines and arcs into spatial bounding box chunks for Main Thread Frustum Culling."""
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
        min_x = round(min(item[2] for item in sub), 2)
        min_y = round(min(item[3] for item in sub), 2)
        max_x = round(max(item[4] for item in sub), 2)
        max_y = round(max(item[5] for item in sub), 2)

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
            "b": [min_x, min_y, max_x, max_y],
            "lines": [{"w": w, "pts": pts} for w, pts in w_lines.items()],
            "arcs": [{"w": w, "arcs": a} for w, a in w_arcs.items()],
        })

    return {
        "chunks": chunks,
        "circles": l_data["circles"],
        "rects": l_data["rects"],
        "surfaces": l_data["surfaces"],
        "texts": l_data["texts"],
    }


def parse_eda_packages(eda_content):
    """Extracts package boundary dimensions, contours, and pin pads from eda/data."""
    packages = {}
    pkg_list = []
    if not eda_content:
        return packages, pkg_list

    scale, _ = get_unit_scale_to_mm(eda_content)
    current_pkg = None
    current_pin = None

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
                w = round(abs(xmax - xmin), 2)
                h = round(abs(ymax - ymin), 2)
                xc = round((xmin + xmax) / 2.0, 2)
                yc = round((ymin + ymax) / 2.0, 2)
            elif len(nums) >= 5:
                cand1 = [n * scale for n in nums[:4]]
                cand2 = [n * scale for n in nums[1:5]]
                if cand1[2] > cand1[0] and cand1[3] > cand1[1]:
                    xmin, ymin, xmax, ymax = cand1
                else:
                    xmin, ymin, xmax, ymax = cand2
                w = round(abs(xmax - xmin), 2)
                h = round(abs(ymax - ymin), 2)
                xc = round((xmin + xmax) / 2.0, 2)
                yc = round((ymin + ymax) / 2.0, 2)

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
            current_pin = None

        elif current_pkg is not None:
            if cmd == "PIN" and len(toks) >= 4:
                p_name = toks[1]
                nums = []
                for t in toks[2:]:
                    try:
                        nums.append(float(t))
                    except ValueError:
                        continue
                if len(nums) >= 2:
                    px = round(nums[0] * scale, 2)
                    py = round(nums[1] * scale, 2)
                    pw = 0.5
                    ph = 0.5
                    pin_obj = {
                        "n": p_name,
                        "x": px,
                        "y": py,
                        "w": pw,
                        "h": ph,
                    }
                    current_pkg["pins"].append(pin_obj)
                    current_pin = pin_obj

            elif cmd == "RC" and len(toks) >= 5:
                # RC directly following a PIN defines that PIN's pad bounds
                try:
                    rc_nums = [float(t) * scale for t in toks[1:5]]
                    dx = round(abs(rc_nums[2]), 2)
                    dy = round(abs(rc_nums[3]), 2)
                    if current_pin:
                        current_pin["w"] = dx
                        current_pin["h"] = dy
                    elif current_pkg["w"] <= 0.1:
                        current_pkg["w"] = dx
                        current_pkg["h"] = dy
                except Exception:
                    pass

            elif cmd == "BND" and len(toks) >= 5:
                try:
                    xmin = float(toks[1]) * scale
                    ymin = float(toks[2]) * scale
                    xmax = float(toks[3]) * scale
                    ymax = float(toks[4]) * scale
                    current_pkg["w"] = round(abs(xmax - xmin), 2)
                    current_pkg["h"] = round(abs(ymax - ymin), 2)
                except Exception:
                    pass

    # Automatically derive footprint boundaries from outer pin extents if missing or too small
    for pkg in pkg_list:
        if pkg["pins"]:
            xs = [p["x"] for p in pkg["pins"]]
            ys = [p["y"] for p in pkg["pins"]]
            pad_w_max = max(p["w"] for p in pkg["pins"])
            pad_h_max = max(p["h"] for p in pkg["pins"])
            span_x = round(max(xs) - min(xs) + pad_w_max + 0.35, 2)
            span_y = round(max(ys) - min(ys) + pad_h_max + 0.35, 2)
            if pkg["w"] <= 0.2 or span_x > pkg["w"]:
                pkg["w"] = span_x
                pkg["xc"] = round((min(xs) + max(xs)) / 2.0, 2)
            if pkg["h"] <= 0.2 or span_y > pkg["h"]:
                pkg["h"] = span_y
                pkg["yc"] = round((min(ys) + max(ys)) / 2.0, 2)

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
                is_tp = ref.upper().startswith("TP")

                if pkg and pkg["w"] > 0.1 and pkg["h"] > 0.1:
                    pw = pkg["w"]
                    ph = pkg["h"]
                    pins = pkg["pins"]
                else:
                    rf = ref.upper()
                    if is_tp:
                        pw, ph = 0.8, 0.8
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

                # Format pins for compact delivery: [n, x, y, w, h]
                formatted_pins = []
                for p in pins:
                    formatted_pins.append([p["n"], p["x"], p["y"], p["w"], p["h"]])

                # Check if component qualifies as Large IC (>= 3.5mm or U... / TR...)
                is_large_ic = not is_tp and (pw >= 3.5 or ph >= 3.5 or ref.upper().startswith("U") or ref.upper().startswith("TR"))

                # Compact array structure: [ref, x, y, w, h, rot, mir, side_code, part, pins, is_tp, is_large_ic]
                components.append([
                    ref,
                    round(x, 2),
                    round(y, 2),
                    round(pw, 2),
                    round(ph, 2),
                    round(rot, 1),
                    mir,
                    0 if side == "TOP" else 1,
                    part,
                    formatted_pins,
                    1 if is_tp else 0,
                    1 if is_large_ic else 0
                ])
            except Exception:
                continue

    return components


def parse_odb_profile(profile_text):
    """Parses profile outer island loops and cutouts, maintaining native circular arcs."""
    coord_scale, _ = get_unit_scale_to_mm(profile_text)
    loops = []
    current_loop = []

    for raw_line in profile_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        cmd = toks[0].upper()

        if cmd == "OB" and len(toks) >= 3:
            current_loop = [["M", round(float(toks[1]) * coord_scale, 2), round(float(toks[2]) * coord_scale, 2)]]
        elif cmd == "OS" and len(toks) >= 3 and current_loop:
            current_loop.append(["L", round(float(toks[1]) * coord_scale, 2), round(float(toks[2]) * coord_scale, 2)])
        elif cmd == "OC" and len(toks) >= 6 and current_loop:
            xe = float(toks[1]) * coord_scale
            ye = float(toks[2]) * coord_scale
            xc = float(toks[3]) * coord_scale
            yc = float(toks[4]) * coord_scale
            cw = 1 if toks[5].upper() in ("Y", "CW", "TRUE") else 0
            xs, ys = current_loop[-1][1], current_loop[-1][2]

            r = round((math.hypot(xs - xc, ys - yc) + math.hypot(xe - xc, ye - yc)) / 2.0, 3)
            a_start = round(math.atan2(ys - yc, xs - xc), 4)
            a_end = round(math.atan2(ye - yc, xe - xc), 4)
            current_loop.append(["A", round(xc, 2), round(yc, 2), r, a_start, a_end, cw])
        elif cmd == "OE" and current_loop:
            if len(current_loop) >= 3:
                loops.append(current_loop)
            current_loop = []

    return loops


def build_viewer_html(board_data):
    """Compiles the single-file, offline-ready HTML5 Canvas application with embedded Base64 payload."""
    json_bytes = json.dumps(board_data, separators=(",", ":")).encode("utf-8")
    compressed_b64 = base64.b64encode(gzip.compress(json_bytes, compresslevel=9)).decode("ascii")

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PCB Investigator - ODB++ HTML5 Interactive Viewer</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #06080a;
    color: #abb2bf;
    overflow: hidden;
    height: 100vh;
    display: flex;
    user-select: none;
  }}
  #canvas-container {{
    flex: 1;
    position: relative;
    height: 100%;
    cursor: default;
    background: #050709;
  }}
  #canvas-container.crosshair {{
    cursor: crosshair;
  }}
  canvas {{
    width: 100%;
    height: 100%;
    display: block;
  }}
  #sidebar {{
    width: 320px;
    background: #0d1015;
    border-left: 1px solid #1a202c;
    display: flex;
    flex-direction: column;
    z-index: 10;
    box-shadow: -4px 0 16px rgba(0, 0, 0, 0.5);
  }}
  .header {{
    padding: 14px 16px 10px;
    border-bottom: 1px solid #1a202c;
    background: #0f131a;
  }}
  .header h1 {{
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: #e5c07b;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .header h1 span.badge {{
    font-size: 9px;
    background: #285438;
    color: #98c379;
    padding: 2px 6px;
    border-radius: 3px;
    text-transform: uppercase;
  }}
  .header .info {{
    font-size: 11px;
    color: #5c6370;
    margin-top: 4px;
  }}
  .search-bar {{
    padding: 10px 12px;
    border-bottom: 1px solid #1a202c;
    background: #0b0e13;
    display: flex;
    gap: 6px;
  }}
  .search-bar input {{
    flex: 1;
    background: #151922;
    border: 1px solid #283141;
    color: #fff;
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
    outline: none;
    transition: border-color 0.2s;
  }}
  .search-bar input:focus {{
    border-color: #61afef;
    background: #181d28;
  }}
  .search-bar input::placeholder {{
    color: #4b5263;
  }}
  .controls-bar {{
    padding: 8px 12px;
    border-bottom: 1px solid #1a202c;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    background: #0b0e13;
  }}
  button {{
    background: #181e29;
    border: 1px solid #283141;
    color: #abb2bf;
    padding: 5px 10px;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.15s ease;
  }}
  button:hover {{
    background: #232c3d;
    color: #fff;
    border-color: #61afef;
  }}
  button.active {{
    background: #1e3a5f;
    border-color: #61afef;
    color: #61afef;
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
    margin: 12px 4px 6px;
    letter-spacing: 0.6px;
  }}
  .layer-row {{
    display: flex;
    align-items: center;
    padding: 6px 8px;
    border-radius: 4px;
    margin-bottom: 3px;
    background: #12161f;
    border: 1px solid transparent;
    transition: background 0.15s;
  }}
  .layer-row:hover {{
    background: #181e2a;
    border-color: #232b3b;
  }}
  .layer-row input[type="checkbox"] {{
    margin-right: 8px;
    accent-color: #61afef;
    cursor: pointer;
    width: 14px;
    height: 14px;
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
    font-size: 11px;
    color: #c8d1e0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .layer-side-badge {{
    font-size: 9px;
    padding: 1px 4px;
    border-radius: 3px;
    margin-right: 6px;
    font-weight: 600;
  }}
  .badge-top {{ background: #1c3d28; color: #98c379; }}
  .badge-bot {{ background: #1a2b42; color: #61afef; }}
  .badge-inner {{ background: #342847; color: #c678dd; }}
  .layer-count {{
    font-size: 10px;
    color: #5c6370;
    margin-left: 6px;
    font-family: monospace;
  }}
  #hud {{
    position: absolute;
    bottom: 14px;
    left: 14px;
    background: rgba(13, 16, 21, 0.94);
    border: 1px solid #232b3b;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    pointer-events: none;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.6);
    line-height: 1.5;
  }}
  #hud span.hl {{
    color: #61afef;
    font-weight: 600;
  }}
  #hud span.side {{
    color: #e5c07b;
    font-weight: 700;
  }}
  #measure-hud {{
    color: #98c379;
    display: none;
    margin-top: 4px;
    padding-top: 4px;
    border-top: 1px solid #1f2735;
    font-weight: 600;
  }}
  #loader {{
    position: absolute;
    inset: 0;
    background: #06080a;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: #e5c07b;
    font-size: 14px;
    z-index: 100;
    gap: 12px;
  }}
  .spinner {{
    width: 32px;
    height: 32px;
    border: 3px solid #1a202c;
    border-top-color: #61afef;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }}
  @keyframes spin {{
    to {{ transform: rotate(360deg); }}
  }}
</style>
</head>
<body>
  <div id="loader">
    <div class="spinner"></div>
    <div id="loader-text">Loading ODB++ Payload...</div>
  </div>

  <div id="canvas-container">
    <canvas id="pcbCanvas"></canvas>
    <div id="hud">
      Cursor: <span class="hl" id="pos-coords">X: 0.000 mm, Y: 0.000 mm</span> | 
      Board: <span class="hl" id="board-dims">--</span> | 
      View: <span class="side" id="view-side">TOP</span> | 
      Zoom: <span class="hl" id="zoom-level">100%</span>
      <div id="measure-hud">
        Measurement (Shift+Drag): <span id="measure-dist">0.000 mm</span>
      </div>
    </div>
  </div>

  <div id="sidebar">
    <div class="header">
      <h1>PCB Investigator <span class="badge">PRO</span></h1>
      <div class="info" id="step-info">ODB++ Step: --</div>
    </div>
    <div class="search-bar">
      <input type="text" id="comp-search" placeholder="Search RefDes (e.g. U5101, TR6500_LW, TP8051)...">
      <button id="btn-search-clear">Clear</button>
    </div>
    <div class="controls-bar">
      <button id="btn-fit" title="Fit to Board (F)">Fit (F)</button>
      <button id="btn-top" class="active" title="Top View">Top View</button>
      <button id="btn-bot" title="Bottom View">Bottom View</button>
      <button id="btn-mirror" title="Toggle Horizontal Mirror (M)">Mirror X (M)</button>
    </div>
    <div class="layer-list" id="layerList"></div>
  </div>

<script>
const payloadB64 = "{compressed_b64}";

let board = null;
const canvas = document.getElementById('pcbCanvas');
const container = document.getElementById('canvas-container');
const ctx = canvas.getContext('2d');
const posCoords = document.getElementById('pos-coords');
const boardDims = document.getElementById('board-dims');
const viewSide = document.getElementById('view-side');
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

// Background Web Worker Thread (Unblocks Main UI Thread)
const workerScript = `
self.onmessage = async function(e) {{
  try {{
    const b64 = e.data;
    const binStr = atob(b64);
    const len = binStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {{
      bytes[i] = binStr.charCodeAt(i);
    }}
    const ds = new DecompressionStream('gzip');
    const writer = ds.writable.getWriter();
    writer.write(bytes);
    writer.close();
    const data = await new Response(ds.readable).json();
    self.postMessage({{ status: 'success', data }});
  }} catch (err) {{
    self.postMessage({{ status: 'error', message: err.message }});
  }}
}};
`;

async function initData() {{
  document.getElementById('loader-text').textContent = 'Decompressing ODB++ in Worker Thread...';
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
  document.getElementById('loader-text').textContent = 'Decompressing ODB++ Stream...';
  try {{
    const binStr = atob(payloadB64);
    const len = binStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {{
      bytes[i] = binStr.charCodeAt(i);
    }}
    const ds = new DecompressionStream('gzip');
    const writer = ds.writable.getWriter();
    writer.write(bytes);
    writer.close();
    const data = await new Response(ds.readable).json();
    onDataReady(data);
  }} catch (err) {{
    document.getElementById('loader-text').textContent = 'Failed to load board payload: ' + err.message;
  }}
}}

function onDataReady(data) {{
  board = data;
  stepInfo.textContent = `Active Step: ${{board.step}} | Components: ${{board.components ? board.components.length : 0}}`;

  // Precompile Path2D for Board Boundary Profile (Outer island and Cutout holes)
  if (board.profile && board.profile.loops) {{
    const p = new Path2D();
    for (const loop of board.profile.loops) {{
      if (!loop || loop.length === 0) continue;
      for (const cmd of loop) {{
        const type = cmd[0];
        if (type === 'M') {{
          p.moveTo(cmd[1], cmd[2]);
        }} else if (type === 'L') {{
          p.lineTo(cmd[1], cmd[2]);
        }} else if (type === 'A') {{
          // ['A', xc, yc, r, a_start, a_end, cw]
          p.arc(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5], cmd[6] === 1);
        }}
      }}
      p.closePath();
    }}
    board.profile.path = p;
  }}

  // Precompile Path2D for each layer chunk and delta-encoded surfaces
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

    // Circular Gold Pads & Vias: flat [x, y, r, ...]
    if (layer.circles && layer.circles.length > 0) {{
      const p = new Path2D();
      const c = layer.circles;
      for (let i = 0; i < c.length; i += 3) {{
        p.moveTo(c[i] + c[i+2], c[i+1]);
        p.arc(c[i], c[i+1], c[i+2], 0, Math.PI * 2);
      }}
      layer.circlePath = p;
    }}

    // Delta-encoded Surface Pours & Silkscreen Polygons: [[lx, ly, dx1, dy1, ...], ...]
    if (layer.surfaces && layer.surfaces.length > 0) {{
      const p = new Path2D();
      for (const d of layer.surfaces) {{
        if (d.length < 4) continue;
        let x = d[0] / 100.0;
        let y = d[1] / 100.0;
        p.moveTo(x, y);
        for (let i = 2; i < d.length; i += 2) {{
          x += d[i] / 100.0;
          y += d[i+1] / 100.0;
          p.lineTo(x, y);
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

  const pad = 48 * window.devicePixelRatio;
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

  // 1. Substrate Body: Deep dark green/black core (#0a120c) with evenodd fill
  if (board.profile && board.profile.path) {{
    ctx.fillStyle = '#0a120c';
    ctx.fill(board.profile.path, 'evenodd');
  }}

  // 2. Hardware-Accelerated Vector Layers (PCB Investigator Palette)
  ctx.lineCap = 'square';
  ctx.lineJoin = 'miter';

  for (const layer of board.layers) {{
    if (!layer.visible) continue;
    // Side culling: immediately discard bottom layers when viewing Top, and vice-versa
    if ((layer.side === 'TOP' && mirrorX) || (layer.side === 'BOTTOM' && !mirrorX)) continue;

    ctx.strokeStyle = layer.color;
    ctx.fillStyle = layer.color;

    // Copper Surfaces & Ground Pours
    if (layer.surfacePath) {{
      const isCopper = layer.type === 'SIGNAL';
      ctx.globalAlpha = isCopper ? 0.72 : 0.95;
      ctx.fill(layer.surfacePath);
      ctx.globalAlpha = 1.0;
    }}

    // Spatial Chunk Culled Traces & Arcs
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

    // Rectangular Gold Pads: flat [x, y, w, h, ang, ...]
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

  // 3. Component Courtyards, Pads & Test Points
  // Component structure: [ref, x, y, w, h, rot, mir, side_code, part, pins, is_tp, is_large_ic]
  if (board.components && board.components.length > 0) {{
    for (const cmp of board.components) {{
      const cSide = cmp[7] === 0 ? 'TOP' : 'BOTTOM';
      // Side Culling
      if ((cSide === 'TOP' && mirrorX) || (cSide === 'BOTTOM' && !mirrorX)) continue;

      const cx = cmp[1], cy = cmp[2], cw = cmp[3], ch = cmp[4], crot = cmp[5];
      // Frustum Culling
      if (cx + cw < vMinX || cx - cw > vMaxX || cy + ch < vMinY || cy - ch > vMaxY) continue;

      const ref = cmp[0];
      const isTP = cmp[10] === 1;
      const isLargeIC = cmp[11] === 1;
      const isTarget = searchTarget && ref.toLowerCase() === searchTarget.toLowerCase();

      ctx.save();
      ctx.translate(cx, cy);

      if (isTP) {{
        // TEST POINTS (TP...): Strictly circles filled with light cyan (#e0f7fa), electric cyan border (#00e5ff)
        // No plus signs or crosshairs.
        if (compConfig.showTestpoints) {{
          const r = Math.max(cw, ch, 0.8) / 2.0;

          // Fill inside circle
          ctx.fillStyle = isTarget ? '#ff2222' : '#e0f7fa';
          ctx.beginPath();
          ctx.arc(0, 0, r, 0, Math.PI * 2);
          ctx.fill();

          // Border outline
          ctx.strokeStyle = isTarget ? '#ffffff' : compConfig.colorTP;
          ctx.lineWidth = Math.max(isTarget ? 0.22 : 0.12, 1.2 / scale);
          ctx.stroke();

          // Centered Test Point RefDes inside circle (LOD threshold: diameter >= 16 screen px)
          const screenD = r * 2 * scale;
          if (screenD >= 16 || isTarget) {{
            ctx.save();
            ctx.scale(mirrorX ? -1 : 1, -1);
            const maxFontPx = Math.min((screenD * 0.82) / (ref.length * 0.6), screenD * 0.38);
            if (maxFontPx >= 7.0 || isTarget) {{
              ctx.font = `bold ${{Math.max(Math.min(maxFontPx, 16), 7.0)}}px monospace`;
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              ctx.fillStyle = isTarget ? '#ffffff' : '#00363a';
              ctx.fillText(ref, 0, 0);
            }}
            ctx.restore();
          }}
        }}
      }} else if (compConfig.showPackages) {{
        // SMD COMPONENT COURTYARD & PINS
        ctx.rotate(crot * (Math.PI / 180));

        // Rectangular Courtyard Outline in Electric Cyan (#00e5ff)
        ctx.strokeStyle = isTarget ? '#ff2222' : compConfig.colorPkg;
        ctx.lineWidth = Math.max(isTarget ? 0.25 : 0.12, 1.2 / scale);
        ctx.strokeRect(-cw / 2, -ch / 2, cw, ch);

        const pins = cmp[9];
        if (pins && pins.length > 0) {{
          // Exact SMT Pads: Pin 1 distinctly in Bright Red (#ff2222); Pin 2+ in Cyan (#00e5ff)
          for (let pIdx = 0; pIdx < pins.length; pIdx++) {{
            const pin = pins[pIdx];
            const pName = String(pin[0]).toLowerCase();
            const isPin1 = (pName === '1' || pName === 'a' || pName === '+' || pIdx === 0);
            const px = pin[1], py = pin[2], pw = pin[3], ph = pin[4];

            ctx.fillStyle = isPin1 ? 'rgba(255, 34, 34, 0.85)' : 'rgba(0, 229, 255, 0.45)';
            ctx.strokeStyle = isPin1 ? '#ff2222' : '#00e5ff';
            ctx.lineWidth = Math.max(0.08, 0.8 / scale);
            ctx.fillRect(px - pw / 2, py - ph / 2, pw, ph);
            ctx.strokeRect(px - pw / 2, py - ph / 2, pw, ph);
          }}
        }} else {{
          // Synthesized pads for 2-pin passives: Pin 1 in Red (#ff2222), Pin 2 in Cyan (#00e5ff)
          const pw = cw >= ch ? Math.min(cw * 0.28, 1.2) : cw * 0.8;
          const ph = cw >= ch ? ch * 0.8 : Math.min(ch * 0.28, 1.2);
          ctx.lineWidth = Math.max(0.08, 0.8 / scale);

          if (cw >= ch) {{
            ctx.fillStyle = 'rgba(255, 34, 34, 0.85)';
            ctx.strokeStyle = '#ff2222';
            ctx.fillRect(-cw / 2 + 0.05, -ph / 2, pw, ph);
            ctx.strokeRect(-cw / 2 + 0.05, -ph / 2, pw, ph);

            ctx.fillStyle = 'rgba(0, 229, 255, 0.45)';
            ctx.strokeStyle = '#00e5ff';
            ctx.fillRect(cw / 2 - pw - 0.05, -ph / 2, pw, ph);
            ctx.strokeRect(cw / 2 - pw - 0.05, -ph / 2, pw, ph);
          }} else {{
            ctx.fillStyle = 'rgba(255, 34, 34, 0.85)';
            ctx.strokeStyle = '#ff2222';
            ctx.fillRect(-pw / 2, ch / 2 - ph - 0.05, pw, ph);
            ctx.strokeRect(-pw / 2, ch / 2 - ph - 0.05, pw, ph);

            ctx.fillStyle = 'rgba(0, 229, 255, 0.45)';
            ctx.strokeStyle = '#00e5ff';
            ctx.fillRect(-pw / 2, -ch / 2 + 0.05, pw, ph);
            ctx.strokeRect(-pw / 2, -ch / 2 + 0.05, pw, ph);
          }}
        }}

        // Bold Orange RefDes (#ff9900) centered inside large IC bodies (>= 3.5mm, U..., TR...)
        // Rotates along component primary axis and flips automatically to remain right-side up.
        // Generic centered RefDes suppressed on small passives to prevent overlapping silkscreen.
        if (isLargeIC || isTarget) {{
          const isWide = cw >= ch;
          let textAngle = isWide ? 0 : Math.PI / 2;
          let totalWorldAngle = crot * (Math.PI / 180) + textAngle;
          // Normalize to [0, 2*PI]
          totalWorldAngle = ((totalWorldAngle % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
          // Auto-flip if upside down in screen coordinates
          const flip = (totalWorldAngle > Math.PI / 2 && totalWorldAngle < 3 * Math.PI / 2);

          const maxDim = isWide ? cw : ch;
          const minDim = isWide ? ch : cw;
          const onScreenDim = minDim * scale;

          if (onScreenDim >= 14 || isTarget) {{
            ctx.save();
            ctx.rotate(textAngle);
            if (flip) ctx.rotate(Math.PI);
            ctx.scale(mirrorX ? -1 : 1, -1);

            const fontMm = Math.min((maxDim * 0.75) / (ref.length * 0.58), minDim * 0.55);
            const fontPx = Math.max(fontMm * scale, 9);
            ctx.font = `700 ${{Math.min(fontPx, 22)}}px -apple-system, BlinkMacSystemFont, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = isTarget ? '#ff2222' : '#ff9900';
            ctx.fillText(ref, 0, 0);
            ctx.restore();
          }}
        }}
      }}

      // Search Highlight Indicator Ring
      if (isTarget) {{
        ctx.save();
        ctx.strokeStyle = '#ff2222';
        ctx.lineWidth = Math.max(0.3, 2.5 / scale);
        ctx.beginPath();
        const markR = Math.max(cw, ch) * 0.85;
        ctx.arc(0, 0, markR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }}

      ctx.restore();
    }}
  }}

  // 4. Board Boundary Edge Outline
  if (board.profile && board.profile.visible && board.profile.path) {{
    ctx.strokeStyle = board.profile.color;
    ctx.lineWidth = Math.max(0.25, 1.5 / scale);
    ctx.stroke(board.profile.path);
  }}

  // 5. Silkscreen Text Layer (Physical Top/Bottom Overlay in Amber Gold #d9a738)
  ctx.restore();
  for (const layer of board.layers) {{
    if (!layer.visible || !layer.texts || layer.texts.length === 0) continue;
    if ((layer.side === 'TOP' && mirrorX) || (layer.side === 'BOTTOM' && !mirrorX)) continue;
    ctx.fillStyle = layer.color;

    for (const t of layer.texts) {{
      const x = t[0], y = t[1], text = t[2], h = t[3], rot = t[4], mir = t[5];
      if (x < vMinX || x > vMaxX || y < vMinY || y > vMaxY) continue;
      // LOD threshold: suppress silkscreen text when on-screen font height < 4.0 pixels
      if (h * scale < 4.0) continue;

      const [sx, sy] = toScreen(x, y);
      ctx.save();
      ctx.translate(sx, sy);

      let ang = -rot * (Math.PI / 180);
      if (mirrorX) ang = Math.PI - ang;
      ctx.rotate(ang);
      if (mir) ctx.scale(-1, 1);

      const FONT_RES = 64;
      ctx.scale(scale / FONT_RES, scale / FONT_RES);
      ctx.font = `600 ${{Math.round(h * FONT_RES)}}px -apple-system, BlinkMacSystemFont, sans-serif`;
      ctx.textBaseline = 'bottom';
      ctx.fillText(text, 0, 0);
      ctx.restore();
    }}
  }}

  // 6. Shift + Measure Tool Caliper Overlay
  if (measureP1 && measureP2) {{
    const [p1x, p1y] = toScreen(measureP1[0], measureP1[1]);
    const [p2x, p2y] = toScreen(measureP2[0], measureP2[1]);

    ctx.save();
    ctx.strokeStyle = '#98c379';
    ctx.lineWidth = 2.0;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(p1x, p1y);
    ctx.lineTo(p2x, p2y);
    ctx.stroke();

    ctx.fillStyle = '#98c379';
    ctx.beginPath();
    ctx.arc(p1x, p1y, 4.5, 0, Math.PI * 2);
    ctx.arc(p2x, p2y, 4.5, 0, Math.PI * 2);
    ctx.fill();

    // Measurement badge at midpoint
    const midX = (p1x + p2x) / 2;
    const midY = (p1y + p2y) / 2;
    const distMm = Math.hypot(measureP2[0] - measureP1[0], measureP2[1] - measureP1[1]);
    const distText = `${{distMm.toFixed(3)}} mm`;

    ctx.font = 'bold 11px monospace';
    const textWidth = ctx.measureText(distText).width;
    ctx.fillStyle = 'rgba(13, 16, 21, 0.9)';
    ctx.fillRect(midX - textWidth / 2 - 5, midY - 18, textWidth + 10, 18);
    ctx.strokeStyle = '#98c379';
    ctx.strokeRect(midX - textWidth / 2 - 5, midY - 18, textWidth + 10, 18);
    ctx.fillStyle = '#98c379';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(distText, midX, midY - 9);

    ctx.restore();
  }}

  zoomLevel.textContent = `${{Math.round((scale / window.devicePixelRatio) * 100)}}%`;
  viewSide.textContent = mirrorX ? 'BOTTOM (Mirrored)' : 'TOP';
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
      <span class="layer-count">${{board.profile.loops ? board.profile.loops.length : 1}}</span>
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
  compSec.textContent = 'Components & Test Points';
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
    <span class="layer-label">Test Points (TP...)</span>
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
  laySec.textContent = 'Physical PCB Layers';
  list.appendChild(laySec);

  board.layers.forEach((layer, idx) => {{
    const row = document.createElement('div');
    row.className = 'layer-row';
    const sideClass = layer.side === 'TOP' ? 'badge-top' : (layer.side === 'BOTTOM' ? 'badge-bot' : 'badge-inner');
    row.innerHTML = `
      <input type="checkbox" id="chk-${{idx}}" ${{layer.visible ? 'checked' : ''}}>
      <input type="color" id="col-${{idx}}" value="${{layer.color}}">
      <span class="layer-side-badge ${{sideClass}}">${{layer.side}}</span>
      <span class="layer-label" title="${{layer.name}}">${{layer.label || layer.name}}</span>
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

// Top / Bottom View Controls
const btnTop = document.getElementById('btn-top');
const btnBot = document.getElementById('btn-bot');

btnTop.addEventListener('click', () => {{
  mirrorX = false;
  btnTop.classList.add('active');
  btnBot.classList.remove('active');
  board.layers.forEach(l => {{
    if (l.side === 'TOP') {{
      l.visible = (l.type === 'SIGNAL' || l.type === 'SOLDER_MASK' || l.type === 'SILK_SCREEN');
    }} else if (l.side === 'BOTTOM' || l.side === 'INNER') {{
      l.visible = false;
    }}
  }});
  buildLayerList();
  fitBoard();
}});

btnBot.addEventListener('click', () => {{
  mirrorX = true;
  btnBot.classList.add('active');
  btnTop.classList.remove('active');
  board.layers.forEach(l => {{
    if (l.side === 'BOTTOM') {{
      l.visible = (l.type === 'SIGNAL' || l.type === 'SOLDER_MASK' || l.type === 'SILK_SCREEN');
    }} else if (l.side === 'TOP' || l.side === 'INNER') {{
      l.visible = false;
    }}
  }});
  buildLayerList();
  fitBoard();
}});

// RefDes Search & Instant Focus
compSearch.addEventListener('input', e => {{
  const query = e.target.value.trim();
  if (query && board.components) {{
    let match = board.components.find(c => c[0].toLowerCase() === query.toLowerCase());
    if (!match && query.length >= 2) {{
      match = board.components.find(c => c[0].toLowerCase().startsWith(query.toLowerCase()));
    }}
    if (match) {{
      searchTarget = match[0];
      const matchSide = match[7] === 0 ? 'TOP' : 'BOTTOM';
      if ((matchSide === 'BOTTOM' && !mirrorX) || (matchSide === 'TOP' && mirrorX)) {{
        mirrorX = (matchSide === 'BOTTOM');
        if (mirrorX) {{
          btnBot.classList.add('active');
          btnTop.classList.remove('active');
        }} else {{
          btnTop.classList.add('active');
          btnBot.classList.remove('active');
        }}
        buildLayerList();
      }}

      // Center smoothly on target
      const targetScale = Math.max(scale, 18.0 * window.devicePixelRatio);
      scale = targetScale;
      panX = canvas.width / 2 - (mirrorX ? -match[1] : match[1]) * scale;
      panY = canvas.height / 2 + match[2] * scale;
    }} else {{
      searchTarget = query;
    }}
  }} else {{
    searchTarget = null;
  }}
  scheduleRender();
}});

document.getElementById('btn-search-clear').addEventListener('click', () => {{
  compSearch.value = '';
  searchTarget = null;
  scheduleRender();
}});

// Canvas Interactions: Pan, Zoom & Caliper Measurement Tool
canvas.addEventListener('mousedown', e => {{
  const mx = e.clientX * window.devicePixelRatio;
  const my = e.clientY * window.devicePixelRatio;

  if (e.shiftKey) {{
    isMeasuring = true;
    measureP1 = toWorld(mx, my);
    measureP2 = measureP1;
    measureHud.style.display = 'block';
    container.classList.add('crosshair');
    scheduleRender();
  }} else {{
    isDragging = true;
    dragStartX = mx - panX;
    dragStartY = my - panY;
    if (measureP1) {{
      measureP1 = null;
      measureP2 = null;
      measureHud.style.display = 'none';
      scheduleRender();
    }}
  }}
}});

window.addEventListener('mouseup', () => {{
  isDragging = false;
  if (isMeasuring) {{
    isMeasuring = false;
    container.classList.remove('crosshair');
  }}
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
    const dx = Math.abs(measureP2[0] - measureP1[0]);
    const dy = Math.abs(measureP2[1] - measureP1[1]);
    const dist = Math.hypot(dx, dy);
    measureDist.textContent = `dX: ${{dx.toFixed(3)}} mm, dY: ${{dy.toFixed(3)}} mm | Dist: ${{dist.toFixed(3)}} mm`;
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
  if (mirrorX) {{
    btnBot.classList.add('active');
    btnTop.classList.remove('active');
  }} else {{
    btnTop.classList.add('active');
    btnBot.classList.remove('active');
  }}
  fitBoard();
}});

window.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'f' || e.key === 'F') fitBoard();
  if (e.key === 'm' || e.key === 'M') {{
    mirrorX = !mirrorX;
    if (mirrorX) {{
      btnBot.classList.add('active');
      btnTop.classList.remove('active');
    }} else {{
      btnTop.classList.add('active');
      btnBot.classList.remove('active');
    }}
    fitBoard();
  }}
  if (e.key === 'Escape') {{
    measureP1 = null;
    measureP2 = null;
    measureHud.style.display = 'none';
    searchTarget = null;
    compSearch.value = '';
    scheduleRender();
  }}
  if (e.key === 'Shift') {{
    container.classList.add('crosshair');
  }}
}});

window.addEventListener('keyup', e => {{
  if (e.key === 'Shift' && !isMeasuring) {{
    container.classList.remove('crosshair');
  }}
}});

initData();
</script>
</body>
</html>"""
    return html_template


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous ODB++ to PCB Investigator-Grade HTML5 Standalone Viewer Compiler."
    )
    parser.add_argument("odb_input", help="Path to ODB++ .zip, .tgz, .tar.gz, or extracted directory")
    parser.add_argument("-o", "--output", default="pcb_viewer.html", help="Output HTML file path")
    parser.add_argument("-s", "--step", default=None, help="Target step name (defaults to primary step)")
    parser.add_argument("--include-inner", action="store_true", help="Include inner copper signal layers")
    args = parser.parse_args()

    if not os.path.exists(args.odb_input):
        print(f"Error: Input source '{args.odb_input}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # 1. Step Detection & Source Resolution
    primary_step, available_steps = get_available_steps(args.odb_input)
    step = args.step if args.step else primary_step
    print(f"Detected ODB++ Steps: {available_steps} -> Active: '{step}'")

    # 2. Parse Matrix Stackup
    matrix_content, _ = read_file_from_source(args.odb_input, "matrix/matrix")
    matrix_meta = parse_matrix_file(matrix_content)

    # 3. Parse Board Profile (Outline & Cutouts)
    profile_content, _ = read_file_from_source(args.odb_input, "profile", preferred_step=step)
    if not profile_content:
        profile_content, _ = read_file_from_source(args.odb_input, "layers/outline/features", preferred_step=step)
    outline_loops = parse_odb_profile(profile_content) if profile_content else []

    # Compute Board Extents Bounding Box
    min_x, min_y, max_x, max_y = float("inf"), float("inf"), float("-inf"), float("-inf")
    for loop in outline_loops:
        for cmd in loop:
            # cmd is ['M', x, y], ['L', x, y], or ['A', xc, yc, r, ...]
            if cmd[0] in ("M", "L"):
                min_x = min(min_x, cmd[1])
                max_x = max(max_x, cmd[1])
                min_y = min(min_y, cmd[2])
                max_y = max(max_y, cmd[2])
            elif cmd[0] == "A":
                min_x = min(min_x, cmd[1] - cmd[3])
                max_x = max(max_x, cmd[1] + cmd[3])
                min_y = min(min_y, cmd[2] - cmd[3])
                max_y = max(max_y, cmd[2] + cmd[3])

    # 4. Parse EDA Footprint Packages and Placement Definitions
    eda_content, _ = read_file_from_source(args.odb_input, "eda/data", preferred_step=step)
    packages, pkg_list = parse_eda_packages(eda_content)

    comp_top_raw, _ = read_file_from_source(args.odb_input, "comp_+_top/components", preferred_step=step)
    comp_bot_raw, _ = read_file_from_source(args.odb_input, "comp_+_bot/components", preferred_step=step)
    components = (
        parse_components_with_packages(comp_top_raw, packages, pkg_list, side="TOP") +
        parse_components_with_packages(comp_bot_raw, packages, pkg_list, side="BOTTOM")
    )
    print(f"Extracted {len(components)} component footprints with pin geometries.")

    # 5. Discover Layer Features for the Target Step
    norm_step = f"steps/{step.lower()}/layers/"
    avail_layers = []
    if os.path.isdir(args.odb_input):
        step_dir = os.path.join(args.odb_input, "steps", step, "layers")
        if os.path.exists(step_dir):
            avail_layers = [d for d in os.listdir(step_dir) if os.path.isdir(os.path.join(step_dir, d))]
    elif zipfile.is_zipfile(args.odb_input):
        with zipfile.ZipFile(args.odb_input, "r") as z:
            for name in z.namelist():
                nl = name.replace("\\", "/").lower()
                if nl.endswith("/features") and norm_step in nl:
                    parts = nl.split(norm_step)[1].split("/")
                    if len(parts) >= 2:
                        avail_layers.append(parts[0])
    elif tarfile.is_tarfile(args.odb_input):
        with tarfile.open(args.odb_input, "r:*") as t:
            for member in t.getmembers():
                nl = member.name.replace("\\", "/").lower()
                if nl.endswith("/features") and norm_step in nl:
                    parts = nl.split(norm_step)[1].split("/")
                    if len(parts) >= 2:
                        avail_layers.append(parts[0])

    avail_layers = sorted(list(set(avail_layers)))

    parsed_layers = []

    # Redundant CAD/CAM composite & drawing frame layers to suppress
    suppress_layers = {
        "all", "frame", "frame_mirrored", "header", "dimension", "paneloutline",
        "title_block", "border", "paste_mask", "drawing", "notes"
    }

    for l_name in avail_layers:
        nl = l_name.lower()
        if nl in suppress_layers:
            continue

        meta = matrix_meta.get(nl, {})
        side = meta.get("COMPUTED_SIDE", meta.get("SIDE", ""))
        l_type = meta.get("TYPE", "")

        # Fallback side determination
        if not side:
            if any(k in nl for k in ("top", "_tpm", "_tsm", "assembly_top")):
                side = "TOP"
            elif any(k in nl for k in ("bottom", "bot", "_bpm", "_bsm", "assembly_bottom")):
                side = "BOTTOM"
            elif any(k in nl for k in ("mid", "inner")):
                side = "INNER"
            else:
                side = "TOP"

        # Fallback type determination
        if not l_type:
            if "silk" in nl or "overlay" in nl or "assembly" in nl:
                l_type = "SILK_SCREEN"
            elif "mask" in nl or "solder" in nl:
                l_type = "SOLDER_MASK"
            elif "drill" in nl or "rout" in nl:
                l_type = "DRILL"
            elif "paste" in nl:
                l_type = "SOLDER_PASTE"
            elif "layer" in nl or "signal" in nl or "top" in nl or "bottom" in nl:
                l_type = "SIGNAL"
            else:
                l_type = "DOCUMENT"

        # Skip document / mechanical layers
        if l_type in ("DOCUMENT", "SOLDER_PASTE"):
            continue

        # Skip inner layers unless explicitly requested
        if side == "INNER" and not args.include_inner:
            continue

        features_data, _ = read_file_from_source(args.odb_input, f"layers/{l_name}/features", preferred_step=step)
        if not features_data:
            continue

        # Strictly suppress T records on copper signal layers, extract only on verified silkscreen
        is_silkscreen = (l_type == "SILK_SCREEN")
        l_data = parse_layer_features(features_data, is_silkscreen=is_silkscreen)

        item_count = (
            len(l_data["lines"]) +
            len(l_data["arcs"]) +
            len(l_data["circles"]) // 3 +
            len(l_data["rects"]) // 5 +
            len(l_data["surfaces"])
        )
        if item_count == 0 and len(l_data["texts"]) == 0:
            continue

        # Visual Palette Matching PCB Investigator
        is_visible = False
        color = "#808080"
        order = 50

        if l_type == "SIGNAL" and side == "TOP":
            color = "#29663c"  # Top Copper Traces & Ground Pours (Emerald Green)
            is_visible = True
            order = 30
        elif l_type == "SIGNAL" and side == "BOTTOM":
            color = "#1e3a5f"  # Bottom Copper Traces & Pours (Slate Navy)
            is_visible = False
            order = 10
        elif l_type == "SIGNAL" and side == "INNER":
            color = "#5c4033"  # Inner Copper
            is_visible = False
            order = 20
        elif l_type == "SOLDER_MASK" and side == "TOP":
            color = "#c49c3e"  # Top Gold / ENIG SMT Pads & Vias
            is_visible = True
            order = 35
        elif l_type == "SOLDER_MASK" and side == "BOTTOM":
            color = "#c49c3e"  # Bottom Gold / ENIG SMT Pads
            is_visible = False
            order = 15
        elif l_type == "SILK_SCREEN" and side == "TOP":
            color = "#d9a738"  # Top Silkscreen (Amber Gold)
            is_visible = True
            order = 60
        elif l_type == "SILK_SCREEN" and side == "BOTTOM":
            color = "#8fa370"  # Bottom Silkscreen
            is_visible = False
            order = 25
        elif l_type == "DRILL":
            color = "#bfa45a"
            is_visible = False
            order = 80

        chunked = chunk_layer_geometry(l_data)

        # Update bounding box from layer lines if outline is missing
        if min_x == float("inf"):
            for l in l_data["lines"]:
                min_x = min(min_x, l[0], l[2])
                max_x = max(max_x, l[0], l[2])
                min_y = min(min_y, l[1], l[3])
                max_y = max(max_y, l[1], l[3])

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
        "bbox": [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)],
        "profile": {
            "color": "#e5c07b",
            "visible": True,
            "loops": outline_loops,
        },
        "components": components,
        "layers": parsed_layers,
    }

    html_out = build_viewer_html(board_payload)
    output_path = os.path.abspath(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Generated standalone viewer: {output_path} ({size_mb:.2f} MB)")
    if size_mb > 2.5:
        print(f"WARNING: Output file size ({size_mb:.2f} MB) exceeds 2.5 MB limit!", file=sys.stderr)
    else:
        print(f"SUCCESS: Output file size ({size_mb:.2f} MB) is well under the 2.5 MB gate.")


if __name__ == "__main__":
    main()
