import adsk.core
import math
import re
import adsk.cam
import adsk.fusion
from types import SimpleNamespace

from ...lib.setup_utils import get_operationId, get_setup, create_setup, delete_operations
from ...lib.component_utils import  TP_ROOT_COMPONENT_NAME
from ...lib.component_utils import FusionFullPath
from ...lib.fusion_utils import Fusion, add_component, get_step_file_content, import_step_file_to_component
from ...lib.general_utils import get_parameter, set_parameter, set_parameters, set_members
from ...lib.general_utils import CURRENT_CONFIG, UserException, log, compress_step_content, extract_step_body_names
from ...lib.geometry import jsonify_Matrix3D, inverse, get_xyz, compose_Matrix3D, transform_point
from copy import copy
import traceback
import json
from typing import List
#import time

class TPOccurrenceManager:
    def __init__(self, *, fusion, part_occ, name, joints, is_first_setup=False):
        assert isinstance(part_occ, adsk.fusion.Occurrence) or part_occ is None
        self.part_occ = part_occ
        self.fusion : Fusion = fusion
        self.tp_root_occ = None
        self.name = name
        self.joints = joints
        self.fixture_occurrences = []
        self.workholding_occ = None
        self.support_geometry_occ = None
        self.is_first_setup = is_first_setup

    def find_occurrence_by_name(self,occurrences,target_name):
        target_occurrence = occurrences.itemByName(target_name)
        if target_occurrence is None:
            for occurrence in occurrences:
                target_occurrence = self.find_occurrence_by_name(occurrence.childOccurrences,target_name)
                if target_occurrence is not None:
                    return target_occurrence
                else:
                    target_occurrence = None

        return target_occurrence
        
    def assemble_next_level(self, base_occurrence,ref_occurrence,stop_name):
        assembly_occurrence = ref_occurrence.nativeObject
        tmp_occurrence = base_occurrence.createForAssemblyContext(assembly_occurrence)
        if ref_occurrence.assemblyContext is None:
            return tmp_occurrence,ref_occurrence
        elif ref_occurrence.assemblyContext.name == stop_name:
            return tmp_occurrence,ref_occurrence
        else:
            return self.assemble_next_level(tmp_occurrence,ref_occurrence.assemblyContext,stop_name)

    def get_tp_root_occ(self) -> adsk.fusion.Occurrence:
        if self.tp_root_occ is None:
            part_occ = self.get_part_occ()
            design = self.fusion.getDesign()
            if part_occ is None:
                # body is in the root component, so just get a basic matrix
                transform = adsk.core.Matrix3D.create()
            else:
                transform = part_occ.transform2

            # if there is enough nesting
            parent_comp = design.rootComponent

            self.tp_root_occ = add_component(
                parent_comp,
                name=TP_ROOT_COMPONENT_NAME + ": " + self.name,
                transform=transform,
                isLightBulbOn=self.is_first_setup,
                isGroundToParent=False,
            )

            if part_occ is None:
                return self.tp_root_occ

            joint_origins = part_occ.component.jointOrigins
            joint_origin = joint_origins.itemByName("Part Origin")
            if joint_origin is None:
                return self.tp_root_occ

            stop_name = design.rootComponent.name+":1"
            tmp2_occurrence3 = None
            if part_occ.assemblyContext is None:
                tmp2_occurrence3 = part_occ
            else:
                # The stored part_occ may carry CAM workspace context
                # (path includes "CAM Component:1+..."), making it invalid
                # for design-level joint creation. Try to find the occurrence
                # directly from root's children to get a clean reference.
                root_occ = design.rootComponent.occurrences.itemByName(part_occ.name)
                if root_occ is not None:
                    tmp2_occurrence3 = root_occ
                else:
                    try:
                        tmp2_occurrence3,occurrence1 = self.assemble_next_level(part_occ.nativeObject,part_occ.assemblyContext,stop_name)
                    except Exception:
                        # Can't resolve occurrence path - skip joint creation
                        pass

            # Only create joint if we have a valid occurrence reference
            if tmp2_occurrence3 is not None:
                root_joint = self.create_joint_origin_at_origin(self.tp_root_occ.component,"Geometry Origin")

                joint_comp = self.tp_root_occ
                # Joint creation may fail for existing documents where the part doesn't have
                # the expected joint origins - this is non-fatal, we just skip the joint
                self.joints.create_rigid_joint_between_components(design.rootComponent, tmp2_occurrence3, joint_comp,TP_ROOT_COMPONENT_NAME + ": " + self.name, first_comp_target="Part Origin",second_comp_target="Geometry Origin")

        return self.tp_root_occ

    def create_joint_origin_at_origin(self,comp,joint_name = "tmp"):
               
        sketches = comp.sketches
        axes = comp.constructionAxes
   
        # Step 1: Create a new sketch on the XY plane
        sketch : adsk.fusion.Sketch = sketches.add(comp.xYConstructionPlane)
        sketch.name = "Origin Joint"
        sketch.isLightBulbOn = False
        joint_vertex = sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))

        # Create a Joint Origin at the target vertex
        joint_origins = comp.jointOrigins
        if joint_origins.itemByName(joint_name) is None:
            joint_origin_input = joint_origins.createInput(
                    adsk.fusion.JointGeometry.createByPoint(joint_vertex)
                )
            joint_origin = joint_origins.add(joint_origin_input)
            joint_origin.name = joint_name
            if joint_origin.isLightBulbOn:
                joint_origin.isLightBulbOn = False
        vertex_point = joint_vertex.geometry
        
        return vertex_point

    def get_T_world_from_sketch(self) -> adsk.core.Matrix3D:
        return inverse(self.get_T_sketch_from_world())
    def get_T_sketch_from_world(self):
        return self.get_tp_root_occ().transform2
        return geom.inverse(self.get_T_world_from_sketch())

    def get_touch_avoid_occ(self) -> adsk.fusion.Occurrence:
        return self.get_tp_root_occ()

    def get_sketch_occ(self) -> adsk.fusion.Occurrence:
        return self.get_tp_root_occ()

    def get_stock_occ(self) -> adsk.fusion.Occurrence:
        return self.get_tp_root_occ()

    def get_part_occ(self) -> adsk.fusion.Occurrence:
        return self.part_occ

    def get_fixture_occ(self) -> adsk.fusion.Occurrence:
        """Return the per-setup fixture parent occurrence (same as TP root)."""
        return self.get_tp_root_occ()

    def get_support_geometry_occ(self) -> adsk.fusion.Occurrence:
        """Return the support geometry container occurrence, or None."""
        return self.support_geometry_occ

    def import_fixtures(self, fixture_data, design, part_offset=None):
        """Import fixtures into this setup's TP root component.

        Args:
            fixture_data: Dictionary with fixture parameters, or None
            design: The Fusion design to import into
            part_offset: Optional offset in mm [x, y, z] for positioning

        Returns:
            List of imported fixture occurrences
        """
        if fixture_data is None:
            return []

        from ...lib.fixture_utils import import_fixture_solids

        parent_comp = self.get_fixture_occ().component
        fixtures, workholding_occ = import_fixture_solids(
            fixture_data=fixture_data,
            design=design,
            fusion=self.fusion,
            part_offset=part_offset or [0.0, 0.0, 0.0],
            parent_component=parent_comp
        )
        self.fixture_occurrences = fixtures
        self.workholding_occ = workholding_occ
        return workholding_occ

    def import_support_geometry(self, window_step_content, pedestal_step_content, part_transform):
        """Import support geometry STEP files into a container component.

        Creates a "Support Geometry" container positioned with part_transform,
        then imports window and/or pedestal STEP content into it.

        Returns:
            The container occurrence, or None if no support content.
        """
        if not window_step_content and not pedestal_step_content:
            return None

        design = self.fusion.getDesign()
        container_occ = add_component(
            design.rootComponent,
            name="Support Geometry",
            isGroundToParent=True,
            isLightBulbOn=True,
            transform=part_transform,
        )
        container_comp = container_occ.component

        for step_content, name in [
            (window_step_content, "Support Window"),
            (pedestal_step_content, "Support Pedestal"),
        ]:
            if not step_content:
                continue
            import_step_file_to_component(step_content, container_comp, fusion=self.fusion)
            occ = container_comp.occurrences.item(container_comp.occurrences.count - 1)
            occ.component.name = name

        self.support_geometry_occ = container_occ
        return container_occ


