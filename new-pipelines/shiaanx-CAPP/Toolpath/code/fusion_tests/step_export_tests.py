"""
Tests for STEP file export functionality.

These tests verify the four-tier export approach in fusion_utils._save_step_file_body_tmp_component:
1. copyToComponent (fastest, works for parametric bodies)
2. TemporaryBRepManager + base feature (fallback when copyToComponent fails)
3. Parent component fallback (when parent has exactly 1 body)
4. Native body + manual transform (last resort for proxy bodies)

Key test classes:
- TestStepExportProxyBody: Tests Tier 1 (copyToComponent) with proxy bodies
- TestStepExportDirectModeling: Tests Tier 2 in Direct Modeling mode

NOTE: The Tier 2 WCS coordinate fix (inverse transform for proxy bodies) is tested
in send_to_toolpath_tests.py using TestTier2WCSCoordinateFix, because programmatically-
created proxy bodies always succeed via Tier 1, even in Direct Modeling mode. The test
file "parametric_stock_in_setup_test" triggers the actual Tier 2 code path.

Tests create geometry programmatically to avoid dependencies on external files.
"""
import unittest
import adsk.core
import adsk.fusion
import tempfile
import os

from ..lib.fusion_utils import Fusion, get_step_file_content, add_component
from ..lib.general_utils import extract_step_body_names, extract_step_product_names


def create_box_body(component: adsk.fusion.Component, name: str,
                    corner=(0, 0, 0), size=(1, 1, 1)) -> adsk.fusion.BRepBody:
    """
    Create a box body in the given component using TemporaryBRepManager.

    Args:
        component: The component to add the body to
        name: Name for the body
        corner: (x, y, z) corner position in cm
        size: (length, width, height) in cm

    Returns:
        The created BRepBody
    """
    cx, cy, cz = corner
    lx, ly, lz = size

    centerPoint = adsk.core.Point3D.create(cx + 0.5*lx, cy + 0.5*ly, cz + 0.5*lz)
    lengthDirection = adsk.core.Vector3D.create(1, 0, 0)
    widthDirection = adsk.core.Vector3D.create(0, 1, 0)

    obox = adsk.core.OrientedBoundingBox3D.create(
        centerPoint=centerPoint,
        lengthDirection=lengthDirection,
        widthDirection=widthDirection,
        length=lx, width=ly, height=lz
    )

    tmpManager = adsk.fusion.TemporaryBRepManager.get()
    brepbox = tmpManager.createBox(obox)

    fusion = Fusion()
    if fusion.isParametricDesign():
        base = component.features.baseFeatures.add()
        base.startEdit()
        try:
            body = component.bRepBodies.add(brepbox, base)
            body.name = name
        finally:
            base.finishEdit()
    else:
        body = component.bRepBodies.add(brepbox)
        body.name = name

    return body


def create_parametric_box_body(component: adsk.fusion.Component, name: str,
                                corner=(0, 0, 0), size=(1, 1, 1)) -> adsk.fusion.BRepBody:
    """
    Create a box body using sketch + extrude (parametric approach).

    Args:
        component: The component to add the body to
        name: Name for the body
        corner: (x, y, z) corner position in cm
        size: (length, width, height) in cm

    Returns:
        The created BRepBody
    """
    cx, cy, cz = corner
    lx, ly, lz = size

    # Create a sketch on XY plane
    sketches = component.sketches
    xyPlane = component.xYConstructionPlane
    sketch = sketches.add(xyPlane)

    # Draw rectangle
    lines = sketch.sketchCurves.sketchLines
    point1 = adsk.core.Point3D.create(cx, cy, 0)
    point2 = adsk.core.Point3D.create(cx + lx, cy, 0)
    point3 = adsk.core.Point3D.create(cx + lx, cy + ly, 0)
    point4 = adsk.core.Point3D.create(cx, cy + ly, 0)
    lines.addByTwoPoints(point1, point2)
    lines.addByTwoPoints(point2, point3)
    lines.addByTwoPoints(point3, point4)
    lines.addByTwoPoints(point4, point1)

    # Extrude
    extrudes = component.features.extrudeFeatures
    extrudeInput = extrudes.createInput(
        sketch.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    )

    # Set start offset if cz != 0
    if cz != 0:
        startOffset = adsk.fusion.OffsetStartDefinition.create(
            adsk.core.ValueInput.createByReal(cz)
        )
        extrudeInput.startExtent = startOffset

    extrudeInput.setDistanceExtent(False, adsk.core.ValueInput.createByReal(lz))
    extrude = extrudes.add(extrudeInput)

    body = extrude.bodies.item(0)
    body.name = name

    return body


