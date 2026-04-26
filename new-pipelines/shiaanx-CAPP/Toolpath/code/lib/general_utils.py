#  Copyright 2022 by Autodesk, Inc.
#  Permission to use, copy, modify, and distribute this software in object code form
#  for any purpose and without fee is hereby granted, provided that the above copyright
#  notice appears in all copies and that both that copyright notice and the limited
#  warranty and restricted rights notice below appear in all supporting documentation.
#
#  AUTODESK PROVIDES THIS PROGRAM "AS IS" AND WITH ALL FAULTS. AUTODESK SPECIFICALLY
#  DISCLAIMS ANY IMPLIED WARRANTY OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR USE.
#  AUTODESK, INC. DOES NOT WARRANT THAT THE OPERATION OF THE PROGRAM WILL BE
#  UNINTERRUPTED OR ERROR FREE.

import sys
import traceback
import os
import adsk.fusion
import json
import secrets

from .coord_utils import get_tool_orientation

class UserException(Exception):
    def __init__(self, user_msg: str, details=None) -> None:
        self.user_msg = user_msg
        self.details = details
        if details is None:
            full_msg = user_msg
        else:
            full_msg = user_msg + "\n\n" + str(details)
        super().__init__(full_msg)

app = adsk.core.Application.get()
ui = app.userInterface

# Workspace IDs
CAM_WORKSPACE_ID = 'CAMEnvironment'
DESIGN_WORKSPACE_ID = 'FusionSolidEnvironment'

# Panel IDs
# https://forums.autodesk.com/t5/fusion-360-api-and-scripts/how-do-i-get-panel-name-strings/td-p/7536055
CAM_INSPECT_PANEL_ID = 'CAMInspectPanel'  # Manufacture -> Inspect
CAM_SETUP_PANEL_ID = 'CAMJobPanel'  # Manufacture -> Setup
CAM_TOOLPATH_PANEL_ID = 'CAMToolpathPanel'  # Manufacture -> Milling -> Toolpath
CAM_MILLING_TAB_ID = "MillingTab"

DESIGN_INSPECT_PANEL_ID = "InspectPanel"
DESIGN_TOOLPATH_PANEL_ID = "DESIGNToolpathPanel"  # Design -> Solid -> Toolpath
DESIGN_SOLID_TAB_ID = "SolidTab"
DESIGN_ASSEMBLY_TAB_ID = "AssemblyTab"
DESIGN_ASSEMBLY_TOOLPATH_PANEL_ID = "DESIGNAssemblyToolpathPanel"  # Design -> Assembly -> Toolpath

TOOLPATH_PANEL_NAME = "Toolpath"

WORKSPACE_PANEL_IDS = [
    (DESIGN_WORKSPACE_ID, DESIGN_SOLID_TAB_ID, DESIGN_TOOLPATH_PANEL_ID),
    (DESIGN_WORKSPACE_ID, DESIGN_ASSEMBLY_TAB_ID, DESIGN_ASSEMBLY_TOOLPATH_PANEL_ID),
    (CAM_WORKSPACE_ID, CAM_MILLING_TAB_ID, CAM_TOOLPATH_PANEL_ID),
]

def get_addin_version() -> str:
    version = load_json(addin_root_rpath("Toolpath.manifest"))["version"]
    parse_version(version) # for checks
    return version

def parse_version(version: str) -> tuple[int, int, int]:
    major_str, minor_str, patch_str = version.split('.')
    major = int(major_str)
    minor = int(minor_str)
    patch = int(patch_str)
    assert major  >= 0
    assert minor  >= 0
    assert patch  >= 0
    return (major, minor, patch)

# This is used when defining unique internal names for various UI elements
# that need a unique name. It's also recommended to use a company name as
# part of the ID to better ensure the ID is unique.
ADDIN_NAME = "Toolpath"
COMPANY_NAME = 'Toolpath CAM'

def something(*args):
    for arg in args:
        if arg is not None:
            return arg
    raise Exception("All arguments are None")

