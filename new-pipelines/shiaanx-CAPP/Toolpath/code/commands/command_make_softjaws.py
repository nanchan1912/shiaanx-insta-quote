import traceback

import adsk.core
import adsk.fusion

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, handle_error
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar


CUSTOM_FEATURE_ID = "ToolpathMakeSoftjaws"
EDIT_CMD_ID = "Toolpath_Make_Softjaws_Edit"

# Debug flag - when True: enables logging, skips custom feature grouping, and labels sketch points
DEBUG = False


class Cmd:
    def __init__(self):
        self.CMD_NAME = 'Make Softjaws'
        self.CMD_ID = command_id_from_name(self.CMD_NAME)
        self.CMD_Description = 'Create softjaws by extruding a silhouette of the part into the softjaw blank.'
        self.ICON_FOLDER = resource_path("toolpath_logo", '')
        self.local_handlers = []
        self._custom_feature_def = None
        self._editing_feature = None  # Set when editing an existing custom feature

        # Input IDs
        self.SOFTJAW_BODY_INPUT_ID = "softjaw_body_input"
        self.PART_BODY_INPUT_ID = "part_body_input"
        self.TOP_FACE_INPUT_ID = "top_face_input"
        self.AUTO_SELECT_TOP_ID = "auto_select_top_checkbox"
        self.MIN_CORNER_RADIUS_ID = "min_corner_radius_input"
        self.CORNER_RELIEF_CHECKBOX_ID = "corner_relief_checkbox"
        self.CHAMFER_CHECKBOX_ID = "chamfer_checkbox"
        self.CHAMFER_SIZE_ID = "chamfer_size_input"
        self.ADDITIONAL_OFFSET_ID = "additional_offset_input"
        self.WARNING_TEXT_ID = "intersection_warning"

        # Default values (in cm, internal units)
        # Inch defaults: 1/16" radius, 0.005" chamfer, 0.0005" offset
        self.DEFAULTS_INCH = {
            'min_radius': 0.0625 * 2.54,
            'chamfer': 0.005 * 2.54,
            'offset': 0.0005 * 2.54
        }
        # Metric defaults: 3mm radius, 0.2mm chamfer, 0.02mm offset
        self.DEFAULTS_METRIC = {
            'min_radius': 0.3,
            'chamfer': 0.02,
            'offset': 0.002
        }

    def start(self):
        ui = None
        try:
            fusion = Fusion()
            ui = fusion.getUI()

            # Create edit command definition FIRST (must exist before setting editCommandId)
            edit_cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if not edit_cmd_def:
                edit_cmd_def = ui.commandDefinitions.addButtonDefinition(
                    EDIT_CMD_ID,
                    'Edit Make Softjaws',
                    'Edit an existing Make Softjaws feature',
                    self.ICON_FOLDER
                )
            add_handler(edit_cmd_def.commandCreated, self.onEditCommandCreated, local_handlers=self.local_handlers)

            # Register custom feature definition to group timeline entries
            self._custom_feature_def = adsk.fusion.CustomFeatureDefinition.create(
                CUSTOM_FEATURE_ID,
                'Make Softjaws',
                self.ICON_FOLDER
            )
            # Link the edit command to the custom feature (enables double-click editing)
            self._custom_feature_def.editCommandId = EDIT_CMD_ID

            cmd_def = addCommandToToolbar(
                self.CMD_ID,
                self.CMD_NAME,
                self.CMD_Description,
                self.ICON_FOLDER,
                IS_PROMOTED=False
            )
            add_handler(cmd_def.commandCreated, self.onCommandCreated, local_handlers=self.local_handlers)
        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    def stop(self):
        ui = None
        try:
            ui = Fusion().getUI()
            removeCommandFromToolbar(self.CMD_ID)
            # Clean up edit command definition
            edit_cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if edit_cmd_def:
                edit_cmd_def.deleteMe()
        except:
            log(traceback.format_exc())
            if ui:
                ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

    def _get_defaults_and_units(self):
        """Get default values and unit string based on current document units."""
        fusion = Fusion()
        design = fusion.getDesign()
        units_mgr = design.unitsManager
        default_length_units = units_mgr.defaultLengthUnits

        if 'in' in default_length_units:
            return self.DEFAULTS_INCH, 'in'
        else:
            return self.DEFAULTS_METRIC, 'mm'

    def _create_command_inputs(self, inputs, stored_values=None):
        """
        Create command inputs with optional pre-filled values for edit mode.

        stored_values: dict with keys 'min_radius', 'chamfer', 'offset',
                      'add_corner_relief', 'add_chamfer', 'auto_select_top',
                      'softjaw_token', 'part_token'
        """
        defaults, unit_str = self._get_defaults_and_units()

        # Use stored values or defaults
        if stored_values is None:
            stored_values = {}
        min_radius_value = stored_values.get('min_radius', defaults['min_radius'])
        chamfer_size_value = stored_values.get('chamfer', defaults['chamfer'])
        additional_offset_value = stored_values.get('offset', defaults['offset'])
        add_corner_relief = stored_values.get('add_corner_relief', True)
        add_chamfer = stored_values.get('add_chamfer', True)
        auto_select_top = stored_values.get('auto_select_top', True)

        # SoftJaw blank body selection
        sel_softjaw = inputs.addSelectionInput(
            self.SOFTJAW_BODY_INPUT_ID,
            "SoftJaw Blank",
            "Select the softjaw blank body"
        )
        sel_softjaw.addSelectionFilter("SolidBodies")
        sel_softjaw.addSelectionFilter("Occurrences")
        sel_softjaw.setSelectionLimits(1, 1)

        # Auto-select jaw top checkbox
        inputs.addBoolValueInput(
            self.AUTO_SELECT_TOP_ID,
            "Auto Select Jaw Top",
            True,
            "",
            auto_select_top
        )

        # Top of softjaw face selection (hidden when auto-select is enabled)
        sel_top_face = inputs.addSelectionInput(
            self.TOP_FACE_INPUT_ID,
            "Top of SoftJaw",
            "Select the top face of the softjaw blank"
        )
        sel_top_face.addSelectionFilter("PlanarFaces")
        sel_top_face.setSelectionLimits(0, 1)
        sel_top_face.isVisible = not auto_select_top

        # Part body selection
        sel_part = inputs.addSelectionInput(
            self.PART_BODY_INPUT_ID,
            "Part Body",
            "Select the part body to subtract"
        )
        sel_part.addSelectionFilter("SolidBodies")
        sel_part.addSelectionFilter("Occurrences")
        sel_part.setSelectionLimits(1, 1)

        # Pre-select bodies from stored tokens (edit mode)
        if 'softjaw_token' in stored_values and stored_values['softjaw_token']:
            design = Fusion().getDesign()
            softjaw_bodies = design.findEntityByToken(stored_values['softjaw_token'])
            if softjaw_bodies and len(softjaw_bodies) > 0:
                sel_softjaw.addSelection(softjaw_bodies[0])

        if 'part_token' in stored_values and stored_values['part_token']:
            design = Fusion().getDesign()
            part_bodies = design.findEntityByToken(stored_values['part_token'])
            if part_bodies and len(part_bodies) > 0:
                sel_part.addSelection(part_bodies[0])

        # Min corner radius input
        inputs.addValueInput(
            self.MIN_CORNER_RADIUS_ID,
            "Min Corner Radius",
            unit_str,
            adsk.core.ValueInput.createByReal(min_radius_value)
        )

        # Corner relief checkbox
        inputs.addBoolValueInput(
            self.CORNER_RELIEF_CHECKBOX_ID,
            "Add Corner Relief",
            True,
            "",
            add_corner_relief
        )

        # Chamfer checkbox
        inputs.addBoolValueInput(
            self.CHAMFER_CHECKBOX_ID,
            "Add Top Edge Chamfer",
            True,
            "",
            add_chamfer
        )

        # Chamfer size input
        inputs.addValueInput(
            self.CHAMFER_SIZE_ID,
            "Chamfer Size",
            unit_str,
            adsk.core.ValueInput.createByReal(chamfer_size_value)
        )

        # Additional offset input
        inputs.addValueInput(
            self.ADDITIONAL_OFFSET_ID,
            "Additional Offset",
            unit_str,
            adsk.core.ValueInput.createByReal(additional_offset_value)
        )

        # Warning message (initially hidden)
        warning_text = inputs.addTextBoxCommandInput(
            self.WARNING_TEXT_ID,
            '',
            '',
            2,
            True
        )
        warning_text.isVisible = False

    def _extract_input_values(self, inputs):
        """Extract all parameter values from command inputs."""
        sel_softjaw = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.SOFTJAW_BODY_INPUT_ID)
        )
        sel_part = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.PART_BODY_INPUT_ID)
        )
        sel_top_face = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.TOP_FACE_INPUT_ID)
        )
        min_radius_input = adsk.core.ValueCommandInput.cast(
            inputs.itemById(self.MIN_CORNER_RADIUS_ID)
        )
        corner_relief_checkbox = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.CORNER_RELIEF_CHECKBOX_ID)
        )
        chamfer_checkbox = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.CHAMFER_CHECKBOX_ID)
        )
        chamfer_size_input = adsk.core.ValueCommandInput.cast(
            inputs.itemById(self.CHAMFER_SIZE_ID)
        )
        additional_offset_input = adsk.core.ValueCommandInput.cast(
            inputs.itemById(self.ADDITIONAL_OFFSET_ID)
        )
        auto_select_checkbox = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.AUTO_SELECT_TOP_ID)
        )

        # Get top face from selection, or None if not selected (will auto-detect)
        top_face = None
        if sel_top_face.selectionCount == 1:
            top_face = adsk.fusion.BRepFace.cast(sel_top_face.selection(0).entity)

        return {
            'softjaw_entity': sel_softjaw.selection(0).entity,
            'part_entity': sel_part.selection(0).entity,
            'top_face': top_face,
            'min_corner_radius': min_radius_input.value,
            'add_corner_relief': corner_relief_checkbox.value,
            'add_chamfer': chamfer_checkbox.value,
            'chamfer_size': chamfer_size_input.value,
            'additional_offset': additional_offset_input.value,
            'auto_select_top': auto_select_checkbox.value
        }

    def onCommandCreated(self, args):
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            inputs = cmd.commandInputs

            # Create all inputs with default values
            self._create_command_inputs(inputs)

            # Register event handlers
            add_handler(cmd.inputChanged, self.onInputChanged, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.execute, self.onExecute, local_handlers=self.local_handlers)

        except Exception as e:
            handle_error(e, True)

    def onEditCommandCreated(self, args):
        """Handle edit command creation - populate dialog with stored values from custom feature."""
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            inputs = cmd.commandInputs

            fusion = Fusion()
            ui = fusion.getUI()

            # Get the custom feature being edited from active selections
            if ui.activeSelections.count == 0:
                ui.messageBox("No feature selected for editing.")
                return

            self._editing_feature = adsk.fusion.CustomFeature.cast(ui.activeSelections.item(0).entity)
            if self._editing_feature is None:
                ui.messageBox("Selected item is not a custom feature.")
                return

            # Retrieve stored parameters
            params = self._editing_feature.parameters
            stored_values = {
                'min_radius': params.itemById('min_corner_radius').value if params.itemById('min_corner_radius') else None,
                'chamfer': params.itemById('chamfer_size').value if params.itemById('chamfer_size') else None,
                'offset': params.itemById('additional_offset').value if params.itemById('additional_offset') else None,
                'add_corner_relief': params.itemById('add_corner_relief').value > 0.5 if params.itemById('add_corner_relief') else True,
                'add_chamfer': params.itemById('add_chamfer').value > 0.5 if params.itemById('add_chamfer') else True,
                'auto_select_top': params.itemById('auto_select_top').value > 0.5 if params.itemById('auto_select_top') else True,
            }

            # Retrieve stored body tokens from attributes
            softjaw_token_attr = self._editing_feature.attributes.itemByName('ToolpathMakeSoftjaws', 'softjaw_body_token')
            part_token_attr = self._editing_feature.attributes.itemByName('ToolpathMakeSoftjaws', 'part_body_token')
            stored_values['softjaw_token'] = softjaw_token_attr.value if softjaw_token_attr else None
            stored_values['part_token'] = part_token_attr.value if part_token_attr else None

            # Create all inputs with stored values
            self._create_command_inputs(inputs, stored_values)

            # Register event handlers (reuse input validation, use edit-specific execute)
            add_handler(cmd.inputChanged, self.onInputChanged, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.execute, self.onEditExecute, local_handlers=self.local_handlers)

        except Exception as e:
            handle_error(e, True)

    def onEditExecute(self, args):
        """Execute edit - delete old feature and recreate with new values."""
        try:
            fusion = Fusion()
            design = fusion.getDesign()
            ui = fusion.getUI()

            command = args.firingEvent.sender
            inputs = command.commandInputs

            # Extract all input values using helper
            values = self._extract_input_values(inputs)

            # Store entity tokens BEFORE rollback - bodies will be re-acquired after
            softjaw_body_temp, _ = self.get_body_from_selection(values['softjaw_entity'])
            part_body_temp, _ = self.get_body_from_selection(values['part_entity'])
            softjaw_body_token = softjaw_body_temp.entityToken
            part_body_token = part_body_temp.entityToken

            # Get timeline info before deleting
            timeline = design.timeline
            custom_feat = self._editing_feature

            # Get construction plane token before deleting the custom feature
            construction_plane_token = None
            if custom_feat and custom_feat.isValid:
                plane_token_attr = custom_feat.attributes.itemByName('ToolpathMakeSoftjaws', 'construction_plane_token')
                if plane_token_attr:
                    construction_plane_token = plane_token_attr.value

                # Roll timeline to just before the custom feature
                custom_feat.timelineObject.rollTo(True)

                # Delete the custom feature (and its grouped features)
                custom_feat.deleteMe()

            # Delete the old construction plane (not grouped in custom feature)
            if construction_plane_token:
                old_planes = design.findEntityByToken(construction_plane_token)
                if old_planes and len(old_planes) > 0:
                    old_plane = old_planes[0]
                    if old_plane.isValid:
                        old_plane.deleteMe()

            # Re-acquire bodies AFTER rollback using entity tokens
            # The geometry has changed, so we need fresh references
            softjaw_bodies = design.findEntityByToken(softjaw_body_token)
            part_bodies = design.findEntityByToken(part_body_token)

            if not softjaw_bodies or len(softjaw_bodies) == 0:
                ui.messageBox("Could not find softjaw body after timeline rollback.")
                return
            if not part_bodies or len(part_bodies) == 0:
                ui.messageBox("Could not find part body after timeline rollback.")
                return

            softjaw_body = softjaw_bodies[0]
            part_body = part_bodies[0]

            # Get top face AFTER rolling back (geometry has changed)
            # The softjaw body is now in its original state before the cut
            top_face = self.infer_top_face(softjaw_body, part_body)

            if top_face is None:
                ui.messageBox("Could not auto-detect the top face. Please manually select the top face of the softjaw blank.")
                return

            # Recreate with new values
            self.perform_silhouette_extrude_cut(
                softjaw_body, part_body, top_face,
                values['min_corner_radius'],
                values['add_corner_relief'],
                values['add_chamfer'],
                values['chamfer_size'],
                values['additional_offset'],
                auto_select_top=values['auto_select_top']
            )

            # Roll timeline back to end
            timeline.moveToEnd()

            # Clear edit state
            self._editing_feature = None

        except Exception as e:
            handle_error(e, True)

    def get_body_from_selection(self, entity):
        """
        Extract body from selection.
        Returns (body, error_message) - error_message is None if valid.
        """
        if isinstance(entity, adsk.fusion.BRepBody):
            return (entity, None)
        elif isinstance(entity, adsk.fusion.Occurrence):
            bodies = entity.bRepBodies
            if bodies.count == 0:
                return (None, "Selected container has no bodies")
            elif bodies.count > 1:
                return (None, "Selected container has multiple bodies - select a container with exactly one body")
            return (bodies.item(0), None)
        return (None, "Invalid selection type")

    def bodies_intersect(self, body1, body2):
        """Check if two bodies intersect using bounding box overlap."""
        bb1 = body1.boundingBox
        bb2 = body2.boundingBox
        return bb1.intersects(bb2)

    def onInputChanged(self, args):
        """Handle input changes to update intersection warning and auto-advance focus."""
        eventArgs = adsk.core.InputChangedEventArgs.cast(args)
        changed_input = eventArgs.input
        inputs = eventArgs.firingEvent.sender.commandInputs

        sel_softjaw = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.SOFTJAW_BODY_INPUT_ID)
        )
        sel_top_face = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.TOP_FACE_INPUT_ID)
        )
        sel_part = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.PART_BODY_INPUT_ID)
        )
        warning_text = adsk.core.TextBoxCommandInput.cast(
            inputs.itemById(self.WARNING_TEXT_ID)
        )
        auto_select_top_checkbox = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.AUTO_SELECT_TOP_ID)
        )
        corner_relief_checkbox = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.CORNER_RELIEF_CHECKBOX_ID)
        )
        chamfer_checkbox = adsk.core.BoolValueCommandInput.cast(
            inputs.itemById(self.CHAMFER_CHECKBOX_ID)
        )
        min_radius_input = inputs.itemById(self.MIN_CORNER_RADIUS_ID)
        chamfer_size_input = inputs.itemById(self.CHAMFER_SIZE_ID)

        # Show/hide top face selector based on auto-select checkbox
        if changed_input.id == self.AUTO_SELECT_TOP_ID:
            sel_top_face.isVisible = not auto_select_top_checkbox.value
            # Clear selection when hiding
            if auto_select_top_checkbox.value:
                sel_top_face.clearSelection()

        # Show/hide min corner radius based on corner relief checkbox
        if changed_input.id == self.CORNER_RELIEF_CHECKBOX_ID:
            min_radius_input.isVisible = corner_relief_checkbox.value

        # Show/hide chamfer size based on chamfer checkbox
        if changed_input.id == self.CHAMFER_CHECKBOX_ID:
            chamfer_size_input.isVisible = chamfer_checkbox.value

        # Auto-advance focus to next selection input after a selection is made
        if changed_input.id == self.SOFTJAW_BODY_INPUT_ID and sel_softjaw.selectionCount == 1:
            if auto_select_top_checkbox.value:
                # Skip top face selector when auto-select is enabled
                sel_part.hasFocus = True
            else:
                sel_top_face.hasFocus = True
        elif changed_input.id == self.TOP_FACE_INPUT_ID and sel_top_face.selectionCount == 1:
            sel_part.hasFocus = True

        # Only check if both bodies are selected
        if sel_softjaw.selectionCount == 1 and sel_part.selectionCount == 1:
            softjaw_entity = sel_softjaw.selection(0).entity
            part_entity = sel_part.selection(0).entity

            # Extract bodies from selections
            softjaw_body, softjaw_error = self.get_body_from_selection(softjaw_entity)
            part_body, part_error = self.get_body_from_selection(part_entity)

            # Check for extraction errors
            if softjaw_error:
                warning_text.formattedText = f"SoftJaw Blank: {softjaw_error}"
                warning_text.isVisible = True
                return
            if part_error:
                warning_text.formattedText = f"Part Body: {part_error}"
                warning_text.isVisible = True
                return

            # Check if same body selected
            if softjaw_body == part_body:
                warning_text.formattedText = "Error: Cannot select the same body for both inputs."
                warning_text.isVisible = True
                return

            # Check bounding box intersection
            if not self.bodies_intersect(softjaw_body, part_body):
                warning_text.formattedText = "Warning: Selected bodies do not overlap."
                warning_text.isVisible = True
                return

            # All good - hide warning
            warning_text.isVisible = False
        else:
            warning_text.isVisible = False

    def onValidateInputs(self, args):
        """Validate that all inputs are selected and valid."""
        eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
        inputs = eventArgs.firingEvent.sender.commandInputs

        sel_softjaw = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.SOFTJAW_BODY_INPUT_ID)
        )
        sel_part = adsk.core.SelectionCommandInput.cast(
            inputs.itemById(self.PART_BODY_INPUT_ID)
        )

        # Require softjaw and part selections (top face is optional - will auto-detect)
        if sel_softjaw.selectionCount != 1 or sel_part.selectionCount != 1:
            eventArgs.areInputsValid = False
            return

        # Extract bodies
        softjaw_entity = sel_softjaw.selection(0).entity
        part_entity = sel_part.selection(0).entity

        softjaw_body, softjaw_error = self.get_body_from_selection(softjaw_entity)
        part_body, part_error = self.get_body_from_selection(part_entity)

        # Validate body extraction
        if softjaw_error or part_error:
            eventArgs.areInputsValid = False
            return

        # Ensure bodies are different
        if softjaw_body == part_body:
            eventArgs.areInputsValid = False
            return

        eventArgs.areInputsValid = True

    def onExecute(self, args):
        """Execute the silhouette projection and extrude cut."""
        try:
            command = args.firingEvent.sender
            inputs = command.commandInputs

            # Extract all input values using helper
            values = self._extract_input_values(inputs)

            softjaw_body, _ = self.get_body_from_selection(values['softjaw_entity'])
            part_body, _ = self.get_body_from_selection(values['part_entity'])

            # Get top face from extracted value, or auto-detect if not selected
            top_face = values['top_face']
            if top_face is None:
                top_face = self.infer_top_face(softjaw_body, part_body)
                if top_face is None:
                    fusion = Fusion()
                    ui = fusion.getUI()
                    ui.messageBox("Could not auto-detect the top face. Please manually select the top face of the softjaw blank.")
                    return

            self.perform_silhouette_extrude_cut(
                softjaw_body, part_body, top_face,
                values['min_corner_radius'],
                values['add_corner_relief'],
                values['add_chamfer'],
                values['chamfer_size'],
                values['additional_offset'],
                auto_select_top=values['auto_select_top']
            )

        except Exception as e:
            handle_error(e, True)

    def cross_product(self, v1, v2):
        """Compute cross product of two Vector3D objects."""
        return adsk.core.Vector3D.create(
            v1.y * v2.z - v1.z * v2.y,
            v1.z * v2.x - v1.x * v2.z,
            v1.x * v2.y - v1.y * v2.x
        )

    def vector_between_points(self, p1, p2):
        """Create a vector from p1 to p2."""
        return adsk.core.Vector3D.create(p2.x - p1.x, p2.y - p1.y, p2.z - p1.z)

    def get_face_centroid(self, face):
        """Get the centroid point of a face."""
        evaluator = face.evaluator
        result, point = evaluator.getPointAtParameter(adsk.core.Point2D.create(0.5, 0.5))
        return point if result else None

    def get_face_normal(self, face, at_point=None):
        """
        Get the outward normal vector of a face.
        For planar faces, the normal is the same everywhere.
        For curved faces, evaluates the normal at the specified point,
        or at the face centroid if no point is specified.
        """
        evaluator = face.evaluator
        if at_point is not None:
            result, normal = evaluator.getNormalAtPoint(at_point)
            if result:
                return normal
        # Fall back to evaluating at face centroid
        point = self.get_face_centroid(face)
        if point:
            result, normal = evaluator.getNormalAtPoint(point)
            if result:
                return normal
        return None

    def get_face_vertices(self, face):
        """
        Get all unique vertices of a face by traversing its edges.
        Returns a list of Point3D objects.
        """
        vertices = []
        seen_tokens = set()
        for edge in face.edges:
            for vertex in [edge.startVertex, edge.endVertex]:
                if vertex.entityToken not in seen_tokens:
                    seen_tokens.add(vertex.entityToken)
                    vertices.append(vertex.geometry)
        return vertices

    def _compute_arc_tangent(self, center, arc_normal, point, which_end):
        """
        Compute tangent direction for arc/circle at given point.
        Returns a 2D-normalized Vector3D (z=0) pointing away from the endpoint.
        """
        # Radius vector from center to point
        radius_vec = self.vector_between_points(center, point)

        # Tangent is perpendicular to radius, in the plane of the arc
        # Use cross product: tangent = arc_normal × radius_vec
        tangent = self.cross_product(arc_normal, radius_vec)

        # For 'end' point, we want direction away from end (back along the arc)
        # The cross product gives tangent in direction of arc travel
        # At start: we want direction of travel (away from start)
        # At end: we want opposite of travel direction (away from end)
        if which_end == 'end':
            tangent.x = -tangent.x
            tangent.y = -tangent.y
            tangent.z = -tangent.z

        tangent.z = 0
        tangent.normalize()
        return tangent

    def get_curve_direction_at_endpoint(self, curve, which_end):
        """
        Get the direction vector of a sketch curve pointing AWAY from the specified endpoint.
        which_end: 'start' or 'end'
        Returns a 2D-normalized Vector3D (z=0).
        """
        geom = curve.geometry

        # Handle lines
        if geom.objectType == adsk.core.Line3D.classType():
            if which_end == 'start':
                vec = self.vector_between_points(geom.startPoint, geom.endPoint)
            else:
                vec = self.vector_between_points(geom.endPoint, geom.startPoint)
            vec.z = 0
            vec.normalize()
            return vec

        # Handle arcs
        if geom.objectType == adsk.core.Arc3D.classType():
            point = geom.startPoint if which_end == 'start' else geom.endPoint
            return self._compute_arc_tangent(geom.center, geom.normal, point, which_end)

        # Handle circles (full circles have no start/end, but just in case)
        if geom.objectType == adsk.core.Circle3D.classType():
            if which_end == 'start' and hasattr(curve, 'startSketchPoint'):
                point = curve.startSketchPoint.geometry
            elif which_end == 'end' and hasattr(curve, 'endSketchPoint'):
                point = curve.endSketchPoint.geometry
            else:
                return None
            return self._compute_arc_tangent(geom.center, geom.normal, point, which_end)

        # Handle NurbsCurve3D (splines) - use evaluator to get tangent at endpoint
        if geom.objectType == adsk.core.NurbsCurve3D.classType():
            evaluator = geom.evaluator
            result, start_param, end_param = evaluator.getParameterExtents()
            if not result:
                return None

            if which_end == 'start':
                param = start_param
            else:
                param = end_param

            # Get first derivative (tangent) at the parameter - singular method, not plural
            result, tangent = evaluator.getFirstDerivative(param)
            if result and tangent:
                # For 'start', tangent points away from start (in direction of curve)
                # For 'end', we want direction away from end (opposite of curve direction)
                if which_end == 'end':
                    tangent.x = -tangent.x
                    tangent.y = -tangent.y
                    tangent.z = -tangent.z
                tangent.z = 0
                tangent.normalize()
                return tangent
            return None

        # Unknown geometry type
        if DEBUG:
            log(f"DEBUG: Unknown curve geometry type: {geom.objectType}", force_console=True)
        return None

    def classify_corners_2d(self, sketch, intersection_entities):
        """
        Classify corners as concave or convex using 2D sketch geometry.

        For each corner point where two curves meet, computes the cross product
        of the direction vectors pointing away from the corner. The sign indicates
        whether it's a left turn (CCW) or right turn (CW) at that corner.

        Returns a dict mapping (x, y) position tuples to is_concave boolean.
        """
        # Build a map of point position -> list of (which_end, curve) tuples
        # Use worldGeometry to get model coordinates (matching add_corner_relief lookups)
        point_to_curves = {}

        for entity in intersection_entities:
            if hasattr(entity, 'startSketchPoint') and entity.startSketchPoint:
                world_pt = entity.startSketchPoint.worldGeometry
                pos = (round(world_pt.x, 6), round(world_pt.y, 6))
                if pos not in point_to_curves:
                    point_to_curves[pos] = []
                point_to_curves[pos].append(('start', entity))

            if hasattr(entity, 'endSketchPoint') and entity.endSketchPoint:
                world_pt = entity.endSketchPoint.worldGeometry
                pos = (round(world_pt.x, 6), round(world_pt.y, 6))
                if pos not in point_to_curves:
                    point_to_curves[pos] = []
                point_to_curves[pos].append(('end', entity))

        # Compute signed area to determine winding direction (using world coordinates)
        # Positive = CCW, Negative = CW
        signed_area = 0.0
        for entity in intersection_entities:
            if hasattr(entity, 'startSketchPoint') and hasattr(entity, 'endSketchPoint'):
                p1 = entity.startSketchPoint.worldGeometry
                p2 = entity.endSketchPoint.worldGeometry
                # Shoelace formula contribution
                signed_area += (p2.x - p1.x) * (p2.y + p1.y)
        is_ccw = signed_area < 0

        if DEBUG:
            log(f"DEBUG: 2D classification - signed_area={signed_area:.6f}, is_ccw={is_ccw}", force_console=True)

        # For each corner point, compute cross product of direction vectors
        corner_classifications = {}

        for pos, curves in point_to_curves.items():
            if len(curves) != 2:
                if DEBUG:
                    log(f"DEBUG: Position {pos} has {len(curves)} curves, skipping", force_console=True)
                continue

            # Debug: Log detailed info about each curve at this corner
            if DEBUG:
                for idx, (which_end, curve) in enumerate(curves):
                    curve_type = type(curve).__name__
                    geom_type = type(curve.geometry).__name__
                    if hasattr(curve, 'startSketchPoint') and hasattr(curve, 'endSketchPoint'):
                        start_pt = curve.startSketchPoint.worldGeometry
                        end_pt = curve.endSketchPoint.worldGeometry
                        log(f"DEBUG: Corner {pos} curve[{idx}]: {curve_type}/{geom_type}, which_end={which_end}, start=({start_pt.x:.4f},{start_pt.y:.4f}), end=({end_pt.x:.4f},{end_pt.y:.4f})", force_console=True)
                    else:
                        log(f"DEBUG: Corner {pos} curve[{idx}]: {curve_type}/{geom_type}, which_end={which_end}", force_console=True)

            # Determine incoming and outgoing curves based on which_end
            # - Curve that ENDS here (which_end='end') is the INCOMING curve
            # - Curve that STARTS here (which_end='start') is the OUTGOING curve
            which_end_0, curve_0 = curves[0]
            which_end_1, curve_1 = curves[1]

            incoming_curve = None
            outgoing_curve = None

            # Normal case: one curve ends here, one starts here
            if which_end_0 != which_end_1:
                if which_end_0 == 'end':
                    incoming_curve = curve_0
                    outgoing_curve = curve_1
                else:
                    incoming_curve = curve_1
                    outgoing_curve = curve_0
            else:
                # Edge case: both curves have same which_end (both start or both end)
                # Use triangle signed area to determine proper ordering
                # Get the "other end" of each curve (the end NOT at this corner)
                def get_other_end(curve, which_end):
                    if which_end == 'start':
                        return curve.endSketchPoint.worldGeometry
                    else:
                        return curve.startSketchPoint.worldGeometry

                other_0 = get_other_end(curve_0, which_end_0)
                other_1 = get_other_end(curve_1, which_end_1)

                # Corner position (use first curve's endpoint at this corner)
                if which_end_0 == 'start':
                    corner_pt = curve_0.startSketchPoint.worldGeometry
                else:
                    corner_pt = curve_0.endSketchPoint.worldGeometry

                # Compute signed area of triangle: other_0 -> corner -> other_1
                # Positive = CCW order, Negative = CW order
                triangle_area = ((corner_pt.x - other_0.x) * (other_1.y - other_0.y) -
                                (other_1.x - other_0.x) * (corner_pt.y - other_0.y))

                if DEBUG:
                    log(f"DEBUG: Corner {pos} EDGE CASE (both which_end={which_end_0})", force_console=True)
                    log(f"DEBUG: Corner {pos} triangle_area={triangle_area:.6f} (other_0={other_0.x:.4f},{other_0.y:.4f} other_1={other_1.x:.4f},{other_1.y:.4f})", force_console=True)

                # For CCW profile traversal:
                # - If triangle is CCW (positive): traversal goes other_0 -> corner -> other_1
                # - If triangle is CW (negative): traversal goes other_1 -> corner -> other_0
                if which_end_0 == 'start':
                    # Both curves START at corner, END at other points
                    # Incoming curve: we arrive FROM its other end (traversed end->start)
                    # Outgoing curve: we depart TO its other end (traversed start->end)
                    if (triangle_area > 0) == is_ccw:
                        # Traversal: other_0 -> corner -> other_1
                        incoming_curve = curve_0  # arrives from other_0
                        outgoing_curve = curve_1  # departs to other_1
                    else:
                        # Traversal: other_1 -> corner -> other_0
                        incoming_curve = curve_1  # arrives from other_1
                        outgoing_curve = curve_0  # departs to other_0
                else:
                    # Both curves END at corner, START at other points
                    # Incoming curve: we arrive via it (traversed start->end)
                    # Outgoing curve: we depart via it (traversed end->start)
                    if (triangle_area > 0) == is_ccw:
                        # Traversal: other_0 -> corner -> other_1
                        incoming_curve = curve_0  # arrives via curve from other_0
                        outgoing_curve = curve_1  # departs via curve to other_1
                    else:
                        # Traversal: other_1 -> corner -> other_0
                        incoming_curve = curve_1  # arrives via curve from other_1
                        outgoing_curve = curve_0  # departs via curve to other_0

                if DEBUG:
                    log(f"DEBUG: Corner {pos} edge case result: curve[0]={'incoming' if incoming_curve == curve_0 else 'outgoing'}, curve[1]={'incoming' if incoming_curve == curve_1 else 'outgoing'}", force_console=True)

            if incoming_curve is None or outgoing_curve is None:
                if DEBUG:
                    log(f"DEBUG: Corner {pos} could not determine incoming/outgoing, skipping", force_console=True)
                continue

            # Get incoming direction (TOWARD corner) and outgoing direction (AWAY from corner)
            # For normal case: incoming ends here ('end'), outgoing starts here ('start')
            # For edge case: both have same which_end, use that for both
            if which_end_0 != which_end_1:
                # Normal case
                incoming_away = self.get_curve_direction_at_endpoint(incoming_curve, 'end')
                outgoing_vec = self.get_curve_direction_at_endpoint(outgoing_curve, 'start')
            else:
                # Edge case: both curves have corner at same endpoint type
                incoming_away = self.get_curve_direction_at_endpoint(incoming_curve, which_end_0)
                outgoing_vec = self.get_curve_direction_at_endpoint(outgoing_curve, which_end_0)

            if incoming_away is None or outgoing_vec is None:
                continue

            # Incoming direction is opposite of "away"
            incoming_vec = adsk.core.Vector3D.create(-incoming_away.x, -incoming_away.y, 0)

            if DEBUG:
                log(f"DEBUG: Corner {pos} incoming: ({incoming_vec.x:.4f}, {incoming_vec.y:.4f})", force_console=True)
                log(f"DEBUG: Corner {pos} outgoing: ({outgoing_vec.x:.4f}, {outgoing_vec.y:.4f})", force_console=True)

            # Compute 2D cross product of incoming × outgoing
            # This gives the signed turn direction at the corner
            cross_z = incoming_vec.x * outgoing_vec.y - incoming_vec.y * outgoing_vec.x

            # For a CCW-wound profile (is_ccw=True):
            # - Positive cross (left/CCW turn) = convex profile corner = concave pocket (needs relief)
            # - Negative cross (right/CW turn) = concave profile corner = convex pocket (gets fillet)
            # For a CW-wound profile, the interpretation is reversed
            if is_ccw:
                is_pocket_concave = cross_z > 0
            else:
                is_pocket_concave = cross_z < 0

            corner_classifications[pos] = is_pocket_concave

            if DEBUG:
                log(f"DEBUG: 2D corner at {pos}: cross_z={cross_z:.6f} -> {'CONCAVE' if is_pocket_concave else 'CONVEX'}", force_console=True)

        return corner_classifications

    def get_part_lowest_z_depth(self, part_body, top_face, face_normal):
        """
        Get the lowest z depth of the part relative to the top face.
        Uses MeasureManager.getOrientedBoundingBox() for accurate calculation.
        Returns the depth (positive value) from the top face to the lowest point.
        """
        app = adsk.core.Application.get()
        measureMgr = app.measureManager

        # Get oriented bounding box aligned with face normal
        # Need to provide two perpendicular directions
        if abs(face_normal.z) < 0.9:
            temp = adsk.core.Vector3D.create(0, 0, 1)
        else:
            temp = adsk.core.Vector3D.create(1, 0, 0)
        perp_axis = self.cross_product(temp, face_normal)
        perp_axis.normalize()

        obb = measureMgr.getOrientedBoundingBox(part_body, face_normal, perp_axis)

        # Find which dimension corresponds to the face_normal direction
        # by checking which direction vector is most aligned with face_normal
        height_dot = abs(obb.heightDirection.dotProduct(face_normal))
        length_dot = abs(obb.lengthDirection.dotProduct(face_normal))
        width_dot = abs(obb.widthDirection.dotProduct(face_normal))

        if height_dot >= length_dot and height_dot >= width_dot:
            z_size = obb.height
            z_direction = obb.heightDirection
        elif length_dot >= width_dot:
            z_size = obb.length
            z_direction = obb.lengthDirection
        else:
            z_size = obb.width
            z_direction = obb.widthDirection

        # Calculate lowest z position relative to top face
        plane_point = self.get_face_centroid(top_face)
        vec_to_center = self.vector_between_points(plane_point, obb.centerPoint)
        center_depth = vec_to_center.dotProduct(face_normal)

        # The lowest point is center_depth - z_size/2 (if z_direction aligns with face_normal)
        # or center_depth + z_size/2 (if z_direction is opposite to face_normal)
        if z_direction.dotProduct(face_normal) > 0:
            lowest_depth = -center_depth + z_size / 2
        else:
            lowest_depth = -center_depth - z_size / 2

        # Make sure we return a positive depth value
        lowest_depth = abs(lowest_depth)

        return lowest_depth

    def infer_top_face(self, softjaw_body, part_body):
        """
        Infer the top face of the softjaw using oriented bounding box analysis.

        Algorithm:
        1. Collect area-weighted face normals to find dominant directions
        2. Group normals into opposing pairs (parallel faces)
        3. The pair with largest total area defines the "top/bottom" direction
        4. Find the actual softjaw face with that normal at the highest position
        """
        # Step 1: Collect planar face normals weighted by area
        normal_groups = []  # List of (normal, total_area, faces)
        angle_tolerance = 0.1  # ~5.7 degrees

        for face in softjaw_body.faces:
            if not isinstance(face.geometry, adsk.core.Plane):
                continue

            face_normal = self.get_face_normal(face)
            if face_normal is None:
                continue

            area = face.area

            # Try to add to existing group
            added = False
            for group in normal_groups:
                group_normal = group[0]
                # Check if normals are parallel (same or opposite direction)
                dot = abs(face_normal.dotProduct(group_normal))
                if dot > 1.0 - angle_tolerance:
                    group[1] += area  # Add to total area
                    group[2].append((face, face_normal, area))
                    added = True
                    break

            if not added:
                # Create new group
                normal_groups.append([face_normal, area, [(face, face_normal, area)]])

        if not normal_groups:
            return None

        # Step 2: Find the group with largest total area (this is the top/bottom direction)
        normal_groups.sort(key=lambda x: x[1], reverse=True)
        dominant_group = normal_groups[0]
        dominant_normal = dominant_group[0]

        # Step 3: From the dominant group, find faces that point "up" (toward the part)
        # and pick the one at the highest position along the dominant direction

        # Determine which direction is "up" by checking where the part is relative to softjaw center
        softjaw_bb = softjaw_body.boundingBox
        softjaw_center = adsk.core.Point3D.create(
            (softjaw_bb.minPoint.x + softjaw_bb.maxPoint.x) / 2,
            (softjaw_bb.minPoint.y + softjaw_bb.maxPoint.y) / 2,
            (softjaw_bb.minPoint.z + softjaw_bb.maxPoint.z) / 2
        )
        part_bb = part_body.boundingBox
        part_center = adsk.core.Point3D.create(
            (part_bb.minPoint.x + part_bb.maxPoint.x) / 2,
            (part_bb.minPoint.y + part_bb.maxPoint.y) / 2,
            (part_bb.minPoint.z + part_bb.maxPoint.z) / 2
        )

        # Vector from softjaw center to part center
        to_part = self.vector_between_points(softjaw_center, part_center)

        # The "up" direction should point toward the part
        if to_part.dotProduct(dominant_normal) < 0:
            # Flip the dominant normal to point toward part
            dominant_normal = adsk.core.Vector3D.create(
                -dominant_normal.x, -dominant_normal.y, -dominant_normal.z
            )

        # Step 4: Find the face with matching normal at the highest position
        best_face = None
        max_height = float('-inf')

        for face, face_normal, area in dominant_group[2]:
            # Check if this face's normal points in the "up" direction
            if face_normal.dotProduct(dominant_normal) > 0.9:
                # Get the height of this face along the dominant direction
                centroid = self.get_face_centroid(face)
                height = (centroid.x * dominant_normal.x +
                         centroid.y * dominant_normal.y +
                         centroid.z * dominant_normal.z)

                if height > max_height:
                    max_height = height
                    best_face = face

        return best_face

    def find_lowest_horizontal_face(self, part_body, top_face, face_normal):
        """
        Find the lowest horizontal face on the part.
        A horizontal face is one whose normal is aligned with the top face normal.

        Returns the lowest horizontal face, or None if not found.
        """
        plane_point = self.get_face_centroid(top_face)
        lowest_horizontal_face = None
        max_horizontal_depth = 0.0

        for face in part_body.faces:
            if not isinstance(face.geometry, adsk.core.Plane):
                continue

            # Check if face is horizontal (normal parallel to top face normal)
            face_norm = self.get_face_normal(face)
            if face_norm is None:
                continue
            dot = abs(face_norm.dotProduct(face_normal))
            if dot < 0.99:  # Not horizontal
                continue

            # Get depth using the plane origin
            face_plane = face.geometry
            vec = self.vector_between_points(plane_point, face_plane.origin)
            dist = vec.dotProduct(face_normal)
            if dist < 0:
                depth = -dist
                if depth > max_horizontal_depth:
                    max_horizontal_depth = depth
                    lowest_horizontal_face = face

        return lowest_horizontal_face

    def get_edge_top_point(self, edge, top_ref_point, face_normal):
        """
        Get the point on an edge that is closest to the top face (highest along face normal).
        top_ref_point: A reference point on the top plane.
        """
        evaluator = edge.evaluator
        _, param_start, param_end = evaluator.getParameterExtents()
        _, start_pt = evaluator.getPointAtParameter(param_start)
        _, end_pt = evaluator.getPointAtParameter(param_end)

        # Project both points onto face normal to find which is higher
        vec_start = self.vector_between_points(top_ref_point, start_pt)
        vec_end = self.vector_between_points(top_ref_point, end_pt)

        dist_start = vec_start.dotProduct(face_normal)
        dist_end = vec_end.dotProduct(face_normal)

        return start_pt if dist_start > dist_end else end_pt

    def add_corner_relief(self, extrude_feature, component, softjaw_body, sketch_plane, top_plane_point, face_normal, min_corner_radius, debug_point_map=None, corner_2d_map=None):
        """
        Add corner relief cuts at concave (inside) corners and fillets at convex (outside) corners.
        Order: 1) Create sketch geometry, 2) Extrude relief cuts, 3) Apply fillets
        Returns tuple: (last_feature, num_concave, num_convex, circle_centers)
        - last_feature: the last feature created, or None if no features were created
        - num_concave: number of concave (inside) corners found
        - num_convex: number of convex (outside) corners found
        - circle_centers: list of (x, y, radius) tuples for relief cut circles

        sketch_plane: Construction plane or face for creating the relief sketch
        top_plane_point: Pre-captured reference point on the top plane
        corner_2d_map: Dict mapping (x, y) positions to is_concave boolean from 2D classification
        """
        last_feature = None
        concave_points = []
        convex_edges = []

        # Collect tokens of all faces created by the extrude (pocket walls and bottom)
        # Edges at the boundary (where pocket meets original softjaw exterior) should be convex
        pocket_face_tokens = set()
        for face in extrude_feature.sideFaces:
            pocket_face_tokens.add(face.entityToken)
        for face in extrude_feature.endFaces:
            pocket_face_tokens.add(face.entityToken)
        for face in extrude_feature.startFaces:
            pocket_face_tokens.add(face.entityToken)

        # Collect all edges from vertical wall faces
        all_edges = []
        processed_tokens = set()
        for face in extrude_feature.sideFaces:
            face_norm = self.get_face_normal(face)
            if face_norm is None:
                continue
            if abs(face_norm.dotProduct(face_normal)) > 0.01:  # Not vertical
                continue

            for edge in face.edges:
                if edge.entityToken not in processed_tokens:
                    processed_tokens.add(edge.entityToken)
                    top_pt = self.get_edge_top_point(edge, top_plane_point, face_normal)
                    all_edges.append((edge, top_pt))

        for edge, top_point in all_edges:
            geom = edge.geometry

            # Check if it's a line (sharp corner) or small radius arc
            is_sharp = geom.objectType == adsk.core.Line3D.classType()
            is_small_radius = False

            if geom.objectType == adsk.core.Circle3D.classType():
                is_small_radius = geom.radius < min_corner_radius
            elif geom.objectType == adsk.core.Arc3D.classType():
                is_small_radius = geom.radius < min_corner_radius

            qualifies_for_corner = is_sharp or is_small_radius
            if not qualifies_for_corner:
                continue

            # Check if this edge is vertical (parallel to face_normal)
            is_vertical = False
            if is_sharp:
                edge_dir = self.vector_between_points(geom.startPoint, geom.endPoint)
                edge_dir.normalize()
                verticality = abs(edge_dir.dotProduct(face_normal))
                is_vertical = abs(verticality - 1.0) <= 0.01
            elif geom.objectType == adsk.core.Circle3D.classType() or geom.objectType == adsk.core.Arc3D.classType():
                # For arcs/circles, the axis (normal) should be parallel to face_normal for vertical edges
                arc_axis = geom.normal
                axis_alignment = abs(arc_axis.dotProduct(face_normal))
                is_vertical = abs(axis_alignment - 1.0) <= 0.01

            if not is_vertical:
                continue

            # Exclude edges that lie on the top plane (both endpoints at top surface level)
            evaluator = edge.evaluator
            _, param_start, param_end = evaluator.getParameterExtents()
            _, start_pt = evaluator.getPointAtParameter(param_start)
            _, end_pt = evaluator.getPointAtParameter(param_end)
            vec_start = self.vector_between_points(top_plane_point, start_pt)
            vec_end = self.vector_between_points(top_plane_point, end_pt)
            dist_start = abs(vec_start.dotProduct(face_normal))
            dist_end = abs(vec_end.dotProduct(face_normal))
            if dist_start < 0.001 and dist_end < 0.001:
                # Edge lies on the top plane - skip it
                continue

            # Check if this edge is at the boundary (one face is exterior to the pocket)
            # If so, treat as convex regardless of geometry
            edge_faces = list(edge.faces)
            is_boundary_edge = False
            if len(edge_faces) == 2:
                face1_in_pocket = edge_faces[0].entityToken in pocket_face_tokens
                face2_in_pocket = edge_faces[1].entityToken in pocket_face_tokens
                is_boundary_edge = not (face1_in_pocket and face2_in_pocket)

            # Look up point index for debug logging and 2D classification
            pos_key = (round(top_point.x, 6), round(top_point.y, 6))
            debug_point_idx = None
            if DEBUG and debug_point_map is not None:
                debug_point_idx = debug_point_map.get(pos_key, None)

            if is_boundary_edge:
                # Debug: Log boundary edge as convex
                if debug_point_idx is not None:
                    log(f"DEBUG: Point {debug_point_idx}: CONVEX (boundary edge)", force_console=True)
                convex_edges.append(edge)
                continue

            # Use 2D classification - skip if not available for this corner
            if corner_2d_map is None or pos_key not in corner_2d_map:
                if DEBUG and debug_point_idx is not None:
                    log(f"DEBUG: Point {debug_point_idx}: skipping (no 2D classification)", force_console=True)
                continue

            is_concave = corner_2d_map[pos_key]
            if DEBUG and debug_point_idx is not None:
                classification = "CONCAVE (relief)" if is_concave else "CONVEX (fillet)"
                log(f"DEBUG: Point {debug_point_idx}: {classification}", force_console=True)

            if is_concave:
                concave_points.append(top_point)
            else:
                convex_edges.append(edge)

        # Step 1: Apply fillets to convex (outside) corners FIRST
        # Must be done before relief cuts, as those modify the geometry and invalidate edge references
        if len(convex_edges) > 0:
            fillet_features = component.features.filletFeatures
            edge_collection = adsk.core.ObjectCollection.create()
            for edge in convex_edges:
                edge_collection.add(edge)

            fillet_input = fillet_features.createInput()
            fillet_input.addConstantRadiusEdgeSet(edge_collection, adsk.core.ValueInput.createByReal(min_corner_radius), True)
            try:
                last_feature = fillet_features.add(fillet_input)
            except RuntimeError:
                # Fillet may fail if edges are too small or geometry is incompatible
                pass

        # Step 2: Create sketch geometry for concave corners
        relief_sketch = None
        created_circles = []
        if len(concave_points) > 0:
            sketches = component.sketches
            relief_sketch = sketches.add(sketch_plane)
            sketch_circles = relief_sketch.sketchCurves.sketchCircles

            relief_radius = min_corner_radius * 1.1  # Slightly larger than min radius
            for point in concave_points:
                sketch_point = relief_sketch.modelToSketchSpace(point)
                center = adsk.core.Point3D.create(sketch_point.x, sketch_point.y, 0)
                sketch_circles.addByCenterRadius(center, relief_radius)
                created_circles.append((center.x, center.y, relief_radius))

        # Step 3: Extrude relief cuts for concave corners
        if relief_sketch is not None and len(created_circles) > 0:
            profiles = relief_sketch.profiles
            if profiles.count > 0:
                profile_collection = adsk.core.ObjectCollection.create()
                for i in range(profiles.count):
                    profile = profiles.item(i)
                    area_props = profile.areaProperties()
                    centroid = area_props.centroid

                    # Check if this centroid is inside any of our circles
                    for cx, cy, r in created_circles:
                        dx = centroid.x - cx
                        dy = centroid.y - cy
                        dist_sq = dx * dx + dy * dy
                        if dist_sq < r * r:
                            profile_collection.add(profile)
                            break

                if profile_collection.count > 0:
                    end_faces = extrude_feature.endFaces
                    if end_faces.count > 0:
                        pocket_bottom = end_faces.item(0)

                        extrudes = component.features.extrudeFeatures
                        extrude_input = extrudes.createInput(profile_collection, adsk.fusion.FeatureOperations.CutFeatureOperation)

                        to_entity = adsk.fusion.ToEntityExtentDefinition.create(pocket_bottom, False)
                        extrude_input.setOneSideExtent(to_entity, adsk.fusion.ExtentDirections.NegativeExtentDirection)
                        extrude_input.participantBodies = [softjaw_body]

                        last_feature = extrudes.add(extrude_input)

        return (last_feature, len(concave_points), len(convex_edges), created_circles)

    def add_top_edge_chamfer(self, softjaw_body, top_plane_point, face_normal, component, chamfer_size):
        """
        Add a chamfer to the top edges of the pocket.
        Finds horizontal edges that lie on the top face plane.

        Returns tuple: (chamfer_feature, num_edges)
        - chamfer_feature: the chamfer feature created, or None if no edges found
        - num_edges: number of edges selected for chamfering

        top_plane_point: A point on the top face plane (captured before modifications)
        """
        edge_collection = adsk.core.ObjectCollection.create()

        for edge in softjaw_body.edges:
            geom = edge.geometry

            # Check if edge is horizontal (perpendicular to face normal)
            if geom.objectType == adsk.core.Line3D.classType():
                edge_dir = self.vector_between_points(geom.startPoint, geom.endPoint)
                edge_dir.normalize()
                if abs(edge_dir.dotProduct(face_normal)) > 0.01:
                    continue  # Not horizontal
            elif geom.objectType == adsk.core.Circle3D.classType() or geom.objectType == adsk.core.Arc3D.classType():
                # For arcs/circles, check if the axis is parallel to face normal
                if abs(abs(geom.normal.dotProduct(face_normal)) - 1.0) > 0.01:
                    continue  # Arc not in horizontal plane
            else:
                continue  # Skip other edge types

            # Check if edge lies on the top face plane
            evaluator = edge.evaluator
            _, param_start, param_end = evaluator.getParameterExtents()
            _, start_pt = evaluator.getPointAtParameter(param_start)
            _, end_pt = evaluator.getPointAtParameter(param_end)

            vec_start = self.vector_between_points(top_plane_point, start_pt)
            vec_end = self.vector_between_points(top_plane_point, end_pt)

            dist_start = abs(vec_start.dotProduct(face_normal))
            dist_end = abs(vec_end.dotProduct(face_normal))

            if dist_start < 0.001 and dist_end < 0.001:
                edge_collection.add(edge)

        if edge_collection.count == 0:
            return (None, 0)

        chamfer_features = component.features.chamferFeatures
        chamfer_input = chamfer_features.createInput2()
        chamfer_input.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
            edge_collection,
            adsk.core.ValueInput.createByReal(chamfer_size),
            True
        )
        try:
            return (chamfer_features.add(chamfer_input), edge_collection.count)
        except RuntimeError:
            # Chamfer may fail if edges are too small or geometry is incompatible
            return (None, 0)

    def _create_intersection_sketch(self, softjaw_component, top_face, part_body, additional_offset):
        """
        Create a sketch with the part body intersection on a construction plane.
        Returns (sketch, construction_plane, intersection_entities) or (None, None, None) on failure.
        """
        fusion = Fusion()

        # Create a construction plane at the top face location
        # This avoids including face boundary curves in the sketch, which is important
        # for non-contiguous softjaw blanks (e.g., U-shaped jaws with two prongs)
        construction_planes = softjaw_component.constructionPlanes
        plane_input = construction_planes.createInput()
        plane_input.setByOffset(top_face, adsk.core.ValueInput.createByReal(0))
        construction_plane = construction_planes.add(plane_input)

        # Tag the construction plane so we can find and delete it during edit
        construction_plane.attributes.add('ToolpathMakeSoftjaws', 'construction_plane', 'true')

        # Create sketch on the construction plane (not the face)
        sketches = softjaw_component.sketches
        sketch = sketches.add(construction_plane)

        # intersectWithSketchPlane: Creates curves where the part body physically
        # crosses the sketch plane. This gives the exact cross-section at that plane.
        intersection_entities = sketch.intersectWithSketchPlane([part_body])

        # If additional offset specified, offset the intersection curves outward
        if additional_offset > 0:
            curves_collection = adsk.core.ObjectCollection.create()
            for entity in intersection_entities:
                curves_collection.add(entity)

            # Calculate bounding box of all curves to determine "outward" direction
            min_x, min_y = float('inf'), float('inf')
            max_x, max_y = float('-inf'), float('-inf')
            for entity in intersection_entities:
                bb = entity.boundingBox
                min_x = min(min_x, bb.minPoint.x)
                min_y = min(min_y, bb.minPoint.y)
                max_x = max(max_x, bb.maxPoint.x)
                max_y = max(max_y, bb.maxPoint.y)

            # Direction point: far outside the bounding box (for outward offset)
            outer_point = adsk.core.Point3D.create(max_x + 1000, max_y + 1000, 0)
            offset_curves = sketch.offset(curves_collection, outer_point, additional_offset)

            if offset_curves and len(offset_curves) > 0:
                for entity in intersection_entities:
                    entity.deleteMe()
                intersection_entities = offset_curves
            else:
                ui = fusion.getUI()
                ui.messageBox("Warning: Could not create offset curves. Using original silhouette.")

        return sketch, construction_plane, intersection_entities

    def _add_debug_labels(self, sketch, intersection_entities):
        """
        Add debug labels to sketch points. Returns a position-to-index map (using world coordinates).
        """
        # Collect points that are endpoints of intersection curves
        profile_point_tokens = set()
        for entity in intersection_entities:
            if hasattr(entity, 'startSketchPoint') and entity.startSketchPoint:
                profile_point_tokens.add(entity.startSketchPoint.entityToken)
            if hasattr(entity, 'endSketchPoint') and entity.endSketchPoint:
                profile_point_tokens.add(entity.endSketchPoint.entityToken)

        # Collect profile points with their indices, deduplicating by world position
        points_to_label = []
        labeled_positions = set()
        for i in range(sketch.sketchPoints.count):
            pt = sketch.sketchPoints.item(i)
            if pt.entityToken in profile_point_tokens:
                world_pt = pt.worldGeometry
                pos_key = (round(world_pt.x, 6), round(world_pt.y, 6))
                if pos_key not in labeled_positions:
                    labeled_positions.add(pos_key)
                    # Store both sketch geometry (for label position) and world position (for map key)
                    points_to_label.append((i, pt.geometry.copy(), world_pt))

        text_height = 0.3  # cm
        debug_point_index_map = {}
        log(f"DEBUG: Point index to coordinate mapping:", force_console=True)
        for i, sketch_position, world_position in points_to_label:
            # Use world coordinates for the map key (to match add_corner_relief lookups)
            pos_key = (round(world_position.x, 6), round(world_position.y, 6))
            debug_point_index_map[pos_key] = i
            log(f"DEBUG:   Point {i} -> {pos_key}", force_console=True)
            # Use sketch coordinates for label placement
            corner = adsk.core.Point3D.create(
                sketch_position.x + text_height * 2,
                sketch_position.y + text_height,
                sketch_position.z
            )
            text_input = sketch.sketchTexts.createInput2(str(i), text_height)
            text_input.setAsMultiLine(
                sketch_position, corner,
                adsk.core.HorizontalAlignments.LeftHorizontalAlignment,
                adsk.core.VerticalAlignments.BottomVerticalAlignment, 0
            )
            sketch.sketchTexts.add(text_input)
        log(f"DEBUG: Labeled {len(points_to_label)} profile boundary points", force_console=True)
        return debug_point_index_map

    def _select_intersection_profiles(self, profiles, intersection_tokens):
        """
        Find profiles where the OUTER loop is made from intersection curves.
        Returns an ObjectCollection of matching profiles.
        """
        profile_collection = adsk.core.ObjectCollection.create()
        for i in range(profiles.count):
            profile = profiles.item(i)
            for loop_idx in range(profile.profileLoops.count):
                loop = profile.profileLoops.item(loop_idx)
                if not loop.isOuter:
                    continue
                # Count how many curves in this loop are from the intersection
                loop_curves = loop.profileCurves.count
                intersection_curve_count = sum(
                    1 for curve_idx in range(loop_curves)
                    if loop.profileCurves.item(curve_idx).sketchEntity.entityToken in intersection_tokens
                )
                # If this is an outer loop AND it's made entirely from intersection curves
                if intersection_curve_count > 0 and intersection_curve_count == loop_curves:
                    profile_collection.add(profile)
                    break
        return profile_collection

    def _wrap_in_custom_feature(self, softjaw_component, sketch, last_feature, softjaw_body,
                                 part_body, construction_plane, min_corner_radius, chamfer_size,
                                 additional_offset, add_corner_relief, add_chamfer, auto_select_top):
        """Wrap all features in a custom feature for clean timeline."""
        fusion = Fusion()
        design = fusion.getDesign()

        custom_features = softjaw_component.features.customFeatures
        custom_input = custom_features.createInput(self._custom_feature_def)
        custom_input.setStartAndEndFeatures(sketch, last_feature)

        # Store parameters for edit recall
        units_mgr = design.unitsManager
        length_units = units_mgr.defaultLengthUnits

        custom_input.addCustomParameter(
            'min_corner_radius', 'Min Corner Radius',
            adsk.core.ValueInput.createByReal(min_corner_radius), length_units, True)
        custom_input.addCustomParameter(
            'chamfer_size', 'Chamfer Size',
            adsk.core.ValueInput.createByReal(chamfer_size), length_units, True)
        custom_input.addCustomParameter(
            'additional_offset', 'Additional Offset',
            adsk.core.ValueInput.createByReal(additional_offset), length_units, True)
        custom_input.addCustomParameter(
            'add_corner_relief', 'Add Corner Relief',
            adsk.core.ValueInput.createByReal(1.0 if add_corner_relief else 0.0), '', False)
        custom_input.addCustomParameter(
            'add_chamfer', 'Add Chamfer',
            adsk.core.ValueInput.createByReal(1.0 if add_chamfer else 0.0), '', False)
        custom_input.addCustomParameter(
            'auto_select_top', 'Auto Select Top',
            adsk.core.ValueInput.createByReal(1.0 if auto_select_top else 0.0), '', False)

        custom_feature = custom_features.add(custom_input)
        custom_feature.attributes.add('ToolpathMakeSoftjaws', 'softjaw_body_token', softjaw_body.entityToken)
        custom_feature.attributes.add('ToolpathMakeSoftjaws', 'part_body_token', part_body.entityToken)
        custom_feature.attributes.add('ToolpathMakeSoftjaws', 'construction_plane_token', construction_plane.entityToken)

    def perform_silhouette_extrude_cut(self, softjaw_body, part_body, top_face, min_corner_radius,
                                        add_corner_relief=True, add_chamfer=True, chamfer_size=0.0127,
                                        additional_offset=0.0, auto_select_top=True):
        """
        Create softjaws by projecting part silhouette onto softjaw and extruding cut.
        Returns a dict with stats: extrusion_depth, num_concave_corners, num_convex_corners, num_chamfer_edges.
        """
        fusion = Fusion()
        design = fusion.getDesign()
        softjaw_component = softjaw_body.parentComponent

        # Get face normal
        face_normal = self.get_face_normal(top_face)
        if face_normal is None:
            fusion.getUI().messageBox("Failed to get face normal from selected face.")
            return None

        # Capture reference point before modifications
        top_evaluator = top_face.evaluator
        _, top_plane_point = top_evaluator.getPointAtParameter(adsk.core.Point2D.create(0.5, 0.5))

        # Calculate extrusion depth
        lowest_z_depth = self.get_part_lowest_z_depth(part_body, top_face, face_normal)
        lowest_horizontal_face = self.find_lowest_horizontal_face(part_body, top_face, face_normal)

        use_distance_extrusion = lowest_horizontal_face is None
        if use_distance_extrusion:
            offset = 0.0
        else:
            plane_point = self.get_face_centroid(top_face)
            face_plane = lowest_horizontal_face.geometry
            vec = self.vector_between_points(plane_point, face_plane.origin)
            horizontal_face_depth = abs(vec.dotProduct(face_normal))
            offset = lowest_z_depth - horizontal_face_depth

        # Capture pending snapshots
        if design.snapshots.hasPendingSnapshot:
            design.snapshots.add()

        # Create intersection sketch
        sketch, construction_plane, intersection_entities = self._create_intersection_sketch(
            softjaw_component, top_face, part_body, additional_offset
        )

        # Collect intersection curve tokens
        intersection_tokens = {entity.entityToken for entity in intersection_entities}

        # Debug labeling
        if DEBUG:
            debug_point_index_map = self._add_debug_labels(sketch, intersection_entities)
        else:
            debug_point_index_map = None

        # Classify corners using 2D sketch geometry
        corner_2d_classifications = self.classify_corners_2d(sketch, intersection_entities)

        # Get profiles
        profiles = sketch.profiles
        if profiles.count == 0:
            fusion.getUI().messageBox("No closed profiles found from the intersection. Ensure the part overlaps the softjaw top face.")
            return None

        # Select profiles with outer loop from intersection curves
        profile_collection = self._select_intersection_profiles(profiles, intersection_tokens)
        if profile_collection.count == 0:
            fusion.getUI().messageBox("No profiles found with outer loop from intersection.")
            return None

        # Create extrude cut feature
        extrudes = softjaw_component.features.extrudeFeatures
        extrude_input = extrudes.createInput(profile_collection, adsk.fusion.FeatureOperations.CutFeatureOperation)

        # Set extrusion extent based on whether we have a horizontal face reference
        if use_distance_extrusion:
            # Use distance-based extrusion when no horizontal face is available
            distance_extent = adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByReal(lowest_z_depth)
            )
            extrude_input.setOneSideExtent(distance_extent, adsk.fusion.ExtentDirections.NegativeExtentDirection)
        else:
            # Set "to object" extent using the lowest horizontal face with offset
            offset_value = adsk.core.ValueInput.createByReal(offset)
            to_entity = adsk.fusion.ToEntityExtentDefinition.create(lowest_horizontal_face, False, offset_value)
            extrude_input.setOneSideExtent(to_entity, adsk.fusion.ExtentDirections.NegativeExtentDirection)

        # Set participant bodies - only cut into the softjaw body
        extrude_input.participantBodies = [softjaw_body]

        # Execute the extrude
        try:
            extrude_feature = extrudes.add(extrude_input)
        except Exception as e:
            ui = fusion.getUI()
            ui.messageBox(f"Extrude cut failed:\n{str(e)}")
            raise

        # Track the last feature created for timeline grouping
        last_feature = extrude_feature

        # Initialize result stats
        result = {
            'extrusion_depth': lowest_z_depth,
            'num_concave_corners': 0,
            'num_convex_corners': 0,
            'num_chamfer_edges': 0,
            'circle_centers': []
        }

        # Optionally add corner relief cuts for inside corners and fillets for outside corners
        if add_corner_relief:
            relief_feature, num_concave, num_convex, circle_centers = self.add_corner_relief(
                extrude_feature, softjaw_component, softjaw_body,
                construction_plane, top_plane_point, face_normal, min_corner_radius,
                debug_point_map=debug_point_index_map,
                corner_2d_map=corner_2d_classifications
            )
            result['num_concave_corners'] = num_concave
            result['num_convex_corners'] = num_convex
            result['circle_centers'] = circle_centers
            if relief_feature is not None:
                last_feature = relief_feature

        # Optionally add chamfer to top edges of pocket
        if add_chamfer:
            chamfer_feature, num_edges = self.add_top_edge_chamfer(softjaw_body, top_plane_point, face_normal, softjaw_component, chamfer_size)
            result['num_chamfer_edges'] = num_edges
            if chamfer_feature is not None:
                last_feature = chamfer_feature

        # Wrap all features in a custom feature for clean timeline (skip in debug mode)
        if not DEBUG and self._custom_feature_def is not None:
            self._wrap_in_custom_feature(
                softjaw_component, sketch, last_feature, softjaw_body, part_body,
                construction_plane, min_corner_radius, chamfer_size, additional_offset,
                add_corner_relief, add_chamfer, auto_select_top
            )

        return result