class SketchBook:
    def __init__(self, design : adsk.fusion.Design, json, occman : TPOccurrenceManager):
        self.json_sketch_by_id = json["sketch_by_id"]
        self.sketch_by_id = {}
        self.selectable_by_id = {}
        self.patch_feature_by_id = {}
        self.extrude_feature_by_id = {}
        self.design = design
        self.occman = occman
        self.touch_avoid_occ = None
        self.last_created = None

    def get_touch_avoid_component(self) -> adsk.fusion.Component:
        return self.occman.get_touch_avoid_occ().component

    def getSketch(self, sketch_id) -> adsk.fusion.Sketch:
        if sketch_id in self.sketch_by_id:
            return self.sketch_by_id[sketch_id]
        else:
            sel = self.createSketch(sketch_id)
            return sel

    def getSelectable(self, sketch_id) -> list:
        self.getSketch(sketch_id) # make sure sketch exists
        occurrence = self.occman.get_sketch_occ()
        # return [selectable.createForAssemblyContext(occurrence) for selectable in self.selectable_by_id[sketch_id]]
        return self.selectable_by_id[sketch_id]
        #return self.selectable_by_id[sketch_id].createForAssemblyContext(occurrence)

    # def getSketchCurves(self, sketch_id,component) -> adsk.fusion.SketchCurves:
    #     sketch = self.getSketch(sketch_id,component)
    #     return sketch.sketchCurves

    # def getSketchPoints(self, sketch_id,component) -> adsk.fusion.SketchPoints:
    #     sketch = self.getSketch(sketch_id,component)
    #     return list(sketch.sketchPoints)[1:-1] # for some reason the first point is an extra origin...

    def create_patch_feature(self, sketch_id) -> adsk.fusion.PatchFeature:
        sketch = self.getSketch(sketch_id)
        
        # find the id of the profile with the maximum number of loops
        profileId = max(range(len(sketch.profiles)), key=lambda i: len(sketch.profiles.item(i).profileLoops))
        targetProfile = sketch.profiles.item(profileId)
        # Create the patch feature
        comp = self.get_touch_avoid_component()
        patches = comp.features.patchFeatures
        patchInput = patches.createInput(targetProfile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        patchFeature = patches.add(patchInput)
        self.patch_feature_by_id[sketch_id] = patchFeature
        return patchFeature

    def get_patch_feature(self, sketch_id) -> adsk.fusion.PatchFeature:
        ret = self.patch_feature_by_id.get(sketch_id, None)
        if ret is None:
            ret = self.create_patch_feature(sketch_id)
        return ret

    def get_brep_surface(self, sketch_id) -> adsk.fusion.BRepBody:
        patchFeature = self.get_patch_feature(sketch_id)
        body = patchFeature.bodies.item(0)
        return body

    def dist_box_point(self, box, p2) -> float:
        x_min = box.minPoint.x
        y_min = box.minPoint.y
        z_min = box.minPoint.z
        x_max = box.maxPoint.x
        y_max = box.maxPoint.y
        z_max = box.maxPoint.z
        def clamp(x, lo, hi):
            return max(lo, min(x, hi))
        d2 = (clamp(p2.x, x_min, x_max) - p2.x)**2 + (clamp(p2.y, y_min, y_max) - p2.y)**2 + (clamp(p2.z, z_min, z_max) - p2.z)**2
        return math.sqrt(d2)

    def create_extrude_feature(self, selection_json, i) -> adsk.fusion.ExtrudeFeature:
        sketch_id = selection_json["sketch_ids"][i]
        height = selection_json["heights"][i]
        if "top_center_sketch_ids" in selection_json.keys():
            top_center_sketch_id = selection_json["top_center_sketch_ids"][i]
            top_center_point = self.getSketch(top_center_sketch_id).sketchPoints.item(1).geometry
        else:
            # backwards compat
            top_center_point = None
        sketch = self.getSketch(sketch_id)
        # sketch should only have a single curve
        profile = sketch.profiles.item(0)

        # Create the extrude feature
        comp = self.get_touch_avoid_component()
        extrudeFeature_best = None
        d_best = math.inf
        for direction in [
            adsk.fusion.ExtentDirections.NegativeExtentDirection,
            adsk.fusion.ExtentDirections.PositiveExtentDirection,
        ]:
            # The API for extruding features is unreliable
            # more precisely it is unpredictable on which side the extrusion happens
            # as a workaround we check if the top_center_point is inside the extrusion and if not
            # extrude the other way
            extrudeFeatures = comp.features.extrudeFeatures
            extrudeFeatureInput = extrudeFeatures.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            extrudeFeatureInput.isSolid = False
            extent_distance = adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByReal(height))
            extrudeFeatureInput.setOneSideExtent(extent_distance, direction)
            extrudeFeature = extrudeFeatures.add(extrudeFeatureInput)
            bb = extrudeFeature.bodies.item(0).boundingBox
            if top_center_point is None:
                # backwards compat
                d = 0.0
            else:
                d = self.dist_box_point(bb, top_center_point)

            if d < d_best:
                d_best = d
                extrudeFeature_best = extrudeFeature

            if d_best < 1e-6:
                break
        
        self.extrude_feature_by_id[sketch_id] = extrudeFeature_best
        return extrudeFeature
    
    def get_extrude_feature(self, selection_json, i) -> adsk.fusion.ExtrudeFeature:
        sketch_id = selection_json["sketch_ids"][i]
        ret = self.extrude_feature_by_id.get(sketch_id, None)
        if ret is None:
            ret = self.create_extrude_feature(selection_json, i)
        return ret
    
    def get_extrude_surface(self, selection_json, i) -> adsk.fusion.BRepBody:
        extrudeFeature = self.get_extrude_feature(selection_json, i)
        body = extrudeFeature.bodies.item(0)
        return body
    
    def createSketch(self, sketch_id) -> adsk.fusion.Sketch:
        json_sketch = self.json_sketch_by_id[sketch_id]
        comp = self.occman.get_sketch_occ().component
        sketch : adsk.fusion.Sketch = comp.sketches.add(comp.xYConstructionPlane)
        self.last_created = sketch
        sketch.isComputeDeferred = True
        selectable = []
        if json_sketch["subtypekey"] == "Sketch_points":
            sketchPoints : adsk.fusion.SketchPoints = sketch.sketchPoints
            for json_point in json_sketch["sketchPoints"]:
                pt = adsk.core.Point3D.create(*json_point["point"])
                spt : adsk.fusion.SketchPoint = sketchPoints.add(pt)
                selectable.append(spt)
        elif json_sketch["subtypekey"] == "Sketch_curves":

            sketchLines : adsk.fusion.SketchLines = sketch.sketchCurves.sketchLines 
            sketchArcs : adsk.fusion.SketchLines = sketch.sketchCurves.sketchArcs
            # sketchPoints : adsk.fusion.SketchPoints = sketch.sketchPoints

            for json_seg in json_sketch["sketchSegments"]:
                if json_seg["subtypekey"] == "SketchArcByCenterStartEnd":
                    json_arc = json_seg
                    centerPoint = adsk.core.Point3D.create(*json_arc["centerPoint"])
                    startPoint = adsk.core.Point3D.create(*json_arc["startPoint"])
                    endPoint = adsk.core.Point3D.create(*json_arc["endPoint"])
                    normal = adsk.core.Vector3D.create(*json_arc["normal"])
                    sa = sketchArcs.addByCenterStartEnd(centerPoint, startPoint, endPoint, normal)
                    selectable.append(sa)
                elif json_seg["subtypekey"] == "SketchLineByTwoPoints":
                    json_sketchLine = json_seg
                    startPoint = adsk.core.Point3D.create(*json_sketchLine["startPoint"])
                    endPoint = adsk.core.Point3D.create(*json_sketchLine["endPoint"])
                    sl = sketchLines.addByTwoPoints(startPoint, endPoint)
                    selectable.append(sl)
                else:
                    raise Exception(f"Unknown sketch segment subtype {json_seg['subtypekey']}")
        else:
            raise Exception(f"Unknown sketch subtype {json_sketch['subtypekey']}")

        sketch.isComputeDeferred = False

        set_members(sketch, json_sketch["Sketch_members"])

        self.sketch_by_id[sketch_id] = sketch
        self.selectable_by_id[sketch_id] = selectable
        return sketch

