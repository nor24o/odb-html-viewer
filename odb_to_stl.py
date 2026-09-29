import argparse
import io
import math
import os
import re
import sys
import tarfile
import zipfile
import numpy as np
import shapely.geometry as sg
import trimesh


# --- Built-in Fallback Triangulator (Zero Dependencies) ---
def _is_point_in_triangle(pt, a, b, c):
    def cross_2d(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[1] - p3[0]) * (p1[1] - p3[1])

    d1 = cross_2d(pt, a, b)
    d2 = cross_2d(pt, b, c)
    d3 = cross_2d(pt, c, a)
    has_neg = (d1 < -1e-7) or (d2 < -1e-7) or (d3 < -1e-7)
    has_pos = (d1 > 1e-7) or (d2 > 1e-7) or (d3 > 1e-7)
    return not (has_neg and has_pos)


def earclip_polygon(vertices):
    """Triangulates a simple 2D polygon using ear clipping."""
    n = len(vertices)
    if n < 3:
        return []

    # Calculate signed area to enforce CCW winding
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]

    idx_list = list(range(n))
    if area < 0:
        idx_list.reverse()

    triangles = []
    attempts = 0
    max_attempts = len(idx_list) * 3

    while len(idx_list) > 3 and attempts < max_attempts:
        ear_found = False
        m = len(idx_list)
        for i in range(m):
            prev_idx = idx_list[(i - 1) % m]
            curr_idx = idx_list[i]
            next_idx = idx_list[(i + 1) % m]

            a = vertices[prev_idx]
            b = vertices[curr_idx]
            c = vertices[next_idx]

            # Convex check
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-9:
                continue

            # Check if any other vertex lies inside this ear candidate
            contains_other = False
            for j in range(m):
                test_idx = idx_list[j]
                if test_idx in (prev_idx, curr_idx, next_idx):
                    continue
                if _is_point_in_triangle(vertices[test_idx], a, b, c):
                    contains_other = True
                    break

            if not contains_other:
                triangles.append((prev_idx, curr_idx, next_idx))
                idx_list.pop(i)
                ear_found = True
                break

        if not ear_found:
            attempts += 1

    if len(idx_list) == 3:
        triangles.append((idx_list[0], idx_list[1], idx_list[2]))

    return triangles


def manual_extrude_polygon(poly_coords, thickness_mm):
    """Extrudes 2D boundary coordinates into a 3D manifold box/prism."""
    v2d = np.array(poly_coords, dtype=np.float64)
    n = len(v2d)

    # 3D vertices: bottom ring (Z=0) then top ring (Z=thickness)
    bot_v3d = np.column_stack([v2d, np.zeros(n)])
    top_v3d = np.column_stack([v2d, np.full(n, thickness_mm)])
    vertices = np.vstack([bot_v3d, top_v3d])

    faces = []
    # Bottom (-Z) and Top (+Z) faces
    tris_2d = earclip_polygon(v2d)
    for i0, i1, i2 in tris_2d:
        faces.append([i2, i1, i0])  # Bottom: inward normal reversed
        faces.append([i0 + n, i1 + n, i2 + n])  # Top

    # Side walls
    for i in range(n):
        next_i = (i + 1) % n
        faces.append([i, next_i, next_i + n])
        faces.append([i, next_i + n, i + n])

    return trimesh.Trimesh(vertices=vertices, faces=np.array(faces), process=True)


# --- ODB++ Archive Processing ---
def read_file_from_source(source, target_suffix, preferred_step=None):
    """Finds and reads a file matching target suffix, prioritizing preferred_step if provided."""
    norm_target = target_suffix.replace("\\", "/").lower()
    matches = []

    if os.path.isdir(source):
        for root, _, files in os.walk(source):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, source).replace("\\", "/").lower()
                if rel_path.endswith(norm_target):
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as fp:
                        matches.append((fp.read(), rel_path))

    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source, "r") as z:
            for name in z.namelist():
                rel_path = name.replace("\\", "/").lower()
                if rel_path.endswith(norm_target):
                    with z.open(name) as fp:
                        matches.append((fp.read().decode("utf-8", errors="ignore"), rel_path))

    elif tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as t:
            for member in t.getmembers():
                rel_path = member.name.replace("\\", "/").lower()
                if rel_path.endswith(norm_target):
                    f = t.extractfile(member)
                    if f:
                        matches.append((f.read().decode("utf-8", errors="ignore"), rel_path))

    if not matches:
        return None, None

    if preferred_step:
        step_needle = f"steps/{preferred_step.lower()}/"
        for content, path in matches:
            if step_needle in path:
                return content, path

    return matches[0]


def get_unit_scale_to_mm(text, default_unit="INCH"):
    unit_match = re.search(r"UNITS\s*=\s*(\w+)", text, re.IGNORECASE)
    unit = unit_match.group(1).upper() if unit_match else default_unit.upper()
    if "INCH" in unit:
        return 25.4
    elif "MIL" in unit:
        return 0.0254
    elif "MICRON" in unit:
        return 0.001
    return 1.0


