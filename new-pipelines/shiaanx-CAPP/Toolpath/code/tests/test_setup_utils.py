import unittest
from unittest.mock import Mock, patch
import sys
import os

# Create mock Point3D, Vector3D, and Matrix3D classes
class MockPoint3D:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
    
    @classmethod
    def create(cls, x, y, z):
        return cls(x, y, z)
    
    def __eq__(self, other):
        return (abs(self.x - other.x) < 1e-10 and 
                abs(self.y - other.y) < 1e-10 and 
                abs(self.z - other.z) < 1e-10)

class MockMatrix3D:
    def __init__(self):
        self.origin = None
        self.xaxis = None
        self.yaxis = None
        self.zaxis = None
        self._data = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]  # Identity matrix
    
    @classmethod
    def create(cls):
        return cls()
    
    def setWithCoordinateSystem(self, origin, xaxis, yaxis, zaxis):
        self.origin = origin
        self.xaxis = xaxis
        self.yaxis = yaxis
        self.zaxis = zaxis
        return True
    
    def copy(self):
        new_matrix = MockMatrix3D()
        new_matrix.origin = self.origin
        new_matrix.xaxis = self.xaxis
        new_matrix.yaxis = self.yaxis
        new_matrix.zaxis = self.zaxis
        new_matrix._data = [row[:] for row in self._data]
        return new_matrix
    
    def invert(self):
        # For testing purposes, just mark as inverted
        self._inverted = True
        return True
    
    def transformBy(self, other_matrix):
        # For testing purposes, just mark as transformed
        self._transformed_by = other_matrix
        return True

# Mock for adsk.cam.SetupStockModes
class MockSetupStockModes:
    FixedBoxStock = "FixedBoxStock"
    RelativeBoxStock = "RelativeBoxStock"
    FixedCylinderStock = "FixedCylinderStock"
    RelativeCylinderStock = "RelativeCylinderStock"
    FixedTubeStock = "FixedTubeStock"
    RelativeTubeStock = "RelativeTubeStock"
    SolidStock = "SolidStock"
    PreviousSetupStock = "PreviousSetupStock"

# Mock for complex Fusion 360 objects
class MockSketch:
    def __init__(self):
        self.sketchPoints = MockSketchPoints()
        self.isVisible = True

class MockSketchPoints:
    def add(self, point):
        mock_point = Mock()
        mock_point.isLightBulbOn = True
        return mock_point

class MockConstructionAxes:
    def createInput(self):
        return MockAxisInput()
    
    def add(self, input_obj):
        mock_axis = Mock()
        mock_axis.isLightBulbOn = True
        return mock_axis

class MockAxisInput:
    def setByTwoPoints(self, point1, point2):
        self.point1 = point1
        self.point2 = point2

class MockConstructionPoints:
    def createInput(self):
        return MockPointInput()
    
    def add(self, input_obj):
        mock_point = Mock()
        mock_point.isLightBulbOn = True
        return mock_point

class MockPointInput:
    def setByPoint(self, point):
        self.point = point

class MockSketches:
    def add(self, plane):
        return MockSketch()

class MockComponent:
    def __init__(self):
        self.sketches = MockSketches()
        self.constructionAxes = MockConstructionAxes()
        self.constructionPoints = MockConstructionPoints()
        self.xYConstructionPlane = Mock()

class MockVector3D:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
    
    @classmethod
    def create(cls, x, y, z):
        return cls(x, y, z)
    
    def __eq__(self, other):
        return (abs(self.x - other.x) < 1e-10 and 
                abs(self.y - other.y) < 1e-10 and 
                abs(self.z - other.z) < 1e-10)

# Create mock modules
mock_adsk_core = Mock()
mock_adsk_core.Point3D = MockPoint3D
mock_adsk_core.Vector3D = MockVector3D
mock_adsk_core.Matrix3D = MockMatrix3D

mock_adsk_cam = Mock()
mock_adsk_cam.SetupStockModes = MockSetupStockModes

mock_adsk = Mock()
mock_adsk.core = mock_adsk_core
mock_adsk.cam = mock_adsk_cam