class UserSpecifiedSetip:
    def __init__(self, obj : adsk.cam.Setup, compute_fusionops : bool, multi_axis="ThreeAxis") -> None:
        assert isinstance(obj, adsk.cam.Setup)
        self.obj : adsk.cam.Setup = obj
        self.compute_fusionops = compute_fusionops
        self.multi_axis = multi_axis
        self.fusion = Fusion()
        self.fusion_paths = FusionFullPath()

    def jsonify_stock_parameters(self) -> dict:
        setup : adsk.cam.Setup = self.obj
        job_stockMode = get_parameter(setup, "job_stockMode").value.value
        ret = {"job_stockMode" : job_stockMode}
        for param in setup.parameters:
            param : adsk.cam.CAMParameter
            name = param.name
            v = self.maybe_stock_param_value_value(name, param)
            if v is None:
                continue
            ret[name] = v
        return ret

    def jsonify_fixture_info(self):
        if CURRENT_CONFIG.get("send_job_fixture", False) == False:
            return None
        p = get_parameter(self.obj, "job_fixture")
        fixtures = p.value.value
        solids = []
        ret = {
            "subtypekey" : "FixtureInfoSolids",
            "solids" : solids,
        }
        for fixture in fixtures:
            bnts = self.fusion_paths.extract_bodies_and_transforms(fixture, all=True)
            for (body, transform) in bnts:
                step_file_content, saved_in_world = get_step_file_content(self.fusion, body)
                step_cs = adsk.core.Matrix3D.create() if saved_in_world else transform

                # TODO: Enable compression for fixture STEP files once the Julia server-side
                # FusionFixtureSolid struct (in FusionTP/src/types.jl) is updated to handle
                # the step_file_compression field and decompress before use.
                # step_file_compressed, compression_info = compress_step_content(step_file_content)
                solid = {
                    "subtypekey" : "FusionFixtureSolid",
                    "step_file_content" : step_file_content,
                    # "step_file_compression" : compression_info["compression"],
                    "stepCoordinateSystem_cm" : jsonify_Matrix3D(step_cs),
                }
                solids.append(solid)
        return ret
        

    def maybe_stock_param_value_value(self, name, param: adsk.cam.CAMParameter):
        if not param.isEditable:
            # to keep the json small
            return None
        if not "stock" in name.lower():
            return None
        val = param.value.value
        if not isinstance(val, (bool, float, int, str)):
            return None
        return val

    def get_job_stockMode(self):
        setup : adsk.cam.Setup = self.obj
        job_stockMode = get_parameter(setup, "job_stockMode").value.value
        return job_stockMode

    def is_stock_defining(self):
        job_stockMode = self.get_job_stockMode()
        return job_stockMode != "previoussetup"

    def get_name(self):
        setup : adsk.cam.Setup = self.obj
        return setup.name

    def diagnose(self) -> List[str]:
        issues = []
        if not self.compute_fusionops:
            return issues
        job_stockMode = self.get_job_stockMode()
        if job_stockMode in ("previoussetup", "solid", "default", "fixedbox"):
            return issues
        config = CURRENT_CONFIG
        if config.get("experimental", False) and (job_stockMode in ("fixedcylinder", "relativecylinder")):
            return issues
        setup = self.obj
        msg = f"""Setup {setup.name} uses unsupported stock: {job_stockMode}."""
        issues.append(msg)
        return issues

    def jsonify(self, part_saved_in_world_space=False):
        setup : adsk.cam.Setup = self.obj
        ret = {}
        job_stockMode = self.get_job_stockMode()

        ret["job_stockMode"] = job_stockMode
        for key in [
            "stockXLow", 
            "stockYLow",
            "stockZLow",     
            "stockXHigh",    
            "stockYHigh",    
            "stockZHigh",    
            "job_type",
            "job_continueMachining",
            "job_groundStockModelOrigin",
            ]:
            param = get_parameter(setup, key)
            ret[key] = param.value.value
        ret["operationId"] = self.get_operationId()
        ret["stock_parameters"] = self.jsonify_stock_parameters()
        if self.compute_fusionops:
            if job_stockMode == "solid":
                solids = get_parameter(setup, "job_stockSolid").value.value
                bnts = self.fusion_paths.extract_bodies_and_transforms(solids[0])
                stock_solid = {"subtypekey" : "FusionStockSolid"}
                ret["stock_info"] = stock_solid
                if len(bnts) == 1:
                    # TODO what types of body are possible? BRepBody for sure. Occurence? Compontent?
                    # TODO do we ever need some additional transformations here?
                    body, transform = bnts[0]
                    if not isinstance(body, (adsk.fusion.BRepBody, adsk.fusion.Component, adsk.fusion.Occurrence)):
                        raise Exception(f"Unsupported stock solid. Please pick another stock.")

                    # Get STEP file content first, then extract actual body name from it
                    step_file_content, saved_in_world = get_step_file_content(self.fusion, body)
                    step_cs = adsk.core.Matrix3D.create() if saved_in_world else transform

                    # Extract actual body name from STEP file's MANIFOLD_SOLID_BREP entity BEFORE compression
                    # This is what Parasolid/Julia will use when reading the STEP file
                    step_solid_brep_names = extract_step_body_names(step_file_content)

                    # TODO: Enable compression for stock STEP files once the Julia server-side
                    # FusionStockSolid struct (in FusionTP/src/types.jl) is updated to handle
                    # the step_file_compression field and decompress before use in
                    # load_body_from_fusion_stock_solid() (FusionTP/src/setups.jl)
                    # step_file_compressed, compression_info = compress_step_content(step_file_content)
                    # stock_solid["step_file_content"] = step_file_compressed
                    # stock_solid["step_file_compression"] = compression_info["compression"]
                    stock_solid["step_file_content"] = step_file_content
                    if step_solid_brep_names:
                        stock_solid["name"] = step_solid_brep_names[0]
                    else:
                        stock_solid["name"] = body.name
                    stock_solid["stepCoordinateSystem_cm"] = jsonify_Matrix3D(step_cs)

                if self.compute_fusionops:
                    if len(solids) != 1:
                        raise Exception(f"Only a single stock solid is supported. Got {len(solids)} instead.")
                    if len(bnts) != 1:
                        raise Exception(f"Only a single stock solid is supported. Got {len(bnts)} instead.")

            elif job_stockMode in ("relativecylinder", "fixedcylinder"):
                job_stockAxisEnabled = get_parameter(setup, "job_stockAxisEnabled").value.value
                if job_stockAxisEnabled:
                    p = get_parameter(setup, "job_stockAxis")
                    edges = p.value.value
                    if len(edges) != 1:
                        raise UserException(f"Only a single stock axis is supported for cylinder stock. Got {len(edges)} instead.")
                    edge = edges[0]
                    if not isinstance(edge, adsk.fusion.BRepEdge):
                        raise UserException(f"Unsupported stock axis selection for cylinder.", f"{type(edge) =}")
                    if not edge.geometry.classType() == "adsk::core::Line3D":
                        raise UserException(f"Unsupported stock axis selection. Expected a line, got {edge.geometry.classType()}.")
                    trafo = self.fusion_paths.extract_transform_from_body(edge.body)
                    axis = {
                        "subtypekey" : "JobStockAxis_BRepEdge",
                        "startPoint" : get_xyz(edge.geometry.startPoint),
                        "endPoint" : get_xyz(edge.geometry.endPoint),
                        "coord_system_cm" : jsonify_Matrix3D(trafo),
                    }
                    ret["stock_info"] = {
                        "subtypekey" : "JobStockInfoCylinder", 
                        "job_stockAxis" : axis,
                    }

        if part_saved_in_world_space:
            ret["stepCoordinateSystem_cm"] = jsonify_Matrix3D(adsk.core.Matrix3D.create())
        else:
            ret["stepCoordinateSystem_cm"] = self.get_json_stepCoordinateSystem_cm()
        ret["workCoordinateSystem_mm"] = self.get_json_workCoordinateSystem_mm()
        ret["compute_fusionops"] = self.compute_fusionops
        ret["fixture_info"] = self.jsonify_fixture_info()
        ret["multi_axis"] = self.multi_axis

        return ret

    def get_stepCoordinateSystem_cm(self):
        results = self.get_bodies_and_transforms()
        if len(results) == 1:
            _, step_coord_sys = results[0]
        else:
            step_coord_sys = adsk.core.Matrix3D.create()
        return step_coord_sys

    def get_workCoordinateSystem_mm(self):
        return self.obj.workCoordinateSystem

    def get_T_world_from_wcs(self):
        T_wcs_from_world_mm = self.get_workCoordinateSystem_mm()
        T_wcs_from_world_cm = T_wcs_from_world_mm.copy()
        translation_mm = T_wcs_from_world_cm.translation
        translation_cm = adsk.core.Vector3D.create(translation_mm.x * 0.1, translation_mm.y * 0.1, translation_mm.z * 0.1)
        T_wcs_from_world_cm.translation = translation_cm
        return T_wcs_from_world_cm

    def get_T_wcs_from_world(self):
        return inverse(self.get_T_world_from_wcs())

    def get_T_world_from_pcs(self):
        return self.get_stepCoordinateSystem_cm()

    def get_T_pcs_from_world(self):
        return inverse(self.get_T_world_from_pcs())

    def get_T_pcs_from_wcs(self):
        T_pcs_from_world = self.get_T_pcs_from_world()
        T_world_from_wcs = self.get_T_world_from_wcs()
        return compose_Matrix3D(T_pcs_from_world, T_world_from_wcs)

    def get_T_wcs_from_pcs(self):
        T_wcs_from_world = self.get_T_wcs_from_world()
        T_world_from_pcs = self.get_T_world_from_pcs()
        return compose_Matrix3D(T_wcs_from_world, T_world_from_pcs)

    def get_machining_direction_step(self):
        # get the machining direction in part coordinate system (pcs)
        T = self.get_T_pcs_from_wcs()
        return jsonify_Matrix3D(T)["zaxis"]

    def get_json_stepCoordinateSystem_cm(self):
        step_coord_sys = self.get_stepCoordinateSystem_cm()
        return jsonify_Matrix3D(step_coord_sys)

    def get_json_workCoordinateSystem_mm(self):
        return jsonify_Matrix3D(self.obj.workCoordinateSystem)

    def get_operationId(self) -> str:
        return get_operationId(self.obj)

    def get_bodies_and_transforms(self):
        return self.fusion_paths.get_bodies_and_transforms(self.obj)

    def get_model(self):
        return self.fusion_paths.get_model(self.obj)

    def get_models(self):
        return self.fusion_paths.get_models(self.obj)

    def nbodies(self):
        return len(self.get_bodies_and_transforms())

