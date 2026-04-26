import unittest
from unittest.mock import Mock, patch, MagicMock
import sys

from .mock_adsk import setup_adsk_modules, MockMatrix3D
setup_adsk_modules()

# Import the functions to test after mocking
from ..lib.geometry import get_xyz,jsonify_Matrix3D

class TestGetXYZ(unittest.TestCase):
    """Test cases for the get_xyz function."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.mock_obj = Mock()

    def test_get_xyz_returns_tuple(self):
        """Test that get_xyz returns a tuple of (x, y, z)."""
        # Arrange
        self.mock_obj.x = 1.0
        self.mock_obj.y = 2.0
        self.mock_obj.z = 3.0

        # Act
        result = get_xyz(self.mock_obj)

        # Assert
        self.assertEqual(result, (1.0, 2.0, 3.0))
        self.assertIsInstance(result, tuple)

    def test_get_xyz_with_negative_values(self):
        """Test get_xyz with negative coordinate values."""
        # Arrange
        self.mock_obj.x = -5.5
        self.mock_obj.y = -10.2
        self.mock_obj.z = -0.1

        # Act
        result = get_xyz(self.mock_obj)

        # Assert
        self.assertEqual(result, (-5.5, -10.2, -0.1))

    def test_get_xyz_with_zero_values(self):
        """Test get_xyz with zero coordinate values."""
        # Arrange
        self.mock_obj.x = 0.0
        self.mock_obj.y = 0.0
        self.mock_obj.z = 0.0

        # Act
        result = get_xyz(self.mock_obj)

        # Assert
        self.assertEqual(result, (0.0, 0.0, 0.0))

    def test_get_xyz_with_large_values(self):
        """Test get_xyz with large coordinate values."""
        # Arrange
        self.mock_obj.x = 1000000.0
        self.mock_obj.y = 2000000.0
        self.mock_obj.z = 3000000.0

        # Act
        result = get_xyz(self.mock_obj)

        # Assert
        self.assertEqual(result, (1000000.0, 2000000.0, 3000000.0))


class TestJsonifyMatrix3D(unittest.TestCase):
    """Test cases for the jsonify_Matrix3D function."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock coordinate system components
        self.mock_origin = Mock()
        self.mock_origin.x, self.mock_origin.y, self.mock_origin.z = 0.0, 0.0, 0.0

        self.mock_xaxis = Mock()
        self.mock_xaxis.x, self.mock_xaxis.y, self.mock_xaxis.z = 1.0, 0.0, 0.0

        self.mock_yaxis = Mock()
        self.mock_yaxis.x, self.mock_yaxis.y, self.mock_yaxis.z = 0.0, 1.0, 0.0

        self.mock_zaxis = Mock()
        self.mock_zaxis.x, self.mock_zaxis.y, self.mock_zaxis.z = 0.0, 0.0, 1.0

        # Create mock Matrix3D instance
        self.mock_coord_sys = MockMatrix3D()
        self.mock_coord_sys.setWithCoordinateSystem(
            self.mock_origin, self.mock_xaxis, self.mock_yaxis, self.mock_zaxis
        )
    @patch('adsk.core.Matrix3D', MockMatrix3D)
    def test_jsonify_matrix3d_identity_matrix(self):
        """Test jsonify_Matrix3D with identity matrix."""
        # Act
        result = jsonify_Matrix3D(self.mock_coord_sys)

        # Assert
        expected = {
            "origin": (0.0, 0.0, 0.0),
            "xaxis": (1.0, 0.0, 0.0),
            "yaxis": (0.0, 1.0, 0.0),
            "zaxis": (0.0, 0.0, 1.0),
        }
        self.assertEqual(result, expected)

    @patch('adsk.core.Matrix3D', MockMatrix3D)
    def test_jsonify_matrix3d_translated_matrix(self):
        """Test jsonify_Matrix3D with a translated coordinate system."""
        # Arrange
        self.mock_origin.x, self.mock_origin.y, self.mock_origin.z = 10.0, 20.0, 30.0
        # Act
        result = jsonify_Matrix3D(self.mock_coord_sys)

        # Assert
        expected = {
            "origin": (10.0, 20.0, 30.0),
            "xaxis": (1.0, 0.0, 0.0),
            "yaxis": (0.0, 1.0, 0.0),
            "zaxis": (0.0, 0.0, 1.0),
        }
        self.assertEqual(result, expected)

    @patch('adsk.core.Matrix3D', MockMatrix3D)
    def test_jsonify_matrix3d_rotated_matrix(self):
        """Test jsonify_Matrix3D with a rotated coordinate system."""
        # Arrange - 90 degree rotation around Z axis
        self.mock_xaxis.x, self.mock_xaxis.y, self.mock_xaxis.z = 0.0, 1.0, 0.0
        self.mock_yaxis.x, self.mock_yaxis.y, self.mock_yaxis.z = -1.0, 0.0, 0.0

        # Act
        result = jsonify_Matrix3D(self.mock_coord_sys)

        # Assert
        expected = {
            "origin": (0.0, 0.0, 0.0),
            "xaxis": (0.0, 1.0, 0.0),
            "yaxis": (-1.0, 0.0, 0.0),
            "zaxis": (0.0, 0.0, 1.0),
        }
        self.assertEqual(result, expected)

    @patch('adsk.core.Matrix3D', MockMatrix3D)
    def test_jsonify_matrix3d_assertion_error(self):
        """Test that jsonify_Matrix3D raises AssertionError for wrong type."""
        # Arrange
        invalid_input = "not a Matrix3D"

        # Act & Assert
        with self.assertRaises(AssertionError):
            jsonify_Matrix3D(invalid_input)

    @patch('adsk.core.Matrix3D',MockMatrix3D)
    def test_jsonify_matrix3d_return_type(self):
        """Test that jsonify_Matrix3D returns a dictionary."""
        # Act
        result = jsonify_Matrix3D(self.mock_coord_sys)

        # Assert
        self.assertIsInstance(result, dict)
        self.assertIn("origin", result)
        self.assertIn("xaxis", result)
        self.assertIn("yaxis", result)
        self.assertIn("zaxis", result)

    @patch('adsk.core.Matrix3D',MockMatrix3D)
    def test_jsonify_matrix3d_with_decimal_values(self):
        """Test jsonify_Matrix3D with decimal coordinate values."""
        # Arrange
        self.mock_origin.x, self.mock_origin.y, self.mock_origin.z = 1.234, 5.678, 9.012
        self.mock_xaxis.x, self.mock_xaxis.y, self.mock_xaxis.z = 0.707, 0.707, 0.0
        self.mock_yaxis.x, self.mock_yaxis.y, self.mock_yaxis.z = -0.707, 0.707, 0.0

        # Act
        result = jsonify_Matrix3D(self.mock_coord_sys)

        # Assert
        expected = {
            "origin": (1.234, 5.678, 9.012),
            "xaxis": (0.707, 0.707, 0.0),
            "yaxis": (-0.707, 0.707, 0.0),
            "zaxis": (0.0, 0.0, 1.0),
        }
        self.assertEqual(result, expected)


