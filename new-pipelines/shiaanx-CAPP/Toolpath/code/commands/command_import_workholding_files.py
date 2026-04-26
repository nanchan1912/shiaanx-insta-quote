import adsk.core
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
from ..lib.workholding_utils import DEFAULT_WORKHOLDING_FOLDER_NAME,get_active_project,create_new_folder,file_exists_in_folder,download_f3d_file,upload_to_fusion_cloud
import traceback

CMD_NAME = 'Sync Workholding Assets'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Sync neccesary workholding assets with Toolpath.'
ICON_FOLDER = resource_path("sync_workholding", '')
local_handlers = []

toollib_checkboxes = []
toollib_url_by_name = None


def import_workholding_files(fusion, progressDialog):
    folder_name = DEFAULT_WORKHOLDING_FOLDER_NAME
    ui = Fusion().getUI() 
    try:
        active_project = get_active_project()
    except Exception as e:
        ui.messageBox("Unable to get active project. Try changing to a project where you have write access.")
        log(traceback.format_exc())
        return None
    try:    
        target_folder = create_new_folder(folder_name,active_project)

        vise_file_list = [
            "Vise_Ancestor" + ".f3d",
            "Narrow-155mm-Centering-96mmStud" + ".f3d",
            "Narrow-Orange-Fixed-96mmStud" + ".f3d",
            "Narrow-6in-Fixed-96mmStud" + ".f3d",        
            "Wide-155mm-Centering-96mmStud" + ".f3d"
        ]

        clamping_file_list = [
            "Clamping_Ancestor" + ".f3d",
            "1x1-Square-96mm-Plate" + ".f3d",
            "1x1-Round-96mm-52mm-Plate" + ".f3d",
            "3x3_HWR_Pallet_No_Risers" + ".f3d"
        ]

        progressDialog.progressValue = 0
        progressDialog.maximumValue = len(vise_file_list)
        progressDialog.message = f'Downloading vise files: Current File: %v, Total files: %m'
        files_updated = False
        for (i,vise_file) in enumerate(vise_file_list):
            if not file_exists_in_folder(vise_file,target_folder):
                files_updated = True
                file_data = download_f3d_file(vise_file,category="vise")
                upload_to_fusion_cloud(file_data,vise_file,target_folder)
            progressDialog.progressValue = i+1

        progressDialog.progressValue = 0
        progressDialog.maximumValue = len(clamping_file_list)
        progressDialog.message = f'Downloading clamping files: Current File: %v, Total files: %m'
        for (i,clamping_file) in enumerate(clamping_file_list):
            if not file_exists_in_folder(clamping_file,target_folder):
                files_updated = True
                file_data = download_f3d_file(clamping_file,category="fixture_plate")
                upload_to_fusion_cloud(file_data,clamping_file,target_folder)
            progressDialog.progressValue = i+1
        progressDialog.hide()

        if not files_updated:
            fusion = Fusion()
            ui = fusion.getUI()
            ui.messageBox("Workholding files are up to date.")
    except Exception as e:
        log(traceback.format_exc())       
        if ui:
            ui.messageBox("Unable to write to current project.")
        
        
        return

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
    fusion = Fusion()
    progressDialog = fusion.getUI().createProgressDialog()
    progressDialog.show("Workholding Download","Syncing Toolpath Workholding",0,1)
    #progressDialog.message = "Syncing Toolpath Workholding"
    progressDialog.cancelButtonText = 'Cancel'
    progressDialog.isBackgroundTranslucent = False
    progressDialog.isCancelButtonShown = False
    progressDialog.progressValue = 0
    
    def needs_cancel():
        adsk.doEvents()
        if progressDialog.wasCancelled:
            log("needs_cancel")
            return True
        else:
            return False


    if needs_cancel(): return
    import_workholding_files(fusion=fusion, progressDialog=progressDialog)

add_handler