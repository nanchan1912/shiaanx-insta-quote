import traceback
import re
import webbrowser
import json
import time
import threading
import urllib.request
import urllib.error
import ssl
import os

import adsk.core
import adsk.fusion

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion, get_step_file_content, make_id, DEBUG_STEP_EXPORT, ensure_hybrid_design_intent
from ..lib.general_utils import resource_path, log, handle_error, load_config, save_config, compress_step_content
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar, get_addin_version, addin_code_rpath
from ..lib.general_utils import extract_step_body_names, extract_step_product_names, extract_step_shape_rep_names
from ..lib.update_utils import download_update
from ..lib.component_utils import FusionFullPath
from ..lib.setup_utils import get_setup_selector_id
from ..lib.client import Client
from ..lib.theme_utils import get_theme
from .command_RequestFusionOps import logic



PALETTE_ID = 'ToolpathWebAppPalette'
WEBSOCKET_PALETTE_ID = 'ToolpathWebSocketPalette'

def get_palette_url():
    config = load_config()
    return config.get("addin_url", "https://addin.toolpath.com")

def get_offline_page_url(theme=None):
    """Get the file:// URL for the offline.html page with optional theme parameter."""
    offline_path = addin_code_rpath("commands", "command_RequestFusionOps", "offline.html")
    base_url = f"file:///{offline_path.replace(os.sep, '/')}"
    if theme:
        return f"{base_url}?theme={theme}"
    return base_url

def check_server_status(url, timeout=5):
    """
    Check if the server is reachable and returns a valid response.
    Returns True if server is OK, False if there's an error (4xx, 5xx, or connection error).
    """
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        request = urllib.request.Request(url, method='HEAD')
        response = urllib.request.urlopen(request, timeout=timeout, context=ctx)
        status_code = response.getcode()
        return status_code < 400
    except urllib.error.HTTPError as e:
        log(f"Server returned HTTP error {e.code}: {e.reason}", force_console=True)
        return False
    except urllib.error.URLError as e:
        log(f"Server connection error: {e.reason}", force_console=True)
        return False
    except Exception as e:
        log(f"Error checking server status: {e}", force_console=True)
        return False

# Custom event ID for websocket palette health check
WEBSOCKET_HEALTH_CHECK_EVENT_ID = 'ToolpathWebSocketHealthCheck'

# Custom event ID for theme change monitoring
THEME_CHECK_EVENT_ID = 'ToolpathThemeCheck'

# Health check interval in seconds
WEBSOCKET_HEALTH_CHECK_INTERVAL = 15

# Theme check interval in seconds
THEME_CHECK_INTERVAL = 2

# Custom event ID for selection-priority toggling.
SELECTION_PRIORITY_TOGGLE_EVENT_ID = 'ToolpathSelectionPriorityToggle'


class WebSocketHealthCheckThread(threading.Thread):
    """Background thread that periodically fires a custom event to check websocket palette health."""

    def __init__(self, app):
        threading.Thread.__init__(self)
        self.app = app
        self.is_stopped = False
        self.daemon = True  # Thread will exit when main program exits

    def run(self):
        while not self.is_stopped:
            try:
                time.sleep(WEBSOCKET_HEALTH_CHECK_INTERVAL)
                if not self.is_stopped:
                    # Fire custom event to trigger health check on main thread
                    self.app.fireCustomEvent(WEBSOCKET_HEALTH_CHECK_EVENT_ID, '{}')
            except Exception:
                # Ignore errors - thread should keep running
                pass

    def stop(self):
        self.is_stopped = True


class WebSocketHealthCheckHandler(adsk.core.CustomEventHandler):
    """Handler for websocket palette health check custom events."""

    def __init__(self, send_to_toolpath_instance):
        super().__init__()
        self.send_to_toolpath = send_to_toolpath_instance

    def notify(self, args):
        try:
            self.send_to_toolpath._check_websocket_palette_health()
        except Exception:
            log(f"Error in websocket health check: {traceback.format_exc()}", force_console=True)


class ThemeCheckThread(threading.Thread):
    """Background thread that periodically fires a custom event to check for theme changes."""

    def __init__(self, app):
        threading.Thread.__init__(self)
        self.app = app
        self.is_stopped = False
        self.daemon = True

    def run(self):
        while not self.is_stopped:
            try:
                time.sleep(THEME_CHECK_INTERVAL)
                if not self.is_stopped:
                    self.app.fireCustomEvent(THEME_CHECK_EVENT_ID, '{}')
            except Exception:
                pass

    def stop(self):
        self.is_stopped = True


class ThemeCheckHandler(adsk.core.CustomEventHandler):
    """Handler for theme change check custom events."""

    def __init__(self, send_to_toolpath_instance):
        super().__init__()
        self.send_to_toolpath = send_to_toolpath_instance

    def notify(self, args):
        try:
            self.send_to_toolpath._check_theme_change()
        except Exception:
            log(f"Error in theme check: {traceback.format_exc()}", force_console=True)


class SelectionPriorityToggleHandler(adsk.core.CustomEventHandler):
    """Handler for deferred selection-priority toggle requests."""

    def __init__(self, send_to_toolpath_instance):
        super().__init__()
        self.send_to_toolpath = send_to_toolpath_instance

    def notify(self, args):
        try:
            event_args = adsk.core.CustomEventArgs.cast(args)
            payload = json.loads(event_args.additionalInfo) if event_args and event_args.additionalInfo else {}
            if "enabled" not in payload:
                return
            self.send_to_toolpath._set_body_priority_mode_impl(bool(payload.get("enabled")))
        except Exception:
            log(f"Error in selection-priority toggle event: {traceback.format_exc()}", force_console=True)


