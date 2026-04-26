"""Shared mock module for the Fusion 360 adsk API.

All test files should import from here instead of defining their own mocks.
Call setup_adsk_modules() at module level before importing any code that
depends on the adsk module.
"""

import sys
from unittest.mock import Mock


# ---- Geometry mocks ---------------------------------------------------------

class MockPoint3D:
    def __init__(self, x=0, y=0, z=0):
        self.x = x
        self.y = y
        self.z = z

    @classmethod
    def create(cls, x, y, z):
        return cls(x, y, z)

    def distanceTo(self, other):
        return ((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)**0.5


class MockVector3D:
    def __init__(self, x=0, y=0, z=0):
        self.x = x
        self.y = y
        self.z = z

    @classmethod
    def create(cls, x, y, z):
        return cls(x, y, z)

    def crossProduct(self, other):
        return MockVector3D(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x
        )


class MockMatrix3D:
    """Unified Matrix3D mock supporting both cell-based and coordinate-system access."""

    def __init__(self):
        self._cells = [[1 if i == j else 0 for j in range(4)] for i in range(4)]
        self.origin = None
        self.xaxis = None
        self.yaxis = None
        self.zaxis = None

    @classmethod
    def create(cls):
        return cls()

    # Cell-based access
    def setCell(self, row, col, value):
        self._cells[row][col] = value

    def getCell(self, row, col):
        return self._cells[row][col]

    # Coordinate-system access
    def setWithCoordinateSystem(self, origin, xaxis, yaxis, zaxis):
        self.origin = origin
        self.xaxis = xaxis
        self.yaxis = yaxis
        self.zaxis = zaxis
        return True

    def getAsCoordinateSystem(self):
        return self.origin, self.xaxis, self.yaxis, self.zaxis

    @property
    def translation(self):
        mock_translation = Mock()
        mock_translation.x = self._cells[0][3]
        mock_translation.y = self._cells[1][3]
        mock_translation.z = self._cells[2][3]
        return mock_translation

    @translation.setter
    def translation(self, vec):
        self._cells[0][3] = vec.x
        self._cells[1][3] = vec.y
        self._cells[2][3] = vec.z

    def copy(self):
        new_matrix = MockMatrix3D()
        new_matrix._cells = [row[:] for row in self._cells]
        new_matrix.origin = self.origin
        new_matrix.xaxis = self.xaxis
        new_matrix.yaxis = self.yaxis
        new_matrix.zaxis = self.zaxis
        return new_matrix

    def invert(self):
        self._inverted = True
        return True

    def transformBy(self, other_matrix):
        self._transformed_by = other_matrix
        return True


class MockBoundingBox:
    def __init__(self):
        self.minPoint = MockPoint3D(0, 0, 0)
        self.maxPoint = MockPoint3D(10, 10, 10)


# ---- Component / Occurrence mocks ------------------------------------------

class MockOccurrences:
    def __init__(self):
        self._items = []

    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index] if index < len(self._items) else None

    def __iter__(self):
        return iter(self._items)


class MockJointOrigins:
    def __init__(self):
        self._items = []

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index] if index < len(self._items) else None

    def itemByName(self, name):
        for item in self._items:
            if item.name == name:
                return item
        return None


class MockJoints:
    def __init__(self):
        self._items = []

    def createInput(self, origin1, origin2):
        return MockJointInput()

    def add(self, joint_input):
        joint = MockJoint()
        self._items.append(joint)
        return joint

    def itemByName(self, name):
        for item in self._items:
            if item.name == name:
                return item
        return None


class MockJointInput:
    def __init__(self):
        self.isFlipped = False

    def setAsRigidJointMotion(self):
        pass

    def setAsPlanarJointMotion(self, direction):
        pass

    def setAsRevoluteJointMotion(self, direction):
        pass


class MockJoint:
    def __init__(self):
        self.name = ""
        self.offsetX = Mock()
        self.offsetY = Mock()
        self.offset = Mock()


class MockBodies:
    def __init__(self):
        self._items = []

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index] if index < len(self._items) else None


class MockRigidGroups:
    def __init__(self):
        self._items = []

    def add(self, collection, include_children):
        group = Mock()
        group.name = ""
        self._items.append(group)
        return group


class MockComponent:
    def __init__(self, name=""):
        self.name = name
        self.occurrences = MockOccurrences()
        self.jointOrigins = MockJointOrigins()
        self.bRepBodies = MockBodies()
        self.opacity = 1.0

    @property
    def xYConstructionPlane(self):
        return Mock()


class MockOccurrence:
    def __init__(self, name=""):
        self.component = MockComponent(name)
        self.isGroundToParent = False
        self.name = name
        self.boundingBox = MockBoundingBox()

    def createForAssemblyContext(self, parent):
        return self


# ---- Type-hint stub classes -------------------------------------------------
# These are real classes (not Mock) so PEP 604 type unions (X | Y) work.

class MockBRepBody:
    pass


class MockDesign:
    pass


class MockSetup:
    pass


class MockCadObjectParameterValue:
    pass


# ---- Module setup -----------------------------------------------------------

def setup_adsk_modules():
    """Install mock adsk modules into sys.modules.

    Call this at module level before importing any code that depends on adsk.
    Returns (mock_adsk, mock_adsk_core, mock_adsk_fusion, mock_adsk_cam) so
    tests can access the module mocks if needed.
    """
    mock_adsk_core = Mock()
    mock_adsk_core.Matrix3D = MockMatrix3D
    mock_adsk_core.Vector3D = MockVector3D
    mock_adsk_core.Point3D = MockPoint3D
    mock_adsk_core.Application.get.return_value = Mock()
    mock_adsk_core.Application.get.return_value.userInterface = Mock()
    
    # Set up UserInterfaceThemes enum values
    mock_adsk_core.UserInterfaceThemes = Mock()
    mock_adsk_core.UserInterfaceThemes.LightGrayUserInterfaceTheme = 0
    mock_adsk_core.UserInterfaceThemes.DarkBlueUserInterfaceTheme = 1
    mock_adsk_core.UserInterfaceThemes.ClassicUserInterfaceTheme = 2
    
    # Set up DialogResults enum values
    mock_adsk_core.DialogResults = Mock()
    mock_adsk_core.DialogResults.DialogOK = 0
    mock_adsk_core.DialogResults.DialogCancel = 1

    mock_adsk_fusion = Mock()
    mock_adsk_fusion.BRepBody = MockBRepBody
    mock_adsk_fusion.Design = MockDesign
    mock_adsk_fusion.Occurrence = MockOccurrence
    mock_adsk_fusion.Component = MockComponent
    mock_adsk_fusion.JointDirections.ZAxisJointDirection = 0

    mock_adsk_cam = Mock()
    mock_adsk_cam.Setup = MockSetup
    mock_adsk_cam.CadObjectParameterValue = MockCadObjectParameterValue

    mock_adsk = Mock()
    mock_adsk.core = mock_adsk_core
    mock_adsk.fusion = mock_adsk_fusion
    mock_adsk.cam = mock_adsk_cam

    sys.modules['adsk'] = mock_adsk
    sys.modules['adsk.core'] = mock_adsk_core
    sys.modules['adsk.fusion'] = mock_adsk_fusion
    sys.modules['adsk.cam'] = mock_adsk_cam

    return mock_adsk, mock_adsk_core, mock_adsk_fusion, mock_adsk_cam
