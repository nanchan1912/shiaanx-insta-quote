import adsk.core

from ..lib.event_utils import command_id_from_name
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, CAM_WORKSPACE_ID, CAM_TOOLPATH_PANEL_ID
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
import traceback

CMD_NAME = 'Inspect edge'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Inspect edge'
IS_PROMOTED = False
WORKSPACE_ID = CAM_WORKSPACE_ID
PANEL_ID = CAM_TOOLPATH_PANEL_ID
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

def start():
    ui = None
    try:
        fusion = Fusion()
        ui = fusion.getUI()
        
        cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED)

        handler = CommandCreatedHandler()
        cmd_def.commandCreated.add(handler)
        local_handlers.append(handler)
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

class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        ui = None
        try:
            ui = Fusion().getUI()
            eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
            cmd = eventArgs.command
            inputs = cmd.commandInputs

            # Body
            tooltip = 'Select edge to inspect.'
            input : adsk.core.SelectionCommandInput = inputs.addSelectionInput('SelectedEdge_id', 'Edge', tooltip)
            input.addSelectionFilter('Edges')
            input.setSelectionLimits(1, 1)
            input.tooltip = tooltip

            # Registration
            exec_handler = CommandExecuteHandler()
            cmd.execute.add(exec_handler)
            local_handlers.append(exec_handler)
        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

class CommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        fusion = Fusion()
        ui = None
        try:
            eventArgs = adsk.core.CommandEventArgs.cast(args)

            inputs = eventArgs.command.commandInputs
            edge : adsk.fusion.BRepEdge = inputs.itemById('SelectedEdge_id').selection(0).entity
            g = edge.startVertex.geometry
            pt = adsk.core.Point3D.create(g.x, g.y, g.z)
            design = fusion.getDesign()
            rootComp = design.rootComponent
            xyPlane = rootComp.xYConstructionPlane
            # Create a new sketch on the XY plane
            sketches = rootComp.sketches
            sketch = sketches.add(xyPlane)
            # Draw a small circle at the vertex location
            # Adjust the circle diameter as needed to make it visible
            circleDiameter = 0.1  # Adjust based on your preference
            circles = sketch.sketchCurves.sketchCircles
            circle = circles.addByCenterRadius(pt, circleDiameter / 2)
        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))