class SendToToolpath():
    enable_event_logging = False

    def __init__(self,testing=False):
        self.setup_checkboxes = []
        self.multiaxis_dropdowns = []
        self.multiaxis_text = []
        self.local_handlers = []
        self.testing = testing
        self.palette = None
        self.websocket_palette = None
        self._body_selection_enabled = False
        self._body_priority_toggled_by_addin = False
        self._selection_priority_toggle_event = None
        self._selection_priority_toggle_handler = None
        self._programmatic_selection_clear = False  # Flag to distinguish programmatic vs user-initiated selection clear

        # Websocket palette health monitoring
        self._websocket_health_check_thread = None
        self._websocket_health_check_event = None
        self._websocket_health_check_handler = None
        self._websocket_reopen_in_progress = False
        self._last_subscriber_heartbeat = None  # Track last heartbeat from subscriber
        self._last_user_uuid = None  # Track userUuid to detect actual changes

        # Theme change monitoring
        self._theme_check_thread = None
        self._theme_check_event = None
        self._theme_check_handler = None
        self._last_theme = None  # Track last known theme to detect changes

        self.CMD_NAME = 'Send Part to Toolpath'
        self.CMD_ID = command_id_from_name(self.CMD_NAME)
        self.CMD_Description = 'Send the current part to Toolpath and continue in the Toolpath web app.'
        self.ICON_FOLDER = resource_path("send_to_toolpath", '')

        self.CMD2_NAME = 'Import from Toolpath'
        self.CMD2_ID = command_id_from_name(self.CMD2_NAME)
        self.CMD2_Description = 'Import a program into new or current Fusion document from Toolpath.'
        self.ICON2_FOLDER = resource_path("import_program", '')

        # Hidden
        self.CMD3_NAME = 'Import to New Document'
        self.CMD3_ID = command_id_from_name(self.CMD3_NAME)
        self.CMD3_Description = 'Import a program into a new document from Toolpath.'
        self.ICON3_FOLDER = resource_path("import_program_new_doc", '')

        # Hidden command for body selection during import (not in toolbar)
        self.CMD4_NAME = 'Import into Current Document: Select Body'
        self.CMD4_ID = command_id_from_name(self.CMD4_NAME)
        self.CMD4_Description = 'Select body to use for importing a program into the current Fusion document.'
        self.ICON4_FOLDER = resource_path("import_program", '')

        # Store pending import data for body selection workflow
        self._pending_import_data = None
        self._pending_import_doc = None
        self._pending_import_design = None

        self.body = None
        self._native_body_for_export = None  # Native body for STEP export (preserves colors)
        self.need_set_default_AutoSetups_body = True
        self.idx_auto_setups = 0
        self.idx_use_existing_setups = 1
        self.setup_mode_idx = self.idx_auto_setups

        self.setup_dropdown_name = "setup_mode"
        self.auto_setips_body_name = "AutoSetips_body"
        self.auto_setips_stock_body_name = "AutoSetips_stock_body"
        self.no_setup_text_name = "no_setup_text"
        self.user_setups_table_name = "UserSetip_table"
        self.user_setup_text_name = "user_setup_text"
        self.setuptype_dropdown_name = "setup_type_dropdown"
        self.support_geometry_dropdown_name = "support_geometry_type"
        self.support_pedestal_dir_dropdown_name = "support_pedestal_dir"
        self.support_window_offset_input_name = "support_window_offset_abs"
        self.support_pedestal_flip_checkbox_name = "support_pedestal_flip"
        # 0 = none, 1 = window, 2 = pedestal
        self.support_geometry_mode = 0
        # Pedestal direction in setup coordinate system (SCS). We now always
        # use -Z in SCS, with no user selection UI.
        self.support_pedestal_direction_scs = None
        self.support_window_offset = 0.0
        self.support_pedestal_flip = False


        self.config = load_config()

        self.progressDialog = None
        self.document_handlers = []
        self._export_in_progress = False  # Flag to ignore document events during export

    # Executed when add-in is run.
    def start(self):
        ui = None
        try:
            fusion = Fusion()
            ui = fusion.getUI()
            app = fusion.getApplication()

            # Create command definitions for all three buttons
            # 1. Import from Toolpath (first in toolbar)
            cmd_def2 = addCommandToToolbar(self.CMD2_ID, self.CMD2_NAME, self.CMD2_Description, self.ICON2_FOLDER, True)
            add_handler(cmd_def2.commandCreated, self._onImportCurrentCommandCreated, local_handlers=self.local_handlers)

            # 2. Send Part to Toolpath (second in toolbar)
            cmd_def = addCommandToToolbar(self.CMD_ID, self.CMD_NAME, self.CMD_Description, self.ICON_FOLDER, True)
            add_handler(cmd_def.commandCreated, self.onCommandCreated, local_handlers=self.local_handlers)

            # 4. Hidden command for body selection during import (not added to toolbar)
            cmd_def4 = ui.commandDefinitions.addButtonDefinition(
                self.CMD4_ID, self.CMD4_NAME, self.CMD4_Description, self.ICON4_FOLDER
            )
            add_handler(cmd_def4.commandCreated, self._onImportSelectBodyCommandCreated, local_handlers=self.local_handlers)

            # Subscribe to document events
            add_handler(app.documentActivated, self._onDocumentActivated, local_handlers=self.document_handlers)
            add_handler(app.documentDeactivated, self._onDocumentDeactivated, local_handlers=self.document_handlers)
            add_handler(app.documentOpened, self._onDocumentOpened, local_handlers=self.document_handlers)
            add_handler(app.documentClosed, self._onDocumentClosed, local_handlers=self.document_handlers)

            # Subscribe to selection events to detect when user clicks on bodies
            add_handler(ui.activeSelectionChanged, self._onSelectionChanged, local_handlers=self.document_handlers)

            # Register custom event for deferred selection-priority toggling.
            self._selection_priority_toggle_event = app.registerCustomEvent(SELECTION_PRIORITY_TOGGLE_EVENT_ID)
            self._selection_priority_toggle_handler = SelectionPriorityToggleHandler(self)
            self._selection_priority_toggle_event.add(self._selection_priority_toggle_handler)
            self.local_handlers.append(self._selection_priority_toggle_handler)

            # Open the websocket subscriber palette (hidden, for receiving push notifications)
            self._open_websocket_palette(ui)

            # Start websocket palette health monitoring
            self._start_websocket_health_monitoring(app)

            # Start theme change monitoring
            self._start_theme_monitoring(app)

        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


    def stop(self):
        ui = None
        try:
            fusion = Fusion()
            ui = fusion.getUI()
            app = fusion.getApplication()

            # Stop websocket health monitoring
            self._stop_websocket_health_monitoring(app)

            # Stop theme monitoring
            self._stop_theme_monitoring(app)

            # Restore selection-priority mode if we toggled it.
            self._set_body_priority_mode(False, immediate=True)

            # Unregister deferred selection-priority custom event.
            try:
                if self._selection_priority_toggle_event and self._selection_priority_toggle_handler:
                    self._selection_priority_toggle_event.remove(self._selection_priority_toggle_handler)
                app.unregisterCustomEvent(SELECTION_PRIORITY_TOGGLE_EVENT_ID)
            except Exception:
                pass
            self._selection_priority_toggle_event = None
            self._selection_priority_toggle_handler = None

            removeCommandFromToolbar(self.CMD_ID)
            removeCommandFromToolbar(self.CMD2_ID)

            # Clean up the hidden body selection command
            cmd_def4 = ui.commandDefinitions.itemById(self.CMD4_ID)
            if cmd_def4:
                cmd_def4.deleteMe()

            # Clean up the palettes
            palette = ui.palettes.itemById(PALETTE_ID)
            if palette:
                palette.deleteMe()
            self.palette = None

            websocket_palette = ui.palettes.itemById(WEBSOCKET_PALETTE_ID)
            if websocket_palette:
                websocket_palette.deleteMe()
            self.websocket_palette = None

            # Clean up document handlers
            self.document_handlers.clear()
        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    def update_visibilities(self,inputs):
        assert self.setup_mode_idx in (self.idx_auto_setups, self.idx_use_existing_setups)
        isvisible_auto_setups = self.setup_mode_idx == self.idx_auto_setups
        isvisible_existing_setups = not isvisible_auto_setups
        fusion = Fusion()
        cam = fusion.getCAM()
        setups = cam.setups


        for input in self.setup_checkboxes:
            adsk.core.BoolValueCommandInput.cast(input).isVisible = isvisible_existing_setups
        for input in self.multiaxis_dropdowns:
            adsk.core.DropDownCommandInput.cast(input).isVisible = isvisible_existing_setups and self.config["enable_multi_axis_setups"]

        # Support-geometry dropdown is only relevant for existing setups and 3+2.
        support_dd = adsk.core.DropDownCommandInput.cast(
            inputs.itemById(self.support_geometry_dropdown_name)
        )
        support_dir_dd = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.support_pedestal_dir_dropdown_name)
        )
        flip_input = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.support_pedestal_flip_checkbox_name)
        )
        window_offset_input = adsk.core.StringValueCommandInput.cast(
            inputs.itemById(self.support_window_offset_input_name)
        )
        if support_dd:
            visible = isvisible_existing_setups and self.config["enable_multi_axis_setups"]
            if visible and self.multiaxis_dropdowns:
                ma_input = adsk.core.DropDownCommandInput.cast(
                    self.multiaxis_dropdowns[0]
                )
                if ma_input and ma_input.selectedItem:
                    visible = ma_input.selectedItem.name == "ThreePlusTwoAxis"
                else:
                    visible = False
            else:
                visible = False
            support_dd.isVisible = visible
            # We no longer expose pedestal-axis or flip selection in the UI.
            if support_dir_dd:
                support_dir_dd.isVisible = False
            if flip_input:
                flip_input.isVisible = False
            if window_offset_input:
                show_window = visible and support_dd.selectedItem and support_dd.selectedItem.name == "Window"
                window_offset_input.isVisible = show_window

        sel_body = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
        sel_body.isVisible = isvisible_auto_setups

        sel_stock = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_stock_body_name))
        sel_stock.isVisible = isvisible_auto_setups and self.config.get("enable_stock_selection", False)

        sel_table = adsk.core.TableCommandInput.cast(inputs.itemById(self.user_setups_table_name))
        sel_table.isVisible = isvisible_existing_setups and len(setups)>0

        sel_text = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.user_setup_text_name))
        sel_text.isVisible = isvisible_existing_setups and len(setups)>0

        no_setup_text = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.no_setup_text_name))
        no_setup_text.isVisible = isvisible_existing_setups and len(setups)<=0


    def set_AutoSetups_default_body_if_needed(self, inputs):
        # From the fusion docs:
        # addSelection 	Adds the selection to the list of selections associated with this input.
        # This method is not valid within the commandCreated event but must be used later in the command lifetime.
        # If you want to pre-populate the selection when the command is starting, you can use this method in the activate method of the Command.
        # It's also valid to use in other events once the command is running, such as the validateInputs event.
        if not self.need_set_default_AutoSetups_body:
            return
        sel_body = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
        if sel_body.selectionCount == 0:
            success = False
            if self.body is not None:
                try:
                    success = sel_body.addSelection(self.body)
                except RuntimeError as err:
                    # TODO sometimes we get:
                    #
                    # RuntimeError: 3 : invalid argument selection
                    #
                    # despite selecting a BRepBody
                    # not sure why
                    handle_error(err, show_message_box=False)
                    assert isinstance(self.body, adsk.fusion.BRepBody)
                    pass
            self.need_set_default_AutoSetups_body = not success

    def get_setup_mode_selector(self, inputs):
        if self.testing:
            return inputs.itemById(self.setup_dropdown_name)
        else:
            return adsk.core.DropDownCommandInput.cast(inputs.itemById(self.setup_dropdown_name))

    def selection_is_valid(self, args):
        eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
        inputs = eventArgs.firingEvent.sender.commandInputs

        # setups
        setup_selection_isvalid = False
        selectedItem = self.get_setup_mode_selector(inputs).selectedItem
        if selectedItem is None:
            eventArgs.areInputsValid = False
            return
        idx = selectedItem.index
        if idx == self.idx_auto_setups:
            sel_input = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
            setup_selection_isvalid = sel_input.selectionCount == 1
        elif idx == self.idx_use_existing_setups:
            for selecetor in self.setup_checkboxes:
                if selecetor.value:
                    setup_selection_isvalid = True
                    break
        else:
            raise Exception("Unreachable")
        return setup_selection_isvalid

    def onValidateInputs(self, args):
        # toollibs
        setup_selection_isvalid = self.selection_is_valid(args)

        eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
        eventArgs.areInputsValid = setup_selection_isvalid


    def onInputsChanged(self,args):
        eventArgs = adsk.core.InputChangedEventArgs.cast(args)
        changedInput = eventArgs.input
        inputs = eventArgs.firingEvent.sender.commandInputs

        # update axes selection input visibility
        if changedInput.id == self.setup_dropdown_name:
            s = self.get_setup_mode_selector(inputs)
            idx = s.selectedItem.index
            self.setup_mode_idx = idx
            if self.setup_mode_idx == self.idx_auto_setups:
                self.need_set_default_AutoSetups_body = True

        self.set_AutoSetups_default_body_if_needed(inputs)
        self.update_visibilities(inputs)
        if changedInput.id == self.support_window_offset_input_name:
            txt = adsk.core.StringValueCommandInput.cast(changedInput)
            if txt:
                try:
                    val = float(txt.value)
                    if val < 0:
                        val = 0.0
                except Exception:
                    val = 0.0
                self.support_window_offset = val
        if changedInput.id == self.support_pedestal_flip_checkbox_name:
            flip = adsk.core.BoolValueCommandInput.cast(changedInput)
            if flip:
                self.support_pedestal_flip = flip.value

    def onActivate(self, args):
        inputs = args.command.commandInputs
        self.set_AutoSetups_default_body_if_needed(inputs)

    def onPreview(self, args):
        pass

    def get_creation_setups_and_body(self):
        body = None

        fusion = Fusion()
        bodies = fusion.get_visible_bodies()
        if len(bodies) > 0:
            body = bodies[-1] # hack

        cam = fusion.getCAM()
        setups = cam.setups

        return body, setups

    def get_setup_picker_initialization_data(self,setup, fusion_paths):
        bodies = fusion_paths.get_bodies(setup)
        nbodies = len(bodies)
        initialValue : bool = nbodies == 1
        setup_name_lower = setup.name.lower()
        for keyword in ("probe", "probing"):
            if keyword in setup_name_lower:
                initialValue = False
        # setup.isActive
        selector_id : str = get_setup_selector_id(setup)
        assert isinstance(initialValue, bool)
        assert isinstance(selector_id, str)
        resourceFolder = ""
        if nbodies > 1:
            initialValue = False
            name = f"{setup.name} (Not supported: {nbodies}  > 1 bodies)"
        else:
            name = setup.name

        body = None
        if nbodies == 1:
            body = bodies[0]

        return initialValue, selector_id, resourceFolder, name, body


    def get_setup_picker_data_for_react(self) -> dict:
        """
        Get all data needed for a React app to render the setup picker UI.

        Returns a dict with:
        - visibleBodies: list of selectable bodies with id, name, entityToken
        - setups: list of existing CAM setups with their properties
        - hasSetups: whether there are existing setups to use
        - defaultMode: "modelBody" or "existingSetups"
        - setupTypes: available setup types for multi-axis dropdown
        - supportGeometryOptions: available support geometry options
        """
        fusion = Fusion()
        fusion_paths = FusionFullPath()

        # Get visible bodies for body selector
        visible_bodies = fusion.get_visible_bodies()
        bodies_data = []
        for i, body in enumerate(visible_bodies):
            body_data = {
                "index": i,
                "name": body.name,
                "entityToken": body.entityToken,
                "parentName": body.parentComponent.name if body.parentComponent else None,
            }
            bodies_data.append(body_data)

        # Get existing CAM setups
        cam = fusion.getCAM()
        setups = cam.setups
        setups_data = []
        default_body_token = None

        for i, setup in enumerate(setups):
            initial_value, selector_id, resource_folder, display_name, body = \
                self.get_setup_picker_initialization_data(setup, fusion_paths)

            # Track the first valid body for default selection
            if body and not default_body_token:
                default_body_token = body.entityToken

            setup_data = {
                "index": i,
                "name": setup.name,
                "displayName": display_name,
                "selectorId": selector_id,
                "initiallySelected": initial_value,
                "bodyCount": len(fusion_paths.get_bodies(setup)),
                "isValid": initial_value or len(fusion_paths.get_bodies(setup)) == 1,
            }
            setups_data.append(setup_data)

        has_setups = len(setups_data) > 0
        # If there are valid setups, default to existing setups mode
        default_mode = "existingSetups" if has_setups else "modelBody"

        # Default body for auto-setups mode
        default_body = None
        if len(bodies_data) > 0:
            default_body = bodies_data[-1]  # Same logic as get_creation_setups_and_body

        return {
            "visibleBodies": bodies_data,
            "setups": setups_data,
            "hasSetups": has_setups,
            "defaultMode": default_mode,
            "defaultBodyIndex": default_body["index"] if default_body else None,
            "setupTypes": [
                {"id": "ThreeAxis", "name": "3 Axis", "default": True},
                {"id": "ThreePlusTwoAxis", "name": "3+2 Axis", "default": False},
            ],
            "supportGeometryOptions": [
                {"id": "none", "name": "None", "default": True},
                {"id": "pedestal", "name": "Pedestal", "default": False},
                {"id": "window", "name": "Window", "default": False},
            ],
            "messages": {
                "noSetups": "Warning: No valid setups found.\nTo send setups to Toolpath, make sure they have:\n  â¢ A model selected\n  â¢ An orientation selected\nSet stock to \"From Preceding Setup\" for subsequent setups.",
                "userSetupInfo": "Toolpath will program within the setup orientations you provide.\nEnsure stock is set to \"From Preceding Setup\" for subsequent setups.",
            },
            "enableStockSelection": self.config.get("enable_stock_selection", False),
        }

    def onCommandCreated(self,args):
        try:
            fusion = Fusion()
            ui = fusion.getUI()

            # Skip the dialog and directly open the palette
            self._open_toolpath_palette(ui)

            # Auto-execute to close the command immediately (palette stays open)
            eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
            eventArgs.command.isAutoExecute = True

        except Exception as e:
            handle_error(e, True)

    def want_AutoSetips(self, inputs) -> bool:
        s = self.get_setup_mode_selector(inputs)
        return s.selectedItem.index == self.idx_auto_setups

    def want_UserSpecifiedSetips(self, inputs) -> bool:
        s = self.get_setup_mode_selector(inputs)
        return s.selectedItem.index == self.idx_use_existing_setups

    def create_UserSpecifiedSetips(self, inputs) -> logic.UserSpecifiedSetips:
        # setups
        fusion = Fusion()
        cam = fusion.getCAM()
        setips = []
        for (i,setup) in enumerate(cam.setups):
            selector_id = get_setup_selector_id(setup)
            selected : bool = inputs.itemById(selector_id).value
            if i == 0:
                if self.testing:
                    multiaxisInput = inputs.itemById(self.setuptype_dropdown_name)
                else:
                    input = self.multiaxis_dropdowns[i]
                    multiaxisInput = adsk.core.DropDownCommandInput.cast(input)
                multiaxis = multiaxisInput.selectedItem.name
            else:
                multiaxis = "ThreeAxis"
            setip = logic.UserSpecifiedSetip(setup, compute_fusionops=selected,multi_axis=multiaxis)
            setips.append(setip)
        return logic.UserSpecifiedSetips(setips)

    def create_AutoSetips(self, stock_body=None) -> logic.AutoSetips:
        return logic.AutoSetips(
            body=self.body,
            fusion=Fusion(),
            stock_body=stock_body,
        )

    def get_setips(self, inputs):
        fusion = Fusion()
        if self.want_UserSpecifiedSetips(inputs):
            cam = fusion.getCAM()
            setups = cam.setups
            if len(setups) == 0:
                raise Exception(f"No setup found. Please create a setup or use auto setup mode.")
            setips = self.create_UserSpecifiedSetips(inputs)
        elif self.want_AutoSetips(inputs):
            if self.testing:
                sel_input = inputs.itemById(self.auto_setips_body_name)
            else:
                sel_input = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
            if sel_input.selectionCount == 0:
                raise Exception("Please select a model to machine.")
            assert sel_input.selectionCount == 1
            self.body = sel_input.selection(0).entity
            assert isinstance(self.body, adsk.fusion.BRepBody)
            # Get optional stock body
            stock_body = None
            if self.testing:
                sel_stock = inputs.itemById(self.auto_setips_stock_body_name)
            else:
                sel_stock = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_stock_body_name))
            if sel_stock.selectionCount == 1:
                stock_body = sel_stock.selection(0).entity
            setips = self.create_AutoSetips(stock_body=stock_body)
        else:
            raise Exception(f"Unreachable")
        return setips

    def needs_cancel(self):
        adsk.doEvents()
        if self.progressDialog is None:
            return False
        if self.progressDialog.wasCancelled:
            log("needs_cancel")
            return True
        else:
            return False

    def confirm_export_if_issues(self) -> bool:
        issues = self.diagnose()
        if len(issues) == 0:
            return True

        text = "\n\n".join(issues)
        msg = f"""
        We encountered issues with the current document:

        {text}
        """
        fusion = Fusion()
        ui = fusion.getUI()
        title = "Warning"
        ui.messageBox(msg, title)
        return False

    def diagnose(self):
        return self.setips.diagnose()

    def get_body(self):
        return self.setips.get_body()

    def get_body_for_export(self):
        """Get the native body for STEP export (preserves colors/appearance)."""
        if hasattr(self.setips, 'get_body_for_export'):
            return self.setips.get_body_for_export()
        # Fallback for UserSpecifiedSetips which doesn't have this method
        return self.setips.get_body()

    def _get_first_selected_user_setip(self):
        """
        Return the first UserSpecifiedSetip with compute_fusionops == True,
        or None if setips is not a UserSpecifiedSetips instance.
        """
        if not isinstance(self.setips, logic.UserSpecifiedSetips):
            return None
        for s in self.setips.setips:
            if getattr(s, "compute_fusionops", False):
                return s
        return None

    def get_support_geometry_mode(self, inputs) -> str | None:
        """
        Returns a mode name based on UI state ("PEDESTAL", "WINDOW"), or None
        when no support geometry is requested. Only active when the first setup
        is ThreePlusTwoAxis.
        """
        if not self.multiaxis_dropdowns:
            return None

        ma_input = adsk.core.DropDownCommandInput.cast(self.multiaxis_dropdowns[0])
        if not ma_input or not ma_input.selectedItem:
            return None
        if ma_input.selectedItem.name != "ThreePlusTwoAxis":
            return None

        support_dd = adsk.core.DropDownCommandInput.cast(
            inputs.itemById(self.support_geometry_dropdown_name)
        )
        if not support_dd or not support_dd.selectedItem:
            return None

        name = support_dd.selectedItem.name
        if name == "Pedestal":
            return "PEDESTAL"
        if name == "Window":
            return "WINDOW"
        return None

    def jsonify(self,config) -> dict:
        fusion = Fusion()
        body = self.get_body()
        # Use native body for STEP export to preserve colors/appearance
        body_for_export = self.get_body_for_export()

        # === NAME DEBUG LOGGING ===
        native_body = body_for_export.nativeObject if body_for_export.nativeObject else body_for_export

        # Simulate Julia's make_safe_name function to preview what Julia will generate
        def julia_make_safe_name(name):
            """Python implementation of Julia's make_safe_name for debugging"""
            import re
            if not isinstance(name, str):
                return ""
            # strip whitespace
            name = name.strip()
            # replace whitespace sequences with underscore
            name = re.sub(r'\s+', '_', name)
            # replace [ and { with (, and ] and } with )
            name = re.sub(r'[\[\{]', '(', name)
            name = re.sub(r'[\]\}]', ')', name)
            # remove non-safe chars (keep only 0-9 a-z A-Z - _ ( ) .)
            name = re.sub(r'[^0-9a-zA-Z\-_().]', '', name)
            return name

        julia_safe_body_name = julia_make_safe_name(body.name)
        julia_safe_native_name = julia_make_safe_name(native_body.name)

        if DEBUG_STEP_EXPORT:
            log(f"[NAME DEBUG] jsonify() - Body naming info:", force_console=True)
            log(f"[NAME DEBUG]   body.name = '{body.name}'", force_console=True)
            log(f"[NAME DEBUG]   native_body.name = '{native_body.name}'", force_console=True)
            log(f"[NAME DEBUG]   Julia make_safe_name(body.name) = '{julia_safe_body_name}'", force_console=True)
            log(f"[NAME DEBUG]   Julia make_safe_name(native_body.name) = '{julia_safe_native_name}'", force_console=True)
            log(f"[NAME DEBUG]   body.nativeObject is None = {body.nativeObject is None}", force_console=True)
            if body.assemblyContext:
                log(f"[NAME DEBUG]   body.assemblyContext.name = '{body.assemblyContext.name}'", force_console=True)
            else:
                log(f"[NAME DEBUG]   body.assemblyContext = None", force_console=True)
            log(f"[NAME DEBUG]   body.parentComponent.name = '{body.parentComponent.name}'", force_console=True)

            # Check for potential name mismatch
            if body.name != julia_safe_body_name:
                log(f"[NAME DEBUG] ⚠️  POTENTIAL NAME MISMATCH!", force_console=True)
                log(f"[NAME DEBUG]   Fusion sends: '{body.name}'", force_console=True)
                log(f"[NAME DEBUG]   Julia expects: '{julia_safe_body_name}'", force_console=True)
        # === END NAME DEBUG LOGGING ===

        # Use body_for_export (native body) for STEP export to preserve colors/appearance
        step_file_content, part_saved_in_world = get_step_file_content(fusion, body_for_export, debug_name="debug-part")

        # Extract body name from STEP file's MANIFOLD_SOLID_BREP entity
        # This is what Parasolid/Julia will use when reading the STEP file
        step_solid_brep_names = extract_step_body_names(step_file_content)
        # extract_step_body_names already filters out empty names
        non_empty_brep_names = step_solid_brep_names

        # Use the MANIFOLD_SOLID_BREP name if available, otherwise fall back to body.name
        if non_empty_brep_names:
            step_body_name = non_empty_brep_names[0]
            if DEBUG_STEP_EXPORT:
                log(f"[NAME DEBUG] Using MANIFOLD_SOLID_BREP name from STEP: '{step_body_name}'", force_console=True)
        else:
            step_body_name = body.name
            if DEBUG_STEP_EXPORT:
                log(f"[NAME DEBUG] No MANIFOLD_SOLID_BREP name found, using body.name: '{step_body_name}'", force_console=True)

        # === STEP FILE NAME DEBUG ===
        if DEBUG_STEP_EXPORT:
            # Extract other names for debugging using shared utilities
            step_product_names = extract_step_product_names(step_file_content)
            step_shape_rep_names = extract_step_shape_rep_names(step_file_content)
            log(f"[NAME DEBUG] STEP file PRODUCT names: {step_product_names[:5]}", force_console=True)
            log(f"[NAME DEBUG] STEP file MANIFOLD_SOLID_BREP names: {step_solid_brep_names[:5]}", force_console=True)
            log(f"[NAME DEBUG] STEP file SHAPE_REPRESENTATION names: {step_shape_rep_names[:5]}", force_console=True)
            all_step_names = step_product_names + step_solid_brep_names + step_shape_rep_names
            unique_step_names = list(set(all_step_names))

            # Also extract Julia-safe versions of STEP names for comparison
            julia_safe_step_names = [julia_make_safe_name(n) for n in unique_step_names]
            julia_safe_step_body_name = julia_make_safe_name(step_body_name)

            # Summary comparison table
            log(f"[NAME DEBUG] ========== SUMMARY COMPARISON ==========", force_console=True)
            log(f"[NAME DEBUG] Fusion body.name: '{body.name}'", force_console=True)
            log(f"[NAME DEBUG] STEP MANIFOLD_SOLID_BREP name: '{step_body_name}'", force_console=True)
            log(f"[NAME DEBUG] JSON body_name being sent: '{step_body_name}'", force_console=True)
            log(f"[NAME DEBUG] Julia make_safe_name(body_name): '{julia_safe_step_body_name}'", force_console=True)
            log(f"[NAME DEBUG] STEP file unique names: {unique_step_names}", force_console=True)
            log(f"[NAME DEBUG] STEP names (Julia-safe): {julia_safe_step_names}", force_console=True)
            log(f"[NAME DEBUG] ==========================================", force_console=True)
        # === END STEP FILE NAME DEBUG ===

        # Compress STEP file content for smaller payload
        step_file_compressed, compression_info = compress_step_content(step_file_content)

        # Store for use in gather_request_data (stepFile upload)
        self._step_file_compressed = step_file_compressed
        self._step_file_compression = compression_info["compression"]

        payload = {
            "subtypekey": "RequestFusionOpsQA",
            "setips" : self.setips.jsonify(part_saved_in_world_space=part_saved_in_world),
            "geometry": None,
            "tool_libraries": None,
            "geometry_tracking_data": self.setips.get_geometry_tracking_data(),
            "preset_naming" : None,
            "body_name" : step_body_name,
            # direction in setup coordinate system (SCS)
            "support_pedestal_direction_scs": self.support_pedestal_direction_scs,
            "support_window_offset": float(self.support_window_offset),
        }

        for key in [
            "deburr",
            "use_pre_roughing",
            "select_path",
            "continue_on_fusionop_error",
            "max_tool_limit",
            "debug",
            "experimental"
        ]:
            payload[key] = config[key]

        payload["support_geometry_mode"] = self.support_geometry_mode

        return payload

    def gather_request_data(self,config,progressDialog):
        fusion = Fusion()
        payload = self.jsonify(config)

        material_name = 'Aluminum, 6061-T6' # TODO remove this hack. It may not even be needed by the frontend anymore
        if progressDialog is not None:
            progressDialog.progressValue += 1
        user = fusion.getUser()
        if progressDialog is not None:
            progressDialog.progressValue += 1
        if self.needs_cancel(): return "cancelled"

        # Get compressed step content from jsonify() (stored on self)
        step_file_compressed = getattr(self, '_step_file_compressed', None)
        step_file_compression = getattr(self, '_step_file_compression', None)

        # Fallback: get fresh step content and compress it
        body = self.get_body()
        if step_file_compressed is None:
            step_str, _ = get_step_file_content(fusion, body.parentComponent, debug_name="debug-part-fallback")
            if step_str:
                step_file_compressed, compression_info = compress_step_content(step_str)
                step_file_compression = compression_info["compression"]
        if self.needs_cancel(): return "cancelled"

        if progressDialog is not None:
            progressDialog.progressValue += 1

        docname = fusion.getApplication().activeDocument.name
        # Use the body_name from payload (which is extracted from STEP MANIFOLD_SOLID_BREP)
        step_body_name = payload.get("body_name", body.name) if payload else body.name
        name = "{} - {}".format(docname, body.name)

        # === NAME DEBUG LOGGING ===
        if DEBUG_STEP_EXPORT:
            log(f"[NAME DEBUG] gather_request_data() - Final request names:", force_console=True)
            log(f"[NAME DEBUG]   docname = '{docname}'", force_console=True)
            log(f"[NAME DEBUG]   body.name = '{body.name}'", force_console=True)
            log(f"[NAME DEBUG]   step_body_name (from STEP) = '{step_body_name}'", force_console=True)
            log(f"[NAME DEBUG]   combined name = '{name}'", force_console=True)
            log(f"[NAME DEBUG]   body_name being sent = '{step_body_name}'", force_console=True)
        # === END NAME DEBUG LOGGING ===

        data = {
            "subtypekey": "RequestQuoteAssistant",
            "stepFile": step_file_compressed,
            "stepFileCompression": step_file_compression,
            "fusionUserId" : user.userId,
            "fusionUserEmail" : user.email,
            "name" : name,
            "body_name" : step_body_name,
            "toolLibraries" : None,
            "material" : material_name,
            "presetNaming" : None,
            "product" : 'QA',
            "product_specific_data" : payload,
        }
        return data

    def get_and_store_setips(self,inputs):
        self.setips = self.get_setips(inputs)
        return self.setips

    def onCommandExecute(self, args):
        # Here you can get the values from the inputs and execute your main logic.
        command = args.firingEvent.sender
        inputs = command.commandInputs

        fusion = Fusion()
        design = fusion.getDesign()
        if not ensure_hybrid_design_intent(design):
            return "cancelled"

        self.get_and_store_setips(inputs)
        ui = fusion.getUI()
        self.progressDialog = ui.createProgressDialog()
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.progressValue = 0

        if self.needs_cancel(): return "cancelled"
        if not self.confirm_export_if_issues():
            self.progressDialog.hide()
            return "cancelled"
        # Determine support-geometry choice based on current UI.
        self.support_geometry_mode = self.get_support_geometry_mode(inputs)

        # If pedestal support is requested, automatically use -Z in the setup
        # coordinate system; no user axis selection.
        if self.support_geometry_mode == "PEDESTAL":
            self.support_pedestal_direction_scs = [0.0, 0.0, -1.0]


        win_off_input = adsk.core.StringValueCommandInput.cast(
            inputs.itemById(self.support_window_offset_input_name)
        )
        if win_off_input:
            try:
                val = float(win_off_input.value)
                if val < 0:
                    val = 0.0
            except Exception:
                val = 0.0
            self.support_window_offset = val
        self.config = load_config()

        data = self.gather_request_data(self.config,self.progressDialog)
        if data == "cancelled":
            return "cancelled"
        self.progressDialog.progressValue += 1
        if self.needs_cancel(): return "cancelled"
        self.progressDialog.show('Toolpath', 'Uploading to Toolpath. Please check your browser for results.', 0, 100)
        client = Client(self.config)
        try:
            resp = client.request(data, method="POST")
        except Exception as e:

            if e.code == 426:
                current_version = get_addin_version()
                msg_box1 = ui.messageBox(
                    f"Version {current_version} of the add-in is out of date and can not upload files.\nAn updated version of the add-in will be downloaded for installation",
                    "Update Needed",
                    adsk.core.MessageBoxButtonTypes.OKButtonType,
                )
                self.progressDialog.hide()
                download_update(verbose=True)
                return
            else:
                raise Exception(f"Bad status_code: {e.code}")
        if self.needs_cancel(): return "cancelled"

        magicLink = resp["magicLink"]
        webbrowser.open(magicLink)


        if resp == "cancelled":
            self.progressDialog.hide()
            return

        self.progressDialog.progressValue = 100
        self.progressDialog.hide()

        # JSG Note: Turning this off for now, but leaving code here cause we might bring it back
        # msg_box = ui.messageBox(
        #     "Your model has been uploaded to Toolpath and can be found on your Projects page.\n\nPlease check your browser for results.",
        #     "Upload Complete",
        #     adsk.core.MessageBoxButtonTypes.OKButtonType,
        # )

    def _open_toolpath_palette(self, ui, route="#/export"):
        """Open the Toolpath web app in a Fusion 360 palette at the specified hash route."""
        try:
            # Reload config to pick up any changes
            self.config = load_config()

            # Delete existing palette if it exists
            existing_palette = ui.palettes.itemById(PALETTE_ID)
            if existing_palette:
                self._cleanup_body_selection_mode(immediate_priority_toggle=True)
                existing_palette.deleteMe()
                # Give Fusion a moment to clean up
                adsk.doEvents()

            # Check if server is reachable before opening palette
            palette_url = get_palette_url()
            if check_server_status(palette_url):
                # Server is OK - use the normal URL with cache-busting and route
                url_with_cache_bust = f"{palette_url}?_t={int(time.time() * 1000)}{route}"
                log(f"Server OK, opening palette at {palette_url}", force_console=True)
            else:
                # Server error (4xx, 5xx, or connection error) - show offline page with current theme
                current_theme = get_theme()
                url_with_cache_bust = get_offline_page_url(theme=current_theme)
                log(f"Server unavailable, showing offline page (theme={current_theme})", force_console=True)

            # Create new palette
            # Parameters: id, name, htmlFileURL, isVisible, showCloseButton, isResizable, width, height, useNewWebBrowser
            palette = ui.palettes.addTransparent(
                PALETTE_ID,
                'Toolpath',
                url_with_cache_bust,
                True,   # isVisible
                False,   # showCloseButton
                True,   # isResizable
                False,
                500,    # width
                525,    # height
            )
            palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
            palette.setMinimumSize(500, 525)
            palette.setMaximumSize(500, 10000)
            palette.dockingOption = adsk.core.PaletteDockingOptions.PaletteDockOptionsToVerticalOnly

            # Forward console.log from palette JS to Text Commands window when debug mode is enabled
            debug_enabled = self.config.get("debug", False)
            palette.isDebuggingEnabled = debug_enabled
            log(f"Palette debugging enabled: {debug_enabled}", force_console=True)

            add_handler(palette.incomingFromHTML, self._onHTMLEvent, local_handlers=self.local_handlers)
            add_handler(palette.closed, self._onPaletteClosed, local_handlers=self.local_handlers)

            palette.isVisible = True
            self.palette = palette

        except Exception as e:
            log(f"Error opening Toolpath palette: {traceback.format_exc()}")

    def _open_websocket_palette(self, ui):
        """Open a hidden palette for websocket subscriber to receive push notifications."""
        try:
            # Prevent re-entrancy during reopen
            if self._websocket_reopen_in_progress:
                return
            self._websocket_reopen_in_progress = True

            # Delete existing websocket palette if it exists
            existing_palette = ui.palettes.itemById(WEBSOCKET_PALETTE_ID)
            if existing_palette:
                existing_palette.deleteMe()
                adsk.doEvents()

            # Build URL with hash route for subscriber
            # Query params must come before the hash fragment
            url = f"{get_palette_url()}?_t={int(time.time() * 1000)}#/subscriber"

            # Create hidden palette for websocket connection
            # Parameters: id, name, htmlFileURL, isVisible, showCloseButton, isResizable, width, height, useNewWebBrowser
            websocket_palette = ui.palettes.add(
                WEBSOCKET_PALETTE_ID,
                'Toolpath WebSocket',
                url,
                False,  # isVisible - hidden
                False,  # showCloseButton
                False,  # isResizable
                1,      # width (minimal since hidden)
                1,      # height (minimal since hidden)
                True    # useNewWebBrowser
            )

            # Register event handler for incoming messages
            add_handler(websocket_palette.incomingFromHTML, self._onHTMLEvent, local_handlers=self.local_handlers)

            # Register closed event handler to auto-reopen (Option 1)
            add_handler(websocket_palette.closed, self._onWebSocketPaletteClosed, local_handlers=self.local_handlers)

            self.websocket_palette = websocket_palette
            self._last_subscriber_heartbeat = time.time()  # Initialize heartbeat baseline
            # Track current userUuid to detect actual changes later
            config = load_config()
            self._last_user_uuid = config.get("userUuid")
            log("WebSocket subscriber palette opened", force_console=True)

        except Exception as e:
            log(f"Error opening WebSocket palette: {traceback.format_exc()}")
        finally:
            self._websocket_reopen_in_progress = False

    def _onImportCurrentCommandCreated(self, args):
        """Handle Import to Current Document command creation."""
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            add_handler(cmd.execute, self._onImportCurrentExecute, local_handlers=self.local_handlers)
        except:
            log(traceback.format_exc())

    def _onImportCurrentExecute(self, args):
        """Execute Import to Current Document - opens palette at #/?mode=current."""
        try:
            fusion = Fusion()
            ui = fusion.getUI()
            self._open_toolpath_palette(ui, route="#/?mode=current")
        except:
            log(traceback.format_exc())

    def _onImportNewCommandCreated(self, args):
        """Handle Import to New Document command creation."""
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            add_handler(cmd.execute, self._onImportNewExecute, local_handlers=self.local_handlers)
        except:
            log(traceback.format_exc())

    def _onImportNewExecute(self, args):
        """Execute Import to New Document - opens palette at #/?mode=new."""
        try:
            fusion = Fusion()
            ui = fusion.getUI()
            self._open_toolpath_palette(ui, route="#/?mode=new")
        except:
            log(traceback.format_exc())

    def _onImportSelectBodyCommandCreated(self, args):
        """Handle body selection command creation for import workflow."""
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            inputs = cmd.commandInputs

            # Warning message about body mismatch
            mismatch_warning_msg = (
                "Warning: the program from Toolpath is not an exact match to this file. "
                "Select a body to associate with the incoming program."
            )
            mismatch_warning = inputs.addTextBoxCommandInput(
                'import_body_mismatch_warning', '', mismatch_warning_msg, 4, True
            )
            mismatch_warning.isVisible = True

            # Body selection input using native Fusion UI
            sel_body = inputs.addSelectionInput(
                'import_body_selection', "Target Body", "Select the body to import the program into"
            )
            sel_body.addSelectionFilter("SolidBodies")
            sel_body.addSelectionFilter("Occurrences")
            sel_body.clearSelection()
            sel_body.setSelectionLimits(1, 1)
            sel_body.isVisible = True

            # Invalid selection warning
            invalid_selection_msg = inputs.addTextBoxCommandInput(
                'import_body_invalid_selection', '', "Root component is not a valid selection", 1, True
            )
            invalid_selection_msg.isVisible = False

            # Info message
            select_body_msg = 'Toolpath will create setups and CAM operations for the selected body.'
            select_body_message = inputs.addTextBoxCommandInput(
                'import_body_select_msg', '', select_body_msg, 3, True
            )
            select_body_message.isVisible = True

            add_handler(cmd.execute, self._onImportSelectBodyExecute, local_handlers=self.local_handlers)
            add_handler(cmd.inputChanged, self._onImportSelectBodyInputChanged, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self._onImportSelectBodyValidateInputs, local_handlers=self.local_handlers)

        except:
            log(traceback.format_exc())

    def _onImportSelectBodyInputChanged(self, args):
        """Handle input changes in body selection dialog."""
        try:
            eventArgs = adsk.core.InputChangedEventArgs.cast(args)
            inputs = eventArgs.inputs
            cmdInput = eventArgs.input

            if cmdInput.id == 'import_body_selection':
                # Show/hide invalid selection warning based on selection validity
                warning_box = inputs.itemById('import_body_invalid_selection')
                if warning_box:
                    warning_box.isVisible = self._import_body_selection_made(inputs) and not self._import_body_selection_valid(inputs)
        except:
            log(traceback.format_exc())

    def _import_body_selection_made(self, inputs):
        """Check if a body selection has been made."""
        sel_input = inputs.itemById('import_body_selection')
        return sel_input and sel_input.selectionCount > 0

    def _import_body_selection_valid(self, inputs):
        """Check if the current body selection is valid (not root component)."""
        sel_input = inputs.itemById('import_body_selection')
        if not sel_input or sel_input.selectionCount == 0:
            return False

        ent = sel_input.selection(0).entity
        if isinstance(ent, adsk.fusion.BRepBody):
            return True

        # Check if it's an occurrence (not root)
        if isinstance(ent, adsk.fusion.Occurrence):
            occ = ent
            if occ == occ.assemblyContext:  # Root occurrence
                return False
            return True

        return False

    def _onImportSelectBodyValidateInputs(self, args):
        """Validate inputs for body selection dialog."""
        try:
            eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
            inputs = eventArgs.firingEvent.sender.commandInputs
            eventArgs.areInputsValid = self._import_body_selection_valid(inputs)
        except:
            log(traceback.format_exc())

    def _onImportSelectBodyExecute(self, args):
        """Execute import with the selected body."""
        try:
            from ..lib.import_program_utils import ImportProgram

            eventArgs = adsk.core.CommandEventArgs.cast(args)
            cmd = eventArgs.command
            inputs = cmd.commandInputs

            # Get the selected body
            sel_input = adsk.core.SelectionCommandInput.cast(inputs.itemById('import_body_selection'))
            if sel_input.selectionCount == 0:
                log("No body selected for import", force_console=True)
                return

            selected_body = sel_input.selection(0).entity
            if not isinstance(selected_body, (adsk.fusion.Occurrence, adsk.fusion.BRepBody)):
                log(f"Invalid selection type: {type(selected_body)}", force_console=True)
                return

            # Retrieve pending import data
            if not self._pending_import_data:
                log("No pending import data found", force_console=True)
                return

            fusion = Fusion()
            ui = fusion.getUI()

            # Create progress dialog
            progress_dialog = ui.createProgressDialog()
            progress_dialog.cancelButtonText = 'Cancel'
            progress_dialog.isBackgroundTranslucent = False
            progress_dialog.isCancelButtonShown = True
            progress_dialog.show('Toolpath', 'Importing program...', 0, 100)

            def needs_cancel():
                adsk.doEvents()
                return progress_dialog.wasCancelled

            try:
                progress_dialog.progressValue = 5

                # Create ImportProgram instance and materialize with selected body
                import_helper = ImportProgram()
                config = load_config()

                import_helper.materialize_response(
                    fusion=fusion,
                    doc=self._pending_import_doc,
                    design=self._pending_import_design,
                    resp=self._pending_import_data,
                    needs_cancel=needs_cancel,
                    progressDialog=progress_dialog,
                    use_workholding=False,
                    use_stock=False,  # Don't create stock for existing documents
                    viseStyle=None,
                    use_existing_document=True,
                    config=config,
                    selected_body=selected_body,
                )

                progress_dialog.progressValue = 100
                progress_dialog.hide()

                # Notify React that import completed successfully
                if self.palette:
                    self.palette.sendInfoToHTML('importCompleted', json.dumps({"success": True}))

            except Exception as e:
                progress_dialog.hide()
                log(f"Error during import with selected body: {traceback.format_exc()}", force_console=True)
                if self.palette:
                    self.palette.sendInfoToHTML('importCompleted', json.dumps({
                        "success": False,
                        "error": str(e)
                    }))
            finally:
                # Clear pending import data
                self._pending_import_data = None
                self._pending_import_doc = None
                self._pending_import_design = None

        except:
            log(traceback.format_exc())

    def _export_part(self, data) -> dict:
        """
        Gather export data and return it for React to make the fetch request.

        Args:
            data: dict with export options:
                - mode: "modelBody" or "existingSetups"
                - selectedSetups: list of setup indices to include (for existingSetups mode)
                - setupType: "ThreeAxis" or "ThreePlusTwoAxis"
                - supportGeometry: "none", "pedestal", or "window"
                - windowOffset: float (for window support)

        Returns:
            dict with success status and either requestData/apiEndpoint or error message
        """
        # Set flag to ignore document events during export
        # STEP export can trigger spurious events (e.g., "Untitled" document closing)
        self._export_in_progress = True
        try:
            fusion = Fusion()
            design = fusion.getDesign()
            ui = fusion.getUI()

            # Check hybrid design intent before proceeding
            if not ensure_hybrid_design_intent(design):
                return {"success": False, "error": "Export cancelled - document must be in Hybrid mode to add components"}

            # Clear selection in 3D viewer
            ui.activeSelections.clear()

            # Clear any cached STEP file from previous exports
            self._step_file_compressed = None
            self._step_file_compression = None

            mode = data.get("mode", "modelBody")

            # Set up setips based on mode
            if mode == "existingSetups":
                # Use existing CAM setups
                cam = fusion.getCAM()
                selected_indices = set(data.get("selectedSetups", []))
                setup_type = data.get("setupType", "ThreeAxis")

                setips = []
                for i, setup in enumerate(cam.setups):
                    compute = i in selected_indices
                    multi_axis = setup_type if i == 0 else "ThreeAxis"
                    setip = logic.UserSpecifiedSetip(setup, compute_fusionops=compute, multi_axis=multi_axis)
                    setips.append(setip)
                self.setips = logic.UserSpecifiedSetips(setips)
            else:
                # Auto-setups mode using selected body
                entity_token = data.get("entityToken")
                native_body_for_export = None
                if entity_token:
                    # Use the body from the entityToken passed by React
                    design = fusion.getDesign()
                    entities = design.findEntityByToken(entity_token)
                    if entities and len(entities) > 0:
                        body = entities[0]
                        # Extract native body for STEP export (preserves colors)
                        if body.nativeObject is not None:
                            native_body_for_export = body.nativeObject
                    else:
                        return {"success": False, "error": "Could not find body from entityToken"}
                elif self.body:
                    # Fallback to cached body if no token provided
                    body = self.body
                    # Use cached native body if available
                    native_body_for_export = self._native_body_for_export
                else:
                    return {"success": False, "error": "No body selected. Please select a body first."}

                # Get optional stock body
                stock_body = None
                stock_entity_token = data.get("stockEntityToken")
                if stock_entity_token:
                    design = fusion.getDesign()
                    stock_entities = design.findEntityByToken(stock_entity_token)
                    if stock_entities and len(stock_entities) > 0:
                        stock_body = stock_entities[0]
                    else:
                        log(f"Warning: Could not find stock body from stockEntityToken", force_console=True)

                self.setips = logic.AutoSetips(body=body, fusion=fusion, stock_body=stock_body, native_body_for_export=native_body_for_export)

            # Handle support geometry options
            support_geo = data.get("supportGeometry", "none")
            if support_geo == "pedestal":
                self.support_geometry_mode = "PEDESTAL"
                self.support_pedestal_direction_scs = [0.0, 0.0, -1.0]
            elif support_geo == "window":
                self.support_geometry_mode = "WINDOW"
                self.support_window_offset = float(data.get("windowOffset", 0.0))
            else:
                self.support_geometry_mode = None

            # Check for issues (shows dialog if there are problems)
            if not self.confirm_export_if_issues():
                return {"success": False, "error": "Export cancelled due to issues with the document"}

            # Reload config and gather request data
            self.config = load_config()
            self.progressDialog = None  # No progress dialog - React handles UI
            request_data = self.gather_request_data(self.config, None)

            if request_data == "cancelled":
                return {"success": False, "error": "Export cancelled"}

            # Verify stepFile is present
            if not request_data.get("stepFile"):
                log(f"Warning: stepFile is missing or empty in request_data", force_console=True)
                return {"success": False, "error": "Failed to generate STEP file"}

            return {
                "success": True,
                "requestData": request_data,
                "method": "POST"
            }

        except Exception as e:
            log(f"Error preparing export data: {traceback.format_exc()}")
            return {"success": False, "error": str(e)}
        finally:
            # Always reset the flag after export completes
            self._export_in_progress = False

    def _import_program(self, data) -> dict:
        """
        Import a program directly from React, bypassing server fetch.

        Args:
            data: dict with:
                - programData: The full server response (setops, step_file_content, etc.)
                - targetDocumentId: string | null - creationId of document to import into,
                  or null to create a new document
                - useWorkholding: boolean (optional, default False)
                - viseStyle: string (optional, for workholding)

        Returns:
            dict with success status, error message, or needsBodySelection flag
        """
        # Import here to avoid circular import
        from ..lib.import_program_utils import ImportProgram
        from ..lib.fusion_utils import find_document_by_creation_id, create_new_design_doc

        try:
            fusion = Fusion()
            ui = fusion.getUI()

            # Extract options
            program_data = data.get("programData")
            if not program_data:
                return {"success": False, "error": "No programData provided"}

            target_document_id = data.get("targetDocumentId", None)
            use_workholding = data.get("useWorkholding", False)
            vise_style = data.get("viseStyle", None)

            # Validate required fields in programData
            if "setops" not in program_data:
                return {"success": False, "error": "programData missing 'setops'"}
            if not target_document_id and "step_file_content" not in program_data:
                return {"success": False, "error": "programData missing 'step_file_content' for new document import"}

            # For existing documents, check hybrid mode BEFORE starting the import
            # This allows the user to accept/cancel the hybrid mode switch without
            # having to cancel an in-progress import
            if target_document_id:
                doc, _ = find_document_by_creation_id(target_document_id)
                if doc is None:
                    return {"success": False, "error": f"Document with id '{target_document_id}' not found. It may have been closed."}
                doc.activate()
                design = doc.products.itemByProductType('DesignProductType')
                if not ensure_hybrid_design_intent(design):
                    return {"success": False, "error": "Import cancelled - document must be in Hybrid mode"}

            # Create progress dialog
            progress_dialog = ui.createProgressDialog()
            progress_dialog.cancelButtonText = 'Cancel'
            progress_dialog.isBackgroundTranslucent = False
            progress_dialog.isCancelButtonShown = True
            progress_dialog.show('Toolpath', 'Importing program...', 0, 100)

            def needs_cancel():
                adsk.doEvents()
                return progress_dialog.wasCancelled

            try:
                # Get or create document based on targetDocumentId
                if target_document_id:
                    # Document already found and activated above during hybrid check
                    # Just get the design reference again
                    design = doc.products.itemByProductType('DesignProductType')
                    use_existing_document = True
                else:
                    # Create a new document
                    part_name = program_data.get("part_name", "Imported Part")
                    doc, design = create_new_design_doc(doc_name=part_name)
                    use_existing_document = False

                if doc is None:
                    progress_dialog.hide()
                    return {"success": False, "error": "Failed to get or create document"}

                progress_dialog.progressValue = 10
                adsk.doEvents()

                # Create ImportProgram instance
                import_helper = ImportProgram()
                config = load_config()

                # For existing documents, check if the body can be found automatically
                if use_existing_document:
                    # Check if the response matches the document (body can be found)
                    body_found = import_helper.confirm_resp_matches_doc(fusion, doc, program_data)


                # Workholding is not supported for existing documents - the part structure
                # may not be compatible with joint creation
                if use_existing_document and use_workholding:
                    log(f"Disabling workholding for import into existing document", force_console=True)
                    use_workholding = False

                import_helper.materialize_response(
                    fusion=fusion,
                    doc=doc,
                    design=design,
                    resp=program_data,
                    needs_cancel=needs_cancel,
                    progressDialog=progress_dialog,
                    use_workholding=use_workholding,
                    use_stock=not use_existing_document,  # Only create stock for new documents
                    viseStyle=vise_style,
                    use_existing_document=use_existing_document,
                    config=config,
                )

                progress_dialog.progressValue = 100
                progress_dialog.hide()

                return {"success": True}

            except Exception as e:
                progress_dialog.hide()
                raise e

        except Exception as e:
            log(f"Error importing program: {traceback.format_exc()}", force_console=True)
            return {"success": False, "error": str(e)}

    def _toggle_body_priority_command(self) -> bool:
        try:
            ui = Fusion().getUI()
            cmd_def = ui.commandDefinitions.itemById("SelectBodyPriorityCommand")
            if not cmd_def:
                log('Fusion command "SelectBodyPriorityCommand" not found.', force_console=True)
                return False
            cmd_def.execute()
            adsk.doEvents()
            return True
        except Exception:
            log(f"Failed to toggle body priority: {traceback.format_exc()}", force_console=True)
            return False

    def _set_body_priority_mode(self, enabled: bool, immediate: bool = False):
        """
        Request native body-priority mode toggle.

        When possible, this is deferred to a custom event so execution happens
        outside palette callback stack.
        """
        if immediate:
            self._set_body_priority_mode_impl(enabled)
            return

        try:
            app = Fusion().getApplication()
            payload = json.dumps({"enabled": bool(enabled)})
            app.fireCustomEvent(SELECTION_PRIORITY_TOGGLE_EVENT_ID, payload)
            return
        except Exception:
            # Fall back to immediate execution if custom events are unavailable.
            pass

        self._set_body_priority_mode_impl(enabled)

    def _set_body_priority_mode_impl(self, enabled: bool):
        """
        Toggle native body-priority mode.

        Runs from palette HTML handlers (not command event handlers).
        """
        if enabled:
            if self._body_priority_toggled_by_addin:
                return

            if self._toggle_body_priority_command():
                self._body_priority_toggled_by_addin = True
                log("Body priority enabled via SelectBodyPriorityCommand", force_console=True)
            else:
                log("Unable to enable body priority via SelectBodyPriorityCommand", force_console=True)
            return

        if not self._body_priority_toggled_by_addin:
            return

        if self._toggle_body_priority_command():
            self._body_priority_toggled_by_addin = False
            log("Body priority restored via SelectBodyPriorityCommand", force_console=True)
            return

        log("Unable to restore selection priority via SelectBodyPriorityCommand.", force_console=True)

    def _cleanup_body_selection_mode(self, immediate_priority_toggle: bool = False):
        """Best-effort cleanup for body selection mode and priority toggle state."""
        self._body_selection_enabled = False
        try:
            ui = Fusion().getUI()
            ui.activeSelections.clear()
        except Exception:
            pass
        self._set_body_priority_mode(False, immediate=immediate_priority_toggle)

    def _parse_html_event_data(self, data_str):
        """Safely parse JSON data from HTML events.

        Handles cases where JavaScript sends 'undefined' or other invalid JSON.
        Returns an empty dict if parsing fails.
        """
        if not data_str or data_str == "undefined" or data_str == "null":
            return {}
        try:
            return json.loads(data_str)
        except (json.JSONDecodeError, TypeError) as e:
            log(f"Warning: Failed to parse HTML event data: {e}, data was: {data_str[:100] if data_str else 'None'}", force_console=True)
            return {}

    def _onHTMLEvent(self, args):
        """Handle incoming messages from the HTML page."""
        try:
            html_args = adsk.core.HTMLEventArgs.cast(args)
            if self.enable_event_logging:
                log(f"Received HTML event: {html_args.action}")

            if html_args.action == "log":
                # Forward console logs from React to Text Commands (only if debug mode)
                config = load_config()
                if config.get("debug", False):
                    log(f"[React] {html_args.data}", force_console=True)
                args.returnData = json.dumps({"success": True})

            elif html_args.action == "alert":
                # Show a Fusion message box popup
                # Usage: window.adsk.fusionSendData('alert', 'Hello world!')
                log(f"[ALERT] Received alert action with data: {html_args.data}", force_console=True)
                fusion = Fusion()
                ui = fusion.getUI()
                message = html_args.data if html_args.data else "(no message)"
                ui.messageBox(message, "Toolpath")
                args.returnData = json.dumps({"success": True})

            elif html_args.action == "getConfig":
                config = load_config()
                # Return relevant config data (be selective about what to expose)
                args.returnData = json.dumps({
                    "deviceId": config.get("device_id"),
                    "userUuid": config.get("userUuid"),
                    "userToken": config.get("userToken"),
                    "partQueue": config.get("partQueue"),
                    "serverUrl": config.get("server_url"),
                    "appEnvironment": config.get("app_environment"),
                    "appServerHost": config.get("appServerHost"),
                    "pluginVersion": get_addin_version(),
                    "theme": get_theme(),
                    "enableMultiAxisSetups": config.get("enable_multi_axis_setups", False),
                    "enableStockSelection": config.get("enable_stock_selection", False),
                })

            elif html_args.action == "setConfig":
                # Update config.json with provided key/value pairs
                data = self._parse_html_event_data(html_args.data)
                if data:
                    # If userUuid is changing, reset the partQueue
                    if "userUuid" in data:
                        data["partQueue"] = []
                    save_config(data)
                    # Broadcast config change to all palettes
                    self._broadcast_config_changed(data)
                    args.returnData = json.dumps({"success": True})
                else:
                    args.returnData = json.dumps({"success": False, "error": "No data provided"})

            elif html_args.action == "getSetupPickerData":
                # Get all data needed to render the setup picker UI in React
                data = self.get_setup_picker_data_for_react()
                args.returnData = json.dumps(data)

            elif html_args.action == "selectBody":
                # Select a body by entity token for auto-setups mode
                data = self._parse_html_event_data(html_args.data)
                entity_token = data.get("entityToken")
                if entity_token:
                    fusion = Fusion()
                    design = fusion.getDesign()
                    entities = design.findEntityByToken(entity_token)
                    if entities and len(entities) == 1:
                        self.body = entities[0]
                        log(f"selectBody: Set self.body to '{self.body.name}' (token: {entity_token[:20]}...)", force_console=True)
                        args.returnData = json.dumps({"success": True, "name": self.body.name})
                    else:
                        log(f"selectBody: findEntityByToken returned {len(entities) if entities else 0} entities", force_console=True)
                        args.returnData = json.dumps({"success": False, "error": "Body not found"})
                else:
                    args.returnData = json.dumps({"success": False, "error": "No entityToken provided"})

            elif html_args.action == "getSelectedBody":
                # Get the currently selected body (if any)
                if self.body:
                    args.returnData = json.dumps({
                        "selected": True,
                        "name": self.body.name,
                        "entityToken": self.body.entityToken,
                        "parentName": self.body.parentComponent.name if self.body.parentComponent else None,
                    })
                else:
                    args.returnData = json.dumps({"selected": False})

            elif html_args.action == "enableBodySelection":
                # Enable body selection mode - user can click bodies in viewport
                try:
                    self._body_selection_enabled = True
                    fusion = Fusion()
                    ui = fusion.getUI()

                    # Enable Fusion native body-priority mode.
                    self._set_body_priority_mode(True)

                    # Clear any existing selection first
                    ui.activeSelections.clear()
                    args.returnData = json.dumps({"success": True})
                except Exception as e:
                    log(f"Error enabling body selection: {traceback.format_exc()}", force_console=True)
                    args.returnData = json.dumps({"success": False, "error": str(e)})

            elif html_args.action == "disableBodySelection":
                # Disable body selection mode - return to normal
                try:
                    self._cleanup_body_selection_mode(immediate_priority_toggle=False)
                    log(f"Body selection mode disabled", force_console=True)
                    args.returnData = json.dumps({"success": True})
                except Exception as e:
                    log(f"Error disabling body selection: {e}")
                    args.returnData = json.dumps({"success": False, "error": str(e)})

            elif html_args.action == "exportPart":
                # Export the part to Toolpath server
                # data can include: mode ("modelBody" or "existingSetups"), selectedSetups, setupType, etc.
                data = self._parse_html_event_data(html_args.data)
                result = self._export_part(data)
                args.returnData = json.dumps(result)

            elif html_args.action == "getDocumentInfo":
                # Get current document information
                args.returnData = json.dumps(self._get_document_info())

            elif html_args.action == "openExternal":
                url = html_args.data
                webbrowser.open(url)

            elif html_args.action == "closePalette":
                # Close the palette
                if self.palette:
                    self._cleanup_body_selection_mode(immediate_priority_toggle=True)
                    self.palette.isVisible = False
                    self.palette.deleteMe()
                    self.palette = None
                args.returnData = json.dumps({"success": True})

            elif html_args.action == "retryConnection":
                # Close and reopen the palette to retry connection
                fusion = Fusion()
                ui = fusion.getUI()
                if self.palette:
                    self._cleanup_body_selection_mode(immediate_priority_toggle=True)
                    self.palette.isVisible = False
                    self.palette.deleteMe()
                    self.palette = None
                # Reopen the palette (will check server status again)
                self._open_toolpath_palette(ui)
                args.returnData = json.dumps({"success": True})

            elif html_args.action == "importProgram":
                # Import a program directly from React (bypasses server fetch)
                data = self._parse_html_event_data(html_args.data)
                result = self._import_program(data)
                args.returnData = json.dumps(result)

            elif html_args.action == "getOpenDocuments":
                # Get list of all open documents
                from ..lib.fusion_utils import get_open_documents
                documents = get_open_documents()
                args.returnData = json.dumps({"documents": documents})

            elif html_args.action == "getDocumentThumbnail":
                # Get thumbnail for a specific document
                from ..lib.fusion_utils import get_document_thumbnail
                data = self._parse_html_event_data(html_args.data)
                document_id = data.get("documentId")
                width = data.get("width", 256)
                height = data.get("height", 256)
                if not document_id:
                    args.returnData = json.dumps({"error": "documentId is required"})
                else:
                    thumbnail = get_document_thumbnail(document_id, width, height)
                    if thumbnail:
                        args.returnData = json.dumps({"thumbnail": thumbnail})
                    else:
                        args.returnData = json.dumps({"error": "Document not found"})

            elif html_args.action == "subscriberHeartbeat":
                # Track last heartbeat from websocket subscriber
                self._last_subscriber_heartbeat = time.time()
                args.returnData = json.dumps({"success": True})

        except Exception as e:
            log(f"Error handling HTML event: {traceback.format_exc()}")
            args.returnData = json.dumps({"error": str(e)})

    def _onPaletteClosed(self, args):
        """Handle palette being closed by user."""
        self._cleanup_body_selection_mode(immediate_priority_toggle=True)
        log("Toolpath palette closed")

    def _onWebSocketPaletteClosed(self, args):
        """Handle websocket palette being closed - auto-reopen it (Option 1)."""
        log("WebSocket palette closed, reopening...", force_console=True)
        try:
            # Clear the reference since it's been closed
            self.websocket_palette = None

            # Reopen the palette
            fusion = Fusion()
            ui = fusion.getUI()
            self._open_websocket_palette(ui)
        except Exception:
            log(f"Error reopening WebSocket palette: {traceback.format_exc()}", force_console=True)

    def _start_websocket_health_monitoring(self, app):
        """Start the websocket palette health monitoring system (Option 2)."""
        try:
            # Register custom event for health checks
            self._websocket_health_check_event = app.registerCustomEvent(WEBSOCKET_HEALTH_CHECK_EVENT_ID)
            self._websocket_health_check_handler = WebSocketHealthCheckHandler(self)
            self._websocket_health_check_event.add(self._websocket_health_check_handler)
            self.local_handlers.append(self._websocket_health_check_handler)

            # Start background thread for periodic health checks
            self._websocket_health_check_thread = WebSocketHealthCheckThread(app)
            self._websocket_health_check_thread.start()

            log("WebSocket health monitoring started", force_console=True)
        except Exception:
            log(f"Error starting WebSocket health monitoring: {traceback.format_exc()}", force_console=True)

    def _stop_websocket_health_monitoring(self, app):
        """Stop the websocket palette health monitoring system."""
        try:
            # Stop the health check thread
            if self._websocket_health_check_thread:
                self._websocket_health_check_thread.stop()
                self._websocket_health_check_thread = None

            # Unregister the custom event
            if self._websocket_health_check_event:
                app.unregisterCustomEvent(WEBSOCKET_HEALTH_CHECK_EVENT_ID)
                self._websocket_health_check_event = None
                self._websocket_health_check_handler = None

            log("WebSocket health monitoring stopped", force_console=True)
        except Exception:
            log(f"Error stopping WebSocket health monitoring: {traceback.format_exc()}", force_console=True)

    def _check_websocket_palette_health(self):
        """Check if websocket palette is healthy and reopen if needed."""
        try:
            fusion = Fusion()
            ui = fusion.getUI()

            # Check if palette still exists
            existing_palette = ui.palettes.itemById(WEBSOCKET_PALETTE_ID)
            if not existing_palette:
                log("WebSocket palette not found during health check, reopening...", force_console=True)
                self.websocket_palette = None
                self._open_websocket_palette(ui)
                return

            # Check if we've received a heartbeat recently (within 2x the check interval)
            if self._last_subscriber_heartbeat is not None:
                time_since_heartbeat = time.time() - self._last_subscriber_heartbeat
                max_heartbeat_age = WEBSOCKET_HEALTH_CHECK_INTERVAL * 2  # Allow 2 intervals (30s at 15s interval)
                if time_since_heartbeat > max_heartbeat_age:
                    log(f"No heartbeat from subscriber for {time_since_heartbeat:.0f}s, refreshing...", force_console=True)
                    self._refresh_websocket_palette(ui)

        except Exception:
            log(f"Error in WebSocket health check: {traceback.format_exc()}", force_console=True)

    def _refresh_websocket_palette(self, ui):
        """Force refresh the websocket palette."""
        log("Refreshing WebSocket palette...", force_console=True)
        self.websocket_palette = None
        self._last_subscriber_heartbeat = time.time()  # Reset to avoid immediate re-refresh
        self._open_websocket_palette(ui)

    def _start_theme_monitoring(self, app):
        """Start the theme change monitoring system."""
        try:
            # Initialize last known theme
            self._last_theme = get_theme()

            # Register custom event for theme checks
            self._theme_check_event = app.registerCustomEvent(THEME_CHECK_EVENT_ID)
            self._theme_check_handler = ThemeCheckHandler(self)
            self._theme_check_event.add(self._theme_check_handler)
            self.local_handlers.append(self._theme_check_handler)

            # Start background thread for periodic theme checks
            self._theme_check_thread = ThemeCheckThread(app)
            self._theme_check_thread.start()

            log("Theme monitoring started", force_console=True)
        except Exception:
            log(f"Error starting theme monitoring: {traceback.format_exc()}", force_console=True)

    def _stop_theme_monitoring(self, app):
        """Stop the theme change monitoring system."""
        try:
            # Stop the theme check thread
            if self._theme_check_thread:
                self._theme_check_thread.stop()
                self._theme_check_thread = None

            # Unregister the custom event
            if self._theme_check_event:
                app.unregisterCustomEvent(THEME_CHECK_EVENT_ID)
                self._theme_check_event = None
                self._theme_check_handler = None

            log("Theme monitoring stopped", force_console=True)
        except Exception:
            log(f"Error stopping theme monitoring: {traceback.format_exc()}", force_console=True)

    def _check_theme_change(self):
        """Check if theme has changed and broadcast to palettes if so."""
        try:
            current_theme = get_theme()
            if current_theme != self._last_theme:
                log(f"Theme changed: {self._last_theme} -> {current_theme}", force_console=True)
                self._last_theme = current_theme
                self._broadcast_theme_changed(current_theme)
        except Exception:
            log(f"Error checking theme: {traceback.format_exc()}", force_console=True)

    def _broadcast_theme_changed(self, theme: str):
        """Broadcast theme change to all palettes."""
        event_data = {"theme": theme}

        # Send to main palette if open
        if self.palette:
            try:
                self.palette.sendInfoToHTML('themeChanged', json.dumps(event_data))
            except:
                pass

        # Send to websocket palette if open
        if self.websocket_palette:
            try:
                self.websocket_palette.sendInfoToHTML('themeChanged', json.dumps(event_data))
            except:
                pass

    def _broadcast_config_changed(self, changed_keys: dict):
        """Broadcast config changes to all palettes."""
        config = load_config()
        config_data = {
            "deviceId": config.get("device_id"),
            "userUuid": config.get("userUuid"),
            "partQueue": config.get("partQueue"),
            "serverUrl": config.get("server_url"),
            "appEnvironment": config.get("app_environment"),
            "appServerHost": config.get("appServerHost"),
            "pluginVersion": get_addin_version(),
            "theme": get_theme(),
        }
        event_data = {
            "changedKeys": changed_keys,
            "config": config_data,
        }

        # Send to main palette if open
        if self.palette:
            try:
                self.palette.sendInfoToHTML('configChanged', json.dumps(event_data))
            except:
                pass

        # If userUuid actually changed to a different value, refresh the websocket palette
        new_user_uuid = changed_keys.get("userUuid")
        if new_user_uuid is not None and new_user_uuid != self._last_user_uuid:
            log(f"userUuid changed from {self._last_user_uuid} to {new_user_uuid}, refreshing websocket palette...", force_console=True)
            self._last_user_uuid = new_user_uuid
            fusion = Fusion()
            ui = fusion.getUI()
            self._refresh_websocket_palette(ui)
        # Otherwise just notify the websocket palette of the config change
        elif self.websocket_palette:
            try:
                self.websocket_palette.sendInfoToHTML('configChanged', json.dumps(event_data))
            except:
                pass

    def _get_document_info(self) -> dict:
        """Get current document information to send to React."""
        try:
            fusion = Fusion()
            app = fusion.getApplication()
            doc = app.activeDocument

            if doc:
                return {
                    "hasDocument": True,
                    "name": doc.name,
                    "id": doc.creationId if hasattr(doc, 'creationId') else None,
                    "isSaved": doc.isSaved,
                }
            else:
                return {"hasDocument": False}
        except Exception as e:
            log(f"Error getting document info: {e}")
            return {"hasDocument": False, "error": str(e)}

    def _onDocumentActivated(self, args):
        """Handle document being activated (switched to)."""
        try:
            # Ignore document events during export - STEP export can trigger
            # spurious document events
            if self._export_in_progress:
                return

            doc_args = adsk.core.DocumentEventArgs.cast(args)
            doc = doc_args.document
            if self.enable_event_logging:
                log(f"Document activated: {doc.name if doc else 'None'}")

            if self.palette:
                self.palette.sendInfoToHTML('documentChanged', json.dumps({
                    "event": "activated",
                    **self._get_document_info()
                }))
        except Exception as e:
            log(f"Error in documentActivated handler: {e}")

    def _onDocumentDeactivated(self, args):
        """Handle document being deactivated (switched away from)."""
        try:
            # Ignore document events during export - STEP export can trigger
            # spurious document events
            if self._export_in_progress:
                return

            doc_args = adsk.core.DocumentEventArgs.cast(args)
            doc = doc_args.document
            if self.enable_event_logging:
                log(f"Document deactivated: {doc.name if doc else 'None'}")

            if self.palette:
                self.palette.sendInfoToHTML('documentChanged', json.dumps({
                    "event": "deactivated",
                    "previousDocument": doc.name if doc else None
                }))
        except Exception as e:
            log(f"Error in documentDeactivated handler: {e}")

    def _onDocumentOpened(self, args):
        """Handle document being opened."""
        try:
            # Ignore document events during export - STEP export can trigger
            # spurious document events
            if self._export_in_progress:
                return

            doc_args = adsk.core.DocumentEventArgs.cast(args)
            doc = doc_args.document
            if self.enable_event_logging:
                log(f"Document opened: {doc.name if doc else 'None'}")

            if self.palette:
                self.palette.sendInfoToHTML('documentChanged', json.dumps({
                    "event": "opened",
                    **self._get_document_info()
                }))
        except Exception as e:
            log(f"Error in documentOpened handler: {e}")

    def _onDocumentClosed(self, args):
        """Handle document being closed."""
        try:
            # Ignore document events during export - STEP export can trigger
            # spurious document events (e.g., "Untitled" document closing)
            if self._export_in_progress:
                return

            doc_args = adsk.core.DocumentEventArgs.cast(args)
            doc = doc_args.document
            if self.enable_event_logging:
                log(f"Document closed: {doc.name if doc else 'None'}")

            # Clear selected body since document changed
            self.body = None

            if self.palette:
                self.palette.sendInfoToHTML('documentChanged', json.dumps({
                    "event": "closed",
                    "closedDocument": doc.name if doc else None,
                    **self._get_document_info()
                }))
        except Exception as e:
            log(f"Error in documentClosed handler: {e}")

    def _onSelectionChanged(self, args):
        """Handle selection changes in the Fusion 360 viewport."""
        try:
            # Only process selections when body selection mode is enabled
            if not self._body_selection_enabled:
                return

            fusion = Fusion()
            ui = fusion.getUI()

            # Get the current selection
            selection = ui.activeSelections

            if selection.count > 0:
                selected_entity = selection.item(0).entity
                if isinstance(selected_entity, adsk.fusion.BRepBody):
                    body = selected_entity
                    # Keep the original body (possibly a proxy) for highlighting
                    selectable_body = body

                    # Store the proxy body for entityToken resolution (findEntityByToken needs this)
                    # The proxy body's token is resolvable and maintains assemblyContext for transforms.
                    # We also store the native body separately for STEP export to preserve colors.
                    self.body = body
                    if body.nativeObject is not None:
                        self._native_body_for_export = body.nativeObject
                    else:
                        self._native_body_for_export = body

                    # Disable selection mode after a body is selected
                    self._body_selection_enabled = False
                    # Restore default priority after one-shot pick completion.
                    self._set_body_priority_mode(False)

                    # Briefly highlight the body before clearing to give visual feedback
                    # Use the original selectable_body (proxy) for highlighting, not the native body
                    selection.clear()
                    selection.add(selectable_body)
                    adsk.doEvents()  # Let Fusion render the highlight

                    # Notify React app about the selection
                    if self.palette:
                        body_data = {
                            "success": True,
                            "name": self.body.name,
                            "entityToken": self.body.entityToken,
                            "parentName": self.body.parentComponent.name if self.body.parentComponent else None,
                        }
                        self.palette.sendInfoToHTML('bodySelected', json.dumps(body_data))
                        # Also notify that selection mode ended
                        self.palette.sendInfoToHTML('bodySelectionEnded', json.dumps({
                            "hasSelection": True
                        }))
        except Exception as e:
            log(f"Error in selection changed handler: {traceback.format_exc()}", force_console=True)
