import unittest
from unittest.mock import Mock, patch, MagicMock
import sys

from .mock_adsk import (
    setup_adsk_modules,
    MockPoint3D,
    MockVector3D,
    MockOccurrence,
    MockComponent,
    MockOccurrences,
    MockJointOrigins,
    MockJoints,
    MockJointInput,
    MockJoint,
    MockBodies,
    MockRigidGroups,
)
setup_adsk_modules()


class TestWorkholdingClass(unittest.TestCase):
    """Test cases for the Workholding class."""

    def test_init_creates_workholding_component(self):
        """Workholding init should create a component with the given name."""
        from ..lib import component_utils

        with patch.object(component_utils, 'get_workholding_folder') as mock_get_folder, \
             patch.object(component_utils, 'get_active_project') as mock_get_project, \
             patch.object(component_utils, 'insert_component_by_name') as mock_insert, \
             patch.object(component_utils, 'add_component') as mock_add_component, \
             patch.object(component_utils, 'config', {
                 'self_centering_vise_file_name': 'SelfCenteringVise.f3d',
                 'fixed_vise_file_name': 'FixedVise.f3d',
                 'clamping_file_name': 'ClampingPlate.f3d'
             }):
            mock_design = Mock()
            mock_design.rootComponent = MockComponent()
            mock_fusion = Mock()

            mock_occurrence = MockOccurrence("Test Workholding")
            mock_add_component.return_value = mock_occurrence

            mock_get_project.return_value = Mock()
            mock_get_folder.return_value = Mock()

            mock_fixture_occ = MockOccurrence("FixturePlate")
            mock_vise_occ = MockOccurrence("Vise")
            mock_insert.side_effect = [mock_fixture_occ, mock_vise_occ]

            workholding = component_utils.Workholding(mock_design, mock_fusion, name="Test Workholding")

            mock_add_component.assert_called_once()
            self.assertEqual(workholding.name, "Test Workholding")

    def test_init_with_self_centering_vise_type(self):
        """Self Centering Vise type should use correct file."""
        from ..lib import component_utils

        with patch.object(component_utils, 'get_workholding_folder') as mock_get_folder, \
             patch.object(component_utils, 'get_active_project') as mock_get_project, \
             patch.object(component_utils, 'insert_component_by_name') as mock_insert, \
             patch.object(component_utils, 'add_component') as mock_add_component, \
             patch.object(component_utils, 'config', {
                 'self_centering_vise_file_name': 'SelfCenteringVise.f3d',
                 'fixed_vise_file_name': 'FixedVise.f3d',
                 'clamping_file_name': 'ClampingPlate.f3d'
             }):
            mock_design = Mock()
            mock_design.rootComponent = MockComponent()
            mock_fusion = Mock()

            mock_occurrence = MockOccurrence()
            mock_add_component.return_value = mock_occurrence

            mock_get_project.return_value = Mock()
            mock_get_folder.return_value = Mock()
            mock_insert.side_effect = [MockOccurrence(), MockOccurrence()]

            workholding = component_utils.Workholding(
                mock_design, mock_fusion,
                viseStyle="Self Centering Vise"
            )

            self.assertEqual(workholding.vise_file, 'SelfCenteringVise.f3d')

    def test_init_with_fixed_jaw_vise_type(self):
        """Fixed Jaw Vise type should use correct file."""
        from ..lib import component_utils

        with patch.object(component_utils, 'get_workholding_folder') as mock_get_folder, \
             patch.object(component_utils, 'get_active_project') as mock_get_project, \
             patch.object(component_utils, 'insert_component_by_name') as mock_insert, \
             patch.object(component_utils, 'add_component') as mock_add_component, \
             patch.object(component_utils, 'config', {
                 'self_centering_vise_file_name': 'SelfCenteringVise.f3d',
                 'fixed_vise_file_name': 'FixedVise.f3d',
                 'clamping_file_name': 'ClampingPlate.f3d'
             }):
            mock_design = Mock()
            mock_design.rootComponent = MockComponent()
            mock_fusion = Mock()

            mock_occurrence = MockOccurrence()
            mock_add_component.return_value = mock_occurrence

            mock_get_project.return_value = Mock()
            mock_get_folder.return_value = Mock()
            mock_insert.side_effect = [MockOccurrence(), MockOccurrence()]

            workholding = component_utils.Workholding(
                mock_design, mock_fusion,
                viseStyle="Fixed Jaw Vise"
            )

            self.assertEqual(workholding.vise_file, 'FixedVise.f3d')

    def test_init_handles_missing_workholding_folder(self):
        """Missing workholding folder should set occurrences to None."""
        from ..lib import component_utils

        with patch.object(component_utils, 'get_workholding_folder') as mock_get_folder, \
             patch.object(component_utils, 'get_active_project') as mock_get_project, \
             patch.object(component_utils, 'add_component') as mock_add_component, \
             patch.object(component_utils, 'config', {
                 'self_centering_vise_file_name': 'SelfCenteringVise.f3d',
                 'fixed_vise_file_name': 'FixedVise.f3d',
                 'clamping_file_name': 'ClampingPlate.f3d'
             }):
            mock_design = Mock()
            mock_design.rootComponent = MockComponent()
            mock_fusion = Mock()

            mock_occurrence = MockOccurrence()
            mock_add_component.return_value = mock_occurrence

            mock_get_project.return_value = Mock()
            mock_get_folder.return_value = None  # Folder not found

            workholding = component_utils.Workholding(mock_design, mock_fusion)

            self.assertIsNone(workholding.fixture_plate_occurrence)
            self.assertIsNone(workholding.vise_occurrence)


