import adsk.core

import json
from ..lib.event_utils import add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import COMPANY_NAME, ADDIN_NAME, resource_path, log, handle_error, desktop_path, CAM_WORKSPACE_ID, CAM_TOOLPATH_PANEL_ID
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar


CMD_NAME = 'dump_stepdown_expressions'
CMD_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_{CMD_NAME}'
CMD_Description = 'Display Hello World'
IS_PROMOTED = False
WORKSPACE_ID = CAM_WORKSPACE_ID
PANEL_ID = CAM_TOOLPATH_PANEL_ID
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

def start():
    cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED)
    add_handler(cmd_def.commandCreated, command_created)    

def stop():
    removeCommandFromToolbar(CMD_ID)

def command_created(args: adsk.core.CommandCreatedEventArgs):
    log(f'{CMD_NAME} Command Created Event')
    add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)

def command_execute(args: adsk.core.CommandEventArgs):
    log(f'{CMD_NAME} Command Execute Event')
    fusion = Fusion()
    try:
        run(fusion)
    except:
        handle_error(CMD_NAME)

def command_destroy(args: adsk.core.CommandEventArgs):
    log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
    
def create_setup(fusion : Fusion) -> adsk.cam.Setup:
    cam = fusion.getCAM()
    setups = cam.setups
    setupInput = setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    setupInput.models = [fusion.get_body()]
    # Define the setup properties and parameters.
    setupInput.name = 'My setup name'
    setupInput.stockMode = adsk.cam.SetupStockModes.RelativeBoxStock
    # # set offset mode
    setupInput.parameters.itemByName('job_stockOffsetMode').expression = "'simple'"
    # # set offset stock side
    setupInput.parameters.itemByName('job_stockOffsetSides').expression = '0 mm'
    # # set offset stock top
    setupInput.parameters.itemByName('job_stockOffsetTop').expression = '1 mm'
    # # set setup origin
    # setupInput.parameters.itemByName('wcs_origin_boxPoint').value.value = SetupWCSPoint.TOP_XMIN_YMIN.value
    # # Create the setup.
    setup = setups.add(setupInput)
    return setup

def run(fusion : Fusion):
    setup = create_setup(fusion)
    nothing = None
    items = [
    {"strategy" : "adaptive2d"     , "stepdown" : "maximumStepdown"  , "stepover" : "optimalLoad"}       ,
    {"strategy" : "pocket2d"       , "stepdown" : "maximumStepdown"  , "stepover" : "maximumStepover"}   ,
    {"strategy" : "face"           , "stepdown" : "maximumStepdown"  , "stepover" : "stepover"}          ,
    {"strategy" : "contour2d"      , "stepdown" : "finishingStepdown", "stepover" : "finishingStepover"} , # also has roughing parameters
    {"strategy" : "slot"           , "stepdown" : "maximumStepdown"  , "stepover" : nothing}             ,
    {"strategy" : "bore"           , "stepdown" : nothing            , "stepover" : "stepover"}          , # also has finishing parameters
    {"strategy" : "circular"       , "stepdown" : "maximumStepdown"  , "stepover" : "stepover"}          ,
    {"strategy" : "chamfer2d"      , "stepdown" : nothing            , "stepover" : nothing}             ,
    {"strategy" : "adaptive"       , "stepdown" : "maximumStepdown"  , "stepover" : "optimalLoad"}       ,
    {"strategy" : "pocket_clearing", "stepdown" : "maximumStepdown"  , "stepover" : "maximumStepover"}   ,
    {"strategy" : "flat"           , "stepdown" : "maximumStepdown"  , "stepover" : "stepover"}          ,
    {"strategy" : "parallel"       , "stepdown" : "maximumStepdown"  , "stepover" : "stepover"}          ,
    {"strategy" : "contour3d"      , "stepdown" : "maximumStepdown"  , "stepover" : nothing}             ,
    {"strategy" : "ramp"           , "stepdown" : "maximumStepdown"  , "stepover" : nothing}             ,
    ]
    ret = []
    for item in items:
        str = item["strategy"]
        inp = setup.operations.createInput(str)
        key_stepdown = item["stepdown"]
        key_stepover = item["stepover"]
        if key_stepover is None:
            expr_stepover = None
        else:
            expr_stepover = inp.parameters.itemByName(key_stepover).expression

        if key_stepdown is None:
            expr_stepdown = None
        else:
            expr_stepdown = inp.parameters.itemByName(key_stepdown).expression

        ret.append({
            "strategy" : str,
            "stepdown" : expr_stepdown,
            "stepover" : expr_stepover,
        })

    path = desktop_path("step_down_over.json")
    print(path)
    with open(path, "w") as file:
        json.dump(ret, file, indent=2)