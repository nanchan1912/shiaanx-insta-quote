import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
import types

from .mock_adsk import setup_adsk_modules, MockSetup
setup_adsk_modules()

# ---------------------------------------------------------------------------
# Import UserSpecifiedSetip/UserSpecifiedSetips from logic.py, bypassing the
# commands package __init__.py (circular imports).  Same approach as
# test_setop_materializer.py: stub packages with types.ModuleType.
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

for _name, _prev in _saved_modules.items():
    if _prev is not None:
        sys.modules[_name] = _prev

UserSpecifiedSetip = _logic_module.UserSpecifiedSetip
UserSpecifiedSetips = _logic_module.UserSpecifiedSetips
UserException = _logic_module.UserException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_setup(name="Setup1", operation_id=0x1234):
    """Create a MockSetup with the minimum attributes."""
    setup = MockSetup()
    setup.name = name
    setup.operationId = operation_id
    setup.parameters = []
    return setup


def _make_setip(compute_fusionops=True, stock_mode="default", name="Setup1",
                nbodies=1, body_entity_token="token_abc", direction=(0, 0, 1)):
    """Create a mock object that implements the UserSpecifiedSetip interface.

    For tests that exercise UserSpecifiedSetips (the aggregate class), we
    don't need real UserSpecifiedSetip instances — just objects that
    implement the methods the aggregate calls.
    """
    setip = Mock()
    setip.compute_fusionops = compute_fusionops
    setip.obj = Mock()
    setip.obj.name = name

    setip.diagnose.return_value = []
    setip.is_stock_defining.return_value = (stock_mode != "previoussetup")
    setip.get_name.return_value = name
    setip.get_machining_direction_step.return_value = direction

    body = Mock()
    body.nativeObject = Mock()
    body.nativeObject.entityToken = body_entity_token

    setip.nbodies.return_value = nbodies
    setip.get_bodies_and_transforms.return_value = [(body, Mock())] * nbodies

    return setip


def _make_user_specified_setips(setip_list):
    """Build a UserSpecifiedSetips bypassing __init__ so we can test
    diagnose() and get_bodies_defining_setip() in isolation."""
    obj = UserSpecifiedSetips.__new__(UserSpecifiedSetips)
    obj.setips = setip_list
    obj.document_creationId = "test-doc-id"
    return obj


# ---------------------------------------------------------------------------
# P0: UserSpecifiedSetip.diagnose()
# ---------------------------------------------------------------------------

class TestUserSpecifiedSetipDiagnose(unittest.TestCase):
    """Tests for UserSpecifiedSetip.diagnose() — 5 branches."""

    def _make_real_setip(self, compute_fusionops=True, stock_mode="default"):
        """Create a real UserSpecifiedSetip with mocked dependencies."""
        setup = _make_mock_setup()
        mock_param = Mock()
        mock_param.value.value = stock_mode

        with patch.object(_logic_module, 'Fusion'), \
             patch.object(_logic_module, 'FusionFullPath'), \
             patch.object(_logic_module, 'get_parameter', return_value=mock_param):
            setip = UserSpecifiedSetip(setup, compute_fusionops=compute_fusionops)

        # Patch get_parameter for subsequent calls too
        setip._get_param_mock = mock_param
        return setip

    def _run_diagnose(self, setip, stock_mode, experimental=False):
        """Run diagnose() with the given stock mode and config."""
        mock_param = Mock()
        mock_param.value.value = stock_mode
        config = {"experimental": experimental}
        with patch.object(_logic_module, 'get_parameter', return_value=mock_param), \
             patch.object(_logic_module, 'CURRENT_CONFIG', config):
            return setip.diagnose()

    def test_not_computing_returns_empty(self):
        """compute_fusionops=False should return no issues."""
        setip = self._make_real_setip(compute_fusionops=False)
        issues = self._run_diagnose(setip, stock_mode="anything")
        self.assertEqual(issues, [])

    def test_solid_stock_returns_empty(self):
        """Stock mode 'solid' is supported, should return no issues."""
        setip = self._make_real_setip(compute_fusionops=True)
        issues = self._run_diagnose(setip, stock_mode="solid")
        self.assertEqual(issues, [])

    def test_default_stock_returns_empty(self):
        """Stock mode 'default' is supported, should return no issues."""
        setip = self._make_real_setip(compute_fusionops=True)
        for mode in ("previoussetup", "default", "fixedbox"):
            issues = self._run_diagnose(setip, stock_mode=mode)
            self.assertEqual(issues, [], f"stock mode '{mode}' should be supported")

    def test_cylinder_stock_with_experimental(self):
        """Cylinder stock is supported when experimental=True."""
        setip = self._make_real_setip(compute_fusionops=True)
        for mode in ("fixedcylinder", "relativecylinder"):
            issues = self._run_diagnose(setip, stock_mode=mode, experimental=True)
            self.assertEqual(issues, [], f"stock mode '{mode}' should be supported with experimental")

    def test_unsupported_stock_returns_issue(self):
        """An unsupported stock mode should produce an issue."""
        setip = self._make_real_setip(compute_fusionops=True)
        issues = self._run_diagnose(setip, stock_mode="relativebox")
        self.assertEqual(len(issues), 1)
        self.assertIn("unsupported stock", issues[0])

    def test_cylinder_stock_without_experimental_returns_issue(self):
        """Cylinder stock without experimental flag should produce an issue."""
        setip = self._make_real_setip(compute_fusionops=True)
        issues = self._run_diagnose(setip, stock_mode="fixedcylinder", experimental=False)
        self.assertEqual(len(issues), 1)
        self.assertIn("unsupported stock", issues[0])