class UserSpecifiedSetips:
    def __init__(self, setips : List[UserSpecifiedSetip]):
        # check all selected setups have consistent body selection
        nb0 = None
        has_body = False
        has_any_selected = False

        for s in setips:
            if not s.compute_fusionops:
                continue
            has_any_selected = True
            nbodies = s.nbodies()
            if nbodies == 0:
                continue
            elif nbodies > 1:
                msg = f"""
                Setup "{s.obj.name}" is selected, but has {nbodies} bodies. Setups with multipe bodies are not supported.
                """
                raise UserException(msg)

            has_body = True
            nb = s.get_bodies_and_transforms()[0][0].nativeObject
            if nb0 is None:
                nb0 = nb
            # Compare entityToken instead of object identity - Fusion creates new wrapper objects each time
            if not nb.entityToken == nb0.entityToken:
                msg = "All selected setups must share the same model."
                raise UserException(msg, "body.nativeObject must be equal for all models.")
        if not has_any_selected:
            msg = "No setup is selected. Please select a setup."
            raise UserException(msg)
        if not has_body:
            msg = "No model found in any selected setup. Please select a model."
            raise UserException(msg)
        self.setips = setips
        self.document_creationId = Fusion().getActiveDocument().creationId
    
    def diagnose(self) -> List[str]:
        issues = []
        selected_stock_definining_setips = []
        stock_definining_setips = []
        selected_directions = []
        selected_names = []
        n_selected = 0
        for s in self.setips:
            issues.extend(s.diagnose())
            if s.compute_fusionops:
                n_selected += 1
            if s.is_stock_defining():
                stock_definining_setips.append(s)
                if s.compute_fusionops:
                    selected_stock_definining_setips.append(s)
            if s.compute_fusionops:
                direction = s.get_machining_direction_step()
                selected_names.append(s.get_name())
                selected_directions.append(direction)

        if n_selected == 0:
            msg = f"""
            Expected at least one selected setup, found none.
            """
            issues.append(msg)
        if len(stock_definining_setips) == 0:
            msg = f"""
            Expected exactly one stock defining setup, found none.
            """
            issues.append(msg)

        for (i1, dir1) in enumerate(selected_directions):
            for (i2, dir2) in enumerate(selected_directions):
                if i2 <= i1: 
                    continue
                x1, y1, z1 = dir1
                x2, y2, z2 = dir2
                d = math.sqrt((x1 - x2)**2 + (y1 - y2)**2 + (z1 - z2) ** 2)
                if d < 1e-7:
                    msg = f"""
                    All selected setups require unique Z-axes.
                    {selected_names[i1]} and {selected_names[i2]} share the same Z-axis.
                    Please deselect one of them.
                    """
                    issues.append(msg)

        if len(selected_stock_definining_setips) > 1:
            msg = f"""
            Expected exactly one stock defining setup, found {len(selected_stock_definining_setips)}:
            {", ".join([s.get_name() for s in selected_stock_definining_setips])}
            """
            issues.append(msg)

        s = self.get_bodies_defining_setip()
        
        nbodies = s.nbodies()
        if nbodies != 1:
            msg = f"""
            Setup {s.get_name()} has {nbodies} bodies. We only support exactly one body.
            """
            issues.append(msg)

        # check exactly one stock defining setip
        return issues

    def jsonify(self, part_saved_in_world_space=False)->dict:
        return {
            "subtypekey" : "UserSpecifiedSetips",
            "document_creationId" : self.document_creationId,
            "setips" : [s.jsonify(part_saved_in_world_space=part_saved_in_world_space) for s in self.setips if s.compute_fusionops],
        }

    def get_bodies_defining_setip(self):
        fusion_paths = FusionFullPath()
        # try bodies from first selected setup
        # if there is none, also allow from unselected
        ifirst_selected = -1
        for (i, s) in enumerate(self.setips):
            if s.compute_fusionops:
                ifirst_selected = i
                if s.nbodies() > 0:
                    return s
                break
        
        # now try the last unselected setup that defines a single body
        for i in range(ifirst_selected -1, -1, -1):
            s = self.setips[i]
            if s.nbodies() > 0:
                return s

        return self.setips[ifirst_selected]

    def get_body(self) -> adsk.fusion.BRepBody:
        fusion_paths = FusionFullPath()
        s = self.get_bodies_defining_setip()
        bnts = s.get_bodies_and_transforms()
        return bnts[0][0]
    
    def get_geometry_tracking_data(self):
        return _get_geometry_tracking_data(self)
        
class AutoSetips:
    def __init__(self,
            body,
            fusion,
            stock_body=None,
            native_body_for_export=None,
                 ):

        if body is None:
            return
        #     raise Exception("Body cannot be None")
        self.body = body
        # Store native body for STEP export (preserves colors/appearance).
        # If not provided, extract from proxy body or use the body itself.
        if native_body_for_export is not None:
            self._native_body_for_export = native_body_for_export
        elif body.nativeObject is not None:
            self._native_body_for_export = body.nativeObject
        else:
            self._native_body_for_export = body

        #assert isinstance(body, adsk.fusion.BRepBody)
        if body.assemblyContext is None:
            self.transform = adsk.core.Matrix3D.create()
        else:
            self.transform = body.assemblyContext.transform2

        self.fusion = fusion
        self.document_creationId = Fusion().getActiveDocument().creationId

        self.stock_body = stock_body
        self.stock_body_transform = None
        if stock_body is not None:
            if stock_body.assemblyContext is None:
                self.stock_body_transform = adsk.core.Matrix3D.create()
            else:
                self.stock_body_transform = stock_body.assemblyContext.transform2

    def get_body_and_transform(self):
        return (self.body, self.transform)

    def diagnose(self) -> List[str]:
        return []

    def get_body(self) -> adsk.fusion.BRepBody:
        return self.body

    def get_body_for_export(self) -> adsk.fusion.BRepBody:
        """Get the native body for STEP export (preserves colors/appearance)."""
        return self._native_body_for_export
    
    def get_geometry_tracking_data(self):
        return _get_geometry_tracking_data(self)

    def jsonify(self, part_saved_in_world_space=False)->dict:

        if part_saved_in_world_space:
            step_coord_sys = adsk.core.Matrix3D.create()
        else:
            step_coord_sys = self.get_body_and_transform()[1]
        stepCoordinateSystem_cm = jsonify_Matrix3D(step_coord_sys)
        entityToken = self.get_body().entityToken
        ret = {
            "subtypekey" : "AutoSetips",
            "stepCoordinateSystem_cm" : stepCoordinateSystem_cm,
            "document_creationId" : self.document_creationId,
            "model_entityToken" : entityToken,
        }

        if self.stock_body is not None:
            step_file_content, saved_in_world = get_step_file_content(self.fusion, self.stock_body, debug_name="debug-stock")
            step_cs = adsk.core.Matrix3D.create() if saved_in_world else self.stock_body_transform
            step_solid_brep_names = extract_step_body_names(step_file_content)
            stock_name = step_solid_brep_names[0] if step_solid_brep_names else self.stock_body.name
            ret["stock_solid"] = {
                "subtypekey": "FusionStockSolid",
                "step_file_content": step_file_content,
                "name": stock_name,
                "stepCoordinateSystem_cm": jsonify_Matrix3D(step_cs),
            }
            ret["stock_entityToken"] = self.stock_body.entityToken

        return ret


