"""
geometry_utils.py
-----------------
Geometric helper functions used by the feature clustering pipeline.
All functions operate on plain dicts matching the PythonOCC JSON output format.
No assumptions are made about part geometry, orientation, or feature types.
"""

import numpy as np


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

def to_vec(d):
    """
    Convert a dict with x/y/z keys to a numpy array.
    Handles near-zero floats like -0.0 cleanly.
    """
    return np.array([d['x'], d['y'], d['z']], dtype=float)


def unit(v):
    """
    Return unit vector.
    Returns zero vector if input magnitude is near zero.
    """
    n = np.linalg.norm(v)
    if n < 1e-10:
        return np.zeros(3)
    return v / n


def are_parallel(v1, v2, tol=1e-4):
    """
    Return True if two vectors are parallel OR anti-parallel.
    Uses the sine of the angle between them (cross product magnitude).
    tol: maximum allowed sine value (approx equal to angle in radians for small angles).
    Works for any axis direction - not limited to principal axes.
    """
    u1 = unit(np.array(v1, dtype=float))
    u2 = unit(np.array(v2, dtype=float))
    cross = np.cross(u1, u2)
    return np.linalg.norm(cross) < tol


def are_same_direction(v1, v2, tol=1e-4):
    """
    Return True only if two vectors point in the same direction (not anti-parallel).
    """
    u1 = unit(np.array(v1, dtype=float))
    u2 = unit(np.array(v2, dtype=float))
    return np.dot(u1, u2) > (1.0 - tol)


def are_opposite_direction(v1, v2, tol=1e-4):
    """
    Return True if two vectors point in opposite directions.
    """
    u1 = unit(np.array(v1, dtype=float))
    u2 = unit(np.array(v2, dtype=float))
    return np.dot(u1, u2) < -(1.0 - tol)


def point_to_line_distance(point, line_origin, line_direction):
    """
    Compute the perpendicular distance from a point to an infinite line.
    line_origin    : any point on the line
    line_direction : direction vector of the line (need not be unit)
    Returns scalar distance.
    """
    p = np.array(point, dtype=float)
    o = np.array(line_origin, dtype=float)
    d = unit(np.array(line_direction, dtype=float))
    delta = p - o
    parallel_component = np.dot(delta, d) * d
    perp_component = delta - parallel_component
    return np.linalg.norm(perp_component)


def is_principal_axis(direction, tol=1e-3):
    """
    Return True if a direction vector is aligned with one of the six
    principal axis directions: +X, -X, +Y, -Y, +Z, -Z.
    Used to flag non-standard (angled) features for setup planning.
    """
    u = unit(np.array(direction, dtype=float))
    principal_axes = [
        [1, 0, 0], [-1, 0, 0],
        [0, 1, 0], [0, -1, 0],
        [0, 0, 1], [0, 0, -1],
    ]
    for ax in principal_axes:
        if np.linalg.norm(u - np.array(ax, dtype=float)) < tol:
            return True
    return False


# ---------------------------------------------------------------------------
# Cylinder-specific helpers
# ---------------------------------------------------------------------------

def axes_are_collinear(cyl1, cyl2, direction_tol=1e-4, distance_tol=0.01):
    """
    Return True if two cylinder faces share the same geometric axis.

    Two cylinders are collinear if:
    1. Their axis_direction vectors are parallel or anti-parallel
    2. The axis_location of cyl2 lies on the infinite line defined by
       cyl1's axis_location and axis_direction

    cyl1, cyl2      : cylinder sub-dicts from JSON with keys
                      'axis_direction' and 'axis_location'
    direction_tol   : tolerance for parallel direction check
    distance_tol    : max allowed perpendicular distance in model units (mm).
                      0.01mm is safe for typical machined parts.
    """
    d1 = to_vec(cyl1['axis_direction'])
    d2 = to_vec(cyl2['axis_direction'])

    if not are_parallel(d1, d2, tol=direction_tol):
        return False

    p1 = to_vec(cyl1['axis_location'])
    p2 = to_vec(cyl2['axis_location'])

    dist = point_to_line_distance(p2, p1, d1)
    return dist < distance_tol


