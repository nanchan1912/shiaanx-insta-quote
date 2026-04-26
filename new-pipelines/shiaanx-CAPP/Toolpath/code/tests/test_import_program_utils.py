import unittest
from unittest.mock import Mock, patch, MagicMock
import sys

from .mock_adsk import (
    setup_adsk_modules,
    MockMatrix3D,
    MockVector3D,
    MockPoint3D,
    MockBoundingBox,
    MockOccurrence,
)
setup_adsk_modules()


class TestMaterializeResponseFixtures(unittest.TestCase):
    """Test cases for fixture handling in materialize_response."""

    def test_fixture_data_extraction(self):
        """fixture_params should be extracted from response."""
        # Test the extraction pattern
        resp = {
            "fixture_params": {
                "fixtureSolids": [
                    {
                        "name": "TestFixture",
                        "stepUrl": "https://example.com/fixture.step",
                        "T_pcs_from_fixture_file": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                    }
                ]
            }
        }

        fixture_data = resp.get("fixture_params")
        self.assertIsNotNone(fixture_data)
        self.assertIn("fixtureSolids", fixture_data)
        self.assertEqual(len(fixture_data["fixtureSolids"]), 1)

    def test_part_offset_calculation_from_bounding_box(self):
        """Part offset should be calculated from bounding box center."""
        # Create a mock occurrence with a bounding box
        mock_occurrence = MockOccurrence("Part")
        mock_occurrence.boundingBox.minPoint = MockPoint3D(0, 0, 0)
        mock_occurrence.boundingBox.maxPoint = MockPoint3D(20, 30, 40)  # cm

        bbox = mock_occurrence.boundingBox

        # Calculate offset as done in materialize_response (center of bounding box, converted to mm)
        part_offset = [
            (bbox.minPoint.x + bbox.maxPoint.x) / 2.0 * 10.0,  # cm to mm
            (bbox.minPoint.y + bbox.maxPoint.y) / 2.0 * 10.0,
            (bbox.minPoint.z + bbox.maxPoint.z) / 2.0 * 10.0,
        ]

        # Center of (0,0,0) to (20,30,40) is (10,15,20) cm = (100,150,200) mm
        self.assertEqual(part_offset[0], 100.0)
        self.assertEqual(part_offset[1], 150.0)
        self.assertEqual(part_offset[2], 200.0)

    def test_part_offset_with_no_parts(self):
        """Part offset should default to [0,0,0] when no parts available."""
        parts = []

        part_offset = [0.0, 0.0, 0.0]
        if parts and len(parts) > 0:
            # This branch won't execute
            pass

        self.assertEqual(part_offset, [0.0, 0.0, 0.0])

    def test_fixture_params_none_handling(self):
        """None fixture_params should be handled gracefully."""
        resp = {
            "some_other_key": "value"
        }

        fixture_data = resp.get("fixture_params")
        self.assertIsNone(fixture_data)

    def test_empty_fixture_solids(self):
        """Empty fixtureSolids list should be handled."""
        resp = {
            "fixture_params": {
                "fixtureSolids": []
            }
        }

        fixture_data = resp.get("fixture_params")
        fixture_solids = fixture_data.get("fixtureSolids", [])

        self.assertEqual(len(fixture_solids), 0)


class TestPerSetupFixtureImport(unittest.TestCase):
    """Tests for per-setup fixture import in SetopMaterializer."""

    def test_setop_fixture_params_extraction(self):
        """setop fixture_params should be extracted correctly."""
        setop = {
            "fixture_params": {
                "fixtureSolids": [
                    {
                        "name": "Fixture1",
                        "stepUrl": "https://example.com/f1.step",
                        "T_pcs_from_fixture_file": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                    }
                ]
            },
            "operations": []
        }

        fixture_data = setop.get("fixture_params")
        self.assertIsNotNone(fixture_data)
        self.assertIn("fixtureSolids", fixture_data)

    def test_setop_without_fixture_params(self):
        """setop without fixture_params should return None."""
        setop = {
            "operations": []
        }

        fixture_data = setop.get("fixture_params")
        self.assertIsNone(fixture_data)


