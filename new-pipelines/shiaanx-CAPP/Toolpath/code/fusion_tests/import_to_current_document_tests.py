import time
import unittest
import os
import adsk.core

from ..lib.fusion_utils import Fusion
from ..lib.setup_utils import get_setup
from ..lib.general_utils import load_config, load_json
from ..lib.import_program_utils import ImportProgram

class SupportClassTesting():
    def __init__(self,file_name,resp_file_name):
        self.config = load_config()
        self.import_helper = ImportProgram(testing=True)
        self.name = file_name
        self.resp_name = resp_file_name
        self.doc = self.load_test_file(sourceFileName=file_name)
        self.design = self.doc.products.itemByProductType('DesignProductType')
        current_dir = os.getcwd()
        this_file = os.path.abspath(__file__)
        this_dir = os.path.dirname(this_file)
        path_to_resp = os.path.join(this_dir, "data/test_responses", resp_file_name)
        self.resp = self.load_test_response(path_to_resp)
        self.fusion = Fusion()
   

    def load_test_file(self, sourceFileName, target_project_name="Toolpath", sourceFolderName="Add-In Test Source - Do Not Edit", destFolderName = "Add-In Test Working Folder - Do Not Remove"):
        app = adsk.core.Application.get()
        ui  = app.userInterface
        data = app.data
        
        # STEP 1: Find source project and file
        sourceProjectName = target_project_name

        # STEP 2: Find destination (temp) project and folder
        destFolderName = 'TempFolder'

        # --- Find source project ---
        sourceProject = None
        for proj in data.dataProjects:
            if proj.name == sourceProjectName:
                sourceProject = proj
                break
        if not sourceProject:
            ui.messageBox(f'Project "{sourceProjectName}" not found.')
            return

        # --- Find source folder ---
        sourceFolder = None
        for folder in sourceProject.rootFolder.dataFolders:
            if folder.name == sourceFolderName:
                sourceFolder = folder
                break
        if not sourceFolder:
            ui.messageBox(f'Folder "{sourceFolderName}" not found.')
            return

        # --- Find the data file ---
        sourceFile = None
        for f in sourceFolder.dataFiles:
            if f.name == sourceFileName:
                sourceFile = f
                break
        if not sourceFile:
            ui.messageBox(f'File "{sourceFileName}" not found.')
            return
        
        # --- Find destination folder (create if needed) ---
        destFolder = None
        for folder in sourceProject.rootFolder.dataFolders:
            if folder.name == destFolderName:
                destFolder = folder
                break

        if not destFolder:
            ui.messageBox(f'Folder "{destFolderName}" not found.')
            return   

        # --- Copy the file ---
        self.copiedFile = sourceFile.copy(destFolder)

        if not self.copiedFile:
            ui.messageBox('Failed to copy file.')
            return

        # --- Open the copied file ---
        doc = app.documents.open(self.copiedFile, True)

        return doc
    
    def needs_cancel(self):

        return False

    def wait_operations(self,setup : adsk.cam.Setup):
        assert isinstance(setup, adsk.cam.Setup)
        for ope in setup.operations:
            while True:
                if not ope.isGenerating:
                    break  # Exit the loop when generation is complete
                else:
                    time.sleep(0.1)  # Wait for a one second before checking again
    
    def check_op_failures(self):
        failed_ops = []
        nops = 0
        if self.resp['setips_subtypekey'] == "UserSpecifiedSetips":
            setups = [get_setup(self.fusion, s["operationId"]) for s in self.resp["setops"]]
        elif self.resp['setips_subtypekey'] == "AutoSetips":
            setups = self.fusion.getCAM().setups
        else:
            raise Exception("Unreachable")
        for setup in setups:
            self.wait_operations(setup)
            for op in setup.operations:
                nops += 1
                if self.op_has_problems(op):
                    failed_ops.append(op)
        return failed_ops,nops

    def op_is_manual(self,op):
        return op.strategy == 'manual'

    def op_has_problems(self,op):
        if self.op_is_manual(op):
            return not op.name.startswith(("Debug",))
        else:
            return op.hasWarning or op.hasError or not op.hasToolpath or not op.isToolpathValid

    
    def load_test_response(self,resp_path:str):
        resp_json = load_json(resp_path) 

        return resp_json
    
    def close_test_file(self,doc):
        doc.close(saveChanges = False)


