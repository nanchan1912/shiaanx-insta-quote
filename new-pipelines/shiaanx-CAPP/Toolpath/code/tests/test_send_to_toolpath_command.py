import unittest
from unittest.mock import Mock, patch
import sys
import os
import types

from .mock_adsk import setup_adsk_modules, MockSetup
_, mock_adsk_core, _, _ = setup_adsk_modules()

# ---------------------------------------------------------------------------
# Import command_send_to_toolpath.py, bypassing the commands package
# __init__.py (circular imports).  Same types.ModuleType stub pattern.
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

from ..commands import command_send_to_toolpath as _cmd_module

for _name, _prev in _saved_modules.items():
    if _prev is not None:
        sys.modules[_name] = _prev

SendToToolpath = _cmd_module.SendToToolpath


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stt(**overrides):
    """Create a SendToToolpath instance bypassing __init__.

    Sets the minimal attributes that the tested methods need.
    """
    obj = SendToToolpath.__new__(SendToToolpath)
    obj.testing = True
    obj.idx_auto_setups = 0
    obj.idx_use_existing_setups = 1
    obj.setup_dropdown_name = "setup_mode"
    obj.multiaxis_dropdowns = []
    obj.support_geometry_dropdown_name = "support_geometry_type"
    for k, v in overrides.items():
        setattr(obj, k, v)
    return obj


def _mock_selector(index):
    """Create a mock dropdown whose selectedItem.index == index."""
    sel = Mock()
    sel.selectedItem = Mock()
    sel.selectedItem.index = index
    return sel


# ---------------------------------------------------------------------------
# P1: want_AutoSetips / want_UserSpecifiedSetips
# ---------------------------------------------------------------------------

class TestWantAutoSetips(unittest.TestCase):
    """Tests for want_AutoSetips / want_UserSpecifiedSetips — index routing."""

    def test_index_0_is_auto(self):
        stt = _make_stt()
        inputs = Mock()
        inputs.itemById.return_value = _mock_selector(0)

        self.assertTrue(stt.want_AutoSetips(inputs))
        self.assertFalse(stt.want_UserSpecifiedSetips(inputs))

    def test_index_1_is_user_specified(self):
        stt = _make_stt()
        inputs = Mock()
        inputs.itemById.return_value = _mock_selector(1)

        self.assertFalse(stt.want_AutoSetips(inputs))
        self.assertTrue(stt.want_UserSpecifiedSetips(inputs))


# ---------------------------------------------------------------------------
# P1: get_setup_picker_initialization_data
# ---------------------------------------------------------------------------

class TestGetSetupPickerInitData(unittest.TestCase):
    """Tests for SendToToolpath.get_setup_picker_initialization_data."""

    def _make_setup(self, name="Setup1", operation_id=0x1234):
        setup = MockSetup()
        setup.name = name
        setup.operationId = operation_id
        return setup

    def test_single_body_selected_by_default(self):
        """Setup with exactly 1 body should be selected (initialValue=True)."""
        stt = _make_stt()
        setup = self._make_setup(name="Op1")
        body = Mock(name="Body1")
        fusion_paths = Mock()
        fusion_paths.get_bodies.return_value = [body]

        init_val, selector_id, _, name, returned_body = \
            stt.get_setup_picker_initialization_data(setup, fusion_paths)

        self.assertTrue(init_val)
        self.assertEqual(name, "Op1")
        self.assertIs(returned_body, body)
        self.assertIn("setup_selector_", selector_id)

    def test_zero_bodies_not_selected(self):
        """Setup with 0 bodies should not be selected."""
        stt = _make_stt()
        setup = self._make_setup(name="Empty")
        fusion_paths = Mock()
        fusion_paths.get_bodies.return_value = []

        init_val, _, _, name, returned_body = \
            stt.get_setup_picker_initialization_data(setup, fusion_paths)

        self.assertFalse(init_val)
        self.assertEqual(name, "Empty")
        self.assertIsNone(returned_body)

    def test_multiple_bodies_not_supported(self):
        """Setup with >1 body should not be selected and name shows warning."""
        stt = _make_stt()
        setup = self._make_setup(name="MultiBod")
        fusion_paths = Mock()
        fusion_paths.get_bodies.return_value = [Mock(), Mock(), Mock()]

        init_val, _, _, name, returned_body = \
            stt.get_setup_picker_initialization_data(setup, fusion_paths)

        self.assertFalse(init_val)
        self.assertIn("Not supported", name)
        self.assertIn("3", name)
        self.assertIsNone(returned_body)

    def test_probe_name_deselected(self):
        """Setup with 'probe' in name should not be selected even with 1 body."""
        stt = _make_stt()
        setup = self._make_setup(name="Probing Setup")
        fusion_paths = Mock()
        fusion_paths.get_bodies.return_value = [Mock()]

        init_val, _, _, name, returned_body = \
            stt.get_setup_picker_initialization_data(setup, fusion_paths)

        self.assertFalse(init_val)
        self.assertEqual(name, "Probing Setup")
        # Body is still returned since nbodies == 1
        self.assertIsNotNone(returned_body)

    def test_probing_keyword_case_insensitive(self):
        """'PROBING' (upper-case) should also trigger deselection."""
        stt = _make_stt()
        setup = self._make_setup(name="PROBING CHECK")
        fusion_paths = Mock()
        fusion_paths.get_bodies.return_value = [Mock()]

        init_val, _, _, _, _ = \
            stt.get_setup_picker_initialization_data(setup, fusion_paths)

        self.assertFalse(init_val)


