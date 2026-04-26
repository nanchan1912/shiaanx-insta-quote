import unittest
from unittest.mock import MagicMock, patch, Mock, mock_open
import re
import sys

from .mock_adsk import setup_adsk_modules
setup_adsk_modules()

# from ..lib import fusion_360_utils
from ..lib.workholding_utils import DEFAULT_WORKHOLDING_FOLDER_NAME
from ..lib.workholding_utils import create_new_folder, get_workholding_folder, file_exists_in_folder, get_workholding_folder_name
from ..lib.fusion_utils import make_id, ensure_hybrid_design_intent
from ..lib.general_utils import (
    extract_step_body_names,
    extract_step_product_names,
    extract_step_shape_rep_names,
    compress_step_content,
    decompress_step_content,
)

# class TestGeneralUtils(unittest.TestCase):

#     def test_new_doc(self):
#         doc_name = "test_doc"
#         doc,design = fusion_360_utils.create_new_design_doc(doc_name=doc_name)

#         self.assertIsInstance(doc, adsk.fusion.FusionDocument)
#         self.assertEqual(doc.name,doc_name)
#         self.assertIsInstance(design, adsk.fusion.Design)


# class TestWorkholdingUtils(unittest.TestCase):

#     def test_file_IO(self):
#         active_project = fusion_360_utils.get_active_project()

#         workholding_folder = fusion_360_utils.get_workholding_folder(active_project)

#         test_file = "bad file" + ".f3d"
#         file_exists = fusion_360_utils.file_exists_in_folder(test_file,workholding_folder)
#         self.assertIs(file_exists,False)

#         test_file = "Narrow-6in-Fixed-96mmStud" + ".f3d"
#         file_exists = fusion_360_utils.file_exists_in_folder(test_file,workholding_folder)
#         self.assertIs(file_exists,True)




