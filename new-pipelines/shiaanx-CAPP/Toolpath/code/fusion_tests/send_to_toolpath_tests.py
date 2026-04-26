import unittest
from unittest.mock import Mock
import adsk.core
import re


from ..lib.fusion_utils import Fusion
from ..lib.setup_utils import get_setup_selector_id
from ..lib.general_utils import load_config, decompress_step_content, extract_step_body_names
from ..commands.command_RequestFusionOps import logic
from ..commands import command_send_to_toolpath


def verify_body_name_matches_step(test_case, step_content, body_name_from_json, context=""):
    """
    Verify that body_name in JSON matches the solid body name in STEP file.

    STEP files contain solid body entities (MANIFOLD_SOLID_BREP or BREP_WITH_VOIDS)
    with names that should match the body_name sent in JSON requests.

    Args:
        test_case: The unittest.TestCase instance
        step_content: Decompressed STEP file content
        body_name_from_json: The body_name value from JSON request
        context: Optional context string for error messages (e.g., "product_specific_data")
    """
    brep_names = extract_step_body_names(step_content)
    test_case.assertGreater(
        len(brep_names), 0,
        "STEP file should have MANIFOLD_SOLID_BREP or BREP_WITH_VOIDS names"
    )
    context_msg = f" in {context}" if context else ""
    test_case.assertEqual(
        body_name_from_json, brep_names[0],
        f"body_name{context_msg} should match STEP file's solid body name"
    )


class SupportClassTesting():
    def __init__(self, file_name, source_subfolder=None):
        self.config = load_config()
        self.name = file_name
        self.doc = self.load_test_file(sourceFileName=file_name, sourceSubfolder=source_subfolder)
        self.test_cmd = command_send_to_toolpath.SendToToolpath(testing=True)

        self.body,self.setups = self.test_cmd.get_creation_setups_and_body()

    def generate_test_data(self, input_generation_function):
        self.inputs = self.mock_inputs(input_generation_function)
        self.setips = self.test_cmd.get_and_store_setips(self.inputs)
        self.req = self.test_cmd.gather_request_data(self.config, progressDialog=None)

    def mock_inputs(self,input_generation_function):
        inputs = Mock()
        inputs.itemById = Mock(side_effect=input_generation_function)
        return inputs

    def get_input_generator(self,use_auto_setups=True,setup_selected = None, stock_body=None):
        self.setup_selector_values = {}
        if use_auto_setups:
            idx = self.test_cmd.idx_auto_setups
        else:
            idx = self.test_cmd.idx_use_existing_setups
            fusion = Fusion()
            assert setup_selected is not None
            cam = fusion.getCAM()
            for (i,setup) in enumerate(cam.setups):
                selector_id = get_setup_selector_id(setup)
                self.setup_selector_values[selector_id] = adskCheckBox(setup_selected[i])
                if i == 0:
                    tmp_setuptype_selection = adskSelector("ThreeAxis")
        # setup test inputs
        tmp_selector = adskSelector(idx)

        tmp_selection = adskSelectionList()
        tmp_selection.add(self.body)

        tmp_stock_selection = adskSelectionList()
        if stock_body is not None:
            tmp_stock_selection.add(stock_body)

        def input_generation(key):
            if key == self.test_cmd.setup_dropdown_name:
                return tmp_selector
            if key == self.test_cmd.auto_setips_body_name:
                return tmp_selection
            if key == self.test_cmd.auto_setips_stock_body_name:
                return tmp_stock_selection
            if key == self.test_cmd.setuptype_dropdown_name:
                return tmp_setuptype_selection
            if key in self.setup_selector_values:
                return self.setup_selector_values[key]

        return input_generation

    def load_test_file(self, sourceFileName, target_project_name="Toolpath", sourceFolderName="Add-In Test Source - Do Not Edit", destFolderName = "Add-In Test Working Folder - Do Not Remove", sourceSubfolder=None):
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

        # --- Navigate to subfolder if specified ---
        if sourceSubfolder:
            for subfolder_name in sourceSubfolder.split('/'):
                found = None
                for folder in sourceFolder.dataFolders:
                    if folder.name == subfolder_name:
                        found = folder
                        break
                if not found:
                    ui.messageBox(f'Subfolder "{subfolder_name}" not found in "{sourceFolder.name}".')
                    return
                sourceFolder = found

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

        # if not destFolder:
        #     destFolder = sourceProject.rootFolder.dataFolders.add(destFolderName)

        # --- Copy the file ---
        self.copiedFile = sourceFile.copy(destFolder)

        if not self.copiedFile:
            ui.messageBox('Failed to copy file.')
            return

        # --- Open the copied file ---
        doc = app.documents.open(self.copiedFile, True)
        #ui.messageBox(f'Copied and opened: {self.copiedFile.name}')

        return doc
    

    def close_test_file(self,doc):
        doc.close(saveChanges = False)
        # Delete the copied test file from TempFolder to avoid accumulation
        if hasattr(self, 'copiedFile') and self.copiedFile:
            try:
                self.copiedFile.deleteMe()
            except:
                pass  # Ignore errors if file already deleted


