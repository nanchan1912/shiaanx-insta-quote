import adsk.core
import os
import tempfile
import base64
import hashlib
import re
import traceback

# Note: analyze_step_content and extract_step_body_names are imported locally
# where needed to avoid circular imports (fusion_utils -> general_utils -> coord_utils -> fusion_utils)

# Debug flag for STEP export logging

DEBUG_STEP_EXPORT = False  # Set to True for name mismatch debugging

# Debug folder for saving copies of STEP files for analysis
DEBUG_STEP_FOLDER = ""
  

def log_model_hierarchy(design, log_func):
    """Log the complete model hierarchy tree showing components, occurrences, and bodies."""
    log_func("[MODEL HIERARCHY]", force_console=True)
    root = design.rootComponent
    log_func(f"  Root: '{root.name}'", force_console=True)

    # Log bodies in root component
    for i in range(root.bRepBodies.count):
        body = root.bRepBodies.item(i)
        log_func(f"    Body: '{body.name}' (in root)", force_console=True)

    # Recursively log occurrences
    def log_occurrence(occ, indent=2):
        prefix = "  " * indent
        comp = occ.component
        log_func(f"{prefix}Occurrence: '{occ.name}' -> Component: '{comp.name}'", force_console=True)

        # Log bodies in this occurrence
        for i in range(occ.bRepBodies.count):
            body = occ.bRepBodies.item(i)
            native = body.nativeObject
            native_name = native.name if native else body.name
            log_func(f"{prefix}  Body: '{body.name}' (native: '{native_name}')", force_console=True)

        # Recurse into child occurrences
        for i in range(occ.childOccurrences.count):
            child = occ.childOccurrences.item(i)
            log_occurrence(child, indent + 1)

    # Log all top-level occurrences
    for i in range(root.occurrences.count):
        occ = root.occurrences.item(i)
        log_occurrence(occ)

    log_func("[END MODEL HIERARCHY]", force_console=True)


def log_step_file_names(path: str, tier_name: str, original_name: str, log_func) -> None:
    """
    Log STEP file body names for debugging.

    Args:
        path: Path to the STEP file
        tier_name: Name of the export tier (e.g., "Tier 1", "Tier 2")
        original_name: The expected body name to check for
        log_func: Logging function to use
    """
    if not DEBUG_STEP_EXPORT:
        return

    try:
        from .general_utils import analyze_step_content
        with open(path, 'r') as f:
            step_content = f.read()

        analysis = analyze_step_content(step_content)

        log_func(f"[STEP DEBUG] {tier_name} - STEP file PRODUCT names: {analysis['product_names'][:5]}", force_console=True)
        log_func(f"[STEP DEBUG] {tier_name} - STEP file solid body names: {analysis['solid_body_names'][:5]}", force_console=True)
        log_func(f"[STEP DEBUG] {tier_name} - STEP file SHAPE_REPRESENTATION names: {analysis['shape_rep_names'][:5]}", force_console=True)

        if original_name in analysis['all_names']:
            log_func(f"[STEP DEBUG] {tier_name} - ✓ Body name '{original_name}' FOUND in STEP file", force_console=True)
        else:
            log_func(f"[STEP DEBUG] {tier_name} - ⚠️  Body name '{original_name}' NOT FOUND in STEP file!", force_console=True)
            log_func(f"[STEP DEBUG] {tier_name} - All unique names in STEP: {analysis['all_names'][:10]}", force_console=True)
    except Exception as e:
        log_func(f"[STEP DEBUG] {tier_name} - Error reading STEP file: {e}", force_console=True)


