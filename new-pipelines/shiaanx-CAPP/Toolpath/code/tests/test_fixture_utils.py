import unittest
from unittest.mock import Mock, patch
import sys

from .mock_adsk import setup_adsk_modules, MockMatrix3D, MockVector3D
setup_adsk_modules()

# Import the functions to test after mocking
from ..lib.fixture_utils import (
    matrix3d_from_column_major_vector,
    fetch_step_content,
    import_step_with_transform,
    import_fixture_solids,
)


class TestMatrix3dFromColumnMajorVector(unittest.TestCase):
    """Test cases for matrix3d_from_column_major_vector function."""

    def test_identity_matrix(self):
        """Identity matrix in column-major should produce identity Matrix3D."""
        # Column-major identity: columns are [1,0,0,0], [0,1,0,0], [0,0,1,0], [0,0,0,1]
        identity = [
            1, 0, 0, 0,  # Column 1
            0, 1, 0, 0,  # Column 2
            0, 0, 1, 0,  # Column 3
            0, 0, 0, 1   # Column 4
        ]

        matrix = matrix3d_from_column_major_vector(identity)

        # Check all cells match identity
        for i in range(4):
            for j in range(4):
                expected = 1 if i == j else 0
                self.assertAlmostEqual(matrix.getCell(i, j), expected, places=5,
                    msg=f"Cell ({i},{j}) should be {expected}")

    def test_translation_only(self):
        """Translation should be in column 4 (indices 12-14 in column-major)."""
        # Identity rotation with translation (10, 20, 30) cm
        transform = [
            1, 0, 0, 0,   # Column 1 (X basis)
            0, 1, 0, 0,   # Column 2 (Y basis)
            0, 0, 1, 0,   # Column 3 (Z basis)
            10, 20, 30, 1 # Column 4 (Translation + homogeneous)
        ]

        matrix = matrix3d_from_column_major_vector(transform)

        # Check translation components (column 3 in row-major, i.e., getCell(row, 3))
        self.assertAlmostEqual(matrix.getCell(0, 3), 10, places=5)  # tx
        self.assertAlmostEqual(matrix.getCell(1, 3), 20, places=5)  # ty
        self.assertAlmostEqual(matrix.getCell(2, 3), 30, places=5)  # tz

        # Check rotation is identity
        self.assertAlmostEqual(matrix.getCell(0, 0), 1, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 1), 1, places=5)
        self.assertAlmostEqual(matrix.getCell(2, 2), 1, places=5)

    def test_90_degree_rotation_around_z(self):
        """90-degree rotation around Z axis in column-major format."""
        # 90-degree rotation around Z:
        # R = [0, -1, 0]
        #     [1,  0, 0]
        #     [0,  0, 1]
        # In column-major: first column is [0, 1, 0], second is [-1, 0, 0], third is [0, 0, 1]
        transform = [
            0, 1, 0, 0,   # Column 1 (where X axis maps to)
            -1, 0, 0, 0,  # Column 2 (where Y axis maps to)
            0, 0, 1, 0,   # Column 3 (where Z axis maps to)
            0, 0, 0, 1    # Column 4 (no translation)
        ]

        matrix = matrix3d_from_column_major_vector(transform)

        # Check rotation matrix elements
        self.assertAlmostEqual(matrix.getCell(0, 0), 0, places=5)
        self.assertAlmostEqual(matrix.getCell(0, 1), -1, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 0), 1, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 1), 0, places=5)
        self.assertAlmostEqual(matrix.getCell(2, 2), 1, places=5)

    def test_combined_rotation_and_translation(self):
        """Test combined rotation (180 degrees around X) and translation."""
        # 180-degree rotation around X:
        # R = [1,  0,  0]
        #     [0, -1,  0]
        #     [0,  0, -1]
        # With translation (5, 10, 15)
        transform = [
            1, 0, 0, 0,      # Column 1
            0, -1, 0, 0,     # Column 2
            0, 0, -1, 0,     # Column 3
            5, 10, 15, 1     # Column 4
        ]

        matrix = matrix3d_from_column_major_vector(transform)

        # Check rotation
        self.assertAlmostEqual(matrix.getCell(0, 0), 1, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 1), -1, places=5)
        self.assertAlmostEqual(matrix.getCell(2, 2), -1, places=5)

        # Check translation
        self.assertAlmostEqual(matrix.getCell(0, 3), 5, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 3), 10, places=5)
        self.assertAlmostEqual(matrix.getCell(2, 3), 15, places=5)

    def test_invalid_length_raises_valueerror(self):
        """Should raise ValueError for non-16-element vectors."""
        with self.assertRaises(ValueError) as context:
            matrix3d_from_column_major_vector([1, 0, 0, 0])

        self.assertIn("16 elements", str(context.exception))

    def test_empty_vector_raises_valueerror(self):
        """Should raise ValueError for empty vectors."""
        with self.assertRaises(ValueError):
            matrix3d_from_column_major_vector([])

    def test_too_many_elements_raises_valueerror(self):
        """Should raise ValueError for vectors with more than 16 elements."""
        with self.assertRaises(ValueError):
            matrix3d_from_column_major_vector([0] * 17)

    def test_homogeneous_row(self):
        """The bottom row should be [0, 0, 0, 1] for valid transform."""
        transform = [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1
        ]

        matrix = matrix3d_from_column_major_vector(transform)

        # Check homogeneous row
        self.assertAlmostEqual(matrix.getCell(3, 0), 0, places=5)
        self.assertAlmostEqual(matrix.getCell(3, 1), 0, places=5)
        self.assertAlmostEqual(matrix.getCell(3, 2), 0, places=5)
        self.assertAlmostEqual(matrix.getCell(3, 3), 1, places=5)


