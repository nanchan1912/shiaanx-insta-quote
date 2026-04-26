import adsk.core
from ...lib.event_utils import add_handler
from ...lib.general_utils import log, save_config
from ...lib.options_schema import (
    OPTIONS_SCHEMA,
    get_options_from_config,
    apply_options_to_config,
    get_project_folders,
    get_files_in_folder,
    check_requires_restart,
)
import json
import os


class OptionsTab:
    def __init__(
        self,
        tab,
        parent,
        local_handlers,
        incomingFromHTML,
        config,
    ) -> None:
        self.parent = parent
        self.config = config
        self.tab = tab

        # Get initial folder and file lists
        current_values = get_options_from_config(config)
        folders = get_project_folders()
        current_folder = current_values.get("workholding_folder", "")
        files = get_files_in_folder(current_folder) if current_folder else []

        # Build initial data to send to HTML
        self.options_data = {
            "schema": OPTIONS_SCHEMA,
            "values": current_values,
            "folders": folders,
            "files": files,
        }

        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "OptionsTab.html"
        )
        assert os.path.exists(path), f"OptionsTab.html not found at {path}"
        self.html_url = f"file:///{path}".replace("\\", "/")
        # Parameters are: id, name, url, minimumHeight, maximumHeight
        self.browser_command_input = tab.children.addBrowserCommandInput(
            "id_OptionsTabBrowser", "", self.html_url, 600, 800
        )
        self.local_handlers = local_handlers
        add_handler(incomingFromHTML, self.onHTMLEvent, local_handlers=local_handlers)

    def onHTMLEvent(self, args):
        html_args = adsk.core.HTMLEventArgs.cast(args)

        if html_args.action == "OptionsTab_getInitialData":
            args.returnData = json.dumps(self.options_data)

        elif html_args.action == "OptionsTab_saveOptions":
            new_values = json.loads(html_args.data)
            old_values = self.options_data["values"]
            needs_restart = check_requires_restart(old_values, new_values)
            self.options_data["values"] = new_values
            apply_options_to_config(self.config, new_values)
            save_config(self.config)
            log("Options saved", force_console=True)
            args.returnData = json.dumps({"success": True, "requires_restart": needs_restart})

        elif html_args.action == "OptionsTab_getOptions":
            args.returnData = json.dumps(self.options_data["values"])

        elif html_args.action == "OptionsTab_getFolders":
            folders = get_project_folders()
            args.returnData = json.dumps(folders)

        elif html_args.action == "OptionsTab_getFilesInFolder":
            folder_name = html_args.data
            files = get_files_in_folder(folder_name)
            args.returnData = json.dumps(files)

    def get_updated_config(self) -> dict:
        """Return the config with any pending option changes applied."""
        return self.config
