import traceback
import time
import re
import webbrowser

import adsk.core
import adsk.fusion

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion, get_step_file_content, make_id, get_current_design_doc
from ..lib.general_utils import resource_path, log, handle_error, load_config
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
from ..lib.component_utils import FusionFullPath
from ..lib.setup_utils import get_setup_selector_id
from ..lib.client import Client
from .command_RequestFusionOps import logic


class Cmd():
    def __init__(self):
        self.setup_checkboxes = []
        self.multiaxis_dropdowns = []
        self.multiaxis_text = []
        self.local_handlers = []

        self.CMD_NAME = 'Automated CAM'
        self.CMD_ID = command_id_from_name(self.CMD_NAME)
        self.CMD_Description = 'Automatically generate and import CAM operations using Toolapth AI.'
        self.ICON_FOLDER = resource_path("send_to_toolpath", '')

        self.body = None
        self.need_set_default_AutoSetups_body = True
        self.idx_auto_setups = 0
        self.idx_use_existing_setups = 1
        self.setup_mode_idx = self.idx_auto_setups

        self.setup_dropdown_name = "setup_mode"
        self.auto_setips_body_name = "AutoSetips_body"
        self.no_setup_text_name = "no_setup_text"
        self.user_setups_table_name = "UserSetip_table"
        self.user_setup_text_name = "user_setup_text"
        self.setuptype_dropdown_name = "setup_type_dropdown"

        self.config = load_config()
        self.progressDialog = None
        self.import_program = None  # Lazy init to avoid circular import

    def start(self):
        ui = None
        try:
            fusion = Fusion()
            ui = fusion.getUI()

            # Clean up any existing command definition
            existing_cmd_def = ui.commandDefinitions.itemById(self.CMD_ID)
            if existing_cmd_def:
                existing_cmd_def.deleteMe()

            cmd_def = addCommandToToolbar(self.CMD_ID, self.CMD_NAME, self.CMD_Description, self.ICON_FOLDER, False)
            add_handler(cmd_def.commandCreated, self.onCommandCreated, local_handlers=self.local_handlers)

        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    def stop(self):
        ui = None
        try:
            ui = Fusion().getUI()
            removeCommandFromToolbar(self.CMD_ID)
        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    def update_visibilities(self, inputs):
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

        sel_body = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
        sel_body.isVisible = isvisible_auto_setups

        sel_table = adsk.core.TableCommandInput.cast(inputs.itemById(self.user_setups_table_name))
        sel_table.isVisible = isvisible_existing_setups and len(setups) > 0

        sel_text = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.user_setup_text_name))
        sel_text.isVisible = isvisible_existing_setups and len(setups) > 0

        no_setup_text = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.no_setup_text_name))
        no_setup_text.isVisible = isvisible_existing_setups and len(setups) <= 0

    def set_AutoSetups_default_body_if_needed(self, inputs):
        if not self.need_set_default_AutoSetups_body:
            return
        sel_body = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
        if sel_body.selectionCount == 0:
            success = False
            if self.body is not None:
                try:
                    success = sel_body.addSelection(self.body)
                except RuntimeError as err:
                    handle_error(err, show_message_box=False)
                    pass
            self.need_set_default_AutoSetups_body = not success

    def get_setup_mode_selector(self, inputs):
        return adsk.core.DropDownCommandInput.cast(inputs.itemById(self.setup_dropdown_name))

    def selection_is_valid(self, args):
        eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
        inputs = eventArgs.firingEvent.sender.commandInputs

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
            for selector in self.setup_checkboxes:
                if selector.value:
                    setup_selection_isvalid = True
                    break
        return setup_selection_isvalid

    def onValidateInputs(self, args):
        setup_selection_isvalid = self.selection_is_valid(args)
        eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
        eventArgs.areInputsValid = setup_selection_isvalid

    def onInputsChanged(self, args):
        eventArgs = adsk.core.InputChangedEventArgs.cast(args)
        changedInput = eventArgs.input
        inputs = eventArgs.firingEvent.sender.commandInputs

        if changedInput.id == self.setup_dropdown_name:
            s = self.get_setup_mode_selector(inputs)
            idx = s.selectedItem.index
            self.setup_mode_idx = idx
            if self.setup_mode_idx == self.idx_auto_setups:
                self.need_set_default_AutoSetups_body = True

        self.set_AutoSetups_default_body_if_needed(inputs)
        self.update_visibilities(inputs)

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
            body = bodies[-1]

        cam = fusion.getCAM()
        setups = cam.setups
        return body, setups

    def get_setup_picker_initialization_data(self, setup, fusion_paths):
        bodies = fusion_paths.get_bodies(setup)
        nbodies = len(bodies)
        initialValue: bool = nbodies == 1
        setup_name_lower = setup.name.lower()
        for keyword in ("probe", "probing"):
            if keyword in setup_name_lower:
                initialValue = False

        selector_id: str = get_setup_selector_id(setup)
        resourceFolder = ""
        if nbodies > 1:
            initialValue = False
            name = f"{setup.name} (Not supported: {nbodies} > 1 bodies)"
        else:
            name = setup.name

        body = None
        if nbodies == 1:
            body = bodies[0]

        return initialValue, selector_id, resourceFolder, name, body

    def onCommandCreated(self, args):
        ui = None
        try:
            self.config = load_config()

            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            fusion_paths = FusionFullPath()

            body, setups = self.get_creation_setups_and_body()
            self.body = body

            inputs = cmd.commandInputs
            cmd.setDialogInitialSize(350, 400)
            cmd.setDialogSize(350, 0)

            setup_mode = inputs.addDropDownCommandInput(self.setup_dropdown_name, "Generate from", adsk.core.DropDownStyles.TextListDropDownStyle)

            if len(setups) > 0:
                setup_mode.listItems.add("Model Body", False)
                setup_mode.listItems.add("Existing Setups", True)
                self.setup_mode_idx = self.idx_use_existing_setups
                want_auto_setups = False
            else:
                setup_mode.listItems.add("Model Body", True)
                setup_mode.listItems.add("Existing Setups", False)
                self.setup_mode_idx = self.idx_auto_setups
                want_auto_setups = True

            sel_body = inputs.addSelectionInput(self.auto_setips_body_name, "Target Body", "Select model")
            sel_body.addSelectionFilter("SolidBodies")
            sel_body.setSelectionLimits(0, 1)
            sel_body.isVisible = want_auto_setups

            setup_mode.tooltip = """Generate CAM operations automatically from a body or use existing setups."""

            no_setips_msg = 'Warning: No valid setups found.\nTo use existing setups, make sure they have:\n  • A model selected\n  • An orientation selected'
            no_setup_message = inputs.addTextBoxCommandInput(self.no_setup_text_name, '', no_setips_msg, 5, True)
            no_setup_message.isVisible = (not want_auto_setups) and (len(setups) <= 0)

            tableInput = inputs.addTableCommandInput(self.user_setups_table_name, 'Available Setups', 3, '1:10:10')
            tableInput.isVisible = not want_auto_setups and (len(setups) > 0)
            cmdInputs = adsk.core.CommandInputs.cast(tableInput.commandInputs)

            isCheckBox = True
            self.setup_checkboxes.clear()
            self.multiaxis_dropdowns.clear()
            self.multiaxis_text.clear()

            for (i, setup) in enumerate(setups):
                initialValue, selector_id, resourceFolder, name, body = self.get_setup_picker_initialization_data(setup, fusion_paths)
                if body is not None:
                    self.body = body

                selector = cmdInputs.addBoolValueInput(selector_id, name, isCheckBox, resourceFolder, initialValue)
                selectorText = cmdInputs.addTextBoxCommandInput(make_id(setup.name, i), '', name, 1, True)
                tableInput.addCommandInput(selector, i, 0)
                self.setup_checkboxes.append(selector)
                tableInput.addCommandInput(selectorText, i, 1)

                if i == 0:
                    ma_dropdown = cmdInputs.addDropDownCommandInput(self.setuptype_dropdown_name, "Setup type: ", adsk.core.DropDownStyles.TextListDropDownStyle)
                    ma_dropdown.listItems.add("ThreeAxis", True)
                    ma_dropdown.listItems.add("ThreePlusTwoAxis", False)
                    tableInput.addCommandInput(ma_dropdown, i, 2)
                    self.multiaxis_dropdowns.append(ma_dropdown)

                selector.tooltip = """Select if this setup should be populated with operations.
                WARNING: Selecting a setup will overwrite any existing operations for that setup.
                """

            user_setip_msg = 'Automated will program within the setup orientations you provide.'
            user_setup_message = inputs.addTextBoxCommandInput(self.user_setup_text_name, '', user_setip_msg, 3, True)
            user_setup_message.isVisible = not want_auto_setups and (len(setups) > 0)

            self.need_set_default_AutoSetups_body = True
            self.update_visibilities(inputs)

            cmd.isExecutedWhenPreEmpted = False

            add_handler(cmd.inputChanged, self.onInputsChanged, local_handlers=self.local_handlers)
            add_handler(cmd.execute, self.onCommandExecute, local_handlers=self.local_handlers)
            add_handler(cmd.executePreview, self.onPreview, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.activate, self.onActivate, local_handlers=self.local_handlers)

        except Exception as e:
            handle_error(e, True)

    def want_AutoSetips(self, inputs) -> bool:
        s = self.get_setup_mode_selector(inputs)
        return s.selectedItem.index == self.idx_auto_setups

    def want_UserSpecifiedSetips(self, inputs) -> bool:
        s = self.get_setup_mode_selector(inputs)
        return s.selectedItem.index == self.idx_use_existing_setups

    def create_UserSpecifiedSetips(self, inputs) -> logic.UserSpecifiedSetips:
        fusion = Fusion()
        cam = fusion.getCAM()
        setips = []
        for (i, setup) in enumerate(cam.setups):
            selector_id = get_setup_selector_id(setup)
            selected: bool = inputs.itemById(selector_id).value
            if i == 0:
                input = self.multiaxis_dropdowns[i]
                multiaxisInput = adsk.core.DropDownCommandInput.cast(input)
                multiaxis = multiaxisInput.selectedItem.name
            else:
                multiaxis = "ThreeAxis"
            setip = logic.UserSpecifiedSetip(setup, compute_fusionops=selected, multi_axis=multiaxis)
            setips.append(setip)
        return logic.UserSpecifiedSetips(setips)

    def create_AutoSetips(self) -> logic.AutoSetips:
        return logic.AutoSetips(
            body=self.body,
            fusion=Fusion(),
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
            sel_input = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.auto_setips_body_name))
            if sel_input.selectionCount == 0:
                raise Exception("Please select a model to machine.")
            assert sel_input.selectionCount == 1
            self.body = sel_input.selection(0).entity
            assert isinstance(self.body, adsk.fusion.BRepBody)
            setips = self.create_AutoSetips()
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

    def jsonify(self, config) -> dict:
        fusion = Fusion()
        body = self.get_body()
        step_file_content, part_saved_in_world = get_step_file_content(fusion, body)

        payload = {
            "subtypekey": "RequestFusionOpsQA",
            "setips": self.setips.jsonify(part_saved_in_world_space=part_saved_in_world),
            "geometry": None,
            "tool_libraries": None,
            "step_file_content": step_file_content,
            "geometry_tracking_data": self.setips.get_geometry_tracking_data(),
            "preset_naming": None,
            "body_name": body.name,
            "support_pedestal_direction_scs": None,
            "support_window_offset": 0.0,
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

        payload["support_geometry_mode"] = None

        return payload

    def gather_request_data(self, config, progressDialog):
        fusion = Fusion()
        payload = self.jsonify(config)

        material_name = 'Aluminum, 6061-T6'
        if progressDialog is not None:
            progressDialog.progressValue += 1
        user = fusion.getUser()
        if progressDialog is not None:
            progressDialog.progressValue += 1
        if self.needs_cancel():
            return "cancelled"

        step_str = payload.get("step_file_content", None)
        body = self.get_body()
        if step_str is None:
            step_str, _ = get_step_file_content(fusion, body.parentComponent)
        if self.needs_cancel():
            return "cancelled"

        if progressDialog is not None:
            progressDialog.progressValue += 1

        docname = fusion.getApplication().activeDocument.name
        name = "{} - {}".format(docname, body.name)

        data = {
            "subtypekey": "RequestQuoteAssistant",
            "stepFile": step_str,
            "fusionUserId": user.userId,
            "fusionUserEmail": user.email,
            "name": name,
            "body_name": body.name,
            "toolLibraries": None,
            "material": material_name,
            "presetNaming": None,
            "product": 'QA',
            "product_specific_data": payload,
        }
        return data

    def get_and_store_setips(self, inputs):
        self.setips = self.get_setips(inputs)
        return self.setips

    def poll_for_program(self, polling_link, progress_dialog):
        """
        Poll the server until program is ready or error occurs.
        Uses chunked sleep for better UI responsiveness.

        Returns:
            dict: The program data (ResponseImportProgram format) or None on error/cancel
        """
        config = load_config()
        max_polls = config.get("CA_max_polls", 900)
        poll_interval = 2.0  # seconds

        client = Client(config)

        for i in range(max_polls):
            # Chunked sleep for responsiveness - check cancel every 100ms
            for _ in range(int(poll_interval * 10)):
                time.sleep(0.1)
                adsk.doEvents()
                if progress_dialog.wasCancelled:
                    return None

            # Update progress message
            progress_dialog.message = f"Generating CAM program..."

            # Make polling request
            try:
                resp = client.request({
                    "subtypekey": "RequestProgramProgress",
                    "polling_link": polling_link
                }, method="POST")
            except Exception as e:
                continue

            # Check for error in response
            error = resp.get("error")
            if error:
                ui = Fusion().getUI()
                ui.messageBox(f"Error generating program: {error}")
                return None

            data = resp.get("data", {})
            progress = data.get("progress", 0)
            program = data.get("program")

            # Update progress bar
            progress_dialog.progressValue = min(progress, 99)  # Save 100 for materialization

            # Check if complete
            if progress >= 100 and program is not None:
                return program

        # Timeout
        ui = Fusion().getUI()
        ui.messageBox("Timeout waiting for program generation. Please try again.")
        return None

    def materialize_program(self, program, progress_dialog):
        """
        Import the program into the current document.
        Skips document validation since we just uploaded from this document.
        """
        # Lazy import to avoid circular import
        from ..lib.import_program_utils import ImportProgram

        if self.import_program is None:
            self.import_program = ImportProgram()

        fusion = Fusion()
        doc, design = get_current_design_doc()

        def needs_cancel():
            adsk.doEvents()
            return progress_dialog.wasCancelled

        self.import_program.materialize_response(
            fusion=fusion,
            design=design,
            doc=doc,
            needs_cancel=needs_cancel,
            progressDialog=progress_dialog,
            use_workholding=False,
            use_stock=True,
            viseStyle=None,
            use_existing_document=True,
            resp=program,
            config=self.config,
        )

    def onCommandExecute(self, args):
        command = args.firingEvent.sender
        inputs = command.commandInputs

        fusion = Fusion()
        ui = fusion.getUI()

        # Setup progress dialog
        self.progressDialog = ui.createProgressDialog()
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = True
        self.progressDialog.show('Automated CAM', 'Preparing upload...', 0, 100)
        self.progressDialog.progressValue = 0

        try:
            # 1. Gather setips and validate
            self.get_and_store_setips(inputs)

            if self.needs_cancel():
                self.progressDialog.hide()
                return

            if not self.confirm_export_if_issues():
                self.progressDialog.hide()
                return

            # Reload config
            self.config = load_config()

            # 2. Upload part
            self.progressDialog.message = "Uploading part to Toolpath..."
            data = self.gather_request_data(self.config, self.progressDialog)

            if data == "cancelled":
                self.progressDialog.hide()
                return

            self.progressDialog.progressValue = 10

            if self.needs_cancel():
                self.progressDialog.hide()
                return

            client = Client(self.config)
            resp = client.request(data, method="POST")

            # Open the claim URL in browser (same as send_to_toolpath)
            magic_link = resp.get("magicLink")
            if magic_link:
                webbrowser.open(magic_link)

            if self.needs_cancel():
                self.progressDialog.hide()
                return

            # 3. Check for polling support
            polling_link = resp.get("pollingLink")

            if not polling_link:
                # Fallback: no polling support, show error
                self.progressDialog.hide()
                ui.messageBox("Server does not support Automated CAM. Please use 'Send to Toolpath' instead.")
                return

            self.progressDialog.progressValue = 15

            # 4. Poll for program
            self.progressDialog.message = "Generating CAM program..."

            program = self.poll_for_program(polling_link, self.progressDialog)

            if program is None:
                self.progressDialog.hide()
                return

            # 5. Materialize the program
            self.progressDialog.message = "Importing CAM operations..."
            self.progressDialog.progressValue = 95

            self.materialize_program(program, self.progressDialog)

            self.progressDialog.progressValue = 100
            self.progressDialog.hide()

            # 6. Activate manufacturing workspace
            fusion.activateCAM()

        except Exception as e:
            self.progressDialog.hide()
            handle_error(e, show_message_box=True)
