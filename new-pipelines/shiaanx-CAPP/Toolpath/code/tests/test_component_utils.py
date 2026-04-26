import unittest
from unittest.mock import Mock, patch, MagicMock
from contextlib import ExitStack
import sys

from .mock_adsk import (
    setup_adsk_modules,
    MockBRepBody,
    MockDesign,
    MockSetup,
    MockCadObjectParameterValue,
    MockOccurrence as _MockOccurrence,
    MockComponent as _MockComponent,
    MockPoint3D,
)
_, mock_adsk_core, _, _ = setup_adsk_modules()

# Now we can import from component_utils
from ..lib.component_utils import FusionFullPath


class TestGetSetupBodyOccurrence(unittest.TestCase):
    """
    Regression test for APP-971: Silhouette selection used wrong body.

    When a component has multiple bodies and the setup selects a non-first body,
    get_setup_body_occurrence must return that specific body, not the first
    body in the occurrence's bRepBodies collection.
    """

    def test_returns_tuple_of_body_and_occurrence(self):
        """Verify the function returns a (body, occurrence) tuple."""
        fusion_paths = FusionFullPath()

        mock_body = Mock()
        mock_body.name = 'TestBody'
        mock_body.parentComponent = Mock()
        mock_body.parentComponent.name = 'TestComponent'
        mock_body.boundingBox = Mock()
        mock_body.boundingBox.minPoint = Mock(x=0, y=0, z=0)
        mock_body.boundingBox.maxPoint = Mock(x=1, y=1, z=1)

        mock_occurrence = Mock()
        mock_occurrence.name = 'TestOccurrence'
        mock_occurrence.fullPathName = 'TestOccurrence:1'
        mock_body.assemblyContext = mock_occurrence

        mock_setup = Mock()
        mock_setup.name = 'Setup1'

        with patch.object(fusion_paths, 'get_bodies', return_value=[mock_body]):
            with patch.object(fusion_paths, 'get_occurence', return_value=mock_occurrence):
                result = fusion_paths.get_setup_body_occurrence(mock_setup)

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        body, occurrence = result
        self.assertEqual(body, mock_body)
        self.assertEqual(occurrence, mock_occurrence)

    def test_returns_selected_body_not_first_body_in_occurrence(self):
        """
        Critical regression test: When occurrence has multiple bodies,
        must return the body selected in setup, not bRepBodies.item(0).

        This tests the fix for a bug where silhouette selection was computed
        on the wrong body because get_setup_body_occurrence only returned the
        occurrence, and later code called get_body(occurrence) which returned
        the first body in the occurrence rather than the actually selected body.
        """
        fusion_paths = FusionFullPath()

        # Create mock bodies - selected_body is NOT the first one
        body1 = Mock(name='Body1')
        body1.name = 'Body1'

        body2 = Mock(name='Body2')
        body2.name = 'Body2'

        selected_body = Mock(name='loadcell mount')
        selected_body.name = 'loadcell mount'
        selected_body.parentComponent = Mock()
        selected_body.parentComponent.name = 'loadcell ometer v1'
        selected_body.boundingBox = Mock()
        selected_body.boundingBox.minPoint = Mock(x=-2.6, y=-3.1, z=7.6)
        selected_body.boundingBox.maxPoint = Mock(x=2.6, y=3.1, z=10.2)

        # All bodies share the same parent occurrence
        parent_occurrence = Mock()
        parent_occurrence.name = 'loadcell ometer v1:1'
        parent_occurrence.fullPathName = 'CAM Component:1+loadcell ometer v1:1'
        parent_occurrence.bRepBodies = Mock()
        parent_occurrence.bRepBodies.count = 3
        # Body1 is at index 0, selected_body is at index 2
        parent_occurrence.bRepBodies.item = lambda i: [body1, body2, selected_body][i]

        selected_body.assemblyContext = parent_occurrence

        mock_setup = Mock()
        mock_setup.name = 'Setup1'

        # get_bodies returns the SELECTED body from setup (as configured by user)
        with patch.object(fusion_paths, 'get_bodies', return_value=[selected_body]):
            with patch.object(fusion_paths, 'get_occurence', return_value=parent_occurrence):
                result_body, result_occurrence = fusion_paths.get_setup_body_occurrence(mock_setup)

        # CRITICAL: Must return selected_body, NOT body1
        self.assertEqual(result_body.name, 'loadcell mount',
            "Must return the body selected in setup, not first body in occurrence")
        self.assertNotEqual(result_body.name, 'Body1',
            "BUG REGRESSION: Returned first body instead of selected body")

        # Verify occurrence is also returned correctly
        self.assertEqual(result_occurrence.name, 'loadcell ometer v1:1')


