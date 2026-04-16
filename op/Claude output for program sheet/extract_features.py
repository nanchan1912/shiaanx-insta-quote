"""
CAD Feature Extractor using PythonOCC
Extracts comprehensive geometric and topological features from a STEP file.
"""

import sys
import os
import json
import math
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.TopExp import TopExp_Explorer, topexp
from OCC.Core.TopAbs import (
    TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_WIRE,
    TopAbs_EDGE, TopAbs_VERTEX, TopAbs_COMPOUND, TopAbs_COMPSOLID,
    TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_INTERNAL, TopAbs_EXTERNAL
)
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.GeomAbs import (
    GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere,
    GeomAbs_Torus, GeomAbs_BezierSurface, GeomAbs_BSplineSurface,
    GeomAbs_SurfaceOfRevolution, GeomAbs_SurfaceOfExtrusion,
    GeomAbs_OffsetSurface, GeomAbs_OtherSurface,
    GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_Hyperbola,
    GeomAbs_Parabola, GeomAbs_BezierCurve, GeomAbs_BSplineCurve,
    GeomAbs_OffsetCurve, GeomAbs_OtherCurve
)
from OCC.Core.BRepGProp import brepgprop_SurfaceProperties, brepgprop_VolumeProperties, brepgprop_LinearProperties
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib_Add
from OCC.Core.TopoDS import topods
from OCC.Extend.TopologyUtils import TopologyExplorer
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.TopTools import TopTools_IndexedMapOfShape


SURFACE_TYPE_MAP = {
    GeomAbs_Plane: "Plane",
    GeomAbs_Cylinder: "Cylinder",
    GeomAbs_Cone: "Cone",
    GeomAbs_Sphere: "Sphere",
    GeomAbs_Torus: "Torus",
    GeomAbs_BezierSurface: "Bezier",
    GeomAbs_BSplineSurface: "BSpline",
    GeomAbs_SurfaceOfRevolution: "SurfaceOfRevolution",
    GeomAbs_SurfaceOfExtrusion: "SurfaceOfExtrusion",
    GeomAbs_OffsetSurface: "Offset",
    GeomAbs_OtherSurface: "Other",
}

CURVE_TYPE_MAP = {
    GeomAbs_Line: "Line",
    GeomAbs_Circle: "Circle",
    GeomAbs_Ellipse: "Ellipse",
    GeomAbs_Hyperbola: "Hyperbola",
    GeomAbs_Parabola: "Parabola",
    GeomAbs_BezierCurve: "Bezier",
    GeomAbs_BSplineCurve: "BSpline",
    GeomAbs_OffsetCurve: "Offset",
    GeomAbs_OtherCurve: "Other",
}

ORIENTATION_MAP = {
    TopAbs_FORWARD: "Forward",
    TopAbs_REVERSED: "Reversed",
    TopAbs_INTERNAL: "Internal",
    TopAbs_EXTERNAL: "External",
}


def load_step(filename):
    reader = STEPControl_Reader()
    status = reader.ReadFile(filename)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file: {filename}")
    reader.TransferRoots()
    shape = reader.OneShape()
    print(f"[OK] Loaded: {filename}")
    return shape


def count_shapes(shape):
    counts = {}
    for topo_type, name in [
        (TopAbs_COMPOUND,   "Compounds"),
        (TopAbs_COMPSOLID,  "CompSolids"),
        (TopAbs_SOLID,      "Solids"),
        (TopAbs_SHELL,      "Shells"),
        (TopAbs_FACE,       "Faces"),
        (TopAbs_WIRE,       "Wires"),
        (TopAbs_EDGE,       "Edges"),
        (TopAbs_VERTEX,     "Vertices"),
    ]:
        m = TopTools_IndexedMapOfShape()
        topexp.MapShapes(shape, topo_type, m)
        counts[name] = m.Size()
    return counts


