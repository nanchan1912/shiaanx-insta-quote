# import unittest
# from unittest.mock import patch, MagicMock, Mock
# import sys

# # # Mock the Fusion 360 modules since they won't be available in test environment
# sys.modules['adsk'] = Mock()
# sys.modules['adsk.core'] = Mock()
# sys.modules['adsk.cam'] = Mock()
# sys.modules['adsk.fusion'] = Mock()


# from ..commands.command_import_workholding_files import import_workholding_files
# # from ..lib.fusion_360_utils import general_utils as utils
# class TestImportWorkholdingFiles(unittest.TestCase):

# #     #@patch("lib.fusion_360_utils.general_utils")
# #     @patch('utils',MagicMock())
#    def test_import_workholding_files_success(self):#, mock_futil):
#         mock_fusion = MagicMock()
#         mock_progress = MagicMock()
# #         mock_ui = MagicMock()
# #         mock_target_folder = MagicMock()

# #         mock_futil.DEFAULT_WORKHOLDING_FOLDER_NAME = "Toolpath Workholding - Do Not Edit"
# #         mock_futil.Fusion.return_value.getUI.return_value = mock_ui
# #         mock_futil.get_active_project.return_value = "mock_project"
# #         mock_futil.create_new_folder.return_value = mock_target_folder
# #         mock_futil.file_exists_in_folder.return_value = False
# #         mock_futil.download_f3d_file.return_value = MagicMock()
# #         mock_futil.upload_to_fusion_cloud.return_value = None

#        import_workholding_files(mock_fusion, mock_progress)

#         self.assertEqual(mock_progress.hide.call_count, 1)
#         self.assertTrue(mock_futil.upload_to_fusion_cloud.called)
#         self.assertFalse(mock_ui.messageBox.called)  # because files were updated
    # @patch('lib.fusion_360_utils.general_utils.futil.upload_to_fusion_cloud')
    # @patch('lib.fusion_360_utils.general_utils.futil.download_f3d_file')
    # @patch('lib.fusion_360_utils.general_utils.futil.file_exists_in_folder', return_value=False)
    # @patch('lib.fusion_360_utils.general_utils.futil.create_new_folder')
    # @patch('lib.fusion_360_utils.general_utils.futil.get_active_project')
    # def test_import_workholding_files_success(
    #     self,
    #     mock_get_active_project,
    #     mock_create_new_folder,
    #     mock_file_exists,
    #     mock_download,
    #     mock_upload
    # ):
    #     mock_get_active_project.return_value = "mock_project"
    #     mock_create_new_folder.return_value = "mock_folder"
    #     mock_download.return_value = b"dummy data"
        
    #     mock_progress = MagicMock()
    #     mock_fusion = MagicMock()

    #     import_workholding_files(mock_fusion, mock_progress)

    #     self.assertTrue(mock_upload.called, "Expected upload_to_fusion_cloud to be called.")

    # @patch("lib.fusion_360_utils.general_utils.futil")
    # def test_import_workholding_files_all_files_exist(self, mock_futil):
    #     mock_fusion = MagicMock()
    #     mock_progress = MagicMock()
    #     mock_ui = MagicMock()

    #     mock_futil.DEFAULT_WORKHOLDING_FOLDER_NAME = "Toolpath Workholding - Do Not Edit"
    #     mock_futil.Fusion.return_value.getUI.return_value = mock_ui
    #     mock_futil.get_active_project.return_value = "mock_project"
    #     mock_futil.create_new_folder.return_value = MagicMock()
    #     mock_futil.file_exists_in_folder.return_value = True

    #     import_workholding_files(mock_fusion, mock_progress)

    #     self.assertEqual(mock_ui.messageBox.call_count, 1)
    #     self.assertEqual(mock_progress.hide.call_count, 1)
    #     self.assertFalse(mock_futil.upload_to_fusion_cloud.called)

    # @patch("lib.fusion_360_utils.general_utils.futil")
    # def test_import_workholding_files_get_project_fails(self, mock_futil):
    #     mock_fusion = MagicMock()
    #     mock_progress = MagicMock()
    #     mock_ui = MagicMock()

    #     mock_futil.Fusion.return_value.getUI.return_value = mock_ui
    #     mock_futil.get_active_project.side_effect = Exception("No project")
    #     mock_futil.log = MagicMock()

    #     result = import_workholding_files(mock_fusion, mock_progress)

    #     self.assertIsNone(result)
    #     self.assertTrue(mock_ui.messageBox.called)
    #     self.assertTrue(mock_futil.log.called)

    # @patch("lib.fusion_360_utils.general_utils.futil")
    # def test_import_workholding_files_upload_fails(self, mock_futil):
    #     mock_fusion = MagicMock()
    #     mock_progress = MagicMock()
    #     mock_ui = MagicMock()

    #     mock_futil.DEFAULT_WORKHOLDING_FOLDER_NAME = "Toolpath Workholding - Do Not Edit"
    #     mock_futil.Fusion.return_value.getUI.return_value = mock_ui
    #     mock_futil.get_active_project.return_value = "mock_project"
    #     mock_futil.create_new_folder.return_value = MagicMock()
    #     mock_futil.file_exists_in_folder.return_value = False
    #     mock_futil.download_f3d_file.side_effect = Exception("Download failed")
    #     mock_futil.log = MagicMock()

    #     import_workholding_files(mock_fusion, mock_progress)

    #     self.assertTrue(mock_futil.log.called)
    #     self.assertTrue(mock_ui.messageBox.called)


# if __name__ == '__main__':
#     unittest.main()