class TestFusionUtils(unittest.TestCase):

    def setUp(self):
        # Common mock folder setup
        self.mock_root_folder = MagicMock()
        self.mock_active_project = MagicMock()
        self.mock_active_project.rootFolder = self.mock_root_folder

        #patcher = patch("lib.fusion_360_utils.app")
        #self.mock_app = patcher.start()
        #self.addCleanup(patcher.stop)

    def test_make_id(self):
        # basic
        self.assertTrue("MyPart" in make_id("MyPart"))
        self.assertNotEqual("MyPart", make_id("MyPart"))
        self.assertNotEqual(make_id("MyPart "), make_id("MyPart"))
        self.assertEqual(make_id("MyPart"), make_id("MyPart"))

        # salt
        id1 = make_id("MyPart",1)
        id1b = make_id("MyPart",1)
        self.assertEqual(id1,id1b)
        id2 = make_id("MyPart",2)
        self.assertNotEqual(id1,id2)

        # no bad characters
        id_exotic = make_id("exotic ) ? chars")
        self.assertTrue(re.match(r'^[A-Za-z0-9_]+$', id_exotic))
        self.assertTrue("exotic" in id_exotic)
        self.assertTrue("chars" in id_exotic)
        self.assertFalse(" " in id_exotic)
        self.assertFalse("?" in id_exotic)
        self.assertFalse(")" in id_exotic)

    def test_create_new_folder_when_not_exists(self):
        mock_data_folders = MagicMock()
        mock_data_folders.__iter__.return_value = []  # No folders yet
        self.mock_root_folder.dataFolders = mock_data_folders

        mock_new_folder = MagicMock()
        mock_data_folders.add.return_value = mock_new_folder

        result = create_new_folder("Fixtures", self.mock_active_project)
        self.assertEqual(result, mock_new_folder)
        mock_data_folders.add.assert_called_once_with("Fixtures")


    def test_create_new_folder_when_exists(self):
        existing_folder = MagicMock()
        existing_folder.name = "Fixtures"

        mock_data_folders = MagicMock()
        mock_data_folders.__iter__.return_value = [existing_folder]
        self.mock_root_folder.dataFolders = mock_data_folders

        result = create_new_folder("Fixtures", self.mock_active_project)
        self.assertEqual(result, existing_folder)
        mock_data_folders.add.assert_not_called()

    def test_get_workholding_folder_when_exists(self):
        folder1 = MagicMock(name="Random")
        folder2 = MagicMock()
        folder2.name = "Toolpath Workholding - Do Not Edit"

        self.mock_root_folder.dataFolders = [folder1, folder2]
        self.mock_active_project.rootFolder = self.mock_root_folder

        result = get_workholding_folder(self.mock_active_project)
        self.assertEqual(result, folder2)

    def test_get_workholding_folder_when_missing(self):
        folder1 = MagicMock()
        folder1.name = "OtherFolder"

        self.mock_root_folder.dataFolders = [folder1]

        result = get_workholding_folder(self.mock_active_project)
        self.assertIsNone(result)

    def test_file_exists_in_folder_when_found(self):
        mock_file = MagicMock()
        mock_file.name = "MyPart"
        mock_folder = MagicMock()
        mock_folder.dataFiles = [mock_file]

        result = file_exists_in_folder("MyPart.f3d", mock_folder)
        self.assertTrue(result)

    def test_file_exists_in_folder_when_not_found(self):
        mock_file = MagicMock()
        mock_file.name = "AnotherPart"
        mock_folder = MagicMock()
        mock_folder.dataFiles = [mock_file]

        result = file_exists_in_folder("MyPart.f3d", mock_folder)
        self.assertFalse(result)

    # def test_get_active_project_success(self):
    #     mock_project = MagicMock()
    #     mock_app = MagicMock()
    #     mock_app.userInterface = MagicMock()
    #     mock_app.data.activeProject = mock_project

    #     with patch("adsk.core.Application.get", return_value=mock_app):
    #         result = fusion_360_utils.get_active_project()
    #         self.assertEqual(result, mock_project)
    #         mock_app.userInterface.messageBox.assert_not_called()

    # def test_get_active_project_none(self):
    #     mock_app = MagicMock()
    #     mock_ui = MagicMock()
    #     mock_data = MagicMock()
    #     mock_data.activeProject = None
    #     mock_app.userInterface = mock_ui
    #     mock_app.data = mock_data

    #     with patch("adsk.core.Application.get", return_value=mock_app):
    #         result = fusion_360_utils.get_active_project()
    #         self.assertIsNone(result)
    #         mock_ui.messageBox.assert_called_once_with("No active project found.")

    def test_get_workholding_folder_name_with_config(self):
        result = get_workholding_folder_name()
        self.assertEqual(result, "Toolpath Workholding - Do Not Edit")

    def test_get_workholding_folder_name_default(self):
        result = get_workholding_folder_name()
        self.assertEqual(result, DEFAULT_WORKHOLDING_FOLDER_NAME)