def create_test_document(name="StepExportTest"):
    """Create a new Fusion document for testing."""
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    doc.name = name
    return doc


def get_step_geometry_info(step_content: str) -> dict:
    """
    Parse STEP file content to extract geometry information.

    Returns dict with:
        - has_closed_shell: True if contains CLOSED_SHELL (solid geometry)
        - body_names: List of body names found in PRODUCT (component names)
        - size: Length of content in bytes
        - solid_body_count: Number of solid body entries (MANIFOLD_SOLID_BREP or BREP_WITH_VOIDS)
        - solid_body_names: List of names from solid body entries

    Note: Parasolid reads entity names from STEP PRODUCT names (component names),
    not from solid body entity names. The temp component is named with the body's
    original name during export.
    """
    # Extract names using shared utilities
    product_names = extract_step_product_names(step_content)
    solid_body_names = extract_step_body_names(step_content)

    return {
        "has_closed_shell": "CLOSED_SHELL" in step_content,
        "body_names": product_names,
        "size": len(step_content),
        "solid_body_count": len(solid_body_names),
        "solid_body_names": solid_body_names,
    }


def get_cartesian_point_bounds(step_content: str) -> dict:
    """
    Parse STEP file content to extract coordinate bounds from CARTESIAN_POINT entries.

    Returns dict with:
        - min_x, max_x: X coordinate bounds
        - min_y, max_y: Y coordinate bounds
        - min_z, max_z: Z coordinate bounds
        - point_count: Number of points found

    Coordinates are in centimeters (Fusion's internal unit).
    """
    import re

    # CARTESIAN_POINT format: #123=CARTESIAN_POINT('',(1.0,2.0,3.0));
    # The coordinates are in parentheses after the name
    pattern = r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([^)]+)\s*\)\s*\)"
    matches = re.findall(pattern, step_content)

    if not matches:
        return {
            "min_x": None, "max_x": None,
            "min_y": None, "max_y": None,
            "min_z": None, "max_z": None,
            "point_count": 0,
        }

    xs, ys, zs = [], [], []
    for match in matches:
        coords = [float(c.strip()) for c in match.split(",")]
        if len(coords) >= 3:
            xs.append(coords[0])
            ys.append(coords[1])
            zs.append(coords[2])

    return {
        "min_x": min(xs) if xs else None,
        "max_x": max(xs) if xs else None,
        "min_y": min(ys) if ys else None,
        "max_y": max(ys) if ys else None,
        "min_z": min(zs) if zs else None,
        "max_z": max(zs) if zs else None,
        "point_count": len(xs),
    }


class TestStepExportBasic(unittest.TestCase):
    """Test basic STEP export from a simple body in root component."""

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportBasicTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Create a simple box in root component
        cls.body = create_box_body(
            cls.design.rootComponent,
            name="TestBox",
            corner=(0, 0, 0),
            size=(2, 3, 4)
        )

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_step_export_produces_content(self):
        """Verify STEP export produces non-empty content."""
        step_content, _ = get_step_file_content(self.fusion, self.body)

        self.assertGreater(len(step_content), 5000,
            "STEP content should be at least 5000 bytes for a simple box")

    def test_step_export_contains_geometry(self):
        """Verify STEP export contains actual solid geometry."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertTrue(info["has_closed_shell"],
            "STEP file should contain CLOSED_SHELL for solid geometry")

    def test_step_export_preserves_body_name(self):
        """Verify body name is preserved in STEP export."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertIn("TestBox", info["body_names"],
            "Body name 'TestBox' should be preserved in STEP file")


