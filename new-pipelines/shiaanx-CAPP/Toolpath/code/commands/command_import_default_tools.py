import adsk.core

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, addin_code_rpath
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
import traceback
import os
import adsk.cam

CMD_NAME = 'import default tools'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Display a greeting for testing purposes.'
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

toollib_checkboxes = []
toollib_url_by_name = None

def import_default_tools():
    name = "Toolpath Default - Do Not Edit"
    path = addin_code_rpath("toolpath_generic_tools.json")
    if not os.path.exists(path):
        raise ValueError(f"{path} does not exist")
    camManager = adsk.cam.CAMManager.get()
    libraryManager = camManager.libraryManager
    toolLibraries = libraryManager.toolLibraries
    localFolder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
    # delete the lib if necessary
    for url in toolLibraries.childAssetURLs(localFolder):
        print(url.pathName)
        if url.leafName == name:
            toolLibraries.deleteAsset(url)
    #
    with open(path, "r") as file:
        json = file.read()
    toollib = adsk.cam.ToolLibrary.createFromJson(json)
    toolLibraries.importToolLibrary(toollib, localFolder, f'{name}.tools')

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
    import_default_tools()

add_handler