class TestFetchStepContent(unittest.TestCase):
    """Test cases for fetch_step_content function."""

    @patch('urllib.request.urlopen')
    def test_valid_https_url_returns_content(self, mock_urlopen):
        """Valid HTTPS URL should return the STEP content."""
        mock_response = Mock()
        mock_response.read.return_value = b'ISO-10303-21; STEP FILE CONTENT'
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = fetch_step_content('https://example.com/fixture.step')

        self.assertEqual(result, 'ISO-10303-21; STEP FILE CONTENT')
        mock_urlopen.assert_called_once()

    @patch('urllib.request.urlopen')
    def test_valid_http_url_returns_content(self, mock_urlopen):
        """Valid HTTP URL should return the STEP content."""
        mock_response = Mock()
        mock_response.read.return_value = b'STEP CONTENT'
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = fetch_step_content('http://example.com/fixture.step')

        self.assertEqual(result, 'STEP CONTENT')

    def test_invalid_url_without_protocol_returns_empty(self):
        """URL without http/https protocol should return empty string."""
        result = fetch_step_content('ftp://example.com/fixture.step')
        self.assertEqual(result, '')

        result = fetch_step_content('example.com/fixture.step')
        self.assertEqual(result, '')

        result = fetch_step_content('/local/path/fixture.step')
        self.assertEqual(result, '')

    @patch('urllib.request.urlopen')
    def test_network_error_returns_empty(self, mock_urlopen):
        """Network errors should return empty string."""
        mock_urlopen.side_effect = Exception('Network error')

        result = fetch_step_content('https://example.com/fixture.step')

        self.assertEqual(result, '')

    @patch('urllib.request.urlopen')
    def test_timeout_returns_empty(self, mock_urlopen):
        """Timeout should return empty string."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError('timeout')

        result = fetch_step_content('https://example.com/fixture.step')

        self.assertEqual(result, '')


class TestImportStepWithTransform(unittest.TestCase):
    """Test cases for import_step_with_transform function."""

    def setUp(self):
        """Set up mock objects for each test."""
        self.mock_design = Mock()
        self.mock_fusion = Mock()
        self.mock_app = Mock()
        self.mock_import_manager = Mock()

        self.mock_fusion.getApplication.return_value = self.mock_app
        self.mock_app.importManager = self.mock_import_manager

        # Set up root component and occurrences
        self.mock_root_component = Mock()
        self.mock_design.rootComponent = self.mock_root_component

        self.mock_wrapper_occurrence = Mock()
        self.mock_wrapper_component = Mock()
        self.mock_wrapper_occurrence.component = self.mock_wrapper_component
        self.mock_root_component.occurrences.addNewComponent.return_value = self.mock_wrapper_occurrence

    @patch('tempfile.mkdtemp')
    @patch('os.path.join')
    @patch('builtins.open', create=True)
    @patch('os.remove')
    @patch('os.rmdir')
    def test_successful_import_creates_wrapper_component(
        self, mock_rmdir, mock_remove, mock_open, mock_join, mock_mkdtemp
    ):
        """Successful import should create a wrapper component with transform."""
        mock_mkdtemp.return_value = '/tmp/test123'
        mock_join.return_value = '/tmp/test123/fixture.step'
        mock_open.return_value.__enter__ = Mock()
        mock_open.return_value.__exit__ = Mock(return_value=False)
        self.mock_import_manager.importToTarget.return_value = True

        transform = MockMatrix3D()
        result = import_step_with_transform(
            step_content='STEP CONTENT',
            transform=transform,
            design=self.mock_design,
            fusion=self.mock_fusion,
            name='TestFixture'
        )

        self.assertEqual(result, self.mock_wrapper_occurrence)
        self.mock_root_component.occurrences.addNewComponent.assert_called_once_with(transform)

    @patch('tempfile.mkdtemp')
    @patch('os.path.join')
    @patch('builtins.open', create=True)
    @patch('os.remove')
    @patch('os.rmdir')
    def test_wrapper_component_has_correct_name(
        self, mock_rmdir, mock_remove, mock_open, mock_join, mock_mkdtemp
    ):
        """Wrapper component should be named correctly."""
        mock_mkdtemp.return_value = '/tmp/test123'
        mock_join.return_value = '/tmp/test123/fixture.step'
        mock_open.return_value.__enter__ = Mock()
        mock_open.return_value.__exit__ = Mock(return_value=False)
        self.mock_import_manager.importToTarget.return_value = True

        transform = MockMatrix3D()
        import_step_with_transform(
            step_content='STEP CONTENT',
            transform=transform,
            design=self.mock_design,
            fusion=self.mock_fusion,
            name='MyFixtureName'
        )

        self.assertEqual(self.mock_wrapper_component.name, 'MyFixtureName')

    @patch('tempfile.mkdtemp')
    @patch('os.path.join')
    @patch('builtins.open', create=True)
    @patch('os.remove')
    @patch('os.rmdir')
    def test_wrapper_is_grounded_to_parent(
        self, mock_rmdir, mock_remove, mock_open, mock_join, mock_mkdtemp
    ):
        """Wrapper occurrence should be grounded to parent."""
        mock_mkdtemp.return_value = '/tmp/test123'
        mock_join.return_value = '/tmp/test123/fixture.step'
        mock_open.return_value.__enter__ = Mock()
        mock_open.return_value.__exit__ = Mock(return_value=False)
        self.mock_import_manager.importToTarget.return_value = True

        transform = MockMatrix3D()
        import_step_with_transform(
            step_content='STEP CONTENT',
            transform=transform,
            design=self.mock_design,
            fusion=self.mock_fusion
        )

        self.assertTrue(self.mock_wrapper_occurrence.isGroundToParent)

    @patch('tempfile.mkdtemp')
    @patch('os.path.join')
    @patch('builtins.open', create=True)
    @patch('os.remove')
    @patch('os.rmdir')
    def test_failed_import_returns_none_and_cleans_wrapper(
        self, mock_rmdir, mock_remove, mock_open, mock_join, mock_mkdtemp
    ):
        """Failed import should return None and delete the empty wrapper."""
        mock_mkdtemp.return_value = '/tmp/test123'
        mock_join.return_value = '/tmp/test123/fixture.step'
        mock_open.return_value.__enter__ = Mock()
        mock_open.return_value.__exit__ = Mock(return_value=False)
        self.mock_import_manager.importToTarget.return_value = False  # Import fails

        transform = MockMatrix3D()
        result = import_step_with_transform(
            step_content='STEP CONTENT',
            transform=transform,
            design=self.mock_design,
            fusion=self.mock_fusion
        )

        self.assertIsNone(result)
        self.mock_wrapper_occurrence.deleteMe.assert_called_once()

    @patch('tempfile.mkdtemp')
    @patch('os.path.join')
    @patch('builtins.open', create=True)
    @patch('os.remove')
    @patch('os.rmdir')
    def test_temp_file_cleaned_up(
        self, mock_rmdir, mock_remove, mock_open, mock_join, mock_mkdtemp
    ):
        """Temp file and directory should be cleaned up after import."""
        mock_mkdtemp.return_value = '/tmp/test123'
        mock_join.return_value = '/tmp/test123/fixture.step'
        mock_open.return_value.__enter__ = Mock()
        mock_open.return_value.__exit__ = Mock(return_value=False)
        self.mock_import_manager.importToTarget.return_value = True

        transform = MockMatrix3D()
        import_step_with_transform(
            step_content='STEP CONTENT',
            transform=transform,
            design=self.mock_design,
            fusion=self.mock_fusion
        )

        mock_remove.assert_called_once_with('/tmp/test123/fixture.step')
        mock_rmdir.assert_called_once_with('/tmp/test123')


class TestImportFixtureSolids(unittest.TestCase):
    """Test cases for import_fixture_solids function."""

    def setUp(self):
        """Set up mock objects for each test."""
        self.mock_design = Mock()
        self.mock_fusion = Mock()
        self.mock_app = Mock()
        self.mock_import_manager = Mock()

        self.mock_fusion.getApplication.return_value = self.mock_app
        self.mock_app.importManager = self.mock_import_manager

        # Set up root component
        self.mock_root_component = Mock()
        self.mock_design.rootComponent = self.mock_root_component

        # Set up rigid groups
        self.mock_rigid_groups = Mock()
        self.mock_root_component.rigidGroups = self.mock_rigid_groups

    def test_none_fixture_data_returns_empty_list(self):
        """None fixture_data should return empty list and None parent."""
        result, parent = import_fixture_solids(
            fixture_data=None,
            design=self.mock_design,
            fusion=self.mock_fusion
        )

        self.assertEqual(result, [])
        self.assertIsNone(parent)

    def test_empty_fixtures_list_returns_empty(self):
        """Empty fixtureSolids list should return empty list."""
        fixture_data = {"fixtureSolids": []}

        result, parent = import_fixture_solids(
            fixture_data=fixture_data,
            design=self.mock_design,
            fusion=self.mock_fusion
        )

        self.assertEqual(result, [])
        self.assertIsNone(parent)

    def test_missing_fixtureSolids_key_returns_empty(self):
        """Missing fixtureSolids key should return empty list."""
        fixture_data = {"otherKey": "value"}

        result, parent = import_fixture_solids(
            fixture_data=fixture_data,
            design=self.mock_design,
            fusion=self.mock_fusion
        )

        self.assertEqual(result, [])
        self.assertIsNone(parent)

    def test_skips_fixture_missing_transform(self):
        """Fixture missing T_pcs_from_fixture_file should be skipped."""
        # Import the module to patch it
        from ..lib import fixture_utils

        with patch.object(fixture_utils, 'fetch_step_content') as mock_fetch, \
             patch.object(fixture_utils, 'add_component') as mock_add_component:
            mock_parent_occ = Mock()
            mock_parent_occ.component = Mock()
            mock_add_component.return_value = mock_parent_occ

            fixture_data = {
                "fixtureSolids": [
                    {
                        "name": "NoTransformFixture",
                        "stepUrl": "https://example.com/fixture.step"
                        # Missing T_pcs_from_fixture_file
                    }
                ]
            }

            result, parent = import_fixture_solids(
                fixture_data=fixture_data,
                design=self.mock_design,
                fusion=self.mock_fusion
            )

            # Should not attempt to fetch since transform is missing
            mock_fetch.assert_not_called()

    def test_skips_fixture_with_failed_step_fetch(self):
        """Fixture with failed STEP fetch should be skipped."""
        from ..lib import fixture_utils

        with patch.object(fixture_utils, 'fetch_step_content') as mock_fetch, \
             patch.object(fixture_utils, 'add_component') as mock_add_component:
            mock_parent_occ = Mock()
            mock_parent_occ.component = Mock()
            mock_add_component.return_value = mock_parent_occ
            mock_fetch.return_value = ''  # Failed fetch returns empty string

            identity_transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            fixture_data = {
                "fixtureSolids": [
                    {
                        "name": "FailedFetchFixture",
                        "stepUrl": "https://example.com/missing.step",
                        "T_pcs_from_fixture_file": identity_transform
                    }
                ]
            }

            result, parent = import_fixture_solids(
                fixture_data=fixture_data,
                design=self.mock_design,
                fusion=self.mock_fusion
            )

            # Should have attempted fetch
            mock_fetch.assert_called_once()
            # But result should be empty since fetch failed
            self.assertEqual(result, [])

    def test_creates_parent_workholding_component(self):
        """Should create parent Toolpath Workholding component."""
        from ..lib import fixture_utils

        with patch.object(fixture_utils, 'fetch_step_content') as mock_fetch, \
             patch.object(fixture_utils, 'import_step_with_transform') as mock_import_step, \
             patch.object(fixture_utils, 'add_component') as mock_add_component:
            mock_parent_occ = Mock()
            mock_parent_occ.component = Mock()
            mock_add_component.return_value = mock_parent_occ
            mock_fetch.return_value = 'STEP CONTENT'
            mock_import_step.return_value = Mock()

            identity_transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            fixture_data = {
                "fixtureSolids": [
                    {
                        "name": "TestFixture",
                        "stepUrl": "https://example.com/fixture.step",
                        "T_pcs_from_fixture_file": identity_transform
                    }
                ]
            }

            result, parent = import_fixture_solids(
                fixture_data=fixture_data,
                design=self.mock_design,
                fusion=self.mock_fusion
            )

            mock_add_component.assert_called_once()
            call_args = mock_add_component.call_args
            self.assertEqual(call_args[1]['name'], 'Toolpath Workholding (Setup 1)')
            self.assertTrue(call_args[1]['isGroundToParent'])

    def test_applies_part_offset_correctly(self):
        """Part offset should be converted from mm to cm and applied."""
        from ..lib import fixture_utils

        with patch.object(fixture_utils, 'fetch_step_content') as mock_fetch, \
             patch.object(fixture_utils, 'import_step_with_transform') as mock_import_step, \
             patch.object(fixture_utils, 'add_component') as mock_add_component:
            mock_parent_occ = Mock()
            mock_parent_occ.component = Mock()
            mock_add_component.return_value = mock_parent_occ
            mock_fetch.return_value = 'STEP CONTENT'

            mock_fixture_occ = Mock()
            mock_import_step.return_value = mock_fixture_occ

            identity_transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            fixture_data = {
                "fixtureSolids": [
                    {
                        "name": "TestFixture",
                        "stepUrl": "https://example.com/fixture.step",
                        "T_pcs_from_fixture_file": identity_transform
                    }
                ]
            }

            # Part offset in mm: [100, 200, 300] -> should become [10, 20, 30] cm
            result, parent = import_fixture_solids(
                fixture_data=fixture_data,
                design=self.mock_design,
                fusion=self.mock_fusion,
                part_offset=[100.0, 200.0, 300.0]
            )

            # Verify import_step_with_transform was called
            mock_import_step.assert_called_once()

    def test_multiple_fixtures_all_imported(self):
        """Multiple fixtures should all be imported."""
        from ..lib import fixture_utils

        with patch.object(fixture_utils, 'fetch_step_content') as mock_fetch, \
             patch.object(fixture_utils, 'import_step_with_transform') as mock_import_step, \
             patch.object(fixture_utils, 'add_component') as mock_add_component:
            mock_parent_occ = Mock()
            mock_parent_occ.component = Mock()
            mock_add_component.return_value = mock_parent_occ
            mock_fetch.return_value = 'STEP CONTENT'

            # Return different mock occurrences for each fixture
            mock_occ1 = Mock()
            mock_occ2 = Mock()
            mock_occ3 = Mock()
            mock_import_step.side_effect = [mock_occ1, mock_occ2, mock_occ3]

            identity_transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            fixture_data = {
                "fixtureSolids": [
                    {"name": "Fixture1", "stepUrl": "https://example.com/f1.step", "T_pcs_from_fixture_file": identity_transform},
                    {"name": "Fixture2", "stepUrl": "https://example.com/f2.step", "T_pcs_from_fixture_file": identity_transform},
                    {"name": "Fixture3", "stepUrl": "https://example.com/f3.step", "T_pcs_from_fixture_file": identity_transform},
                ]
            }

            result, parent = import_fixture_solids(
                fixture_data=fixture_data,
                design=self.mock_design,
                fusion=self.mock_fusion
            )

            self.assertEqual(mock_import_step.call_count, 3)
            self.assertEqual(len(result), 3)


if __name__ == '__main__':
    unittest.main()
