# native import
import webbrowser

# ADSK imports
import adsk.core

# Add in imports
from ...lib.fusion_utils import Fusion, get_step_file_content, get_f3d_content_base64
from ...lib.general_utils import log
from ...lib.import_program_utils import ImportProgram
from .logic import jsonify_toollib, AutoSetips,UserSpecifiedSetips
from ...lib.client import Client

class RequestFusionOpsQA:
    def __init__(self, 
        fusion : Fusion, 
        config,
        toollibs : list,
        setips,
        preset_naming,
        body=None,
        product="CA",
        toollibs_json = None, # as an optimization
    ):
        if not product in ("QA", "CA"):
            raise Exception(f"Bad {product = }")
        if product == "CA":
            fusion.activateCAM()
            app = fusion.getApplication()
        self.progressDialog = fusion.getUI().createProgressDialog()
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.progressValue = 0

        self.fusion = fusion
        self.config = config
        self.toollibs = toollibs
        if (toollibs_json is None) and (toollibs is not None):
            toollibs_json = [jsonify_toollib(t) for t in toollibs]
        self.toollibs_json = toollibs_json
        self.preset_naming = preset_naming
        self.product = product
        self.import_program = ImportProgram()

        assert isinstance(setips, UserSpecifiedSetips) or isinstance(setips, AutoSetips)
        # TODO
        self.setips = setips
        # TODO check all setips have the same body

        if self.config["use_geometry_matcher"]:
            if body is None:
                body = self.setips.get_body()
            #self.facet_id_table = FacetIdTable(body, self.config)
            self.facet_id_table = None
            # facetIDTable doesn't exist in geometry an more.
            raise Exception("Dead code? ")
        else:
            self.facet_id_table = None

    def diagnose(self):
        return self.setips.diagnose()

    def get_body(self):
        return self.setips.get_body()

    def jsonify(self) -> dict:
        config = self.config
        fusion = self.fusion
        body = self.get_body()

        if self.facet_id_table is None:
            geometry = None
        else:
            geometry = self.facet_id_table.jsonify()
        payload = {
            "subtypekey": "RequestFusionOpsQA",
            "setips" : self.setips.jsonify(),
            "geometry": geometry,
            "tool_libraries": self.toollibs_json,
            "preset_naming" : self.preset_naming,
            "body_name" : body.name,
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

        if config["request_include_f3d"] and config["use_FusionTP_server"]:
            f3d_content = get_f3d_content_base64(fusion)
            payload["f3d_content_base64"] = f3d_content
        return payload

    def needs_cancel(self):
        adsk.doEvents()
        if self.progressDialog.wasCancelled:
            log("needs_cancel")
            return True
        else:
            return False

    def run_QA(self,*,
        product_specific_data,
        open_magic_link=True,
    ):


        assert self.product in ("CA", "QA")
        material_name = 'Aluminum, 6061-T6' # TODO remove this hack. It may not even be needed by the frontend anymore
        self.progressDialog.progressValue += 1
        user = self.fusion.getUser()
        self.progressDialog.progressValue += 1
        if self.needs_cancel(): return "cancelled"
        # Get step content directly from body (not from product_specific_data)
        body = self.get_body()
        step_str, _ = get_step_file_content(self.fusion, body.parentComponent, debug_name="debug-part-fallback")
        if self.needs_cancel(): return "cancelled"

        self.progressDialog.progressValue += 1

        docname = self.fusion.getApplication().activeDocument.name
        name = "{} - {}".format(docname, body.name)
        # build payload
        data = {
            "subtypekey": "RequestQuoteAssistant",
            "stepFile": step_str,
            "fusionUserId" : user.userId,
            "fusionUserEmail" : user.email,
            "name" : name,
            "body_name" : body.name,
            "toolLibraries" : self.toollibs_json,
            "material" : material_name,
            "presetNaming" : self.preset_naming,
            "product" : self.product,
            "product_specific_data" : product_specific_data,
        }
        self.progressDialog.progressValue += 1
        if self.needs_cancel(): return "cancelled"
        client = Client(self.config)
        resp = client.request(data, method="POST")
        if self.needs_cancel(): return "cancelled"
        if open_magic_link:
            magicLink = resp["magicLink"]
            webbrowser.open(magicLink)
        return resp


    def confirm_export_if_issues(self) -> bool:
        issues = self.diagnose()
        if len(issues) == 0:
            return True

        text = "\n\n".join(issues)
        msg = f"""
        We encountered issues with the current document:

        {text}
        """
        ui = self.fusion.getUI()
        title = "Warning"
        # buttons = adsk.core.MessageBoxButtonTypes.OKCancelButtonType
        # result = ui.messageBox(msg, title, buttons)
        # return result == adsk.core.DialogResults.DialogOK
        ui.messageBox(msg, title)
        return False

    def execute(self):
        if self.needs_cancel(): return "cancelled"
        if not self.confirm_export_if_issues():
            return "cancelled"
        client = Client(self.config)
        fusion = self.fusion
        config = self.config
        payload = self.jsonify()
        #if config["run_QA_with_CAM"]:
        if self.product == "CA":
            self.progressDialog.show('Toolpath', 'Running Toolpath. Check your browser results.', 0, 100)
        elif self.product == "QA":
            self.progressDialog.show('Toolpath', 'Uploading to Toolpath. Please check your browser for results.', 0, 100)
        else:
            raise Exception(f"Bad {self.product = }")
        use_FusionTP_server = config["use_FusionTP_server"]
        #TODO streamline this request and the plain QA request
        resp = self.run_QA(
            product_specific_data=payload,
            open_magic_link= (self.product == "QA") or (not use_FusionTP_server)
        )
        if resp == "cancelled":
            return 
        
        self.progressDialog.progressValue = 100
        self.progressDialog.hide()

        if self.product == "QA":
            ui = fusion.getUI()
            msg_box = ui.messageBox(
                "Your model has been uploaded to Toolpath and can be found on your Projects page.\n\nPlease check your browser for results.",
                "Upload Complete",
                adsk.core.MessageBoxButtonTypes.OKButtonType,
            )
            # no need to compute ops etc
        return resp

    def execute_and_materialize(self,*,doc):
        # used for creating and debugging FusionTP tests
        assert self.product == "CA"
        resp = self.execute()["data"]

        ui = self.fusion.getUI()
        progressDialog = ui.createProgressDialog()
        self.import_program.materialize_response(
            fusion=self.fusion, 
            progressDialog=progressDialog,
            doc=doc,
            use_workholding=False,
            viseStyle=None,
            use_existing_document=True,
            resp=resp,
            toollibs=self.toollibs,
            use_stock=False,
            config=self.config,
        )
        progressDialog.hide()