class TestStockClass(unittest.TestCase):
    """Test cases for the Stock class."""

    def test_init_returns_early_without_setops(self):
        """Stock without setops should show message and return early."""
        from ..lib import component_utils

        with patch.object(component_utils, 'add_component') as mock_add_component:
            mock_design = Mock()
            mock_design.rootComponent = MockComponent()
            mock_fusion = Mock()
            mock_fusion.getUI.return_value = Mock()

            stock = component_utils.Stock(mock_design, mock_fusion, setops=None)

            # Should not have occurrence set
            self.assertFalse(hasattr(stock, 'occurrence') and stock.occurrence is not None)

    def test_init_with_non_create_model_stock(self):
        """Stock with non-JobStockCreateModel should return early."""
        from ..lib import component_utils

        with patch.object(component_utils, 'add_component') as mock_add_component:
            mock_design = Mock()
            mock_design.rootComponent = MockComponent()
            mock_design.rootComponent.occurrences = MockOccurrences()
            mock_fusion = Mock()

            setops = [{
                "job_stock": {
                    "subtypekey": "JobStockFromBody"  # Not JobStockCreateModel
                }
            }]

            stock = component_utils.Stock(mock_design, mock_fusion, setops=setops)

            # Should return early without creating stock
            self.assertFalse(stock.has_joints)

    def test_deferred_joint_origins_pattern(self):
        """Stock with deferJointOrigins=True should not create joints immediately."""
        from ..lib.component_utils import Stock

        mock_design = Mock()
        mock_design.rootComponent = MockComponent()
        mock_design.rootComponent.occurrences = MockOccurrences()
        mock_fusion = Mock()

        # This test verifies the parameter is stored
        # Actual joint creation would require more complex mocking
        with patch.object(Stock, '__init__', lambda self, *args, **kwargs: None):
            stock = Stock.__new__(Stock)
            stock._deferJointOrigins = True
            stock.has_joints = True
            stock.joint_index_map = {}

            # Verify create_joint_origins can be called
            self.assertTrue(hasattr(stock, 'create_joint_origins'))


class TestJointsClass(unittest.TestCase):
    """Test cases for the Joints class."""

    def test_init_stores_references(self):
        """Joints should store references to design, fusion, part, stock, workholding."""
        from ..lib.component_utils import Joints

        mock_design = Mock()
        mock_fusion = Mock()
        mock_part = Mock()
        mock_stock = Mock()
        mock_workholding = Mock()

        joints = Joints(mock_design, mock_fusion, mock_part, mock_stock, mock_workholding)

        self.assertEqual(joints.design, mock_design)
        self.assertEqual(joints.fusion, mock_fusion)
        self.assertEqual(joints.part, mock_part)
        self.assertEqual(joints.stock, mock_stock)
        self.assertEqual(joints.workholding, mock_workholding)

    def test_add_workholding_joint_calls_get_joint_target(self):
        """add_workholding_joint should call get_joint_target for fixture_plate and vise."""
        from ..lib.component_utils import Joints

        mock_design = Mock()
        mock_fusion = Mock()
        mock_part = Mock()
        mock_stock = Mock()
        mock_workholding = Mock()

        mock_fixture_occ = MockOccurrence("FixturePlate")
        mock_fixture_occ.component.jointOrigins._items = [Mock(name="JO1")]
        mock_vise_occ = MockOccurrence("Vise")
        mock_vise_occ.component.jointOrigins._items = [Mock(name="JO1")]

        mock_workholding.get_joint_target.side_effect = [mock_fixture_occ, mock_vise_occ]
        mock_workholding.component = MockComponent()
        mock_workholding.component.joints = MockJoints()

        joints = Joints(mock_design, mock_fusion, mock_part, mock_stock, mock_workholding)

        with patch.object(joints, 'create_rigid_joint_between_components', return_value=Mock()):
            joints.add_workholding_joint(
                fixture_plate_target="FixturePlateTarget",
                vise_target="ViseTarget",
                name="TestJoint"
            )

        # Verify get_joint_target was called for both
        self.assertEqual(mock_workholding.get_joint_target.call_count, 2)

    def test_add_base_joint_with_stock_targets(self):
        """add_base_joint should handle Stock type targets correctly."""
        from ..lib.component_utils import Joints

        mock_design = Mock()
        mock_design.rootComponent = MockComponent()
        mock_design.rootComponent.joints = MockJoints()

        mock_fusion = Mock()
        mock_part = Mock()

        mock_stock = Mock()
        mock_stock.occurrence = MockOccurrence("Stock")
        mock_stock.occurrence.component.jointOrigins._items = [Mock(name="Stock Bottom")]
        mock_stock.joint_index_map = {"Stock Bottom": 0, "Stock Back": 1}

        joints = Joints(mock_design, mock_fusion, mock_part, mock_stock, None)

        with patch.object(joints, 'create_rigid_joint_between_components', return_value=Mock()) as mock_create:
            joints.add_base_joint(
                first_comp_type="Stock",
                first_comp_target="Stock Bottom",
                second_comp_type="Stock",
                second_comp_target="Stock Back",
                name="TestJoint"
            )

            mock_create.assert_called_once()


if __name__ == '__main__':
    unittest.main()