# Mock the Fusion 360 modules since they won't be available in test environment
sys.modules['adsk'] = mock_adsk
sys.modules['adsk.core'] = mock_adsk_core
sys.modules['adsk.cam'] = mock_adsk_cam
sys.modules['adsk.fusion'] = Mock()

# # Now import your module (adjust the import path as needed)
# # from your_module import convert_units, convert_point3D, getPoint3DFromJson, Vector3D_from_json, Point3D_from_json
# from ..lib.setup_utils import convert_units,convert_point3D,getPoint3DFromJson,Point3D_from_json,Vector3D_from_json,Matrix3D_from_json,invert,compose,stockMode_from_str,construct_coord_system

# class TestConvertUnits(unittest.TestCase):
#     """Test cases for the convert_units function."""
    
#     def test_same_unit_conversion(self):
#         """Test conversion between the same units."""
#         self.assertEqual(convert_units(10, 'cm', 'cm'), 10)
#         self.assertEqual(convert_units(5.5, 'mm', 'mm'), 5.5)
#         self.assertEqual(convert_units(1, 'in', 'in'), 1)
    
#     def test_cm_to_mm_conversion(self):
#         """Test conversion from centimeters to millimeters."""
#         self.assertAlmostEqual(convert_units(1, 'cm', 'mm'), 10, places=7)
#         self.assertAlmostEqual(convert_units(2.5, 'cm', 'mm'), 25, places=7)
    
#     def test_mm_to_cm_conversion(self):
#         """Test conversion from millimeters to centimeters."""
#         self.assertAlmostEqual(convert_units(10, 'mm', 'cm'), 1, places=7)
#         self.assertAlmostEqual(convert_units(25, 'mm', 'cm'), 2.5, places=7)
    
#     def test_inch_to_cm_conversion(self):
#         """Test conversion from inches to centimeters."""
#         self.assertAlmostEqual(convert_units(1, 'in', 'cm'), 2.54, places=7)
#         self.assertAlmostEqual(convert_units(2, 'in', 'cm'), 5.08, places=7)
    
#     def test_cm_to_inch_conversion(self):
#         """Test conversion from centimeters to inches."""
#         self.assertAlmostEqual(convert_units(2.54, 'cm', 'in'), 1, places=7)
#         self.assertAlmostEqual(convert_units(5.08, 'cm', 'in'), 2, places=7)
    
#     def test_feet_to_meter_conversion(self):
#         """Test conversion from feet to meters."""
#         self.assertAlmostEqual(convert_units(1, 'ft', 'm'), 0.3048, places=7)
#         self.assertAlmostEqual(convert_units(3.28084, 'ft', 'm'), 1, places=5)
    
#     def test_meter_to_feet_conversion(self):
#         """Test conversion from meters to feet."""
#         self.assertAlmostEqual(convert_units(1, 'm', 'ft'), 3.28084, places=5)
#         self.assertAlmostEqual(convert_units(0.3048, 'm', 'ft'), 1, places=7)
    
#     def test_zero_value_conversion(self):
#         """Test conversion of zero values."""
#         self.assertEqual(convert_units(0, 'cm', 'mm'), 0)
#         self.assertEqual(convert_units(0, 'in', 'ft'), 0)
    
#     def test_negative_value_conversion(self):
#         """Test conversion of negative values."""
#         self.assertAlmostEqual(convert_units(-1, 'cm', 'mm'), -10, places=7)
#         self.assertAlmostEqual(convert_units(-2.54, 'cm', 'in'), -1, places=7)
    
#     def test_invalid_unit_raises_keyerror(self):
#         """Test that invalid units raise KeyError."""
#         with self.assertRaises(KeyError):
#             convert_units(1, 'invalid_unit', 'cm')
#         with self.assertRaises(KeyError):
#             convert_units(1, 'cm', 'invalid_unit')


# class TestConvertPoint3D(unittest.TestCase):
#     """Test cases for the convert_point3D function."""
    