class adskCheckBox():
    def __init__(self,selected):
        self.value = selected
        

class adskSelectedItem():
    def __init__(self,index=0,name=""):
        self.index = index
        self.name = name

class adskSelector():
    def __init__(self,index=0,name=""):
        self.selectedItem = adskSelectedItem(index,name)

class adskSelection():
    def __init__(self,entity):
        self.entity = entity

class adskSelectionList():
    def __init__(self):
        self.selectionCount = None
        self.selectionList = []

    def add(self, entity):
        self.selectionList.append(adskSelection(entity))
        self.selectionCount = len(self.selectionList)

    def selection(self,idx):
        return self.selectionList[idx]

# things we want to test:
# did we open the correct file
# are we getting the right kind of setup
# are we getting the right number and names of setups - setup ID?
# are we getting the correct stock body
# check that the stock body is not empty
# are we getting the correct part body
# check that the part body is not empty

class TestNoComponent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "no_component_test"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/zNQsFAwAEJDBSMgBrOMLIAsQwUTkIiZQsiBL/m/SxyLd0i3tHeu+bISrNLYTMEy0dzUzMDAUNc0LS1J1yTFLFE3KTHNUtfCLNnUwMjUxMDYPAVoGtgMBjQAts9AAWwdyHojCwsA/8P/A4gAAAA="
        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body1",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),0)


    def test_inputs(self):
        input_generator = self.ref.get_input_generator(use_auto_setups=True)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.AutoSetips)

    def test_request_data(self):
        input_generator = self.ref.get_input_generator(use_auto_setups=True)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        verify_body_name_matches_step(self, step_content, self.ref.req["body_name"], "request")
        self.assertGreater(len(step_content), 5000)

class TestRidgeBackClipAuto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "ridgeback-clip-machining-self-contained"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/32QMU5FQQhFG3s7W19iTQIzzAw038aNDMywC+3ch51bcEF2bkLeW8APISHkcLmAhxyYQUfJvKoiWdHBZ6cffw8/jx8vb7evp9/P1+f374us/ViCdbQ1wKsY8CIHLauDdKXtzDKdUyNJdrRg7YBiBDyFQXIaCi9G5rImXdwKZC/cYQQpMGGKmRcojXF7qeEYJ1e3UfO9QE1zs0fA3LWAN41Rq8cqdnLbps5JAotHTYdbU48GaKRFDjPu9f6NyuFzzAD1hsB7j3Runjf4jrHDSPj62flDkibjH8zSmntPAQAA"
        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body1",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),2)

    def test_inputs(self):
        input_generator = self.ref.get_input_generator(use_auto_setups=True)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.AutoSetips)

    def test_request_data(self):
        input_generator = self.ref.get_input_generator(use_auto_setups=True)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        self.assertGreater(len(step_content), 5000)