class TestNoComponent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "no_component_test"
        resp_file = "no_component_response.json"
        test_class = SupportClassTesting(file_name=test_file,resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatiblity(self):
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(self.ref.fusion, self.ref.doc, self.ref.resp)
        self.assertTrue(resp_match)

    def test_materialize_resp(self):
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
                    fusion=self.ref.fusion, 
                    design=self.ref.design,
                    doc=self.ref.doc,
                    needs_cancel=self.ref.needs_cancel,
                    progressDialog=self.progressDialog,
                    use_workholding=False,
                    use_stock=True,
                    viseStyle=None,
                    use_existing_document=True,
                    resp=self.ref.resp,
                    config= load_config(),
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 5)

class TestNoComponentEmpty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "Empty_file_import_test"
        resp_file = "no_component_response.json"
        test_class = SupportClassTesting(file_name=test_file,resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatiblity(self):
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(self.ref.fusion, self.ref.doc, self.ref.resp)
        self.assertFalse(resp_match)

    def test_has_body(self):
        has_body = self.ref.import_helper.file_has_body(self.ref.design)
        self.assertFalse(has_body)

    def test_materialize_resp(self):
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
                    fusion=self.ref.fusion, 
                    design=self.ref.design,
                    doc=self.ref.doc,
                    needs_cancel=self.ref.needs_cancel,
                    progressDialog=self.progressDialog,
                    use_workholding=False,
                    use_stock=True,
                    viseStyle=None,
                    use_existing_document=False,
                    resp=self.ref.resp,
                    config= load_config(),
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 5)


class TestFlatToolpath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "Flat_toolpath_test"
        resp_file = "flat_toolpath_test_response.json"
        test_class = SupportClassTesting(file_name=test_file,resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatiblity(self):
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(self.ref.fusion, self.ref.doc, self.ref.resp)
        self.assertTrue(resp_match)
        pass

    def test_materialize_resp(self):
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
                    fusion=self.ref.fusion, 
                    design=self.ref.design,
                    doc=self.ref.doc,
                    needs_cancel=self.ref.needs_cancel,
                    progressDialog=self.progressDialog,
                    use_workholding=False,
                    use_stock=True,
                    viseStyle=None,
                    use_existing_document=True,
                    resp=self.ref.resp,
                    config= load_config(),
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 8)


