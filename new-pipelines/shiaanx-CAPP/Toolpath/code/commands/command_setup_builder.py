import math
import re
import traceback

import adsk.core
import adsk.fusion

from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log, handle_error
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar


CUSTOM_FEATURE_ID = "ToolpathSetupBuilder"
EDIT_CMD_ID = "Toolpath_Setup_Builder_Edit"


class Cmd:
    def __init__(self):
        self.CMD_NAME = 'Setup Builder'
        self.CMD_ID = command_id_from_name(self.CMD_NAME)
        self.CMD_Description = 'Build setup containers with workholding, stock, and joints.'
        self.ICON_FOLDER = resource_path("toolpath_logo", '')
        self.local_handlers = []

        # Input IDs
        self.STOCK_INPUT_ID = "stock_input"
        self.STOCK_ERROR_ID = "stock_error"
        self.VISE_INPUT_ID = "vise_input"
        self.VISE_ERROR_ID = "vise_error"
        self.PLATE_INPUT_ID = "plate_input"
        self.PLATE_ERROR_ID = "plate_error"
        self.ZERO_POINT_DROPDOWN_ID = "zero_point_dropdown"

        # Track zero point occurrences for visibility toggling
        self.zero_point_occurrences = []
        self.zero_point_original_visibility = {}  # Store original visibility states

        # Track joint origins and joints visibility
        self.joint_origins_original_visibility = {}
        self.joints_original_visibility = {}

        # Track created joints for cleanup/recreation
        self.stock_vise_joints = []  # Stock-Vise joints (created once)
        self.vise_plate_joint = None  # Vise-Plate joint (recreated on dropdown change)

        # Store references to selected occurrences for joint creation
        self.stock_occ = None
        self.vise_occ = None
        self.plate_occ = None
        self.selected_zero_point = None  # Store selected zero point name for execute

        # Track if command was executed (OK clicked) vs cancelled
        self.command_executed = False

        # Custom feature support for edit capability
        self._custom_feature_def = None
        self._editing_feature = None  # Set when editing an existing custom feature

        # Required child components (that must have joint origins) for each container type
        self.STOCK_REQUIRED_CHILDREN = [
            "Stock Attachment",
            "Jaw Position 1",
            "Jaw Position 2",
            "Vise Center",
            "Part Attachment",
        ]

        self.VISE_REQUIRED_CHILDREN = [
            "Stock Attachment",
            "Zero Point Attachment",
            "Jaw Position 1",
            "Jaw Position 2",
            "Vise Center",
        ]

        self.PLATE_REQUIRED_CHILDREN = [
            "WCS",
            "Zero Point 1",
            "Machine Model",
        ]

    def cleanup_orphaned_joints(self):
        """
        Clean up joints that were created by Setup Builder but whose custom feature
        has been deleted. This handles the case where a user deletes the custom feature
        from the timeline - the associated joints should also be removed.
        """
        try:
            fusion = Fusion()
            design = fusion.getDesign()
            if design is None:
                return

            root_comp = design.rootComponent
            joints_to_delete = []

            # Find all joints with our attribute
            for joint in root_comp.allJoints:
                attr = joint.attributes.itemByName(CUSTOM_FEATURE_ID, 'parent_feature_token')
                if attr is not None:
                    # Check if the parent custom feature still exists
                    parent_token = attr.value
                    entities = design.findEntityByToken(parent_token)
                    if not entities or len(entities) == 0:
                        # Parent feature was deleted - mark joint for deletion
                        joints_to_delete.append(joint)

            # Delete orphaned joints
            for joint in joints_to_delete:
                try:
                    joint.deleteMe()
                except:
                    pass

        except Exception as e:
            log(f"Error cleaning up orphaned joints: {e}", force_console=True)

    def start(self):
        ui = None
        try:
            fusion = Fusion()
            ui = fusion.getUI()

            # Clean up any orphaned joints from previous sessions
            self.cleanup_orphaned_joints()

            # Create edit command definition FIRST (must exist before setting editCommandId)
            edit_cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
            if not edit_cmd_def:
                edit_cmd_def = ui.commandDefinitions.addButtonDefinition(
                    EDIT_CMD_ID,
                    'Edit Setup Builder',
                    'Edit an existing Setup Builder feature',
                    self.ICON_FOLDER
                )
            add_handler(edit_cmd_def.commandCreated, self.onEditCommandCreated, local_handlers=self.local_handlers)

            # Register custom feature definition (serves as edit handle for the joints)
            self._custom_feature_def = adsk.fusion.CustomFeatureDefinition.create(
                CUSTOM_FEATURE_ID,
                'Setup Builder',
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

    def onCommandCreated(self, args):
        try:
            # Clean up orphaned joints before showing the dialog
            self.cleanup_orphaned_joints()

            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            inputs = cmd.commandInputs

            # Stock selection
            sel_stock = inputs.addSelectionInput(
                self.STOCK_INPUT_ID,
                "Stock",
                "Select the stock container"
            )
            sel_stock.addSelectionFilter("Occurrences")
            sel_stock.setSelectionLimits(1, 1)
            sel_stock.tooltip = "Select the stock container component"

            # Stock error message (initially hidden)
            stock_error = inputs.addTextBoxCommandInput(
                self.STOCK_ERROR_ID,
                "",
                "",
                1,
                True
            )
            stock_error.isVisible = False

            # Vise selection
            sel_vise = inputs.addSelectionInput(
                self.VISE_INPUT_ID,
                "Vise",
                "Select the vise component"
            )
            sel_vise.addSelectionFilter("Occurrences")
            sel_vise.setSelectionLimits(1, 1)
            sel_vise.tooltip = "Select the vise component for workholding"

            # Vise error message (initially hidden)
            vise_error = inputs.addTextBoxCommandInput(
                self.VISE_ERROR_ID,
                "",
                "",
                1,
                True
            )
            vise_error.isVisible = False

            # Plate (clamping) selection
            sel_plate = inputs.addSelectionInput(
                self.PLATE_INPUT_ID,
                "Plate",
                "Select the clamping plate component"
            )
            sel_plate.addSelectionFilter("Occurrences")
            sel_plate.setSelectionLimits(1, 1)
            sel_plate.tooltip = "Select the clamping/fixture plate component"

            # Plate error message (initially hidden)
            plate_error = inputs.addTextBoxCommandInput(
                self.PLATE_ERROR_ID,
                "",
                "",
                1,
                True
            )
            plate_error.isVisible = False

            # Zero Point dropdown (initially hidden, populated when plate is selected)
            zero_point_dropdown = inputs.addDropDownCommandInput(
                self.ZERO_POINT_DROPDOWN_ID,
                "Zero Point",
                adsk.core.DropDownStyles.TextListDropDownStyle
            )
            zero_point_dropdown.isVisible = False
            zero_point_dropdown.tooltip = "Select which zero point to use for the vise-plate joint"

            # Register event handlers
            add_handler(cmd.execute, self.onCommandExecute, local_handlers=self.local_handlers)
            add_handler(cmd.executePreview, self.onExecutePreview, local_handlers=self.local_handlers)
            add_handler(cmd.inputChanged, self.onInputChanged, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.destroy, self.onCommandDestroy, local_handlers=self.local_handlers)

        except:
            log(traceback.format_exc())

    def find_valid_container_in_hierarchy(self, entity, required_children):
        """
        Scan upward through the model hierarchy to find a valid container.
        Starts from the selected entity and walks up through parent occurrences.
        Returns (valid_occurrence, is_valid, missing_children) tuple.
        - valid_occurrence: The first occurrence that passes validation, or the original if none found
        - is_valid: True if a valid container was found
        - missing_children: List of missing children (empty if valid)
        """
        # Get the starting occurrence
        if isinstance(entity, adsk.fusion.Occurrence):
            current_occ = entity
        elif hasattr(entity, 'assemblyContext') and entity.assemblyContext is not None:
            # Entity is inside an occurrence (e.g., a body, face, etc.)
            current_occ = entity.assemblyContext
        else:
            return (None, False, ["Cannot determine parent occurrence"])

        original_entity = entity

        # Walk up the hierarchy checking each occurrence
        while current_occ is not None:
            is_valid, missing = self.validate_container(current_occ, required_children)
            if is_valid:
                return (current_occ, True, [])
            # Move to parent occurrence
            current_occ = current_occ.assemblyContext

        # No valid container found - return original with validation result
        if isinstance(original_entity, adsk.fusion.Occurrence):
            is_valid, missing = self.validate_container(original_entity, required_children)
            return (original_entity, is_valid, missing)
        else:
            return (None, False, ["No valid container found in hierarchy"])

    def name_matches(self, actual_name, required_name):
        """
        Check if an actual component name matches a required name.
        Handles Fusion's naming convention where broken links get suffixes like " (1)", " (2)", etc.
        Examples:
            "Stock Attachment" matches "Stock Attachment"
            "Stock Attachment (1)" matches "Stock Attachment"
            "Stock Attachment (12)" matches "Stock Attachment"
            "Stock Attachment v2" does NOT match "Stock Attachment"
        """
        if actual_name == required_name:
            return True
        # Check for pattern: "Required Name (N)" where N is one or more digits
        pattern = re.escape(required_name) + r' \(\d+\)$'
        return re.match(pattern, actual_name) is not None

    def find_child_by_name(self, parent_occ, required_name):
        """
        Find a child occurrence by name, handling Fusion's (N) suffix convention.
        Returns the child occurrence or None if not found.
        """
        child_occs = parent_occ.childOccurrences
        for i in range(child_occs.count):
            child = child_occs.item(i)
            if self.name_matches(child.component.name, required_name):
                return child
        return None

    def validate_container(self, occurrence, required_children):
        """
        Validate that a container has the required children with joint origins.
        Returns (is_valid, missing_children) tuple.
        - is_valid: True if all required children are present with joint origins
        - missing_children: List of missing or invalid child names
        """
        if occurrence is None:
            return (False, ["No occurrence selected"])

        # Build a list of child component names that have joint origins
        children_with_joints = []
        child_occs = occurrence.childOccurrences
        for i in range(child_occs.count):
            child = child_occs.item(i)
            if child.component.jointOrigins.count > 0:
                children_with_joints.append(child.component.name)

        # Check for missing required children (using flexible name matching)
        missing = []
        for required in required_children:
            found = False
            for actual_name in children_with_joints:
                if self.name_matches(actual_name, required):
                    found = True
                    break
            if not found:
                missing.append(required)

        return (len(missing) == 0, missing)

    def populate_zero_point_dropdown(self, plate_occ, dropdown):
        """
        Find all Zero Point children in the plate and populate the dropdown.
        Does NOT create joints - that's handled by rebuild_all_joints.
        """
        # Clear existing items
        dropdown.listItems.clear()
        self.zero_point_occurrences = []
        self.zero_point_original_visibility = {}

        # Find all children whose component name starts with "Zero Point"
        child_occs = plate_occ.childOccurrences
        for i in range(child_occs.count):
            child = child_occs.item(i)
            if child.component.name.startswith("Zero Point"):
                # Store original visibility state
                self.zero_point_original_visibility[child.entityToken] = child.isLightBulbOn
                self.zero_point_occurrences.append(child)
                dropdown.listItems.add(child.component.name, False)

        # Select the first item by default if there are any
        if dropdown.listItems.count > 0:
            dropdown.listItems.item(0).isSelected = True
            dropdown.isVisible = True
        else:
            dropdown.isVisible = False

    def get_selected_zero_point(self, dropdown):
        """Get the currently selected zero point name from dropdown."""
        if dropdown is None or dropdown.listItems.count == 0:
            return None
        for i in range(dropdown.listItems.count):
            item = dropdown.listItems.item(i)
            if item.isSelected:
                return item.name
        return None

    def rebuild_all_joints(self, inputs):
        """
        Delete all joints and recreate them based on current selections.
        This is the single source of truth for joint creation.
        - Only stock selected: no joints
        - Stock + vise selected: create stock-vise joints only
        - All three selected: create all joints
        """
        # Always delete all existing joints first
        self.delete_all_joints()

        # Get inputs
        sel_stock = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.STOCK_INPUT_ID))
        sel_vise = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.VISE_INPUT_ID))
        sel_plate = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.PLATE_INPUT_ID))
        dropdown = adsk.core.DropDownCommandInput.cast(inputs.itemById(self.ZERO_POINT_DROPDOWN_ID))

        # Get occurrences from selections
        self.stock_occ = adsk.fusion.Occurrence.cast(sel_stock.selection(0).entity) if sel_stock.selectionCount == 1 else None
        self.vise_occ = adsk.fusion.Occurrence.cast(sel_vise.selection(0).entity) if sel_vise.selectionCount == 1 else None
        self.plate_occ = adsk.fusion.Occurrence.cast(sel_plate.selection(0).entity) if sel_plate.selectionCount == 1 else None

        # Only stock selected: no joints
        if self.stock_occ is None:
            return

        # Validate stock
        stock_valid, _ = self.validate_container(self.stock_occ, self.STOCK_REQUIRED_CHILDREN)
        if not stock_valid:
            return

        # Only stock selected (no vise): no joints
        if self.vise_occ is None:
            return

        # Validate vise
        vise_valid, _ = self.validate_container(self.vise_occ, self.VISE_REQUIRED_CHILDREN)
        if not vise_valid:
            return

        # Stock + vise selected: create stock-vise joints
        self.create_stock_vise_joints()

        # If no plate, we're done (only stock-vise joints)
        if self.plate_occ is None:
            return

        # Validate plate
        plate_valid, _ = self.validate_container(self.plate_occ, self.PLATE_REQUIRED_CHILDREN)
        if not plate_valid:
            return

        # Get selected zero point
        self.selected_zero_point = self.get_selected_zero_point(dropdown)
        if self.selected_zero_point is None:
            return

        # All three selected: also create vise-plate joint
        self.create_vise_plate_joint(self.selected_zero_point)

    def create_stock_vise_joints(self):
        """Create planar joints between stock and vise containers."""
        if self.stock_occ is None or self.vise_occ is None:
            return

        fusion = Fusion()
        design = fusion.getDesign()
        root_comp = design.rootComponent

        # Find matching joint origin names between stock and vise
        matching_names = []
        for name in self.STOCK_REQUIRED_CHILDREN:
            if name in self.VISE_REQUIRED_CHILDREN:
                matching_names.append(name)

        # Jaw joint names
        jaw_joints = ["Jaw Position 1", "Jaw Position 2"]

        # Determine jaw rotation once (same for both jaw positions)
        jaw_rotation_needed = self.should_rotate_jaw_joints(self.vise_occ)

        # Create planar joints between matching joint origins
        for name in matching_names:
            stock_jo = self.get_joint_origin_from_child(self.stock_occ, name)
            vise_jo = self.get_joint_origin_from_child(self.vise_occ, name)

            if stock_jo is not None and vise_jo is not None:
                joint_name = f"Stock-Vise: {name}"
                # Determine if joint should be flipped
                if name == "Stock Attachment":
                    # Base flip on stock container's internal orientation
                    is_flipped = self.should_flip_stock_attachment(self.stock_occ)
                else:
                    # Jaw joints always need isFlipped=True
                    is_flipped = True
                # Apply 180° rotation to jaw joints based on vise internal orientation
                rotation_degrees = 180 if (name in jaw_joints and jaw_rotation_needed) else 0
                try:
                    joint = self.create_planar_joint(root_comp, stock_jo, vise_jo, joint_name, is_flipped, rotation_degrees)
                    if joint is not None:
                        # Check for build warnings/errors
                        health_state = joint.healthState
                        if health_state != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
                            # Try with opposite flip setting
                            joint.deleteMe()
                            opposite_flip = not is_flipped
                            joint = self.create_planar_joint(root_comp, stock_jo, vise_jo, joint_name, opposite_flip, rotation_degrees)
                            if joint is not None:
                                health_state = joint.healthState
                                if health_state != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
                                    joint.deleteMe()
                                else:
                                    self.stock_vise_joints.append(joint)
                        else:
                            self.stock_vise_joints.append(joint)
                except Exception as e:
                    # Skip joints that fail to create
                    log(f"Skipping joint '{joint_name}': {e}", force_console=True)

    def create_vise_plate_joint(self, selected_zero_point_name):
        """Create rigid joint between vise and plate at the selected zero point."""
        if self.vise_occ is None or self.plate_occ is None:
            return

        fusion = Fusion()
        design = fusion.getDesign()
        root_comp = design.rootComponent

        vise_jo = self.get_joint_origin_from_child(self.vise_occ, "Zero Point Attachment")
        plate_jo = self.get_joint_origin_from_child(self.plate_occ, selected_zero_point_name)

        if vise_jo is not None and plate_jo is not None:
            joint_name = f"Vise-Plate: {selected_zero_point_name}"
            self.vise_plate_joint = self.create_rigid_joint(root_comp, vise_jo, plate_jo, joint_name)

    def delete_vise_plate_joint(self):
        """Delete the vise-plate joint if it exists."""
        if self.vise_plate_joint is not None:
            try:
                self.vise_plate_joint.deleteMe()
            except:
                pass
            self.vise_plate_joint = None

    def delete_all_joints(self):
        """Delete all created joints (stock-vise and vise-plate)."""
        # Delete vise-plate joint
        self.delete_vise_plate_joint()

        # Delete stock-vise joints
        for joint in self.stock_vise_joints:
            try:
                joint.deleteMe()
            except:
                pass
        self.stock_vise_joints = []

    def set_occurrence_visibility_recursive(self, occ, visible):
        """Set visibility for an occurrence and all its children recursively."""
        occ.isLightBulbOn = visible

        # Set visibility for joint origins using proxy
        component = occ.component
        for i in range(component.jointOrigins.count):
            jo = component.jointOrigins.item(i)
            jo_proxy = jo.createForAssemblyContext(occ)
            jo_proxy.isLightBulbOn = visible

        # Recursively set visibility for all child occurrences
        for i in range(occ.childOccurrences.count):
            child_occ = occ.childOccurrences.item(i)
            self.set_occurrence_visibility_recursive(child_occ, visible)

    def restore_zero_point_visibility(self):
        """Restore original visibility states for all zero point occurrences."""
        for occ in self.zero_point_occurrences:
            token = occ.entityToken
            if token in self.zero_point_original_visibility:
                occ.isLightBulbOn = self.zero_point_original_visibility[token]
        self.zero_point_occurrences = []
        self.zero_point_original_visibility = {}

    def hide_all_joint_origins_and_joints(self):
        """Hide all joint origins and joints in the design, storing original visibility."""
        fusion = Fusion()
        design = fusion.getDesign()
        root_comp = design.rootComponent

        # Store and hide all joint origins
        self.joint_origins_original_visibility = {}
        for jo in root_comp.allJointOrigins:
            self.joint_origins_original_visibility[jo.entityToken] = jo.isLightBulbOn
            jo.isLightBulbOn = False

        # Store and hide all joints (relationships)
        self.joints_original_visibility = {}
        for joint in root_comp.allJoints:
            self.joints_original_visibility[joint.entityToken] = joint.isLightBulbOn
            joint.isLightBulbOn = False

    def restore_joint_origins_and_joints_visibility(self):
        """Restore original visibility for all joint origins and joints."""
        fusion = Fusion()
        design = fusion.getDesign()
        root_comp = design.rootComponent

        # Restore joint origins visibility
        for jo in root_comp.allJointOrigins:
            token = jo.entityToken
            if token in self.joint_origins_original_visibility:
                jo.isLightBulbOn = self.joint_origins_original_visibility[token]

        # Restore joints visibility
        for joint in root_comp.allJoints:
            token = joint.entityToken
            if token in self.joints_original_visibility:
                joint.isLightBulbOn = self.joints_original_visibility[token]

        self.joint_origins_original_visibility = {}
        self.joints_original_visibility = {}

    def update_error_message(self, error_input, is_valid, missing_children):
        """Update error message text box visibility and content."""
        if is_valid:
            error_input.isVisible = False
            error_input.formattedText = ""
        else:
            missing_str = ", ".join(missing_children)
            error_input.formattedText = f'<font color="red">Missing: {missing_str}</font>'
            error_input.isVisible = True

    def handle_container_selection(self, sel_input, error_input, required_children,
                                     next_focus_input=None, dropdown=None):
        """
        Handle selection for a container input - find valid parent and update UI.
        Returns (valid_occ, is_valid) tuple.
        """
        if sel_input.selectionCount != 1:
            error_input.isVisible = False
            if dropdown is not None:
                dropdown.isVisible = False
            return (None, False)

        entity = sel_input.selection(0).entity
        valid_occ, is_valid, missing = self.find_valid_container_in_hierarchy(
            entity, required_children
        )

        # Replace selection with valid parent if found
        if is_valid and valid_occ is not None:
            original_occ = adsk.fusion.Occurrence.cast(entity) if isinstance(entity, adsk.fusion.Occurrence) else None
            if original_occ is None or valid_occ.entityToken != original_occ.entityToken:
                sel_input.clearSelection()
                sel_input.addSelection(valid_occ)

        self.update_error_message(error_input, is_valid, missing)

        if is_valid:
            if next_focus_input is not None:
                next_focus_input.hasFocus = True
            if dropdown is not None and valid_occ is not None:
                self.populate_zero_point_dropdown(valid_occ, dropdown)

        return (valid_occ, is_valid)

    def onInputChanged(self, args):
        """Handle input changes - UI updates only. Joint creation happens in executePreview."""
        try:
            eventArgs = adsk.core.InputChangedEventArgs.cast(args)
            changedInput = eventArgs.input
            inputs = eventArgs.firingEvent.sender.commandInputs

            sel_stock = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.STOCK_INPUT_ID))
            sel_vise = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.VISE_INPUT_ID))
            sel_plate = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.PLATE_INPUT_ID))
            stock_error = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.STOCK_ERROR_ID))
            vise_error = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.VISE_ERROR_ID))
            plate_error = adsk.core.TextBoxCommandInput.cast(inputs.itemById(self.PLATE_ERROR_ID))
            zero_point_dropdown = adsk.core.DropDownCommandInput.cast(inputs.itemById(self.ZERO_POINT_DROPDOWN_ID))

            # Update validation errors and auto-advance for selection changes
            if changedInput.id == self.STOCK_INPUT_ID:
                self.handle_container_selection(
                    sel_stock, stock_error, self.STOCK_REQUIRED_CHILDREN,
                    next_focus_input=sel_vise
                )

            elif changedInput.id == self.VISE_INPUT_ID:
                self.handle_container_selection(
                    sel_vise, vise_error, self.VISE_REQUIRED_CHILDREN,
                    next_focus_input=sel_plate
                )

            elif changedInput.id == self.PLATE_INPUT_ID:
                self.handle_container_selection(
                    sel_plate, plate_error, self.PLATE_REQUIRED_CHILDREN,
                    dropdown=zero_point_dropdown
                )

        except:
            log(traceback.format_exc())

    def onValidateInputs(self, args):
        """Validate that all required inputs are selected and have correct structure."""
        try:
            eventArgs = adsk.core.ValidateInputsEventArgs.cast(args)
            inputs = eventArgs.firingEvent.sender.commandInputs

            sel_stock = adsk.core.SelectionCommandInput.cast(
                inputs.itemById(self.STOCK_INPUT_ID)
            )
            sel_vise = adsk.core.SelectionCommandInput.cast(
                inputs.itemById(self.VISE_INPUT_ID)
            )
            sel_plate = adsk.core.SelectionCommandInput.cast(
                inputs.itemById(self.PLATE_INPUT_ID)
            )

            # If we have stored occurrences (joints already created), consider valid
            # This handles the case where Fusion clears selections during OK click
            if self.stock_occ is not None and self.vise_occ is not None and self.plate_occ is not None:
                eventArgs.areInputsValid = True
                return

            # Otherwise, require all three selections from UI
            if sel_stock.selectionCount != 1:
                eventArgs.areInputsValid = False
                return
            if sel_vise.selectionCount != 1:
                eventArgs.areInputsValid = False
                return
            if sel_plate.selectionCount != 1:
                eventArgs.areInputsValid = False
                return

            # Validate Stock container structure
            stock_occ = adsk.fusion.Occurrence.cast(sel_stock.selection(0).entity)
            stock_valid, stock_missing = self.validate_container(stock_occ, self.STOCK_REQUIRED_CHILDREN)
            if not stock_valid:
                eventArgs.areInputsValid = False
                return

            # Validate Vise container structure
            vise_occ = adsk.fusion.Occurrence.cast(sel_vise.selection(0).entity)
            vise_valid, vise_missing = self.validate_container(vise_occ, self.VISE_REQUIRED_CHILDREN)
            if not vise_valid:
                eventArgs.areInputsValid = False
                return

            # Validate Plate container structure
            plate_occ = adsk.fusion.Occurrence.cast(sel_plate.selection(0).entity)
            plate_valid, plate_missing = self.validate_container(plate_occ, self.PLATE_REQUIRED_CHILDREN)
            if not plate_valid:
                eventArgs.areInputsValid = False
                return

            eventArgs.areInputsValid = True

        except:
            log(traceback.format_exc())
            eventArgs.areInputsValid = False

    def find_lowest_common_parent(self, occ1, occ2):
        """
        Find the lowest common parent component that contains both occurrences.
        Returns the component where joints should be created.
        """
        fusion = Fusion()
        design = fusion.getDesign()

        # Build path to root for occ1 (using entity tokens for comparison)
        path1_tokens = set()
        path1_map = {}  # token -> component
        current = occ1
        while current is not None:
            token = current.component.entityToken
            path1_tokens.add(token)
            path1_map[token] = current.component
            current = current.assemblyContext

        # Also add root component
        root_token = design.rootComponent.entityToken
        path1_tokens.add(root_token)
        path1_map[root_token] = design.rootComponent

        # Find first occurrence in occ2's path whose component is also in occ1's path
        current = occ2
        while current is not None:
            token = current.component.entityToken
            if token in path1_tokens:
                return path1_map[token]
            current = current.assemblyContext

        # If no common parent found, use root component
        return design.rootComponent

    def get_joint_origin_from_child(self, parent_occ, child_component_name):
        """
        Get a joint origin from a child occurrence by component name.
        The joint origin is expected to have the same name as the component.
        Handles Fusion's (N) suffix naming convention for broken links.
        Returns the joint origin in assembly context, or None if not found.
        """
        child = self.find_child_by_name(parent_occ, child_component_name)
        if child is not None:
            joint_origins = child.component.jointOrigins
            if joint_origins.count > 0:
                # Get the first joint origin
                jo = joint_origins.item(0)
                # Create assembly context for the joint origin
                return jo.createForAssemblyContext(child)
        return None

    def should_flip_stock_attachment(self, stock_occ):
        """
        Determine if the Stock Attachment joint should be flipped when connecting
        stock to vise, based on the stock container's internal orientation.

        Compares the Z-axes of "Stock Attachment" and "Part Attachment" within
        the stock container:
        - If same direction: stock is "right-side up" -> flip needed
        - If opposite direction: stock is "upside down" internally -> no flip needed

        Returns True if flip is needed, False otherwise.
        """
        try:
            stock_attachment_jo = self.get_joint_origin_from_child(stock_occ, "Stock Attachment")
            part_attachment_jo = self.get_joint_origin_from_child(stock_occ, "Part Attachment")

            if stock_attachment_jo is None or part_attachment_jo is None:
                return False

            z_stock = stock_attachment_jo.primaryAxisVector
            z_part = part_attachment_jo.primaryAxisVector

            dot_product = z_stock.x * z_part.x + z_stock.y * z_part.y + z_stock.z * z_part.z

            # If Z-axes point same direction (dot > 0), flip is needed
            # If Z-axes point opposite (dot < 0), no flip needed
            return dot_product > 0
        except Exception as e:
            log(f"Error determining stock flip: {e}", force_console=True)
            return False

    def should_rotate_jaw_joints(self, vise_occ):
        """
        Determine if the Jaw Position joints need 180° rotation when connecting
        stock to vise, based on the vise container's internal jaw orientation.

        Looks at existing joints in the vise container that connect Jaw Position
        to the vise jaws. If the Z-axes are opposite (dot < -0.5), rotation is needed.
        If perpendicular or same direction, no rotation needed.

        Returns True if 180° rotation is needed, False otherwise.
        """
        try:
            # Get all joints in the vise container's component
            vise_component = vise_occ.component
            all_joints = list(vise_component.allJoints)

            # Look for a joint involving "Jaw Position 1" or "Jaw Position 2"
            for joint in all_joints:
                geo1 = joint.geometryOrOriginOne
                geo2 = joint.geometryOrOriginTwo

                # Check if this joint involves a Jaw Position joint origin
                jo_info = None
                other_geo = None

                if geo1 is not None and hasattr(geo1, 'name'):
                    if self.name_matches(geo1.name, "Jaw Position 1") or self.name_matches(geo1.name, "Jaw Position 2"):
                        jo_info = geo1
                        other_geo = geo2

                if jo_info is None and geo2 is not None and hasattr(geo2, 'name'):
                    if self.name_matches(geo2.name, "Jaw Position 1") or self.name_matches(geo2.name, "Jaw Position 2"):
                        jo_info = geo2
                        other_geo = geo1

                if jo_info is not None and other_geo is not None:
                    # Found a joint with Jaw Position - compare Z-axes
                    if hasattr(jo_info, 'primaryAxisVector') and hasattr(other_geo, 'primaryAxisVector'):
                        z_jaw_pos = jo_info.primaryAxisVector
                        z_vise_jaw = other_geo.primaryAxisVector

                        dot_product = z_jaw_pos.x * z_vise_jaw.x + z_jaw_pos.y * z_vise_jaw.y + z_jaw_pos.z * z_vise_jaw.z

                        # If Z-axes are opposite (dot < -0.5), rotation is needed
                        # If perpendicular or same, no rotation needed
                        return dot_product < -0.5

            # No internal jaw joint found - default to False (no rotation)
            return False
        except Exception as e:
            log(f"Error determining jaw rotation: {e}", force_console=True)
            return False

    def get_unique_joint_name(self, joints, base_name):
        """
        Get a unique joint name by appending (N) suffix if needed.
        """
        if joints.itemByName(base_name) is None:
            return base_name

        # Find the next available number
        counter = 1
        while True:
            candidate = f"{base_name} ({counter})"
            if joints.itemByName(candidate) is None:
                return candidate
            counter += 1

    def create_planar_joint(self, parent_component, joint_origin_1, joint_origin_2, joint_name, is_flipped=False, rotation_degrees=0):
        """
        Create a planar joint between two joint origins.
        rotation_degrees: Rotation angle around Z-axis in degrees (applied to joint).
        """
        joints = parent_component.joints

        # Get unique name if this name already exists
        unique_name = self.get_unique_joint_name(joints, joint_name)

        joint_input = joints.createInput(joint_origin_1, joint_origin_2)
        joint_input.setAsPlanarJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
        joint_input.isFlipped = is_flipped

        # Apply rotation if specified
        if abs(rotation_degrees) > 0.001:
            rotation_radians = math.radians(rotation_degrees)
            joint_input.angle = adsk.core.ValueInput.createByReal(rotation_radians)

        new_joint = joints.add(joint_input)

        # Try to set the name, but don't fail if it doesn't work
        try:
            new_joint.name = unique_name
        except:
            pass  # Joint created successfully, name just couldn't be set

        return new_joint

    def create_rigid_joint(self, parent_component, joint_origin_1, joint_origin_2, joint_name):
        """
        Create a rigid joint between two joint origins.
        """
        joints = parent_component.joints

        # Get unique name if this name already exists
        unique_name = self.get_unique_joint_name(joints, joint_name)

        joint_input = joints.createInput(joint_origin_1, joint_origin_2)
        joint_input.setAsRigidJointMotion()

        new_joint = joints.add(joint_input)

        # Try to set the name, but don't fail if it doesn't work
        try:
            new_joint.name = unique_name
        except:
            pass  # Joint created successfully, name just couldn't be set

        return new_joint

    def onExecutePreview(self, args):
        """Preview handler - creates joints for preview. Fusion handles undo/redo automatically."""
        try:
            eventArgs = adsk.core.CommandEventArgs.cast(args)
            inputs = eventArgs.command.commandInputs

            # Rebuild all joints for preview
            self.rebuild_all_joints(inputs)

            # IMPORTANT: Set isValidResult to False so Fusion calls execute handler
            # If True, Fusion commits the preview directly and skips execute
            eventArgs.isValidResult = False
        except:
            log(traceback.format_exc())

    def onCommandExecute(self, args):
        try:
            # Mark command as executed (OK clicked)
            self.command_executed = True

            fusion = Fusion()
            design = fusion.getDesign()
            timeline = design.timeline

            # Store occurrence tokens before creating joints (for edit recall)
            stock_token = self.stock_occ.entityToken if self.stock_occ else None
            vise_token = self.vise_occ.entityToken if self.vise_occ else None
            plate_token = self.plate_occ.entityToken if self.plate_occ else None

            # Record timeline index before creating joints
            start_index = timeline.count

            # Clear joint lists (preview joints were rolled back, references are invalid)
            self.stock_vise_joints = []
            self.vise_plate_joint = None

            # Create joints using stored occurrences and zero point
            # (Preview has been rolled back, so we use the values stored during preview)
            if self.stock_occ is not None and self.vise_occ is not None:
                self.create_stock_vise_joints()

                if self.plate_occ is not None and self.selected_zero_point is not None:
                    self.create_vise_plate_joint(self.selected_zero_point)

            # Collect joint tokens for edit recall
            joint_tokens = []
            for joint in self.stock_vise_joints:
                joint_tokens.append(joint.entityToken)
            if self.vise_plate_joint is not None:
                joint_tokens.append(self.vise_plate_joint.entityToken)

            # Group all created joints into a timeline folder
            end_index = timeline.count - 1
            timeline_group = None
            timeline_group_token = None
            if end_index >= start_index:
                timeline_group = timeline.timelineGroups.add(start_index, end_index)
                timeline_group.name = "Setup Builder Joints"
                # Store timeline group token for cleanup
                # Accessing .entity can throw if the associated feature is invalid
                try:
                    timeline_group_token = timeline_group.entity.entityToken if timeline_group.entity else None
                except RuntimeError:
                    timeline_group_token = None

            # Create a sketch as a placeholder for the custom feature
            # This allows the custom feature to be double-clicked for editing
            if self._custom_feature_def is not None and len(joint_tokens) > 0:
                root_comp = design.rootComponent

                # Create a minimal sketch on XY plane as placeholder
                sketches = root_comp.sketches
                xy_plane = root_comp.xYConstructionPlane
                placeholder_sketch = sketches.add(xy_plane)
                placeholder_sketch.name = "Setup Builder Edit Handle"
                placeholder_sketch.isVisible = False  # Hide it
                # Add a point so the sketch isn't empty
                placeholder_sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))

                # Create custom feature wrapping the sketch
                custom_features = root_comp.features.customFeatures
                custom_input = custom_features.createInput(self._custom_feature_def)
                custom_input.setStartAndEndFeatures(placeholder_sketch, placeholder_sketch)

                custom_feature = custom_features.add(custom_input)

                # Store occurrence tokens, zero point, and joint tokens on custom feature
                if stock_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'stock_token', stock_token)
                if vise_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'vise_token', vise_token)
                if plate_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'plate_token', plate_token)
                if self.selected_zero_point:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'zero_point_name', self.selected_zero_point)
                # Store joint tokens as comma-separated string
                custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'joint_tokens', ','.join(joint_tokens))
                # Store timeline group token for cleanup
                if timeline_group_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'timeline_group_token', timeline_group_token)

                # Tag each joint with the custom feature token for cleanup tracking
                custom_feature_token = custom_feature.entityToken
                for joint in self.stock_vise_joints:
                    if joint.isValid:
                        joint.attributes.add(CUSTOM_FEATURE_ID, 'parent_feature_token', custom_feature_token)
                if self.vise_plate_joint is not None and self.vise_plate_joint.isValid:
                    self.vise_plate_joint.attributes.add(CUSTOM_FEATURE_ID, 'parent_feature_token', custom_feature_token)

            # Clear tracking (joints now belong to the document, not our preview)
            self.stock_vise_joints = []
            self.vise_plate_joint = None
            self.stock_occ = None
            self.vise_occ = None
            self.plate_occ = None
            self.selected_zero_point = None

        except:
            log(traceback.format_exc())
            ui = Fusion().getUI()
            ui.messageBox(f'Error in Setup Builder:\n{traceback.format_exc()}')

    def onCommandDestroy(self, args):
        try:
            # If command was cancelled (not executed), delete created joints
            if not self.command_executed:
                self.delete_all_joints()

            # Reset state for next invocation
            self.command_executed = False
            self.stock_occ = None
            self.vise_occ = None
            self.plate_occ = None

            # Restore visibility of zero points and joint origins/joints when dialog closes
            self.restore_zero_point_visibility()
            self.restore_joint_origins_and_joints_visibility()
        except:
            log(traceback.format_exc())

    def onEditCommandCreated(self, args):
        """Handle edit command creation - populate dialog with stored values from custom feature."""
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            inputs = cmd.commandInputs

            fusion = Fusion()
            ui = fusion.getUI()
            design = fusion.getDesign()

            # Get the custom feature being edited from active selections
            if ui.activeSelections.count == 0:
                ui.messageBox("No feature selected for editing.")
                return

            self._editing_feature = adsk.fusion.CustomFeature.cast(ui.activeSelections.item(0).entity)
            if self._editing_feature is None:
                ui.messageBox("Selected item is not a custom feature.")
                return

            # Retrieve stored occurrence tokens from attributes
            stock_token_attr = self._editing_feature.attributes.itemByName(CUSTOM_FEATURE_ID, 'stock_token')
            vise_token_attr = self._editing_feature.attributes.itemByName(CUSTOM_FEATURE_ID, 'vise_token')
            plate_token_attr = self._editing_feature.attributes.itemByName(CUSTOM_FEATURE_ID, 'plate_token')
            zero_point_attr = self._editing_feature.attributes.itemByName(CUSTOM_FEATURE_ID, 'zero_point_name')

            stock_token = stock_token_attr.value if stock_token_attr else None
            vise_token = vise_token_attr.value if vise_token_attr else None
            plate_token = plate_token_attr.value if plate_token_attr else None
            stored_zero_point = zero_point_attr.value if zero_point_attr else None

            # Create Stock selection input
            sel_stock = inputs.addSelectionInput(
                self.STOCK_INPUT_ID,
                "Stock",
                "Select the stock container"
            )
            sel_stock.addSelectionFilter("Occurrences")
            sel_stock.setSelectionLimits(1, 1)

            # Stock error message (initially hidden)
            stock_error = inputs.addTextBoxCommandInput(self.STOCK_ERROR_ID, "", "", 1, True)
            stock_error.isVisible = False

            # Create Vise selection input
            sel_vise = inputs.addSelectionInput(
                self.VISE_INPUT_ID,
                "Vise",
                "Select the vise component"
            )
            sel_vise.addSelectionFilter("Occurrences")
            sel_vise.setSelectionLimits(1, 1)

            # Vise error message (initially hidden)
            vise_error = inputs.addTextBoxCommandInput(self.VISE_ERROR_ID, "", "", 1, True)
            vise_error.isVisible = False

            # Create Plate selection input
            sel_plate = inputs.addSelectionInput(
                self.PLATE_INPUT_ID,
                "Plate",
                "Select the clamping plate component"
            )
            sel_plate.addSelectionFilter("Occurrences")
            sel_plate.setSelectionLimits(1, 1)

            # Plate error message (initially hidden)
            plate_error = inputs.addTextBoxCommandInput(self.PLATE_ERROR_ID, "", "", 1, True)
            plate_error.isVisible = False

            # Zero Point dropdown
            zero_point_dropdown = inputs.addDropDownCommandInput(
                self.ZERO_POINT_DROPDOWN_ID,
                "Zero Point",
                adsk.core.DropDownStyles.TextListDropDownStyle
            )
            zero_point_dropdown.isVisible = False

            # Pre-select occurrences from stored tokens
            if stock_token:
                stock_entities = design.findEntityByToken(stock_token)
                if stock_entities and len(stock_entities) > 0:
                    sel_stock.addSelection(stock_entities[0])

            if vise_token:
                vise_entities = design.findEntityByToken(vise_token)
                if vise_entities and len(vise_entities) > 0:
                    sel_vise.addSelection(vise_entities[0])

            if plate_token:
                plate_entities = design.findEntityByToken(plate_token)
                if plate_entities and len(plate_entities) > 0:
                    plate_occ = plate_entities[0]
                    sel_plate.addSelection(plate_occ)
                    # Populate zero point dropdown
                    self.populate_zero_point_dropdown(plate_occ, zero_point_dropdown)
                    # Select the stored zero point if it exists
                    if stored_zero_point:
                        for i in range(zero_point_dropdown.listItems.count):
                            item = zero_point_dropdown.listItems.item(i)
                            if item.name == stored_zero_point:
                                # Deselect all first
                                for j in range(zero_point_dropdown.listItems.count):
                                    zero_point_dropdown.listItems.item(j).isSelected = False
                                item.isSelected = True
                                break

            # Register event handlers
            add_handler(cmd.execute, self.onEditExecute, local_handlers=self.local_handlers)
            add_handler(cmd.executePreview, self.onEditPreview, local_handlers=self.local_handlers)
            add_handler(cmd.inputChanged, self.onInputChanged, local_handlers=self.local_handlers)
            add_handler(cmd.validateInputs, self.onValidateInputs, local_handlers=self.local_handlers)
            add_handler(cmd.destroy, self.onEditDestroy, local_handlers=self.local_handlers)

        except:
            log(traceback.format_exc())

    def onEditPreview(self, args):
        """Preview handler for edit - rebuilds joints for preview."""
        try:
            eventArgs = adsk.core.CommandEventArgs.cast(args)
            inputs = eventArgs.command.commandInputs

            # Delete old joints first
            self._delete_joints_from_custom_feature()

            # Rebuild joints for preview
            self.rebuild_all_joints(inputs)

            eventArgs.isValidResult = False
        except:
            log(traceback.format_exc())

    def onEditDestroy(self, args):
        """Handle edit command destroy - cleanup."""
        try:
            # Reset edit state
            self._editing_feature = None
            self.stock_occ = None
            self.vise_occ = None
            self.plate_occ = None

            # Clear joint tracking
            self.stock_vise_joints = []
            self.vise_plate_joint = None

            # Restore visibility
            self.restore_zero_point_visibility()
            self.restore_joint_origins_and_joints_visibility()
        except:
            log(traceback.format_exc())

    def _delete_joints_from_custom_feature(self):
        """Delete joints stored in the custom feature being edited."""
        if self._editing_feature is None or not self._editing_feature.isValid:
            return

        fusion = Fusion()
        design = fusion.getDesign()

        # Get stored joint tokens
        joint_tokens_attr = self._editing_feature.attributes.itemByName(CUSTOM_FEATURE_ID, 'joint_tokens')
        if joint_tokens_attr is None:
            return

        joint_tokens = joint_tokens_attr.value.split(',')
        for token in joint_tokens:
            if token:
                entities = design.findEntityByToken(token)
                if entities and len(entities) > 0:
                    joint = entities[0]
                    if joint.isValid:
                        try:
                            joint.deleteMe()
                        except:
                            pass

    def onEditExecute(self, args):
        """Execute edit - delete old joints and recreate with new values."""
        try:
            fusion = Fusion()
            design = fusion.getDesign()
            timeline = design.timeline
            root_comp = design.rootComponent

            command = args.firingEvent.sender
            inputs = command.commandInputs

            # Get current selections
            sel_stock = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.STOCK_INPUT_ID))
            sel_vise = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.VISE_INPUT_ID))
            sel_plate = adsk.core.SelectionCommandInput.cast(inputs.itemById(self.PLATE_INPUT_ID))
            dropdown = adsk.core.DropDownCommandInput.cast(inputs.itemById(self.ZERO_POINT_DROPDOWN_ID))

            # Get occurrences from selections
            stock_occ = adsk.fusion.Occurrence.cast(sel_stock.selection(0).entity) if sel_stock.selectionCount == 1 else None
            vise_occ = adsk.fusion.Occurrence.cast(sel_vise.selection(0).entity) if sel_vise.selectionCount == 1 else None
            plate_occ = adsk.fusion.Occurrence.cast(sel_plate.selection(0).entity) if sel_plate.selectionCount == 1 else None
            selected_zero_point = self.get_selected_zero_point(dropdown)

            # Store tokens
            stock_token = stock_occ.entityToken if stock_occ else None
            vise_token = vise_occ.entityToken if vise_occ else None
            plate_token = plate_occ.entityToken if plate_occ else None

            custom_feat = self._editing_feature

            # Roll timeline to just before the custom feature and delete old joints
            if custom_feat and custom_feat.isValid:
                custom_feat.timelineObject.rollTo(True)
                self._delete_joints_from_custom_feature()

                # Delete the old custom feature (and its construction point)
                custom_feat.deleteMe()

            # Re-acquire occurrences after timeline rollback
            if stock_token:
                entities = design.findEntityByToken(stock_token)
                stock_occ = entities[0] if entities and len(entities) > 0 else None
            if vise_token:
                entities = design.findEntityByToken(vise_token)
                vise_occ = entities[0] if entities and len(entities) > 0 else None
            if plate_token:
                entities = design.findEntityByToken(plate_token)
                plate_occ = entities[0] if entities and len(entities) > 0 else None

            # Store for joint creation
            self.stock_occ = stock_occ
            self.vise_occ = vise_occ
            self.plate_occ = plate_occ
            self.selected_zero_point = selected_zero_point

            # Record timeline index before creating joints
            start_index = timeline.count

            # Clear joint lists before creating new joints
            self.stock_vise_joints = []
            self.vise_plate_joint = None

            # Recreate joints
            if self.stock_occ is not None and self.vise_occ is not None:
                self.create_stock_vise_joints()

                if self.plate_occ is not None and self.selected_zero_point is not None:
                    self.create_vise_plate_joint(self.selected_zero_point)

            # Collect joint tokens
            joint_tokens = []
            for joint in self.stock_vise_joints:
                joint_tokens.append(joint.entityToken)
            if self.vise_plate_joint is not None:
                joint_tokens.append(self.vise_plate_joint.entityToken)

            # Group joints in timeline
            end_index = timeline.count - 1
            timeline_group = None
            timeline_group_token = None
            if end_index >= start_index:
                timeline_group = timeline.timelineGroups.add(start_index, end_index)
                timeline_group.name = "Setup Builder Joints"
                timeline_group_token = timeline_group.entity.entityToken if timeline_group.entity else None

            # Create new custom feature as edit handle
            if self._custom_feature_def is not None and len(joint_tokens) > 0:
                # Create a minimal sketch on XY plane as placeholder
                sketches = root_comp.sketches
                xy_plane = root_comp.xYConstructionPlane
                placeholder_sketch = sketches.add(xy_plane)
                placeholder_sketch.name = "Setup Builder Edit Handle"
                placeholder_sketch.isVisible = False
                placeholder_sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))

                custom_features = root_comp.features.customFeatures
                custom_input = custom_features.createInput(self._custom_feature_def)
                custom_input.setStartAndEndFeatures(placeholder_sketch, placeholder_sketch)

                custom_feature = custom_features.add(custom_input)

                # Store attributes
                if stock_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'stock_token', stock_token)
                if vise_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'vise_token', vise_token)
                if plate_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'plate_token', plate_token)
                if selected_zero_point:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'zero_point_name', selected_zero_point)
                custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'joint_tokens', ','.join(joint_tokens))
                if timeline_group_token:
                    custom_feature.attributes.add(CUSTOM_FEATURE_ID, 'timeline_group_token', timeline_group_token)

                # Tag each joint with the custom feature token for cleanup tracking
                custom_feature_token = custom_feature.entityToken
                for joint in self.stock_vise_joints:
                    if joint.isValid:
                        joint.attributes.add(CUSTOM_FEATURE_ID, 'parent_feature_token', custom_feature_token)
                if self.vise_plate_joint is not None and self.vise_plate_joint.isValid:
                    self.vise_plate_joint.attributes.add(CUSTOM_FEATURE_ID, 'parent_feature_token', custom_feature_token)

            # Move timeline to end
            timeline.moveToEnd()

            # Clear state
            self._editing_feature = None
            self.stock_vise_joints = []
            self.vise_plate_joint = None
            self.stock_occ = None
            self.vise_occ = None
            self.plate_occ = None
            self.selected_zero_point = None

        except:
            log(traceback.format_exc())
            ui = Fusion().getUI()
            ui.messageBox(f'Error editing Setup Builder:\n{traceback.format_exc()}')