class TestStepExportProxyBody(unittest.TestCase):
    """
    Test STEP export of proxy body with assembly transformation (Tier 1).

    This tests that when a body is accessed through an occurrence (proxy),
    the export succeeds via Tier 1 (copyToComponent). Tier 1 copies the body
    at world coordinates (with assembly transform baked in).

    Note: This class tests Tier 1 behavior. For Tier 2 proxy body tests with
    local coordinate verification, see TestStepExportTier2ProxyBody.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportProxyTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Create a subcomponent with transformation
        transform = adsk.core.Matrix3D.create()
        transform.translation = adsk.core.Vector3D.create(10, 20, 30)  # Offset the component

        cls.occurrence = cls.design.rootComponent.occurrences.addNewComponent(transform)
        cls.occurrence.component.name = "TransformedComponent"

        # Create body in the subcomponent (at local origin)
        cls.native_body = create_box_body(
            cls.occurrence.component,
            name="TransformedBox",
            corner=(0, 0, 0),
            size=(2, 2, 2)
        )

        # Get the proxy body (which has the transformation applied)
        cls.proxy_body = cls.occurrence.bRepBodies.item(0)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_proxy_body_setup(self):
        """Verify we have a proper proxy body setup for testing."""
        self.assertIsNotNone(self.proxy_body)
        self.assertIsNotNone(self.native_body)

        # Proxy body should have a nativeObject (confirming it's a proxy)
        self.assertIsNotNone(self.proxy_body.nativeObject,
            "Proxy body should have a nativeObject (confirming it's accessed through occurrence)")

        # Both should have the same name (same underlying geometry)
        self.assertEqual(self.proxy_body.name, self.native_body.name,
            "Proxy and native body should have the same name")

    def test_proxy_body_export_produces_content(self):
        """Verify proxy body STEP export produces non-empty content."""
        step_content, _ = get_step_file_content(self.fusion, self.proxy_body)

        self.assertGreater(len(step_content), 5000,
            "STEP content should be at least 5000 bytes")

    def test_proxy_body_export_contains_geometry(self):
        """
        Verify that exporting a proxy body via Tier 1 produces valid solid geometry.

        Tier 1 (copyToComponent) copies geometry at world coordinates.
        """
        step_content, _ = get_step_file_content(self.fusion, self.proxy_body)

        self.assertIn("CARTESIAN_POINT", step_content,
            "STEP file should contain CARTESIAN_POINT definitions")

        # Verify it contains geometry (solid body was exported)
        info = get_step_geometry_info(step_content)
        self.assertTrue(info["has_closed_shell"],
            "STEP file should contain CLOSED_SHELL for solid geometry")

    def test_proxy_body_export_preserves_name(self):
        """Verify body name is preserved when exporting proxy body."""
        step_content, _ = get_step_file_content(self.fusion, self.proxy_body)
        info = get_step_geometry_info(step_content)

        self.assertIn("TransformedBox", info["body_names"],
            "Body name should be preserved in STEP file")


class TestStepExportDirectModeling(unittest.TestCase):
    """
    Test STEP export in Direct Modeling mode.

    In Direct Modeling, copyToComponent may fail due to lack of parametric history.
    This tests that the TemporaryBRepManager fallback works correctly.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportDirectModelingTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Switch to Direct Modeling mode
        cls.design.designType = adsk.fusion.DesignTypes.DirectDesignType

        # Create a body using TemporaryBRepManager (typical for direct modeling)
        cls.body = create_box_body(
            cls.design.rootComponent,
            name="DirectModelingBox",
            corner=(0, 0, 0),
            size=(3, 4, 5)
        )

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_direct_modeling_mode_active(self):
        """Verify we're in Direct Modeling mode."""
        self.assertEqual(
            self.design.designType,
            adsk.fusion.DesignTypes.DirectDesignType,
            "Design should be in Direct Modeling mode"
        )

    def test_direct_modeling_export_produces_content(self):
        """Verify STEP export works in Direct Modeling mode."""
        step_content, _ = get_step_file_content(self.fusion, self.body)

        self.assertGreater(len(step_content), 5000,
            "STEP content should be at least 5000 bytes in Direct Modeling mode")

    def test_direct_modeling_export_contains_geometry(self):
        """Verify exported STEP contains solid geometry."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertTrue(info["has_closed_shell"],
            "STEP file should contain CLOSED_SHELL")

    def test_direct_modeling_export_preserves_name(self):
        """Verify body name is preserved in Direct Modeling export."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertIn("DirectModelingBox", info["body_names"],
            "Body name should be preserved")