def log(message: str, level: adsk.core.LogLevels = adsk.core.LogLevels.InfoLogLevel, force_console: bool = False):
    """Utility function to easily handle logging in your app.

    Arguments:
    message -- The message to log.
    level -- The logging severity level.
    force_console -- Forces the message to be written to the Text Command window.
    """
    # Always print to console, only seen through IDE.
    print(message)

    # Log all errors to Fusion log file.
    if level == adsk.core.LogLevels.ErrorLogLevel:
        log_type = adsk.core.LogTypes.FileLogType
        app.log(message, level, log_type)

    # If config.DEBUG is True write all log messages to the console.
    if isdebug() or force_console:
        log_type = adsk.core.LogTypes.ConsoleLogType
        app.log(message, level, log_type)


def handle_error(name: str, show_message_box: bool = False):
    """Utility function to simplify error handling.

    Arguments:
    name -- A name used to label the error.
    show_message_box -- Indicates if the error should be shown in the message box.
                        If False, it will only be shown in the Text Command window
                        and logged to the log file.
    """

    log('===== Error =====', adsk.core.LogLevels.ErrorLogLevel)
    log(f'{name}\n{traceback.format_exc()}', adsk.core.LogLevels.ErrorLogLevel)

    # If desired you could show an error as a message box.
    if show_message_box:
        (typ, ex, tb) = sys.exc_info()
        if isinstance(ex, UserException):
            ui.messageBox(f'{ex.user_msg}')
        else:
            ui.messageBox(f'{name}\n{traceback.format_exc(limit=3)}')


def addCommandToToolbar(CMD_ID,CMD_NAME,CMD_Description, ICON_FOLDER, IS_PROMOTED: bool,addSeparator: bool=False):
    # Add trailing newlines to balance out Fusion's top padding in tooltips
    padded_description = CMD_Description + "\n\n"
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, padded_description, ICON_FOLDER
        )
    for (WORKSPACE_ID, TAB_ID, PANEL_ID) in WORKSPACE_PANEL_IDS:
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if not tab:
            continue
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            continue

        if addSeparator:
            panel.controls.addSeparator() # we want a horizontal line above the command
        control = panel.controls.addCommand(cmd_def)
        control.isPromoted = IS_PROMOTED

    return cmd_def

def rename_toolbar_command(CMD_ID,new_name):

    for (WORKSPACE_ID, TAB_ID, PANEL_ID) in WORKSPACE_PANEL_IDS:
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        control = panel.controls.itemById(CMD_ID)
        cmd_def = control.commandDefinition

        if cmd_def:
            cmd_def.name = new_name

def get_toolbar_command_text(CMD_ID,workspace_panel_id_idx=0):

    (WORKSPACE_ID, TAB_ID, PANEL_ID) = WORKSPACE_PANEL_IDS[workspace_panel_id_idx]
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if not workspace:
        return None
    tab = workspace.toolbarTabs.itemById(TAB_ID)
    if not tab:
        return None
    panel = tab.toolbarPanels.itemById(PANEL_ID)
    if not panel:
        return None
    control = panel.controls.itemById(CMD_ID)
    if not control:
        return None
    cmd_def = control.commandDefinition

    if cmd_def:
        return cmd_def.name

    return None

def removeCommandFromToolbar(CMD_ID):
    cmdDef = ui.commandDefinitions.itemById(CMD_ID)
    if cmdDef:
        cmdDef.deleteMe()
    for (WORKSPACE_ID, TAB_ID, PANEL_ID) in WORKSPACE_PANEL_IDS:
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        if not workspace:
            continue
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if not tab:
            continue
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            continue
        command_control = panel.controls.itemById(CMD_ID)
        if command_control:
            command_control.deleteMe()


def ancestor_dir(path, n):
    for i in range(n):
        path = os.path.dirname(path)
    return path
def addin_code_rpath(*rpath) -> str:
    root = ancestor_dir(__file__, 2)
    return os.path.join(root, *rpath)

def addin_root_rpath(*rpath) -> str:
    root = ancestor_dir(__file__, 3)
    return os.path.join(root, *rpath)

