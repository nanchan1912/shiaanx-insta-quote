import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
import types

from .mock_adsk import setup_adsk_modules
setup_adsk_modules()

# ---------------------------------------------------------------------------
# Import SetopMaterializer directly from logic.py, bypassing the commands
# package __init__.py (which has circular imports).  We insert empty package
# modules into sys.modules so Python can locate logic.py on disk without
# running the __init__.py files.
# ---------------------------------------------------------------------------

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_code_dir = os.path.dirname(_tests_dir)
_commands_dir = os.path.join(_code_dir, 'commands')
_req_ops_dir = os.path.join(_commands_dir, 'command_RequestFusionOps')

_packages_to_stub = {
    'Toolpath.code.commands': _commands_dir,
    'Toolpath.code.commands.command_RequestFusionOps': _req_ops_dir,
}
_saved_modules = {}
for _name, _path in _packages_to_stub.items():
    _saved_modules[_name] = sys.modules.get(_name)
    if _name not in sys.modules:
        _pkg = types.ModuleType(_name)
        _pkg.__path__ = [_path]
        _pkg.__package__ = _name
        sys.modules[_name] = _pkg

from ..commands.command_RequestFusionOps import logic as _logic_module

# Restore any modules we stubbed (leave them if another import populated them)
for _name, _prev in _saved_modules.items():
    if _prev is not None:
        sys.modules[_name] = _prev

SetopMaterializer = _logic_module.SetopMaterializer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_materializer(**overrides):
    """Create a SetopMaterializer with all-Mock defaults, overridden by kwargs."""
    defaults = dict(
        setops=[{"operations": []}],
        parts=None,
        stock=None,
        workholding=None,
        joints=None,
        progressDialog=Mock(),
        needs_cancel=lambda: False,
        toollibs=[],
        fusion=Mock(),
        config={},
        reuse_existing_setups=False,
        fusion_paths=Mock(),
        support_window_step_content=None,
        support_pedestal_step_content=None,
        support_part_transform=None,
    )
    defaults.update(overrides)
    return SetopMaterializer(**defaults)


# ---------------------------------------------------------------------------
# Tests for _apply_support_model
# ---------------------------------------------------------------------------

class TestApplySupportModel(unittest.TestCase):
    """Tests for SetopMaterializer._apply_support_model()."""

    def _make_op_with_params(self, override_model=True, model_list=None,
                             include_setup_model=True):
        """Build a mock CAM operation with the three parameter mocks.

        Parameters
        ----------
        override_model : bool or None
            If None, itemByName('overrideModel') returns None (param missing).
        model_list : list or None
            Starting contents of model_param.value.value.  Defaults to [].
        include_setup_model : bool
            Whether to include the includeSetupModel parameter.
        """
        if model_list is None:
            model_list = []

        override_param = Mock() if override_model is not None else None
        model_val = Mock()
        model_val.value = list(model_list)
        model_param = Mock()
        model_param.value = model_val
        include_param = Mock() if include_setup_model else None

        def item_by_name(name):
            return {
                'overrideModel': override_param,
                'model': model_param,
                'includeSetupModel': include_param,
            }.get(name)

        op = Mock()
        op.parameters.itemByName = item_by_name
        return op, override_param, model_val, include_param

    def test_happy_path_sets_all_params(self):
        """Should enable overrideModel, append support occ, enable includeSetupModel."""
        support_occ = Mock(name='support_container')
        m = _make_materializer()
        m.support_container_occ = support_occ
        op, override_param, model_val, include_param = self._make_op_with_params()

        m._apply_support_model(op)

        self.assertEqual(override_param.expression, 'true')
        self.assertEqual(include_param.expression, 'true')
        # model_val.value should have been set to a list containing the support occ
        set_value = model_val.value
        self.assertIn(support_occ, set_value)

    def test_preserves_existing_model_entries(self):
        """Existing model entries should not be removed when appending support occ."""
        existing_body = Mock(name='existing_body')
        support_occ = Mock(name='support_container')
        m = _make_materializer()
        m.support_container_occ = support_occ
        op, _, model_val, _ = self._make_op_with_params(model_list=[existing_body])

        m._apply_support_model(op)

        set_value = model_val.value
        self.assertIn(existing_body, set_value)
        self.assertIn(support_occ, set_value)
        self.assertEqual(len(set_value), 2)

    def test_returns_early_when_override_param_missing(self):
        """If overrideModel param is None, should return without touching other params."""
        support_occ = Mock(name='support_container')
        m = _make_materializer()
        m.support_container_occ = support_occ
        op, _, model_val, _ = self._make_op_with_params(override_model=None)

        m._apply_support_model(op)

        # model should not have been modified
        self.assertEqual(model_val.value, [])

    def test_exception_is_caught_and_logged(self):
        """Parameter access failure should be caught and logged, not raised."""
        support_occ = Mock(name='support_container')
        m = _make_materializer()
        m.support_container_occ = support_occ

        op = Mock()
        op.parameters.itemByName.side_effect = RuntimeError("param not found")

        # Patch log where _apply_support_model imports it from
        from ..lib import general_utils
        with patch.object(general_utils, 'log') as mock_log:
            # Should not raise
            m._apply_support_model(op)
            mock_log.assert_called_once()
            self.assertIn("Warning", mock_log.call_args[0][0])


