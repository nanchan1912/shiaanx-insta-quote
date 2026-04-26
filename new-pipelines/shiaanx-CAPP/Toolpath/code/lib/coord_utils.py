import adsk.core
import adsk.fusion
import json
from types import SimpleNamespace
from .fusion_utils import Fusion


# Global cache to store construction geometry by coordinate system
# Key: tuple of (origin, xaxis, zaxis, component_id)
# Value: SimpleNamespace(originPoint, xAxis, zAxis)
_coord_system_cache = {}


def convert_units(value, from_unit, to_unit):
    # Conversion factors from 1 unit to cm
    conversion_to_cm = {
        'cm': 1,
        'mm': 0.1,
        'in': 2.54,
        'ft': 30.48,
        'm': 100
    }
    
    # Convert the value to cm first
    value_in_cm = value * conversion_to_cm[from_unit]
    
    # Convert from cm to the target unit
    value_in_target_unit = value_in_cm / conversion_to_cm[to_unit]
    
    return value_in_target_unit

def mm_from_cm(x):
    return x * 10.0

def convert_point3D(point, from_unit, to_unit):
    x_converted = convert_units(point.x, from_unit, to_unit)
    y_converted = convert_units(point.y, from_unit, to_unit)
    z_converted = convert_units(point.z, from_unit, to_unit)
    
    return adsk.core.Point3D.create(x_converted, y_converted, z_converted)

def getPoint3DFromJson(json_point) -> adsk.core.Point3D:
    return adsk.core.Point3D.create(json_point[0], json_point[1], json_point[2])

def Vector3D_from_json(json) -> adsk.core.Vector3D:
    return adsk.core.Vector3D.create(json[0], json[1], json[2])

def Point3D_from_json(json) -> adsk.core.Point3D:
    return adsk.core.Point3D.create(json[0], json[1], json[2])

def Matrix3D_from_json(json) -> adsk.core.Matrix3D:
    origin = Point3D_from_json(json['origin'])
    xaxis  = Vector3D_from_json(json['xaxis'])
    yaxis  = Vector3D_from_json(json['yaxis'])
    zaxis  = Vector3D_from_json(json['zaxis'])
    ret = adsk.core.Matrix3D.create()
    assert ret.setWithCoordinateSystem(origin, xaxis, yaxis, zaxis)
    assert isinstance(ret, adsk.core.Matrix3D)
    return ret

def create_vector_3d(arr: list[float]) -> adsk.core.Vector3D:
    """Utility function to create a Vector3D from an array.

    Arguments:
    arr -- An array of 3 floats.
    """
    return adsk.core.Vector3D.create(arr[0], arr[1], arr[2])

def invert(obj : adsk.core.Matrix3D) -> adsk.core.Matrix3D:
    ret = obj.copy()
    assert ret.invert()
    return ret

def compose(obj1 : adsk.core.Matrix3D, obj2 : adsk.core.Matrix3D) -> adsk.core.Matrix3D:
    ret = obj1.copy()
    assert ret.transformBy(obj2)
    return ret

def get_coord_system_tuple(origin, xaxis, zaxis, component):
    """
    Generate a unique tuple for a coordinate system.
    This allows us to identify when the same WCS is being used.
    
    Arguments:
    origin -- Origin point (Point3D)
    xaxis -- X-axis vector (Vector3D)
    zaxis -- Z-axis vector (Vector3D)
    component -- The component the geometry is in
    
    Returns:
    String tuple that uniquely identifies this coordinate system
    """
    # Round to avoid floating point precision issues
    origin_str = f"{round(origin.x, 6)},{round(origin.y, 6)},{round(origin.z, 6)}"
    xaxis_str = f"{round(xaxis.x, 6)},{round(xaxis.y, 6)},{round(xaxis.z, 6)}"
    zaxis_str = f"{round(zaxis.x, 6)},{round(zaxis.y, 6)},{round(zaxis.z, 6)}"
    
    # Include component ID to keep coordinate systems separate per component
    component_id = component.entityToken if component else "root"
    
    # Combine all into a 4-tuple
    combined = (origin_str, xaxis_str, zaxis_str, component_id)
    return combined

