# WARNING this file is copied to the addin root and run from there
# Be careful about file paths. In particular
# all linters will get this wrong
# also this file SHOULD NOT IMPORT ANY OTHER FILES FROM THE ADD-IN
# since these might be resolved to point to older Add-in versions

def log(msg):
    print(msg)

log("Starting launcher")

import os
import shutil
def messageBox(msg):
    import adsk.core
    app = adsk.core.Application.get()
    ui = app.userInterface
    ui.messageBox(msg)

DEFAULT_TOOLLIB_NAME = "Toolpath Default - Do Not Edit"
def has_default_toollib():
    import adsk.cam
    name = DEFAULT_TOOLLIB_NAME
    camManager = adsk.cam.CAMManager.get()
    libraryManager = camManager.libraryManager
    toolLibraries = libraryManager.toolLibraries
    localFolder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
    # delete the lib if necessary
    for url in toolLibraries.childAssetURLs(localFolder):
        if url.leafName == name:
            return True
    return False

def import_default_toollib():
    import adsk.cam
    try:
        name = DEFAULT_TOOLLIB_NAME
        camManager = adsk.cam.CAMManager.get()
        libraryManager = camManager.libraryManager
        toolLibraries = libraryManager.toolLibraries
        localFolder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
        # delete the lib if necessary
        for url in toolLibraries.childAssetURLs(localFolder):
            if url.leafName == name:
                log(f"Deleted old default tool library {url.pathName}")
                toolLibraries.deleteAsset(url)
        
        path = os.path.join(current_addin_dir, "toolpath_generic_tools.json")
        if not os.path.exists(path):
            messageBox(f"Failed to update default tools.{path} does not exist. Please try to update again. If it does not help, please reinstall the Add-In.")
            return
        log(f"Updating default tools with {path}")
        with open(path, "r") as file:
            json = file.read()
        toollib = adsk.cam.ToolLibrary.createFromJson(json)
        toolLibraries.importToolLibrary(toollib, localFolder, name)
    except Exception as err: 
        msg = f"""
        Failed to update default tool library. Please try to update again. If it does not help, please reinstall the Add-In.
        {err}
        """
        messageBox(f"")

addin_root = os.path.dirname(os.path.abspath(__file__))
assert os.path.isdir(addin_root)
next_addin_dir = os.path.join(addin_root, "new_code")
current_addin_dir = os.path.join(addin_root, "code")
old_addin_dir = os.path.join(addin_root, "old_code")

need_update_default_tools = not has_default_toollib()
if os.path.exists(next_addin_dir):
    need_update_default_tools = True
    if os.path.exists(old_addin_dir):
        shutil.rmtree(old_addin_dir) # TODO handle error
    
    log(f"Updating {current_addin_dir}")
    shutil.move(current_addin_dir, old_addin_dir)
    shutil.move(next_addin_dir, current_addin_dir)
else:
    pass

new_manifest_path = os.path.join(current_addin_dir, "Toolpath.manifest")
if os.path.exists(new_manifest_path):
    need_update_default_tools = True
    current_manifest_path = os.path.join(addin_root, "Toolpath.manifest")
    log(f"Updating {current_manifest_path}")
    shutil.copy2(new_manifest_path, current_manifest_path)
    os.remove(new_manifest_path)


if need_update_default_tools:
    import_default_toollib()

log("Finished launcher")
from .code.entry_point import run, stop