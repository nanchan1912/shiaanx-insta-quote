"""
Utilities for importing fixture solids into Fusion 360.

Fixture solids are auxiliary geometry (e.g., clamps, jigs, support structures)
that are imported alongside the main part during program import.

Fixture Transform Convention
----------------------------
Fixtures are positioned using T_pcs_from_fixture_file, a 4x4 homogeneous
transformation matrix that transforms coordinates FROM the fixture's STEP
file coordinate system TO the Part Coordinate System (PCS).

The matrix is encoded as a 16-element vector in column-major order:
    [M_11, M_21, M_31, M_41,   # Column 1 (X basis vector)
     M_12, M_22, M_32, M_42,   # Column 2 (Y basis vector)
     M_13, M_23, M_33, M_43,   # Column 3 (Z basis vector)
     M_14, M_24, M_34, M_44]   # Column 4 (Translation, in cm)

This matches the pattern used for stepCoordinateSystem_cm in other STEP
file handling (stock solids, etc.).

Coordinate System Notes:
- Translation values are in centimeters (cm)
- The web application is responsible for computing this transform, including
  any coordinate system conversions (e.g., Three.js Z-up to Fusion Y-up)
- The add-in simply applies the transform as-is without additional conversion
"""

import adsk.core
import adsk.fusion
import tempfile
import os
import urllib.request
from typing import List, Dict, Any, Optional, Tuple

from .fusion_utils import Fusion, add_component
from .general_utils import log


def matrix3d_from_column_major_vector(transform_vector: List[float]) -> adsk.core.Matrix3D:
    """
    Create a Fusion Matrix3D from a 16-element column-major flattened 4x4 matrix.

    The transform represents T_pcs_from_fixture_file - the transform FROM fixture
    file coordinates TO part coordinate system (PCS).

    Arguments:
        transform_vector: 16 floats in column-major order:
            [M_11, M_21, M_31, M_41, M_12, M_22, M_32, M_42,
             M_13, M_23, M_33, M_43, M_14, M_24, M_34, M_44]
            where M_i4 (indices 12,13,14) are translation components in cm

    Returns:
        Matrix3D ready to use with Fusion API

    Raises:
        ValueError: If transform_vector does not have exactly 16 elements

    Note: Translation is expected in cm (matching stepCoordinateSystem_cm pattern).
    """
    if len(transform_vector) != 16:
        raise ValueError(f"Transform vector must have 16 elements, got {len(transform_vector)}")

    matrix = adsk.core.Matrix3D.create()

    # Column-major to Fusion Matrix3D conversion
    # Column-major index: col * 4 + row
    for row in range(4):
        for col in range(4):
            idx = col * 4 + row
            matrix.setCell(row, col, transform_vector[idx])

    return matrix


def fetch_step_content(step_url: str) -> str:
    """
    Fetch STEP file content from a URL.

    Arguments:
        step_url: URL to the STEP file (http:// or https://)

    Returns:
        STEP file content as string, or empty string on failure
    """
    if not step_url.startswith('http://') and not step_url.startswith('https://'):
        log(f"[fetch_step_content] Invalid STEP URL (must be http/https): {step_url}", force_console=True)
        return ""

    try:
        with urllib.request.urlopen(step_url, timeout=30) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        log(f"[fetch_step_content] Failed to fetch STEP from URL: {e}", force_console=True)
        return ""


def import_step_with_transform(
    step_content: str,
    transform: adsk.core.Matrix3D,
    design: adsk.fusion.Design,
    fusion: Fusion,
    name: str = "Fixture",
    target_component: Optional[adsk.fusion.Component] = None
) -> Optional[adsk.fusion.Occurrence]:
    """
    Import a STEP file and apply a transformation to position it in the design.

    Strategy: Create a wrapper component with the desired transform, then import
    the STEP into that wrapper. This ensures proper positioning since Fusion's
    addNewComponent accepts a transform at creation time.

    Arguments:
        step_content: The STEP file content as a string
        transform: Matrix3D transformation to apply to the imported geometry
        design: The Fusion design to import into
        fusion: Fusion utility instance
        name: Name to give the imported component
        target_component: Component to import into (defaults to rootComponent)

    Returns:
        The wrapper occurrence (positioned), or None if import failed
    """
    app = fusion.getApplication()
    importManager = app.importManager
    target_comp = target_component if target_component else design.rootComponent

    # Create a wrapper component WITH the transform applied at creation time
    # This is the pattern that works in Fusion - transform at creation via addNewComponent
    wrapper_occurrence = target_comp.occurrences.addNewComponent(transform)
    wrapper_occurrence.component.name = name
    wrapper_occurrence.isGroundToParent = True

    wrapper_comp = wrapper_occurrence.component

    # Write STEP content to temp file and import INTO the wrapper
    tmpdir = tempfile.mkdtemp()
    step_path = os.path.join(tmpdir, "fixture.step")

    try:
        with open(step_path, "w") as f:
            f.write(step_content)

        options = importManager.createSTEPImportOptions(step_path)

        # Import to the wrapper component (at origin relative to wrapper)
        imported = importManager.importToTarget(options, wrapper_comp)

        if not imported:
            log(f"[import_step_with_transform] Failed to import STEP file: {name}", force_console=True)
            # Clean up the empty wrapper
            wrapper_occurrence.deleteMe()
            return None

    finally:
        # Clean up temp file
        try:
            os.remove(step_path)
            os.rmdir(tmpdir)
        except Exception:
            pass

    return wrapper_occurrence


