import adsk.core
import traceback
from .general_utils import log

# Schema defining which config options are editable in the UI
# Options are organized by category (top-level) and group (sub-level)
# Options with requires_restart=True need plugin restart to take effect
OPTIONS_SCHEMA = [
    {
        "key": "enable_multi_axis_setups",
        "type": "bool",
        "label": "Enable 3+2 Setups",
        "tooltip": "Allow generation of 3+2 axis setups for multi-sided machining",
        "category": "Experimental Features",
        "group": "Multi-Axis",
        "requires_restart": False,
    },
    # {
    #     "key": "enable_command_make_softjaws",
    #     "type": "bool",
    #     "label": "Enable create softjaw command",
    #     "tooltip": "Allow automatic creation of softjaws through a new plugin command",
    #     "category": "Experimental Features",
    #     "group": "Setups",
    #     "requires_restart": False,
    # },
    # {
    #     "key": "enable_command_setup_builder",
    #     "type": "bool",
    #     "label": "Enable Setup Builder and Rigger commands",
    #     "tooltip": "Helpers to build flexible workholding setups and to rig the vised, plates, and bodies for it",
    #     "category": "Experimental Features",
    #     "group": "Setups",
    #     "requires_restart": False,
    # },
    {
        "key": "enable_stock_selection",
        "type": "bool",
        "label": "Enable Stock Body Selection",
        "tooltip": "Show an optional stock body selector in Model Body mode when sending parts to Toolpath",
        "category": "Experimental Features",
        "group": "Setups",
        "requires_restart": False,
    },
    {
        "key": "enable_command_ai_cam",
        "type": "bool",
        "label": "Enable One-Shot CAM",
        "tooltip": "Experimental workflow to automatically program the part without the need to visit the Toolpath web app. Will program parts in a single setup.",
        "category": "Experimental Features",
        "group": "One-Shot CAM",
        "requires_restart": False,
    }
]


def check_requires_restart(old_values: dict, new_values: dict) -> bool:
    """Check if any changed options require a restart."""
    for opt in OPTIONS_SCHEMA:
        if opt.get("requires_restart", False):
            key = opt["key"]
            if old_values.get(key) != new_values.get(key):
                return True
    return False


def get_options_from_config(config: dict) -> dict:
    """Extract the editable options from the config based on the schema."""
    options = {}
    for opt in OPTIONS_SCHEMA:
        key = opt["key"]
        options[key] = config.get(key)
    return options


def apply_options_to_config(config: dict, options: dict) -> dict:
    """Apply the options values back to the config."""
    for opt in OPTIONS_SCHEMA:
        key = opt["key"]
        if key in options:
            config[key] = options[key]
    return config


def get_project_folders() -> list:
    """Get list of folder names from the active Fusion 360 project."""
    try:
        app = adsk.core.Application.get()
        if not app:
            return []

        active_project = app.data.activeProject
        if not active_project:
            return []

        root_folder = active_project.rootFolder
        folders = []
        for folder in root_folder.dataFolders:
            folders.append(folder.name)

        folders.sort()
        return folders
    except:
        log(f"Error getting project folders: {traceback.format_exc()}", force_console=True)
        return []


def get_files_in_folder(folder_name: str) -> list:
    """Get list of .f3d file names (without extension) from a folder in the active project."""
    try:
        app = adsk.core.Application.get()
        if not app:
            return []

        active_project = app.data.activeProject
        if not active_project:
            return []

        root_folder = active_project.rootFolder
        target_folder = None
        for folder in root_folder.dataFolders:
            if folder.name == folder_name:
                target_folder = folder
                break

        if not target_folder:
            return []

        files = []
        for file in target_folder.dataFiles:
            if file.fileExtension == "f3d":
                name = file.name
                if name.endswith(".f3d"):
                    name = name[:-4]
                files.append(name)

        files.sort()
        return files
    except:
        log(f"Error getting files in folder: {traceback.format_exc()}", force_console=True)
        return []
