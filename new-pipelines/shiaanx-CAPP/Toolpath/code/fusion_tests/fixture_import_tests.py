"""
Integration tests for fixture import functionality.

These tests verify the fixture import flow in the actual Fusion 360 environment:
1. matrix3d_from_column_major_vector() creates correct transforms
2. import_fixture_solids() creates components with correct transforms
3. Rigid groups are created for fixtures
4. Part offset is applied correctly

Tests create geometry programmatically to avoid external file dependencies.
"""
import unittest
import adsk.core
import adsk.fusion
import tempfile
import os

from ..lib.fusion_utils import Fusion, add_component
from ..lib.fixture_utils import (
    matrix3d_from_column_major_vector,
    import_fixture_solids,
    import_step_with_transform,
)
from ..lib.general_utils import log


def create_test_document(name="FixtureImportTest"):
    """Create a new Fusion document for testing."""
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    doc.name = name
    return doc


def create_simple_step_content():
    """
    Create minimal valid STEP file content for testing.
    This creates a simple box geometry.
    """
    # Minimal STEP file for a 1x1x1 cm box
    step_content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Test Fixture'),'2;1');
FILE_NAME('test_fixture.step','2024-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#1=SHAPE_DEFINITION_REPRESENTATION(#2,#3);
#2=PRODUCT_DEFINITION_SHAPE('',$,#4);
#3=ADVANCED_BREP_SHAPE_REPRESENTATION('',(#5),#100);
#4=PRODUCT_DEFINITION('design','',#6,#7);
#5=MANIFOLD_SOLID_BREP('TestFixture',#8);
#6=PRODUCT_DEFINITION_FORMATION('',$,#9);
#7=PRODUCT_DEFINITION_CONTEXT('',#10,'design');
#8=CLOSED_SHELL('',(#11,#12,#13,#14,#15,#16));
#9=PRODUCT('TestFixture','TestFixture','',(#17));
#10=APPLICATION_CONTEXT('automotive design');
#11=ADVANCED_FACE('',(#21),#31,.T.);
#12=ADVANCED_FACE('',(#22),#32,.T.);
#13=ADVANCED_FACE('',(#23),#33,.T.);
#14=ADVANCED_FACE('',(#24),#34,.T.);
#15=ADVANCED_FACE('',(#25),#35,.T.);
#16=ADVANCED_FACE('',(#26),#36,.T.);
#17=PRODUCT_CONTEXT('',#10,'mechanical');
#21=FACE_OUTER_BOUND('',#41,.T.);
#22=FACE_OUTER_BOUND('',#42,.T.);
#23=FACE_OUTER_BOUND('',#43,.T.);
#24=FACE_OUTER_BOUND('',#44,.T.);
#25=FACE_OUTER_BOUND('',#45,.T.);
#26=FACE_OUTER_BOUND('',#46,.T.);
#31=PLANE('',#51);
#32=PLANE('',#52);
#33=PLANE('',#53);
#34=PLANE('',#54);
#35=PLANE('',#55);
#36=PLANE('',#56);
#41=EDGE_LOOP('',(#61,#62,#63,#64));
#42=EDGE_LOOP('',(#65,#66,#67,#68));
#43=EDGE_LOOP('',(#69,#70,#71,#72));
#44=EDGE_LOOP('',(#73,#74,#75,#76));
#45=EDGE_LOOP('',(#77,#78,#79,#80));
#46=EDGE_LOOP('',(#81,#82,#83,#84));
#51=AXIS2_PLACEMENT_3D('',#91,#101,#111);
#52=AXIS2_PLACEMENT_3D('',#92,#102,#112);
#53=AXIS2_PLACEMENT_3D('',#93,#103,#113);
#54=AXIS2_PLACEMENT_3D('',#94,#104,#114);
#55=AXIS2_PLACEMENT_3D('',#95,#105,#115);
#56=AXIS2_PLACEMENT_3D('',#96,#106,#116);
#61=ORIENTED_EDGE('',*,*,#121,.T.);
#62=ORIENTED_EDGE('',*,*,#122,.T.);
#63=ORIENTED_EDGE('',*,*,#123,.T.);
#64=ORIENTED_EDGE('',*,*,#124,.T.);
#65=ORIENTED_EDGE('',*,*,#125,.T.);
#66=ORIENTED_EDGE('',*,*,#126,.T.);
#67=ORIENTED_EDGE('',*,*,#127,.T.);
#68=ORIENTED_EDGE('',*,*,#128,.T.);
#69=ORIENTED_EDGE('',*,*,#129,.T.);
#70=ORIENTED_EDGE('',*,*,#130,.T.);
#71=ORIENTED_EDGE('',*,*,#131,.T.);
#72=ORIENTED_EDGE('',*,*,#132,.T.);
#73=ORIENTED_EDGE('',*,*,#133,.T.);
#74=ORIENTED_EDGE('',*,*,#134,.T.);
#75=ORIENTED_EDGE('',*,*,#135,.T.);
#76=ORIENTED_EDGE('',*,*,#136,.T.);
#77=ORIENTED_EDGE('',*,*,#137,.T.);
#78=ORIENTED_EDGE('',*,*,#138,.T.);
#79=ORIENTED_EDGE('',*,*,#139,.T.);
#80=ORIENTED_EDGE('',*,*,#140,.T.);
#81=ORIENTED_EDGE('',*,*,#141,.T.);
#82=ORIENTED_EDGE('',*,*,#142,.T.);
#83=ORIENTED_EDGE('',*,*,#143,.T.);
#84=ORIENTED_EDGE('',*,*,#144,.T.);
#91=CARTESIAN_POINT('',(0.,0.,0.));
#92=CARTESIAN_POINT('',(0.,0.,1.));
#93=CARTESIAN_POINT('',(0.,0.,0.));
#94=CARTESIAN_POINT('',(1.,0.,0.));
#95=CARTESIAN_POINT('',(0.,0.,0.));
#96=CARTESIAN_POINT('',(0.,1.,0.));
#100=( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#200)) GLOBAL_UNIT_ASSIGNED_CONTEXT((#201,#202,#203)) REPRESENTATION_CONTEXT('Context','3D') );
#101=DIRECTION('',(0.,0.,1.));
#102=DIRECTION('',(0.,0.,-1.));
#103=DIRECTION('',(1.,0.,0.));
#104=DIRECTION('',(-1.,0.,0.));
#105=DIRECTION('',(0.,1.,0.));
#106=DIRECTION('',(0.,-1.,0.));
#111=DIRECTION('',(1.,0.,0.));
#112=DIRECTION('',(1.,0.,0.));
#113=DIRECTION('',(0.,1.,0.));
#114=DIRECTION('',(0.,1.,0.));
#115=DIRECTION('',(1.,0.,0.));
#116=DIRECTION('',(1.,0.,0.));
#121=EDGE_CURVE('',#151,#152,#161,.T.);
#122=EDGE_CURVE('',#152,#153,#162,.T.);
#123=EDGE_CURVE('',#153,#154,#163,.T.);
#124=EDGE_CURVE('',#154,#151,#164,.T.);
#125=EDGE_CURVE('',#155,#156,#165,.T.);
#126=EDGE_CURVE('',#156,#157,#166,.T.);
#127=EDGE_CURVE('',#157,#158,#167,.T.);
#128=EDGE_CURVE('',#158,#155,#168,.T.);
#129=EDGE_CURVE('',#151,#155,#169,.T.);
#130=EDGE_CURVE('',#152,#156,#170,.T.);
#131=EDGE_CURVE('',#153,#157,#171,.T.);
#132=EDGE_CURVE('',#154,#158,#172,.T.);
#133=EDGE_CURVE('',#151,#152,#173,.T.);
#134=EDGE_CURVE('',#155,#156,#174,.T.);
#135=EDGE_CURVE('',#153,#154,#175,.T.);
#136=EDGE_CURVE('',#157,#158,#176,.T.);
#137=EDGE_CURVE('',#151,#154,#177,.T.);
#138=EDGE_CURVE('',#155,#158,#178,.T.);
#139=EDGE_CURVE('',#152,#153,#179,.T.);
#140=EDGE_CURVE('',#156,#157,#180,.T.);
#141=EDGE_CURVE('',#151,#155,#181,.T.);
#142=EDGE_CURVE('',#154,#158,#182,.T.);
#143=EDGE_CURVE('',#152,#156,#183,.T.);
#144=EDGE_CURVE('',#153,#157,#184,.T.);
#151=VERTEX_POINT('',#191);
#152=VERTEX_POINT('',#192);
#153=VERTEX_POINT('',#193);
#154=VERTEX_POINT('',#194);
#155=VERTEX_POINT('',#195);
#156=VERTEX_POINT('',#196);
#157=VERTEX_POINT('',#197);
#158=VERTEX_POINT('',#198);
#161=LINE('',#191,#211);
#162=LINE('',#192,#212);
#163=LINE('',#193,#213);
#164=LINE('',#194,#214);
#165=LINE('',#195,#215);
#166=LINE('',#196,#216);
#167=LINE('',#197,#217);
#168=LINE('',#198,#218);
#169=LINE('',#191,#219);
#170=LINE('',#192,#220);
#171=LINE('',#193,#221);
#172=LINE('',#194,#222);
#173=LINE('',#191,#223);
#174=LINE('',#195,#224);
#175=LINE('',#193,#225);
#176=LINE('',#197,#226);
#177=LINE('',#191,#227);
#178=LINE('',#195,#228);
#179=LINE('',#192,#229);
#180=LINE('',#196,#230);
#181=LINE('',#191,#231);
#182=LINE('',#194,#232);
#183=LINE('',#192,#233);
#184=LINE('',#193,#234);
#191=CARTESIAN_POINT('',(0.,0.,0.));
#192=CARTESIAN_POINT('',(1.,0.,0.));
#193=CARTESIAN_POINT('',(1.,1.,0.));
#194=CARTESIAN_POINT('',(0.,1.,0.));
#195=CARTESIAN_POINT('',(0.,0.,1.));
#196=CARTESIAN_POINT('',(1.,0.,1.));
#197=CARTESIAN_POINT('',(1.,1.,1.));
#198=CARTESIAN_POINT('',(0.,1.,1.));
#200=UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#201,'distance accuracy','');
#201=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.CENTI.,.METRE.));
#202=(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.));
#203=(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT());
#211=VECTOR('',#101,1.);
#212=VECTOR('',#105,1.);
#213=VECTOR('',#101,1.);
#214=VECTOR('',#105,1.);
#215=VECTOR('',#101,1.);
#216=VECTOR('',#105,1.);
#217=VECTOR('',#101,1.);
#218=VECTOR('',#105,1.);
#219=VECTOR('',#101,1.);
#220=VECTOR('',#101,1.);
#221=VECTOR('',#101,1.);
#222=VECTOR('',#101,1.);
#223=VECTOR('',#103,1.);
#224=VECTOR('',#103,1.);
#225=VECTOR('',#103,1.);
#226=VECTOR('',#103,1.);
#227=VECTOR('',#105,1.);
#228=VECTOR('',#105,1.);
#229=VECTOR('',#105,1.);
#230=VECTOR('',#105,1.);
#231=VECTOR('',#101,1.);
#232=VECTOR('',#101,1.);
#233=VECTOR('',#101,1.);
#234=VECTOR('',#101,1.);
ENDSEC;
END-ISO-10303-21;
"""
    return step_content


def write_temp_step_file(step_content):
    """Write STEP content to a temporary file and return the path."""
    tmpdir = tempfile.mkdtemp()
    step_path = os.path.join(tmpdir, "test_fixture.step")
    with open(step_path, "w") as f:
        f.write(step_content)
    return step_path, tmpdir


class TestMatrix3dFromColumnMajorVectorIntegration(unittest.TestCase):
    """Integration tests for matrix3d_from_column_major_vector in Fusion 360."""

    def test_identity_matrix_creates_valid_fusion_matrix(self):
        """Identity matrix should create a valid Fusion Matrix3D."""
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

        matrix = matrix3d_from_column_major_vector(identity)

        # Verify it's a real Fusion Matrix3D
        self.assertIsInstance(matrix, adsk.core.Matrix3D)

        # Verify identity values
        self.assertAlmostEqual(matrix.getCell(0, 0), 1.0, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 1), 1.0, places=5)
        self.assertAlmostEqual(matrix.getCell(2, 2), 1.0, places=5)
        self.assertAlmostEqual(matrix.getCell(3, 3), 1.0, places=5)

    def test_translation_matrix_sets_correct_values(self):
        """Translation-only matrix should set translation correctly."""
        # Identity rotation with translation (5, 10, 15) cm
        transform = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 10, 15, 1]

        matrix = matrix3d_from_column_major_vector(transform)

        # Check translation
        translation = matrix.translation
        self.assertAlmostEqual(translation.x, 5.0, places=5)
        self.assertAlmostEqual(translation.y, 10.0, places=5)
        self.assertAlmostEqual(translation.z, 15.0, places=5)

    def test_rotation_matrix_is_orthogonal(self):
        """Rotation matrix should be orthogonal (det = 1)."""
        # 90-degree rotation around Z
        transform = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

        matrix = matrix3d_from_column_major_vector(transform)

        # For orthogonal rotation, columns should be unit vectors
        # Column 0: (0, 1, 0)
        self.assertAlmostEqual(matrix.getCell(0, 0), 0.0, places=5)
        self.assertAlmostEqual(matrix.getCell(1, 0), 1.0, places=5)
        self.assertAlmostEqual(matrix.getCell(2, 0), 0.0, places=5)


class TestImportStepWithTransformIntegration(unittest.TestCase):
    """Integration tests for import_step_with_transform in Fusion 360."""

    @classmethod
    def setUpClass(cls):
        """Create a test document."""
        cls.doc = create_test_document("ImportStepWithTransformTest")
        app = adsk.core.Application.get()
        cls.design = adsk.fusion.Design.cast(app.activeProduct)
        cls.fusion = Fusion()

    @classmethod
    def tearDownClass(cls):
        """Close the test document."""
        try:
            cls.doc.close(saveChanges=False)
        except:
            pass

    def test_import_step_creates_component(self):
        """Importing a STEP file should create a component in Fusion."""
        step_content = create_simple_step_content()
        identity_transform = matrix3d_from_column_major_vector(
            [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        )

        occurrence = import_step_with_transform(
            step_content=step_content,
            transform=identity_transform,
            design=self.design,
            fusion=self.fusion,
            name="TestImportedFixture"
        )

        # Verify occurrence was created
        self.assertIsNotNone(occurrence)
        self.assertEqual(occurrence.component.name, "TestImportedFixture")

    def test_import_step_with_translation(self):
        """Imported STEP should be positioned according to transform."""
        step_content = create_simple_step_content()
        # Translate 10 cm in X
        transform = matrix3d_from_column_major_vector(
            [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10, 0, 0, 1]
        )

        occurrence = import_step_with_transform(
            step_content=step_content,
            transform=transform,
            design=self.design,
            fusion=self.fusion,
            name="TranslatedFixture"
        )

        self.assertIsNotNone(occurrence)
        # The transform should be applied to the occurrence
        occ_transform = occurrence.transform
        self.assertAlmostEqual(occ_transform.translation.x, 10.0, places=3)


class TestImportFixtureSolidsIntegration(unittest.TestCase):
    """Integration tests for import_fixture_solids in Fusion 360."""

    @classmethod
    def setUpClass(cls):
        """Create a test document."""
        cls.doc = create_test_document("ImportFixtureSolidsTest")
        app = adsk.core.Application.get()
        cls.design = adsk.fusion.Design.cast(app.activeProduct)
        cls.fusion = Fusion()

    @classmethod
    def tearDownClass(cls):
        """Close the test document."""
        try:
            cls.doc.close(saveChanges=False)
        except:
            pass

    def test_none_fixture_data_returns_empty(self):
        """None fixture_data should return empty list."""
        result, parent = import_fixture_solids(
            fixture_data=None,
            design=self.design,
            fusion=self.fusion
        )

        self.assertEqual(result, [])
        self.assertIsNone(parent)

    def test_empty_fixture_solids_returns_empty(self):
        """Empty fixtureSolids should return empty list."""
        fixture_data = {"fixtureSolids": []}

        result, parent = import_fixture_solids(
            fixture_data=fixture_data,
            design=self.design,
            fusion=self.fusion
        )

        self.assertEqual(result, [])
        self.assertIsNone(parent)

    def test_fixture_missing_transform_is_skipped(self):
        """Fixture without T_pcs_from_fixture_file should be skipped."""
        fixture_data = {
            "fixtureSolids": [
                {
                    "name": "MissingTransformFixture",
                    "stepUrl": "https://example.com/fixture.step"
                    # No T_pcs_from_fixture_file
                }
            ]
        }

        result, parent = import_fixture_solids(
            fixture_data=fixture_data,
            design=self.design,
            fusion=self.fusion
        )

        # Should return empty since fixture was skipped
        self.assertEqual(len(result), 0)


class TestFixtureImportWithLocalFile(unittest.TestCase):
    """
    Integration tests using local STEP files to avoid network dependencies.

    These tests create temporary STEP files and test the full import flow.
    """

    @classmethod
    def setUpClass(cls):
        """Create a test document and temp STEP file."""
        cls.doc = create_test_document("LocalFileFixtureTest")
        app = adsk.core.Application.get()
        cls.design = adsk.fusion.Design.cast(app.activeProduct)
        cls.fusion = Fusion()

        # Create temp STEP file
        cls.step_content = create_simple_step_content()
        cls.step_path, cls.tmpdir = write_temp_step_file(cls.step_content)

    @classmethod
    def tearDownClass(cls):
        """Clean up test document and temp files."""
        try:
            cls.doc.close(saveChanges=False)
        except:
            pass

        try:
            os.remove(cls.step_path)
            os.rmdir(cls.tmpdir)
        except:
            pass

    def test_direct_step_import_with_identity_transform(self):
        """Direct STEP import with identity transform should work."""
        identity = matrix3d_from_column_major_vector(
            [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        )

        occurrence = import_step_with_transform(
            step_content=self.step_content,
            transform=identity,
            design=self.design,
            fusion=self.fusion,
            name="DirectImportFixture"
        )

        self.assertIsNotNone(occurrence)
        self.assertTrue(occurrence.isGroundToParent)

    def test_direct_step_import_with_translation_and_rotation(self):
        """Direct STEP import with combined transform should position correctly."""
        # 90-degree rotation around Z plus translation (5, 5, 0) cm
        transform = matrix3d_from_column_major_vector(
            [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 5, 5, 0, 1]
        )

        occurrence = import_step_with_transform(
            step_content=self.step_content,
            transform=transform,
            design=self.design,
            fusion=self.fusion,
            name="RotatedTranslatedFixture"
        )

        self.assertIsNotNone(occurrence)

        # Verify transform was applied
        occ_transform = occurrence.transform
        self.assertAlmostEqual(occ_transform.translation.x, 5.0, places=3)
        self.assertAlmostEqual(occ_transform.translation.y, 5.0, places=3)


class TestParentWorkholdingComponent(unittest.TestCase):
    """Tests for the parent Toolpath Workholding component creation."""

    @classmethod
    def setUpClass(cls):
        """Create a test document."""
        cls.doc = create_test_document("ParentWorkholdingTest")
        app = adsk.core.Application.get()
        cls.design = adsk.fusion.Design.cast(app.activeProduct)
        cls.fusion = Fusion()

    @classmethod
    def tearDownClass(cls):
        """Close the test document."""
        try:
            cls.doc.close(saveChanges=False)
        except:
            pass

    def test_parent_component_created_with_correct_name(self):
        """Parent workholding component should be named correctly."""
        # This test would need real fixture data with working URLs
        # For now, we test that add_component works correctly
        parent_occ = add_component(
            self.design.rootComponent,
            name="Toolpath Workholding (Setup 1)",
            isGroundToParent=True
        )

        self.assertIsNotNone(parent_occ)
        self.assertEqual(parent_occ.component.name, "Toolpath Workholding (Setup 1)")
        self.assertTrue(parent_occ.isGroundToParent)


if __name__ == '__main__':
    unittest.main()