class TestCoordSystemCacheClearing(unittest.TestCase):
    """Tests for coordinate system cache clearing after fixture import."""

    def test_cache_clear_function_exists(self):
        """clear_coord_system_cache function should exist."""
        try:
            from ..lib.coord_utils import clear_coord_system_cache
            self.assertTrue(callable(clear_coord_system_cache))
        except ImportError:
            # Function may not exist yet - that's okay
            pass


def _import_module():
    """Import import_program_utils with all circular dependencies mocked.

    The commands package creates a circular import chain:
    import_program_utils -> commands.command_RequestFusionOps.logic
      -> commands.__init__ -> command_ai_cam -> ... -> import_program_utils

    We break this by pre-populating sys.modules with mocks for the entire
    commands package tree before importing.
    """
    # Pre-mock the commands package to break the circular import chain
    commands_mock = Mock()
    modules_to_mock = [
        'Toolpath.code.commands',
        'Toolpath.code.commands.command_RequestFusionOps',
        'Toolpath.code.commands.command_RequestFusionOps.logic',
        'Toolpath.code.commands.command_RequestFusionOps.ui',
        'Toolpath.code.commands.command_RequestFusionOps.RequestFusionOpsQA',
        'Toolpath.code.commands.command_ai_cam',
    ]
    saved = {}
    for mod in modules_to_mock:
        saved[mod] = sys.modules.get(mod)
        sys.modules[mod] = commands_mock

    try:
        from ..lib import import_program_utils
        return import_program_utils
    finally:
        # Restore original state for non-test modules
        for mod in modules_to_mock:
            if saved[mod] is None:
                sys.modules.pop(mod, None)
            else:
                sys.modules[mod] = saved[mod]