class TestTryOpSelectHole(unittest.TestCase):
    """
    Regression test for hole selection with multi-body components.

    When a component has multiple bodies and the setup selects a non-first body,
    try_op_select_hole must search for cylinders on the selected body, not the
    first body in the occurrence's bRepBodies collection.

    This is the same class of bug as APP-971 (silhouette selection).
    """

    def _create_cylinder_face_mocks(self, centroid=(1.0, 2.0, 3.0)):
        """Create mock cylinder face and its native counterpart."""
        cylinder_face = Mock()
        cylinder_face.geometry = Mock()
        cylinder_face.geometry.classType = Mock(return_value='adsk::core::Cylinder')

        native_face = Mock()
        native_face.centroid = Mock(x=centroid[0], y=centroid[1], z=centroid[2])
        return cylinder_face, native_face

    def _create_mock_body(self, name, faces=None):
        """Create mock body with optional faces."""
        body = Mock(name=name)
        body.name = name
        body.faces = faces if faces is not None else []
        return body

    def _create_mock_occurrence(self, name, bodies):
        """Create mock occurrence containing multiple bodies."""
        occ = Mock()
        occ.name = name
        occ.bRepBodies = Mock()
        occ.bRepBodies.count = len(bodies)
        occ.bRepBodies.item = lambda i: bodies[i]
        return occ

    def _create_mock_sketch_book(self):
        """Create mock sketch_book with identity transform."""
        sketch_book = Mock()
        sketch_book.occman = Mock()
        sketch_book.occman.get_T_world_from_sketch = Mock(return_value=Mock())
        return sketch_book

    def _setup_hole_selection_patches(self, stack, get_native_body_fn, mock_param):
        """Apply common patches for hole selection tests. Returns mock_fusion_paths."""
        mock_fusion_paths = Mock()
        stack.enter_context(
            patch('Toolpath.code.commands.command_RequestFusionOps.logic.FusionFullPath',
                  return_value=mock_fusion_paths))
        mock_fusion_paths.get_native_body = get_native_body_fn

        stack.enter_context(
            patch('Toolpath.code.commands.command_RequestFusionOps.logic.get_parameter',
                  return_value=mock_param))
        stack.enter_context(
            patch('Toolpath.code.commands.command_RequestFusionOps.logic.set_parameter'))

        transformed_point = Mock(x=1.0, y=2.0, z=3.0)
        stack.enter_context(
            patch('Toolpath.code.commands.command_RequestFusionOps.logic.transform_point',
                  return_value=transformed_point))

        # Patch adsk.cam.CadObjectParameterValue at the logic module's namespace for isinstance checks
        stack.enter_context(
            patch('Toolpath.code.commands.command_RequestFusionOps.logic.adsk.cam.CadObjectParameterValue',
                  MockCadObjectParameterValue))

        return mock_fusion_paths

    def test_without_setup_body_searches_wrong_body(self):
        """
        Documents the bug scenario: When setup_body is not provided,
        get_body(occurrence) returns the first body which may be wrong.

        Scenario:
        - Occurrence has 3 bodies: Body1 (no holes), Body2 (no holes), SelectedBody (has holes)
        - Setup selects SelectedBody for machining
        - Without setup_body, hole selection searches Body1 and fails
        """
        from ..commands.command_RequestFusionOps.logic import try_op_select_hole

        # Create cylinder face for selected body
        cylinder_face, native_cylinder_face = self._create_cylinder_face_mocks()

        # Create bodies
        selected_body = self._create_mock_body('SelectedBody', faces=[cylinder_face])
        native_selected_body = Mock()
        native_selected_body.faces = [native_cylinder_face]

        body1 = self._create_mock_body('Body1', faces=[])
        native_body1 = Mock()
        native_body1.faces = []

        # Create occurrence with multiple bodies
        parent_occurrence = self._create_mock_occurrence(
            'MultiBodyComponent:1',
            [body1, Mock(), selected_body]
        )

        mock_sketch_book = self._create_mock_sketch_book()

        mock_param = Mock()
        mock_param.value = Mock()
        mock_param.value.value = []

        single_hole_selections = [{"centroid": [1.0, 2.0, 3.0]}]

        def mock_get_body(obj):
            if obj == parent_occurrence:
                return body1  # BUG: returns first body
            return obj

        def mock_get_native_body(body):
            if body == selected_body:
                return native_selected_body
            elif body == body1:
                return native_body1
            return Mock(faces=[])

        with ExitStack() as stack:
            mock_fusion_paths = self._setup_hole_selection_patches(
                stack, mock_get_native_body, mock_param)
            mock_fusion_paths.get_body = mock_get_body

            result = try_op_select_hole(
                op=Mock(),
                body_occurrence=parent_occurrence,
                single_hole_selections=single_hole_selections,
                selection_param_name="holeFace",
                selection_param_value_type="CadObjectParameterValue",
                sketch_book=mock_sketch_book,
                # setup_body not provided - triggers fallback to get_body()
            )

        # Without setup_body, searches wrong body (Body1) and fails
        self.assertIsNotNone(result,
            "Expected error because wrong body (Body1 with no cylinders) was searched")
        self.assertIn("got 0 cylinders", result,
            "Should fail to find cylinders when searching wrong body")

    def test_with_setup_body_finds_correct_cylinders(self):
        """
        Test that when setup_body is provided, the correct body's cylinders are found.
        """
        from ..commands.command_RequestFusionOps.logic import try_op_select_hole

        # Create cylinder face for selected body
        cylinder_face, native_cylinder_face = self._create_cylinder_face_mocks()

        # Create bodies
        selected_body = self._create_mock_body('SelectedBody', faces=[cylinder_face])
        native_selected_body = Mock()
        native_selected_body.faces = [native_cylinder_face]

        body1 = self._create_mock_body('Body1', faces=[])

        # Create occurrence
        parent_occurrence = self._create_mock_occurrence(
            'MultiBodyComponent:1',
            [body1, selected_body]
        )

        mock_sketch_book = self._create_mock_sketch_book()

        mock_param = Mock()
        mock_param_value = MockCadObjectParameterValue()
        mock_param_value.value = []
        mock_param.value = mock_param_value

        single_hole_selections = [{"centroid": [1.0, 2.0, 3.0]}]

        def mock_get_native_body(body):
            if body == selected_body:
                return native_selected_body
            return Mock(faces=[])

        with ExitStack() as stack:
            mock_fusion_paths = self._setup_hole_selection_patches(
                stack, mock_get_native_body, mock_param)
            mock_fusion_paths.get_body = Mock(return_value=selected_body)

            result = try_op_select_hole(
                op=Mock(),
                body_occurrence=parent_occurrence,
                single_hole_selections=single_hole_selections,
                selection_param_name="holeFace",
                selection_param_value_type="CadObjectParameterValue",
                sketch_book=mock_sketch_book,
                setup_body=selected_body,  # The fix: pass correct body explicitly
            )

        # With setup_body, should find the cylinder and return None (success)
        self.assertIsNone(result,
            "With setup_body parameter, should find cylinder on correct body")


if __name__ == '__main__':
    unittest.main()