def get_bounding_box(shape):
    bbox = Bnd_Box()
    brepbndlib_Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
    return {
        "xmin": round(xmin, 6), "ymin": round(ymin, 6), "zmin": round(zmin, 6),
        "xmax": round(xmax, 6), "ymax": round(ymax, 6), "zmax": round(zmax, 6),
        "length_x": round(dx, 6),
        "length_y": round(dy, 6),
        "length_z": round(dz, 6),
        "diagonal": round(math.sqrt(dx**2 + dy**2 + dz**2), 6),
    }


def get_mass_properties(shape):
    props = GProp_GProps()
    brepgprop_VolumeProperties(shape, props)
    volume = props.Mass()
    cog = props.CentreOfMass()
    matrix = props.MatrixOfInertia()

    inertia = []
    for i in range(1, 4):
        row = []
        for j in range(1, 4):
            row.append(round(matrix.Value(i, j), 6))
        inertia.append(row)

    return {
        "volume": round(volume, 6),
        "center_of_mass": {
            "x": round(cog.X(), 6),
            "y": round(cog.Y(), 6),
            "z": round(cog.Z(), 6),
        },
        "matrix_of_inertia": inertia,
    }


def get_surface_area(shape):
    props = GProp_GProps()
    brepgprop_SurfaceProperties(shape, props)
    return round(props.Mass(), 6)


def analyze_faces(shape):
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    faces_data = []
    surface_type_counts = {}
    total_area = 0.0

    while explorer.More():
        face = topods.Face(explorer.Current())
        surf = BRepAdaptor_Surface(face, True)
        surf_type = SURFACE_TYPE_MAP.get(surf.GetType(), "Unknown")
        surface_type_counts[surf_type] = surface_type_counts.get(surf_type, 0) + 1

        props = GProp_GProps()
        brepgprop_SurfaceProperties(face, props)
        area = round(props.Mass(), 6)
        total_area += area

        face_info = {
            "surface_type": surf_type,
            "area": area,
            "orientation": ORIENTATION_MAP.get(face.Orientation(), "Unknown"),
        }

        if surf_type == "Plane":
            pln = surf.Plane()
            normal = pln.Axis().Direction()
            origin = pln.Location()
            face_info["plane"] = {
                "origin": {"x": round(origin.X(), 6), "y": round(origin.Y(), 6), "z": round(origin.Z(), 6)},
                "normal": {"x": round(normal.X(), 6), "y": round(normal.Y(), 6), "z": round(normal.Z(), 6)},
            }
        elif surf_type == "Cylinder":
            cyl = surf.Cylinder()
            face_info["cylinder"] = {
                "radius": round(cyl.Radius(), 6),
                "axis_direction": {
                    "x": round(cyl.Axis().Direction().X(), 6),
                    "y": round(cyl.Axis().Direction().Y(), 6),
                    "z": round(cyl.Axis().Direction().Z(), 6),
                },
                "axis_location": {
                    "x": round(cyl.Location().X(), 6),
                    "y": round(cyl.Location().Y(), 6),
                    "z": round(cyl.Location().Z(), 6),
                },
            }
        elif surf_type == "Cone":
            cone = surf.Cone()
            face_info["cone"] = {
                "semi_angle_deg": round(math.degrees(cone.SemiAngle()), 6),
                "apex": {
                    "x": round(cone.Apex().X(), 6),
                    "y": round(cone.Apex().Y(), 6),
                    "z": round(cone.Apex().Z(), 6),
                },
                "axis_direction": {
                    "x": round(cone.Axis().Direction().X(), 6),
                    "y": round(cone.Axis().Direction().Y(), 6),
                    "z": round(cone.Axis().Direction().Z(), 6),
                },
                "axis_location": {
                    "x": round(cone.Location().X(), 6),
                    "y": round(cone.Location().Y(), 6),
                    "z": round(cone.Location().Z(), 6),
                },
            }
        elif surf_type == "Sphere":
            sph = surf.Sphere()
            face_info["sphere"] = {
                "radius": round(sph.Radius(), 6),
                "center": {
                    "x": round(sph.Location().X(), 6),
                    "y": round(sph.Location().Y(), 6),
                    "z": round(sph.Location().Z(), 6),
                },
            }
        elif surf_type == "Torus":
            tor = surf.Torus()
            face_info["torus"] = {
                "major_radius": round(tor.MajorRadius(), 6),
                "minor_radius": round(tor.MinorRadius(), 6),
            }

        faces_data.append(face_info)
        explorer.Next()

    return {
        "total_faces": len(faces_data),
        "total_area": round(total_area, 6),
        "surface_type_counts": surface_type_counts,
        "faces": faces_data,
    }


