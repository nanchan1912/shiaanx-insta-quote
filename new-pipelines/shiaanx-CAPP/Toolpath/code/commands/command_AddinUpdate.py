import adsk.core
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, handle_error, parse_version,rename_toolbar_command,get_toolbar_command_text
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
from ..lib.client import Client
from ..lib.update_utils import download_update
import traceback

# Fusion UI: Replace "plugin" with "Add-in" #2130 
# https://github.com/toolpath/ToolpathPackages/issues/2130
CMD_NAME = 'Check for Updates'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Check for the latest version of the Toolpath Add-In.'
ICON_FOLDER = resource_path("update_addin", '')
prefix = "Update Available"
local_handlers = []

toollib_checkboxes = []
toollib_url_by_name = None

def start():
    ui = None
    try:
        fusion = Fusion()
        app = fusion.getApplication()
        ui = fusion.getUI()

        cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED=False,addSeparator=True)

        client = Client()
        def commandCheckForUpdates(args):
            check_for_updates(client)
   
        add_handler(cmd_def.commandCreated, onCommandCreated, local_handlers=local_handlers)
        add_handler(app.startupCompleted,commandCheckForUpdates,local_handlers=local_handlers)      
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

def check_for_updates(client : Client):
    if not client.config["enable_updates"]: 
        log("Skipping update check")
        return True
    try:
        data = {
            "subtypekey" : "RequestPluginVersion",
        }
        resp = client.request(data)["data"]
        cloud_version = resp["code_version"]
        addin_version = client.addin_version

        cloud_major, cloud_minor, cloud_patch = parse_version(cloud_version)
        addin_major, addin_minor, addin_patch = parse_version(addin_version)

        msg = None
        is_breaking_change = (cloud_major > addin_major) or ((cloud_major >= addin_major) and (cloud_minor > addin_minor))
        is_change = (cloud_major, cloud_minor, cloud_patch) != (addin_major, addin_minor, addin_patch)
        if is_breaking_change:
            msg = f"""
            The current Toolpath addin version is outdated. It is highly recommended to restart the addin, to automatically install the latest version:
            Current version: {addin_version}
            Latest version : {cloud_version}
            """
        elif is_change:
            msg = f"""
            A new Toolpath addin version is available. It will be automatically installed on the next addin restart.
            Current version: {addin_version}
            Latest version : {cloud_version}
            """
        if is_breaking_change or is_change:
            cmd_text = f"{prefix} ({cloud_version})"
            rename_toolbar_command(CMD_ID,cmd_text)
        else:
            cmd_text = f"{CMD_NAME}"
            rename_toolbar_command(CMD_ID,cmd_text)
        
        if msg is None:
            return False
        else:
            download_update(verbose=False)
        if is_breaking_change:
            fusion = Fusion()
            ui = fusion.getUI()
            ui.messageBox(
                msg
                ,
                "Addin Update",
                adsk.core.MessageBoxButtonTypes.OKButtonType,
                )
        return True
    except:
        handle_error("Unreachable")

def onCommandExecute(args):
    cmd_name = get_toolbar_command_text(CMD_ID)
    if prefix in cmd_name:
        msg = """
            A new Toolpath addin version is available. It will be automatically installed on the next addin restart.
        """
        fusion = Fusion()
        ui = fusion.getUI()
        ui.messageBox(
            msg
            ,
            "Addin Update",
            adsk.core.MessageBoxButtonTypes.OKButtonType,
        )
    else:
        new_version = download_update(verbose=True)
        if new_version is not None:
            #update the command
            cmd_text = f"{prefix} ({new_version})"
            rename_toolbar_command(CMD_ID,cmd_text)

def onCommandCreated(args):
    eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
    cmd = eventArgs.command
    inputs = cmd.commandInputs

    # Registration
    add_handler(cmd.execute, onCommandExecute, local_handlers=local_handlers)