#     def setUp(self):
#         """Set up test fixtures."""
#         self.test_point = MockPoint3D(1, 2, 3)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_same_unit_point_conversion(self):
#         """Test point conversion with same units."""
#         result = convert_point3D(self.test_point, 'cm', 'cm')
#         self.assertEqual(result.x, 1)
#         self.assertEqual(result.y, 2)
#         self.assertEqual(result.z, 3)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_cm_to_mm_point_conversion(self):
#         """Test point conversion from cm to mm."""
#         result = convert_point3D(self.test_point, 'cm', 'mm')
#         self.assertAlmostEqual(result.x, 10, places=7)
#         self.assertAlmostEqual(result.y, 20, places=7)
#         self.assertAlmostEqual(result.z, 30, places=7)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_inch_to_cm_point_conversion(self):
#         """Test point conversion from inches to cm."""
#         inch_point = MockPoint3D(1, 2, 3)
#         result = convert_point3D(inch_point, 'in', 'cm')
#         self.assertAlmostEqual(result.x, 2.54, places=7)
#         self.assertAlmostEqual(result.y, 5.08, places=7)
#         self.assertAlmostEqual(result.z, 7.62, places=7)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_zero_point_conversion(self):
#         """Test conversion of zero point."""
#         zero_point = MockPoint3D(0, 0, 0)
#         result = convert_point3D(zero_point, 'cm', 'mm')
#         self.assertEqual(result.x, 0)
#         self.assertEqual(result.y, 0)
#         self.assertEqual(result.z, 0)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_negative_coordinates_conversion(self):
#         """Test conversion of point with negative coordinates."""
#         negative_point = MockPoint3D(-1, -2, -3)
#         result = convert_point3D(negative_point, 'cm', 'mm')
#         self.assertAlmostEqual(result.x, -10, places=7)
#         self.assertAlmostEqual(result.y, -20, places=7)
#         self.assertAlmostEqual(result.z, -30, places=7)


# class TestGetPoint3DFromJson(unittest.TestCase):
#     """Test cases for the getPoint3DFromJson function."""
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_valid_json_point(self):
#         """Test creating Point3D from valid JSON array."""
#         json_point = [1.5, 2.7, 3.9]
#         result = getPoint3DFromJson(json_point)
#         self.assertEqual(result.x, 1.5)
#         self.assertEqual(result.y, 2.7)
#         self.assertEqual(result.z, 3.9)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_zero_coordinates(self):
#         """Test creating Point3D with zero coordinates."""
#         json_point = [0, 0, 0]
#         result = getPoint3DFromJson(json_point)
#         self.assertEqual(result.x, 0)
#         self.assertEqual(result.y, 0)
#         self.assertEqual(result.z, 0)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_negative_coordinates(self):
#         """Test creating Point3D with negative coordinates."""
#         json_point = [-1.5, -2.7, -3.9]
#         result = getPoint3DFromJson(json_point)
#         self.assertEqual(result.x, -1.5)
#         self.assertEqual(result.y, -2.7)
#         self.assertEqual(result.z, -3.9)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_integer_coordinates(self):
#         """Test creating Point3D with integer coordinates."""
#         json_point = [1, 2, 3]
#         result = getPoint3DFromJson(json_point)
#         self.assertEqual(result.x, 1)
#         self.assertEqual(result.y, 2)
#         self.assertEqual(result.z, 3)
    
#     def test_invalid_json_format(self):
#         """Test that invalid JSON format raises appropriate error."""
#         with self.assertRaises(IndexError):
#             getPoint3DFromJson([1, 2])  # Missing z coordinate
#         with self.assertRaises(IndexError):
#             getPoint3DFromJson([])  # Empty array


# class TestVector3DFromJson(unittest.TestCase):
#     """Test cases for the Vector3D_from_json function."""
    
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_valid_json_vector(self):
#         """Test creating Vector3D from valid JSON array."""
#         json_vector = [1.0, 0.0, 0.0]
#         result = Vector3D_from_json(json_vector)
#         self.assertEqual(result.x, 1.0)
#         self.assertEqual(result.y, 0.0)
#         self.assertEqual(result.z, 0.0)
    
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_unit_vectors(self):
#         """Test creating unit vectors."""
#         # X unit vector
#         result_x = Vector3D_from_json([1, 0, 0])
#         self.assertEqual(result_x.x, 1)
#         self.assertEqual(result_x.y, 0)
#         self.assertEqual(result_x.z, 0)
        