def analyze_edges(shape):
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    edges_data = []
    curve_type_counts = {}
    total_length = 0.0

    while explorer.More():
        edge = topods.Edge(explorer.Current())
        curve = BRepAdaptor_Curve(edge)
        curve_type = CURVE_TYPE_MAP.get(curve.GetType(), "Unknown")
        curve_type_counts[curve_type] = curve_type_counts.get(curve_type, 0) + 1

        props = GProp_GProps()
        brepgprop_LinearProperties(edge, props)
        length = round(props.Mass(), 6)
        total_length += length

        edge_info = {
            "curve_type": curve_type,
            "length": length,
            "orientation": ORIENTATION_MAP.get(edge.Orientation(), "Unknown"),
        }

        if curve_type == "Circle":
            circ = curve.Circle()
            edge_info["circle"] = {
                "radius": round(circ.Radius(), 6),
                "center": {
                    "x": round(circ.Location().X(), 6),
                    "y": round(circ.Location().Y(), 6),
                    "z": round(circ.Location().Z(), 6),
                },
                "normal": {
                    "x": round(circ.Axis().Direction().X(), 6),
                    "y": round(circ.Axis().Direction().Y(), 6),
                    "z": round(circ.Axis().Direction().Z(), 6),
                },
            }
        elif curve_type == "Line":
            lin = curve.Line()
            edge_info["line"] = {
                "direction": {
                    "x": round(lin.Direction().X(), 6),
                    "y": round(lin.Direction().Y(), 6),
                    "z": round(lin.Direction().Z(), 6),
                },
                "location": {
                    "x": round(lin.Location().X(), 6),
                    "y": round(lin.Location().Y(), 6),
                    "z": round(lin.Location().Z(), 6),
                },
            }

        edges_data.append(edge_info)
        explorer.Next()

    return {
        "total_edges": len(edges_data),
        "total_length": round(total_length, 6),
        "curve_type_counts": curve_type_counts,
        "edges": edges_data,
    }


def analyze_vertices(shape):
    explorer = TopExp_Explorer(shape, TopAbs_VERTEX)
    vertices = []
    seen = set()

    while explorer.More():
        vertex = topods.Vertex(explorer.Current())
        pt = BRep_Tool.Pnt(vertex)
        key = (round(pt.X(), 4), round(pt.Y(), 4), round(pt.Z(), 4))
        if key not in seen:
            seen.add(key)
            vertices.append({"x": round(pt.X(), 6), "y": round(pt.Y(), 6), "z": round(pt.Z(), 6)})
        explorer.Next()

    return {"total_unique_vertices": len(vertices), "vertices": vertices}


def detect_holes(face_data):
    """Detect cylindrical faces as potential holes/bores."""
    holes = []
    for i, face in enumerate(face_data["faces"]):
        if face["surface_type"] == "Cylinder":
            cyl = face.get("cylinder", {})
            holes.append({
                "face_index": i,
                "radius": cyl.get("radius"),
                "diameter": round(cyl.get("radius", 0) * 2, 6),
                "axis_direction": cyl.get("axis_direction"),
                "axis_location": cyl.get("axis_location"),
            })
    return holes


