"""
coord_system.py
---------------
Coordinate system definition and transformation.

Sits between feature extraction and all downstream steps (setup planning,
tool selection, parameter calculation, program sheet).

Solves two independent problems:

PROBLEM A — CAD axes ≠ machine axes (rotation)
-----------------------------------------------
CAD software and machine tools use different axis conventions. Most CAD
packages (SOLIDWORKS, CATIA, STEP files) use Z as the vertical axis.
Most VMCs use Y as the spindle (vertical) axis, or sometimes Z.

When a STEP file is imported, the feature axes in the JSON reflect the
CAD convention. If we use them directly for setup planning, we will assign
features to the wrong setups.

This module applies a rotation matrix that maps CAD axes to machine axes.
For example, if CAD +Z is up but the machine uses +Y as up:
    machine_vector = R @ cad_vector
    where R rotates Z→Y.

PROBLEM B — CAD origin ≠ work zero (translation)
-------------------------------------------------
The G54 work offset (program zero) is the reference point the CNC
controller uses for all coordinate values in the NC program. It must be
a physically meaningful datum on the part — typically the top face centre,
a corner, or a datum hole.

Raw CAD coordinates are measured from the CAD file origin, which may be
far from the part (as on the Hub, where Z origin is 11mm below the part).

This module computes the offset between the CAD origin and the chosen
work zero, so that all downstream coordinates are expressed correctly.

Transformation applied
----------------------
For any CAD point p_cad:
    p_machine = R @ (p_cad - work_zero_cad)

For any CAD direction vector v_cad (axes, normals):
    v_machine = R @ v_cad
    (directions are unaffected by translation)

Usage
-----
From Python:
    from coord_system import CoordSystem

    cs = CoordSystem.from_features(
        features_data,           # parsed features JSON
        cad_up_axis='+Y',        # which CAD axis points toward spindle
        work_zero='top_face_centre'  # or 'bottom_face_centre', 'origin', 'manual'
    )

    # Convert a CAD point to machine coordinates
    machine_pt = cs.to_machine([x_cad, y_cad, z_cad])

    # Convert a CAD direction to machine direction
    machine_ax = cs.axis_to_machine([ax, ay, az])

    # Attach to JSON data and propagate to downstream steps
    data['coord_system'] = cs.to_dict()

Supported CAD up-axis values
-----------------------------
    '+Y'   — CAD Y is vertical (no rotation needed for standard VMC)
    '+Z'   — CAD Z is vertical (common in SOLIDWORKS, FreeCAD, Fusion 360)
    '-Z'   — CAD -Z is vertical (less common)
    '+X'   — CAD X is vertical (unusual)

Supported work zero conventions
---------------------------------
    'top_face_centre'     — centre of the top face of the bounding box.
                            X = (xmin+xmax)/2, Y = ymax, Z = (zmin+zmax)/2
                            In machine coords: this becomes (0, 0, 0).
                            Depths into the part are negative Y (or Z on
                            a Z-spindle machine).
                            MOST COMMON for VMC milling.

    'bottom_face_centre'  — centre of the bottom face.
                            X = (xmin+xmax)/2, Y = ymin, Z = (zmin+zmax)/2

    'top_face_corner'     — front-left corner of the top face.
                            X = xmin, Y = ymax, Z = zmin

    'origin'              — use CAD origin as-is (no translation).
                            Only correct if the CAD file was modelled with
                            work zero at the origin.

    'manual'              — user supplies explicit CAD coordinates for the
                            work zero via work_zero_manual=[x, y, z].
"""

import json
import sys
import copy
import numpy as np
from typing import Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Rotation matrices for each supported CAD up-axis
# ---------------------------------------------------------------------------
#
# Machine convention used throughout this pipeline:
#   +Y = up (spindle axis, tool approaches from +Y, cuts in -Y direction)
#   +X = right
#   +Z = toward operator (front)
#
# Each matrix R satisfies: machine_vector = R @ cad_vector