#         # Y unit vector
#         result_y = Vector3D_from_json([0, 1, 0])
#         self.assertEqual(result_y.x, 0)
#         self.assertEqual(result_y.y, 1)
#         self.assertEqual(result_y.z, 0)
        
#         # Z unit vector
#         result_z = Vector3D_from_json([0, 0, 1])
#         self.assertEqual(result_z.x, 0)
#         self.assertEqual(result_z.y, 0)
#         self.assertEqual(result_z.z, 1)
    
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_zero_vector(self):
#         """Test creating zero vector."""
#         json_vector = [0, 0, 0]
#         result = Vector3D_from_json(json_vector)
#         self.assertEqual(result.x, 0)
#         self.assertEqual(result.y, 0)
#         self.assertEqual(result.z, 0)
    
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_negative_components(self):
#         """Test creating vector with negative components."""
#         json_vector = [-1.5, -2.7, -3.9]
#         result = Vector3D_from_json(json_vector)
#         self.assertEqual(result.x, -1.5)
#         self.assertEqual(result.y, -2.7)
#         self.assertEqual(result.z, -3.9)


# class TestPoint3DFromJson(unittest.TestCase):
#     """Test cases for the Point3D_from_json function."""
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_valid_json_point(self):
#         """Test creating Point3D from valid JSON array."""
#         json_point = [10.5, 20.7, 30.9]
#         result = Point3D_from_json(json_point)
#         self.assertEqual(result.x, 10.5)
#         self.assertEqual(result.y, 20.7)
#         self.assertEqual(result.z, 30.9)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_origin_point(self):
#         """Test creating origin point."""
#         json_point = [0, 0, 0]
#         result = Point3D_from_json(json_point)
#         self.assertEqual(result.x, 0)
#         self.assertEqual(result.y, 0)
#         self.assertEqual(result.z, 0)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_large_coordinates(self):
#         """Test creating Point3D with large coordinates."""
#         json_point = [1000000, 2000000, 3000000]
#         result = Point3D_from_json(json_point)
#         self.assertEqual(result.x, 1000000)
#         self.assertEqual(result.y, 2000000)
#         self.assertEqual(result.z, 3000000)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_small_coordinates(self):
#         """Test creating Point3D with very small coordinates."""
#         json_point = [0.000001, 0.000002, 0.000003]
#         result = Point3D_from_json(json_point)
#         self.assertEqual(result.x, 0.000001)
#         self.assertEqual(result.y, 0.000002)
#         self.assertEqual(result.z, 0.000003)


# class TestMatrix3DFromJson(unittest.TestCase):
#     """Test cases for the Matrix3D_from_json function."""
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     @patch('adsk.core.Point3D', MockPoint3D)
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_valid_matrix_json(self):
#         """Test creating Matrix3D from valid JSON."""
#         json_matrix = {
#             'origin': [1, 2, 3],
#             'xaxis': [1, 0, 0],
#             'yaxis': [0, 1, 0],
#             'zaxis': [0, 0, 1]
#         }
#         result = Matrix3D_from_json(json_matrix)
        
#         self.assertIsInstance(result, MockMatrix3D)
#         self.assertEqual(result.origin.x, 1)
#         self.assertEqual(result.origin.y, 2)
#         self.assertEqual(result.origin.z, 3)
#         self.assertEqual(result.xaxis.x, 1)
#         self.assertEqual(result.xaxis.y, 0)
#         self.assertEqual(result.xaxis.z, 0)
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     @patch('adsk.core.Point3D', MockPoint3D)
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_identity_matrix_json(self):
#         """Test creating identity matrix from JSON."""
#         json_matrix = {
#             'origin': [0, 0, 0],
#             'xaxis': [1, 0, 0],
#             'yaxis': [0, 1, 0],
#             'zaxis': [0, 0, 1]
#         }
#         result = Matrix3D_from_json(json_matrix)
        
