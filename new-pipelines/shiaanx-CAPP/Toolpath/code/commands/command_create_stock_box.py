import adsk.core
import adsk.fusion
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import DESIGN_WORKSPACE_ID, CAM_WORKSPACE_ID,  DESIGN_TOOLPATH_PANEL_ID, CAM_TOOLPATH_PANEL_ID
from ..lib.general_utils import resource_path, log
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar

import traceback

CMD_NAME = 'command_create_stock_box'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Display a greeting for testing purposes.'
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

toollib_checkboxes = []
toollib_url_by_name = None

WORKSPACE_PANEL_IDS = [
    (DESIGN_WORKSPACE_ID, DESIGN_TOOLPATH_PANEL_ID),
    (CAM_WORKSPACE_ID, CAM_TOOLPATH_PANEL_ID),
]
def start():
    ui = None
    try:
        fusion = Fusion()
        ui = fusion.getUI()
        
        cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED=False)

        add_handler(cmd_def.commandCreated, onCommandCreated, local_handlers=local_handlers)
    except:
        log(traceback.format_exc())
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

def stop():
    ui = None
    try:
        ui = Fusion().getUI()
        removeCommandFromToolbar(CMD_ID)
    except:
        log(traceback.format_exc())
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

def onCommandCreated(args):
    eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
    cmd = eventArgs.command
    inputs = cmd.commandInputs
    # Registration
    add_handler(cmd.execute, onCommandExecute, local_handlers=local_handlers)

def create_box(fusion : Fusion, *, name : str, size : list, transform : adsk.core.Matrix3D):
    app = fusion.getApplication()
    ui = app.userInterface
    design = fusion.getDesign()

    # Get the root component of the active design
    rootComp = design.rootComponent

    # Create a sketch on the construction plane
    sketches = rootComp.sketches

    xyPlane = rootComp.xYConstructionPlane
    sketch = sketches.add(xyPlane)
    sketch.name = name

        # Draw a rectangle on the sketch
    lines = sketch.sketchCurves.sketchLines
    lx, ly, lz = size
    point1 = adsk.core.Point3D.create(0, 0, 0)
    point2 = adsk.core.Point3D.create(lx, 0, 0)
    point3 = adsk.core.Point3D.create(lx, ly, 0)
    point4 = adsk.core.Point3D.create(0, ly, 0)
    lines.addByTwoPoints(point1, point2)
    lines.addByTwoPoints(point2, point3)
    lines.addByTwoPoints(point3, point4)
    lines.addByTwoPoints(point4, point1)

    # Extrude the rectangle to create a box
    extrudes = rootComp.features.extrudeFeatures
    extrudeInput = extrudes.createInput(sketch.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extrudeInput.setDistanceExtent(False, adsk.core.ValueInput.createByReal(lz))
    extrude = extrudes.add(extrudeInput)

    body = extrude.bodies.item(0)
    body.name = name
    body.transform(transform)

    ui.messageBox('Box created successfully!')

def Vector3d_from_itr(itr) -> adsk.core.Vector3D:
    x,y,z = itr
    return adsk.core.Vector3D.create(x,y,z)

def Point3D_from_itr(itr) -> adsk.core.Point3D:
    x,y,z = itr
    return adsk.core.Point3D.create(x,y,z)

def create_box_BRepBody(corner, xdir, ydir, size, component : adsk.fusion.Component, name) -> adsk.fusion.BRepBody:
    length, width, height = size
    cx,cy,cz = corner
    lx,ly,lz = size
    centerPoint = adsk.core.Point3D.create(cx + 0.5*lx, cy + 0.5*ly, cz + 0.5*lz)
    lengthDirection = Vector3d_from_itr(xdir)
    widthDirection = Vector3d_from_itr(ydir)
    assert lengthDirection.normalize()
    assert widthDirection.normalize()
    assert abs(lengthDirection.dotProduct(widthDirection)) < 1e-4

    obox = adsk.core.OrientedBoundingBox3D.create(centerPoint=centerPoint, lengthDirection=lengthDirection, widthDirection=widthDirection, length=length, width=width, height=height)
    tmpManager = adsk.fusion.TemporaryBRepManager.get()
    brepbox : adsk.fusion.BRepBody = tmpManager.createBox(obox)
    assert isinstance(brepbox, adsk.fusion.BRepBody)

    
    if Fusion().isParametricDesign():
        base = component.features.baseFeatures.add()
        base.startEdit()
        try:
            body = component.bRepBodies.add(brepbox, base)
            body.name = name
        finally:
            if base != None:
                base.finishEdit()
    else:
        body = component.bRepBodies.add(brepbox)
        body.name = name
    return body

def onCommandExecute(args):
    fusion = Fusion()

    # Apply transformation matrix to the body
    # Example transformation matrix (replace with your own)
    transform = adsk.core.Matrix3D.create()
    transform.translation = adsk.core.Vector3D.create(50, 50, 50)  # Translate by 50 units in each direction
    design = fusion.getDesign()
        # Get the body and transform it
    corner=[1,2,3]
    xdir=[1,0,1]
    ydir=[0,1,0]
    size = [1,2,3]
    component = design.rootComponent
    name = "hello"
    create_box_BRepBody(corner, xdir, ydir, size, component, name)