class Fusion:
    def __init__(self, doc = None):
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface

        # doc is not available during fusion startup
        # so we initialize it lazy
        self._doc = doc
        self._cam = None
        self._design = None


    def getCAM(self) -> adsk.cam.CAM:
        if self._cam is None:
            self.makeWorkspaceAvailable("CAM")
        return self._cam

    def makeWorkspaceAvailable(self, name):
        known_names = ["CAM", "Design"]
        if not name in known_names:
            raise Exception(f"Unknown workspace: {name}. Known workspaces: {known_names}")

        prodname = None
        envname = None
        if name == "CAM":
            prodname = "CAMProductType"
            envname = "CAMEnvironment"
        elif name == "Design":
            prodname = "DesignProductType"
            envname = "FusionSolidEnvironment"
        else:
            raise Exception("Unreachable")

        if self._doc is None:
            self._doc = self.app.activeDocument
        prod = None
        try:
            prod = self._doc.products.itemByProductType(prodname)
        except RuntimeError:
            ui = self.getUI()
            id_before = ui.activeWorkspace.id
            ws = ui.workspaces.itemById(envname) 
            ws.activate()
            prod = self._doc.products.itemByProductType(prodname)

            ui.workspaces.itemById(id_before).activate()

        if name == "Design":
            self._design = adsk.fusion.Design.cast(prod)
        elif name == "CAM":
            self._cam : adsk.cam.CAM = adsk.cam.CAM.cast(prod)
        else:
            raise Exception("Unreachable")

    def activateCAM(self):
        ui = self.getUI()
        camWS = ui.workspaces.itemById('CAMEnvironment') 
        camWS.activate()

    def activateDesign(self):
        ui = self.getUI()
        ws = ui.workspaces.itemById('FusionSolidEnvironment')
        ws.activate()

    def getDesign(self) -> adsk.fusion.Design:
        if self._design is None:
            self.makeWorkspaceAvailable("Design")

        return self._design
    
    def getUI(self) -> adsk.core.UserInterface:
        return self.ui

    def getApplication(self) -> adsk.core.Application:
        return self.app

    def getUser(self) -> adsk.core.User:
        user = self.app.currentUser
        assert isinstance(user, adsk.core.User)
        return user

    def getActiveDocument(self) -> adsk.core.Document:
        return self.getApplication().activeDocument

    def get_bodies_using_components(self) -> list:
        design = self.getDesign()
        ret = []
        for component in design.allComponents:
            for body in component.bRepBodies:
                ret.append(body)
        return ret

    def get_bodies_using_occurrences(self) -> list:
        design = self.getDesign()
        ret = []
        for occ in design.rootComponent.allOccurrences:
            for body in occ.bRepBodies:
                ret.append(body)
        return ret

    def get_bodies(self) -> list[adsk.fusion.BRepBody]:
        bodies = self.get_bodies_using_occurrences()
        if len(bodies) == 0:
            bodies = self.get_bodies_using_components()
        return bodies

    def get_visible_bodies(self) -> list[adsk.fusion.BRepBody]:
        # there is a fusion bug here
        # according to the docs b.isVisible
        # tells whether a body is visible, according to
        # the lightbulb of the body itself and any ancestor component
        # however this doesn't always seem to work
        return [b for b in self.get_bodies() if b.isVisible]

    def get_body(self) -> adsk.fusion.BRepBody:
        bodies = self.get_bodies()
        if len(bodies) > 1:
            bodies = [b for b in self.get_bodies() if b.isVisible]
        if not len(bodies) == 1:
            raise Exception(f"Expected a single body, got {len(bodies)} bodies instead.")
        return bodies[0]

    def get_first_body(self) -> adsk.fusion.BRepBody:
        return self.get_bodies()[0]
    
    def find_first_body(self, component):
        """
        Recursively searches through the component tree to find the first BRepBody.
        
        :param component: adsk.fusion.Component - The starting component.
        :return: adsk.fusion.BRepBody or None if no body is found.
        """
        if not component:
            return None
        
        # Check if the current component has any bodies
        if component.bRepBodies and component.bRepBodies.count > 0:
            return component.bRepBodies.item(0)  # Return the first body found

        # If no bodies, check the subcomponents
        for subComponent in component.occurrences:
            body = self.find_first_body(subComponent.component)
            if body:
                return body  # Return as soon as a body is found

        return None  # No body found in the entire hierarchy

    def save_step_file(self, path : str, body_comp_occ) -> bool:
        """
        Save the object to a STEP file and return whether the geometry was saved
        in world space (True) or local/component space (False).

        World space means any assembly transform has already been baked into the
        STEP geometry, so the corresponding stepCoordinateSystem_cm should be
        identity. Local space means the STEP geometry is in component-local coords
        and the assembly transform is still needed as stepCoordinateSystem_cm.
        """
        if isinstance(body_comp_occ, adsk.fusion.Component):
            self._save_step_file_component(path, body_comp_occ)
            return False
        elif isinstance(body_comp_occ, adsk.fusion.BRepBody):
            return self._save_step_file_body_tmp_component(path, body_comp_occ)
        elif isinstance(body_comp_occ, adsk.fusion.Occurrence):
            self._save_step_file_component(path, body_comp_occ.component)
            return False
        else:
            raise Exception(f"Unsupported type {type(body_comp_occ)}. Only BRepBody and Component are supported.")

    def _save_step_file_component(self, path : str, component : adsk.fusion.Component):
        # TODO allow saving single body
        assert isinstance(component, adsk.fusion.Component)
        m = self.getDesign().exportManager
        options = m.createSTEPExportOptions(path, component)
        m.execute(options)
        return path

    def _save_step_file_body_tmp_component(self, path: str, body: adsk.fusion.BRepBody) -> bool:
        """
        Returns True if the body was saved in world space (assembly transform baked in),
        or False if saved in local/component space.
        Tier 1, 3, 4a, 4b: world space (return True).
        Tier 2: local space — inverse transform applied before export (return False).
        """
        """
        Export a single BRepBody to a STEP file.

        Fusion 360's export manager can only export entire components, not individual bodies.
        This method works around that limitation by copying the body to a temporary component,
        exporting that component, then cleaning up.

        The method uses a four-tier fallback approach because different body types and design
        modes require different copy strategies. All tiers preserve assembly transforms.

        Tier 1 - copyToComponent (preferred):
            - Fastest and preserves proxy body transformations implicitly
            - Fails with RuntimeError in Direct Modeling mode or for certain body types
              (linked bodies, bodies created via TemporaryBRepManager, etc.)

        Tier 2 - TemporaryBRepManager.copy + base feature:
            - Copies the raw BRep geometry to a transient body
            - Creates a base feature in the temp component to hold the geometry
            - Works in Direct Modeling mode and for most body types
            - Preserves transformation when copying from proxy body

        Tier 3 - Parent body copy + transform:
            - Copies the body from parent component to temp component
            - Only attempted when parent has exactly 1 body
            - Applies assembly transform using MoveFeature if non-identity

        Tier 4 - Native body + transform (for proxy bodies only):
            - If Tiers 1-3 fail and we were using a proxy body, retry with native body
            - The proxy body's transformation may be causing InternalValidationError
            - Tier 4a: copyToComponent with native body + apply transform via MoveFeature
            - Tier 4b: TemporaryBRepManager.copy + apply transform via transform()

        Args:
            path: Output file path for the STEP file
            body: The BRepBody to export (can be proxy or native)
        """
        from .general_utils import log
        assert isinstance(body, adsk.fusion.BRepBody)

        # Determine if this is a proxy body (accessed through an occurrence).
        # Proxy bodies have assembly transformations baked in.
        # We need the native body only to get metadata (name, parent component),
        # but we use the proxy body for all copy operations to preserve transformation.
        native_body = body.nativeObject if body.nativeObject else body
        original_name = native_body.name
        parent_component = native_body.parentComponent

        # Debug: check if transform is identity (helps diagnose if Tier 3/4 fallback is safe)
        is_proxy = body.nativeObject is not None
        parent_name = parent_component.name if parent_component else "None"
        assembly_context_name = body.assemblyContext.name if body.assemblyContext else "None"

        # Save assembly transform for use in Tiers 3 and 4 (applied via MoveFeature or transform())
        assembly_transform = None
        is_identity = True
        if body.assemblyContext is not None:
            assembly_transform = body.assemblyContext.transform2
            identity = adsk.core.Matrix3D.create()
            is_identity = assembly_transform.isEqualTo(identity)

        design = self.getDesign()

        # Force Fusion to refresh its internal hierarchy state before export.
        # This traversal is REQUIRED - without it, body names may not be written
        # correctly to the STEP file. Accessing body.nativeObject and body.name
        # for all bodies forces Fusion to update internal state.
        if DEBUG_STEP_EXPORT:
            log_model_hierarchy(design, log)
            log(f"[STEP DEBUG] Exporting: body='{original_name}' parent_component='{parent_name}' assembly_context='{assembly_context_name}'", force_console=True)
            if body.assemblyContext is not None:
                log(f"[STEP DEBUG]   is_proxy={is_proxy} has_assemblyContext=True is_identity_transform={is_identity}", force_console=True)
            else:
                log(f"[STEP DEBUG]   is_proxy={is_proxy} has_assemblyContext=False (no transform to lose)", force_console=True)
        else:
            # Same traversal as log_model_hierarchy but without logging
            log_model_hierarchy(design, lambda *args, **kwargs: None)

        # Create a temporary component to hold the copied body for export.
        # IMPORTANT: Name the component with the body's original name so it appears
        # correctly in the exported STEP file. Parasolid reads entity names from
        # STEP component names, not body names.
        # This component will be deleted in the finally block.
        newOcc = add_component(design.rootComponent, name=original_name)
        if DEBUG_STEP_EXPORT:
            log(f"[STEP DEBUG] Created temp component with name='{original_name}'", force_console=True)

        tier1_result = "not_tried"
        tier2_result = "not_tried"
        tier3_result = "not_tried"
        tier4_result = "not_tried"

        try:
            # Tier 1: copyToComponent - fastest, preserves proxy transformation
            try:
                # CRITICAL: Force Fusion to refresh body state before copy.
                # Accessing these properties forces Fusion to update internal state,
                # which affects how body names are written to the STEP file.
                # Without this, the STEP file may contain wrong body names.
                _ = body.name
                _ = body.nativeObject.name if body.nativeObject else None

                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 1: Trying body.copyToComponent...", force_console=True)
                body.copyToComponent(newOcc)
                copied_bodies = newOcc.component.bRepBodies
                if copied_bodies.count == 1:
                    copied_bodies.item(0).name = original_name
                    # Force Fusion to recognize the name change before export
                    _ = copied_bodies.item(0).name
                self._save_step_file_component(path, newOcc.component)
                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 1 SUCCESS for '{original_name}'", force_console=True)
                    log_step_file_names(path, "Tier 1", original_name, log)
                return True  # world space: proxy transform baked in by copyToComponent
            except RuntimeError as e:
                # copyToComponent fails in Direct Modeling mode or for certain body types
                tier1_result = f"FAIL({e})"
                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 1 FAILED: {e}", force_console=True)

            # Tier 2: TemporaryBRepManager - works for Direct Modeling and edge cases
            try:
                # Clear any bodies from Tier 1 failed attempt
                leftover_count = newOcc.component.bRepBodies.count
                if leftover_count > 0:
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 2: Cleaning up {leftover_count} leftover body(ies) from Tier 1 failure", force_console=True)
                    for i in range(leftover_count - 1, -1, -1):
                        newOcc.component.bRepBodies.item(i).deleteMe()

                # CRITICAL: Force Fusion to refresh body state before copy.
                # Accessing these properties forces Fusion to update internal state,
                # which affects how body names are written to the STEP file.
                # Without this, the STEP file may contain wrong body names.
                _ = body.name
                _ = body.nativeObject.name if body.nativeObject else None

                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 2: Trying TemporaryBRepManager.copy(body)...", force_console=True)
                temp_brep_mgr = adsk.fusion.TemporaryBRepManager.get()
                transient_body = temp_brep_mgr.copy(body)

                # CRITICAL FIX: When copying a proxy body, TemporaryBRepManager.copy() returns
                # geometry in world coordinates (with the assembly transform already applied).
                # However, stepCoordinateSystem_cm describes T_world_from_pcs (local to world),
                # and Julia expects the STEP geometry to be in local (part) coordinates.
                # We must apply the INVERSE transform to convert from world back to local coords.
                if assembly_transform is not None and not is_identity:
                    inverse_transform = assembly_transform.copy()
                    inverse_transform.invert()
                    temp_brep_mgr.transform(transient_body, inverse_transform)
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 2: Applied INVERSE assembly transform to convert from world to local coords", force_console=True)

                target_comp = newOcc.component
                base_feature = target_comp.features.baseFeatures.add()

                # Add transient body to base feature. finishEdit must be called
                # even if add() fails to avoid leaving the feature in edit mode.
                base_feature.startEdit()
                try:
                    new_body = target_comp.bRepBodies.add(transient_body, base_feature)
                finally:
                    base_feature.finishEdit()

                if new_body:
                    new_body.name = original_name
                    # Force Fusion to recognize the name change before export
                    _ = new_body.name
                    self._save_step_file_component(path, newOcc.component)
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 2 SUCCESS for '{original_name}'", force_console=True)
                        log_step_file_names(path, "Tier 2", original_name, log)
                    return False  # local space: inverse transform was applied before export
                else:
                    tier2_result = "FAIL(new_body=None)"
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 2 FAILED: new_body=None", force_console=True)
            except Exception as e:
                # Catch broad Exception here - Fusion API can throw various types
                tier2_result = f"FAIL({type(e).__name__}:{e})"
                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 2 FAILED: {type(e).__name__}:{e}", force_console=True)

            # Tier 3: Copy body from parent component to temp component, apply transform, export
            # Only possible when parent has exactly 1 body
            if DEBUG_STEP_EXPORT:
                log(f"[STEP DEBUG] Tier 3: Checking parent_component.bRepBodies.count = {parent_component.bRepBodies.count}", force_console=True)
            if parent_component.bRepBodies.count == 1:
                try:
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 3: Trying parent_body.copyToComponent...", force_console=True)
                    # Clear any bodies from previous failed attempts
                    for i in range(newOcc.component.bRepBodies.count - 1, -1, -1):
                        newOcc.component.bRepBodies.item(i).deleteMe()

                    # Get body from parent component and copy to temp component
                    parent_body = parent_component.bRepBodies.item(0)
                    parent_body.copyToComponent(newOcc)
                    copied_bodies = newOcc.component.bRepBodies

                    if copied_bodies.count == 1:
                        copied_body = copied_bodies.item(0)
                        copied_body.name = original_name
                        # Force Fusion to recognize the name change before export
                        _ = copied_body.name

                        # Apply assembly transform if non-identity
                        if assembly_transform is not None and not is_identity:
                            target_comp = newOcc.component
                            move_features = target_comp.features.moveFeatures
                            bodies_collection = adsk.core.ObjectCollection.create()
                            bodies_collection.add(copied_body)
                            move_input = move_features.createInput2(bodies_collection)
                            move_input.defineAsFreeMove(assembly_transform)
                            move_features.add(move_input)
                            if DEBUG_STEP_EXPORT:
                                log(f"[STEP DEBUG] Tier 3: Applied assembly transform to '{original_name}'", force_console=True)

                        self._save_step_file_component(path, newOcc.component)
                        if DEBUG_STEP_EXPORT:
                            log(f"[STEP DEBUG] Tier 3 SUCCESS for '{original_name}' (parent body copy with transform)", force_console=True)
                            log_step_file_names(path, "Tier 3", original_name, log)
                        return True  # world space: assembly transform applied via MoveFeature
                    else:
                        tier3_result = f"FAIL(copied_bodies={copied_bodies.count})"
                        if DEBUG_STEP_EXPORT:
                            log(f"[STEP DEBUG] Tier 3 FAILED: copied_bodies={copied_bodies.count}", force_console=True)
                except Exception as e:
                    tier3_result = f"FAIL({type(e).__name__}:{e})"
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 3 FAILED: {type(e).__name__}:{e}", force_console=True)
            else:
                tier3_result = f"SKIP(bodies={parent_component.bRepBodies.count})"
                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 3 SKIPPED: parent has {parent_component.bRepBodies.count} bodies (need exactly 1)", force_console=True)

            # Tier 4: Retry with native body if we were using a proxy body
            # The proxy body's transformation may be causing InternalValidationError
            if body.nativeObject:
                native = body.nativeObject
                if DEBUG_STEP_EXPORT:
                    log(f"[STEP DEBUG] Tier 4: Using native body (is proxy)", force_console=True)

                # Tier 4a: Try copyToComponent with native body
                try:
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 4a: Trying native.copyToComponent...", force_console=True)
                    # Clear any bodies from previous failed attempts
                    for i in range(newOcc.component.bRepBodies.count - 1, -1, -1):
                        newOcc.component.bRepBodies.item(i).deleteMe()

                    native.copyToComponent(newOcc)
                    copied_bodies = newOcc.component.bRepBodies
                    if copied_bodies.count == 1:
                        copied_body = copied_bodies.item(0)
                        copied_body.name = original_name
                        # Force Fusion to recognize the name change before export
                        _ = copied_body.name

                        # Apply assembly transform if it was non-identity
                        if assembly_transform is not None and not is_identity:
                            target_comp = newOcc.component
                            move_features = target_comp.features.moveFeatures
                            bodies_collection = adsk.core.ObjectCollection.create()
                            bodies_collection.add(copied_body)
                            move_input = move_features.createInput2(bodies_collection)
                            move_input.defineAsFreeMove(assembly_transform)
                            move_features.add(move_input)
                            if DEBUG_STEP_EXPORT:
                                log(f"[STEP DEBUG] Tier 4a: Applied assembly transform to '{original_name}'", force_console=True)

                    self._save_step_file_component(path, newOcc.component)
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 4a SUCCESS for '{original_name}' (native copyToComponent)", force_console=True)
                        log_step_file_names(path, "Tier 4a", original_name, log)
                    return True  # world space: assembly transform applied via MoveFeature
                except RuntimeError as e:
                    tier4_result = f"FAIL_4a({e})"
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 4a FAILED: {e}", force_console=True)

                # Tier 4b: Try TemporaryBRepManager with native body
                try:
                    if DEBUG_STEP_EXPORT:
                        log(f"[STEP DEBUG] Tier 4b: Trying TemporaryBRepManager.copy(native)...", force_console=True)
                    # Clear any bodies from previous failed attempts
                    for i in range(newOcc.component.bRepBodies.count - 1, -1, -1):
                        newOcc.component.bRepBodies.item(i).deleteMe()

                    temp_brep_mgr = adsk.fusion.TemporaryBRepManager.get()
                    transient_body = temp_brep_mgr.copy(native)

                    # Apply assembly transform if it was non-identity
                    if assembly_transform is not None and not is_identity:
                        temp_brep_mgr.transform(transient_body, assembly_transform)
                        if DEBUG_STEP_EXPORT:
                            log(f"[STEP DEBUG] Tier 4b: Applied assembly transform to transient body '{original_name}'", force_console=True)

                    target_comp = newOcc.component
                    base_feature = target_comp.features.baseFeatures.add()
                    base_feature.startEdit()
                    try:
                        new_body = target_comp.bRepBodies.add(transient_body, base_feature)
                    finally:
                        base_feature.finishEdit()

                    if new_body:
                        new_body.name = original_name
                        # Force Fusion to recognize the name change before export
                        _ = new_body.name
                        self._save_step_file_component(path, newOcc.component)
                        if DEBUG_STEP_EXPORT:
                            log(f"[STEP DEBUG] Tier 4b SUCCESS for '{original_name}' (native TemporaryBRepManager)", force_console=True)
                            log_step_file_names(path, "Tier 4b", original_name, log)
                        return True  # world space: assembly transform applied to transient body
                    else:
                        tier4_result = f"FAIL_4b(new_body=None)"
                except Exception as e:
                    tier4_result = f"FAIL_4b({type(e).__name__}:{e})"
            else:
                tier4_result = "SKIP(not_proxy)"

            error_msg = f"All export methods failed for '{original_name}': T1={tier1_result}, T2={tier2_result}, T3={tier3_result}, T4={tier4_result}"
            log(f"[STEP] FAILED: {error_msg}", force_console=True)
            log(f"[STEP] Traceback:\n{traceback.format_stack()}", force_console=True)
            raise RuntimeError(error_msg)

        finally:
            # Always clean up the temporary component
            newOcc.deleteMe()

    def save_f3d_file(self, path : str):
        m = self.getDesign().exportManager
        options = m.createFusionArchiveExportOptions(path)
        m.execute(options)
        return path
    
    def isParametricDesign(self) -> bool:
        design_type = self.getDesign().designType
        return design_type == adsk.fusion.DesignTypes.ParametricDesignType
    
