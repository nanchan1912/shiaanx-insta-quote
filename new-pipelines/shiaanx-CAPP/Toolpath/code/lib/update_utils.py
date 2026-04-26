import base64
import os
import tarfile
import shutil
import adsk.core

from .fusion_utils import Fusion
from .client import Client
from .general_utils import get_addin_version, addin_root_rpath, log


def download_update(verbose):
    fusion = Fusion()
    ui = fusion.getUI()
    progressDialog = ui.createProgressDialog()
    progressDialog.isBackgroundTranslucent = False
    progressDialog.isCancelButtonShown = False
    progressDialog.show('Add-In Update', 'Downloading Update', 0, 100)
    progressDialog.progressValue = 0
    
    client = Client()
    current_version = get_addin_version()
    data = {
        "subtypekey" : "RequestPluginUpdate",
        "version" : current_version,
    }
    resp = client.request(data)["data"]
    new_version = resp["code_version"]

    # don't do anything if we're up to date already 
    if verbose and new_version == current_version: 
        progressDialog.progressValue = 100
        progressDialog.hide()
        msg_box = ui.messageBox(
        f"""You have the latest version: {current_version}

        Congrats!
        """
        ,
        "Up to Date", 
        adsk.core.MessageBoxButtonTypes.OKButtonType,
        )
      
        return   

    progressDialog.progressValue = 30
    progressDialog.show('Add-In Update', 'Unpacking Update', 0, 100)
    adsk.doEvents()
    code_tar = base64.b64decode(resp["code"])
    tar_path = addin_root_rpath("new_code.tar")
    if os.path.exists(tar_path):
        os.remove(tar_path)
    with open(tar_path, 'wb') as f:
        f.write(code_tar)
    new_code_path = addin_root_rpath("new_code")

    DRYRUN = False
    if not DRYRUN:
        if os.path.exists(new_code_path):
            log(f"removing {new_code_path}")
            shutil.rmtree(new_code_path)
        with tarfile.open(tar_path) as tar:
            log(f"extracting update to {new_code_path}")
            tar.extractall(new_code_path)
    os.remove(tar_path)

    if not DRYRUN:
        new_manifest_path = os.path.join(new_code_path, "Toolpath.manifest")
        with open(new_manifest_path, "w") as f:
            log(f"writing Add-In manifest to {new_manifest_path}")
            f.write(resp["plugin_manifest"])

    progressDialog.progressValue = 100
    progressDialog.hide()
    
    adsk.doEvents()
    if verbose:
        msg_box = ui.messageBox(
            f"""Update successfully downloaded. It will be installed, next time you restart the Add-In or Fusion.

            Current version: {current_version}
            New version    : {new_version}
            """
            ,
            "Update downloaded",
            adsk.core.MessageBoxButtonTypes.OKButtonType,
        )

    return new_version
