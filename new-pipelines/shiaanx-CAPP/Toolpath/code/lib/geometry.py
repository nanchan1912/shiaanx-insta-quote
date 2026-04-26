import adsk.core
import math
import adsk.core

def get_xyz(obj):
    return (obj.x, obj.y, obj.z)

def jsonify_Matrix3D(coord_sys : adsk.core.Matrix3D) -> dict:
    assert isinstance(coord_sys, adsk.core.Matrix3D)
    origin, xaxis, yaxis, zaxis = coord_sys.getAsCoordinateSystem()
    return {
        "origin" : get_xyz(origin), # seems to be always in cm
        "xaxis"  : get_xyz(xaxis),
        "yaxis"  : get_xyz(yaxis),
        "zaxis"  : get_xyz(zaxis),
    }
def transform_point(m : adsk.core.Matrix3D, pt : adsk.core.Point3D):
    ret = pt.copy()
    success =  pt.transformBy(m)
    if not success:
        msg = f"""
        Failed to transform point:
        matrix: {sprint_Matrix3D(m)}
        point : {sprint_Point3D(pt)}
        """
    return ret

def inverse(m : adsk.core.Matrix3D):
    ret = m.copy()
    success = ret.invert()
    if not success:
        msg = f"""
        Failed to invert matrix:
        matrix: {sprint_Matrix3D(m)}
        """
    return ret

def compose_Matrix3D(m1 : adsk.core.Matrix3D, m2 : adsk.core.Matrix3D):
    ret = m2.copy()
    success = ret.transformBy(m1)
    if not success:
        msg = f"""
        Failed to compose matrices:
        m1: {sprint_Matrix3D(m1)}
        m2: {sprint_Matrix3D(m2)}
        """
    return ret

def sprint_Point3D(pt : adsk.core.Point3D):
    (x,y,z) = pt.asArray()
    return f'[{x}, {y}, {z}]'

def sprint_Matrix3D(m : adsk.core.Matrix3D):
    row0, row1, row2, row3 = listify(m)
    return f"""[{row0},
  {row1},
  {row2},
  {row3}]"""

def listify(obj):
    if isinstance(obj, (adsk.core.Point3D, adsk.core.Vector3D)):
        (x,y,z) = obj.asArray()
        return [x,y,z]
    elif isinstance(obj, adsk.core.Matrix3D):
        m = obj
        return [ [m.getCell(0,0), m.getCell(0,1), m.getCell(0,2), m.getCell(0,3)],
          [m.getCell(1,0), m.getCell(1,1), m.getCell(1,2), m.getCell(1,3)],
          [m.getCell(2,0), m.getCell(2,1), m.getCell(2,2), m.getCell(2,3)],
          [m.getCell(3,0), m.getCell(3,1), m.getCell(3,2), m.getCell(3,3)] ]
    else:
        raise Exception(f'listify({obj})')

def isapprox_array(arr1, arr2, * , rtol=1e-5, atol=0.0) -> bool:
    dsq = 0.0
    n1sq = 0.0
    n2sq = 0.0
    for (x1, x2) in zip(arr1, arr2):
        n1sq += x1**2
        n2sq += x2**2
        dsq += (x1 - x2)**2
    d = math.sqrt(dsq)
    n1 = math.sqrt(n1sq)
    n2 = math.sqrt(n2sq)
    return d <= rtol * max(n1, n2) + atol

def isapprox(obj1, obj, **kw) -> bool:
    return isapprox_array(obj1.asArray(), obj.asArray(), **kw)
