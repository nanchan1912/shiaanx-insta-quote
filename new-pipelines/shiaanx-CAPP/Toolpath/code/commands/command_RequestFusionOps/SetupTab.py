import adsk.core, adsk.fusion
from ...lib.fusion_utils import Fusion
from ...lib.general_utils import isdebug, resource_path, log, handle_error, load_json
from ...lib.component_utils import FusionFullPath
from . import logic

def get_name_from_toollib_url(url):
    return url.split("/")[-1]

class SetupTab:

    def __init__(self, tab,
        cutdata,
        parent,
        config,
        inputs,
        product,
        enable_CuttingConfigTab,
        ):
        assert product in ("CA", "QA")
        self.product = product
        self.config = config
        self.parent = parent
        self.cutdata = cutdata
        self.enable_CuttingConfigTab = enable_CuttingConfigTab
        fusion_paths = FusionFullPath()

        self.setup_checkboxes = []
        self.tab = tab
        fusion = Fusion()
        bodies = fusion.get_visible_bodies()
        self.body = None
        if len(bodies) > 0:
            self.body = bodies[-1] # hack

        self.selectors = dict() # keys are config keys values indicate how to retrieve the selection. E.g. item.value, item.selected.name...

        cam = fusion.getCAM()

        if self.enable_CuttingConfigTab:
            # cut config
            self.bundle_selector = self.tab.children.addDropDownCommandInput("cut_bundle", "Cutting Config", adsk.core.DropDownStyles.TextListDropDownStyle)
            numRows = 6
            isReadOnly = True
            name = "Selected Tool Libraries"
            self.bundle_textbox = self.tab.children.addTextBoxCommandInput("cut_bundle_textbox", name, "", numRows, isReadOnly)
            self.refresh_bundle_selector()
            self.refresh_bundle_textbox()

        # setup_mode
        setups = cam.setups
        setup_mode = self.tab.children.addDropDownCommandInput("setup_mode", "Setups", adsk.core.DropDownStyles.TextListDropDownStyle)
        self.idx_auto_setups = 0
        self.idx_use_existing_setups = 1
        if len(setups) > 0:
            setup_mode.listItems.add("Generate Automatically", False)
            setup_mode.listItems.add("Use Existing Setups", True)
            self.setup_mode_idx = self.idx_use_existing_setups
            want_auto_setups = False
        else:
            setup_mode.listItems.add("Generate Automatically", True)
            self.setup_mode_idx = self.idx_auto_setups
            want_auto_setups = True

        ### body selector
        sel_body = self.tab.children.addSelectionInput("AutoSetips_body", "Model", "Select model")
        sel_body.addSelectionFilter("SolidBodies")
        sel_body.setSelectionLimits(0, 1) # it is important to allow for 0 bodies. Otherwise there are strange fusion bugs see https://github.com/toolpath/ToolpathPackages/pull/2111
        sel_body.isVisible = want_auto_setups

        setup_mode.tooltip = """Generate a series of setups automatically or use your existing setups."""

        isCheckBox = True
        self.setup_checkboxes.clear()
        for setup in setups:
            bodies = fusion_paths.get_bodies(setup)
            nbodies = len(bodies)
            initialValue : bool = nbodies == 1
            setup_name_lower = setup.name.lower()
            for keyword in ("probe", "probing"):
                if keyword in setup_name_lower:
                    initialValue = False
            # setup.isActive
            selector_id : str = self.get_setup_selector_id(setup)
            assert isinstance(initialValue, bool)
            assert isinstance(selector_id, str)
            resourceFolder = ""
            if nbodies > 1:
                initialValue = False
                name = f"{setup.name} (Not supported: {nbodies}  > 1 bodies)"
            else:
                name = setup.name

            selector = self.tab.children.addBoolValueInput(selector_id, name, isCheckBox, resourceFolder, initialValue)
            self.setup_checkboxes.append(selector)

            if nbodies == 1:
                self.body = bodies[0]
            selector.tooltip = """Select if this setup should be populated with operations.
            WARNING: Selecting a setup, will overwrite any existing operations for that setup.
            """

        # assert self.body is not None
        
        self.data_from_julia = load_json(resource_path("data_from_julia.json")) # should we drop this? Only used for old feeds and speeds
        if isdebug():
            if self.product == "CA":
                self.addCheckbox(self.tab.children,
                    config_name="use_FusionTP_server",
                    description="Use local server",
                    tooltip="Use local FusionTP server. If false AWS lambda will be used.",
                )

            # select path text box
            select_path_selector = self.tab.children.addStringValueInput('select_paths_selector_id', 'Machining strategy', config["select_path"])
            select_path_selector.tooltip = """Advanced option, that allows to change the machining strategy algorithm. Some possible values are:
            * greedy_v9
            * AI
            Please make sure you spell the algorithm correctly."""

            if self.product == "CA":
                self.addCheckbox(self.tab.children,
                    config_name="generate_toolpaths",
                    description="Generate toolpaths",
                    tooltip="""If true toolpaths will be generated in fusion. If false, operations will be generated, 
                    but you need to click "Generate" manually in order to get the toolpaths.""",
                )


        self.need_set_default_AutoSetups_body = True
        self.update_visibilities(inputs)

    def set_AutoSetups_default_body_if_needed(self, inputs):
        # From the fusion docs:
        # addSelection 	Adds the selection to the list of selections associated with this input. 
        # This method is not valid within the commandCreated event but must be used later in the command lifetime. 
        # If you want to pre-populate the selection when the command is starting, you can use this method in the activate method of the Command. 
        # It's also valid to use in other events once the command is running, such as the validateInputs event.
        if not self.need_set_default_AutoSetups_body:
            return 
        sel_body = adsk.core.SelectionCommandInput.cast(inputs.itemById('AutoSetips_body'))
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

    def onActivate(self, args):
        inputs = args.command.commandInputs
        self.set_AutoSetups_default_body_if_needed(inputs)

    def update_visibilities(self, inputs):
        assert self.setup_mode_idx in (self.idx_auto_setups, self.idx_use_existing_setups)
        isvisible_auto_setups = self.setup_mode_idx == self.idx_auto_setups
        isvisible_existing_setups = not isvisible_auto_setups

        for input in self.setup_checkboxes:
            adsk.core.BoolValueCommandInput.cast(input).isVisible = isvisible_existing_setups

        sel_body = adsk.core.SelectionCommandInput.cast(inputs.itemById('AutoSetips_body'))
        sel_body.isVisible = isvisible_auto_setups


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
            sel_input = adsk.core.SelectionCommandInput.cast(inputs.itemById('AutoSetips_body'))
            setup_selection_isvalid = sel_input.selectionCount == 1
        elif idx == self.idx_use_existing_setups:
            for selecetor in self.setup_checkboxes:
                if selecetor.value:
                    setup_selection_isvalid = True
                    break
        else:
            raise Exception("Unreachable")
        return setup_selection_isvalid

    def addCheckbox(self, inputs, config_name, description, tooltip):
        isCheckBox = True
        resourceFolder = ""
        key = config_name 
        initialValue = self.config[key]
        use_pre_roughing_selector = inputs.addBoolValueInput(key, description, isCheckBox, resourceFolder, initialValue)
        use_pre_roughing_selector.tooltip = tooltip
        if key in self.selectors.keys():
            msg = f"""
            Key {key} already exists in self.selectors
            """
            raise Exception(msg)
            
        self.selectors[key] = ".value"

    def get_setup_mode_selector(self, inputs):
        return adsk.core.DropDownCommandInput.cast(inputs.itemById('setup_mode'))

    def get_bundle_selector(self, inputs):
        assert self.enable_CuttingConfigTab
        return adsk.core.DropDownCommandInput.cast(inputs.itemById('cut_bundle'))

    def want_AutoSetips(self, inputs) -> bool:
        s = self.get_setup_mode_selector(inputs)
        return s.selectedItem.index == self.idx_auto_setups

    def want_UserSpecifiedSetips(self, inputs) -> bool:
        s = self.get_setup_mode_selector(inputs)
        return s.selectedItem.index == self.idx_use_existing_setups

    def refresh_bundle_selector(self):
        assert self.enable_CuttingConfigTab
        selected_index = self.cutdata["selectedBundleIndex"]
        s = self.bundle_selector
        s.listItems.clear()
        for (index, bundle) in enumerate(self.cutdata["bundles"]):
            isselected = index == selected_index
            s.listItems.add(bundle['name'], isselected)

    def refresh_bundle_textbox(self):
        assert self.enable_CuttingConfigTab
        selected_index = self.cutdata["selectedBundleIndex"]
        tb = self.bundle_textbox
        bundle = self.cutdata["bundles"][selected_index]
        names = [get_name_from_toollib_url(url) for url in bundle["selectedToollibURLs"]]
        if len(names) == 0:
            bundle_name = bundle["name"]
            tb.text = f"Please select tool libraries for '{bundle_name}' in the Cutting Config Tab"
        else:
            tb.text = "\n".join(names)

    def onPreview(self, args):
        pass

    def receiveMsg(self, msg):
        subtypekey = msg["subtypekey"]
        if subtypekey == "selectedBundleIndexSetFromMaterialTab":
            self.refresh_bundle_selector()
            self.refresh_bundle_textbox()
        elif subtypekey == "ToolLibTab_selectionChanged":
            self.refresh_bundle_textbox()
        else:
            raise Exception(f"Unrecognized subtypekey: {subtypekey}")

    def onInputsChanged(self, args):
        eventArgs = adsk.core.InputChangedEventArgs.cast(args)
        changedInput = eventArgs.input
        inputs = eventArgs.firingEvent.sender.commandInputs
        self.set_AutoSetups_default_body_if_needed(inputs)
        # update axes selection input visibility
        if changedInput.id == 'setup_mode':
            s = self.get_setup_mode_selector(inputs)
            idx = s.selectedItem.index
            self.setup_mode_idx = idx

        if changedInput.id == "cut_bundle":
            s = self.get_bundle_selector(inputs)
            if s.selectedItem is not None:
                index = s.selectedItem.index
                self.parent.receiveMsg(
                    {
                        "subtypekey" : "selectedBundleIndexSetFromSetupTab",
                        "selectedBundleIndex" : index,
                    }
                )
                self.refresh_bundle_textbox()

        self.update_visibilities(inputs)

    def update_config(self, inputs):
        self.config["deburr"] = False
        for (key, extract) in self.selectors.items():
            item = inputs.itemById(key)
            if extract == ".value":
                val = item.value
            elif extract == ".selectedItem.name":
                val = item.selectedItem.name
            else:
                raise Exception(f"TODO: {extract = }")
            self.config[key] = val

        if isdebug():
            select_path = inputs.itemById('select_paths_selector_id').value
            self.config["select_path"] = select_path


        return self.config


    def get_setips(self, inputs):
        fusion = Fusion()
        if self.want_UserSpecifiedSetips(inputs):
            cam = fusion.getCAM()
            setups = cam.setups
            if len(setups) == 0:
                raise Exception(f"No setup found. Please create a setup or use auto setup mode.")
            setips = self.create_UserSpecifiedSetips(inputs)
        elif self.want_AutoSetips(inputs):
            sel_input = adsk.core.SelectionCommandInput.cast(inputs.itemById('AutoSetips_body'))
            if sel_input.selectionCount == 0:
                raise Exception("Please select a model to machine.")
            assert sel_input.selectionCount == 1
            self.body = sel_input.selection(0).entity
            assert isinstance(self.body, adsk.fusion.BRepBody)
            setips = self.create_AutoSetips(inputs)
        else:
            raise Exception(f"Unreachable")
        return setips


    def create_UserSpecifiedSetips(self, inputs) -> logic.UserSpecifiedSetips:
        # setups
        fusion = Fusion()
        cam = fusion.getCAM()
        setips = []
        for setup in cam.setups:
            selector_id = self.get_setup_selector_id(setup)
            selected : bool = inputs.itemById(selector_id).value
            setip = logic.UserSpecifiedSetip(setup, compute_fusionops=selected)
            setips.append(setip)
        return logic.UserSpecifiedSetips(setips)

    def create_AutoSetips(self, inputs) -> logic.AutoSetips:
        return logic.AutoSetips(
            body=self.body,
            fusion = Fusion(),
        )

    def get_setup_selector_id(self, setup):
        id = setup.operationId
        return f"setup_selector_{id}"