class TestLoadConfigDeviceId(unittest.TestCase):
    """Tests for device_id generation in load_config"""

    @patch('Toolpath.code.lib.general_utils.save_json')
    @patch('Toolpath.code.lib.general_utils.load_json')
    @patch('Toolpath.code.lib.general_utils.os.path.exists')
    @patch('Toolpath.code.lib.general_utils.addin_root_rpath')
    @patch('Toolpath.code.lib.general_utils.addin_code_rpath')
    def test_generates_device_id_when_missing(self, mock_code_rpath, mock_root_rpath, 
                                               mock_exists, mock_load, mock_save):
        """Test that a device_id is generated when not present in config"""
        from ..lib.general_utils import load_config
        
        mock_code_rpath.return_value = "/fake/config_template.json"
        mock_root_rpath.return_value = "/fake/config.json"
        mock_exists.return_value = True
        mock_load.side_effect = [
            {"server_url": "http://example.com"},  # template
            {"some_setting": "value"},  # user config (no device_id)
        ]
        
        config = load_config()
        
        self.assertIn("device_id", config)
        self.assertEqual(len(config["device_id"]), 32)
        self.assertTrue(all(c in '0123456789abcdef' for c in config["device_id"]))
        mock_save.assert_called()

    @patch('Toolpath.code.lib.general_utils.save_json')
    @patch('Toolpath.code.lib.general_utils.load_json')
    @patch('Toolpath.code.lib.general_utils.os.path.exists')
    @patch('Toolpath.code.lib.general_utils.addin_root_rpath')
    @patch('Toolpath.code.lib.general_utils.addin_code_rpath')
    def test_preserves_existing_device_id(self, mock_code_rpath, mock_root_rpath,
                                           mock_exists, mock_load, mock_save):
        """Test that existing device_id is not overwritten"""
        from ..lib.general_utils import load_config
        
        existing_id = "abcd1234efgh5678ijkl9012mnop3456"
        mock_code_rpath.return_value = "/fake/config_template.json"
        mock_root_rpath.return_value = "/fake/config.json"
        mock_exists.return_value = True
        mock_load.side_effect = [
            {"server_url": "http://example.com"},  # template
            {"device_id": existing_id},  # user config with existing device_id
        ]
        
        config = load_config()
        
        self.assertEqual(config["device_id"], existing_id)
        # save_json should only be called once for the initial config creation, not for device_id
        # Actually it shouldn't be called at all since device_id exists
        save_calls = [call for call in mock_save.call_args_list 
                      if "device_id" in str(call)]
        self.assertEqual(len(save_calls), 0)

    @patch('Toolpath.code.lib.general_utils.save_json')
    @patch('Toolpath.code.lib.general_utils.load_json')
    @patch('Toolpath.code.lib.general_utils.os.path.exists')
    @patch('Toolpath.code.lib.general_utils.addin_root_rpath')
    @patch('Toolpath.code.lib.general_utils.addin_code_rpath')
    def test_generates_device_id_when_empty_string(self, mock_code_rpath, mock_root_rpath,
                                                    mock_exists, mock_load, mock_save):
        """Test that device_id is generated when it's an empty string"""
        from ..lib.general_utils import load_config
        
        mock_code_rpath.return_value = "/fake/config_template.json"
        mock_root_rpath.return_value = "/fake/config.json"
        mock_exists.return_value = True
        mock_load.side_effect = [
            {"server_url": "http://example.com"},  # template
            {"device_id": ""},  # user config with empty device_id
        ]
        
        config = load_config()
        
        self.assertIn("device_id", config)
        self.assertNotEqual(config["device_id"], "")
        self.assertEqual(len(config["device_id"]), 32)
        mock_save.assert_called()


    # #@patch("lib.fusion_360_utils.workholding_utils.urllib.request.urlopen")
    # def test_download_f3d_file_success(self):#, mock_urlopen):
    #     mock_response = MagicMock()
    #     mock_response.read.return_value = b"testdata"
    #     #mock_urlopen.return_value.__enter__.return_value = mock_response

    #     result = workholding_utils.download_f3d_file("test.f3d", category="vise")
    #     self.assertIsInstance(result, io.BytesIO)
    #     self.assertEqual(result.getvalue(), b"testdata")

    # #@patch("lib.fusion_360_utils.workholding_utils.urllib.request.urlopen", side_effect=Exception("Download error"))
    # #@patch("lib.fusion_360_utils.workholding_utils.urllib.request.urlopen", side_effect=urllib.error.URLError("Download error"))
    # def test_download_f3d_file_failure(self):#, mock_urlopen):
    #     result = workholding_utils.download_f3d_file("test.f3d", category="vise")
    #     self.assertIsNone(result)


    # def test_download_f3d_file_failure(self, mock_urlopen):
    #     result = workholding_utils.download_f3d_file("test.f3d", category="vise")
    #     self.assertIsNone(result)