#         self.assertIsInstance(result, MockMatrix3D)
#         # Check origin is at zero
#         self.assertEqual(result.origin.x, 0)
#         self.assertEqual(result.origin.y, 0)
#         self.assertEqual(result.origin.z, 0)
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     @patch('adsk.core.Point3D', MockPoint3D)
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_translated_matrix_json(self):
#         """Test creating translated matrix from JSON."""
#         json_matrix = {
#             'origin': [10, 20, 30],
#             'xaxis': [1, 0, 0],
#             'yaxis': [0, 1, 0],
#             'zaxis': [0, 0, 1]
#         }
#         result = Matrix3D_from_json(json_matrix)
        
#         self.assertEqual(result.origin.x, 10)
#         self.assertEqual(result.origin.y, 20)
#         self.assertEqual(result.origin.z, 30)
    
#     def test_missing_keys_raises_keyerror(self):
#         """Test that missing keys in JSON raise KeyError."""
#         incomplete_json = {
#             'origin': [0, 0, 0],
#             'xaxis': [1, 0, 0]
#             # Missing yaxis and zaxis
#         }
#         with self.assertRaises(KeyError):
#             Matrix3D_from_json(incomplete_json)


# class TestInvert(unittest.TestCase):
#     """Test cases for the invert function."""
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     def test_invert_matrix(self):
#         """Test matrix inversion."""
#         original_matrix = MockMatrix3D()
#         inverted_matrix = invert(original_matrix)
        
#         # Should return a new matrix (copy)
#         self.assertIsNot(inverted_matrix, original_matrix)
#         self.assertTrue(hasattr(inverted_matrix, '_inverted'))
#         self.assertTrue(inverted_matrix._inverted)
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     def test_invert_preserves_original(self):
#         """Test that invert doesn't modify the original matrix."""
#         original_matrix = MockMatrix3D()
#         original_matrix.test_attribute = "original"
        
#         inverted_matrix = invert(original_matrix)
        
#         # Original should not have _inverted attribute
#         self.assertFalse(hasattr(original_matrix, '_inverted'))
#         # Inverted should be marked as inverted
#         self.assertTrue(hasattr(inverted_matrix, '_inverted'))


# class TestCompose(unittest.TestCase):
#     """Test cases for the compose function."""
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     def test_compose_matrices(self):
#         """Test matrix composition."""
#         matrix1 = MockMatrix3D()
#         matrix2 = MockMatrix3D()
        
#         result = compose(matrix1, matrix2)
        
#         # Should return a new matrix (copy of first)
#         self.assertIsNot(result, matrix1)
#         self.assertIsNot(result, matrix2)
#         self.assertTrue(hasattr(result, '_transformed_by'))
#         self.assertEqual(result._transformed_by, matrix2)
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     def test_compose_preserves_original(self):
#         """Test that compose doesn't modify original matrices."""
#         matrix1 = MockMatrix3D()
#         matrix2 = MockMatrix3D()
#         matrix1.test_attribute = "matrix1"
#         matrix2.test_attribute = "matrix2"
        
#         result = compose(matrix1, matrix2)
        
#         # Originals should not be modified
#         self.assertFalse(hasattr(matrix1, '_transformed_by'))
#         self.assertFalse(hasattr(matrix2, '_transformed_by'))
#         # Result should be transformed
#         self.assertTrue(hasattr(result, '_transformed_by'))


# class TestStockModeFromStr(unittest.TestCase):
#     """Test cases for the stockMode_from_str function."""
    
#     def test_valid_stock_modes(self):
#         """Test conversion of valid stock mode strings."""
#         test_cases = [
#             ("FixedBoxStock", "FixedBoxStock"),
#             ("RelativeBoxStock", "RelativeBoxStock"),
#             ("FixedCylinderStock", "FixedCylinderStock"),
#             ("RelativeCylinderStock", "RelativeCylinderStock"),
#             ("FixedTubeStock", "FixedTubeStock"),
#             ("RelativeTubeStock", "RelativeTubeStock"),
#             ("SolidStock", "SolidStock"),
#             ("PreviousSetupStock", "PreviousSetupStock")
#         ]
        
