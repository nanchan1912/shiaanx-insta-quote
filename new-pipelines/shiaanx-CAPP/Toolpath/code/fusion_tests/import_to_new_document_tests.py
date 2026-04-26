import time
import unittest
import os
import adsk.core

from ..lib.fusion_utils import Fusion
from ..lib.general_utils import load_config, load_json
from ..lib.import_program_utils import ImportProgram
from ..commands.command_RequestFusionOps import UserSpecifiedSetips, AutoSetips

class SupportClassTesting():
    def __init__(self,resp_file_name):
        self.config = load_config()
        # Force generate_toolpaths for tests, regardless of user config
        self.config["generate_toolpaths"] = True
        self.import_helper = ImportProgram(testing=True)
        self.resp_name = resp_file_name
        this_file = os.path.abspath(__file__)
        this_dir = os.path.dirname(this_file)
        path_to_resp = os.path.join(this_dir, "data/test_responses", resp_file_name)
        self.resp = self.load_test_response(path_to_resp)
        self.fusion = Fusion()
        ui = self.fusion.getUI()
        self.progressDialog = ui.createProgressDialog()
        self.doc, self.design = self.import_helper.get_doc_and_design(self.resp, self.fusion, self.progressDialog,use_existing_document=False)


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
        setups = self.fusion.getCAM().setups

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

    def check_wcs_stock_box_point_origin(self):
        """
        Verify all setups have WCS origin set to stock box point (top center).

        Returns a list of tuples (setup_name, origin_mode, box_point) for setups
        that don't have the expected settings.
        """
        failures = []
        setups = self.fusion.getCAM().setups

        for setup in setups:
            origin_mode_param = setup.parameters.itemByName("wcs_origin_mode")
            box_point_param = setup.parameters.itemByName("wcs_origin_boxPoint")

            origin_mode = origin_mode_param.value.value if origin_mode_param else None
            box_point = box_point_param.value.value if box_point_param else None

            if origin_mode != "stockPoint" or box_point != "top center":
                failures.append((setup.name, origin_mode, box_point))

        return failures

    
    def load_test_response(self,resp_path:str):
        resp_json = load_json(resp_path) 

        return resp_json
    
    def close_test_file(self,doc):
        doc.close(saveChanges = False)


class TestNoComponent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resp_file = "no_component_response.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_import_document(self):

        message = f"Testing response {self.ref.resp_name}"
        self.progressDialog = self.ref.progressDialog
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
                    config=self.ref.config,
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 5)

class TestNoComponentWorkholding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resp_file = "no_component_response.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_import_document(self):

        message = f"Testing response {self.ref.resp_name}"
        self.progressDialog = self.ref.progressDialog
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
                    use_workholding=True,
                    use_stock=True,
                    viseStyle="Self Centering Vise",
                    use_existing_document=False,
                    resp=self.ref.resp,
                    config=self.ref.config,
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 5)

class TestNestedPocketStepAvoidSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resp_file = "nested_pocket_with_step_surface.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_import_document(self):

        message = f"Testing response {self.ref.resp_name}"
        self.progressDialog = self.ref.progressDialog
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
                    use_stock=False,
                    viseStyle="Self Centering Vise",
                    use_existing_document=False,
                    resp=self.ref.resp,
                    config=self.ref.config,
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 1)
        self.assertEqual(nops, 11)


class TestBikeClamp4Vises(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resp_file = "bike_clamp_4_vises.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_import_document(self):
    
        message = f"Testing response {self.ref.resp_name}"
        self.progressDialog = self.ref.progressDialog
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
                    config=self.ref.config,
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 18)

class TestBikeClampToolOrientation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resp_file = "bike_clamp_tool_orientation_response.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_import_document(self):

        message = f"Testing response {self.ref.resp_name}"
        self.progressDialog = self.ref.progressDialog
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
                    config=self.ref.config,
                )
        op_failures,nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertEqual(nops, 18)


class TestWCSStockBoxPointOrigin(unittest.TestCase):
    """
    Test that all setups have WCS origin set to stock box point (top center).

    This verifies the fix that sets wcs_origin_mode to 'stockPoint' and
    wcs_origin_boxPoint to 'top center' for all AbsoluteCoordDef setups.
    """
    @classmethod
    def setUpClass(cls):
        # Use a response with multiple setups
        resp_file = "bike_clamp_4_vises.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_wcs_origin_stock_box_point(self):
        """Verify all setups use stock box point origin at top center."""
        message = f"Testing WCS stock box point origin"
        self.progressDialog = self.ref.progressDialog
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
            config=load_config(),
        )

        # Check that all setups have WCS origin set to stock box point (top center)
        wcs_failures = self.ref.check_wcs_stock_box_point_origin()

        # Verify no failures
        self.assertEqual(
            len(wcs_failures), 0,
            f"Setups with incorrect WCS origin: {wcs_failures}"
        )

        # Also verify we have multiple setups (this test is meaningful with multiple setups)
        num_setups = len(list(self.ref.fusion.getCAM().setups))
        self.assertGreater(num_setups, 1, "Test should have multiple setups to be meaningful")


class TestImportWithStock(unittest.TestCase):
    """Test import to new document with stock entity token in response."""

    @classmethod
    def setUpClass(cls):
        resp_file = "ridgeback-clip-machining_self_contained_with_stock.json"
        test_class = SupportClassTesting(resp_file_name=resp_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_response_has_stock_entity_token(self):
        """Verify test response contains stock_entityToken."""
        self.assertIn("stock_entityToken", self.ref.resp)
        self.assertIsNotNone(self.ref.resp["stock_entityToken"])

    def test_import_document(self):
        """Import with stock should create setups and operations without failures."""
        message = f"Testing response {self.ref.resp_name}"
        self.progressDialog = self.ref.progressDialog
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
            config=self.ref.config,
        )
        op_failures, nops = self.ref.check_op_failures()
        self.assertEqual(len(op_failures), 0)
        self.assertGreater(nops, 0)

    def test_stock_setup_created(self):
        """Verify that a setup with stock was created (stock mode is 'solid')."""
        setups = self.ref.fusion.getCAM().setups
        has_stock_setup = False
        for setup in setups:
            stock_mode_param = setup.parameters.itemByName("job_stockMode")
            if stock_mode_param and stock_mode_param.value.value == "solid":
                has_stock_setup = True
                break
        self.assertTrue(has_stock_setup, "Expected at least one setup with solid stock mode")