def check_key_val(json : dict, key : str, val):
    if not key in json.keys():
        raise Exception(f"Expected key {key}, to be present.")

    actual_val = json[key]
    if val != actual_val:
        raise Exception(f"Expected {key} = {val}, got {actual_val} instead.")

def get_brep_edges(facet_id_table, body, selection) -> List[adsk.fusion.BRepEdge]:
    edges = [facet_id_table.get_edge_by_id(body, eid) for eid in selection["edge_ids"]]
    return edges

def get_brep_faces(facet_id_table, body, body_occurrence, selection) -> List[adsk.fusion.BRepFace]:
    return [facet_id_table.get_face_by_id(body, fid).createForAssemblyContext(body_occurrence) for fid in selection["face_ids"]]

def are_equal(p1 : adsk.core.Point3D, p2 : adsk.core.Point3D, tol : float = 1e-05) -> bool:
    return abs(p1.x - p2.x) < tol and abs(p1.y - p2.y) < tol and abs(p1.z - p2.z) < tol

def get_transformed_z_coordinate(point: adsk.core.Point3D, matrix: adsk.core.Matrix3D):
    copied_point = point.copy()
    success = copied_point.transformBy(matrix)
    return copied_point.z if success else point.z

def is_close_chain(sketch_lines:adsk.fusion.SketchLines):
    first_index = 0
    last_index = sketch_lines.count - 1
    return sketch_lines[first_index].startSketchPoint.geometry.isEqualTo(sketch_lines[last_index].endSketchPoint.geometry)

def select_faces_using_CadContours2dParameterValue(
    contourParam :adsk.cam.CadContours2dParameterValue, 
    faces : List[adsk.fusion.BRepFace],
    ):
    curveSelections = contourParam.getCurveSelections()
    facecontourSel: adsk.cam.FaceContourSelection = curveSelections.createNewFaceContourSelection()
    facecontourSel.loopType = adsk.cam.LoopTypes.AllLoops
    facecontourSel.sideType = adsk.cam.SideTypes.StartOutsideSideType
    facecontourSel.inputGeometry = faces
    contourParam.applyCurveSelections(curveSelections)

def select_using_CadContours2dParameterValue(
    fusion : Fusion,
    contourParam :adsk.cam.CadContours2dParameterValue,
    body_occurrence,
    selection,
    facet_id_table,
    sketch_book : SketchBook,
    setup_body = None,
):
    assert isinstance(contourParam, adsk.cam.CadContours2dParameterValue)
    subtypekey = selection["subtypekey"]
    if subtypekey == "NoSelection":
        return
    elif subtypekey == "MultiSelection":
        # It is important not to instantiate curveSelection
        # In case of MultiSelection.
        # Doing so leads to heisenbugs
        for sel in selection["selections"]:
            select_using_CadContours2dParameterValue(fusion, contourParam, body_occurrence, sel, facet_id_table, sketch_book, setup_body=setup_body)
        return

    fusion_paths = FusionFullPath()
    body = fusion_paths.get_body(body_occurrence)
    if subtypekey == "EdgeSelection":
        curveSelections = contourParam.getCurveSelections()
        chain = curveSelections.createNewChainSelection()
        chain.isOpen = selection["isOpen"]
        chain.isReverted = selection.get("isReverted", False)
        chain.inputGeometry = get_brep_edges(facet_id_table, body, selection)
        contourParam.applyCurveSelections(curveSelections)
    elif subtypekey == "FaceSelection":
        faces = get_brep_faces(facet_id_table, body,body_occurrence, selection)
        select_faces_using_CadContours2dParameterValue(contourParam, faces)
    elif subtypekey == "PocketSelection":
        curveSelections = contourParam.getCurveSelections()
        pocketSel: adsk.cam.PocketSelection = curveSelections.createNewPocketSelection()
        pocketSel.isSelectingSamePlaneFaces = False
        pocketSel.inputGeometry = get_brep_faces(facet_id_table, body, body_occurrence,selection)
        contourParam.applyCurveSelections(curveSelections)
    elif subtypekey == "SketchSelection_curves":
        curveSelections = contourParam.getCurveSelections()
        for [i,sketch_id] in enumerate(selection["sketch_ids"]):
            chain = curveSelections.createNewChainSelection()
            chain.isOpen = selection["isOpen"][i]
            chain.isReverted = selection["isReverted"][i]
            sel = sketch_book.getSelectable(sketch_id)
            chain.inputGeometry = sel
        contourParam.applyCurveSelections(curveSelections)
    elif subtypekey == "Silhouette":
        # Use setup_body (the actual body from the setup) if available, otherwise fall back to body from occurrence
        silhouette_body = setup_body if setup_body is not None else body
        curveSelections = contourParam.getCurveSelections()
        silhouetteSel: adsk.cam.SilhouetteSelection = curveSelections.createNewSilhouetteSelection()
        silhouetteSel.isSetupModelSelected = False
        silhouetteSel.loopType = adsk.cam.LoopTypes.OnlyOutsideLoops
        silhouetteSel.inputGeometry = [silhouette_body]
        contourParam.applyCurveSelections(curveSelections)
    elif subtypekey == "DiameterSelection":
        select_using_CadContours2dParameterValue(
            fusion=fusion,
            contourParam=contourParam,
            body_occurrence=body_occurrence,
            selection=selection["containmentBoundary"],
            facet_id_table=facet_id_table,
            sketch_book=sketch_book,
            setup_body=setup_body,
        )
    else:
        raise Exception(f"Unexpected selection subtypekey: {subtypekey} for contour")


def select_using_CadObjectParameterValue(param : adsk.cam.CadObjectParameterValue, 
            body_occurrence, selection, facet_id_table, sketch_book : SketchBook):
    assert isinstance(param, adsk.cam.CadObjectParameterValue)
    subtypekey = selection["subtypekey"]
    if subtypekey == "NoSelection":
        return
    elif subtypekey == "FaceSelection":
        fusion_paths = FusionFullPath()
        body = fusion_paths.get_body(body_occurrence)
        # patch op
        brep_faces = get_brep_faces(facet_id_table, body, body_occurrence,selection)
        faces = param.value
        for brep_face in brep_faces:
            faces.append(brep_face)
        param.value = faces
    elif subtypekey == "SketchSelection_points":
        sketch_id = selection["sketch_id"]
        param.value = sketch_book.getSelectable(sketch_id)
    else:
        raise Exception(f"Unexpected selection subtypekey: {subtypekey}")


def prepare_op(setup : adsk.cam.Setup, op_json, tool : adsk.cam.Tool, config, sketch_book : SketchBook) -> adsk.cam.OperationBase:
    assert isinstance(setup, adsk.cam.Setup)
    strategy = op_json["strategy"]
    input = setup.operations.createInput(strategy)
    input.tool = tool
    input.displayName =  op_json["displayName"]
    
    preset_json = op_json["toolPreset"]
    subtypekey =  preset_json.get("subtypekey", "PresetCreate")
    if subtypekey == "PresetCreate":
        preset = tool.presets.add()
        preset.name = preset_json["name"]
        set_parameters(preset, preset_json["parameters"])
    elif subtypekey == "PresetByName":
        presets = input.tool.presets.itemsByName(preset_json["name"])
        if len(presets) != 1:
            raise Exception(f"Expected exactly one preset with name {preset_json['name']}. Got {len(presets)} instead.")
        preset = presets[0]
    else: 
        raise Exception(f"Unexpected preset subtypekey: {subtypekey}")

    tp_occ = sketch_book.occman.get_tp_root_occ()
    set_parameters(input, op_json["parameters"], tp_occ, sketch_book,config)
    input.toolPreset = preset
    op : adsk.cam.OperationBase = setup.operations.add(input)
    
    return op

