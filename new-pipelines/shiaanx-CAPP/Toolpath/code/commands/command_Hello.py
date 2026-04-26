import adsk.core

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar

import traceback

CMD_NAME = 'Hello World'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Display a greeting for testing purposes.'
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

toollib_checkboxes = []
toollib_url_by_name = None


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

def onCommandExecute(args):
    url = "https://support.toolpath.com/"
    ui = Fusion().getUI()
    msg_box = ui.messageBox(
        f"""Hello World!""",
        "Hello",
        adsk.core.MessageBoxButtonTypes.OKButtonType,
    )