#         for input_str, expected in test_cases:
#             with self.subTest(input_str=input_str):
#                 result = stockMode_from_str(input_str)
#                 self.assertEqual(result, expected)
    
#     def test_invalid_stock_mode(self):
#         """Test that invalid stock mode strings raise Exception."""
#         invalid_modes = ["InvalidMode", "BoxStock", "", "None", "undefined"]
        
#         for invalid_mode in invalid_modes:
#             with self.subTest(invalid_mode=invalid_mode):
#                 with self.assertRaises(Exception) as context:
#                     stockMode_from_str(invalid_mode)
#                 self.assertIn("Unknown stock mode", str(context.exception))
    
#     def test_case_sensitive(self):
#         """Test that function is case sensitive."""
#         with self.assertRaises(Exception):
#             stockMode_from_str("fixedboxstock")  # lowercase
#         with self.assertRaises(Exception):
#             stockMode_from_str("FIXEDBOXSTOCK")  # uppercase


# class TestConstructCoordSystem(unittest.TestCase):
#     """Test cases for the construct_coord_system function."""
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_construct_coord_system(self):
#         """Test coordinate system construction."""
#         mock_fusion = Mock()
#         mock_body_component = MockComponent()
#         origin = MockPoint3D(1, 2, 3)
#         xaxis = MockVector3D(1, 0, 0)
#         zaxis = MockVector3D(0, 0, 1)
        
#         result = construct_coord_system(mock_fusion, mock_body_component, origin, xaxis, zaxis)
        
#         # Check that result has required attributes
#         self.assertTrue(hasattr(result, 'originPoint'))
#         self.assertTrue(hasattr(result, 'xAxis'))
#         self.assertTrue(hasattr(result, 'zAxis'))
        
#         # Check that light bulbs are turned off (visibility set to False)
#         self.assertFalse(result.originPoint.isLightBulbOn)
#         self.assertFalse(result.xAxis.isLightBulbOn)
#         self.assertFalse(result.zAxis.isLightBulbOn)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_construct_coord_system_with_zero_origin(self):
#         """Test coordinate system construction with zero origin."""
#         mock_fusion = Mock()
#         mock_body_component = MockComponent()
#         origin = MockPoint3D(0, 0, 0)
#         xaxis = MockVector3D(1, 0, 0)
#         zaxis = MockVector3D(0, 0, 1)
        
#         result = construct_coord_system(mock_fusion, mock_body_component, origin, xaxis, zaxis)
        
#         self.assertIsNotNone(result.originPoint)
#         self.assertIsNotNone(result.xAxis)
#         self.assertIsNotNone(result.zAxis)
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_construct_coord_system_different_axes(self):
#         """Test coordinate system construction with different axis orientations."""
#         mock_fusion = Mock()
#         mock_body_component = MockComponent()
#         origin = MockPoint3D(5, 10, 15)
#         xaxis = MockVector3D(0, 1, 0)  # Y direction as X axis
#         zaxis = MockVector3D(1, 0, 0)  # X direction as Z axis
        
#         result = construct_coord_system(mock_fusion, mock_body_component, origin, xaxis, zaxis)
        
#         # Should still create the coordinate system successfully
#         self.assertTrue(hasattr(result, 'originPoint'))
#         self.assertTrue(hasattr(result, 'xAxis'))
#         self.assertTrue(hasattr(result, 'zAxis'))


# class TestIntegrationNewFunctions(unittest.TestCase):
#     """Integration tests for the new functions."""
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     @patch('adsk.core.Point3D', MockPoint3D)
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_matrix_creation_and_inversion(self):
#         """Test creating a matrix from JSON and then inverting it."""
#         json_matrix = {
#             'origin': [1, 2, 3],
#             'xaxis': [1, 0, 0],
#             'yaxis': [0, 1, 0],
#             'zaxis': [0, 0, 1]
#         }
        