class TestRidgeBackClipUserSpecified(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "ridgeback-clip-machining-self-contained"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/32QMU5FQQhFG3s7W19iTQIzzAw038aNDMywC+3ch51bcEF2bkLeW8APISHkcLmAhxyYQUfJvKoiWdHBZ6cffw8/jx8vb7evp9/P1+f374us/ViCdbQ1wKsY8CIHLauDdKXtzDKdUyNJdrRg7YBiBDyFQXIaCi9G5rImXdwKZC/cYQQpMGGKmRcojXF7qeEYJ1e3UfO9QE1zs0fA3LWAN41Rq8cqdnLbps5JAotHTYdbU48GaKRFDjPu9f6NyuFzzAD1hsB7j3Runjf4jrHDSPj62flDkibjH8zSmntPAQAA"

        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body1",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),2)

    def test_inputs(self):
        setup_selected = [True, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False,setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)

    def test_request_data(self):
        setup_selected = [True, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False,setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        verify_body_name_matches_step(self, step_content, self.ref.req["body_name"], "request")
        self.assertGreater(len(step_content), 5000)

class TestWebinarCameraPart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "webinar machining camera"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/32PQQpCMQxEQY/gAezKXSBJk7bZ/qW3aNq69TDi1ouIh/IAgv3fvQwZhvBIGAwl4BQFnrMlLjNRkHWTwvWwP97Py+d5e592j+W1kTGFhOaaa4UeyUDIEdxHBi+MqtZdk012kh698ogF0qUiiOQBhZpDt6hcRiOy+v/XekCaR9CkDKLVoA7MULqhoPEQ+bVYXWJW/AJY5a7d1wAAAA=="

        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body7",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),5)

    def test_inputs(self):
        setup_selected = [False, True, True, True, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)
        self.assertEqual(len(self.ref.setips.setips),5)

    def test_request_data(self):
        setup_selected = [False, True, True, True, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        self.assertGreater(len(step_content), 5000)

class TestWebinarCameraSoftJaws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "webinar machining camera"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/32PQQpCMQxEQY/gAezKXSBJk7bZ/qW3aNq69TDi1ouIh/IAgv3fvQwZhvBIGAwl4BQFnrMlLjNRkHWTwvWwP97Py+d5e592j+W1kTGFhOaaa4UeyUDIEdxHBi+MqtZdk012kh698ogF0qUiiOQBhZpDt6hcRiOy+v/XekCaR9CkDKLVoA7MULqhoPEQ+bVYXWJW/AJY5a7d1wAAAA=="
        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)        
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body7",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),5)

    def test_inputs(self):
        setup_selected = [True, False, False, False, False]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)
        self.assertEqual(len(self.ref.setips.setips),5)

    def test_request_data(self):
        setup_selected = [True, False, False, False, False]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        self.assertGreater(len(step_content), 5000)

class TestBikeClamp4Vises(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "Bike Clamp -Top Manufacturing 4 Vises_Sandy test"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/4VP0Q1CMQhM/NQlugARKNAygIPwqh3BDxdwCR3BDz+MU7iEk9i+BQw5crkcuQNTTTiGEg+sjOtglGQqli7Xx367Oeye3/v7c769hoNTtpSVjAp1yMUCRM0gcnPImIu7m8Y4punkpQQ17ODeHaRqAUdp0Ho3lB5aj3+yOJo6BwJXNpASC4Q7gQqdivHY1dbu85cRLvUH7hls/dcAAAA="

        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body1",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),5)

    def test_inputs(self):
        setup_selected = [False,True, True, True, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)
        self.assertEqual(len(self.ref.setips.setips),5)

    def test_request_data(self):
        setup_selected = [False,True, True, True, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        self.assertGreater(len(step_content), 5000)

class TestCylindricalStockUserSpecified(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_file = "CylindricalStockTest"
        test_class = SupportClassTesting(file_name=test_file)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        ref_entityToken = "/v4BAAAARlJLZXkAH4sIAAAAAAAA/4WPzQkCMAyFN3CGLhBo0qZNEPHk0bsXD01/JnABt3AA13EHR7HtAhICj8dH3ot34vwcdDR3K5Kp0MXlJHe4hu99XJ7v8+dxet2OmwzJmdHwNAxCQYFYSMB6U8BmOHKynmqZ7CK9IVuswNoMIimCSepQWHP1FEjHnyyOlbNhhlErQRQN8wIRoHZprXRUr7v7+oU5/wDgrBVw1QAAAA=="

        self.assertEqual(self.ref.doc.name, self.ref.name + " v1")
        self.assertIsInstance(self.ref.body, adsk.fusion.BRepBody)
        self.assertEqual(ref_entityToken,self.ref.body.entityToken)
        self.assertEqual("Body1",self.ref.body.name)

        self.assertEqual(len(self.ref.setups),2)

    def test_inputs(self):
        setup_selected = [False, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False,setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)

    def test_request_data(self):
        setup_selected = [False, True]
        input_generator = self.ref.get_input_generator(use_auto_setups=False,setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)
        step_content = decompress_step_content(self.ref.req["stepFile"])
        self.assertGreater(len(step_content), 5000)


class TestHiddenOccurrenceSolidStock(unittest.TestCase):
    """
    Test that solid stock from a hidden occurrence exports correctly.

    This tests the fix for the bug where stock bodies accessed through hidden
    occurrences would produce empty STEP files. The fix uses body.nativeObject
    to get the actual body before copying to a temp component for export.

    """

    # TODO: Update these values for your test file
    TEST_FILE_NAME = "parametric_stock_in_setup_test"  # Name of the f3d file (without extension)
    SOURCE_SUBFOLDER = None  # Subfolder within "Add-In Test Source - Do Not Edit", or None
    SETUP_NAME = "Jaws"  # Name of the setup with the hidden occurrence solid stock
    NUM_SETUPS = 3  # Total number of setups in the file

    @classmethod
    def setUpClass(cls):
        test_class = SupportClassTesting(file_name=cls.TEST_FILE_NAME, source_subfolder=cls.SOURCE_SUBFOLDER)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        """Verify the test file loaded correctly."""
        self.assertEqual(self.ref.doc.name, self.TEST_FILE_NAME + " v1")
        self.assertEqual(len(self.ref.setups), self.NUM_SETUPS)

    def test_solid_stock_export(self):
        """
        Test that solid stock from a hidden occurrence produces valid STEP content.

        This is the key test for the hidden occurrence stock export bug fix.
        """
        # Find the index of the setup by name
        setup_index = None
        fusion = Fusion()
        cam = fusion.getCAM()
        for i, setup in enumerate(cam.setups):
            if setup.name == self.SETUP_NAME:
                setup_index = i
                break
        self.assertIsNotNone(setup_index, f"Setup '{self.SETUP_NAME}' not found in CAM setups")

        # Select only the target setup
        setup_selected = [i == setup_index for i in range(self.NUM_SETUPS)]
        # from ..lib.general_utils import log
        # log(f"setup_selected: {setup_selected}, setup_index: {setup_index}", force_console=True)
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)

        # Verify we got the request data
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)

        product_specific_data = self.ref.req["product_specific_data"]
        setips_wrapper = product_specific_data["setips"]
        # The actual setups list is nested inside setips_wrapper["setips"]
        setips_list = setips_wrapper["setips"]

        # Since we filter to only selected setups, the list should have exactly 1 element
        self.assertEqual(len(setips_list), 1, "Expected exactly 1 selected setup in the list")
        setup_data = setips_list[0]
        stock_info = setup_data["stock_info"]
        self.assertEqual(stock_info["subtypekey"], "FusionStockSolid")

        # The key assertion: stock STEP content should contain actual body geometry
        # Note: Stock STEP content is not compressed (compression disabled pending server-side support)
        step_content = stock_info.get("step_file_content", "")

        # Verify the STEP file is not empty
        self.assertGreater(len(step_content), 0,
            f"Stock STEP content for '{self.SETUP_NAME}' is empty.")

        # Verify the STEP file contains actual solid body geometry
        self.assertTrue(
            "MANIFOLD_SOLID_BREP" in step_content or "BREP_WITH_VOIDS" in step_content,
            "Stock STEP file missing solid body (MANIFOLD_SOLID_BREP or BREP_WITH_VOIDS)")

        self.assertIn("CLOSED_SHELL", step_content,
            "Stock STEP file missing CLOSED_SHELL - no solid geometry exported")

        # Verify the stock name in JSON matches the actual solid body name in the STEP file
        # This tests the fix for the bug where stock_solid["name"] used body.name instead of
        # extracting the actual name from the STEP file (which can differ due to Fusion's
        # internal renaming during the four-tier export process)
        brep_names = extract_step_body_names(step_content)
        self.assertGreater(len(brep_names), 0,
            "Stock STEP file should have solid body names")
        self.assertEqual(stock_info["name"], brep_names[0],
            f"stock_info['name'] ({stock_info['name']}) should match STEP file's "
            f"solid body name ({brep_names[0]})")


class TestTier2WCSCoordinateFix(unittest.TestCase):
    """
    Test that Tier 2 STEP export produces geometry in LOCAL coordinates.

    This tests the Tier 2 WCS coordinate fix using the "op1" setup from the
    parametric_stock_in_setup_test file, which triggers Tier 2 for proxy bodies
    with non-identity transforms.

    Key behavior tested:
    - TemporaryBRepManager.copy(proxy_body) returns geometry in world coordinates
    - The Tier 2 fix applies inverse transform to convert back to local coordinates
    - Julia expects geometry in local coords + stepCoordinateSystem_cm for transform
    """

    TEST_FILE_NAME = "parametric_stock_in_setup_test"
    SOURCE_SUBFOLDER = None
    SETUP_NAME = "Op1"  # This setup triggers Tier 2 for proxy body export
    NUM_SETUPS = 3

    @classmethod
    def setUpClass(cls):
        test_class = SupportClassTesting(file_name=cls.TEST_FILE_NAME, source_subfolder=cls.SOURCE_SUBFOLDER)
        cls.ref = test_class

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_initialization(self):
        """Verify the test file loaded correctly."""
        self.assertEqual(self.ref.doc.name, self.TEST_FILE_NAME + " v1")
        self.assertEqual(len(self.ref.setups), self.NUM_SETUPS)

    def test_tier2_part_geometry_in_local_coordinates(self):
        """
        Test that part STEP export via Tier 2 produces geometry in LOCAL coordinates.

        This is the key test for the Tier 2 WCS coordinate fix. The "op1" setup
        triggers Tier 2 export for proxy bodies with non-identity transforms.

        If this test fails with coordinates that are offset by the assembly transform,
        the Tier 2 inverse transform fix has regressed.
        """
        # Find the index of the setup by name
        setup_index = None
        fusion = Fusion()
        cam = fusion.getCAM()
        for i, setup in enumerate(cam.setups):
            if setup.name.strip() == self.SETUP_NAME:
                setup_index = i
                break
        self.assertIsNotNone(setup_index, f"Setup '{self.SETUP_NAME}' not found in CAM setups")

        # Select only the target setup
        setup_selected = [i == setup_index for i in range(self.NUM_SETUPS)]
        input_generator = self.ref.get_input_generator(use_auto_setups=False, setup_selected=setup_selected)

        self.ref.generate_test_data(input_generator)

        # Verify we got the request data
        self.assertIsInstance(self.ref.setips, logic.UserSpecifiedSetips)

        product_specific_data = self.ref.req["product_specific_data"]

        # The part STEP content is at the request level (compressed)
        step_content_compressed = self.ref.req.get("stepFile", "")

        # Verify the STEP file is not empty
        self.assertGreater(len(step_content_compressed), 0,
            f"Part STEP content for '{self.SETUP_NAME}' is empty.")

        # Decompress for content checks
        step_content = decompress_step_content(step_content_compressed)

        # Verify the STEP file contains solid body geometry
        self.assertTrue(
            "MANIFOLD_SOLID_BREP" in step_content or "BREP_WITH_VOIDS" in step_content,
            "Part STEP file missing solid body (MANIFOLD_SOLID_BREP or BREP_WITH_VOIDS)")

        self.assertIn("CLOSED_SHELL", step_content,
            "Part STEP file missing CLOSED_SHELL - no solid geometry exported")

        # Parse coordinates to verify they're in local space
        bounds = self._get_cartesian_point_bounds(step_content)

        self.assertIsNotNone(bounds["max_x"],
            "STEP file should contain CARTESIAN_POINT entries")

        # The geometry should be in local coordinates (near origin)
        # If the Tier 2 fix regresses, coordinates will be offset by the assembly transform
        # We use a generous bound since we don't know the exact part dimensions
        max_local_bound = 50.0  # cm - adjust based on expected part size

        # Note: This test may need adjustment based on the actual part geometry
        # The key is that coordinates should NOT be offset by a large assembly transform
        self.assertLess(abs(bounds["max_x"]), max_local_bound,
            f"X coordinates appear to be in world space, not local space. "
            f"max_x={bounds['max_x']}. Tier 2 inverse transform fix may have regressed.")

    def _get_cartesian_point_bounds(self, step_content: str) -> dict:
        """Parse STEP file content to extract coordinate bounds from CARTESIAN_POINT entries."""
        # CARTESIAN_POINT format: #123=CARTESIAN_POINT('',(1.0,2.0,3.0));
        pattern = r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([^)]+)\s*\)\s*\)"
        matches = re.findall(pattern, step_content)

        if not matches:
            return {
                "min_x": None, "max_x": None,
                "min_y": None, "max_y": None,
                "min_z": None, "max_z": None,
                "point_count": 0,
            }

        xs, ys, zs = [], [], []
        for match in matches:
            coords = [float(c.strip()) for c in match.split(",")]
            if len(coords) >= 3:
                xs.append(coords[0])
                ys.append(coords[1])
                zs.append(coords[2])

        return {
            "min_x": min(xs) if xs else None,
            "max_x": max(xs) if xs else None,
            "min_y": min(ys) if ys else None,
            "max_y": max(ys) if ys else None,
            "min_z": min(zs) if zs else None,
            "max_z": max(zs) if zs else None,
            "point_count": len(xs),
        }