# ---------------------------------------------------------------------------
# P0: UserSpecifiedSetips.diagnose()
# ---------------------------------------------------------------------------

class TestUserSpecifiedSetipsDiagnose(unittest.TestCase):
    """Tests for UserSpecifiedSetips.diagnose() — 6 branches."""

    def test_clean_single_setup_no_issues(self):
        """One selected, stock-defining setup with 1 body → no issues."""
        s = _make_setip(compute_fusionops=True, stock_mode="default",
                        nbodies=1, direction=(0, 0, 1))
        obj = _make_user_specified_setips([s])
        issues = obj.diagnose()
        self.assertEqual(issues, [])

    def test_zero_selected_setups(self):
        """No setip has compute_fusionops=True → issue."""
        s = _make_setip(compute_fusionops=False)
        obj = _make_user_specified_setips([s])
        issues = obj.diagnose()
        self.assertTrue(any("at least one" in i for i in issues))

    def test_zero_stock_defining_setups(self):
        """All setips use 'previoussetup' stock → issue."""
        s = _make_setip(compute_fusionops=True, stock_mode="previoussetup",
                        nbodies=1)
        obj = _make_user_specified_setips([s])
        issues = obj.diagnose()
        self.assertTrue(any("stock defining" in i for i in issues))

    def test_duplicate_z_axis_detected(self):
        """Two selected setups with same Z-axis → issue."""
        s1 = _make_setip(compute_fusionops=True, name="Setup1",
                         nbodies=1, direction=(0, 0, 1))
        s2 = _make_setip(compute_fusionops=True, name="Setup2",
                         nbodies=1, direction=(0, 0, 1),
                         body_entity_token="token_abc")
        obj = _make_user_specified_setips([s1, s2])
        issues = obj.diagnose()
        self.assertTrue(any("unique Z-axes" in i for i in issues))

    def test_different_z_axes_no_issue(self):
        """Two selected setups with different Z-axes → no duplicate-axis issue."""
        s1 = _make_setip(compute_fusionops=True, name="Setup1",
                         nbodies=1, direction=(0, 0, 1))
        s2 = _make_setip(compute_fusionops=True, name="Setup2",
                         nbodies=1, direction=(0, 1, 0),
                         body_entity_token="token_abc")
        obj = _make_user_specified_setips([s1, s2])
        issues = obj.diagnose()
        self.assertFalse(any("unique Z-axes" in i for i in issues))

    def test_multiple_stock_defining_selected(self):
        """Two selected stock-defining setups → issue."""
        s1 = _make_setip(compute_fusionops=True, name="Setup1",
                         stock_mode="default", nbodies=1,
                         direction=(0, 0, 1))
        s2 = _make_setip(compute_fusionops=True, name="Setup2",
                         stock_mode="solid", nbodies=1,
                         direction=(0, 1, 0),
                         body_entity_token="token_abc")
        obj = _make_user_specified_setips([s1, s2])
        issues = obj.diagnose()
        self.assertTrue(any("stock defining" in i.lower() for i in issues))

    def test_body_count_not_one_produces_issue(self):
        """Setup with 0 bodies → issue about body count."""
        s = _make_setip(compute_fusionops=True, nbodies=0)
        obj = _make_user_specified_setips([s])
        issues = obj.diagnose()
        self.assertTrue(any("bodies" in i.lower() for i in issues))

    def test_child_setip_diagnose_issues_propagated(self):
        """Issues from child setip.diagnose() should be included."""
        s = _make_setip(compute_fusionops=True, nbodies=1)
        s.diagnose.return_value = ["child issue: unsupported stock"]
        obj = _make_user_specified_setips([s])
        issues = obj.diagnose()
        self.assertIn("child issue: unsupported stock", issues)


# ---------------------------------------------------------------------------
# P0: UserSpecifiedSetips.__init__ validation
# ---------------------------------------------------------------------------

