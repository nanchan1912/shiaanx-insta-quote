import math
import traceback

import adsk.core
import adsk.fusion

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, handle_error
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar


class Cmd:
    """Command for rigging joint origins to faces on setup containers (bodies, vises, plates)."""

    # Log prefix for consistent logging
    LOG_PREFIX = "[SetupRigger]"

    # Geometry constants for preview indicators
    INDICATOR_CYLINDER_RADIUS = 0.3  # cm (3mm)
    INDICATOR_CYLINDER_HEIGHT = 1.0  # cm (10mm)
    INDICATOR_PLANE_THICKNESS = 0.05  # cm (0.5mm)
    INDICATOR_SPHERE_RADIUS = 0.4  # cm (4mm)

    # Available colors mapped to Fusion appearance names
    # Note: Fusion only has Red, Blue, Green, Yellow, White, Gray for translucent plastics
    # Magenta/Cyan are created as custom appearances by modifying the base color
    APPEARANCE_MAP = {
        "Red": "Plastic - Translucent Glossy (Red)",
        "Green": "Plastic - Translucent Glossy (Green)",
        "Blue": "Plastic - Translucent Glossy (Blue)",
        "Yellow": "Plastic - Translucent Glossy (Yellow)",
        "White": "Plastic - Translucent Glossy (White)",
        "Gray": "Plastic - Translucent Glossy (Gray)",
        "Magenta": None,  # Custom - created by modifying base appearance
        "Cyan": None,  # Custom - created by modifying base appearance
    }

    # Custom colors that need to be created by modifying a base appearance
    # Format: color_name -> (base_appearance, R, G, B, A) where RGBA are 0-255
    CUSTOM_COLORS = {
        "Magenta": ("Plastic - Translucent Glossy (Red)", 255, 0, 255, 255),
        "Cyan": ("Plastic - Translucent Glossy (Blue)", 0, 255, 255, 255),
    }

    # RGB values for UI color swatches
    COLOR_RGB = {
        "Red": (255, 0, 0),
        "Green": (0, 255, 0),
        "Blue": (0, 0, 255),
        "Yellow": (255, 255, 0),
        "White": (255, 255, 255),
        "Gray": (128, 128, 128),
        "Magenta": (255, 0, 255),
        "Cyan": (0, 255, 255),
    }

    # Anchor definitions for each setup type
    # - name: display name in UI
    # - joint_origin_name: name of the joint origin in the model hierarchy
    # - appearance: color name from APPEARANCE_MAP/COLOR_RGB
    # - dual_selection: True for anchors needing two parallel faces (like vise center)
    BODY_ANCHORS = [
        {"name": "Stock Attachment", "joint_origin_name": "Stock Attachment", "appearance": "Red", "id": "body_stock_attachment"},
        {"name": "Jaw 1", "joint_origin_name": "Jaw Position 1", "appearance": "Green", "id": "body_jaw_1", "selection_type": "edge_face", "rotation": 180},
        {"name": "Jaw 2", "joint_origin_name": "Jaw Position 2", "appearance": "Blue", "id": "body_jaw_2", "selection_type": "edge_face", "rotation": 180},
        {"name": "Vise Center", "joint_origin_name": "Vise Center", "appearance": "Yellow", "id": "body_vise_center", "dual_selection": True},
        {"name": "Part Attachment", "joint_origin_name": "Part Attachment", "appearance": "Gray", "id": "body_part_attachment"},
    ]

    # Vise anchor IDs (used in VISE_ANCHORS and validation)
    VISE_STOCK_ID = "vise_stock_attachment"
    VISE_JAW1_ID = "vise_jaw_1"
    VISE_JAW2_ID = "vise_jaw_2"
    VISE_CENTER_ID = "vise_center"
    VISE_ZERO_POINT_ID = "vise_zero_point"

    # TODO: Get hierarchy logs to determine correct joint_origin_name values for VISE and PLATE
    VISE_ANCHORS = [
        {"name": "Stock Attachment", "joint_origin_name": "Stock Attachment", "appearance": "Red", "id": VISE_STOCK_ID},
        {"name": "Jaw 1", "joint_origin_name": "Jaw Position 1", "appearance": "Green", "id": VISE_JAW1_ID},
        {"name": "Jaw 2", "joint_origin_name": "Jaw Position 2", "appearance": "Blue", "id": VISE_JAW2_ID},
        {"name": "Vise Center", "joint_origin_name": "Vise Center", "appearance": "Yellow", "id": VISE_CENTER_ID, "dual_selection": True},
        {"name": "Zero Point Attachment", "joint_origin_name": "Zero Point Attachment", "appearance": "Magenta", "id": VISE_ZERO_POINT_ID, "selection_type": "point"},
    ]

    PLATE_ANCHORS = [
        {"name": "WCS", "joint_origin_name": "WCS Attachment", "appearance": "Magenta", "id": "plate_wcs", "selection_type": "point"},
        {"name": "Machine Attachment", "joint_origin_name": "Machine Model Attachment", "appearance": "Magenta", "id": "plate_machine", "selection_type": "point", "flip": True},
        {"name": "Zero Points", "joint_origin_name_prefix": "Zero Point Attachment", "appearance": "Red", "id": "plate_zero_points", "selection_type": "multi_point", "count": 9},
    ]

    def __init__(self):
        self.CMD_NAME = 'Setup Rigger'
        self.CMD_ID = command_id_from_name(self.CMD_NAME)
        self.CMD_Description = 'Rig joint origins to faces for setup containers.'
        self.ICON_FOLDER = resource_path("toolpath_logo", '')
        self.local_handlers = []

        # Input IDs
        self.SETUP_TYPE_DROPDOWN_ID = "setup_type_dropdown"

        # Setup type options
        self.SETUP_TYPE_BODY = "Body"
        self.SETUP_TYPE_VISE = "Vise"
        self.SETUP_TYPE_PLATE = "Plate"

        # Track selected faces via entity tokens (tokens survive across event calls)
        self.selected_face_tokens = {}  # anchor_id or anchor_id_face1/face2 -> token string

        # Track snap points for point selections (when edge/face is selected via snap point)
        self.selected_snap_points = {}  # anchor_id -> Point3D

        # Cache for document appearances (cleared when preview rolls back)
        self.appearance_cache = {}

        # Track preview geometry for manual cleanup
        self.preview_sketches = []  # List of sketch entity tokens
        self.preview_features = []  # List of feature entity tokens

    def start(self):
        ui = None
        try:
            fusion = Fusion()
            ui = fusion.getUI()

            cmd_def = addCommandToToolbar(
                self.CMD_ID,
                self.CMD_NAME,
                self.CMD_Description,
                self.ICON_FOLDER,
                IS_PROMOTED=False
            )
            add_handler(cmd_def.commandCreated, self.onCommandCreated, local_handlers=self.local_handlers)
        except Exception:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    def stop(self):
        ui = None
        try:
            ui = Fusion().getUI()
            removeCommandFromToolbar(self.CMD_ID)
        except Exception:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    # -------------------------------------------------------------------------
    # Logging Helpers
    # -------------------------------------------------------------------------

    def _log(self, message, indent=False):
        """Log a message with consistent prefix."""
        prefix = f"{self.LOG_PREFIX} " + ("  " if indent else "")
        log(f"{prefix}{message}", force_console=True)

    def _log_error(self, message, indent=False):
        """Log an error message with consistent prefix."""
        self._log(f"Error: {message}", indent)

    def _log_warning(self, message, indent=False):
        """Log a warning message with consistent prefix."""
        self._log(f"Warning: {message}", indent)

    def _log_exception(self, context, exception, include_trace=True):
        """Log an exception with context and optional traceback."""
        self._log(f"Exception in {context}: {exception}")
        if include_trace:
            log(traceback.format_exc(), force_console=True)

    # -------------------------------------------------------------------------
    # Anchor Configuration Helpers
    # -------------------------------------------------------------------------

    def get_anchors_for_type(self, setup_type):
        """Return the anchor definitions for the given setup type."""
        if setup_type == self.SETUP_TYPE_BODY:
            return self.BODY_ANCHORS
        elif setup_type == self.SETUP_TYPE_VISE:
            return self.VISE_ANCHORS
        elif setup_type == self.SETUP_TYPE_PLATE:
            return self.PLATE_ANCHORS
        return []

    def get_all_anchors(self):
        """Return all anchor definitions across all setup types."""
        return self.BODY_ANCHORS + self.VISE_ANCHORS + self.PLATE_ANCHORS

    # -------------------------------------------------------------------------
    # Appearance Helpers
    # -------------------------------------------------------------------------

    def get_or_create_appearance(self, color_name):
        """Get appearance from cache, document, or copy from material library."""
        if color_name in self.appearance_cache:
            return self.appearance_cache[color_name]

        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)

        # Check if already in document
        doc_appearance_name = f"SetupRigger_{color_name}"
        existing = design.appearances.itemByName(doc_appearance_name)
        if existing:
            self.appearance_cache[color_name] = existing
            return existing

        # Check if this is a custom color that needs to be created
        if color_name in self.CUSTOM_COLORS:
            return self._create_custom_appearance(color_name, doc_appearance_name)

        # Find in material libraries and copy to document
        lib_appearance_name = self.APPEARANCE_MAP.get(color_name)
        if not lib_appearance_name:
            log(f"No appearance mapping for color: {color_name}", force_console=True)
            return None

        for i in range(app.materialLibraries.count):
            lib = app.materialLibraries.item(i)
            try:
                lib_appearance = lib.appearances.itemByName(lib_appearance_name)
                if lib_appearance:
                    doc_appearance = design.appearances.addByCopy(lib_appearance, doc_appearance_name)
                    self.appearance_cache[color_name] = doc_appearance
                    return doc_appearance
            except Exception:
                continue

        log(f"Appearance '{lib_appearance_name}' not found in any library", force_console=True)
        return None

    def _create_custom_appearance(self, color_name, doc_appearance_name):
        """Create a custom appearance by copying a base and modifying its color."""
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)

        base_name, r, g, b, a = self.CUSTOM_COLORS[color_name]

        # Find and copy the base appearance
        base_appearance = None
        for i in range(app.materialLibraries.count):
            lib = app.materialLibraries.item(i)
            try:
                base_appearance = lib.appearances.itemByName(base_name)
                if base_appearance:
                    break
            except Exception:
                continue

        if not base_appearance:
            log(f"Base appearance '{base_name}' not found for custom color {color_name}", force_console=True)
            return None

        try:
            # Copy the base appearance to the document
            doc_appearance = design.appearances.addByCopy(base_appearance, doc_appearance_name)

            # Find and modify the color property
            for i in range(doc_appearance.appearanceProperties.count):
                prop = doc_appearance.appearanceProperties.item(i)
                if prop.name == "Color" and hasattr(prop, 'value'):
                    # This is a ColorProperty
                    color_prop = adsk.core.ColorProperty.cast(prop)
                    if color_prop:
                        color_prop.value = adsk.core.Color.create(r, g, b, a)
                        break

            self.appearance_cache[color_name] = doc_appearance
            return doc_appearance

        except Exception as e:
            log(f"Error creating custom appearance {color_name}: {e}", force_console=True)
            return None

    def apply_appearance_to_body(self, body, color_name):
        """Apply appearance to body with retry on stale cache."""
        doc_appearance = self.get_or_create_appearance(color_name)
        if not doc_appearance:
            log(f"Could not find or create appearance for {color_name}", force_console=True)
            return

        try:
            body.appearance = doc_appearance
        except Exception:
            # Appearance may be stale, clear cache and retry
            if color_name in self.appearance_cache:
                del self.appearance_cache[color_name]
            doc_appearance = self.get_or_create_appearance(color_name)
            if doc_appearance:
                body.appearance = doc_appearance

    # -------------------------------------------------------------------------
    # Geometry Helpers
    # -------------------------------------------------------------------------

    def _get_world_point_from_selection(self, selection):
        """
        Get world coordinates from a selection.
        Handles SketchPoints specially since selection.point gives wrong coordinates for them.
        Returns Point3D in world coordinates, or None on failure.
        """
        entity = selection.entity

        if isinstance(entity, adsk.fusion.SketchPoint):
            # SketchPoint: use worldGeometry (selection.point gives wrong coordinates)
            if hasattr(entity, 'worldGeometry'):
                world_geom = entity.worldGeometry
                return adsk.core.Point3D.create(world_geom.x, world_geom.y, world_geom.z)
            # Fallback: apply sketch transform + occurrence transform
            geom = entity.geometry
            parent_sketch = entity.parentSketch
            if parent_sketch:
                sketch_transform = parent_sketch.transform
                world_point = adsk.core.Point3D.create(geom.x, geom.y, geom.z)
                world_point.transformBy(sketch_transform)
                if hasattr(entity, 'assemblyContext') and entity.assemblyContext is not None:
                    occ_transform = entity.assemblyContext.transform2
                    world_point.transformBy(occ_transform)
                return world_point
            return adsk.core.Point3D.create(geom.x, geom.y, geom.z)

        # For other entity types (BRepVertex, etc.), selection.point is correct
        snap_point = selection.point
        if snap_point:
            return adsk.core.Point3D.create(snap_point.x, snap_point.y, snap_point.z)
        return None

    def get_face_centroid_and_normal(self, face):
        """
        Get face centroid and outward normal in world space.
        Returns (centroid: Point3D, normal: Vector3D) or (None, None) on failure.
        """
        evaluator = face.evaluator

        # Get the parametric range and calculate center
        param_range = evaluator.parametricRange()
        u_center = (param_range.minPoint.x + param_range.maxPoint.x) / 2
        v_center = (param_range.minPoint.y + param_range.maxPoint.y) / 2

        ret, centroid = evaluator.getPointAtParameter(adsk.core.Point2D.create(u_center, v_center))
        if not ret:
            # Fallback to bounding box center
            bbox = face.boundingBox
            centroid = adsk.core.Point3D.create(
                (bbox.minPoint.x + bbox.maxPoint.x) / 2,
                (bbox.minPoint.y + bbox.maxPoint.y) / 2,
                (bbox.minPoint.z + bbox.maxPoint.z) / 2
            )

        ret, normal = evaluator.getNormalAtPoint(centroid)
        if not ret:
            normal = adsk.core.Vector3D.create(0, 0, 1)

        normal.normalize()
        return centroid, normal

    def build_transform_z_to_vector(self, target_vector, translation_point):
        """
        Build a Matrix3D that rotates Z-axis to align with target_vector,
        then translates to translation_point.
        """
        z_axis = adsk.core.Vector3D.create(0, 0, 1)
        transform = adsk.core.Matrix3D.create()

        dot = z_axis.dotProduct(target_vector)
        if abs(dot) < 0.9999:
            # Need rotation
            rotation_axis = z_axis.crossProduct(target_vector)
            rotation_axis.normalize()
            angle = math.acos(max(-1, min(1, dot)))
            transform.setToRotation(angle, rotation_axis, adsk.core.Point3D.create(0, 0, 0))
        elif dot < 0:
            # Opposite direction - rotate 180 degrees around X
            transform.setToRotation(math.pi, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Point3D.create(0, 0, 0))

        # Add translation
        current = transform.translation
        transform.translation = adsk.core.Vector3D.create(
            current.x + translation_point.x,
            current.y + translation_point.y,
            current.z + translation_point.z
        )
        return transform

    def are_faces_parallel(self, face1, face2, tolerance=0.01):
        """Check if two faces are parallel by comparing their normals."""
        _, normal1 = self.get_face_centroid_and_normal(face1)
        _, normal2 = self.get_face_centroid_and_normal(face2)

        if normal1 is None or normal2 is None:
            return False

        # Faces are parallel if normals are parallel (same or opposite direction)
        dot = abs(normal1.dotProduct(normal2))
        return dot > (1.0 - tolerance)

    def are_faces_orthogonal(self, face1, face2, tolerance=0.01):
        """Check if two faces are orthogonal (perpendicular) by comparing their normals."""
        _, normal1 = self.get_face_centroid_and_normal(face1)
        _, normal2 = self.get_face_centroid_and_normal(face2)

        if normal1 is None or normal2 is None:
            return False

        # Faces are orthogonal if normals are perpendicular (dot product ≈ 0)
        dot = abs(normal1.dotProduct(normal2))
        return dot < tolerance

    # -------------------------------------------------------------------------
    # Preview Indicator Creation
    # -------------------------------------------------------------------------

    def _create_cylinder_at_position(self, position, normal, color_name):
        """
        Create a colored cylinder indicator at the given position, aligned with the normal.
        This is the core indicator creation method used by other indicator methods.
        """
        if position is None or normal is None:
            return

        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        root = design.rootComponent

        try:
            # Create sketch and circle at origin (on XY plane)
            sketch = root.sketches.add(root.xYConstructionPlane)
            sketch.sketchCurves.sketchCircles.addByCenterRadius(
                adsk.core.Point3D.create(0, 0, 0),
                self.INDICATOR_CYLINDER_RADIUS
            )
            self.preview_sketches.append(sketch.entityToken)

            # Extrude in positive Z direction
            prof = sketch.profiles.item(0)
            ext_input = root.features.extrudeFeatures.createInput(
                prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
            )
            ext_input.setDistanceExtent(
                False,
                adsk.core.ValueInput.createByReal(self.INDICATOR_CYLINDER_HEIGHT)
            )
            extrude = root.features.extrudeFeatures.add(ext_input)
            self.preview_features.append(extrude.entityToken)
            body = extrude.bodies.item(0)

            # Transform to position and orientation (Z-axis aligned with normal)
            transform = self.build_transform_z_to_vector(normal, position)

            bodies_coll = adsk.core.ObjectCollection.create()
            bodies_coll.add(body)
            move_input = root.features.moveFeatures.createInput2(bodies_coll)
            move_input.defineAsFreeMove(transform)
            move_feature = root.features.moveFeatures.add(move_input)
            self.preview_features.append(move_feature.entityToken)

            # Apply color
            self.apply_appearance_to_body(body, color_name)

        except Exception as e:
            log(f"Error creating cylinder indicator: {e}", force_console=True)

    def create_cylinder_indicator(self, face, color_name):
        """Create a small colored cylinder at the face centroid aligned with the normal."""
        if face is None:
            return
        centroid, normal = self.get_face_centroid_and_normal(face)
        self._create_cylinder_at_position(centroid, normal, color_name)

    def create_edge_face_indicator(self, edge_entity, face, color_name):
        """Create a cylinder indicator at the edge midpoint, oriented by the face normal."""
        if edge_entity is None or face is None:
            return

        # Get the position from the edge (midpoint for edges, geometry for points)
        position = None
        if isinstance(edge_entity, adsk.fusion.BRepEdge):
            evaluator = edge_entity.evaluator
            ret, start_param, end_param = evaluator.getParameterExtents()
            if ret:
                mid_param = (start_param + end_param) / 2
                ret, position = evaluator.getPointAtParameter(mid_param)
        elif isinstance(edge_entity, adsk.fusion.BRepVertex):
            position = edge_entity.geometry
        elif hasattr(edge_entity, 'geometry'):
            position = edge_entity.geometry
        elif isinstance(edge_entity, adsk.fusion.JointOrigin):
            position = edge_entity.geometry.origin

        if position is None:
            return

        # Get the face normal for orientation
        _, normal = self.get_face_centroid_and_normal(face)
        self._create_cylinder_at_position(position, normal, color_name)

    def create_sphere_at_position(self, position, color_name):
        """Create a colored sphere indicator at the given world position."""
        if position is None:
            return

        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        root = design.rootComponent

        try:
            # Create a sphere by revolving a semicircle on XZ plane
            sketch = root.sketches.add(root.xZConstructionPlane)
            self.preview_sketches.append(sketch.entityToken)

            arcs = sketch.sketchCurves.sketchArcs
            lines = sketch.sketchCurves.sketchLines

            radius = self.INDICATOR_SPHERE_RADIUS
            start_point = adsk.core.Point3D.create(0, radius, 0)
            end_point = adsk.core.Point3D.create(0, -radius, 0)
            center_point = adsk.core.Point3D.create(0, 0, 0)

            # Create semicircle arc and closing line
            arcs.addByCenterStartSweep(center_point, start_point, math.pi)
            lines.addByTwoPoints(end_point, start_point)

            # Revolve 360 degrees around the axis line
            prof = sketch.profiles.item(0)
            revolve_input = root.features.revolveFeatures.createInput(
                prof,
                sketch.sketchCurves.sketchLines.item(0),
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation
            )
            revolve_input.setAngleExtent(False, adsk.core.ValueInput.createByReal(2 * math.pi))
            revolve = root.features.revolveFeatures.add(revolve_input)
            self.preview_features.append(revolve.entityToken)
            body = revolve.bodies.item(0)

            # Move to target position
            if abs(position.x) > 0.0001 or abs(position.y) > 0.0001 or abs(position.z) > 0.0001:
                bodies_coll = adsk.core.ObjectCollection.create()
                bodies_coll.add(body)
                move_input = root.features.moveFeatures.createInput2(bodies_coll)

                transform = adsk.core.Matrix3D.create()
                transform.translation = adsk.core.Vector3D.create(position.x, position.y, position.z)
                move_input.defineAsFreeMove(transform)

                move_feature = root.features.moveFeatures.add(move_input)
                self.preview_features.append(move_feature.entityToken)

            # Apply color
            self.apply_appearance_to_body(body, color_name)

        except Exception as e:
            log(f"Error creating sphere at position: {e}", force_console=True)
            log(traceback.format_exc(), force_console=True)

    def create_sphere_indicator(self, point_entity, color_name):
        """Create a colored sphere indicator at the given point entity."""
        if point_entity is None:
            return

        # Get the position from the point entity
        position = None
        entity_type = type(point_entity).__name__

        if isinstance(point_entity, adsk.fusion.BRepVertex):
            position = point_entity.geometry
        elif isinstance(point_entity, adsk.fusion.SketchPoint):
            position = point_entity.geometry
        elif isinstance(point_entity, adsk.fusion.ConstructionPoint):
            position = point_entity.geometry
        elif isinstance(point_entity, adsk.fusion.JointOrigin):
            position = point_entity.geometry.origin
        elif hasattr(point_entity, 'geometry'):
            geom = point_entity.geometry
            if isinstance(geom, adsk.core.Point3D):
                position = geom
            elif hasattr(geom, 'origin'):
                position = geom.origin

        if position is None:
            log(f"Sphere indicator: Could not get position from {entity_type}", force_console=True)
            return

        # Transform position to world coordinates if it's a proxy
        if hasattr(point_entity, 'assemblyContext') and point_entity.assemblyContext is not None:
            transform = point_entity.assemblyContext.transform2
            position = position.copy()
            position.transformBy(transform)

        self.create_sphere_at_position(position, color_name)

    def create_center_plane_indicator(self, face1, face2, color_name):
        """Create a thin tall box representing the center plane between two parallel faces."""
        if face1 is None or face2 is None:
            return

        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        root = design.rootComponent

        centroid1, normal1 = self.get_face_centroid_and_normal(face1)
        centroid2, _ = self.get_face_centroid_and_normal(face2)

        if centroid1 is None or centroid2 is None:
            return

        try:
            # Calculate center point between the two face centroids
            center = adsk.core.Point3D.create(
                (centroid1.x + centroid2.x) / 2,
                (centroid1.y + centroid2.y) / 2,
                (centroid1.z + centroid2.z) / 2
            )

            # Get body bounding box to size the plane
            if face1.assemblyContext is not None:
                bbox = face1.assemblyContext.boundingBox
            else:
                bbox = face1.body.boundingBox

            body_height = bbox.maxPoint.z - bbox.minPoint.z
            body_width_x = bbox.maxPoint.x - bbox.minPoint.x
            body_width_y = bbox.maxPoint.y - bbox.minPoint.y

            # Size plane relative to body
            plane_height = max(body_height, body_width_x, body_width_y) * 1.2
            plane_width = max(body_width_x, body_width_y) * 0.8

            # Create sketch with rectangle at origin
            sketch = root.sketches.add(root.xYConstructionPlane)
            self.preview_sketches.append(sketch.entityToken)
            lines = sketch.sketchCurves.sketchLines
            half_w, half_h = plane_width / 2, plane_height / 2

            p1 = adsk.core.Point3D.create(-half_w, -half_h, 0)
            p2 = adsk.core.Point3D.create(half_w, -half_h, 0)
            p3 = adsk.core.Point3D.create(half_w, half_h, 0)
            p4 = adsk.core.Point3D.create(-half_w, half_h, 0)
            lines.addByTwoPoints(p1, p2)
            lines.addByTwoPoints(p2, p3)
            lines.addByTwoPoints(p3, p4)
            lines.addByTwoPoints(p4, p1)

            # Extrude thin
            prof = sketch.profiles.item(0)
            ext_input = root.features.extrudeFeatures.createInput(
                prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
            )
            ext_input.setSymmetricExtent(
                adsk.core.ValueInput.createByReal(self.INDICATOR_PLANE_THICKNESS / 2), True
            )
            extrude = root.features.extrudeFeatures.add(ext_input)
            self.preview_features.append(extrude.entityToken)
            body = extrude.bodies.item(0)

            # Transform: Z-axis aligns with face normal, position at center
            # This makes the plane parallel to the selected faces, positioned at midpoint
            transform = self.build_transform_z_to_vector(normal1, center)
            bodies_coll = adsk.core.ObjectCollection.create()
            bodies_coll.add(body)
            move_input = root.features.moveFeatures.createInput2(bodies_coll)
            move_input.defineAsFreeMove(transform)
            move_feature = root.features.moveFeatures.add(move_input)
            self.preview_features.append(move_feature.entityToken)

            # Apply color
            self.apply_appearance_to_body(body, color_name)

        except Exception as e:
            log(f"Error creating center plane indicator: {e}", force_console=True)

    # -------------------------------------------------------------------------
    # Joint Creation
    # -------------------------------------------------------------------------

    # Set to True to enable hierarchy logging for debugging joint origin search
    DEBUG_HIERARCHY = False

    def _log_model_hierarchy(self, design):
        """Log the full model hierarchy including joint origins for debugging."""
        if not self.DEBUG_HIERARCHY:
            return

        self._log("=" * 60)
        self._log("MODEL HIERARCHY DUMP")
        self._log("=" * 60)

        root = design.rootComponent
        self._log(f"Root Component: {root.name}")

        # Log root component joint origins
        if root.jointOrigins.count > 0:
            self._log(f"  Joint Origins ({root.jointOrigins.count}):")
            for i in range(root.jointOrigins.count):
                jo = root.jointOrigins.item(i)
                self._log(f"    - '{jo.name}'")
        else:
            self._log("  Joint Origins: (none)")

        # Log root component joints
        if root.joints.count > 0:
            self._log(f"  Joints ({root.joints.count}):")
            for i in range(root.joints.count):
                j = root.joints.item(i)
                self._log(f"    - '{j.name}'")

        # Log occurrences recursively
        if root.occurrences.count > 0:
            self._log(f"  Occurrences ({root.occurrences.count}):")
            self._log_occurrences_hierarchy(root.occurrences, indent=2)
        else:
            self._log("  Occurrences: (none)")

        self._log("=" * 60)

    def _log_occurrences_hierarchy(self, occurrences, indent=0):
        """Recursively log occurrences and their joint origins."""
        prefix = "  " * indent
        for i in range(occurrences.count):
            occ = occurrences.item(i)
            comp = occ.component
            self._log(f"{prefix}- Occurrence: '{occ.name}' (Component: '{comp.name}')")

            # Log joint origins on this component
            if comp.jointOrigins.count > 0:
                self._log(f"{prefix}  Joint Origins ({comp.jointOrigins.count}):")
                for j in range(comp.jointOrigins.count):
                    jo = comp.jointOrigins.item(j)
                    self._log(f"{prefix}    - '{jo.name}'")

            # Log sketches (they may contain points used for joint origins)
            if comp.sketches.count > 0:
                sketch_names = [comp.sketches.item(k).name for k in range(comp.sketches.count)]
                self._log(f"{prefix}  Sketches: {sketch_names}")

            # Log construction points
            if comp.constructionPoints.count > 0:
                cp_names = [comp.constructionPoints.item(k).name for k in range(comp.constructionPoints.count)]
                self._log(f"{prefix}  Construction Points: {cp_names}")

            # Recurse into child occurrences
            if occ.childOccurrences.count > 0:
                self._log(f"{prefix}  Child Occurrences ({occ.childOccurrences.count}):")
                self._log_occurrences_hierarchy(occ.childOccurrences, indent + 2)

    def _find_joint_origin_by_name(self, design, joint_origin_name):
        """
        Find a joint origin by name in the model hierarchy.
        Searches all occurrences for a joint origin with the matching name.
        Returns the joint origin in assembly context, or None if not found.
        """
        root = design.rootComponent

        # Check root component first
        for i in range(root.jointOrigins.count):
            jo = root.jointOrigins.item(i)
            if jo.name == joint_origin_name:
                return jo

        # Search all occurrences recursively
        return self._search_occurrences_for_joint_origin(root.occurrences, joint_origin_name)

    def _search_occurrences_for_joint_origin(self, occurrences, joint_origin_name):
        """Recursively search occurrences for a joint origin by name."""
        for i in range(occurrences.count):
            occ = occurrences.item(i)
            comp = occ.component

            # Check joint origins on this component
            for j in range(comp.jointOrigins.count):
                jo = comp.jointOrigins.item(j)
                if jo.name == joint_origin_name:
                    # Return in assembly context
                    return jo.createForAssemblyContext(occ)

            # Check child occurrences
            if occ.childOccurrences.count > 0:
                result = self._search_occurrences_for_joint_origin(occ.childOccurrences, joint_origin_name)
                if result:
                    return result

        return None



    def _create_rigid_joint(self, design, joint_origin, face, joint_name, offset_z=0):
        """
        Create a rigid joint between a joint origin and a face.
        Joint is created at root component level.
        offset_z: Optional Z-axis offset (default 0).
        Returns the joint, or None on failure.
        """
        root = design.rootComponent
        joints = root.joints

        # Check if joint already exists by name
        existing = joints.itemByName(joint_name)
        if existing:
            return existing

        # Check if joint origin is already in use
        if self._is_joint_origin_in_use(design, joint_origin):
            return None

        try:
            # Create joint geometry from the face (at center point)
            face_geometry = adsk.fusion.JointGeometry.createByPlanarFace(
                face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint
            )

            joint_input = joints.createInput(joint_origin, face_geometry)
            joint_input.setAsRigidJointMotion()

            # Apply Z offset if specified
            if abs(offset_z) > 0.0001:
                joint_input.offset = adsk.core.ValueInput.createByReal(offset_z)

            new_joint = joints.add(joint_input)
            new_joint.name = joint_name
            new_joint.isLightBulbOn = False

            return new_joint

        except Exception as e:
            self._log_error(f"Creating joint '{joint_name}': {e}", indent=True)
            return None

    def _create_single_face_joint(self, design, anchor):
        """Create a joint for a single-face anchor."""
        anchor_id = anchor['id']
        anchor_name = anchor['name']
        joint_origin_name = anchor.get('joint_origin_name', anchor_name)

        # Get the selected face token
        token = self.selected_face_tokens.get(anchor_id)
        if not token:
            return False

        # Find the face entity
        entities = design.findEntityByToken(token)
        if not entities or len(entities) == 0:
            log(f"  Could not find face for {anchor_name}", force_console=True)
            return False

        face = entities[0]

        # Find the joint origin
        joint_origin = self._find_joint_origin_by_name(design, joint_origin_name)
        if not joint_origin:
            log(f"  Joint origin '{joint_origin_name}' not found", force_console=True)
            return False

        # Create the joint
        joint_name = f"{anchor_name} Joint"
        joint = self._create_rigid_joint(design, joint_origin, face, joint_name)

        return joint is not None

    def _create_point_joint(self, design, anchor):
        """Create a joint for a point-based anchor."""
        anchor_id = anchor['id']
        anchor_name = anchor['name']
        joint_origin_name = anchor.get('joint_origin_name', anchor_name)
        flip = anchor.get('flip', False)

        # Get the selected point token
        token = self.selected_face_tokens.get(anchor_id)
        if not token:
            return False

        # Find the point entity
        entities = design.findEntityByToken(token)
        if not entities or len(entities) == 0:
            return False

        point_entity = entities[0]

        # Find the joint origin
        joint_origin = self._find_joint_origin_by_name(design, joint_origin_name)
        if not joint_origin:
            return False

        # Create the joint
        joint_name = f"{anchor_name} Joint"
        joint = self._create_rigid_joint_from_point(design, joint_origin, point_entity, joint_name, anchor_id, flip=flip)

        return joint is not None

    def _create_multi_point_joints(self, design, anchor):
        """Create joints for all selected points in a multi_point anchor."""
        anchor_id = anchor['id']
        joint_origin_prefix = anchor.get('joint_origin_name_prefix', anchor['name'])
        count = anchor.get('count', 9)

        for i in range(1, count + 1):
            token_key = f"{anchor_id}_{i}"
            token = self.selected_face_tokens.get(token_key)
            if not token:
                continue

            # Find the point entity
            entities = design.findEntityByToken(token)
            if not entities or len(entities) == 0:
                continue

            point_entity = entities[0]

            # Find the joint origin (e.g., "Zero Point Attachment 1")
            joint_origin_name = f"{joint_origin_prefix} {i}"
            joint_origin = self._find_joint_origin_by_name(design, joint_origin_name)
            if not joint_origin:
                continue

            # Create the joint
            joint_name = f"{joint_origin_name} Joint"
            self._create_rigid_joint_from_point(design, joint_origin, point_entity, joint_name, token_key)

    def _is_joint_origin_in_use(self, design, joint_origin):
        """Check if a joint origin is already participating in a joint."""
        root = design.rootComponent
        for i in range(root.joints.count):
            joint = root.joints.item(i)
            # Check both geometry inputs of the joint
            if joint.geometryOrOriginOne == joint_origin or joint.geometryOrOriginTwo == joint_origin:
                return True
        return False

    def _create_rigid_joint_from_point(self, design, joint_origin, point_entity, joint_name, anchor_id, flip=False):
        """
        Create a rigid joint between a joint origin and a point entity.
        Handles snap points on edges/faces.
        flip: If True, flip the joint's Z axis (180 degree rotation).
        Returns the joint, or None on failure.
        """
        root = design.rootComponent
        joints = root.joints

        # Check if joint already exists by name
        existing = joints.itemByName(joint_name)
        if existing:
            return existing

        # Check if joint origin is already in use
        if self._is_joint_origin_in_use(design, joint_origin):
            return None

        try:
            point_geometry = None

            # Check entity type and create appropriate joint geometry
            if isinstance(point_entity, adsk.fusion.JointOrigin):
                point_geometry = adsk.fusion.JointGeometry.createByJointOrigin(point_entity)
            elif isinstance(point_entity, (adsk.fusion.BRepEdge, adsk.fusion.BRepFace)):
                # Edge or Face selected via snap point - use the snap point location
                snap_point = self.selected_snap_points.get(anchor_id)
                if snap_point:
                    # Create joint geometry using the edge/face with the snap point
                    if isinstance(point_entity, adsk.fusion.BRepEdge):
                        point_geometry = adsk.fusion.JointGeometry.createByNonPlanarFace(
                            point_entity, snap_point, adsk.fusion.JointKeyPointTypes.CenterKeyPoint
                        )
                    else:
                        # For faces, use planar face geometry with the snap point
                        point_geometry = adsk.fusion.JointGeometry.createByPlanarFace(
                            point_entity, snap_point, adsk.fusion.JointKeyPointTypes.CenterKeyPoint
                        )
                else:
                    log(f"  No snap point found for edge/face selection", force_console=True)
                    return None
            elif hasattr(point_entity, 'geometry'):
                # SketchPoint, ConstructionPoint, Vertex
                point_geometry = adsk.fusion.JointGeometry.createByPoint(point_entity)
            else:
                log(f"  Unsupported entity type for point joint: {type(point_entity)}", force_console=True)
                return None

            if not point_geometry:
                self._log_error("Could not create joint geometry", indent=True)
                return None

            joint_input = joints.createInput(joint_origin, point_geometry)
            joint_input.setAsRigidJointMotion()

            # Flip the Z axis if requested
            if flip:
                joint_input.isFlipped = True

            new_joint = joints.add(joint_input)
            new_joint.name = joint_name
            new_joint.isLightBulbOn = False

            return new_joint

        except Exception as e:
            self._log_error(f"Creating joint '{joint_name}': {e}", indent=True)
            return None

    def _create_edge_face_joint(self, design, anchor):
        """Create a joint for an edge_face anchor (edge/point for position, face for Z-axis)."""
        anchor_id = anchor['id']
        anchor_name = anchor['name']
        joint_origin_name = anchor.get('joint_origin_name', anchor_name)
        rotation_degrees = anchor.get('rotation', 0)  # Rotation in degrees

        # Get the selected edge and face tokens
        edge_token = self.selected_face_tokens.get(f"{anchor_id}_edge")
        face_token = self.selected_face_tokens.get(f"{anchor_id}_face")

        if not edge_token or not face_token:
            return False

        # Find the entities
        edge_entities = design.findEntityByToken(edge_token)
        face_entities = design.findEntityByToken(face_token)

        if not edge_entities or not face_entities or len(edge_entities) == 0 or len(face_entities) == 0:
            log(f"  Could not find edge or face for {anchor_name}", force_console=True)
            return False

        edge_entity = edge_entities[0]
        face = face_entities[0]

        # Find the joint origin
        joint_origin = self._find_joint_origin_by_name(design, joint_origin_name)
        if not joint_origin:
            log(f"  Joint origin '{joint_origin_name}' not found", force_console=True)
            return False

        # Create the joint
        joint_name = f"{anchor_name} Joint"
        joint = self._create_rigid_joint_from_edge_face(design, joint_origin, edge_entity, face, joint_name, anchor_id, rotation_degrees)

        return joint is not None

    def _get_edge_midpoint(self, edge):
        """Get the midpoint of an edge. Returns Point3D or None."""
        evaluator = edge.evaluator
        ret, start_param, end_param = evaluator.getParameterExtents()
        if not ret:
            return None
        mid_param = (start_param + end_param) / 2
        ret, midpoint = evaluator.getPointAtParameter(mid_param)
        return midpoint if ret else None

    def _get_face_local_axes(self, face, face_normal):
        """
        Get the local X and Y axes for a face's coordinate system.
        Uses surface derivatives when available, with fallback calculation.
        Returns (x_axis, y_axis) as normalized Vector3D.
        """
        face_evaluator = face.evaluator
        param_range = face_evaluator.parametricRange()
        u_center = (param_range.minPoint.x + param_range.maxPoint.x) / 2
        v_center = (param_range.minPoint.y + param_range.maxPoint.y) / 2
        center_param = adsk.core.Point2D.create(u_center, v_center)

        ret, u_directions, v_directions = face_evaluator.getFirstDerivatives([center_param])
        if ret and len(u_directions) > 0 and len(v_directions) > 0:
            x_axis = u_directions[0]
            y_axis = v_directions[0]
            x_axis.normalize()
            y_axis.normalize()
            return x_axis, y_axis

        # Fallback: calculate axes from face normal
        z_axis = face_normal.copy()
        z_axis.normalize()
        world_x = adsk.core.Vector3D.create(1, 0, 0)
        world_y = adsk.core.Vector3D.create(0, 1, 0)
        if abs(z_axis.dotProduct(world_x)) < 0.9:
            x_axis = z_axis.crossProduct(world_x)
        else:
            x_axis = z_axis.crossProduct(world_y)
        x_axis.normalize()
        y_axis = z_axis.crossProduct(x_axis)
        y_axis.normalize()
        return x_axis, y_axis

    def _create_joint_geometry_from_face(self, face):
        """Create joint geometry from a planar face, with fallback for proxy faces."""
        try:
            return adsk.fusion.JointGeometry.createByPlanarFace(
                face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint
            )
        except Exception:
            if face.assemblyContext is not None:
                native_face = face.nativeObject
                return adsk.fusion.JointGeometry.createByPlanarFace(
                    native_face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint
                )
            raise

    def _create_joint_geometry_from_entity(self, entity):
        """Create joint geometry from various entity types. Returns geometry or None."""
        if isinstance(entity, adsk.fusion.BRepVertex):
            return adsk.fusion.JointGeometry.createByPoint(entity)
        elif isinstance(entity, adsk.fusion.JointOrigin):
            return adsk.fusion.JointGeometry.createByJointOrigin(entity)
        elif isinstance(entity, (adsk.fusion.SketchPoint, adsk.fusion.ConstructionPoint)):
            return adsk.fusion.JointGeometry.createByPoint(entity)
        return None

    def _create_rigid_joint_from_edge_face(self, design, joint_origin, edge_entity, face, joint_name, anchor_id, rotation_degrees=0):
        """
        Create a rigid joint using edge/point for position and face for Z-axis orientation.
        rotation_degrees: Rotation angle around the Z-axis in degrees.
        Returns the joint, or None on failure.
        """
        root = design.rootComponent
        joints = root.joints

        # Check if joint already exists
        existing = joints.itemByName(joint_name)
        if existing:
            return existing

        try:
            # Handle BRepEdge specially - needs offset calculations
            if isinstance(edge_entity, adsk.fusion.BRepEdge):
                midpoint = self._get_edge_midpoint(edge_entity)
                if midpoint is None:
                    log(f"  Could not get edge midpoint", force_console=True)
                    return None

                face_center, face_normal = self.get_face_centroid_and_normal(face)
                if face_normal is None:
                    log(f"  Could not get face normal", force_console=True)
                    return None

                target_geometry = self._create_joint_geometry_from_face(face)

                # Calculate offset from face center to edge midpoint in local coordinates
                offset_vec = adsk.core.Vector3D.create(
                    midpoint.x - face_center.x,
                    midpoint.y - face_center.y,
                    midpoint.z - face_center.z
                )
                x_axis, y_axis = self._get_face_local_axes(face, face_normal)
                x_offset = offset_vec.dotProduct(x_axis)
                y_offset = offset_vec.dotProduct(y_axis)
                z_offset = offset_vec.dotProduct(face_normal)

                # Create joint with offsets and rotation
                rotation_radians = math.radians(rotation_degrees)
                joint_input = joints.createInput(joint_origin, target_geometry)
                joint_input.setAsRigidJointMotion()

                if abs(z_offset) > 0.0001:
                    joint_input.offset = adsk.core.ValueInput.createByReal(z_offset)
                if abs(rotation_radians) > 0.0001:
                    joint_input.angle = adsk.core.ValueInput.createByReal(rotation_radians)

                new_joint = joints.add(joint_input)
                new_joint.name = joint_name
                new_joint.isLightBulbOn = False

                # Set X/Y offsets after creation (only available on Joint object)
                if abs(x_offset) > 0.0001:
                    new_joint.offsetX.value = x_offset
                if abs(y_offset) > 0.0001:
                    new_joint.offsetY.value = y_offset

                return new_joint

            # Handle other entity types (vertex, joint origin, points)
            target_geometry = self._create_joint_geometry_from_entity(edge_entity)
            if target_geometry is None:
                log(f"  Unsupported entity type: {type(edge_entity)}", force_console=True)
                return None

            joint_input = joints.createInput(joint_origin, target_geometry)
            joint_input.setAsRigidJointMotion()

            new_joint = joints.add(joint_input)
            new_joint.name = joint_name
            new_joint.isLightBulbOn = False

            return new_joint

        except Exception as e:
            log(f"  ERROR creating joint '{joint_name}': {e}", force_console=True)
            return None

    def _create_dual_face_joint(self, design, anchor):
        """Create a joint for a dual-face (center plane) anchor."""
        anchor_id = anchor['id']
        anchor_name = anchor['name']
        joint_origin_name = anchor.get('joint_origin_name', anchor_name)

        # Get the selected face tokens
        face1_token = self.selected_face_tokens.get(f"{anchor_id}_face1")
        face2_token = self.selected_face_tokens.get(f"{anchor_id}_face2")

        if not face1_token or not face2_token:
            return False

        # Find the face entities
        entities1 = design.findEntityByToken(face1_token)
        entities2 = design.findEntityByToken(face2_token)

        if not entities1 or not entities2 or len(entities1) == 0 or len(entities2) == 0:
            log(f"  Could not find faces for {anchor_name}", force_console=True)
            return False

        face1 = entities1[0]
        face2 = entities2[0]

        # Find the joint origin
        joint_origin = self._find_joint_origin_by_name(design, joint_origin_name)
        if not joint_origin:
            log(f"  Joint origin '{joint_origin_name}' not found", force_console=True)
            return False

        # Calculate center point and offset
        centroid1, normal1 = self.get_face_centroid_and_normal(face1)
        centroid2, normal2 = self.get_face_centroid_and_normal(face2)

        if centroid1 is None or centroid2 is None:
            log(f"  Could not get face centroids for {anchor_name}", force_console=True)
            return False

        # Calculate offset from face1 to center (distance along normal)
        offset_vec = adsk.core.Vector3D.create(
            (centroid2.x - centroid1.x) / 2,
            (centroid2.y - centroid1.y) / 2,
            (centroid2.z - centroid1.z) / 2
        )
        offset_distance = offset_vec.dotProduct(normal1)

        # Create joint with offset
        joint_name = f"{anchor_name} Joint"
        joint = self._create_rigid_joint(design, joint_origin, face1, joint_name, offset_z=offset_distance)

        return joint is not None

    # -------------------------------------------------------------------------
    # UI Input Creation
    # -------------------------------------------------------------------------

    def create_anchor_inputs(self, inputs, anchors):
        """Create selection inputs for the given anchors."""
        for anchor in anchors:
            group = inputs.addGroupCommandInput(f"{anchor['id']}_group", anchor['name'])
            group.isExpanded = True
            children = group.children

            # Color indicator
            color_name = anchor.get('appearance', 'Gray')
            r, g, b = self.COLOR_RGB.get(color_name, (128, 128, 128))
            children.addTextBoxCommandInput(
                f"{anchor['id']}_color", "Color",
                f'<div style="width:20px;height:20px;background-color:rgb({r},{g},{b});border:1px solid #333;"></div>',
                1, True
            )

            is_dual = anchor.get('dual_selection', False)
            selection_type = anchor.get('selection_type', 'face')

            if is_dual:
                # Instruction + two face pickers with picked indicators
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_instruction", "",
                    "Select two parallel faces", 1, True
                )

                sel1 = children.addSelectionInput(
                    f"{anchor['id']}_face1", "Face 1",
                    f"Select first face for {anchor['name']}"
                )
                sel1.addSelectionFilter("Faces")
                sel1.setSelectionLimits(0, 1)
                # Picked indicator for face1
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_face1_picked", "", "", 1, True
                )

                sel2 = children.addSelectionInput(
                    f"{anchor['id']}_face2", "Face 2",
                    f"Select second face for {anchor['name']}"
                )
                sel2.addSelectionFilter("Faces")
                sel2.setSelectionLimits(0, 1)
                # Picked indicator for face2
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_face2_picked", "", "", 1, True
                )
            elif selection_type == 'point':
                # Point/origin picker with picked indicator
                sel = children.addSelectionInput(
                    anchor['id'], "Point",
                    f"Select point or origin for {anchor['name']}"
                )
                sel.addSelectionFilter("SketchPoints")
                sel.addSelectionFilter("ConstructionPoints")
                sel.addSelectionFilter("JointOrigins")
                sel.addSelectionFilter("Vertices")
                sel.setSelectionLimits(0, 1)
                # Picked indicator
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_picked", "", "", 1, True
                )
            elif selection_type == 'edge_face':
                # Edge/Point + Face picker (edge/point for position, face for Z-axis orientation)
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_instruction", "",
                    "Select edge/point (position) + face (Z-axis)", 1, True
                )

                sel_edge = children.addSelectionInput(
                    f"{anchor['id']}_edge", "Edge/Point",
                    f"Select edge or point for {anchor['name']} position"
                )
                sel_edge.addSelectionFilter("LinearEdges")
                sel_edge.addSelectionFilter("CircularEdges")
                sel_edge.addSelectionFilter("Edges")
                sel_edge.addSelectionFilter("Vertices")
                sel_edge.addSelectionFilter("SketchPoints")
                sel_edge.addSelectionFilter("ConstructionPoints")
                sel_edge.addSelectionFilter("JointOrigins")
                sel_edge.setSelectionLimits(0, 1)
                # Picked indicator for edge
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_edge_picked", "", "", 1, True
                )

                sel_face = children.addSelectionInput(
                    f"{anchor['id']}_face", "Face",
                    f"Select face for {anchor['name']} Z-axis orientation"
                )
                sel_face.addSelectionFilter("Faces")
                sel_face.setSelectionLimits(0, 1)
                # Picked indicator for face
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_face_picked", "", "", 1, True
                )
            elif selection_type == 'multi_point':
                # Multi-point picker with dropdown for number selection
                count = anchor.get('count', 9)

                # Dropdown for selecting which point number
                dropdown = children.addDropDownCommandInput(
                    f"{anchor['id']}_dropdown", "Number",
                    adsk.core.DropDownStyles.TextListDropDownStyle
                )
                for i in range(1, count + 1):
                    dropdown.listItems.add(str(i), i == 1)

                # Single point selector
                sel = children.addSelectionInput(
                    f"{anchor['id']}_select", "Point",
                    f"Select point for {anchor['name']}"
                )
                sel.addSelectionFilter("SketchPoints")
                sel.addSelectionFilter("ConstructionPoints")
                sel.addSelectionFilter("JointOrigins")
                sel.addSelectionFilter("Vertices")
                sel.setSelectionLimits(0, 1)

                # Status text showing which numbers are picked
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_status", "Picked", "None", 1, True
                )

                # Clear all button
                clear_btn = children.addBoolValueInput(f"{anchor['id']}_clear", "Clear All", False)
                clear_btn.isFullWidth = False
            else:
                # Single face picker with picked indicator
                sel = children.addSelectionInput(
                    anchor['id'], "Face",
                    f"Select face for {anchor['name']}"
                )
                sel.addSelectionFilter("Faces")
                sel.setSelectionLimits(0, 1)
                # Picked indicator
                children.addTextBoxCommandInput(
                    f"{anchor['id']}_picked", "", "", 1, True
                )

    def remove_anchor_inputs(self, inputs, anchors):
        """Remove selection inputs for the given anchors."""
        for anchor in anchors:
            group_input = inputs.itemById(f"{anchor['id']}_group")
            if group_input:
                group_input.deleteMe()

    def set_anchor_visibility(self, inputs, anchors, visible):
        """Show or hide anchor inputs."""
        for anchor in anchors:
            group_input = inputs.itemById(f"{anchor['id']}_group")
            if group_input:
                group_input.isVisible = visible

    # -------------------------------------------------------------------------
    # State Management
    # -------------------------------------------------------------------------

    def _get_entity_from_token(self, design, token):
        """Get entity from token string. Returns entity or None."""
        if not token:
            return None
        entities = design.findEntityByToken(token)
        if entities and len(entities) > 0:
            return entities[0]
        return None

    def clear_state(self):
        """Clear all tracked state (called on setup type change or dialog close)."""
        self.selected_face_tokens = {}
        self.selected_snap_points = {}
        self.appearance_cache = {}
        self.preview_sketches = []
        self.preview_features = []

    def delete_preview_geometry(self):
        """Delete all tracked preview geometry (sketches and features)."""
        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        if not design:
            return

        # Delete features first (they depend on sketches)
        for token in reversed(self.preview_features):
            try:
                entities = design.findEntityByToken(token)
                if entities and len(entities) > 0:
                    feature = entities[0]
                    if feature.isValid:
                        feature.deleteMe()
            except Exception:
                pass  # Continue cleanup even if one delete fails

        # Delete sketches
        for token in reversed(self.preview_sketches):
            try:
                entities = design.findEntityByToken(token)
                if entities and len(entities) > 0:
                    sketch = entities[0]
                    if sketch.isValid:
                        sketch.deleteMe()
            except Exception:
                pass  # Continue cleanup even if one delete fails

        self.preview_sketches = []
        self.preview_features = []

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def onCommandCreated(self, args):
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            inputs = cmd.commandInputs

            self.clear_state()

            # Setup type dropdown
            dropdown = inputs.addDropDownCommandInput(
                self.SETUP_TYPE_DROPDOWN_ID, "Setup Type",
                adsk.core.DropDownStyles.TextListDropDownStyle
            )
            dropdown.listItems.add(self.SETUP_TYPE_BODY, True)
            dropdown.listItems.add(self.SETUP_TYPE_VISE, False)
            dropdown.listItems.add(self.SETUP_TYPE_PLATE, False)

            # Create all anchor inputs upfront, hide non-active ones
            self.create_anchor_inputs(inputs, self.BODY_ANCHORS)
            self.create_anchor_inputs(inputs, self.VISE_ANCHORS)
            self.create_anchor_inputs(inputs, self.PLATE_ANCHORS)

            # Show only body anchors initially
            self.set_anchor_visibility(inputs, self.BODY_ANCHORS, True)
            self.set_anchor_visibility(inputs, self.VISE_ANCHORS, False)
            self.set_anchor_visibility(inputs, self.PLATE_ANCHORS, False)

            # Register handlers
            add_handler(cmd.execute, self.onCommandExecute, local_handlers=self.local_handlers)
            add_handler(cmd.executePreview, self.onExecutePreview, local_handlers=self.local_handlers)
            add_handler(cmd.inputChanged, self.onInputChanged, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.destroy, self.onCommandDestroy, local_handlers=self.local_handlers)

        except Exception as e:
            self._log_exception("onCommandCreated", e)

    def onInputChanged(self, args):
        try:
            event_args = adsk.core.InputChangedEventArgs.cast(args)
            changed_input = event_args.input
            inputs = event_args.firingEvent.sender.commandInputs

            # Handle setup type change
            if changed_input.id == self.SETUP_TYPE_DROPDOWN_ID:
                self._handle_setup_type_change(changed_input, inputs)
                return

            # Handle multi_point clear button
            if changed_input.id.endswith('_clear'):
                self._handle_multi_point_clear(changed_input, inputs)
                return

            # Handle face selection changes
            self._handle_face_selection_change(changed_input, inputs)

        except Exception as e:
            self._log_exception("onInputChanged", e)

    def _handle_setup_type_change(self, dropdown_input, inputs):
        """Handle setup type dropdown change."""
        selected_type = adsk.core.DropDownCommandInput.cast(dropdown_input).selectedItem.name

        self.clear_state()

        # Hide all anchor inputs, then show only the ones for the selected type
        self.set_anchor_visibility(inputs, self.BODY_ANCHORS, selected_type == self.SETUP_TYPE_BODY)
        self.set_anchor_visibility(inputs, self.VISE_ANCHORS, selected_type == self.SETUP_TYPE_VISE)
        self.set_anchor_visibility(inputs, self.PLATE_ANCHORS, selected_type == self.SETUP_TYPE_PLATE)

    def _handle_multi_point_clear(self, clear_input, inputs):
        """Handle clear button for multi_point anchors."""
        bool_input = adsk.core.BoolValueCommandInput.cast(clear_input)

        # Only act when button is checked
        if not bool_input.value:
            return

        # Get the anchor ID by removing '_clear' suffix
        anchor_id = clear_input.id[:-6]  # Remove '_clear'

        # Find the anchor to get its count
        for anchor in self.get_all_anchors():
            if anchor['id'] == anchor_id and anchor.get('selection_type') == 'multi_point':
                count = anchor.get('count', 9)

                # Clear all stored tokens for this multi_point anchor
                for i in range(1, count + 1):
                    token_key = f"{anchor_id}_{i}"
                    self.selected_face_tokens.pop(token_key, None)
                    self.selected_snap_points.pop(token_key, None)

                # Update status text
                self._update_multi_point_status(inputs, anchor_id, count)

                # Reset dropdown to 1
                dropdown = inputs.itemById(f"{anchor_id}_dropdown")
                if not dropdown:
                    group = inputs.itemById(f"{anchor_id}_group")
                    if group:
                        group = adsk.core.GroupCommandInput.cast(group)
                        dropdown = group.children.itemById(f"{anchor_id}_dropdown")
                if dropdown:
                    dropdown = adsk.core.DropDownCommandInput.cast(dropdown)
                    dropdown.listItems.item(0).isSelected = True

                break

        # Uncheck the button
        bool_input.value = False

    def _update_multi_point_status(self, inputs, anchor_id, count):
        """Update the status text for a multi_point anchor showing which numbers are picked."""
        picked_numbers = []
        for i in range(1, count + 1):
            token_key = f"{anchor_id}_{i}"
            if token_key in self.selected_face_tokens:
                picked_numbers.append(str(i))

        # Find and update status text
        status_input = inputs.itemById(f"{anchor_id}_status")
        if not status_input:
            group = inputs.itemById(f"{anchor_id}_group")
            if group:
                group = adsk.core.GroupCommandInput.cast(group)
                status_input = group.children.itemById(f"{anchor_id}_status")

        if status_input:
            text_input = adsk.core.TextBoxCommandInput.cast(status_input)
            if picked_numbers:
                text_input.formattedText = ', '.join(picked_numbers)
            else:
                text_input.formattedText = "None"

    def _handle_face_selection_change(self, changed_input, inputs):
        """Handle face selection input changes by dispatching to type-specific handlers."""
        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)

        # Get current anchors
        setup_dropdown = inputs.itemById(self.SETUP_TYPE_DROPDOWN_ID)
        current_type = adsk.core.DropDownCommandInput.cast(setup_dropdown).selectedItem.name
        anchors = self.get_anchors_for_type(current_type)

        for i, anchor in enumerate(anchors):
            is_dual = anchor.get('dual_selection', False)
            selection_type = anchor.get('selection_type', 'face')

            if is_dual:
                if self._handle_dual_selection(changed_input, inputs, design, anchor, anchors, i):
                    break
            elif selection_type == 'edge_face':
                if self._handle_edge_face_selection(changed_input, inputs, anchor, anchors, i):
                    break
            elif selection_type == 'multi_point':
                if self._handle_multi_point_selection(changed_input, inputs, anchor):
                    break
            else:
                if self._handle_single_face_selection(changed_input, inputs, design, anchor, anchors, i, current_type):
                    break

    def _handle_dual_selection(self, changed_input, inputs, design, anchor, anchors, index):
        """Handle dual-face selection (e.g., vise center). Returns True if handled."""
        face1_id = f"{anchor['id']}_face1"
        face2_id = f"{anchor['id']}_face2"

        if changed_input.id == face1_id:
            self._store_face_token(changed_input, face1_id, clear_after_store=True, inputs=inputs)
            self._set_focus_to_input(inputs, anchor['id'], face2_id)
            return True
        elif changed_input.id == face2_id:
            self._store_face_token(changed_input, face2_id, clear_after_store=True, inputs=inputs)
            self._validate_parallel_faces(design, face1_id, face2_id, anchor['name'])
            self._advance_to_next_anchor(inputs, anchors, index)
            return True
        return False

    def _handle_edge_face_selection(self, changed_input, inputs, anchor, anchors, index):
        """Handle edge/face selection (e.g., jaw positions). Returns True if handled."""
        edge_id = f"{anchor['id']}_edge"
        face_id = f"{anchor['id']}_face"

        if changed_input.id == edge_id:
            self._store_face_token(changed_input, edge_id, clear_after_store=True, inputs=inputs)
            self._set_focus_to_input(inputs, anchor['id'], face_id)
            return True
        elif changed_input.id == face_id:
            self._store_face_token(changed_input, face_id, clear_after_store=True, inputs=inputs)
            self._advance_to_next_anchor(inputs, anchors, index)
            return True
        return False

    def _handle_multi_point_selection(self, changed_input, inputs, anchor):
        """Handle multi-point selection (e.g., zero points). Returns True if handled."""
        select_id = f"{anchor['id']}_select"
        if changed_input.id != select_id:
            return False

        sel = adsk.core.SelectionCommandInput.cast(changed_input)
        if sel.selectionCount == 0:
            return True

        # Get current dropdown number
        dropdown = self._get_dropdown_for_anchor(inputs, anchor['id'])
        if not dropdown:
            return True

        current_number = int(dropdown.selectedItem.name)
        token_key = f"{anchor['id']}_{current_number}"
        selection = sel.selection(0)
        entity = selection.entity

        # Store token and world position
        if hasattr(entity, 'entityToken'):
            self.selected_face_tokens[token_key] = entity.entityToken

        world_point = self._get_world_point_from_selection(selection)
        if world_point:
            self.selected_snap_points[token_key] = world_point
        else:
            self.selected_snap_points.pop(token_key, None)

        sel.clearSelection()

        # Update status and auto-advance
        count = anchor.get('count', 9)
        self._update_multi_point_status(inputs, anchor['id'], count)
        if current_number < count:
            dropdown.listItems.item(current_number).isSelected = True

        return True

    def _handle_single_face_selection(self, changed_input, inputs, design, anchor, anchors, index, current_type):
        """Handle single-face selection. Returns True if handled."""
        if changed_input.id != anchor['id']:
            return False

        self._store_face_token(changed_input, anchor['id'], clear_after_store=True, inputs=inputs)

        # Vise validations for stock and jaw relationships
        validation_passed = True
        if current_type == self.SETUP_TYPE_VISE:
            validation_passed = self._validate_vise_face_relationships(design, anchor['id'], changed_input)

        if validation_passed:
            self._advance_to_next_anchor(inputs, anchors, index)
        return True

    def _get_dropdown_for_anchor(self, inputs, anchor_id):
        """Get the dropdown input for a multi_point anchor."""
        group = inputs.itemById(f"{anchor_id}_group")
        if group:
            group = adsk.core.GroupCommandInput.cast(group)
            dropdown = group.children.itemById(f"{anchor_id}_dropdown")
            if dropdown:
                return adsk.core.DropDownCommandInput.cast(dropdown)
        return None

    def _update_picked_indicator(self, inputs, input_id, picked):
        """Update the 'picked' text indicator for a selection input."""
        # Determine the picked indicator ID based on the input ID
        picked_id = f"{input_id}_picked"

        # Find the picked indicator - it might be in a group
        picked_input = inputs.itemById(picked_id)
        if not picked_input:
            # Try to find it in group children
            for anchor in self.get_all_anchors():
                group = inputs.itemById(f"{anchor['id']}_group")
                if group:
                    group = adsk.core.GroupCommandInput.cast(group)
                    picked_input = group.children.itemById(picked_id)
                    if picked_input:
                        break

        if picked_input:
            text_input = adsk.core.TextBoxCommandInput.cast(picked_input)
            if picked:
                text_input.formattedText = '<span style="color:#00aa00;font-weight:bold;">picked</span>'
            else:
                text_input.formattedText = ""


    def _clear_anchor_selection(self, inputs, token_key):
        """Clear the stored token and picked indicator for an anchor."""
        # Remove the stored token
        self.selected_face_tokens.pop(token_key, None)
        self.selected_snap_points.pop(token_key, None)

        # Clear the picked indicator
        self._update_picked_indicator(inputs, token_key, False)

    def _store_face_token(self, sel_input, token_key, clear_after_store=False, inputs=None):
        """Store or clear entity token and snap point based on selection state."""
        sel = adsk.core.SelectionCommandInput.cast(sel_input)
        if sel.selectionCount > 0:
            selection = sel.selection(0)
            entity = selection.entity

            # Store the entity token
            if hasattr(entity, 'entityToken'):
                self.selected_face_tokens[token_key] = entity.entityToken

            # Get world position for the selected point
            world_point = self._get_world_point_from_selection(selection)
            if world_point:
                self.selected_snap_points[token_key] = world_point
            else:
                self.selected_snap_points.pop(token_key, None)

            # Update the picked indicator
            if inputs:
                self._update_picked_indicator(inputs, token_key, True)

            # Clear the selection so the entity can be selected in other inputs
            if clear_after_store:
                sel.clearSelection()
        else:
            # Only clear stored token if we don't have clear_after_store behavior
            # (which means empty selection is intentional, not from our clearing)
            pass  # Don't clear - we might have cleared it ourselves

    def _set_focus_to_input(self, inputs, anchor_id, target_input_id):
        """Set focus to a selection input within an anchor group."""
        group = inputs.itemById(f"{anchor_id}_group")
        if group:
            group = adsk.core.GroupCommandInput.cast(group)
            target = group.children.itemById(target_input_id)
            if target:
                adsk.core.SelectionCommandInput.cast(target).hasFocus = True

    def _advance_to_next_anchor(self, inputs, anchors, current_index):
        """Advance focus to the next anchor's selection input."""
        next_index = current_index + 1
        if next_index >= len(anchors):
            return  # No more anchors

        next_anchor = anchors[next_index]
        next_is_dual = next_anchor.get('dual_selection', False)
        next_selection_type = next_anchor.get('selection_type', 'face')

        if next_is_dual:
            # Focus on first face input
            target_id = f"{next_anchor['id']}_face1"
        elif next_selection_type == 'edge_face':
            # Focus on edge input
            target_id = f"{next_anchor['id']}_edge"
        elif next_selection_type == 'point':
            # Focus on point input
            target_id = next_anchor['id']
        else:
            # Focus on single face input
            target_id = next_anchor['id']

        self._set_focus_to_input(inputs, next_anchor['id'], target_id)

    def _validate_parallel_faces(self, design, face1_id, face2_id, anchor_name):
        """Log warning if selected faces are not parallel."""
        if face1_id not in self.selected_face_tokens or face2_id not in self.selected_face_tokens:
            return

        entities1 = design.findEntityByToken(self.selected_face_tokens[face1_id])
        entities2 = design.findEntityByToken(self.selected_face_tokens[face2_id])

        if entities1 and entities2 and len(entities1) > 0 and len(entities2) > 0:
            if not self.are_faces_parallel(entities1[0], entities2[0]):
                self._log_warning(f"Faces are NOT parallel for {anchor_name}")

    def _validate_vise_face_relationships(self, design, changed_anchor_id, changed_input):
        """
        Validate geometric relationships between vise anchor faces.
        Returns True if valid, False if invalid (and clears the selection).
        """
        ui = adsk.core.Application.get().userInterface

        def get_face(anchor_id):
            return self._get_entity_from_token(design, self.selected_face_tokens.get(anchor_id))

        def reject_selection(message):
            """Clear the selection and show error message."""
            ui.messageBox(message, "Invalid Selection", adsk.core.MessageBoxButtonTypes.OKButtonType, adsk.core.MessageBoxIconTypes.WarningIconType)
            sel_input = adsk.core.SelectionCommandInput.cast(changed_input)
            sel_input.clearSelection()
            self.selected_face_tokens.pop(changed_anchor_id, None)

        # When Jaw 1 is selected, check Stock ⊥ Jaw 1
        if changed_anchor_id == self.VISE_JAW1_ID:
            stock_face = get_face(self.VISE_STOCK_ID)
            jaw1_face = get_face(self.VISE_JAW1_ID)
            if stock_face and jaw1_face:
                if not self.are_faces_orthogonal(stock_face, jaw1_face):
                    reject_selection("Jaw 1 must be perpendicular (orthogonal) to Stock Attachment.\n\nThe jaw face should be vertical while the stock face is horizontal.")
                    return False

        # When Jaw 2 is selected, check Jaw 1 ∥ Jaw 2
        elif changed_anchor_id == self.VISE_JAW2_ID:
            jaw1_face = get_face(self.VISE_JAW1_ID)
            jaw2_face = get_face(self.VISE_JAW2_ID)
            if jaw1_face and jaw2_face:
                if not self.are_faces_parallel(jaw1_face, jaw2_face):
                    reject_selection("Jaw 2 must be parallel to Jaw 1.\n\nBoth jaw faces should face the same direction (opposing sides of the vise).")
                    return False

        return True

    def onExecutePreview(self, args):
        """Create indicator geometry for all selected faces."""
        try:
            event_args = adsk.core.CommandEventArgs.cast(args)
            design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)

            # Delete any existing preview geometry before creating new
            self.delete_preview_geometry()

            for anchor in self.get_all_anchors():
                anchor_id = anchor['id']
                color_name = anchor.get('appearance', 'Red')
                is_dual = anchor.get('dual_selection', False)
                selection_type = anchor.get('selection_type', 'face')

                try:
                    if is_dual:
                        self._create_dual_indicator(design, anchor_id, color_name)
                    elif selection_type == 'edge_face':
                        self._create_edge_face_indicator(design, anchor_id, color_name)
                    elif selection_type == 'point':
                        self._create_point_indicator(design, anchor_id, color_name)
                    elif selection_type == 'multi_point':
                        count = anchor.get('count', 9)
                        self._create_multi_point_indicator(design, anchor_id, color_name, count)
                    else:
                        self._create_single_indicator(design, anchor_id, color_name)
                except Exception as e:
                    self._log_error(f"Creating indicator for {anchor_id}: {e}")

            # Set to False - preview geometry will be rolled back when OK is clicked
            event_args.isValidResult = False

        except Exception as e:
            self._log_exception("onExecutePreview", e)

    def _create_single_indicator(self, design, anchor_id, color_name):
        """Create cylinder indicator for single-face anchor."""
        entity = self._get_entity_from_token(design, self.selected_face_tokens.get(anchor_id))
        if entity:
            self.create_cylinder_indicator(entity, color_name)

    def _create_dual_indicator(self, design, anchor_id, color_name):
        """Create center plane indicator for dual-face anchor (only when both faces selected)."""
        face1 = self._get_entity_from_token(design, self.selected_face_tokens.get(f"{anchor_id}_face1"))
        face2 = self._get_entity_from_token(design, self.selected_face_tokens.get(f"{anchor_id}_face2"))
        if face1 and face2:
            self.create_center_plane_indicator(face1, face2, color_name)

    def _create_edge_face_indicator(self, design, anchor_id, color_name):
        """Create cylinder indicator for edge_face anchor (only when both edge and face selected)."""
        edge = self._get_entity_from_token(design, self.selected_face_tokens.get(f"{anchor_id}_edge"))
        face = self._get_entity_from_token(design, self.selected_face_tokens.get(f"{anchor_id}_face"))
        if edge and face:
            self.create_edge_face_indicator(edge, face, color_name)

    def _create_sphere_indicator_for_token(self, design, token_key, color_name):
        """Create sphere indicator for a single token key (common logic for point indicators)."""
        token = self.selected_face_tokens.get(token_key)
        if not token:
            return

        # Prefer snap point (world coordinates) over entity geometry
        snap_point = self.selected_snap_points.get(token_key)
        if snap_point:
            self.create_sphere_at_position(snap_point, color_name)
        else:
            entity = self._get_entity_from_token(design, token)
            if entity:
                self.create_sphere_indicator(entity, color_name)

    def _create_point_indicator(self, design, anchor_id, color_name):
        """Create sphere indicator for point anchor."""
        self._create_sphere_indicator_for_token(design, anchor_id, color_name)

    def _create_multi_point_indicator(self, design, anchor_id, color_name, count):
        """Create sphere indicators for all selected points in a multi_point anchor."""
        for i in range(1, count + 1):
            self._create_sphere_indicator_for_token(design, f"{anchor_id}_{i}", color_name)

    def onValidateInputs(self, args):
        try:
            event_args = adsk.core.ValidateInputsEventArgs.cast(args)
            event_args.areInputsValid = True
        except Exception as e:
            self._log_exception("onValidateInputs", e)

    def onCommandExecute(self, args):
        """Handle OK button - create joints between joint origins and selected faces."""
        try:
            design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)

            # Get current setup type and anchors
            inputs = adsk.core.CommandEventArgs.cast(args).command.commandInputs
            setup_dropdown = inputs.itemById(self.SETUP_TYPE_DROPDOWN_ID)
            selected_type = adsk.core.DropDownCommandInput.cast(setup_dropdown).selectedItem.name
            anchors = self.get_anchors_for_type(selected_type)

            # Create joints for each anchor with a selection
            for anchor in anchors:
                is_dual = anchor.get('dual_selection', False)
                selection_type = anchor.get('selection_type', 'face')

                if is_dual:
                    self._create_dual_face_joint(design, anchor)
                elif selection_type == 'edge_face':
                    self._create_edge_face_joint(design, anchor)
                elif selection_type == 'point':
                    self._create_point_joint(design, anchor)
                elif selection_type == 'multi_point':
                    self._create_multi_point_joints(design, anchor)
                else:
                    self._create_single_face_joint(design, anchor)

            self.clear_state()

        except Exception as e:
            self._log_exception("onCommandExecute", e)

    def onCommandDestroy(self, args):
        try:
            # Delete any remaining preview geometry (e.g., on Cancel)
            self.delete_preview_geometry()
            self.clear_state()
        except Exception as e:
            self._log_exception("onCommandDestroy", e)