_ROTATION_MATRICES = {
    '+Y': np.eye(3),           # No rotation — CAD Y is already machine Y
    '-Y': np.array([           # Flip Y (upside-down CAD)
        [ 1,  0,  0],
        [ 0, -1,  0],
        [ 0,  0, -1],
    ], dtype=float),
    '+Z': np.array([           # CAD Z → Machine Y (rotate -90° around X)
        [ 1,  0,  0],
        [ 0,  0,  1],
        [ 0, -1,  0],
    ], dtype=float),
    '-Z': np.array([           # CAD -Z → Machine Y (rotate +90° around X)
        [ 1,  0,  0],
        [ 0,  0, -1],
        [ 0,  1,  0],
    ], dtype=float),
    '+X': np.array([           # CAD X → Machine Y (rotate +90° around Z)
        [ 0, -1,  0],
        [ 1,  0,  0],
        [ 0,  0,  1],
    ], dtype=float),
    '-X': np.array([           # CAD -X → Machine Y
        [ 0,  1,  0],
        [-1,  0,  0],
        [ 0,  0,  1],
    ], dtype=float),
}


class CoordSystem:
    """
    Encapsulates the coordinate transformation between CAD space and
    machine (G54) space for a single part setup session.
    """

    def __init__(self,
                 rotation: np.ndarray,
                 work_zero_cad: np.ndarray,
                 cad_up_axis: str,
                 work_zero_convention: str,
                 bounding_box: Dict):
        """
        Direct constructor — prefer CoordSystem.from_features() instead.

        Parameters
        ----------
        rotation          : 3x3 ndarray — CAD→machine rotation matrix
        work_zero_cad     : 3-vector — work zero expressed in CAD coordinates
        cad_up_axis       : str — e.g. '+Y', '+Z'
        work_zero_convention : str — e.g. 'top_face_centre'
        bounding_box      : dict — raw bounding box from features JSON
        """
        self.R                    = rotation
        self.work_zero_cad        = work_zero_cad
        self.cad_up_axis          = cad_up_axis
        self.work_zero_convention = work_zero_convention
        self.bounding_box         = bounding_box

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_features(cls,
                      features_data: Dict,
                      cad_up_axis: str = '+Y',
                      work_zero: str = 'top_face_centre',
                      work_zero_manual: Optional[List[float]] = None
                      ) -> 'CoordSystem':
        """
        Build a CoordSystem from a features JSON dict.

        Parameters
        ----------
        features_data     : dict — parsed features JSON (from extract_features.py)
        cad_up_axis       : str  — which CAD axis is vertical ('+Y', '+Z', etc.)
        work_zero         : str  — work zero placement convention
        work_zero_manual  : list — [x,y,z] in CAD coords (only for work_zero='manual')
        """
        if cad_up_axis not in _ROTATION_MATRICES:
            raise ValueError(f"cad_up_axis must be one of {list(_ROTATION_MATRICES)}. "
                             f"Got: {cad_up_axis!r}")

        R  = _ROTATION_MATRICES[cad_up_axis]
        bb = features_data.get('bounding_box', {})

        xmin = bb.get('xmin', 0.0)
        xmax = bb.get('xmax', 0.0)
        ymin = bb.get('ymin', 0.0)
        ymax = bb.get('ymax', 0.0)
        zmin = bb.get('zmin', 0.0)
        zmax = bb.get('zmax', 0.0)

        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        cz = (zmin + zmax) / 2.0

        if work_zero == 'top_face_centre':
            # Top face = maximum value along the CAD up-axis
            # For +Y: top face is at ymax; centre in XZ plane
            up = cad_up_axis  # e.g. '+Y'
            if   up in ('+Y',):  wz = np.array([cx, ymax, cz])
            elif up in ('-Y',):  wz = np.array([cx, ymin, cz])
            elif up in ('+Z',):  wz = np.array([cx, cy,   zmax])
            elif up in ('-Z',):  wz = np.array([cx, cy,   zmin])
            elif up in ('+X',):  wz = np.array([xmax, cy, cz])
            elif up in ('-X',):  wz = np.array([xmin, cy, cz])
            else:                wz = np.array([cx, ymax, cz])

        elif work_zero == 'bottom_face_centre':
            if   up in ('+Y',):  wz = np.array([cx, ymin, cz])
            elif up in ('-Y',):  wz = np.array([cx, ymax, cz])
            elif up in ('+Z',):  wz = np.array([cx, cy,   zmin])
            elif up in ('-Z',):  wz = np.array([cx, cy,   zmax])
            elif up in ('+X',):  wz = np.array([xmin, cy, cz])
            elif up in ('-X',):  wz = np.array([xmax, cy, cz])
            else:                wz = np.array([cx, ymin, cz])

        elif work_zero == 'top_face_corner':
            # Front-left corner: xmin, zmin, top Y
            if   cad_up_axis in ('+Y',):  wz = np.array([xmin, ymax, zmin])
            elif cad_up_axis in ('+Z',):  wz = np.array([xmin, ymin, zmax])
            else:                         wz = np.array([xmin, ymax, zmin])

        elif work_zero == 'origin':
            wz = np.zeros(3)

        elif work_zero == 'manual':
            if work_zero_manual is None:
                raise ValueError("work_zero='manual' requires work_zero_manual=[x,y,z]")
            wz = np.array(work_zero_manual, dtype=float)

        else:
            raise ValueError(f"Unknown work_zero convention: {work_zero!r}")

        return cls(
            rotation             = R,
            work_zero_cad        = wz,
            cad_up_axis          = cad_up_axis,
            work_zero_convention = work_zero,
            bounding_box         = bb,
        )

    # ------------------------------------------------------------------
    # Transformation methods
    # ------------------------------------------------------------------

    def to_machine(self, cad_point: Union[List, np.ndarray]) -> np.ndarray:
        """
        Convert a CAD-space point to machine (G54) coordinates.

        machine_point = R @ (cad_point - work_zero_cad)

        Parameters
        ----------
        cad_point : [x, y, z] in CAD coordinates

        Returns
        -------
        np.ndarray : [x, y, z] in machine coordinates
        """
        p = np.array(cad_point, dtype=float)
        return self.R @ (p - self.work_zero_cad)

    def axis_to_machine(self, cad_axis: Union[List, np.ndarray]) -> np.ndarray:
        """
        Convert a CAD-space direction vector to machine direction.

        Directions are only rotated — not translated.
        machine_axis = R @ cad_axis

        Parameters
        ----------
        cad_axis : [ax, ay, az] unit vector in CAD coordinates

        Returns
        -------
        np.ndarray : unit vector in machine coordinates
        """
        v = np.array(cad_axis, dtype=float)
        result = self.R @ v
        norm = np.linalg.norm(result)
        return result / norm if norm > 1e-10 else result

    def to_cad(self, machine_point: Union[List, np.ndarray]) -> np.ndarray:
        """
        Inverse: convert machine (G54) coordinates back to CAD space.

        cad_point = R.T @ machine_point + work_zero_cad
        """
        p = np.array(machine_point, dtype=float)
        return self.R.T @ p + self.work_zero_cad

    # ------------------------------------------------------------------
    # Convenience: transform a bounding box
    # ------------------------------------------------------------------

    def machine_bounding_box(self) -> Dict:
        """
        Return the bounding box expressed in machine (G54) coordinates.

        This is what the program sheet uses to know the extents of the
        part in machine space — useful for safe rapid height calculation
        and work envelope checks.
        """
        bb = self.bounding_box
        corners_cad = [
            [bb['xmin'], bb['ymin'], bb['zmin']],
            [bb['xmin'], bb['ymin'], bb['zmax']],
            [bb['xmin'], bb['ymax'], bb['zmin']],
            [bb['xmin'], bb['ymax'], bb['zmax']],
            [bb['xmax'], bb['ymin'], bb['zmin']],
            [bb['xmax'], bb['ymin'], bb['zmax']],
            [bb['xmax'], bb['ymax'], bb['zmin']],
            [bb['xmax'], bb['ymax'], bb['zmax']],
        ]
        corners_machine = [self.to_machine(c) for c in corners_cad]
        xs = [c[0] for c in corners_machine]
        ys = [c[1] for c in corners_machine]
        zs = [c[2] for c in corners_machine]

        return {
            'xmin': round(min(xs), 4), 'xmax': round(max(xs), 4),
            'ymin': round(min(ys), 4), 'ymax': round(max(ys), 4),
            'zmin': round(min(zs), 4), 'zmax': round(max(zs), 4),
            'length_x': round(max(xs) - min(xs), 4),
            'length_y': round(max(ys) - min(ys), 4),
            'length_z': round(max(zs) - min(zs), 4),
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict:
        """
        Serialise to a plain dict suitable for embedding in JSON output.
        """
        wz = self.work_zero_cad
        mbb = self.machine_bounding_box()

        return {
            'cad_up_axis'          : self.cad_up_axis,
            'work_zero_convention' : self.work_zero_convention,
            'work_zero_cad'        : [round(float(x), 6) for x in wz],
            'rotation_matrix'      : [[round(float(v), 6) for v in row]
                                       for row in self.R],
            'is_identity_rotation' : bool(np.allclose(self.R, np.eye(3))),
            'notes': (
                f"Work zero (G54) = top face centre of bounding box "
                f"at CAD coords ({round(float(wz[0]),3)}, "
                f"{round(float(wz[1]),3)}, {round(float(wz[2]),3)}). "
                f"CAD up-axis: {self.cad_up_axis}. "
                f"{'No axis rotation required.' if np.allclose(self.R, np.eye(3)) else 'Axis rotation applied.'}"
            ),
            'machine_bounding_box' : mbb,
            'g54_meaning': (
                'All machine coordinates are relative to the work zero. '
                'In machine space: X=0, Y=0, Z=0 is the work zero. '
                'Positive Y = above part surface (air). '
                'Negative Y = depth into part. '
                'Set G54 offset on the controller to locate this point '
                'on the physical part on the machine table.'
            ),
        }

    @classmethod
    def from_dict(cls, d: Dict, bounding_box: Dict = None) -> 'CoordSystem':
        """Reconstruct a CoordSystem from a serialised dict."""
        R  = np.array(d['rotation_matrix'], dtype=float)
        wz = np.array(d['work_zero_cad'],   dtype=float)
        return cls(
            rotation             = R,
            work_zero_cad        = wz,
            cad_up_axis          = d['cad_up_axis'],
            work_zero_convention = d['work_zero_convention'],
            bounding_box         = bounding_box or {},
        )

    def __repr__(self):
        wz = self.work_zero_cad
        return (f"CoordSystem(up={self.cad_up_axis}, "
                f"work_zero={self.work_zero_convention}, "
                f"wz_cad=({wz[0]:.3f},{wz[1]:.3f},{wz[2]:.3f}))")


# ---------------------------------------------------------------------------
# Apply coord system to a full pipeline JSON
# ---------------------------------------------------------------------------

def apply_coord_system(data: Dict, cs: CoordSystem) -> Dict:
    """
    Embed the coord_system dict into the pipeline JSON and transform
    all feature axes and setup spindle directions into machine coordinates.

    Also transforms bounding box and adds machine-space work zero info.

    Parameters
    ----------
    data : dict — any pipeline JSON (features, processes, setups, etc.)
    cs   : CoordSystem instance

    Returns
    -------
    dict — copy of data with coord_system added and axes transformed
    """
    result = copy.deepcopy(data)
    result['coord_system'] = cs.to_dict()

    # Transform feature axes in clusters (if present)
    for c in result.get('clusters', []):
        cad_ax = c.get('feature_axis')
        if cad_ax is not None:
            machine_ax = cs.axis_to_machine(cad_ax)
            c['feature_axis_machine'] = [round(float(x), 6) for x in machine_ax]
            # flag if the transform changed anything meaningful
            c['axis_transform_applied'] = not np.allclose(
                np.array(cad_ax), machine_ax, atol=1e-4)

    # Transform setup spindle directions (if setups present)
    for s in result.get('setups', []):
        sd = s.get('spindle_direction')
        if sd is not None:
            machine_sd = cs.axis_to_machine(sd)
            s['spindle_direction_machine'] = [round(float(x), 6) for x in machine_sd]

        fa = s.get('feature_axis')
        if fa is not None:
            machine_fa = cs.axis_to_machine(fa)
            s['feature_axis_machine'] = [round(float(x), 6) for x in machine_fa]

    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_coord_summary(cs: CoordSystem):
    """Print a plain-language explanation of the coordinate system."""
    d   = cs.to_dict()
    wz  = cs.work_zero_cad
    mbb = d['machine_bounding_box']

    print("Coordinate system summary")
    print(f"  CAD up-axis        : {cs.cad_up_axis}")
    print(f"  Rotation applied   : {'No (identity)' if d['is_identity_rotation'] else 'Yes'}")
    print(f"  Work zero (G54)    : {cs.work_zero_convention}")
    print(f"  Work zero CAD pos  : "
          f"X={wz[0]:.3f}, Y={wz[1]:.3f}, Z={wz[2]:.3f}")
    print(f"  Machine bbox       :"
          f" X [{mbb['xmin']} → {mbb['xmax']}]"
          f" Y [{mbb['ymin']} → {mbb['ymax']}]"
          f" Z [{mbb['zmin']} → {mbb['zmax']}]")
    print(f"  Part height (Y)    : {mbb['length_y']} mm")
    print(f"  Notes: {d['notes']}")
    print()

    # Show a worked example
    print("  Example transforms (CAD → Machine):")
    examples = [
        ('Work zero (top face centre)', wz.tolist()),
        ('Bottom face centre',
         [float(wz[0]), float(cs.bounding_box.get('ymin',0)), float(wz[2])]),
        ('CAD origin', [0.0, 0.0, 0.0]),
    ]
    for label, cad_pt in examples:
        mpt = cs.to_machine(cad_pt)
        print(f"    {label:30s}: "
              f"CAD ({cad_pt[0]:.2f},{cad_pt[1]:.2f},{cad_pt[2]:.2f}) "
              f"→ Machine ({mpt[0]:.2f},{mpt[1]:.2f},{mpt[2]:.2f})")
    print()


# ---------------------------------------------------------------------------
# Entry point — run standalone to inspect and attach coord system to JSON
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python coord_system.py <features_json> [output_json] "
              "[--up +Y|+Z|+X] [--zero top_face_centre|bottom_face_centre|origin|manual] "
              "[--manual-zero x,y,z]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = None
    up_axis     = '+Y'
    zero_conv   = 'top_face_centre'
    manual_zero = None

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--up' and i + 1 < len(sys.argv):
            up_axis = sys.argv[i + 1]; i += 2
        elif arg == '--zero' and i + 1 < len(sys.argv):
            zero_conv = sys.argv[i + 1]; i += 2
        elif arg == '--manual-zero' and i + 1 < len(sys.argv):
            manual_zero = [float(v) for v in sys.argv[i+1].split(',')]; i += 2
        elif not arg.startswith('--') and output_path is None:
            output_path = arg; i += 1
        else:
            i += 1

    if output_path is None:
        output_path = input_path.replace('.json', '_coordsys.json')

    with open(input_path) as f:
        data = json.load(f)

    cs = CoordSystem.from_features(
        data,
        cad_up_axis      = up_axis,
        work_zero        = zero_conv,
        work_zero_manual = manual_zero,
    )

    print_coord_summary(cs)

    result = apply_coord_system(data, cs)
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Saved to: {output_path}")
