import adsk.core
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, handle_error, get_addin_version
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
import traceback

CMD_NAME = 'About'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'About the Toolpath Add-In'
IS_PROMOTED = False

ICON_FOLDER = resource_path("about", '')
local_handlers = []

toollib_checkboxes = []
toollib_url_by_name = None

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
        eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
        cmd = eventArgs.command
        inputs = cmd.commandInputs
        # Registration
        exec_handler = CommandExecuteHandler()
        cmd.execute.add(exec_handler)
        local_handlers.append(exec_handler)

class CommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        fusion = Fusion()
        ui = None
        try:
            ui = fusion.getUI()
            # TODO company logo
            # and link
            msg_box = ui.messageBox(
                f"""
                Toolpath Labs, Inc.
                www.toolpath.com

                Add-In Version {get_addin_version()}
                Terms of Service (https://toolpath.com/legal/terms)
                Copyright © 2025, Toolpath Labs Inc.
                """,
                "About Toolpath",
                adsk.core.MessageBoxButtonTypes.OKButtonType,
            )
        except:
            handle_error("Unreachable")