class TestUserSpecifiedSetipsInitValidation(unittest.TestCase):
    """Tests for UserSpecifiedSetips.__init__() rejection logic — 4 cases."""

    def _construct(self, setip_list):
        """Call __init__ with Fusion() mocked."""
        mock_doc = Mock()
        mock_doc.creationId = "test-doc-id"
        with patch.object(_logic_module, 'Fusion') as mock_fusion_cls:
            mock_fusion_cls.return_value.getActiveDocument.return_value = mock_doc
            return UserSpecifiedSetips(setip_list)

    def test_multi_body_setup_rejected(self):
        """Setup with >1 body should raise UserException."""
        s = _make_setip(compute_fusionops=True, nbodies=2)
        with self.assertRaises(UserException) as ctx:
            self._construct([s])
        self.assertIn("bodies", ctx.exception.user_msg.lower())

    def test_mismatched_entity_tokens_rejected(self):
        """Two setups with different body entity tokens should raise UserException."""
        s1 = _make_setip(compute_fusionops=True, nbodies=1,
                         body_entity_token="token_A")
        s2 = _make_setip(compute_fusionops=True, nbodies=1,
                         body_entity_token="token_B")
        with self.assertRaises(UserException) as ctx:
            self._construct([s1, s2])
        self.assertIn("same model", ctx.exception.user_msg.lower())

    def test_no_selected_setup_rejected(self):
        """No setip with compute_fusionops=True should raise UserException."""
        s = _make_setip(compute_fusionops=False, nbodies=1)
        with self.assertRaises(UserException) as ctx:
            self._construct([s])
        self.assertIn("select", ctx.exception.user_msg.lower())

    def test_no_body_found_rejected(self):
        """All selected setups with 0 bodies should raise UserException."""
        s = _make_setip(compute_fusionops=True, nbodies=0)
        with self.assertRaises(UserException) as ctx:
            self._construct([s])
        self.assertIn("model", ctx.exception.user_msg.lower())

    def test_valid_construction_succeeds(self):
        """Valid setup list should construct without error."""
        s = _make_setip(compute_fusionops=True, nbodies=1,
                        body_entity_token="token_A")
        obj = self._construct([s])
        self.assertEqual(len(obj.setips), 1)
        self.assertEqual(obj.document_creationId, "test-doc-id")

    def test_matching_entity_tokens_accepted(self):
        """Two setups with same body entity token should be accepted."""
        s1 = _make_setip(compute_fusionops=True, nbodies=1,
                         body_entity_token="token_A")
        s2 = _make_setip(compute_fusionops=True, nbodies=1,
                         body_entity_token="token_A")
        obj = self._construct([s1, s2])
        self.assertEqual(len(obj.setips), 2)


# ---------------------------------------------------------------------------
# P0: get_bodies_defining_setip()
# ---------------------------------------------------------------------------

class TestGetBodiesDefiningSetip(unittest.TestCase):
    """Tests for UserSpecifiedSetips.get_bodies_defining_setip() — 3 fallback cases."""

    def test_first_selected_with_bodies_returned(self):
        """First selected setip with bodies should be returned directly."""
        s1 = _make_setip(compute_fusionops=False, nbodies=1, name="S1")
        s2 = _make_setip(compute_fusionops=True, nbodies=1, name="S2")
        s3 = _make_setip(compute_fusionops=True, nbodies=1, name="S3")
        obj = _make_user_specified_setips([s1, s2, s3])

        result = obj.get_bodies_defining_setip()
        self.assertEqual(result.get_name(), "S2")

    def test_fallback_to_earlier_unselected(self):
        """When first selected has 0 bodies, fall back to earlier unselected with bodies."""
        s1 = _make_setip(compute_fusionops=False, nbodies=1, name="S1_unselected")
        s2 = _make_setip(compute_fusionops=True, nbodies=0, name="S2_selected_no_body")
        obj = _make_user_specified_setips([s1, s2])

        result = obj.get_bodies_defining_setip()
        self.assertEqual(result.get_name(), "S1_unselected")

    def test_no_bodies_anywhere_returns_first_selected(self):
        """When no setip has bodies, returns first selected setip anyway."""
        s1 = _make_setip(compute_fusionops=False, nbodies=0, name="S1")
        s2 = _make_setip(compute_fusionops=True, nbodies=0, name="S2")
        obj = _make_user_specified_setips([s1, s2])

        result = obj.get_bodies_defining_setip()
        self.assertEqual(result.get_name(), "S2")

    def test_multiple_unselected_returns_last_with_body(self):
        """Fallback should pick the last unselected setip before the selected one."""
        s0 = _make_setip(compute_fusionops=False, nbodies=1, name="S0")
        s1 = _make_setip(compute_fusionops=False, nbodies=1, name="S1")
        s2 = _make_setip(compute_fusionops=True, nbodies=0, name="S2_selected")
        obj = _make_user_specified_setips([s0, s1, s2])

        result = obj.get_bodies_defining_setip()
        # Falls back searching from index 1 down to 0, returns first match
        self.assertEqual(result.get_name(), "S1")


if __name__ == '__main__':
    unittest.main()