def new_document_from_step_file_content(step_data: str, file_name="file.step", fusion: Fusion = Fusion()) -> adsk.core.Document:
    app = fusion.getApplication()
    importManager = app.importManager
    with tempfile.TemporaryDirectory() as tmpdir:
        step_path = os.path.join(tmpdir, file_name)
        with open(step_path, "w") as f:
            f.write(step_data)
        options = importManager.createSTEPImportOptions(step_path)
        # TODO coordinates are wrong here, we need to apply the stepCoordinateSystem from req_json
        return importManager.importToNewDocument(options)


def light_bulb_off(obj):
    # naive obj.isLightBulbOn = False
    # can result in fusion throwing an exception
    if obj.isLightBulbOn:
        obj.isLightBulbOn = False


def import_step_file_to_component(step_data: str, targetComponent, file_name="file.step", fusion: Fusion = Fusion()):
    app = fusion.getApplication()
    rootComp = targetComponent
    importManager = app.importManager
    with tempfile.TemporaryDirectory() as tmpdir:
        step_path = os.path.join(tmpdir, file_name)
        with open(step_path, "w") as f:
            f.write(step_data)
        options = importManager.createSTEPImportOptions(step_path)
        imported_occurrence = importManager.importToTarget(options, rootComp)

        if not imported_occurrence:
            fusion.getUI().messageBox("Failed to import component.")
            return None

        return