def find_existing_coord_system(origin, xaxis, zaxis, component):
    """
    Check if construction geometry already exists for this coordinate system.
    
    Returns:
    SimpleNamespace with (originPoint, xAxis, zAxis) if found, None otherwise
    """
    cache_key = get_coord_system_tuple(origin, xaxis, zaxis, component)
    
    if cache_key in _coord_system_cache:
        cached = _coord_system_cache[cache_key]
        
        # Verify the cached objects still exist and are valid
        if (cached.originPoint.isValid and 
                cached.xAxis.isValid and 
                cached.zAxis.isValid):
                # print(f"Reusing existing WCS construction geometry (tuple: {cache_key})")
                return cached
        else:
            # Cached objects are no longer valid, remove from cache
            del _coord_system_cache[cache_key]
            # print(f"Cached WCS geometry invalid, will recreate")
    
    return None

def cache_coord_system(origin, xaxis, zaxis, component, coord_system):
    """
    Store construction geometry in the cache for reuse.
    
    Arguments:
    origin, xaxis, zaxis, component -- Coordinate system definition
    coord_system -- SimpleNamespace with (originPoint, xAxis, zAxis)
    """
    cache_key = get_coord_system_tuple(origin, xaxis, zaxis, component)
    _coord_system_cache[cache_key] = coord_system
    # print(f"Cached new WCS construction geometry (tuple: {cache_key}...)")

def clear_coord_system_cache():
    """Clear the entire cache. Call this when starting a new document or on cleanup."""
    global _coord_system_cache
    _coord_system_cache.clear()
    # print("Cleared WCS construction geometry cache")

def construct_coord_system(wcs_occurrence, origin, xaxis, zaxis):
    """
    Creates coordinate system construction geometry.
    """
    if wcs_occurrence is None:
        comp = Fusion().getDesign().rootComponent
    else:
        comp = wcs_occurrence.component

    # Convert origin from mm to cm (Fusion's internal unit)
    origin_cm = convert_point3D(origin, 'mm', 'cm')
    
    # Check if we already have construction geometry for this coordinate system
    existing = find_existing_coord_system(origin_cm, xaxis, zaxis, comp)
    if existing:
        return existing

    sketches = comp.sketches
    axes = comp.constructionAxes
   
    # Step 1: Create a new sketch on the XY plane
    sketch : adsk.fusion.Sketch = sketches.add(comp.xYConstructionPlane) 
    sketch.name = "WCS Sketch (Toolpath)"
    
    # Step 2: Add sketch points
    point1 = sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))
    zPoint = adsk.core.Point3D.create(zaxis.x , zaxis.y , zaxis.z )
    point2 = sketch.sketchPoints.add(zPoint)
    xPoint = adsk.core.Point3D.create(xaxis.x , xaxis.y , xaxis.z )
    point3 = sketch.sketchPoints.add(xPoint)
    oPoint = convert_point3D(origin, 'mm', 'cm')
    point4 = sketch.sketchPoints.add(oPoint)
    
    # Add lines between points
    line1 = sketch.sketchCurves.sketchLines.addByTwoPoints(point1, point2)
    line2 = sketch.sketchCurves.sketchLines.addByTwoPoints(point1, point3)
    
    # Make lines construction geometry
    line1.isConstruction = True
    line2.isConstruction = True

    zAxisLine = sketch.sketchCurves.sketchLines.addByTwoPoints(point1, point2)
    zAxisLine.isConstruction = True
    zAxisLine.isFixed = True
    
    xAxisLine = sketch.sketchCurves.sketchLines.addByTwoPoints(point1, point3)
    xAxisLine.isConstruction = True
    xAxisLine.isFixed = True

    sketch.isVisible = False 
    
    

    result = SimpleNamespace(originPoint=point1, xAxis=xAxisLine, zAxis=zAxisLine, sketch=sketch)
    cache_coord_system(origin_cm, xaxis, zaxis, comp, result)

    return result

def get_tool_orientation(coorddef, body_occurrence):
    """
    Creates a tool orientation axis from a coordinate definition.
    
    Arguments:
    coorddef -- Dictionary containing 'workCoordinateSystem_mm' matrix
    body_occurrence -- The body occurrence for assembly context
    
    Returns:
    SimpleNamespace with zAxis and xAxis construction axes in assembly context
    """
    wcs_from_comp = Matrix3D_from_json(coorddef['workCoordinateSystem_mm'])

    origin, xaxis, yaxis, zaxis = wcs_from_comp.getAsCoordinateSystem()
    c = construct_coord_system(body_occurrence, origin, xaxis, zaxis)

    return SimpleNamespace(
        zAxis=c.zAxis.createForAssemblyContext(body_occurrence),
        xAxis=c.xAxis.createForAssemblyContext(body_occurrence),
    )