def fusiontp_path(*rpath) -> str:
    root = ancestor_dir(__file__, 4)
    return os.path.join(root, *rpath)

def julia_test_data_path(*path) -> str:
    return fusiontp_path("test", "data", *path)

def test_path(*rpath) -> str:
    return resource_path("test_data", *rpath)

def resource_path(*rpath) -> str:
    return addin_code_rpath("resources", *rpath)

def desktop_path(*rpath) -> str:
    return os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", *rpath)

def datapath(*rpath) -> str:
    return addin_code_rpath("data", *rpath)

def load_json(path: str):
    if not os.path.exists(path):
        raise Exception(f"File not found: {path}")
    with open(path) as file:
        return json.load(file)

def save_json(path : str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as file:
        json.dump(data, file, indent=2)

CURRENT_CONFIG = None
def load_config() -> dict:
    path = addin_code_rpath("config_template.json")
    config = load_json(path)
    path_user = addin_root_rpath("config.json")
    if os.path.exists(path_user):
        user_config = load_json(path_user)
        config.update(user_config)
    else:
        default_config = {}
        save_json(path_user, default_config)
    config["use_FusionTP_server"] = False

    # Generate device_id if not set
    if not config.get("device_id"):
        config["device_id"] = secrets.token_hex(16)  # 32 character hex string
        save_json(path_user, config)

    global CURRENT_CONFIG
    CURRENT_CONFIG = config
    return config

CURRENT_CONFIG = load_config()

# Flag that indicates to run in Debug mode or not. When running in Debug mode
# more information is written to the Text Command window. Generally, it's useful
# to set this to True while developing an add-in and set it to False when you
# are ready to distribute it.
def isdebug() -> bool:
    global CURRENT_CONFIG
    return CURRENT_CONFIG["debug"] == True

def persistent_state_path(*args):
    return addin_root_rpath("persistent_state", *args)

def save_config(config):
    path_user = addin_root_rpath("config.json")
    if os.path.exists(path_user):
        current_config = load_json(path_user)
        current_config.update(config)
    else:
        current_config = config
    global CURRENT_CONFIG
    CURRENT_CONFIG = current_config
    save_json(path_user, current_config)



def range_first_last_len(first, last, length):
    step = (last - first) / (length -1)
    return [first + i*step for i in range(length)]


def load_toollib(path : str) -> adsk.cam.ToolLibrary:
    if not isinstance(path, str):
        raise Exception(f"Toollib path must be a string, not {path} : {type(path)}")
    if not os.path.exists(path):
        raise Exception(f"Toollib path does not exist: {path}")
    with open(path, "r") as file:
        json = file.read()
    toollib = adsk.cam.ToolLibrary.createFromJson(json)
    return toollib

def get_stock_bounding_box(setup : adsk.cam.Setup):
    """
    Return the bounding box of the stock in WCS coordinates.
    The unit seems to be cm, the internal length unit of fusion.
    """
    x_min : float = setup.parameters.itemByName("stockXLow").value.value
    y_min : float = setup.parameters.itemByName("stockYLow").value.value
    z_min : float = setup.parameters.itemByName("stockZLow").value.value
    x_max : float = setup.parameters.itemByName("stockXHigh").value.value
    y_max : float = setup.parameters.itemByName("stockYHigh").value.value
    z_max : float = setup.parameters.itemByName("stockZHigh").value.value
    lims = ((x_min, x_max), (y_min, y_max), (z_min, z_max))
    return lims


def get_parameter(obj, name : str):
    assert isinstance(name, str)
    p = obj.parameters.itemByName(name)
    if p is None:
        names = [p.name for p in obj.parameters]
        names.sort()
        msg = f"""
        Parameter not found.
        name = {name}
        available parameters:
        {names}
        """
        raise Exception(msg)
    assert isinstance(p, adsk.cam.CAMParameter)
    return p

def set_parameter(obj, key, value, body_occurrence=None, sketch_book=None):
    if key == "bottomHeight_ref":
        pass
    p = obj.parameters.itemByName(key)
    if isinstance(value, dict):
        subtypekey = value["subtypekey"]
        pass
    else:
        subtypekey = None

    if p is None:
        # BACKWARDS COMPATIBILITY: Handle restMaterialShadow/restMaterialShadowPocket parameter name.
        # Fusion unified the "reduce air cutting" parameter to restMaterialShadow for all operation
        # types (including pocket_clearing). Older Fusion versions used restMaterialShadowPocket
        # for pocket_clearing operations. The Julia code may also send the wrong parameter name
        # if the plan_case doesn't match the actual Fusion operation type.
        # This fallback tries the alternate parameter in both directions.
        # See commit ffc9e4289c for history.
        if key == "restMaterialShadowPocket":
            alt_key = "restMaterialShadow"
            p = obj.parameters.itemByName(alt_key)
            if p is not None:
                key = alt_key
        elif key == "restMaterialShadow":
            alt_key = "restMaterialShadowPocket"
            p = obj.parameters.itemByName(alt_key)
            if p is not None:
                key = alt_key
        # END BACKWARDS COMPATIBILITY

        if p is None:
            if subtypekey == "TryParameterValue":
                return
            else:
                raise Exception(f"Parameter is None: {key = }")

    if subtypekey == None:
        try:
            p.value.value = value
        except Exception as error:
            msg = f"""
            Could not set parameter value:
            {key =}
            {value = }
            {error = }
            """
            raise Exception(msg)
    elif subtypekey == "TryParameterValue":
        v = value["inner"]
        try:
            p.value.value = v
        except Exception as error:
            pass
        return
    elif subtypekey == "Expression":
        expression = value["expression"]
        try:
            p.expression = expression
        except Exception as error:
            msg = f"""
            Could not set parameter expression:
            {key =}
            {expression = }
            {error = }
            """
            raise Exception(msg)
    elif subtypekey == "Sketch_points":
        if sketch_book is None:
            raise Exception("sketch_book is None")
        sketch_id = value["sketch_id"]
        sel = sketch_book.getSelectable(sketch_id)
        p.value.value = sel

    elif subtypekey == "AbsoluteCoordDef":
        # AbsoluteCoordDef carries the full coordinate system.
        # Set both Z and X orientation axes from it.
        tool_orientation = get_tool_orientation(value, body_occurrence)

        # p = view_orientation_axisZ
        valObj = adsk.cam.CadObjectParameterValue.cast(p.value)
        zAxes = valObj.value
        zAxes.append(tool_orientation.zAxis)
        valObj.value = zAxes

        p_x = obj.parameters.itemByName("view_orientation_axisX")
        if p_x is not None:
            valObj_x = adsk.cam.CadObjectParameterValue.cast(p_x.value)
            xAxes = valObj_x.value
            xAxes.append(tool_orientation.xAxis)
            valObj_x.value = xAxes

    else:
        raise Exception(f"Unexpected subtypekey: {subtypekey}")

    return p

def set_member(obj, key, value):
    setattr(obj, key, value)
    return obj

def set_members(obj, d : dict):
    for (k, v) in d.items():
        set_member(obj, k, v)
    return obj

def set_parameters(obj, d : dict, body_occurrence=None, sketch_book=None, config=None):
    skip_parameters = []
    if config is not None:
        enable_tool_orientation = config["enable_tool_orientation"] or config["enable_multi_axis_setups"]
        if not enable_tool_orientation:
            skip_parameters = ["overrideToolView","view_orientation_mode","view_orientation_axisZ","view_orientation_axisX"]

    for (k, v) in d.items():
        if k in skip_parameters:
            continue
        set_parameter(obj, k, v, body_occurrence=body_occurrence, sketch_book=sketch_book)
    return obj

def only(itr):
    assert len(itr) == 1
    return itr[0]


def compress_step_content(step_str: str) -> tuple[str, dict]:
    """
    Compress STEP file content using gzip and base64 encode for JSON transport.

    Args:
        step_str: The STEP file content as a string

    Returns:
        tuple of (compressed_b64_string, compression_info_dict)

        compression_info_dict contains:
        - compression: "gzip+base64" (the method used)
        - original_size: size in bytes before compression
        - compressed_size: size in bytes after compression (before base64)
        - ratio: compression ratio (original/compressed)
    """
    import gzip
    import base64

    original_bytes = step_str.encode('utf-8')
    original_size = len(original_bytes)

    # Compress with maximum compression level
    compressed_bytes = gzip.compress(original_bytes, compresslevel=9)
    compressed_size = len(compressed_bytes)

    # Base64 encode for safe JSON transport
    compressed_b64 = base64.b64encode(compressed_bytes).decode('ascii')

    ratio = original_size / compressed_size if compressed_size > 0 else 0

    compression_info = {
        "compression": "gzip+base64",
        "original_size": original_size,
        "compressed_size": compressed_size,
        "ratio": ratio,
    }

    return compressed_b64, compression_info


def decompress_step_content(compressed_b64: str) -> str:
    """
    Decompress STEP file content that was compressed with compress_step_content.

    Args:
        compressed_b64: The gzip+base64 compressed STEP content

    Returns:
        The original STEP file content as a string
    """
    import gzip
    import base64

    compressed_bytes = base64.b64decode(compressed_b64)
    original_bytes = gzip.decompress(compressed_bytes)
    return original_bytes.decode('utf-8')


# =============================================================================
# STEP File Parsing Utilities
# =============================================================================

# Regex pattern to extract solid body names from STEP files.
# Matches both MANIFOLD_SOLID_BREP (standard solids) and BREP_WITH_VOIDS (solids with internal voids)
STEP_SOLID_BODY_PATTERN = r"(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(\s*'([^']*)'"

# Regex pattern to extract product/component names from STEP files
STEP_PRODUCT_PATTERN = r"PRODUCT\s*\(\s*'([^']*)'"

# Regex pattern to extract shape representation names from STEP files
STEP_SHAPE_REP_PATTERN = r"ADVANCED_BREP_SHAPE_REPRESENTATION\s*\(\s*'([^']*)'"


def extract_step_body_names(step_content: str) -> list:
    """
    Extract solid body names from STEP file content.

    Args:
        step_content: Raw STEP file content as a string

    Returns:
        List of non-empty body names found in the STEP file
    """
    import re
    names = re.findall(STEP_SOLID_BODY_PATTERN, step_content)
    return [n for n in names if n]


def extract_step_product_names(step_content: str) -> list:
    """
    Extract product/component names from STEP file content.

    Args:
        step_content: Raw STEP file content as a string

    Returns:
        List of product names found in the STEP file
    """
    import re
    return re.findall(STEP_PRODUCT_PATTERN, step_content)


def extract_step_shape_rep_names(step_content: str) -> list:
    """
    Extract ADVANCED_BREP_SHAPE_REPRESENTATION names from STEP file content.

    Args:
        step_content: Raw STEP file content as a string

    Returns:
        List of shape representation names found in the STEP file
    """
    import re
    return re.findall(STEP_SHAPE_REP_PATTERN, step_content)


def analyze_step_content(step_content: str) -> dict:
    """
    Analyze STEP file content and extract all relevant names.

    Args:
        step_content: Raw STEP file content as a string

    Returns:
        Dictionary with:
        - product_names: List of PRODUCT names
        - solid_body_names: List of solid body names (non-empty only)
        - shape_rep_names: List of ADVANCED_BREP_SHAPE_REPRESENTATION names
        - all_names: Combined list of all unique names
    """
    product_names = extract_step_product_names(step_content)
    solid_body_names = extract_step_body_names(step_content)
    shape_rep_names = extract_step_shape_rep_names(step_content)

    all_names = list(set(product_names + solid_body_names + shape_rep_names))

    return {
        "product_names": product_names,
        "solid_body_names": solid_body_names,
        "shape_rep_names": shape_rep_names,
        "all_names": all_names,
    }