def add_component(parent_component, name, *, isGroundToParent=True, transform=adsk.core.Matrix3D.create(),
                  isLightBulbOn=True):
    occurrence = parent_component.occurrences.addNewComponent(transform)
    # Name the new component
    if name is not None:
        occurrence.component.name = name
    if isGroundToParent is not None:
        occurrence.isGroundToParent = isGroundToParent
    occurrence.isLightBulbOn = isLightBulbOn
    return occurrence

def insert_component_by_name(target_component, root_folder, file_name, by_ref=True):
    file_found = False
    for data_file in root_folder.dataFiles:
        if data_file.name == file_name:
            file_found = True
            break
    if not file_found:
        return None

    occurrences = target_component.occurrences
    occurrence = occurrences.addByInsert(data_file,adsk.core.Matrix3D.create(),by_ref)

    return occurrence

def get_current_design_doc():
    app = adsk.core.Application.get()
    doc = app.activeDocument
    design = doc.products.itemByProductType('DesignProductType')
    return doc, design


def create_new_design_doc(doc_name="sample_document"):
    app = adsk.core.Application.get()
    ui = app.userInterface
    new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)

    if not new_doc:
        ui.messageBox("Failed to create a new design document.")
        return None

    new_doc.name = doc_name
    new_design = new_doc.products.itemByProductType('DesignProductType')

    if not isinstance(new_design, adsk.fusion.Design):
        ui.messageBox("New document is not a Fusion design.")
        return None

    return new_doc, new_design