# NOTE: TestStepExportTier2ProxyBody was removed because programmatically-created
# proxy bodies always succeed via Tier 1 (copyToComponent), even in Direct Modeling mode.
# The Tier 2 WCS coordinate fix is tested in send_to_toolpath_tests.py using
# TestTier2WCSCoordinateFix with the "parametric_stock_in_setup_test" file,
# which triggers Tier 2 for proxy bodies with non-identity transforms.


class TestStepExportSubcomponentBody(unittest.TestCase):
    """
    Test STEP export of a body in a subcomponent (not through occurrence proxy).

    This tests the parent component fallback when the body is accessed directly
    from a component rather than through an occurrence.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportSubcomponentTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Create a subcomponent
        cls.occurrence = add_component(cls.design.rootComponent, name="SubComponent")

        # Create body in subcomponent
        cls.body = create_box_body(
            cls.occurrence.component,
            name="SubcomponentBox",
            corner=(0, 0, 0),
            size=(1.5, 2.5, 3.5)
        )

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_subcomponent_body_export_produces_content(self):
        """Verify STEP export from subcomponent body works."""
        step_content, _ = get_step_file_content(self.fusion, self.body)

        self.assertGreater(len(step_content), 5000,
            "STEP content should be at least 5000 bytes")

    def test_subcomponent_body_export_contains_geometry(self):
        """Verify exported STEP contains solid geometry."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertTrue(info["has_closed_shell"],
            "STEP file should contain CLOSED_SHELL")


class TestStepExportParametricBody(unittest.TestCase):
    """
    Test STEP export of a body created via parametric features (sketch + extrude).

    This tests the standard parametric workflow where copyToComponent should work.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportParametricTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Ensure we're in parametric mode
        cls.design.designType = adsk.fusion.DesignTypes.ParametricDesignType

        # Create body using parametric features
        cls.body = create_parametric_box_body(
            cls.design.rootComponent,
            name="ParametricBox",
            corner=(0, 0, 0),
            size=(2, 3, 4)
        )

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_parametric_mode_active(self):
        """Verify we're in Parametric mode."""
        self.assertEqual(
            self.design.designType,
            adsk.fusion.DesignTypes.ParametricDesignType,
            "Design should be in Parametric mode"
        )

    def test_parametric_body_export_produces_content(self):
        """Verify STEP export works for parametric body."""
        step_content, _ = get_step_file_content(self.fusion, self.body)

        self.assertGreater(len(step_content), 5000,
            "STEP content should be at least 5000 bytes")

    def test_parametric_body_export_contains_geometry(self):
        """Verify exported STEP contains solid geometry."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertTrue(info["has_closed_shell"],
            "STEP file should contain CLOSED_SHELL")

    def test_parametric_body_export_preserves_name(self):
        """Verify body name is preserved."""
        step_content, _ = get_step_file_content(self.fusion, self.body)
        info = get_step_geometry_info(step_content)

        self.assertIn("ParametricBox", info["body_names"],
            "Body name should be preserved")


class TestStepExportHiddenOccurrence(unittest.TestCase):
    """
    Test STEP export of a body from a hidden occurrence.

    This specifically tests the bug fix where bodies accessed through hidden
    occurrences were producing empty STEP files.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportHiddenOccurrenceTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Create a subcomponent
        cls.occurrence = add_component(cls.design.rootComponent, name="HiddenComponent")

        # Create body in subcomponent
        cls.native_body = create_box_body(
            cls.occurrence.component,
            name="HiddenBox",
            corner=(0, 0, 0),
            size=(2, 2, 2)
        )

        # Hide the occurrence
        cls.occurrence.isLightBulbOn = False

        # Get the proxy body (even though occurrence is hidden)
        cls.proxy_body = cls.occurrence.bRepBodies.item(0)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_occurrence_is_hidden(self):
        """Verify the occurrence is actually hidden."""
        self.assertFalse(self.occurrence.isLightBulbOn,
            "Occurrence should be hidden (light bulb off)")

    def test_hidden_occurrence_body_export_produces_content(self):
        """
        Verify STEP export works for body from hidden occurrence.

        This is the key test for the hidden occurrence bug fix.
        """
        step_content, _ = get_step_file_content(self.fusion, self.proxy_body)

        self.assertGreater(len(step_content), 5000,
            "STEP content from hidden occurrence should be at least 5000 bytes. "
            "If this fails, the hidden occurrence export bug may have regressed.")

    def test_hidden_occurrence_body_export_contains_geometry(self):
        """Verify exported STEP contains solid geometry."""
        step_content, _ = get_step_file_content(self.fusion, self.proxy_body)
        info = get_step_geometry_info(step_content)

        self.assertTrue(info["has_closed_shell"],
            "STEP file should contain CLOSED_SHELL")