def create_op(fusion : Fusion, setup : adsk.cam.Setup, op_json, tool : adsk.cam.Tool, facet_id_table, config, sketch_book : SketchBook) -> adsk.cam.OperationBase:
    strategy = op_json["strategy"]
    fusion_paths = FusionFullPath()
    (setup_body, body_occurrence) = fusion_paths.get_setup_body_occurrence(setup)
    op : adsk.cam.OperationBase = prepare_op(setup, op_json, tool, config, sketch_book)
    sel_notes = op_select_geometry(op, op_json, fusion, body_occurrence, facet_id_table, sketch_book, setup_body=setup_body)
    jl_notes = op_json["notes"]
    if (sel_notes is None) and (jl_notes is None):
        pass
    elif (sel_notes is None) and (jl_notes is not None):
        op.notes = jl_notes
    elif (sel_notes is not None) and (jl_notes is None):
        op.notes = sel_notes
    elif (sel_notes is not None) and (jl_notes is not None):
        op.notes = sel_notes + "\n" + jl_notes
    else:
        raise Exception("Unreachable")

    return op

def create_op_from_template(setup : adsk.cam.Setup, op_json) -> adsk.cam.OperationBase:
    assert isinstance(setup, adsk.cam.Setup)
    assert op_json["subtypekey"] == "CAMTemplate"
    nops_before = len(setup.operations)
    input = adsk.cam.CreateFromCAMTemplateInput.create()
    input.camTemplate = adsk.cam.CAMTemplate.createFromXML(op_json["xml"])
    setup.createFromCAMTemplate2(input)
    nops_after = len(setup.operations)
    assert nops_after - nops_before == 1
    op = setup.operations[-1]
    if op_json["notes"] is not None:
        op.notes = op_json["notes"]
    return op

def square_distance(p1, p2):
    ret = 0.0
    for (x1_i, x2_i) in zip(p1, p2):
        ret += (x1_i - x2_i)**2
    return ret

def find_containing_cylinders(centroid1, face_native_face_pairs):
    ret = []
    atol2 = 1e-8 # cm^2
    for (face, native_face) in face_native_face_pairs:
        x,y,z = get_xyz(native_face.centroid)
        if square_distance((x,y,z), centroid1) < atol2:
            ret.append(face)

    return ret
    
def try_op_select_hole(*,op, body_occurrence, single_hole_selections, selection_param_name, selection_param_value_type, sketch_book, setup_body=None):
    # we want to select body faces, but we want to use native coordinates
    # SetupsDistinctOccurrencesOfSamePart.json and
    # NestedComponents.json are good examples
    fusion_paths = FusionFullPath()
    # Use setup_body if provided, otherwise fall back to deriving from occurrence
    body = setup_body if setup_body is not None else fusion_paths.get_body(body_occurrence)

    native_body = fusion_paths.get_native_body(body)
    cylinders = [(face,native_face) for (face,native_face) in zip(body.faces, native_body.faces) if face.geometry.classType() == 'adsk::core::Cylinder']
    faces = []
    T_world_from_sketch = sketch_book.occman.get_T_world_from_sketch()

    for shs in single_hole_selections:
        x,y,z = shs["centroid"] # in sketch coords
        centroid_sketch = adsk.core.Point3D.create(x,y,z)
        c = transform_point(T_world_from_sketch, centroid_sketch)
        centroid1 = (c.x, c.y, c.z)
        # c = geom.transform_point(T_sketch_from_world, centroid_sketch)
        # centroid2 = (c.x, c.y, c.z)

        gs = find_containing_cylinders(centroid1, cylinders)
        if len(gs) != 1:
            return f"Expected a single cylinder to contain {shs}, got {len(gs)} cylinders instead."
        #faces.extend([cyl.createForAssemblyContext(body_occurrence) for cyl in gs])
        faces.extend(gs)
    
    param = get_parameter(op, selection_param_name)
    if selection_param_value_type == "CadObjectParameterValue":
        assert isinstance(param.value, adsk.cam.CadObjectParameterValue)
        param.value.value = faces
    elif selection_param_value_type == "CadContours2dParameterValue":
        try:
            select_faces_using_CadContours2dParameterValue(param.value, faces)
        except Exception as e:
            return "Failed to select faces using CadContours2dParameterValue: " + str(e)
    else:
        raise Exception(f"Unexpected selection_param_value_type: {selection_param_value_type}")
    set_parameter(op, "holeMode", "selection-faces")
    return None

def try_op_select_extrude_feature_hole(op, selection_json, sketch_book : SketchBook):
    faces = []
    for (i,_) in enumerate(selection_json["sketch_ids"]):
        body = sketch_book.get_extrude_surface(selection_json, i)
        cylinders = [face for face in body.faces if face.geometry.classType() == 'adsk::core::Cylinder']
        faces.extend(cylinders)

    selection_param_name = selection_json["selection_param_name"]
    selection_param_value_type = selection_json["selection_param_value_type"]
    param = get_parameter(op, selection_param_name)
    if selection_param_value_type == "CadObjectParameterValue":
        assert isinstance(param.value, adsk.cam.CadObjectParameterValue)
        param.value.value = faces
    else:
        raise Exception(f"Unexpected selection_param_value_type: {selection_param_value_type}")
    set_parameter(op, "holeMode", "selection-faces")
    return None

def op_select_geometry(op, op_json, fusion, body_occurrence, facet_id_table, sketch_book : SketchBook, setup_body=None):
    assert isinstance(body_occurrence, adsk.fusion.Occurrence)
    selection_json = op_json["selection"]
    subtypekey = selection_json["subtypekey"]
    notes = None
    if subtypekey == "NoSelection":
        return notes
    if subtypekey == "HoleSelection":
        res = try_op_select_hole(
            op=op,
            body_occurrence=body_occurrence,
            single_hole_selections=selection_json["single_hole_selections"],
            selection_param_name=op_json["selection_param_name"],
            selection_param_value_type=op_json["selection_param_value_type"],
            sketch_book=sketch_book,
            setup_body=setup_body,
            )
        if res is None:
            # selection worked
            return notes
        else:
            assert isinstance(res, str)
            selection_json = selection_json["fallback"]
            subtypekey = selection_json["subtypekey"]
            notes = res

    if subtypekey == "ExtrudeFeatureHoleSelection":
        res = try_op_select_extrude_feature_hole(op, selection_json, sketch_book)
        if res is None:
            # selection worked
            return notes
        else:
            res = try_op_select_hole(
                op=op,
                body_occurrence=body_occurrence,
                single_hole_selections=selection_json["fallback"],
                selection_param_name=op_json["selection_param_name"],
                selection_param_value_type=op_json["selection_param_value_type"],
                sketch_book=sketch_book,
                setup_body=setup_body,
            )
            return notes

    key = "selection_param_name"
    sel_param_name = selection_json.get(key, op_json.get(key, None))
    key = "selection_param_value_type"
    s = selection_json.get(key, op_json.get(key, None))
    if selection_json["subtypekey"] == "NoSelection":
        return notes
    elif s == "CadObjectParameterValue":
        sel_param_type = adsk.cam.CadObjectParameterValue
    elif s == "CadContours2dParameterValue":
        sel_param_type = adsk.cam.CadContours2dParameterValue
    elif s == "CadMachineAvoidGroupsParameterValue":
        sel_param_type = adsk.cam.CadMachineAvoidGroupsParameterValue
    else:
        raise Exception(f"Unexpected selection_param_type {s}")

    strategy = op.strategy

    if sel_param_type == adsk.cam.CadContours2dParameterValue:
        param = get_parameter(op, sel_param_name).value
        assert isinstance(param, sel_param_type)
        try:
            if selection_json["subtypekey"] == "MultiSelection":
                # It is important not to instantiate curveSelection
                # In case of MultiSelection.
                # Doing so leads to heisenbugs
                for sel in selection_json["selections"]:
                    name_key = "selection_param_name"
                    sel_param_name_c = sel.get(name_key, op_json.get(name_key, None))
                    contourParam = get_parameter(op, sel_param_name_c).value
                    select_using_CadContours2dParameterValue(fusion, contourParam, body_occurrence, sel, facet_id_table, sketch_book, setup_body=setup_body)
            else:
                select_using_CadContours2dParameterValue(fusion, param, body_occurrence, selection_json, facet_id_table, sketch_book, setup_body=setup_body)
        except RuntimeError as err:
            curveSelections = param.getCurveSelections()
            curveSelections.clear()
            errmsg = traceback.format_exc()
            feature = {op_json.get("feature_string", "Unknown feature")}
            notes = f"""
            Geometry selection failed:
            feature = {feature}
            error   = {errmsg}
            """
    elif sel_param_type == adsk.cam.CadObjectParameterValue:
        param = get_parameter(op, sel_param_name).value
        assert isinstance(param, sel_param_type)
        select_using_CadObjectParameterValue(param, body_occurrence, selection_json, facet_id_table, sketch_book=sketch_book)
    elif sel_param_type == adsk.cam.CadMachineAvoidGroupsParameterValue:
        param = get_parameter(op, sel_param_name).value
        assert isinstance(param, sel_param_type)
        success = False
        try:
            success = select_using_CadMachineAvoidGroupsParameterValue(fusion, param, body_occurrence,selection_json, facet_id_table, sketch_book=sketch_book)
        except Exception as err:
            errmsg = traceback.format_exc()
            log(f"Touch Avoid selection failed: {errmsg}")
        if not success:
            sel = selection_json.get("fallback", None)
            log(f"Touch Avoid selection not successful")
            if sel is None:
                return notes
            op_json = copy(op_json)
            op_json["selection"] = sel
            return op_select_geometry(op, op_json, fusion, body_occurrence, facet_id_table, sketch_book, setup_body=setup_body)
    else:
        raise Exception(f"TODO: selection for {strategy = } {subtypekey = } {sel_param_type = }")

    op_parameters = selection_json.get("op_parameters", None)
    if op_parameters is not None:
        set_parameters(op, op_parameters)

    return notes

