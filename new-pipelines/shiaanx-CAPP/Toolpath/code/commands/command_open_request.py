import adsk.core
import adsk.cam

from ..lib.event_utils import SimpleCommand
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import get_parameter, set_parameter, load_config, julia_test_data_path, load_json
from ..lib.setup_utils import create_setup
import os
import tempfile
import base64
from .command_RequestFusionOps import RequestFusionOpsQA, UserSpecifiedSetip, UserSpecifiedSetips, unjsonify_toollib, AutoSetips
from types import SimpleNamespace

def create_setips(fusion : Fusion, setips_json) -> UserSpecifiedSetips:
    assert setips_json["subtypekey"] == "UserSpecifiedSetips"
    setips = []
    for setip_json in setips_json["setips"]:
        fusion_setup = create_setup(fusion, setip_json)
        compute_fusionops = setip_json["compute_fusionops"]
        setup = UserSpecifiedSetip(fusion_setup, compute_fusionops)
        if get_parameter(fusion_setup, "job_stockMode").value.value == "previoussetup":
            set_parameter(fusion_setup, "job_continueMachining", True)
        setips.append(setup)
    return UserSpecifiedSetips(setips)

def create_setips_auto(fusion : Fusion, setips_json, body=None) -> AutoSetips:
    if body is None:
        body = fusion.get_body()
    return AutoSetips(
        body=body,
        fusion=fusion,
    )

def get_setup_by_operationId(fusion : Fusion, operationId : str) -> adsk.cam.Setup:
    assert isinstance(operationId, str)
    cam = fusion.getCAM()
    for setup in cam.setups:
        if hex(setup.operationId) == operationId:
            return setup

    operationIds = [hex(setup.operationId) for setup in cam.setups]
    raise Exception(f"Could not find setup with operationId {operationId}. Possible values are: {operationIds}")

def create_setips_f3d(fusion : Fusion, setips_json) -> UserSpecifiedSetips:
    assert setips_json["subtypekey"] == "UserSpecifiedSetips"
    setips = []

    for setip_json in setips_json["setips"]:
        operationId = setip_json["operationId"]
        obj = get_setup_by_operationId(fusion, operationId)
        compute_fusionops = setip_json["compute_fusionops"]
        setip = UserSpecifiedSetip(obj, compute_fusionops)
        setips.append(setip)
    return UserSpecifiedSetips(setips)

def unwrap_lambda_request(req_json):
    if "request" in req_json.keys():
        req_json = req_json["request"]
    if "request" in req_json.keys():
        req_json = req_json["request"]
    return req_json