def import_fixture_solids(
    fixture_data: Optional[Dict[str, Any]],
    design: adsk.fusion.Design,
    fusion: Fusion,
    part_offset: List[float] = [0.0, 0.0, 0.0],
    parent_component: Optional[adsk.fusion.Component] = None
) -> Tuple[List[Optional[adsk.fusion.Occurrence]], Optional[adsk.fusion.Occurrence]]:
    """
    Import all fixture solids from the fixture data.

    Fixtures are positioned and grounded immediately during import, so their
    positions will persist through timeline recomputation.

    Arguments:
        fixture_data: Dictionary containing 'fixtureSolids' list, or None
        design: The Fusion design to import into
        fusion: Fusion utility instance
        part_offset: Offset to add (in cm) to account for arbitrary
                     part origin vs workholding being relative to center
        parent_component: Component to import fixtures into (creates Toolpath Workholding (Setup 1) if None)

    Returns:
        Tuple of (imported_fixtures, parent_occurrence):
            - imported_fixtures: List of fixture occurrences (already positioned and grounded)
            - parent_occurrence: The parent "Toolpath Workholding (Setup 1)" occurrence, or None

    The fixture_data structure is expected to be:
    {
        "fixtureSolids": [
            {
                "name": str,
                "stepUrl": str,  # URL to STEP file (must be http:// or https://)
                "T_pcs_from_fixture_file": [16 floats]  # 4x4 matrix, column-major, cm
            },
            ...
        ]
    }
    """
    imported_fixtures = []

    if fixture_data is None:
        return imported_fixtures, None

    fixture_solids = fixture_data.get("fixtureSolids", [])

    if not fixture_solids:
        return imported_fixtures, None

    log(f"[import_fixture_solids] Importing {len(fixture_solids)} fixture solids", force_console=True)

    # Create a "Workholding" container under the parent component
    # This keeps fixtures separate from other sketches/geometry and allows visibility control
    workholding_occ = add_component(
        parent_component if parent_component else design.rootComponent,
        name="Toolpath Workholding (Setup 1)",
        isGroundToParent=True,
        isLightBulbOn=True  # Make workholding visible even if parent is hidden
    )
    workholding_comp = workholding_occ.component

    for i, solid in enumerate(fixture_solids):
        try:
            name = solid.get("name", f"Fixture_{i+1}")
            step_path = solid.get("stepUrl", "")
            transform_vector = solid.get("T_pcs_from_fixture_file")

            # Validate transform data
            if transform_vector is None:
                log(f"[import_fixture_solids] Fixture '{name}' missing T_pcs_from_fixture_file, skipping", force_console=True)
                continue

            # Fetch STEP content from URL
            step_content = fetch_step_content(step_path)

            if not step_content:
                log(f"[import_fixture_solids] Failed to fetch STEP for '{name}', skipping", force_console=True)
                continue

            # Create transform from the provided matrix
            # The frontend handles all coordinate system conversions (Z-up to Y-up)
            transform = matrix3d_from_column_major_vector(transform_vector)

            # Apply part offset (converts mm to cm)
            ox = part_offset[0] / 10.0
            oy = part_offset[1] / 10.0
            oz = part_offset[2] / 10.0

            offset_vector = adsk.core.Vector3D.create(ox, oy, oz)
            offset_matrix = adsk.core.Matrix3D.create()
            offset_matrix.translation = offset_vector

            transform.transformBy(offset_matrix)

            # Import the STEP file into the Workholding container
            occurrence = import_step_with_transform(
                step_content=step_content,
                transform=transform,
                design=design,
                fusion=fusion,
                name=name,
                target_component=workholding_comp
            )

            if occurrence:
                log(f"[import_fixture_solids] Imported fixture '{name}'", force_console=True)
                imported_fixtures.append(occurrence)
            else:
                log(f"[import_fixture_solids] Failed to import '{name}'", force_console=True)

        except Exception as e:
            log(f"[import_fixture_solids] Exception importing fixture: {e}", force_console=True)
            continue

    # Create a rigid group for all fixtures to lock them together
    if len(imported_fixtures) > 0:
        try:
            occs = adsk.core.ObjectCollection.create()
            occs.add(workholding_occ)
            rigid_group = design.rootComponent.rigidGroups.add(occs, True)  # True = include children
            if rigid_group:
                rigid_group.name = "Toolpath Workholding Rigid Group"
        except Exception as e:
            log(f"[import_fixture_solids] Failed to create rigid group: {e}", force_console=True)

    return imported_fixtures, workholding_occ