def get_body_faces(body: adsk.fusion.BRepBody):
    ''' Adds all the faces in the selected body to a list '''
    allFaces: list[adsk.fusion.BRepFace] = []                               
    for face in body.faces:
         allFaces.append(face)                 
    return allFaces

def select_using_CadMachineAvoidGroupsParameterValue(fusion,param, body_occurrence, selection_json, facet_id_table, sketch_book : SketchBook):
    assert isinstance(body_occurrence, adsk.fusion.Occurrence)
    assert isinstance(param, adsk.cam.CadMachineAvoidGroupsParameterValue)
    subtypekey = selection_json["subtypekey"]
    if subtypekey == "PatchFeatureTouchSelection":
        groups : adsk.cam.MachineAvoidGroups = param.getMachineAvoidGroups()
        ngroups_before = len(groups)
        # groups.clear() # remove default model selecion
        # deselect the model

        # groups.createNewMachineAvoidDirectSelectionGroup()
        ngroups_added = 0
        for sketch_id in selection_json["sketch_ids"]:
            group : adsk.cam.MachineAvoidDirectSelection = groups.createNewMachineAvoidDirectSelectionGroup()
            group.machineMode = adsk.cam.MachiningMode.Machine_MachiningMode
            body = sketch_book.get_brep_surface(sketch_id)
            sel = [body]
            group.inputGeometry = sel
            ngroups_added += 1
            param.applyMachineAvoidGroups(groups)
            ngroups_after = len(groups)
            # sometimes fusion silently just does not make the selection
            # https://github.com/toolpath/ToolpathPackages/issues/5598
            if ngroups_before + ngroups_added == ngroups_after:
                pass
                # fine
            else:
                return False

        modelGroup = groups.defaultGroup(adsk.cam.DefaultGroupType.Model_GroupType)
        modelGroup.machineMode = adsk.cam.MachiningMode.Avoid_MachiningMode
        param.applyMachineAvoidGroups(groups)
    elif subtypekey == "FaceSelection":
        groups : adsk.cam.MachineAvoidGroups = param.getMachineAvoidGroups()
        group : adsk.cam.MachineAvoidDirectSelection = groups.createNewMachineAvoidDirectSelectionGroup()
        fusion_paths = FusionFullPath()
        body = fusion_paths.get_body(body_occurrence)
        brep_faces = get_brep_faces(facet_id_table, body, body_occurrence,selection_json)
        group.inputGeometry = brep_faces
        param.applyMachineAvoidGroups(groups)
    elif subtypekey == "PatchFeatureTouchStepSelection":
        groups : adsk.cam.MachineAvoidGroups = param.getMachineAvoidGroups()
        ngroups_before = len(groups)
        ngroups_added = 0
        step_data = selection_json["step_file_content"]

        fusion.activateDesign()
        design = fusion.getDesign()
        touch_avoid_comp = sketch_book.get_touch_avoid_component()

        # import the avoid surfaces
        import_step_file_to_component(step_data, touch_avoid_comp, fusion=fusion)
        touch_avoid_occurrence = touch_avoid_comp.occurrences.item(touch_avoid_comp.occurrences.count - 1)
        touch_avoid_occurrence.isGroundToParent = True
        # ungroup the imported surfaces
        timelineGroups = design.timeline.timelineGroups
        import_group = timelineGroups.item(timelineGroups.count-1)
        import_group.deleteMe(False)

        fusion_paths = FusionFullPath()
        body = fusion_paths.get_body(touch_avoid_occurrence)
        if body is None:
            faces = []
            for occurrence in touch_avoid_occurrence.component.occurrences:
                body = fusion_paths.get_body(occurrence)
                faces.extend(get_body_faces(body))
        else:
            faces = get_body_faces(body)
        fusion.activateCAM()
        group : adsk.cam.MachineAvoidDirectSelection = groups.createNewMachineAvoidDirectSelectionGroup()
        group.machineMode = adsk.cam.MachiningMode.Avoid_MachiningMode
        
        group.inputGeometry = faces 
        ngroups_added += 1
        param.applyMachineAvoidGroups(groups)
        ngroups_after = len(groups)
        # sometimes fusion silently just does not make the selection
        # https://github.com/toolpath/ToolpathPackages/issues/5598
        if ngroups_before + ngroups_added == ngroups_after:
            pass
            # fine
        else:
            return False

        modelGroup = groups.defaultGroup(adsk.cam.DefaultGroupType.Model_GroupType)
        modelGroup.machineMode = adsk.cam.MachiningMode.Machine_MachiningMode
        param.applyMachineAvoidGroups(groups)
    else:
        raise Exception(f"Unexpected selection subtypekey: {subtypekey}")
    return True

def get_tool_guid(tool):
    # TODO is there a better way???
    return json.loads(tool.toJson())["guid"]

def _get_geometry_tracking_data(self):
    body = self.get_body()
    faceData = []
    faces = body.faces
    for face in body.faces:
        faceData.append({"entityToken":face.entityToken, "area":face.area})
    return faceData

def calc_tool_by_id_dict(toollibs : adsk.cam.ToolLibrary) -> dict:
    tool_by_id = {}
    for (i,toollib) in enumerate(toollibs):
        for tool in toollib:
            id = (get_tool_guid(tool), i)
            if id in tool_by_id:
                raise Exception(f"Duplicate tool id: {id}")
            tool_by_id[id] = tool
    return tool_by_id
    

def maybe_setop_with_operationId(setops, operationId):
    for setop in setops:
        subtypekey = setop["subtypekey"]
        if subtypekey == "SetopUserSpecified":
            if setop["operationId"] == operationId:
                return setop
        else:
            raise Exception(f"Unreachable subtypekey: {subtypekey}")
    return None

def has_setop_with_operationId(setops, operationId):
    return maybe_setop_with_operationId(setops, operationId) is not None

def get_setop_with_operationId(setops, operationId):
    res = maybe_setop_with_operationId(setops, operationId)
    if res is None:
        raise Exception(f"Could not find setop with operationId {operationId}")
    return res

def jsonify_toollib(toollib : adsk.cam.ToolLibrary):
    assert isinstance(toollib, adsk.cam.ToolLibrary)
    ret = json.loads(toollib.toJson())
    return ret

def unjsonify_toollib(toollib_json : dict) -> adsk.cam.ToolLibrary:
    assert isinstance(toollib_json, dict)
    toollib = adsk.cam.ToolLibrary.createFromJson(json.dumps(toollib_json, indent = 2))
    return toollib

def models_contain_root_component(fusion, setup) -> bool:
    pass