# ---------------------------------------------------------------------------
# P1: get_support_geometry_mode
# ---------------------------------------------------------------------------

class TestGetSupportGeometryMode(unittest.TestCase):
    """Tests for SendToToolpath.get_support_geometry_mode."""

    def setUp(self):
        # Make adsk.core.DropDownCommandInput.cast act as identity
        mock_adsk_core.DropDownCommandInput.cast = lambda x: x

    def _make_dropdown(self, selected_name):
        dd = Mock()
        dd.selectedItem = Mock()
        dd.selectedItem.name = selected_name
        return dd

    def test_no_multiaxis_dropdowns_returns_none(self):
        stt = _make_stt(multiaxis_dropdowns=[])
        result = stt.get_support_geometry_mode(Mock())
        self.assertIsNone(result)

    def test_three_axis_returns_none(self):
        stt = _make_stt(multiaxis_dropdowns=[self._make_dropdown("ThreeAxis")])
        result = stt.get_support_geometry_mode(Mock())
        self.assertIsNone(result)

    def test_three_plus_two_pedestal(self):
        ma_dd = self._make_dropdown("ThreePlusTwoAxis")
        support_dd = self._make_dropdown("Pedestal")
        inputs = Mock()
        inputs.itemById.return_value = support_dd

        stt = _make_stt(multiaxis_dropdowns=[ma_dd])
        result = stt.get_support_geometry_mode(inputs)
        self.assertEqual(result, "PEDESTAL")

    def test_three_plus_two_window(self):
        ma_dd = self._make_dropdown("ThreePlusTwoAxis")
        support_dd = self._make_dropdown("Window")
        inputs = Mock()
        inputs.itemById.return_value = support_dd

        stt = _make_stt(multiaxis_dropdowns=[ma_dd])
        result = stt.get_support_geometry_mode(inputs)
        self.assertEqual(result, "WINDOW")

    def test_three_plus_two_none_selected(self):
        ma_dd = self._make_dropdown("ThreePlusTwoAxis")
        support_dd = self._make_dropdown("None")
        inputs = Mock()
        inputs.itemById.return_value = support_dd

        stt = _make_stt(multiaxis_dropdowns=[ma_dd])
        result = stt.get_support_geometry_mode(inputs)
        self.assertIsNone(result)

    def test_no_support_dropdown_returns_none(self):
        """When inputs.itemById returns None for the support dropdown."""
        ma_dd = self._make_dropdown("ThreePlusTwoAxis")
        inputs = Mock()
        inputs.itemById.return_value = None

        stt = _make_stt(multiaxis_dropdowns=[ma_dd])
        result = stt.get_support_geometry_mode(inputs)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