class TestBikeClamp4Vises(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "Bike Clamp -Top Manufacturing 4 Vises_Sandy test"
        resp_file = "bike_clamp_4_vises.json"
        test_class = SupportClassTesting(file_name=test_file,resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatiblity(self):
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(self.ref.fusion, self.ref.doc, self.ref.resp)
        self.assertTrue(resp_match)
        pass

    def test_materialize_resp(self):
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
                    fusion=self.ref.fusion, 
                    design=self.ref.design,
                    doc=self.ref.doc,
                    needs_cancel=self.ref.needs_cancel,
                    progressDialog=self.progressDialog,
                    use_workholding=False,
                    use_stock=True,
                    viseStyle=None,
                    use_existing_document=True,
                    resp=self.ref.resp,
                    config= load_config(),
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 18)

class TestRidgebackClip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "ridgeback-clip-machining-self-contained"
        resp_file = "ridgeback-clip-machining_self_contained.json"
        test_class = SupportClassTesting(file_name=test_file,resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatiblity(self):
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(self.ref.fusion, self.ref.doc, self.ref.resp)
        self.assertTrue(resp_match)
        pass

    @unittest.expectedFailure
    def test_materialize_resp(self):
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
                    fusion=self.ref.fusion, 
                    design=self.ref.design,
                    doc=self.ref.doc,
                    needs_cancel=self.ref.needs_cancel,
                    progressDialog=self.progressDialog,
                    use_workholding=False,
                    use_stock=True,
                    viseStyle=None,
                    use_existing_document=True,
                    resp=self.ref.resp,
                    config= load_config(),
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 8)

class TestBottleOpener(unittest.TestCase):
    #test for rigid joint between toolpath geometry and part
    @classmethod
    def setUpClass(cls):
        test_file = "Bottle_Opener_Machining_Crash_testing"
        resp_file = "bottle_opener_response.json"
        test_class = SupportClassTesting(file_name=test_file,resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatiblity(self):
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(self.ref.fusion, self.ref.doc, self.ref.resp)
        self.assertTrue(resp_match)
        pass

    #@unittest.expectedFailure
    def test_materialize_resp(self):
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
                    fusion=self.ref.fusion, 
                    design=self.ref.design,
                    doc=self.ref.doc,
                    needs_cancel=self.ref.needs_cancel,
                    progressDialog=self.progressDialog,
                    use_workholding=False,
                    use_stock=True,
                    viseStyle=None,
                    use_existing_document=True,
                    resp=self.ref.resp,
                    config= load_config(),
                )
        op_failures,nops = self.ref.check_op_failures()
     
        # expected op failures: 
        # setup1 - 6 adaptive; 16 2d Countour
        # setup2 - 2 pocket clearing
        # JSG Note: I am seeing 4 failures, with one extra on setup2: 7 2d Pocket. Sandy wasn't able to replicate so I think its an os-x specific bug. 
        self.assertEqual(len(op_failures), 3)

        self.assertEqual(nops, 34)


class TestImportCurrentDocWithStock(unittest.TestCase):
    """Test import to current document with stock entity token reuse."""

    @classmethod
    def setUpClass(cls):
        test_file = "ridgeback-clip-machining-self-contained"
        resp_file = "ridgeback-clip-machining_self_contained_with_stock.json"
        test_class = SupportClassTesting(file_name=test_file, resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_document_compatibility(self):
        """Response should match the current document."""
        resp_match = self.ref.import_helper.confirm_resp_matches_doc(
            self.ref.fusion, self.ref.doc, self.ref.resp
        )
        self.assertTrue(resp_match)

    def test_stock_entity_token_in_response(self):
        """Verify response contains stock_entityToken."""
        self.assertIn("stock_entityToken", self.ref.resp)
        token = self.ref.resp["stock_entityToken"]
        self.assertIsNotNone(token)
        self.assertGreater(len(token), 0)

    def test_stock_body_findable_by_token(self):
        """The stock_entityToken should resolve to a body in the current document."""
        design = self.ref.fusion.getDesign()
        token = self.ref.resp["stock_entityToken"]
        entities = design.findEntityByToken(token)
        self.assertEqual(len(entities), 1,
            f"Expected exactly 1 entity for stock token, got {len(entities)}")

    def test_materialize_resp(self):
        """Import into current doc with stock reuse should succeed."""
        self.progressDialog = self.ref.fusion.getUI().createProgressDialog()
        message = f"Testing {self.ref.name} with response {self.ref.resp_name}"
        self.progressDialog.cancelButtonText = 'Cancel'
        self.progressDialog.isBackgroundTranslucent = False
        self.progressDialog.isCancelButtonShown = False
        self.progressDialog.show("Test import program progress", message, 0, 100)
        self.progressDialog.hide()
        self.ref.import_helper.materialize_response(
            fusion=self.ref.fusion,
            design=self.ref.design,
            doc=self.ref.doc,
            needs_cancel=self.ref.needs_cancel,
            progressDialog=self.progressDialog,
            use_workholding=False,
            use_stock=True,
            viseStyle=None,
            use_existing_document=True,
            resp=self.ref.resp,
            config=load_config(),
        )
        op_failures, nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertGreater(nops, 0)

    def test_stock_reused_not_reimported(self):
        """After import, the stock should be the original body (not a new import).

        We verify this by checking that a setup with solid stock mode exists,
        confirming the entity token reuse path was used instead of STEP import.
        """
        setups = self.ref.fusion.getCAM().setups
        has_stock_setup = False
        for setup in setups:
            stock_mode_param = setup.parameters.itemByName("job_stockMode")
            if stock_mode_param and stock_mode_param.value.value == "solid":
                has_stock_setup = True
                break
        self.assertTrue(has_stock_setup, "Expected setup with solid stock mode")