import adsk.core
import traceback
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, handle_error
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar

CMD_NAME = 'Options'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Configure Toolpath Add-In options'
IS_PROMOTED = False

ICON_FOLDER = resource_path("options", '')
local_handlers = []


def start():
    ui = None
    try:
        fusion = Fusion()
        ui = fusion.getUI()

        cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED)

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
    try:
        ui = Fusion().getUI()

        # Get the SendToToolpath instance from the commands module and use its palette method
        from . import command_send_to_toolpath
        # Find the existing SendToToolpath instance
        from . import commands
        for cmd in commands:
            if isinstance(cmd, command_send_to_toolpath.SendToToolpath):
                cmd._open_toolpath_palette(ui, route="#/options")
                break

        # Auto-execute to close the command immediately (palette stays open)
        eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
        eventArgs.command.isAutoExecute = True
    except:
        handle_error(traceback.format_exc())