def parse_stackup_thickness(matrix_content):
    if not matrix_content:
        return None
    scale = get_unit_scale_to_mm(matrix_content, default_unit="INCH")
    thickness_matches = re.findall(r"THICKNESS\s*=\s*([\d\.]+)", matrix_content, re.IGNORECASE)
    if thickness_matches:
        return sum(float(val) for val in thickness_matches) * scale
    return None


def interpolate_arc(p_start, p_end, p_center, cw, max_step_deg=5.0):
    xs, ys = p_start
    xe, ye = p_end
    xc, yc = p_center

    r = (math.hypot(xs - xc, ys - yc) + math.hypot(xe - xc, ye - yc)) / 2.0
    a_start = math.atan2(ys - yc, xs - xc)
    a_end = math.atan2(ye - yc, xe - xc)

    if cw and a_end >= a_start:
        a_end -= 2.0 * math.pi
    elif not cw and a_end <= a_start:
        a_end += 2.0 * math.pi

    steps = max(4, int(math.ceil(math.degrees(abs(a_end - a_start)) / max_step_deg)))
    return [
        (xc + r * math.cos(a_start + (i / steps) * (a_end - a_start)),
         yc + r * math.sin(a_start + (i / steps) * (a_end - a_start)))
        for i in range(1, steps + 1)
    ]


def parse_odb_profile(profile_text):
    scale = get_unit_scale_to_mm(profile_text, default_unit="INCH")
    islands, cutouts = [], []
    current_loop = []
    loop_type = "I"

    for raw_line in profile_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        tokens = line.split()
        cmd = tokens[0].upper()

        if cmd == "OB":
            x, y = float(tokens[1]) * scale, float(tokens[2]) * scale
            loop_type = tokens[3].upper() if len(tokens) > 3 else "I"
            current_loop = [(x, y)]

        elif cmd == "OS":
            x, y = float(tokens[1]) * scale, float(tokens[2]) * scale
            if current_loop:
                current_loop.append((x, y))

        elif cmd == "OC":
            xe, ye = float(tokens[1]) * scale, float(tokens[2]) * scale
            xc, yc = float(tokens[3]) * scale, float(tokens[4]) * scale
            cw = tokens[5].upper() in ("Y", "CW", "TRUE")
            if current_loop:
                current_loop.extend(interpolate_arc(current_loop[-1], (xe, ye), (xc, yc), cw))

        elif cmd == "OE":
            if len(current_loop) >= 3:
                if np.allclose(current_loop[0], current_loop[-1], atol=1e-5):
                    current_loop.pop()
                if loop_type == "H":
                    cutouts.append(current_loop)
                else:
                    islands.append(current_loop)
            current_loop = []

    return islands, cutouts


def build_solid_mesh(islands, cutouts, thickness_mm):
    meshes = []
    for isl in islands:
        # Try mapbox-earcut / trimesh native extrusion first
        try:
            poly = sg.Polygon(isl)
            if not poly.is_valid:
                poly = poly.buffer(0)
            extruded = trimesh.creation.extrude_polygon(poly, height=thickness_mm)
            meshes.append(extruded)
        except Exception:
            # Fallback to the built-in ear-clipping triangulator
            extruded = manual_extrude_polygon(isl, thickness_mm)
            meshes.append(extruded)

    if not meshes:
        raise ValueError("No valid board boundary contour found to extrude.")

    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]


def main():
    parser = argparse.ArgumentParser(description="Extract PCB outline to 3D STL from ODB++.")
    parser.add_argument("odb_input", help="Path to ODB++ .zip, .tgz, or folder")
    parser.add_argument("-o", "--output", default="pcb_board.stl", help="Output STL filename")
    parser.add_argument("-s", "--step", default=None, help="Target step name (e.g. board_0_0 or 1976156-03)")
    parser.add_argument("-t", "--thickness", type=float, default=None, help="Board thickness in mm")
    args = parser.parse_args()

    profile_data, path_used = read_file_from_source(args.odb_input, "profile", preferred_step=args.step)
    if not profile_data:
        profile_data, path_used = read_file_from_source(args.odb_input, "layers/outline/features", preferred_step=args.step)

    if not profile_data:
        sys.exit("Error: Could not locate a profile or outline feature file.")

    print(f"Loaded outline geometry from: {path_used}")
    islands, cutouts = parse_odb_profile(profile_data)
    print(f"Extracted {len(islands)} outer boundary contour(s) and {len(cutouts)} cutout(s).")

    thickness_mm = args.thickness
    if thickness_mm is None:
        matrix_data, _ = read_file_from_source(args.odb_input, "matrix/matrix")
        detected_th = parse_stackup_thickness(matrix_data)
        thickness_mm = detected_th if (detected_th and detected_th > 0) else 1.6
        print(f"Using board thickness: {thickness_mm:.3f} mm")
    else:
        print(f"Using manual thickness: {thickness_mm:.3f} mm")

    mesh = build_solid_mesh(islands, cutouts, thickness_mm)
    mesh.export(args.output)
    print(f"Successfully generated: {args.output}")


if __name__ == "__main__":
    main()