# ---------------------------------------------------------------------------
# Tests for the conditional gating in execute()
# ---------------------------------------------------------------------------

class TestExecuteSupportModelConditional(unittest.TestCase):
    """Tests for the conditional in execute() that triggers _apply_support_model."""

    def _run_execute_with_ops(self, support_occ, ops_json):
        """Run execute() with the given ops, return the _apply_support_model mock.

        If support_occ is not None, import_support_geometry will return it,
        simulating support content being present. If None, no support content
        is provided and import_support_geometry returns None.

        Patches all heavy dependencies so execute() can run with minimal setup.
        """
        mock_setup = Mock()
        mock_occman = Mock()
        mock_occman.import_fixtures.return_value = None
        mock_occman.import_support_geometry.return_value = support_occ

        mock_fusion = Mock()
        mock_fusion.getDesign.return_value = Mock()
        mock_fusion.isParametricDesign.return_value = False
        mock_fusion.activateCAM.return_value = None

        mock_progress = Mock()
        mock_progress.wasCancelled = False

        support_kwargs = {}
        if support_occ is not None:
            support_kwargs['support_window_step_content'] = "WINDOW STEP"

        m = _make_materializer(
            setops=[{"operations": ops_json, "sketch_book": {}}],
            fusion=mock_fusion,
            progressDialog=mock_progress,
            toollibs=[],
            **support_kwargs,
        )

        with patch.object(m, 'make_setup_occman', return_value=(mock_setup, mock_occman)), \
             patch.object(m, '_apply_support_model') as mock_apply, \
             patch.object(m, 'generate_toolpaths_if_needed'), \
             patch.object(_logic_module, 'calc_tool_by_id_dict',
                          return_value={(1,): Mock()}), \
             patch.object(_logic_module, 'SketchBook'), \
             patch.object(_logic_module, 'create_op',
                          return_value=Mock()), \
             patch('adsk.core.ObjectCollection.create', return_value=Mock()), \
             patch('adsk.core.Matrix3D.create', return_value=Mock()):
            m.execute()

        return mock_apply

    def test_adaptive_strategy_with_support_triggers_apply(self):
        """support content present + strategy=='adaptive' should call _apply_support_model."""
        ops = [{"subtypekey": "FusionOp", "ftool_id": [1], "strategy": "adaptive"}]
        mock_apply = self._run_execute_with_ops(
            support_occ=Mock(name='support'),
            ops_json=ops,
        )
        mock_apply.assert_called_once()

    def test_no_support_content_skips_apply(self):
        """No support content should never call _apply_support_model."""
        ops = [{"subtypekey": "FusionOp", "ftool_id": [1], "strategy": "adaptive"}]
        mock_apply = self._run_execute_with_ops(
            support_occ=None,
            ops_json=ops,
        )
        mock_apply.assert_not_called()

    def test_non_adaptive_strategy_skips_apply(self):
        """Non-adaptive strategy should not call _apply_support_model."""
        ops = [{"subtypekey": "FusionOp", "ftool_id": [1], "strategy": "contour"}]
        mock_apply = self._run_execute_with_ops(
            support_occ=Mock(name='support'),
            ops_json=ops,
        )
        mock_apply.assert_not_called()

    def test_missing_strategy_key_skips_apply(self):
        """Op with no 'strategy' key should not call _apply_support_model."""
        ops = [{"subtypekey": "FusionOp", "ftool_id": [1]}]
        mock_apply = self._run_execute_with_ops(
            support_occ=Mock(name='support'),
            ops_json=ops,
        )
        mock_apply.assert_not_called()

    def test_multiple_ops_only_adaptive_gets_apply(self):
        """Only the adaptive op should get _apply_support_model, not the others."""
        ops = [
            {"subtypekey": "FusionOp", "ftool_id": [1], "strategy": "contour"},
            {"subtypekey": "FusionOp", "ftool_id": [1], "strategy": "adaptive"},
            {"subtypekey": "FusionOp", "ftool_id": [1], "strategy": "pocket"},
        ]
        mock_apply = self._run_execute_with_ops(
            support_occ=Mock(name='support'),
            ops_json=ops,
        )
        self.assertEqual(mock_apply.call_count, 1)


if __name__ == '__main__':
    unittest.main()