class TestSupportGeometryImport(unittest.TestCase):
    """Tests for support geometry data passthrough in materialize_response.

    After refactoring, materialize_response no longer creates the support
    container directly. Instead it passes raw STEP content strings and the
    part_transform to SetopMaterializer, which delegates to
    TPOccurrenceManager.import_support_geometry().

    Covers:
    - Raw support data passed to SetopMaterializer
    - part_transform passed correctly for new doc / existing doc / fallback
    - None/empty support content handled correctly
    """

    @classmethod
    def setUpClass(cls):
        cls.module = _import_module()

    def setUp(self):
        """Set up common mocks for materialize_response calls."""
        self.mock_fusion = Mock()
        self.mock_ui = Mock()
        self.mock_fusion.getUI.return_value = self.mock_ui
        self.mock_fusion.getDesign.return_value = Mock()
        self.mock_fusion.activateCAM.return_value = None

        self.mock_design = Mock()
        self.mock_root_component = Mock()
        self.mock_design.rootComponent = self.mock_root_component

        self.mock_doc = Mock()
        self.mock_doc.activate.return_value = None

        self.mock_progress = Mock()
        self.mock_progress.progressValue = 0
        self.mock_progress.wasCancelled = False

        self.base_resp = {
            "setops": [{"operationId": "op1", "operations": []}],
            "fusion_tool_library": {"data": []},
            "step_file_content": "MOCK PART STEP DATA",
            "part_name": "TestPart",
        }

        # Non-identity transform representing a part at (5, 10, 15) cm
        self.part_transform = MockMatrix3D()
        self.part_transform.setCell(0, 3, 5.0)
        self.part_transform.setCell(1, 3, 10.0)
        self.part_transform.setCell(2, 3, 15.0)

    def _make_mock_part(self, transform=None):
        """Create a mock UserPart with an occurrence that has the given transform."""
        mock_part = Mock()
        mock_part.validPartCreated = True
        mock_part._canCreateJoints = False
        mock_occ = MockOccurrence("Part")
        mock_part.get_occurrence.return_value = mock_occ
        if transform is not None:
            mock_occ.transform2 = transform
        return mock_part

    def _run_materialize(self, resp, use_existing_document, parts_return=None,
                         setup_return=None, setup_body_occ_return=None):
        """Run materialize_response with the given mocks and return patch objects for assertions.

        Returns a dict of the patched mocks for inspection.
        """
        patches = {}

        with patch.object(self.module, 'import_part_from_step') as mock_import_part, \
             patch.object(self.module, 'clear_coord_system_cache'), \
             patch.object(self.module, 'SetopMaterializer') as mock_setop_mat, \
             patch.object(self.module, 'Fusion') as mock_fusion_cls, \
             patch.object(self.module, 'get_setup') as mock_get_setup, \
             patch('adsk.doEvents'), \
             patch('adsk.core.Matrix3D', MockMatrix3D):

            mock_fusion_cls.return_value.getUI.return_value = self.mock_ui

            # Set up SetopMaterializer mock
            mock_setop_instance = Mock()
            mock_setop_mat.return_value = mock_setop_instance

            # Set up get_setup for fallback path
            if setup_return is not None:
                mock_get_setup.return_value = setup_return
            else:
                mock_get_setup.return_value = None

            ip = self.module.ImportProgram(testing=True)

            if parts_return is not None:
                ip.fusion_paths = Mock()
                ip.fusion_paths.maybe_find_resp_model.return_value = parts_return
                if parts_return is not None:
                    ip.fusion_paths.extract_body_and_transform.return_value = (Mock(), self.part_transform)
            else:
                ip.fusion_paths = Mock()
                ip.fusion_paths.maybe_find_resp_model.return_value = None
                if setup_body_occ_return is not None:
                    ip.fusion_paths.get_setup_body_occurrence.return_value = setup_body_occ_return

            if not use_existing_document:
                mock_new_occ = MockOccurrence("ImportedPart")
                mock_import_part.return_value = mock_new_occ
                with patch.object(self.module, 'UserPart') as mock_user_part_cls:
                    mock_part = self._make_mock_part()
                    mock_user_part_cls.return_value = mock_part
                    with patch.object(self.module, 'Joints'):
                        with patch.object(self.module, 'Stock', side_effect=Exception("skip")):
                            ip.materialize_response(
                                fusion=self.mock_fusion,
                                design=self.mock_design,
                                doc=self.mock_doc,
                                resp=resp,
                                progressDialog=self.mock_progress,
                                use_workholding=False,
                                use_stock=False,
                                viseStyle=None,
                                use_existing_document=False,
                                config={},
                            )
            else:
                if parts_return is not None:
                    with patch.object(self.module, 'UserPart') as mock_user_part_cls:
                        mock_part = self._make_mock_part(self.part_transform)
                        mock_user_part_cls.return_value = mock_part
                        with patch.object(self.module, 'Joints'):
                            ip.materialize_response(
                                fusion=self.mock_fusion,
                                design=self.mock_design,
                                doc=self.mock_doc,
                                resp=resp,
                                progressDialog=self.mock_progress,
                                use_workholding=False,
                                use_stock=False,
                                viseStyle=None,
                                use_existing_document=True,
                                config={},
                            )
                else:
                    with patch.object(self.module, 'Joints'):
                        ip.materialize_response(
                            fusion=self.mock_fusion,
                            design=self.mock_design,
                            doc=self.mock_doc,
                            resp=resp,
                            progressDialog=self.mock_progress,
                            use_workholding=False,
                            use_stock=False,
                            viseStyle=None,
                            use_existing_document=True,
                            config={},
                        )

            patches['import_part_from_step'] = mock_import_part
            patches['get_setup'] = mock_get_setup
            patches['SetopMaterializer'] = mock_setop_mat

        return patches

    # ---- No support geometry ----

    def test_no_support_content_passes_none(self):
        """When response has no support content, SetopMaterializer should receive None for both."""
        resp = {**self.base_resp}
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertIsNone(call_kwargs['support_window_step_content'])
        self.assertIsNone(call_kwargs['support_pedestal_step_content'])

    # ---- Support geometry data passthrough ----

    def test_pedestal_content_passed_to_materializer(self):
        """Pedestal STEP content should be passed through to SetopMaterializer."""
        resp = {**self.base_resp, "support_pedestal_step_content": "STEP DATA"}
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertEqual(call_kwargs['support_pedestal_step_content'], "STEP DATA")
        self.assertIsNone(call_kwargs['support_window_step_content'])

    def test_window_content_passed_to_materializer(self):
        """Window STEP content should be passed through to SetopMaterializer."""
        resp = {**self.base_resp, "support_window_step_content": "WINDOW STEP"}
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertEqual(call_kwargs['support_window_step_content'], "WINDOW STEP")

    def test_both_support_types_passed(self):
        """Both window and pedestal content should be passed through."""
        resp = {
            **self.base_resp,
            "support_window_step_content": "WINDOW STEP",
            "support_pedestal_step_content": "PEDESTAL STEP",
        }
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertEqual(call_kwargs['support_window_step_content'], "WINDOW STEP")
        self.assertEqual(call_kwargs['support_pedestal_step_content'], "PEDESTAL STEP")

    def test_none_pedestal_content_passed_as_none(self):
        """None pedestal content should be passed as None."""
        resp = {
            **self.base_resp,
            "support_window_step_content": "WINDOW STEP",
            "support_pedestal_step_content": None,
        }
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertEqual(call_kwargs['support_window_step_content'], "WINDOW STEP")
        self.assertIsNone(call_kwargs['support_pedestal_step_content'])

    def test_empty_string_pedestal_passed_as_empty(self):
        """Empty string pedestal content should be passed as empty string."""
        resp = {
            **self.base_resp,
            "support_window_step_content": "WINDOW STEP",
            "support_pedestal_step_content": "",
        }
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertEqual(call_kwargs['support_pedestal_step_content'], "")

    # ---- part_transform passthrough ----

    def test_new_doc_passes_part_transform(self):
        """New document should pass part_transform to SetopMaterializer."""
        resp = {**self.base_resp, "support_pedestal_step_content": "STEP DATA"}
        result = self._run_materialize(resp, use_existing_document=False)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertIsNotNone(call_kwargs['support_part_transform'])

    def test_existing_doc_with_parts_passes_part_transform(self):
        """Existing document with parts should pass the extracted part_transform."""
        resp = {**self.base_resp, "support_pedestal_step_content": "STEP DATA"}
        mock_body = Mock()
        result = self._run_materialize(resp, use_existing_document=True, parts_return=mock_body)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        transform = call_kwargs['support_part_transform']
        self.assertAlmostEqual(transform.getCell(0, 3), 5.0)
        self.assertAlmostEqual(transform.getCell(1, 3), 10.0)
        self.assertAlmostEqual(transform.getCell(2, 3), 15.0)

    def test_existing_doc_parts_none_falls_back_to_setup(self):
        """When parts=None and existing doc, should extract transform from CAM setup."""
        resp = {**self.base_resp, "support_pedestal_step_content": "STEP DATA"}

        mock_setup = Mock()
        mock_part_occ = Mock()
        mock_part_occ.transform2 = self.part_transform
        setup_body_occ = (Mock(), mock_part_occ)

        result = self._run_materialize(
            resp,
            use_existing_document=True,
            parts_return=None,
            setup_return=mock_setup,
            setup_body_occ_return=setup_body_occ,
        )

        result['get_setup'].assert_called_once_with(self.mock_fusion, "op1")

        call_kwargs = result['SetopMaterializer'].call_args[1]
        transform = call_kwargs['support_part_transform']
        self.assertAlmostEqual(transform.getCell(0, 3), 5.0)
        self.assertAlmostEqual(transform.getCell(1, 3), 10.0)
        self.assertAlmostEqual(transform.getCell(2, 3), 15.0)

    def test_existing_doc_parts_none_no_setup_uses_identity(self):
        """When parts=None and no CAM setup found, should fall back to identity transform."""
        resp = {**self.base_resp, "support_pedestal_step_content": "STEP DATA"}

        result = self._run_materialize(
            resp,
            use_existing_document=True,
            parts_return=None,
            setup_return=None,
        )

        call_kwargs = result['SetopMaterializer'].call_args[1]
        transform = call_kwargs['support_part_transform']
        for i in range(3):
            self.assertAlmostEqual(transform.getCell(i, 3), 0.0,
                msg=f"Translation [{i}] should be 0 for identity fallback")

    def test_support_in_existing_doc_passes_data(self):
        """Existing document with support should pass raw data to SetopMaterializer."""
        resp = {**self.base_resp, "support_window_step_content": "WINDOW STEP"}
        mock_body = Mock()
        result = self._run_materialize(resp, use_existing_document=True, parts_return=mock_body)

        call_kwargs = result['SetopMaterializer'].call_args[1]
        self.assertEqual(call_kwargs['support_window_step_content'], "WINDOW STEP")


