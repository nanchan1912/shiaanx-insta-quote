import adsk.core, adsk.fusion
from ...lib.event_utils import command_id_from_name, add_handler
from ...lib.fusion_utils import Fusion
from ...lib.general_utils import resource_path, log, handle_error, load_config, save_config
from ...lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
from .RequestFusionOpsQA import RequestFusionOpsQA

from .CuttingConfigTab import CuttingConfigTab, load_cutdata
from .CuttingConfigTab import  ask_user_stop_if_missing_presets
from .SetupTab import SetupTab

class Cmd():

    def __init__(self, product):
        app = Fusion().getApplication()
        self.product = product
        if product == "CA":
            self.CMD_NAME = 'Run CAM Accelerator'
            self.CMD_Description = 'Create machining operations automatically.'
            self.enable_CuttingConfigTab = True
            self.config_patch = {
                "use_FusionTP_server": True,
            }
        elif product == "QA":
            self.CMD_NAME = 'Legacy Send Part to Toolpath'
            self.enable_CuttingConfigTab = False
            self.CMD_Description = 'Send the current part to Toolpath and continue in the Toolpath web app.'
            self.config_patch = {}
        else:
            raise ValueError(f"Invalid {product =}")

        self.ICON_FOLDER = resource_path("send_to_toolpath", '')
        self.CMD_ID = command_id_from_name(self.CMD_NAME)

        # Local list of event handlers used to maintain a reference so
        # they are not released and garbage collected.
        self.local_handlers = []
        self.cmd_def = None

    # Executed when add-in is run.
    def start(self):
        ui = Fusion().getUI()
        # Create a command Definition.
        self.cmd_def = addCommandToToolbar(self.CMD_ID, self.CMD_NAME, self.CMD_Description, self.ICON_FOLDER, IS_PROMOTED=True)
        
        add_handler(self.cmd_def.commandCreated, self.onCommandCreated, local_handlers=self.local_handlers)
        
    def stop(self):
        # when we restart the debugger, first 'stop' is called, then 'start' is called. 
        ui = Fusion().getUI()
        removeCommandFromToolbar(self.CMD_ID)
        

    def receiveMsg(self, msg):
        subtypekey = msg["subtypekey"]
        if subtypekey == "selectedBundleIndexSetFromSetupTab":
            if self.enable_CuttingConfigTab:
                self.cutting_config_tab.receiveMsg(msg)
        elif subtypekey == "selectedBundleIndexSetFromMaterialTab":
            self.setup_tab.receiveMsg(msg)
        elif subtypekey == "ToolLibTab_selectionChanged":
            self.setup_tab.receiveMsg(msg)
        else:
            raise Exception(f"Unrecognized subtypekey: {subtypekey}")

    def onCommandCreated(self, args):
        ui = None
        try:
            self.config = load_config()
            self.config.update(self.config_patch)
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command

            inputs = cmd.commandInputs
            setup_tab  = inputs.addTabCommandInput("main_tab_id", "Main")
            self.cutdata = load_cutdata()
            self.setup_tab = SetupTab(setup_tab,
                cutdata=self.cutdata,
                parent=self,
                product=self.product,
                config=self.config,
                inputs=inputs,
                enable_CuttingConfigTab=self.enable_CuttingConfigTab,
            )

            if self.enable_CuttingConfigTab:
                tab = inputs.addTabCommandInput("CuttingConfigTab", "Cutting config")
                self.cutting_config_tab = CuttingConfigTab(tab, 
                    cutdata=self.cutdata,
                    parent=self,
                    local_handlers=self.local_handlers,
                    incomingFromHTML=cmd.incomingFromHTML,
                    config=self.config,
                )
                # this is a bit ugly hack
                self.setup_tab.cutdata = self.cutting_config_tab.cutdata

            cmd.isExecutedWhenPreEmpted = False
            # https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-C1BF7FBF-6D35-4490-984B-11EB26232EAD
            # Specifies what the behavior will be when a command is preempted by the user executing another command. 
            # If true (the default), and all of the current inputs are valid, the command will be executed just the same as if the user clicked the OK button. 
            # If false, the command is terminated.


            add_handler(cmd.inputChanged, self.onInputsChanged, local_handlers=self.local_handlers)
            add_handler(cmd.execute, self.onCommandExecute, local_handlers=self.local_handlers)
            add_handler(cmd.executePreview, self.onPreview, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.activate, self.onActivate, local_handlers=self.local_handlers)
        except Exception as e:
            handle_error(e, True)

    def onValidateInputs(self, args):

        # toollibs

        toollib_selection_isvalid = True
        if self.enable_CuttingConfigTab:
            toollib_selection_isvalid = self.cutting_config_tab.selection_is_valid(args)
        setup_selection_isvalid = self.setup_tab.selection_is_valid(args)

        eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
        eventArgs.areInputsValid = setup_selection_isvalid and toollib_selection_isvalid

    def onPreview(self, args):
        self.setup_tab.onPreview(args)
    
    def onActivate(self, args):
        self.setup_tab.onActivate(args)
    
    def onInputsChanged(self, args):
        self.setup_tab.onInputsChanged(args)


    def onCommandExecute(self, args):
        # Here you can get the values from the inputs and execute your main logic.
        command = args.firingEvent.sender
        inputs = command.commandInputs
        self.config = self.setup_tab.update_config(inputs)

        toollibs = None
        toollibs_json = None
        preset_naming = None
        if self.enable_CuttingConfigTab:
            results = self.cutting_config_tab.get_results_and_update_config(inputs)
            toollibs = results.toollibs
            toollibs_json = results.toollibs_json
            preset_naming = results.preset_naming
            save_config(self.config)
            if ask_user_stop_if_missing_presets(results.toollibs_json, results.preset_naming):
                return None
        else:
            save_config(self.config)
        fusion = Fusion()
        setips = self.setup_tab.get_setips(inputs)

        req = RequestFusionOpsQA(fusion=fusion, 
            product=self.product,
            config=self.config, 
            toollibs=toollibs, 
            toollibs_json=toollibs_json,
            setips=setips,
            preset_naming=preset_naming,
        )
        if self.product == "CA":
            doc = fusion.app.activeDocument
            req.execute_and_materialize(doc=doc)
        elif self.product == "QA":
            req.execute()
        else:
            raise Exception("Unknown product: " + self.product)
        log(str(T))