def get_open_documents():
    """Get a list of all open top-level documents (excluding referenced sub-documents).
    
    In Fusion 360, when an assembly has externally referenced components, those
    referenced documents also appear in app.documents. This function filters to
    return only the documents that the user explicitly opened (top-level documents),
    not the automatically-loaded reference documents.
    """
    app = adsk.core.Application.get()
    
    # Build a set of creationIds that are referenced by other documents
    referenced_ids = set()
    for i in range(app.documents.count):
        doc = app.documents.item(i)
        try:
            design = doc.products.itemByProductType('DesignProductType')
            if design:
                root = design.rootComponent
                # Check all occurrences for external references
                for j in range(root.allOccurrences.count):
                    occ = root.allOccurrences.item(j)
                    if occ.isReferencedComponent:
                        # This occurrence references an external document
                        ref_doc = occ.component.parentDesign.parentDocument
                        if ref_doc:
                            referenced_ids.add(ref_doc.creationId)
        except Exception:
            pass
    
    # Return only documents that are not referenced by other documents
    documents = []
    for i in range(app.documents.count):
        doc = app.documents.item(i)
        if doc.creationId not in referenced_ids:
            documents.append({
                "id": doc.creationId,
                "name": doc.name,
                "isSaved": doc.isSaved,
            })
    return documents