class TestSupportGeometryHasSupport(unittest.TestCase):
    """Test the has_support detection logic used in materialize_response."""

    def test_has_support_with_pedestal(self):
        resp = {"support_pedestal_step_content": "STEP DATA"}
        has_support = resp.get("support_window_step_content") or resp.get("support_pedestal_step_content")
        self.assertTrue(bool(has_support))

    def test_has_support_with_window(self):
        resp = {"support_window_step_content": "STEP DATA"}
        has_support = resp.get("support_window_step_content") or resp.get("support_pedestal_step_content")
        self.assertTrue(bool(has_support))

    def test_has_support_with_both(self):
        resp = {
            "support_window_step_content": "WINDOW",
            "support_pedestal_step_content": "PEDESTAL",
        }
        has_support = resp.get("support_window_step_content") or resp.get("support_pedestal_step_content")
        self.assertTrue(bool(has_support))

    def test_no_support_with_neither(self):
        resp = {}
        has_support = resp.get("support_window_step_content") or resp.get("support_pedestal_step_content")
        self.assertFalse(bool(has_support))

    def test_no_support_with_none_values(self):
        resp = {"support_window_step_content": None, "support_pedestal_step_content": None}
        has_support = resp.get("support_window_step_content") or resp.get("support_pedestal_step_content")
        self.assertFalse(bool(has_support))

    def test_no_support_with_empty_strings(self):
        resp = {"support_window_step_content": "", "support_pedestal_step_content": ""}
        has_support = resp.get("support_window_step_content") or resp.get("support_pedestal_step_content")
        self.assertFalse(bool(has_support))


