# native package imports
import json
from copy import deepcopy
import os
import typing
import traceback

# ADSK imports
import adsk.core

# Add in imports
from .client import Client
from .fusion_utils import Fusion, import_part_from_step, create_new_design_doc, get_current_design_doc
from .coord_utils import clear_coord_system_cache
from .general_utils import load_config, log, get_addin_version, isdebug
from .component_utils import UserPart, Stock,Workholding, Joints, MachiningFeature
from .component_utils import FusionFullPath
from .component_utils import compute_distance_vector, find_first_body
from ..commands.command_RequestFusionOps.logic import SetopMaterializer
from .setup_utils import get_setup


class ImportProgram():
    def __init__(self, testing=False):
        self.fusion_paths = FusionFullPath()
        self.testing = testing

    def create_toollib(self,toollib_json):
        str = json.dumps(toollib_json)
        toollib = adsk.cam.ToolLibrary.createFromJson(str)
        return toollib

    def create_stock_to_part_joint(self, joints, parts, stock, progressDialog):
        """Create the joint connecting Stock to Part. Call after Stock is fully created."""
        if parts is None:
            return None
        part = parts[0]

        if not (stock.has_joints and (part.jointVertex is not None)):
            return None

        joint_offset = compute_distance_vector(part.jointVertex, stock.top_center)
        joint_stock = joints.add_base_joint(
            "Stock", "Stock Corner", "Part", "Part Vertex", "Part Top",
            offset=joint_offset, create_assembly_context=False
        )
        joint_stock.isLightBulbOn = False
        progressDialog.progressValue += 10
        adsk.doEvents()
        return joint_stock

    def create_workholding_joints(self, joints, stock, ui, progressDialog):
        """Create all joints for workholding. Call after Workholding is fully created."""
        try:
            joint0 = joints.add_workholding_joint(
                fixture_plate_target="Zero Point 1",
                vise_target="Zero Point Attachment",
                name="Vise fixture joint"
            )
        except Exception as e:
            log(traceback.format_exc())
            if ui:
                ui.messageBox("Unable to create Zero Point 1 joint. Check that your vise file has a Zero Point Attachment Joint Origin defined and that your clamping plate has Zero Point 1 defined")
            progressDialog.hide()
            return None

        try:
            joint1 = joints.add_base_joint(
                "vise", "Stock Attachment", "Stock", stock.stock_bottom,
                "Stock bottom to vise", isFlipped=True, isPlanar=True
            )
        except Exception as e:
            log(traceback.format_exc())
            if ui:
                ui.messageBox("Unable to create stock bottom joint. Check that your vise file has a Stock Bottom joint origin defined")
            progressDialog.hide()
            return None

        try:
            joint2 = joints.add_base_joint(
                "vise", "Jaw Position 1", "Stock", stock.stock_back,
                "Stock Side to jaw 1", isPlanar=True, isFlipped=True
            )
        except Exception as e:
            log(traceback.format_exc())
            if ui:
                ui.messageBox("Unable to create Jaw Position 1 joint. Check that your vise file has a Jaw Position 1 joint origin defined")
            progressDialog.hide()
            return None

        try:
            joint3 = joints.add_base_joint(
                "Stock", stock.stock_front, "vise", "Jaw Position 2",
                "Stock Side to jaw 2", isPlanar=True, isFlipped=True
            )
        except Exception as e:
            log(traceback.format_exc())
            if ui:
                ui.messageBox("Unable to create Jaw Position 2 joint. Check that your vise file has a Jaw Position 2 joint origin defined")
            progressDialog.hide()
            return None

        try:
            joint4 = joints.add_base_joint(
                "Stock", stock.stock_left, "vise", "Vise Center",
                "Stock Side to Vise Center", isPlanar=True, offset=True
            )
        except Exception as e:
            log(traceback.format_exc())
            if ui:
                ui.messageBox("Unable to create vise center joint. Check that your vise file has a Vise Center joint origin defined")
            progressDialog.hide()
            return None

        joint0.isLightBulbOn = False
        joint1.isLightBulbOn = False
        joint2.isLightBulbOn = False
        joint3.isLightBulbOn = False
        joint4.isLightBulbOn = False

        progressDialog.progressValue += 10
        adsk.doEvents()
        return [joint0, joint1, joint2, joint3, joint4]

    def confirm_geometry_matches_body(self,design,resp):
        if "geometry_tracking_data" in resp:
            for geometry in resp["geometry_tracking_data"]:
                faces = design.findEntityByToken(geometry["entityToken"])
                found = False
                for face in faces:
                    if abs(face.area - geometry["area"] <= 0.002):
                        found = True
                        break
                if not found: return False
        return True

    def file_has_body(self,design):
        root_comp = design.rootComponent
        bodies = find_first_body(root_comp)
        return bodies is not None

    def confirm_resp_matches_doc(self,fusion,doc,resp):
        document_creationId = resp.get("document_creationId", None)
        if document_creationId != doc.creationId:
            return False

        setips_subtypekey = resp.get("setips_subtypekey", None)

        if setips_subtypekey == "AutoSetips":
            body = self.fusion_paths.maybe_find_resp_model(fusion.getDesign(), resp)
            if body is None:
                return False
            else:
                return self.confirm_geometry_matches_body(fusion.getDesign(), resp)
        else:
            setops = resp['setops']
            all_setups_present = True
            for setop in setops:
                operationId = setop.get("operationId", None)
                setup = get_setup(fusion, operationId)
                if setup is None:
                    all_setups_present = False
            if all_setups_present:
                return self.confirm_geometry_matches_body(fusion.getDesign(), resp)

        return False

    # return true if we want the import
    def confirm_import_if_issues(self,fusion, doc, resp) -> bool:
        document_creationId = resp.get("document_creationId", None)
        issues = []

        if document_creationId != doc.creationId:
            text = f"""
            Mismatch between current document and the document used
            for creating the program to be imported.
            current document: {doc.creationId}
            program document: {document_creationId}
            """
            issues.append(text)

        setips_subtypekey = resp.get("setips_subtypekey", None)

        if setips_subtypekey == "AutoSetips":
            body = self.fusion_paths.maybe_find_resp_model(fusion.getDesign(), resp)
            #body = maybe_find_model(fusion.getDesign(), resp)
            if body is None:
                text = f"""
                Failed to find the body to be machined in the current document.
                """
                issues.append(text)

        if len(issues) == 0:
            return True

        text = "\n\n".join(issues)
        msg = f"""
        We encountered issues while importing the program into the current document:

        {text}

        Import into current document should only be used for programs sent to Toolpath from within the current document.
        """
        ui = fusion.getUI()
        # buttons = adsk.core.MessageBoxButtonTypes.OKCancelButtonType
        # title = "Warning"
        # result = ui.messageBox(msg, title, buttons)
        # return result == adsk.core.DialogResults.DialogOK
        title = "Warning"
        result = ui.messageBox(msg, title)
        return False

    def materialize_response(self,*,
                            fusion,
                            doc,
                            design=None,
                            resp,
                            needs_cancel=None,
                            progressDialog,
                            use_workholding,
                            use_stock,
                            viseStyle,
                            use_existing_document,
                            toollibs=None,
                            config,
                            selected_body = None
                         ):
        ui = Fusion().getUI()
        setops = resp["setops"]
        if use_workholding and not use_stock:
            raise Exception("use_stock must be true if use_workholding is true")
        if needs_cancel is None:
            needs_cancel = lambda : False
        if design is None:
            design = fusion.getDesign()

        try:
            # Clear the coordinate system cache when creating a new document
            # to avoid stale references from previous documents
            if not use_existing_document:
                clear_coord_system_cache()

            if use_existing_document:
                if selected_body is not None:
                    body = selected_body
                else:
                    body = self.fusion_paths.maybe_find_resp_model(design, resp)

                if body is None:
                    parts = None
                else:
                    # We want the name to be None here so we don't overwrite the existing name in the doc.
                    part = UserPart(design, fusion, name=None, part=body, testing=self.testing, enableJoints=False)
                    if not part.validPartCreated:
                        progressDialog.hide()
                        return
                    parts = [part]
            else:
                step_file_content = resp["step_file_content"]
                occurrence = import_part_from_step(step_file_content, design, fusion)
                selected_bodies = [occurrence]
                parts = []
                for (i, body) in enumerate(selected_bodies):
                    if use_existing_document:  # TODO this branch is unreachable
                        part_name = None
                    else:
                        if "part_name" in resp:
                            part_name = resp["part_name"] + f"_{i}"
                        else:
                            part_name = "Part"

                    part = UserPart(design, fusion, name=part_name, part=body, testing=self.testing, deferJointOrigins=True)
                    parts.append(part)

                if not parts[0].validPartCreated:
                    return

            # Extract the part's assembly transform once, for positioning support
            # geometry and stock relative to the part. In a new document this is
            # identity; in an existing document it is the part's occurrence transform.
            part_transform = adsk.core.Matrix3D.create()
            if parts and len(parts) > 0:
                try:
                    part_occ = parts[0].get_occurrence()
                    _, part_transform = self.fusion_paths.extract_body_and_transform(part_occ)
                except Exception:
                    pass
            elif use_existing_document:
                # When parts=None (body not found by token), get the transform
                # from the existing CAM setup via the response operationId.
                try:
                    setop0 = setops[0] if setops else None
                    op_id = setop0.get("operationId") if setop0 else None
                    if op_id:
                        setup = get_setup(fusion, op_id)
                        if setup:
                            (setup_body, part_occ) = self.fusion_paths.get_setup_body_occurrence(setup)
                            if part_occ:
                                part_transform = part_occ.transform2
                            elif setup_body:
                                part_transform = self.fusion_paths.extract_transform_from_body(setup_body)
                except Exception:
                    pass

            # Calculate offset for fixture positioning
            # Workholding position is relative to part center, but part origin is at bottom
            # So we offset fixtures by the center of the part's bounding box
            # Note: Fusion bounding box is in cm, fixture positions are in mm, so convert cm -> mm (* 10)
            part_offset = [0.0, 0.0, 0.0]
            if parts and len(parts) > 0:
                try:
                    part_occurrence = parts[0].get_occurrence()
                    bbox = part_occurrence.boundingBox
                    part_offset[0] = (bbox.minPoint.x + bbox.maxPoint.x) / 2.0 * 10.0  # cm to mm
                    part_offset[1] = (bbox.minPoint.y + bbox.maxPoint.y) / 2.0 * 10.0
                    part_offset[2] = (bbox.minPoint.z + bbox.maxPoint.z) / 2.0 * 10.0
                except Exception:
                    pass

            # Fixtures are now imported per-setup in SetopMaterializer.execute()
            # fixture_parent_occ is kept for backwards compatibility with legacy workholding flow
            fixture_parent_occ = None

            progressDialog.progressValue += 10
            adsk.doEvents()

            stock = None
            joints = Joints(design, fusion, parts[0] if parts else None, None, None)
            if use_stock and use_workholding:
                try:
                    stock = Stock(design, fusion, setops, "TP_Stock", part_transform=part_transform, deferJointOrigins=True)
                    progressDialog.progressValue += 10
                    adsk.doEvents()
                except Exception as e:
                    progressDialog.progressValue = 100
                    progressDialog.hide()
                    adsk.doEvents()
                    log(traceback.format_exc())
                    if ui:
                        ui.messageBox("Unable to create parametric stock")
                    return

                # Check if we can create joints (use _canCreateJoints since jointVertex is deferred)
                if not stock.has_joints and parts[0]._canCreateJoints:
                    if ui:
                        ui.messageBox("Unable to import workholding with your current stock selection. Disabling work holding import and continuing.")
                    use_workholding = False

                # Update joints object with stock reference
                if stock.has_joints:
                    joints.stock = stock

            workholding = None
            if use_workholding and stock is not None:
                stock.get_body().isLightBulbOn = True
                machining_feature = MachiningFeature(design, fusion, "Machining Feature")
                try:
                    workholding = Workholding(design, fusion, name="Op1 Fixture 1", viseStyle=viseStyle)
                except Exception:
                    workholding = None

                if workholding is None or workholding.fixture_plate_occurrence is None or workholding.vise_occurrence is None:
                    use_workholding = False
                    workholding = None
                else:
                    # Update joints object with workholding reference
                    joints.workholding = workholding

            progressDialog.progressValue += 10
            adsk.doEvents()

            # Create deferred joint origins
            if parts:
                for part in parts:
                    part.create_joint_origins()

            if stock is not None and stock.has_joints:
                stock.create_joint_origins()

            # Create inter-container joints
            if stock is not None and stock.has_joints:
                self.create_stock_to_part_joint(joints, parts, stock, progressDialog)

            if workholding is not None:
                self.create_workholding_joints(joints, stock, ui, progressDialog)

        except Exception as e:
            log(traceback.format_exc())
            if ui:
                ui.messageBox(f"Error during import: {e}")
            return

        doc.activate()
        fusion.activateCAM()
        if needs_cancel(): return

        if toollibs is None:
            toollibs = [self.create_toollib(resp["fusion_tool_library"])]

        m = SetopMaterializer(
            setops=setops,
            parts = parts,
            stock = stock,
            workholding = workholding,
            fixture_parent_occ = fixture_parent_occ,
            joints = joints,
            progressDialog=progressDialog,
            needs_cancel=needs_cancel,
            toollibs=toollibs,
            config=config,
            fusion=fusion,
            reuse_existing_setups=use_existing_document,
            fusion_paths=self.fusion_paths,
            part_offset=part_offset,
            stock_entityToken=resp.get("stock_entityToken", None),      
            support_window_step_content=resp.get("support_window_step_content"),
            support_pedestal_step_content=resp.get("support_pedestal_step_content"),
            support_part_transform=part_transform,
        )
        m.execute()


    def get_program_from_server(self,share_key,needs_cancel,progressDialog):
        req = {
            "access_key" : share_key,
            "geometry" : None,
            "version" : get_addin_version(),
            "debug" : isdebug(),
            "subtypekey" : "RequestImportProgram",
            "v2" : True,
        }
        if needs_cancel(): return None, None
        progressDialog.message = "Requesting program from the cloud."
        resp = None
        err = None
        config = load_config()
        _config = deepcopy(config)
        progressDialog.progressValue += 10
        adsk.doEvents()

        # for debugging also allow importing from a local path
        if os.path.isfile(share_key):
            with open(share_key, "r") as f:
                resp = json.load(f)
        else:
            for deployment in ["production", "staging"]:
                if resp is not None:
                    break
                # the program might be on production or on staging
                # for convienience we try all possibilities here
                _config["app_environment"] = deployment
                client = Client(_config)
                try:
                    resp = client.request(req, method="POST")
                    break
                except Exception as e:
                    err = e
                    continue

        if resp is None:
            raise err

        # save the response for later use
        progressDialog.progressValue += 50
        adsk.doEvents()
        ui = Fusion().getUI()
        if needs_cancel(): return
        subtypekey = resp["subtypekey"]
        if subtypekey == "FusionError":

            ui.messageBox(resp["msg"])
            return None, None
        elif subtypekey == "ResponseImportProgram":
            pass
        else:
            raise Exception(f"TODO {subtypekey = }")
        if needs_cancel(): return None, None
        progressDialog.progressValue += 40
        adsk.doEvents()

        return resp,err

    def get_doc_and_design(self,resp, fusion, progressDialog,use_existing_document):
        # Create a new document
        progressDialog.message = "Program Received: Creating document"
        progressDialog.progressValue = 5
        adsk.doEvents()
        if use_existing_document:
            doc, design = get_current_design_doc()
        else:
            #TODO handle part_name not in response
            if "part_name" in resp:
                part_name = resp["part_name"]
            else:
                part_name = "Imported Part"
            doc, design = create_new_design_doc(doc_name = part_name)

            progressDialog.progressValue += 10
            adsk.doEvents()

        if use_existing_document:
            if not self.confirm_import_if_issues(fusion, doc, resp):
                log("Cancel import into current document.")
                return None,None

        return doc,design

    def import_program(self,share_key, fusion, needs_cancel, progressDialog,use_workholding,viseStyle,use_existing_document) -> typing.Union[adsk.core.Document, None]:
        resp, err = self.get_program_from_server(share_key,needs_cancel,progressDialog)

        if resp is None:
            if err is None:
                return
            else:
                raise err

        doc, design = self.get_doc_and_design(resp, fusion, progressDialog,use_existing_document)
        if doc is None:
            return

        return self.materialize_response(
            fusion=fusion,
            design=design,
            doc=doc,
            needs_cancel=needs_cancel,
            progressDialog=progressDialog,
            use_workholding=use_workholding,
            use_stock=True,
            viseStyle=viseStyle,
            use_existing_document=use_existing_document,
            resp=resp,
            config= load_config(),
        )