def extract_face_adjacency(shape):
    """
    Returns a dict: {face_index: [list of adjacent face indices]}
    Two faces are adjacent if they share at least one edge.
    """
    topo = TopologyExplorer(shape)

    # Build a global edge index map for stable edge IDs
    edge_map = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, TopAbs_EDGE, edge_map)

    # First pass: build edge_index → faces mapping
    edge_to_faces = {}

    faces = list(topo.faces())

    for face_idx, face in enumerate(faces):
        for edge in topo.edges_from_face(face):
            edge_idx = edge_map.FindIndex(edge)
            if edge_idx not in edge_to_faces:
                edge_to_faces[edge_idx] = []
            edge_to_faces[edge_idx].append(face_idx)

    # Second pass: build face adjacency from shared edges
    adjacency = {i: set() for i in range(len(faces))}

    for edge_idx, face_indices in edge_to_faces.items():
        if len(face_indices) == 2:  # Edge shared by exactly 2 faces
            f1, f2 = face_indices
            adjacency[f1].add(f2)
            adjacency[f2].add(f1)

    # Convert sets to sorted lists for JSON serialisation
    return {k: sorted(list(v)) for k, v in adjacency.items()}


def check_validity(shape):
    analyzer = BRepCheck_Analyzer(shape)
    return bool(analyzer.IsValid())


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_features.py <path_to_step_file> [output_json]")
        sys.exit(1)

    step_file = os.path.abspath(sys.argv[1])
    if not os.path.exists(step_file):
        print(f"ERROR: File not found: {step_file}")
        sys.exit(1)

    print("=" * 60)
    print("CAD Feature Extractor - PythonOCC")
    print("=" * 60)

    shape = load_step(step_file)

    print("\n[1] Topology Counts")
    counts = count_shapes(shape)
    for k, v in counts.items():
        print(f"    {k}: {v}")

    print("\n[2] Bounding Box")
    bbox = get_bounding_box(shape)
    for k, v in bbox.items():
        print(f"    {k}: {v}")

    print("\n[3] Mass / Volume Properties")
    mass = get_mass_properties(shape)
    print(f"    Volume:         {mass['volume']}")
    cog = mass['center_of_mass']
    print(f"    Center of Mass: ({cog['x']}, {cog['y']}, {cog['z']})")

    print("\n[4] Total Surface Area")
    area = get_surface_area(shape)
    print(f"    {area}")

    print("\n[5] Face Analysis")
    face_data = analyze_faces(shape)
    print(f"    Total Faces: {face_data['total_faces']}")
    print(f"    Total Area:  {face_data['total_area']}")
    print("    Surface Types:")
    for stype, cnt in face_data["surface_type_counts"].items():
        print(f"        {stype}: {cnt}")

    print("\n[6] Edge Analysis")
    edge_data = analyze_edges(shape)
    print(f"    Total Edges:  {edge_data['total_edges']}")
    print(f"    Total Length: {edge_data['total_length']}")
    print("    Curve Types:")
    for ctype, cnt in edge_data["curve_type_counts"].items():
        print(f"        {ctype}: {cnt}")

    print("\n[7] Vertex Analysis")
    vertex_data = analyze_vertices(shape)
    print(f"    Unique Vertices: {vertex_data['total_unique_vertices']}")

    print("\n[8] Hole / Bore Detection (Cylindrical Faces)")
    holes = detect_holes(face_data)
    print(f"    Detected: {len(holes)} cylindrical feature(s)")
    for h in holes:
        print(f"        Face {h['face_index']}: diameter={h['diameter']}, axis={h['axis_direction']}")

    print("\n[9] Shape Validity")
    valid = check_validity(shape)
    print(f"    Valid: {valid}")

    print("\n[10] Face Adjacency")
    adjacency = extract_face_adjacency(shape)
    for face_idx, neighbors in adjacency.items():
        print(f"    Face {face_idx}: adjacent to {neighbors}")

    output = {
        "file": step_file,
        "topology_counts": counts,
        "bounding_box": bbox,
        "mass_properties": mass,
        "surface_area": area,
        "faces": face_data,
        "edges": edge_data,
        "vertices": vertex_data,
        "holes_detected": holes,
        "is_valid": valid,
        "face_adjacency": adjacency,
    }

    if len(sys.argv) >= 3:
        out_path = os.path.abspath(sys.argv[2])
    else:
        base = os.path.splitext(step_file)[0]
        out_path = base + "_features.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[DONE] Full results saved to: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