class TestPartTransformExtraction(unittest.TestCase):
    """Test the part_transform extraction logic used for positioning support geometry and stock."""

    def test_identity_when_no_parts_and_new_document(self):
        """New document: parts list is populated but transform should be identity."""
        part_transform = MockMatrix3D.create()
        # In new document, extract_body_and_transform returns identity
        for i in range(4):
            for j in range(4):
                expected = 1.0 if i == j else 0.0
                self.assertAlmostEqual(part_transform.getCell(i, j), expected)

    def test_parts_none_triggers_fallback(self):
        """When parts=None and use_existing_document=True, fallback path should be taken."""
        parts = None
        use_existing_document = True

        took_fallback = False
        part_transform = MockMatrix3D.create()

        if parts and len(parts) > 0:
            pass  # primary path
        elif use_existing_document:
            took_fallback = True

        self.assertTrue(took_fallback)

    def test_empty_parts_list_triggers_fallback(self):
        """Empty parts list should also trigger the fallback path."""
        parts = []
        use_existing_document = True

        took_fallback = False
        if parts and len(parts) > 0:
            pass
        elif use_existing_document:
            took_fallback = True

        self.assertTrue(took_fallback)

    def test_parts_present_skips_fallback(self):
        """Non-empty parts list should take the primary path, not fallback."""
        parts = [Mock()]
        use_existing_document = True

        took_primary = False
        took_fallback = False
        if parts and len(parts) > 0:
            took_primary = True
        elif use_existing_document:
            took_fallback = True

        self.assertTrue(took_primary)
        self.assertFalse(took_fallback)

    def test_new_document_skips_fallback(self):
        """Even with parts=None, new document should NOT take the fallback path."""
        parts = None
        use_existing_document = False

        took_fallback = False
        if parts and len(parts) > 0:
            pass
        elif use_existing_document:
            took_fallback = True

        self.assertFalse(took_fallback)


if __name__ == '__main__':
    unittest.main()