def cylinder_depth(cyl_face):
    """
    Estimate the axial length (depth) of a cylindrical face from area and radius.
    Derived from: area = 2 * pi * r * h  =>  h = area / (2 * pi * r)
    Returns None if radius is zero.
    """
    r = cyl_face['cylinder']['radius']
    if r < 1e-10:
        return None
    area = cyl_face['area']
    return area / (2 * np.pi * r)


# ---------------------------------------------------------------------------
# Cone-specific helpers
# ---------------------------------------------------------------------------

def cone_axis_collinear_with_cylinder(cone_face, cyl_face,
                                      direction_tol=1e-4, distance_tol=0.01):
    """
    Return True if a Cone face shares its rotational axis with a Cylinder face.

    Used to detect countersinks and chamfers at bore entries: PythonOCC outputs
    a Cone surface for any conical feature (countersink entry, chamfered edge).
    If the cone's axis is collinear with the cylinder seed's axis, it belongs
    to the same feature.

    cone_face : face node dict with surface_type == 'Cone' and a 'cone' sub-dict
                containing 'axis_direction' and 'axis_location'
    cyl_face  : face node dict with surface_type == 'Cylinder' and a 'cylinder'
                sub-dict (used as the reference axis)
    """
    cone = cone_face['cone']
    cyl  = cyl_face['cylinder']

    d_cone = to_vec(cone['axis_direction'])
    d_cyl  = to_vec(cyl['axis_direction'])

    if not are_parallel(d_cone, d_cyl, tol=direction_tol):
        return False

    p_cone = to_vec(cone['axis_location'])
    p_cyl  = to_vec(cyl['axis_location'])

    dist = point_to_line_distance(p_cone, p_cyl, d_cyl)
    return dist < distance_tol


# ---------------------------------------------------------------------------
# Torus-specific helpers
# ---------------------------------------------------------------------------

def torus_is_bridge(torus_node, cluster, G):
    """
    Return True if a Torus face has at least two neighbours already in the cluster.

    A torus face (fillet or blend radius) that touches two or more faces already
    in the cluster is acting as a geometric bridge between them and belongs to the
    same feature. This handles filleted bore floors and chamfered pocket corners.

    torus_node : integer face index of the Torus face being tested
    cluster    : set of face indices currently in the growing cluster
    G          : the face adjacency NetworkX graph
    """
    neighbours_in_cluster = sum(1 for nb in G.neighbors(torus_node) if nb in cluster)
    return neighbours_in_cluster >= 2


# ---------------------------------------------------------------------------
# Plane-specific helpers
# ---------------------------------------------------------------------------

def planes_are_parallel(plane1, plane2, tol=1e-4):
    """
    Return True if two plane faces have parallel or anti-parallel normals.
    """
    n1 = to_vec(plane1['plane']['normal'])
    n2 = to_vec(plane2['plane']['normal'])
    return are_parallel(n1, n2, tol=tol)


def plane_normal_matches_axis(plane_face, axis_direction, tol=1e-3):
    """
    Return True if a plane's normal is parallel or anti-parallel to
    a given axis direction vector.
    Used to detect whether a plane could be the bottom face of a hole
    whose axis matches axis_direction.
    axis_direction may be a dict {x,y,z} or a numpy array or list.
    """
    normal = to_vec(plane_face['plane']['normal'])
    # Accept both dict form {x,y,z} and array/list form
    if isinstance(axis_direction, dict):
        axis = to_vec(axis_direction)
    else:
        axis = np.array(axis_direction, dtype=float)
    return are_parallel(normal, axis, tol=tol)


