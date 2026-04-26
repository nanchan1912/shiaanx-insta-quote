import adsk.core

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import COMPANY_NAME, ADDIN_NAME, resource_path, log, handle_error, get_stock_bounding_box
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
from ..lib.setup_utils import create_setup


CMD_NAME = 'debug_setup'
CMD_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_{CMD_NAME}'
CMD_Description = 'Debug the currently selected setup'
IS_PROMOTED = False
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

def start():
    ui = Fusion().getUI()
    cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED)
    add_handler(cmd_def.commandCreated, command_created)


def stop():
    ui = Fusion().getUI()
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

def run(fusion : Fusion):
    parameters = {
        'workCoordinateSystem_mm' : {
            "xaxis" : [1, 0, 0],
            "yaxis" : [0, 1, 0],
            "zaxis" : [0, 0, 1],
            "origin" : [0, 0, 0],
    },
        "stockXLow" : -10, 
        "stockYLow" : -10,
        "stockZLow" : -10,     
        "stockXHigh" : 10,    
        "stockYHigh" : 10,    
        "stockZHigh" : 10,    
        "stockMode": "RelativeBoxStock",
        "job_continueMachining" : False,
    }
    setup = create_setup(fusion, parameters)
    
    print(get_stock_bounding_box(setup))
    origin, xaxis, yaxis, zaxis = setup.workCoordinateSystem.getAsCoordinateSystem()
    return None