class TestStepExportMultipleBodiesComponent(unittest.TestCase):
    """
    Test STEP export when component has multiple bodies.

    This tests that only the target body is exported, not all bodies in the component.
    Uses parametric bodies (sketch+extrude) which work better with copyToComponent.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportMultipleBodiesTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Ensure parametric mode for best compatibility with copyToComponent
        cls.design.designType = adsk.fusion.DesignTypes.ParametricDesignType

        # Create multiple bodies using parametric features (sketch+extrude)
        # This works better with copyToComponent than TemporaryBRepManager bodies
        cls.body1 = create_parametric_box_body(
            cls.design.rootComponent,
            name="Box1",
            corner=(0, 0, 0),
            size=(1, 1, 1)
        )

        cls.body2 = create_parametric_box_body(
            cls.design.rootComponent,
            name="Box2",
            corner=(5, 0, 0),
            size=(2, 2, 2)
        )

        cls.body3 = create_parametric_box_body(
            cls.design.rootComponent,
            name="Box3",
            corner=(10, 0, 0),
            size=(3, 3, 3)
        )

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_multiple_bodies_exist(self):
        """Verify we have multiple bodies in the component."""
        body_count = self.design.rootComponent.bRepBodies.count
        self.assertEqual(body_count, 3, "Should have 3 bodies in component")

    def test_export_single_body_only(self):
        """Verify only the target body is exported, not all bodies."""
        step_content, _ = get_step_file_content(self.fusion, self.body2)
        info = get_step_geometry_info(step_content)

        # Should only contain Box2, not Box1 or Box3
        self.assertEqual(len(info["body_names"]), 1,
            "Should export only one body")
        self.assertIn("Box2", info["body_names"],
            "Should export Box2")
        self.assertNotIn("Box1", info["body_names"],
            "Should not export Box1")
        self.assertNotIn("Box3", info["body_names"],
            "Should not export Box3")


class TestStepExportTierCleanup(unittest.TestCase):
    """
    Test that STEP export produces exactly one body, even when tier fallbacks occur.

    This catches bugs where cleanup between tiers is missing, causing
    duplicate bodies in the exported STEP file. For example, if Tier 1
    partially succeeds (copies a body to the temp component) but then fails,
    Tier 2 must clean up the leftover body before adding its own.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = create_test_document("StepExportTierCleanupTest")
        cls.fusion = Fusion(cls.doc)
        cls.design = cls.fusion.getDesign()

        # Create a proxy body scenario that triggers Tier 1 failure.
        # Proxy bodies with non-identity transforms often cause
        # InternalValidationError in copyToComponent, forcing fallback to Tier 2.
        transform = adsk.core.Matrix3D.create()
        transform.translation = adsk.core.Vector3D.create(10, 20, 30)

        cls.occurrence = cls.design.rootComponent.occurrences.addNewComponent(transform)
        cls.occurrence.component.name = "TierCleanupTestComponent"

        cls.native_body = create_box_body(
            cls.occurrence.component,
            name="TierCleanupTestBody",
            corner=(0, 0, 0),
            size=(2, 2, 2)
        )
        cls.proxy_body = cls.occurrence.bRepBodies.item(0)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close(saveChanges=False)

    def test_proxy_body_is_valid(self):
        """Verify we have a proper proxy body for testing."""
        self.assertIsNotNone(self.proxy_body)
        self.assertIsNotNone(self.proxy_body.nativeObject,
            "Should be a proxy body (accessed through occurrence)")

    def test_export_produces_exactly_one_solid_body(self):
        """
        Verify STEP export produces exactly one solid body entity.

        This catches the bug where Tier 1 partially succeeds (copies body)
        then fails, and Tier 2 adds another body without cleanup, resulting
        in duplicate bodies in the exported STEP file.
        """
        step_content, _ = get_step_file_content(self.fusion, self.proxy_body)
        info = get_step_geometry_info(step_content)

        self.assertEqual(info["solid_body_count"], 1,
            f"STEP export should contain exactly 1 solid body, "
            f"but found {info['solid_body_count']}: {info['solid_body_names']}")