def point_inside_cylinder_cross_section(plane_face, cyl_face, tol=1.0):
    """
    Return True if the plane face origin lies within or near the circular
    cross-section of a cylinder.
    Used to confirm a plane is the bottom cap of a hole rather than an
    unrelated nearby face with a coincidentally parallel normal.

    tol: allowed overshoot beyond cylinder radius (mm), accounts for
         floating point and modelling tolerances.
    """
    plane_origin = to_vec(plane_face['plane']['origin'])
    cyl_axis_loc = to_vec(cyl_face['cylinder']['axis_location'])
    cyl_axis_dir = to_vec(cyl_face['cylinder']['axis_direction'])
    cyl_radius   = cyl_face['cylinder']['radius']

    dist = point_to_line_distance(plane_origin, cyl_axis_loc, cyl_axis_dir)
    return dist <= (cyl_radius + tol)


def plane_closes_cylinder(plane_face, cyl_face,
                           direction_tol=1e-3, position_tol=1.0,
                           area_ratio_max=4.0):
    """
    Return True if a plane face is the closing cap (bottom or top) of a
    cylindrical hole or boss.

    Conditions — both must be true:
    1. Plane normal is parallel to cylinder axis direction
    2. Plane area is not excessively larger than the cylinder cross-section area
       This prevents large stock/datum faces from being absorbed into a hole cluster.
       A cap plane should have area close to pi*r^2.
       area_ratio_max: max allowed ratio of plane_area / (pi * r^2).
       Default 4.0 gives generous tolerance for non-circular caps and bosses.

    NOTE: The position check (plane origin inside cylinder cross-section) was
    deliberately removed. PythonOCC reports a plane's origin as a mathematical
    reference point on the infinite plane, not the physical centre of the face.
    This means the origin can be arbitrarily far from the actual face geometry,
    causing false negatives for valid cap planes (e.g. face 80/84 on the hub part,
    origin reported at (0,7.8,0) while the bore is at (-10.5,11.8,16.2)).
    The position check is unnecessary because grow_cluster only calls this function
    on faces that are already graph-adjacent to the cylinder — they share a physical
    BRep edge, so physical contact is guaranteed by the graph. The area-ratio check
    alone is sufficient to reject large stock faces.
    """
    if not plane_normal_matches_axis(
        plane_face,
        cyl_face['cylinder']['axis_direction'],
        tol=direction_tol
    ):
        return False

    # Area ratio check
    import math
    cyl_cross_section_area = math.pi * cyl_face['cylinder']['radius'] ** 2
    if cyl_cross_section_area > 1e-6:
        ratio = plane_face['area'] / cyl_cross_section_area
        if ratio > area_ratio_max:
            return False

    return True


# ---------------------------------------------------------------------------
# Cluster-level summary helpers
# (used after clustering to summarise what was found)
# ---------------------------------------------------------------------------

def get_feature_axis(cluster_faces):
    """
    For a cluster containing at least one cylinder face, return the
    normalised axis direction of the first cylinder found.
    Returns None if no cylinder faces are present.
    """
    for face in cluster_faces:
        if face['surface_type'] == 'Cylinder':
            return unit(to_vec(face['cylinder']['axis_direction'])).tolist()
    return None


def get_feature_depth(cluster_faces):
    """
    Estimate total axial depth of a hole/boss feature.
    Sums axial lengths of all cylinder faces in the cluster.
    Returns None if no cylinders present.
    """
    total_depth = 0.0
    found = False
    for face in cluster_faces:
        if face['surface_type'] == 'Cylinder':
            d = cylinder_depth(face)
            if d is not None:
                total_depth += d
                found = True
    return round(total_depth, 4) if found else None


def get_radii_sorted(cluster_faces):
    """
    Return sorted list of unique radii from cylinder faces in the cluster.
    Ascending order: smallest (innermost bore) first.
    Used for counterbore/countersink detection in the classification step.
    """
    radii = set()
    for face in cluster_faces:
        if face['surface_type'] == 'Cylinder':
            radii.add(round(face['cylinder']['radius'], 4))
    return sorted(radii)