#     @patch("lib.fusion_360_utils.workholding_utils.tempfile.gettempdir", return_value="/tmp")
#     @patch("lib.fusion_360_utils.workholding_utils.open", new_callable=mock_open)
#     @patch("lib.fusion_360_utils.workholding_utils.os.remove")
#     def test_upload_to_fusion_cloud(self, mock_remove, mock_open_func, mock_tempdir):
#         file_data = io.BytesIO(b"mockfiledata")
#         file_name = "test_upload.f3d"
#         mock_folder = MagicMock()
#         mock_folder.uploadFile.return_value = True

#         workholding_utils.upload_to_fusion_cloud(file_data, file_name, mock_folder)

#         temp_path = os.path.join("/tmp", file_name)
#         mock_open_func.assert_called_with(temp_path, "wb")
#         mock_folder.uploadFile.assert_called_with(temp_path)
#         mock_remove.assert_called_with(temp_path)


# ---------------------------------------------------------------------------
# STEP file parsing utilities
# ---------------------------------------------------------------------------

# Canned STEP snippets for testing regex extraction.
_STEP_SNIPPET_MANIFOLD = (
    "#100=MANIFOLD_SOLID_BREP('Body1',#101);\n"
    "#200=MANIFOLD_SOLID_BREP('Body2',#201);\n"
)

_STEP_SNIPPET_BREP_WITH_VOIDS = (
    "#300=BREP_WITH_VOIDS('VoidBody',#301,(#302));\n"
)

_STEP_SNIPPET_PRODUCT = (
    "#400=PRODUCT('PartProduct','PartProduct',$,(#1));\n"
    "#401=PRODUCT('Assembly','Assembly',$,(#1));\n"
)

_STEP_SNIPPET_SHAPE_REP = (
    "#500=ADVANCED_BREP_SHAPE_REPRESENTATION('ShapeRep1',(#1),#2);\n"
)


class TestExtractStepBodyNames(unittest.TestCase):
    """Tests for extract_step_body_names — regex on STEP content."""

    def test_manifold_solid_brep_extracted(self):
        names = extract_step_body_names(_STEP_SNIPPET_MANIFOLD)
        self.assertEqual(names, ['Body1', 'Body2'])

    def test_brep_with_voids_extracted(self):
        names = extract_step_body_names(_STEP_SNIPPET_BREP_WITH_VOIDS)
        self.assertEqual(names, ['VoidBody'])

    def test_mixed_body_types(self):
        content = _STEP_SNIPPET_MANIFOLD + _STEP_SNIPPET_BREP_WITH_VOIDS
        names = extract_step_body_names(content)
        self.assertEqual(names, ['Body1', 'Body2', 'VoidBody'])

    def test_empty_name_filtered_out(self):
        content = "#100=MANIFOLD_SOLID_BREP('',#101);\n"
        names = extract_step_body_names(content)
        self.assertEqual(names, [])

    def test_no_body_entries_returns_empty(self):
        content = "#100=PRODUCT('SomeProduct','SomeProduct',$,(#1));\n"
        names = extract_step_body_names(content)
        self.assertEqual(names, [])

    def test_whitespace_variations(self):
        content = "#100=MANIFOLD_SOLID_BREP  ( 'SpacedName' , #101);\n"
        names = extract_step_body_names(content)
        self.assertEqual(names, ['SpacedName'])


class TestExtractStepProductNames(unittest.TestCase):
    """Tests for extract_step_product_names."""

    def test_product_names_extracted(self):
        names = extract_step_product_names(_STEP_SNIPPET_PRODUCT)
        self.assertEqual(names, ['PartProduct', 'Assembly'])

    def test_no_products_returns_empty(self):
        names = extract_step_product_names(_STEP_SNIPPET_MANIFOLD)
        self.assertEqual(names, [])


class TestExtractStepShapeRepNames(unittest.TestCase):
    """Tests for extract_step_shape_rep_names."""

    def test_shape_rep_names_extracted(self):
        names = extract_step_shape_rep_names(_STEP_SNIPPET_SHAPE_REP)
        self.assertEqual(names, ['ShapeRep1'])

    def test_no_shape_reps_returns_empty(self):
        names = extract_step_shape_rep_names(_STEP_SNIPPET_MANIFOLD)
        self.assertEqual(names, [])