class TestIntegration(unittest.TestCase):
    """Integration tests for both functions working together."""

    @patch('adsk.core.Matrix3D',MockMatrix3D)
    def test_get_xyz_called_by_jsonify_matrix3d(self):
        """Test that jsonify_Matrix3D correctly uses get_xyz for all components."""
        # Arrange
        mock_origin = Mock()
        mock_origin.x, mock_origin.y, mock_origin.z = 1.0, 2.0, 3.0

        mock_xaxis = Mock()
        mock_xaxis.x, mock_xaxis.y, mock_xaxis.z = 4.0, 5.0, 6.0

        mock_yaxis = Mock()
        mock_yaxis.x, mock_yaxis.y, mock_yaxis.z = 7.0, 8.0, 9.0

        mock_zaxis = Mock()
        mock_zaxis.x, mock_zaxis.y, mock_zaxis.z = 10.0, 11.0, 12.0

        mock_coord_sys = MockMatrix3D()
        mock_coord_sys.setWithCoordinateSystem(
            mock_origin, mock_xaxis, mock_yaxis, mock_zaxis
        )

        # Act
        result = jsonify_Matrix3D(mock_coord_sys)

        # Assert
        expected = {
            "origin": (1.0, 2.0, 3.0),
            "xaxis": (4.0, 5.0, 6.0),
            "yaxis": (7.0, 8.0, 9.0),
            "zaxis": (10.0, 11.0, 12.0),
        }
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()
