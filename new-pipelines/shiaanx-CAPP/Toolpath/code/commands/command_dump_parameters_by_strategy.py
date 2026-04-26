import adsk.core

from ..lib.event_utils import add_handler
from ..lib.general_utils import COMPANY_NAME, ADDIN_NAME, resource_path, log, handle_error 
from ..lib.fusion_utils import Fusion

from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar, addin_root_rpath
import json

CMD_NAME = 'dump_parameters_by_strategy'
CMD_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_{CMD_NAME}'
CMD_Description = 'Display Hello World'
IS_PROMOTED = False
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
    startegies = [str.name for str in setup.operations.compatibleStrategies]
    ret = {}
    for str in startegies:
        inp = setup.operations.createInput(str)
        params = [p.name for p in inp.parameters]
        ret[str] = params

    path = addin_root_rpath("..", "data", "params_by_strategy.json")
    print(path)
    with open(path, "w") as file:
        json.dump(ret, file, indent=2)