class SetopMaterializer:
    """
    This class populates a document with setups and operations 
    """
    def __init__(self, *,
            setops : list,
            parts,
            stock,
            workholding,
            fixture_parent_occ=None,
            joints,
            progressDialog,
            needs_cancel,
            toollibs,
            fusion,
            config,
            reuse_existing_setups,
            fusion_paths,
            part_offset=None,
            stock_entityToken=None,
            support_window_step_content=None,
            support_pedestal_step_content=None,
            support_part_transform=None,
    ):
        self.setops = setops
        assert(parts is None or len(parts)==len(setops) or len(parts)==1)
        self.parts = parts
        self.stock = stock
        self.workholding = workholding
        self.fixture_parent_occ = fixture_parent_occ
        self.joints = joints
        self.progressDialog = progressDialog
        self.needs_cancel = needs_cancel
        self.toollibs = toollibs
        self.fusion = fusion
        self.config = config
        self.reuse_existing_setups = reuse_existing_setups
        self.facet_id_table = None
        self.fusion_paths = fusion_paths
        self.part_offset = part_offset or [0.0, 0.0, 0.0]
        self.stock_entityToken = stock_entityToken
        self.support_window_step_content = support_window_step_content
        self.support_pedestal_step_content = support_pedestal_step_content
        self.support_part_transform = support_part_transform
        self.support_container_occ = None

    def execute(self):
        self.progressDialog.message = "Materializing setups and operations."

        design = self.fusion.getDesign()
        first_setup = None
        setups_needing_op_gen = []
        nops = 0

        for (j,setop) in enumerate(self.setops):
            setup, occman = self.make_setup_occman(setop,j)
            if setup is None:
                # this should be unreachable
                continue
            if first_setup is None:
                first_setup = setup

            # Import fixtures for this setup (only first setup gets fixtures)
            fixture_data = setop.get("fixture_params")
            if fixture_data is not None:
                workholding_occ = occman.import_fixtures(fixture_data, design, part_offset=self.part_offset)
                if workholding_occ is not None:
                    # Set workholding as the setup's fixture
                    setup.parameters.itemByName('job_fixture').value.value = [workholding_occ]

            # Import support geometry once on first setup that has it
            if self.support_container_occ is None:
                self.support_container_occ = occman.import_support_geometry(
                    self.support_window_step_content,
                    self.support_pedestal_step_content,
                    self.support_part_transform or adsk.core.Matrix3D.create(),
                )

            ops_json = setop["operations"]
            if len(ops_json) == 0:
                continue
            setups_needing_op_gen.append(setup)
            nops += len(ops_json)

            self.progressDialog.progressValue = 0
            self.progressDialog.maximumValue = len(ops_json)
            self.progressDialog.message = f'Create operations for setup: Current Operation: %v, Total operations: %m'
            if self.needs_cancel(): return "cancelled"

            operations = adsk.core.ObjectCollection.create()
            tool_by_id = calc_tool_by_id_dict(self.toollibs)

            sketch_book = SketchBook(design, setop["sketch_book"], occman=occman)
            for (i,op_json) in enumerate(ops_json):
                if self.needs_cancel(): return "cancelled"
                subtypekey = op_json["subtypekey"]
                if subtypekey == "CAMTemplate":
                    create_op_from_template(setup, op_json)
                elif subtypekey == "FusionOp":
                    id = tuple(op_json["ftool_id"])
                    tool = tool_by_id[id]
                    op = create_op(self.fusion, setup, op_json, tool, self.facet_id_table,self.config,sketch_book=sketch_book)
                    operations.add(op)
                    if self.support_container_occ is not None and op_json.get("strategy") == "adaptive":
                        self._apply_support_model(op)
                    self.progressDialog.progressValue = i+1
                else:
                    raise Exception(f"TODO: {subtypekey =}")
            if self.fusion.isParametricDesign():
                self.group_sketches_on_timeline(sketch_book, occman, setop)

        if self.needs_cancel(): return "cancelled"

        self.generate_toolpaths_if_needed(setups_needing_op_gen)
        self.progressDialog.hide()
        # active first_setup
        if first_setup is not None:
            first_setup.activate()
        self.fusion.activateCAM()

    def _apply_support_model(self, op):
        """Add support geometry occurrence as additional model geometry on the operation."""
        from ...lib.general_utils import log
        try:
            override_param = op.parameters.itemByName('overrideModel')
            if override_param is None:
                return
            override_param.expression = 'true'
            model_param = op.parameters.itemByName('model')
            model_val = model_param.value
            current = list(model_val.value)
            current.append(self.support_container_occ)
            model_val.value = current
            # Keep setup model included
            include_param = op.parameters.itemByName('includeSetupModel')
            include_param.expression = 'true'
        except Exception as e:
            log(f"Warning: could not apply support model override: {e}", force_console=True)

    def is_occ_above_root_component(self, occ) -> bool:
        rootComp = self.fusion.getDesign().rootComponent
        if occ.component == rootComp:
            return True
        for child in occ.childOccurrences:
            if self.is_occ_above_root_component(child):
                return True
        return False

    def has_model_above_root_component(self, setup) -> bool:
        for model in setup.models:
            if isinstance(model, adsk.fusion.Component):
                for occ in model.occurrences:
                    if self.is_occ_above_root_component(occ):
                        return True
            elif isinstance(model, adsk.fusion.BRepBody):
                pass
            elif isinstance(model, adsk.fusion.Occurrence):
                if self.is_occ_above_root_component(model):
                    return True
            else:
                raise Exception(f"TODO: {type(model)}")
        return False

    def make_setup_occman(self, setop, i):
        subtypekey = setop["subtypekey"]
        name = setop["name"]
        if self.reuse_existing_setups:
            operationId = setop.get("operationId", None)
            if not operationId is None:
                setup = get_setup(self.fusion, operationId)
                if self.config["on_import_wipe_existing_ops_from_setup"]:
                    delete_operations(setup)
                (setup_body, part_occ) = self.fusion_paths.get_setup_body_occurrence(setup)
                occman = TPOccurrenceManager(fusion=self.fusion, part_occ=part_occ,name=name,joints = self.joints, is_first_setup=(i == 0))
                # we might need to reselect
                if self.has_model_above_root_component(setup):
                    body = setup_body
                    models = adsk.core.ObjectCollection.create()
                    models.add(body)
                    setup.models = models
                return (setup, occman)
                
        if subtypekey == "SetopUserSpecified":
            # legacy programs only
            setup = next(s.obj for s in self.setips.setips if s.get_operationId() == setop["operationId"])
            (_setup_body, part_occ) = self.fusion_paths.get_setup_body_occurrence(setup)
            occman = TPOccurrenceManager(fusion=self.fusion, part_occ=part_occ, name=name, joints=self.joints, is_first_setup=(i == 0))
        elif subtypekey == "SetopAuto":           
            body_occurrence = self.get_parts_occurrence(i)
            body = self.get_parts_body(i)
            n_bodies = self.get_n_parts_bodies(i)
            fixtures = self.get_setup_fixture(i)
            occman = TPOccurrenceManager(fusion=self.fusion, part_occ=body_occurrence, name=name, joints=self.joints, is_first_setup=(i == 0)) 
            setup =  create_setup(self.fusion, setop, body=body,n_bodies=n_bodies, stock=self.stock, fixtures = fixtures, facet_id_table=self.facet_id_table, occman=occman, stock_entityToken=self.stock_entityToken)
        else:
            raise Exception(f"Unexpected subtypekey: {subtypekey}")

        assert isinstance(setup, adsk.cam.Setup)
        return (setup, occman)
    
    def get_parts_occurrence(self,i):
        if len(self.parts) == 1:
            return self.parts[0].occurrence
            
        return self.parts[i].occurrence
    
    def get_parts_body(self,i):
        if len(self.parts) == 1:
            return self.parts[0].part_body
            
        return self.parts[i].part_body
    
    def get_n_parts_bodies(self,i):
        if len(self.parts) == 1:
            return self.parts[0].get_number_of_bodies()
        
        return self.parts[i].get_number_of_bodies()

    def get_setup_fixture(self,i):
            if i==0:
                # Return workholding if available, otherwise return fixture_parent_occ wrapped
                # in a SimpleNamespace so it has the .occurrence property expected by create_setup
                # Currently workholding is ONLY supported in setup 1 - this should be changed when we support multiple setups
                if self.workholding is not None:
                    return self.workholding
                elif self.fixture_parent_occ is not None:
                    return SimpleNamespace(occurrence=self.fixture_parent_occ)
            return None
    
    def generate_toolpaths_if_needed(self, setups : List[adsk.cam.Setup]):
        if self.config["generate_toolpaths"]:
            log("Start launching toolpaths")
            cam = self.fusion.getCAM()
            for setup in setups:
                cam.generateToolpath(setup)
        else:
            log("Skip launching toolpaths")

    def group_sketches_on_timeline(self, sketch_book : SketchBook, occman : TPOccurrenceManager, setop):
        design = self.fusion.getDesign()
        timelineGroups = design.timeline.timelineGroups
        # the root occurance from the occurrence manager will be the start index
        first_timeline_object = occman.get_tp_root_occ().timelineObject
        start_index = first_timeline_object.index

        # then we want the index of the last sketch created
        last_sketch_created = sketch_book.last_created
        if last_sketch_created is not None:
            last_sketch_index = last_sketch_created.timelineObject.index

            # then create a timeline group from the start index to the last sketch index
            try: 
                timeline_group = timelineGroups.add(start_index, last_sketch_index)
                timeline_group.name = f"TP Geometry: {setop['name']}"
            except Exception:
                # The grouping can fail in cases where there is already a group in place inbetween the start and last sketch indices.
                # The exception lets us keep going, even if we can't tidy up the timeline for some reason.
                pass