def import_request_to_new_doc(fusion : Fusion, req_path : str, use_f3d : bool) -> SimpleNamespace:
    req_json = load_json(req_path) 
    req_json = unwrap_lambda_request(req_json)
    toollibs_json = req_json["tool_libraries"]

    app = fusion.getApplication()
    ui  = app.userInterface
    importManager = app.importManager
    file_name, ext = os.path.splitext(os.path.basename(req_path))
    
    with tempfile.TemporaryDirectory() as tmpdir:
        if use_f3d:
            f3d_base64  = req_json["f3d_content_base64"]
            binary_data = base64.b64decode(f3d_base64)
            path = os.path.join(tmpdir, file_name + ".f3d")
            with open(path, "wb") as file:
                file.write(binary_data)
            options = importManager.createFusionArchiveImportOptions(path)
        else:
            step_data = req_json["step_file_content"]
            step_path = os.path.join(tmpdir, file_name + ".step")
            open(step_path, "w").write(step_data)
            options = importManager.createSTEPImportOptions(step_path)
            # TODO coordinates are wrong here, we need to apply 
            # the stepCoordinateSystem from req_json
            subtypekey = req_json["setips"]["subtypekey"]
            std_coordsys = {'origin': [0, 0, 0], 'xaxis': [1, 0, 0], 'yaxis': [0, 1, 0], 'zaxis': [0, 0, 1]}
            if subtypekey in ("AutoSetips", "AutoSetipsUsingFacets"):
                coordsys = std_coordsys
            elif subtypekey == "UserSpecifiedSetips":
                coordsys = req_json["setips"]["setips"][0]["stepCoordinateSystem_cm"]
            else:
                raise Exception(f"Unreachable {subtypekey}")

            if coordsys != std_coordsys:
                msg = f"""
                TODO support stepCoordinateSystem_cm for step file import.
                {coordsys}
                """
                raise Exception(msg)

        doc = importManager.importToNewDocument(options)
        doc.name = file_name

        camWS = ui.workspaces.itemById('CAMEnvironment') 
        camWS.activate()
        fusion = Fusion(doc)
        subtypekey = req_json["setips"]["subtypekey"] 
        if use_f3d and (subtypekey == "UserSpecifiedSetips"):
            setips = create_setips_f3d(fusion, req_json["setips"])
        elif (subtypekey == "UserSpecifiedSetips"):
            setips = create_setips(fusion, req_json["setips"])
        elif (subtypekey == "AutoSetips", "AutoSetipsUsingFacets" ):
            setips = create_setips_auto(fusion, req_json["setips"])
        else:
            raise Exception(f"Unreachable {subtypekey}")

    toollibs = [unjsonify_toollib(json) for json in toollibs_json]
    config = load_config()
    # config["run_QA_with_CAM"] = False
    config["use_FusionTP_server"] = True
    config.update(req_json)
    request = RequestFusionOpsQA(fusion=fusion,
        config=config,
        toollibs=toollibs,
        toollibs_json=toollibs_json,
        setips = setips,
        preset_naming=req_json["preset_naming"],
    )
    return SimpleNamespace(request=request, doc=doc, request_json=req_json)


class Cmd(SimpleCommand):
    def __init__(self):
        super().__init__(name='open request', description='Loads fusion request.')

    def run(self,fusion : Fusion):
        app = adsk.core.Application.get()
        ui = app.userInterface

        # Show a file dialog
        file_dialog = ui.createFileDialog()
        file_dialog.title = "Select a request file"
        file_dialog.filter = "Json files (*.json) | *.json"
        file_dialog.initialDirectory = julia_test_data_path("RequestFusionOps")
        result_file_dialog = file_dialog.showOpen()
        if not result_file_dialog == adsk.core.DialogResults.DialogOK:
           ui.messageBox("File selection canceled.")
           return
        req_path = file_dialog.filename
        
        dialog_result = ui.messageBox("""
            Use .f3d from request (Yes) ? 
            Otherwise only other properties of the request will be used. 
            This can be useful for debugging, or if the .f3d is not available or broken.
            """, 
            "Use .f3d from request?", 
            adsk.core.MessageBoxButtonTypes.YesNoButtonType,
        )
        if dialog_result == adsk.core.DialogResults.DialogYes:
            use_f3d = True
        elif dialog_result == adsk.core.DialogResults.DialogNo:
            use_f3d = False
        else:
            raise Exception(f"Unexpected dialog result {dialog_result}.")

        imported = import_request_to_new_doc(fusion, req_path, use_f3d=use_f3d)
        
        dialog_result = ui.messageBox("""
            Send request to the backend?
            """, 
            "Send request to the backend to create the operations?", 
            adsk.core.MessageBoxButtonTypes.YesNoButtonType,
        )
        if dialog_result == adsk.core.DialogResults.DialogYes:
            create_ops = True
        elif dialog_result == adsk.core.DialogResults.DialogNo:
            create_ops = False
        else:
            raise Exception(f"Unexpected dialog result {dialog_result}.")

        if not create_ops:
            # TODO import tool lib ?
            camManager = adsk.cam.CAMManager.get()
            libraryManager = camManager.libraryManager
            toolLibraries = libraryManager.toolLibraries
            # localFolder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
            # file_name, ext = os.path.splitext(os.path.basename(req_path))
            # toolLibraries.importToolLibrary(req.toollib, localFolder, str(file_name) +'_tool_lib.tools')
            return

        req = imported.request
        doc = imported.doc
        req.execute_and_materialize(doc=doc)