def get_document_thumbnail(document_id: str, width: int = 256, height: int = 256) -> str:
    """Get a base64-encoded thumbnail image for a document.
    
    Args:
        document_id: The creationId of the document
        width: Thumbnail width in pixels (default 256)
        height: Thumbnail height in pixels (default 256)
        
    Returns:
        Base64-encoded PNG image string, or None if document not found
    """
    doc, design = find_document_by_creation_id(document_id)
    if not doc or not design:
        return None
    
    root_comp = design.rootComponent
    data_object = root_comp.createThumbnail(width, height, "PNG")
    return data_object.getAsBase64String()


def find_document_by_creation_id(creation_id: str):
    """Find an open document by its creationId and return (doc, design) or (None, None)."""
    app = adsk.core.Application.get()
    for i in range(app.documents.count):
        doc = app.documents.item(i)
        if doc.creationId == creation_id:
            design = doc.products.itemByProductType('DesignProductType')
            return doc, design
    return None, None


def get_step_file_content(fusion : Fusion, obj, debug_name: str = None) -> tuple:
    """
    Export an object to STEP format and return the file content as a string.

    Args:
        fusion: Fusion instance
        obj: The object to export (BRepBody, Component, or Occurrence)
        debug_name: Optional label for debug logging (e.g., "debug-part", "debug-stock")
    """
    from .general_utils import log

    path = tempfile.mktemp() + ".step"

    if DEBUG_STEP_EXPORT and debug_name:
        log(f"[STEP DEBUG] === Exporting {debug_name} ===", force_console=True)
        log(f"[STEP DEBUG] Object type: {type(obj).__name__}", force_console=True)
        if hasattr(obj, 'name'):
            log(f"[STEP DEBUG] Object name: '{obj.name}'", force_console=True)
        if hasattr(obj, 'nativeObject') and obj.nativeObject:
            log(f"[STEP DEBUG] Native object name: '{obj.nativeObject.name}'", force_console=True)
        if hasattr(obj, 'boundingBox') and obj.boundingBox:
            bb = obj.boundingBox
            log(f"[STEP DEBUG] Bounding box min: ({bb.minPoint.x:.4f}, {bb.minPoint.y:.4f}, {bb.minPoint.z:.4f}) cm", force_console=True)
            log(f"[STEP DEBUG] Bounding box max: ({bb.maxPoint.x:.4f}, {bb.maxPoint.y:.4f}, {bb.maxPoint.z:.4f}) cm", force_console=True)
            log(f"[STEP DEBUG] Bounding box size: ({bb.maxPoint.x - bb.minPoint.x:.4f}, {bb.maxPoint.y - bb.minPoint.y:.4f}, {bb.maxPoint.z - bb.minPoint.z:.4f}) cm", force_console=True)
        if hasattr(obj, 'assemblyContext') and obj.assemblyContext:
            log(f"[STEP DEBUG] Assembly context: '{obj.assemblyContext.name}'", force_console=True)
            transform = obj.assemblyContext.transform2
            origin, xaxis, yaxis, zaxis = transform.getAsCoordinateSystem()
            log(f"[STEP DEBUG] Assembly transform origin: ({origin.x:.4f}, {origin.y:.4f}, {origin.z:.4f}) cm", force_console=True)

    saved_in_world_space = fusion.save_step_file(path, obj)

    with open(path, "r") as file:
        content = file.read()

    if DEBUG_STEP_EXPORT and debug_name:
        log(f"[STEP DEBUG] STEP file size: {len(content)} bytes", force_console=True)

        # Save a copy of the STEP file to the debug folder for analysis
        if DEBUG_STEP_FOLDER:
            import shutil
            import datetime
            os.makedirs(DEBUG_STEP_FOLDER, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            obj_name = getattr(obj, 'name', 'unknown').replace('/', '_').replace('\\', '_')
            debug_filename = f"{debug_name}_{obj_name}_{timestamp}.step"
            debug_path = os.path.join(DEBUG_STEP_FOLDER, debug_filename)
            shutil.copy(path, debug_path)
            log(f"[STEP DEBUG] Saved debug copy to: {debug_path}", force_console=True)

            # Also log body count and names from the STEP file
            from .general_utils import analyze_step_content
            analysis = analyze_step_content(content)
            log(f"[STEP DEBUG] STEP file body analysis:", force_console=True)
            log(f"[STEP DEBUG]   PRODUCT count: {len(analysis['product_names'])}, names: {analysis['product_names']}", force_console=True)
            log(f"[STEP DEBUG]   Solid body count: {len(analysis['solid_body_names'])}, names: {analysis['solid_body_names']}", force_console=True)
            log(f"[STEP DEBUG]   ADVANCED_BREP_SHAPE_REPRESENTATION count: {len(analysis['shape_rep_names'])}, names: {analysis['shape_rep_names']}", force_console=True)

        log(f"[STEP DEBUG] === End {debug_name} ===", force_console=True)

    return content, saved_in_world_space

def import_part_from_step(step_data, design, fusion):
    import_step_file_to_component(step_data, design.rootComponent, fusion=fusion)
    occurrence = design.rootComponent.occurrences.item(design.rootComponent.occurrences.count - 1)

    return occurrence


def get_f3d_content_base64(fusion : Fusion) -> str:        
    path = tempfile.mkstemp()[1] + ".f3d"
    fusion.save_f3d_file(path)
    with open(path, "rb") as file:
        content = file.read()
    content64 = base64.b64encode(content).decode("utf-8")
    return content64


def get_active_setup(fusion : Fusion) -> adsk.cam.Setup:
    app = fusion.getApplication()
    # Check to see if the CAM workspace is active.
    if app.activeProduct.objectType != adsk.cam.CAM.classType():
        raise Exception("The Manufacturing workspace must be active.")
    cam: adsk.cam.CAM = app.activeProduct
    active_setup = None
    for setup in cam.setups:
        if setup.isActive:
            active_setup = setup
            break
    if active_setup is None:
        raise Exception("A setup must be selected")
    return active_setup

def get_comp_transformation(fusion : Fusion, component : adsk.fusion.Component) ->adsk.core.Matrix3D:
    compTransf = adsk.core.Matrix3D.create()
    component_occ = fusion.getDesign().rootComponent.occurrencesByComponent(component)
    if (component_occ.count == 1):
        compTransf = component_occ[0].transform2
    elif component_occ.count > 1:
        raise Exception("Currently the component must have only one occurrence")
    
    return compTransf

def make_id(name, salt=None) -> str:
    """
    Create a string that is suitable as an id for fusion, such that the following properties hold:
    * same name and salt will give the exact same id. Even if fusion is restarted
    * different name or salt will give different ids
    * Only a z-a-zA-Z0-9_ characters are used, fusion complains about more exotic characters in some contexts
    * name will be recognizable from the id which might be useful for debugging
    """
    name = str(name)
    salt = str(salt)
    bytes = f"{name}{salt}".encode("utf-8")
    h = hashlib.blake2b(bytes).hexdigest()
    id = re.sub(r'[^A-Za-z0-9_]', '', f"{name}_{h}")
    assert isinstance(id, str)
    return id


def ensure_hybrid_design_intent(design):
    """Check if design is in Hybrid mode, prompt user to switch if not.

    Returns True if we can proceed (already hybrid or user confirmed switch).
    Returns False if user cancelled the switch.
    """
    import adsk.fusion

    current_intent = design.designIntent
    if current_intent == adsk.fusion.DesignIntentTypes.HybridDesignIntentType:
        return True

    # Map intent values to display names
    intent_names = {
        adsk.fusion.DesignIntentTypes.PartDesignIntentType: "Part",
        adsk.fusion.DesignIntentTypes.AssemblyDesignIntentType: "Assembly",
        adsk.fusion.DesignIntentTypes.HybridDesignIntentType: "Hybrid",
    }
    current_name = intent_names.get(current_intent, f"Unknown ({current_intent})")

    ui = Fusion().getUI()
    result = ui.messageBox(
        f"Convert this document type from {current_name} to Hybrid?",
        "Design Mode Change Required",
        adsk.core.MessageBoxButtonTypes.OKCancelButtonType,
        adsk.core.MessageBoxIconTypes.WarningIconType
    )

    if result == adsk.core.DialogResults.DialogOK:
        design.designIntent = adsk.fusion.DesignIntentTypes.HybridDesignIntentType
        return True
    else:
        return False