# ---------------------------------------------------------------------------
# STEP content compression / decompression
# ---------------------------------------------------------------------------

class TestStepContentCompression(unittest.TestCase):
    """Tests for compress_step_content / decompress_step_content."""

    _SAMPLE_STEP = (
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('FusionExport'),'2;1');\n"
        "ENDSEC;\nDATA;\n#1=MANIFOLD_SOLID_BREP('TestBody',#2);\nENDSEC;\n"
        "END-ISO-10303-21;\n"
    )

    def test_round_trip(self):
        compressed, info = compress_step_content(self._SAMPLE_STEP)
        result = decompress_step_content(compressed)
        self.assertEqual(result, self._SAMPLE_STEP)

    def test_empty_string_round_trip(self):
        compressed, info = compress_step_content("")
        result = decompress_step_content(compressed)
        self.assertEqual(result, "")

    def test_compression_info_keys(self):
        _, info = compress_step_content(self._SAMPLE_STEP)
        self.assertEqual(info["compression"], "gzip+base64")
        self.assertIn("original_size", info)
        self.assertIn("compressed_size", info)
        self.assertIn("ratio", info)
        self.assertEqual(info["original_size"], len(self._SAMPLE_STEP.encode('utf-8')))
        self.assertGreater(info["ratio"], 0)

    def test_compressed_is_base64_string(self):
        compressed, _ = compress_step_content(self._SAMPLE_STEP)
        self.assertIsInstance(compressed, str)
        # Base64 strings only contain alphanumeric, +, /, and = characters
        import re
        self.assertRegex(compressed, r'^[A-Za-z0-9+/=]+$')


# ---------------------------------------------------------------------------
# ensure_hybrid_design_intent
# ---------------------------------------------------------------------------

class TestEnsureHybridDesignIntent(unittest.TestCase):
    """Tests for ensure_hybrid_design_intent — design mode gating."""

    def _make_design(self, intent):
        design = Mock()
        design.designIntent = intent
        return design

    def test_already_hybrid_returns_true_no_dialog(self):
        """When design is already Hybrid, return True without showing a dialog."""
        import adsk.fusion
        design = self._make_design(adsk.fusion.DesignIntentTypes.HybridDesignIntentType)

        with patch('Toolpath.code.lib.fusion_utils.Fusion') as MockFusion:
            result = ensure_hybrid_design_intent(design)

        self.assertTrue(result)
        MockFusion.assert_not_called()

    def test_part_mode_user_confirms_converts_and_returns_true(self):
        """When design is Part and user clicks OK, convert to Hybrid and return True."""
        import adsk.fusion
        import adsk.core
        design = self._make_design(adsk.fusion.DesignIntentTypes.PartDesignIntentType)

        mock_ui = Mock()
        mock_ui.messageBox.return_value = adsk.core.DialogResults.DialogOK

        with patch('Toolpath.code.lib.fusion_utils.Fusion') as MockFusion:
            MockFusion.return_value.getUI.return_value = mock_ui
            result = ensure_hybrid_design_intent(design)

        self.assertTrue(result)
        self.assertEqual(design.designIntent, adsk.fusion.DesignIntentTypes.HybridDesignIntentType)
        mock_ui.messageBox.assert_called_once()

    def test_part_mode_user_cancels_returns_false(self):
        """When design is Part and user clicks Cancel, return False without converting."""
        import adsk.fusion
        import adsk.core
        design = self._make_design(adsk.fusion.DesignIntentTypes.PartDesignIntentType)
        original_intent = design.designIntent

        mock_ui = Mock()
        mock_ui.messageBox.return_value = adsk.core.DialogResults.DialogCancel

        with patch('Toolpath.code.lib.fusion_utils.Fusion') as MockFusion:
            MockFusion.return_value.getUI.return_value = mock_ui
            result = ensure_hybrid_design_intent(design)

        self.assertFalse(result)
        self.assertEqual(design.designIntent, original_intent)
        mock_ui.messageBox.assert_called_once()
