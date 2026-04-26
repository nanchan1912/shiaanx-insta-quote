import adsk.core
from ..lib.event_utils import command_id_from_name
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import DESIGN_WORKSPACE_ID, DESIGN_TOOLPATH_PANEL_ID, log, resource_path, load_config, save_config
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
from ..lib.component_utils import FusionFullPath
from ..lib import geometry as geom
import traceback

CMD_NAME = 'Export Step File'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Export step file'
IS_PROMOTED = False
WORKSPACE_ID = DESIGN_WORKSPACE_ID
PANEL_ID = DESIGN_TOOLPATH_PANEL_ID
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
            eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
            cmd = eventArgs.command
            inputs = cmd.commandInputs

            # Model
            tooltip = 'Select the model that should be analyzed by Design Advisor.'
            input : adsk.core.SelectionCommandInput = inputs.addSelectionInput('SelectBody_id', 'Model', tooltip)
            input.addSelectionFilter('SolidBodies')
            input.addSelectionFilter('Occurrences')
            input.addSelectionFilter('RootComponents')
            input.setSelectionLimits(1, 1)
            input.tooltip = tooltip

            # use nativeObject
            input = inputs.addBoolValueInput('UseNativeObject_id', 'Use native object', True, '', False)
            input.tooltip = 'Export body.nativeObject to step'

            # Path
            config = load_config()
            path = config["export_step_file_path"]
            if path is None:
                path = ""
            inputs.addStringValueInput('SelectPath_id', 'Path', path)

            # Registration
            exec_handler = CommandExecuteHandler()
            cmd.execute.add(exec_handler)
            local_handlers.append(exec_handler)
        except:
            msg = 'Failed:\n{}'.format(traceback.format_exc())
            log(msg)

class CommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        fusion = Fusion()
        ui = None
        try:
            ui = fusion.getUI()

            eventArgs = adsk.core.CommandEventArgs.cast(args)
            inputs = eventArgs.command.commandInputs

            # path
            path = inputs.itemById('SelectPath_id').value
            config = load_config()
            config["export_step_file_path"] = path
            save_config(config)

            #model
            fusion_paths = FusionFullPath()
            model = inputs.itemById('SelectBody_id').selection(0).entity
            body, transform = fusion_paths.extract_body_and_transform(model)

            if inputs.itemById('UseNativeObject_id').value:
                body = body.nativeObject
                assert body.assemblyContext is None
                transform = None
            else:
                transform = geom.jsonify_Matrix3D(transform)

            assert isinstance(body, adsk.fusion.BRepBody)
            msg = f"""
            Model: {model}   
            Transform: (not saved)
            {transform}
            """
            log(msg)

            fusion.save_step_file(path, body)
            log(f"Saved step file to {path}")
            
            assert isinstance(body, adsk.fusion.BRepBody)
        except:
            msg = 'Failed:\n{}'.format(traceback.format_exc())
            log(msg)