class TestAutoSetipsWithStock(unittest.TestCase):
    """Test send_to_toolpath with AutoSetips + user-selected stock body."""

    TEST_FILE_NAME = "ridgeback-clip-machining-self-contained"

    @classmethod
    def setUpClass(cls):
        test_class = SupportClassTesting(file_name=cls.TEST_FILE_NAME)
        cls.ref = test_class
        cls.stock_body = cls._find_stock_body()

    @classmethod
    def _find_stock_body(cls):
        """Find a body to use as stock in the test document."""
        fusion = Fusion()
        design = fusion.getDesign()
        root = design.rootComponent
        # Find a body that is different from the main part body
        for occ in root.allOccurrences:
            for body in occ.bRepBodies:
                if body.entityToken != cls.ref.body.entityToken:
                    return body
        # Fallback: look in root component bodies
        for body in root.bRepBodies:
            if body.entityToken != cls.ref.body.entityToken:
                return body
        return None

    @classmethod
    def tearDownClass(cls):
        cls.ref.close_test_file(cls.ref.doc)
        del cls.ref

    def test_stock_body_found(self):
        """Verify we found a stock body in the test document."""
        self.assertIsNotNone(self.stock_body, "No stock body found in test document")

    def test_auto_setips_with_stock(self):
        """AutoSetips with stock_body should produce setips with stock data."""
        input_gen = self.ref.get_input_generator(
            use_auto_setups=True,
            stock_body=self.stock_body
        )
        self.ref.generate_test_data(input_gen)
        self.assertIsInstance(self.ref.setips, logic.AutoSetips)
        self.assertIsNotNone(self.ref.setips.stock_body)

    def test_jsonify_includes_stock_solid(self):
        """jsonify() should include stock_solid when stock_body is set."""
        input_gen = self.ref.get_input_generator(
            use_auto_setups=True,
            stock_body=self.stock_body
        )
        self.ref.generate_test_data(input_gen)

        product_data = self.ref.req["product_specific_data"]
        setips_data = product_data["setips"]

        self.assertIn("stock_solid", setips_data)
        stock_solid = setips_data["stock_solid"]
        self.assertEqual(stock_solid["subtypekey"], "FusionStockSolid")
        self.assertGreater(len(stock_solid["step_file_content"]), 0)
        self.assertIn("name", stock_solid)
        self.assertIn("stepCoordinateSystem_cm", stock_solid)

    def test_jsonify_includes_stock_entity_token(self):
        """jsonify() should include stock_entityToken."""
        input_gen = self.ref.get_input_generator(
            use_auto_setups=True,
            stock_body=self.stock_body
        )
        self.ref.generate_test_data(input_gen)

        product_data = self.ref.req["product_specific_data"]
        setips_data = product_data["setips"]

        self.assertIn("stock_entityToken", setips_data)
        self.assertEqual(setips_data["stock_entityToken"], self.stock_body.entityToken)

    def test_stock_step_content_has_geometry(self):
        """Stock STEP content should contain solid body geometry."""
        input_gen = self.ref.get_input_generator(
            use_auto_setups=True,
            stock_body=self.stock_body
        )
        self.ref.generate_test_data(input_gen)

        product_data = self.ref.req["product_specific_data"]
        step_content = product_data["setips"]["stock_solid"]["step_file_content"]

        self.assertTrue(
            "MANIFOLD_SOLID_BREP" in step_content or "BREP_WITH_VOIDS" in step_content,
            "Stock STEP file missing solid body geometry"
        )
        self.assertIn("CLOSED_SHELL", step_content)

    def test_stock_name_matches_step(self):
        """stock_solid name should match the STEP file body name."""
        input_gen = self.ref.get_input_generator(
            use_auto_setups=True,
            stock_body=self.stock_body
        )
        self.ref.generate_test_data(input_gen)

        product_data = self.ref.req["product_specific_data"]
        stock_solid = product_data["setips"]["stock_solid"]
        step_content = stock_solid["step_file_content"]

        brep_names = extract_step_body_names(step_content)
        self.assertGreater(len(brep_names), 0)
        self.assertEqual(stock_solid["name"], brep_names[0])

