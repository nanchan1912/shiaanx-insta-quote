import adsk.core
import os
import adsk.cam
import adsk.fusion
import tempfile
import urllib.request
import io
from .general_utils import load_config

DEFAULT_WORKHOLDING_FOLDER_NAME = "Toolpath Workholding - Do Not Edit"
config = load_config()

ENABLE_WORKHOLDING = config["enable_legacy_workholding"]

app = adsk.core.Application.get()
ui = app.userInterface

def get_workholding_folder_name():
    return config.get("workholding_folder") or DEFAULT_WORKHOLDING_FOLDER_NAME

def download_f3d_file(file_name,category="vise"):
    """Downloads the .f3d file from GitHub using authentication and stores it in memory."""
    
    AMAZON_BUCKET_URL = "https://toolpath-public.s3.amazonaws.com/fusion_workholding/"

    if category == "vise":
        file_url = AMAZON_BUCKET_URL +"vises/" + file_name
    elif category == "fixture_plate":
        file_url = AMAZON_BUCKET_URL +"clamping/" + file_name
    else:
        print("Unknown file category.")
    try:
        # Create request
        request = urllib.request.Request(file_url)

        # Open the request and read file data
        with urllib.request.urlopen(request) as response:
            file_data = io.BytesIO(response.read())  # Store file in memory
            print("File downloaded successfully into memory.")
            return file_data

    except urllib.error.URLError as e:
        print(f"Error downloading file: {e}")
        return None

def upload_to_fusion_cloud(file_data, file_name,target_folder):
    """Uploads an in-memory .f3d file to Fusion 360 cloud storage using a temporary file."""

    # Define a specific filename
    desired_filename = file_name

    # Create a temporary directory and use the desired filename
    temp_dir = tempfile.gettempdir()  # Get system temp directory
    temp_file_path = os.path.join(temp_dir, desired_filename)

    # Open the file manually
    with open(temp_file_path, "wb") as temp_file:
        temp_file.write(file_data.getvalue())

    print(f"Temporary file created at: {temp_file_path}")

    # Upload the temp file to Fusion 360 cloud
    upload_item = target_folder.uploadFile(temp_file_path)
    if upload_item:
        print(f"File uploaded successfully to Fusion 360 cloud:")

    # Delete the temporary file after upload
    os.remove(temp_file_path)

def create_new_folder(folder_name, active_project):
    # Get the root folder of the active project
    root_folder = active_project.rootFolder

    # Check if the folder already exists
    for folder in root_folder.dataFolders:
        if folder.name == folder_name:
            return folder

    # Create a new folder
    new_folder = root_folder.dataFolders.add(folder_name)

    return new_folder

def get_workholding_folder(active_project):
    # Get the root folder of the active project
    if active_project is None:
        return None
    root_folder = active_project.rootFolder

    # Check if the folder already exists
    for folder in root_folder.dataFolders:
        if folder.name == get_workholding_folder_name():
            return folder
    return None

def file_exists_in_folder(file_name, folder):
    # Use the specified folder or default to the root folder
    target_folder = folder 
    suffix = ".f3d"
    # Loop through all files in the folder
    for file in target_folder.dataFiles:
        if file.name + suffix == file_name:
            return True
    return False

def get_active_project():
    app = adsk.core.Application.get()
    ui = app.userInterface
    data_mgr = app.data

    # Get the active project (the one displayed in the side panel)
    try:
        active_project = data_mgr.activeProject
    except RuntimeError:
        # activeProject throws RuntimeError for unsaved/offline documents
        return None

    if active_project:
        return active_project
    else:
        ui.messageBox("No active project found.")
        return None