#         # Create matrix from JSON
#         matrix = Matrix3D_from_json(json_matrix)
        
#         # Invert the matrix
#         inverted = invert(matrix)
        
#         # Verify inversion
#         self.assertTrue(hasattr(inverted, '_inverted'))
#         self.assertTrue(inverted._inverted)
#         # Original should be unchanged
#         self.assertFalse(hasattr(matrix, '_inverted'))
    
#     @patch('adsk.core.Matrix3D', MockMatrix3D)
#     @patch('adsk.core.Point3D', MockPoint3D)
#     @patch('adsk.core.Vector3D', MockVector3D)
#     def test_matrix_creation_and_composition(self):
#         """Test creating matrices from JSON and composing them."""
#         json_matrix1 = {
#             'origin': [1, 0, 0],
#             'xaxis': [1, 0, 0],
#             'yaxis': [0, 1, 0],
#             'zaxis': [0, 0, 1]
#         }
        
#         json_matrix2 = {
#             'origin': [0, 1, 0],
#             'xaxis': [1, 0, 0],
#             'yaxis': [0, 1, 0],
#             'zaxis': [0, 0, 1]
#         }
        
#         matrix1 = Matrix3D_from_json(json_matrix1)
#         matrix2 = Matrix3D_from_json(json_matrix2)
        
#         # Compose matrices
#         composed = compose(matrix1, matrix2)
        
#         # Verify composition
#         self.assertTrue(hasattr(composed, '_transformed_by'))
#         self.assertEqual(composed._transformed_by, matrix2)
    
#     def test_stock_mode_and_coord_system_integration(self):
#         """Test that stock mode conversion works with coordinate system construction."""
#         # Test stock mode conversion
#         stock_mode = stockMode_from_str("FixedBoxStock")
#         self.assertEqual(stock_mode, "FixedBoxStock")
        
#         # This would typically be used together in a larger workflow
#         # The test verifies both functions work independently
#         mock_fusion = Mock()
#         mock_body_component = MockComponent()
#         origin = MockPoint3D(0, 0, 0)
#         xaxis = MockVector3D(1, 0, 0)
#         zaxis = MockVector3D(0, 0, 1)
        
#         with patch('adsk.core.Point3D', MockPoint3D):
#             coord_system = construct_coord_system(mock_fusion, mock_body_component, origin, xaxis, zaxis)
            
#         # Both functions should work without interference
#         self.assertIsNotNone(coord_system)
#         self.assertEqual(stock_mode, "FixedBoxStock")



#     """Integration tests combining multiple functions."""
    
#     @patch('adsk.core.Point3D', MockPoint3D)
#     def test_json_to_point_and_convert_units(self):
#         """Test creating point from JSON and converting units."""
#         # Create a point in inches from JSON
#         json_point = [1, 2, 3]  # inches
#         point_in_inches = Point3D_from_json(json_point)
        
#         # Convert to centimeters
#         point_in_cm = convert_point3D(point_in_inches, 'in', 'cm')
        
#         self.assertAlmostEqual(point_in_cm.x, 2.54, places=7)
#         self.assertAlmostEqual(point_in_cm.y, 5.08, places=7)
#         self.assertAlmostEqual(point_in_cm.z, 7.62, places=7)
    
#     def test_multiple_unit_conversions(self):
#         """Test chaining multiple unit conversions."""
#         # Start with 1 meter
#         original_value = 1
        
#         # Convert m -> cm -> mm -> in -> ft
#         cm_value = convert_units(original_value, 'm', 'cm')
#         mm_value = convert_units(cm_value, 'cm', 'mm')
#         in_value = convert_units(mm_value, 'mm', 'in')
#         ft_value = convert_units(in_value, 'in', 'ft')
        
#         # Should be approximately 3.28084 feet
#         self.assertAlmostEqual(ft_value, 3.28084, places=5)


# if __name__ == '__main__':
#     unittest.main()
# #     # Create a test suite
# #     test_suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    
# #     # Run the tests with verbose output
# #     unittest.TextTestRunner(verbosity=2).run(test_suite)