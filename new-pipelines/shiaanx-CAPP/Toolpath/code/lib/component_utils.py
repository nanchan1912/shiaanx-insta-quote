import adsk.core
import traceback
from .fusion_utils import add_component, insert_component_by_name
from .general_utils import load_config
from .workholding_utils import get_active_project,get_workholding_folder, get_workholding_folder_name
import copy

config = load_config()

TP_ROOT_COMPONENT_NAME = "Toolpath geometry: Do not edit"

def is_tp_component_name(name):
    # TODO add more toolpath components like for stock and sketches
    return name.startswith(TP_ROOT_COMPONENT_NAME)

def find_closest_vertex(body, target_point):
    """Finds the closest BRepVertex to the given Point3D."""
    min_distance = float("inf")
    closest_vertex = None

    for vertex in body.vertices:
        distance = vertex.geometry.distanceTo(target_point)
        if distance < min_distance:
            min_distance = distance
            closest_vertex = vertex

    return closest_vertex

def find_first_body(component):
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
        body = find_first_body(subComponent.component)
        if body:
            return body  # Return as soon as a body is found

    return None  # No body found in the entire hierarchy

def create_box_component(comp_name,body_name,rootComp):
    # Create the first component (for Box 1)
    comp = add_component(rootComp,comp_name)
    compComponent = comp.component

    # Create a sketch for Box 1 in Component 1
    sketches = compComponent.sketches
    xyPlane = compComponent.xYConstructionPlane
    box = sketches.add(xyPlane)
    box.name = body_name

    # Draw a rectangle for Box 1
    box.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(5, 5, 0))

    # Extrude the rectangle to create the box
    extrudes = compComponent.features.extrudeFeatures
    profile = box.profiles.item(0)
    extrude = extrudes.addSimple(profile, adsk.core.ValueInput.createByString('5 cm'), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    box_body = extrude.bodies.item(0)
    joint_vertex = box_body.vertices.item(0)
    joint_origins = compComponent.jointOrigins
    joint_origin_input = joint_origins.createInput(
            adsk.fusion.JointGeometry.createByPoint(joint_vertex)
        )
    joint_origin = joint_origins.add(joint_origin_input)
    joint_origin.name = comp_name+body_name
    return comp
    
def mre_func():
    app = adsk.core.Application.get()
    ui = app.userInterface
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = app.activeProduct
    configuration = design.createConfiguredDesign()
     
    # Create the first box
    rootComp = design.rootComponent

    occurrence1 = create_box_component("comp1","box1",rootComp)
    occurrence2 = create_box_component("comp2","box2",rootComp)

    joint_origins1 = occurrence1.component.jointOrigins
    joint_origins2 = occurrence2.component.jointOrigins
        
    # Select the first joint origin from each component
    joint_origin_1 = joint_origins1.item(0)
    joint_origin_2 = joint_origins2.item(0)
    joint_origin_1 = joint_origin_1.createForAssemblyContext(occurrence1)
    joint_origin_2 = joint_origin_2.createForAssemblyContext(occurrence2)
        
    # Create the Rigid Joint
    joints = rootComp.joints
    joint_input = joints.createInput(joint_origin_1, joint_origin_2)
    joint_input.setAsRigidJointMotion()

    # Add the joint
    joint1 = joints.add(joint_input)

    ui.messageBox('Created two boxes and a joint between them!')

    # now setup a basic configuration
    rows = configuration.rows
    rows.item(0).name = "On"
    rows.add("off")
    
    columns = configuration.columns
    suppress_col = columns.addSuppressColumn(joint1)

def select_face_by_direction(target_comp,origin =[0.0,0.0,0.0],direction=[0.0,1.0,0.0]):
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        # Step 1: Access the first body in the target component (Assuming a body exists)
        bodies = target_comp.bRepBodies
        if bodies.count == 0:
            ui.messageBox("No bodies found in the component.")
            return None

        body = bodies.item(0)  # Assuming the first body is the extruded body

        # Step 2: Access the faces of the body
        faces = body.faces
        if faces.count == 0:
            ui.messageBox("No faces found on the body.")
            return None

        # Step 3: Iterate through the faces and compare centroid positions
        origin = adsk.core.Point3D.create(origin[0],origin[1],origin[2])

        limit_compare_value = -1000000
        selected_face = None
        for face in faces:
            # Get the centroid of the face
            face_centroid = face.centroid

            compare_value = get_projected_distance(face_centroid,origin,direction)

            # Compare the centroid to the target point (tolerance of 0.001 units)
            if (compare_value > limit_compare_value):
                selected_face = copy.copy(face)
                limit_compare_value = copy.copy(compare_value)
        
        # Step 4: Found the face matching the target centroid
        face_centroid = selected_face.centroid
        
        return selected_face


    except Exception as e:
        ui.messageBox(f"Error:\n{traceback.format_exc()}")
        return None    

def get_top_center_of_bounding_box(body):
    """Computes the top-center of the bounding box."""
    bbox = body.boundingBox
    center_x = (bbox.minPoint.x + bbox.maxPoint.x) / 2
    center_y = (bbox.minPoint.y + bbox.maxPoint.y) / 2
    top_z = bbox.maxPoint.z  # Highest point in Z

    return adsk.core.Point3D.create(center_x, center_y, top_z)

def compute_distance_vector(start_point, end_point):
    """Returns the vector from start_point to end_point as a Vector3D object."""
    return start_point.vectorTo(end_point)

def get_projected_distance(point,center,direction):
    point_vector = compute_distance_vector(center,point)
    return point_vector.dotProduct(direction)

def rotate_vector_by_matrix(vector, matrix):
    """
    Rotates a Vector3D using a given Matrix3D transformation.
    
    :param vector: The Vector3D to be rotated
    :param matrix: The Matrix3D transformation matrix
    :return: A new rotated Vector3D
    """
    vector_copy = vector.copy()  # Avoid modifying the original vector
    vector_copy.transformBy(matrix)  # Apply transformation
    return vector_copy

class Workholding:
    def __init__(self,design,fusion,name = "Op1 Fixture", byXref = True,viseStyle="Self Centering Vise"):
        self.name = name
        self.design = design
        self.fusion = fusion
        self.viseStyle = viseStyle
        app = adsk.core.Application.get()
        ui = app.userInterface

        if viseStyle == "Self Centering Vise":
            self.vise_file = config["self_centering_vise_file_name"]
        elif viseStyle == "Fixed Jaw Vise":
            self.vise_file = config["fixed_vise_file_name"]
        else:
            self.vise_file = None
        self.fixture_plate_file = config["clamping_file_name"]

        self.occurrence = add_component(design.rootComponent,name)
        self.component = self.occurrence.component

        active_project = get_active_project()
        workholding_folder = get_workholding_folder(active_project)
        if workholding_folder:
            fixture_plate_occurrence = insert_component_by_name(self.component, workholding_folder, self.fixture_plate_file, byXref)
            if fixture_plate_occurrence is None:
                ui.messageBox("Clamping file not found")
                self.fixture_plate_occurrence = None
                return
            self.fixture_plate_occurrence = fixture_plate_occurrence.createForAssemblyContext(self.occurrence)

            vise_occurrence = insert_component_by_name(self.component, workholding_folder, self.vise_file, byXref)
            if vise_occurrence is None:
                ui.messageBox("Vise file not found")
                self.vise_occurrence = None
                return
            self.vise_occurrence = vise_occurrence.createForAssemblyContext(self.occurrence)
        else:
            ui.messageBox(f"Workholding folder: '{get_workholding_folder_name()}' not found")
            self.fixture_plate_occurrence = None
            self.vise_occurrence = None
            return
        self.vise_joint_origin_counter = 0
        self.vise_joint_origin_map = {}
        self.fixture_plate_joint_origin_counter = 0
        self.fixture_plate_joint_origin_map = {}
        self.joints = []

    def get_joint_target(self,target_name,sub_component_type="vise"):
        target_occurrence = None
        if sub_component_type == "vise":
            target_occurrence = self.vise_occurrence
        elif sub_component_type == "fixture_plate":
            target_occurrence = self.fixture_plate_occurrence

        for occurrence in target_occurrence.component.occurrences:
            if occurrence.component.name == target_name:
                return_occurrence = occurrence.createForAssemblyContext(target_occurrence)
                break

        return return_occurrence
    
class MachiningFeature:
    def __init__(self,design,fusion,setops = None, name= "Machining Feature"):
        self.name = name
        self.design = design
        self.fusion = fusion
        self.occurrence = add_component(design.rootComponent,name)
        self.component = self.occurrence.component


class Stock:
    def __init__(self, design, fusion, setops, name="Stock", part_transform=None, deferJointOrigins=False):
        self.name = name
        self.design = design
        self.fusion = fusion
        self.has_joints = False
        self.stock_x_name = "StockX"
        self.stock_y_name = "StockY"
        self.stock_z_name = "StockZ"
        self.stock_dx_name = "StockXOffset"
        self.stock_dy_name = "StockYOffset"
        self.stock_dz_name = "StockTopOversize"
        self.has_offset_params = False
        self.part_transform = part_transform
        self._deferJointOrigins = deferJointOrigins
        self._job_stock = None  # Store for deferred joint origin creation

        if setops is not None:
            first_setop = setops[0]
            job_stock = first_setop.get("job_stock", None)
            self._job_stock = job_stock
            subtypekey = job_stock["subtypekey"]
            if subtypekey != "JobStockCreateModel":
                return
        else:
            ui = fusion.getUI()
            if ui:
                ui.messageBox("No stock defining setup available")
            return

        part_component = design.rootComponent

        self.occurrence = self.find_stock_occurrence_by_name(name, part_component)

        if self.occurrence is None:
            self.occurrence = add_component(part_component, name, isGroundToParent=False)
        self.component = self.occurrence.component
        self.component.opacity = 0.5
        self.joint_index_map = {}
        self.transform = None

        if setops is not None:
            if "shape" in job_stock.keys():
                shape = job_stock["shape"]
                if shape["name"] == 'box':
                    self.create_parametric_solid_box(design, job_stock)
                elif shape["name"] == 'cylinder':
                    self.create_parametric_solid_cylinder(design, job_stock)
            else:
                self.create_parametric_solid_box(design, job_stock)
        if self.get_body().isLightBulbOn:
            self.get_body().isLightBulbOn = False

    def create_parametric_solid_box(self, design, job_stock):
        self.center = job_stock["center"]
        xdir = job_stock["xdirection"]
        ydir = job_stock["ydirection"]
        self.has_joints = True

        self.xdir = adsk.core.Vector3D.create(xdir[0], xdir[1], xdir[2])
        self.ydir = adsk.core.Vector3D.create(ydir[0], ydir[1], ydir[2])
        self.xdir_neg = adsk.core.Vector3D.create(-xdir[0], -xdir[1], -xdir[2])
        self.ydir_neg = adsk.core.Vector3D.create(-ydir[0], -ydir[1], -ydir[2])
        self.zdir = self.xdir.crossProduct(self.ydir)
        self.zdir_neg = self.ydir.crossProduct(self.xdir)

        self.create_extruded_box_with_fusion_parameters_and_coincident_constraint(design, self.component, job_stock)

        # Create joint origins now unless deferred
        if not self._deferJointOrigins:
            self._create_stock_joint_origins()

    def create_joint_origins(self):
        """Create joint origins for this stock. Can be called later if deferJointOrigins=True."""
        if self.has_joints and len(self.joint_index_map) == 0:
            self._create_stock_joint_origins()

    def _create_stock_joint_origins(self):
        """Internal method to create all stock joint origins."""
        self.stock_bottom = "Stock Bottom"
        joint_name = self.stock_bottom
        self.create_joint_origin_at_face_centroid(direction=self.zdir_neg, joint_name=joint_name)
        self.joint_index_map[joint_name] = 0

        self.stock_back = "Stock Back"
        joint_name = self.stock_back
        self.create_joint_origin_at_face_centroid(direction=self.ydir, joint_name=joint_name)
        self.joint_index_map[joint_name] = 1

        self.stock_part_ref = "Stock Corner"
        joint_name = self.stock_part_ref
        self.joint_vertex = self.create_joint_origin_at_vertex(joint_name)
        self.joint_index_map[joint_name] = 2

        self.stock_front = "Stock Front"
        joint_name = self.stock_front
        self.create_joint_origin_at_face_centroid(direction=self.ydir_neg, joint_name=joint_name)
        self.joint_index_map[joint_name] = 3

        self.stock_left = "Stock Left"
        joint_name = self.stock_left
        self.create_joint_origin_at_face_centroid(direction=self.xdir_neg, joint_name=joint_name)
        self.joint_index_map[joint_name] = 4         
        

    def create_parametric_solid_cylinder(self,design,job_stock):
        raise NotImplementedError
        pass

    def find_stock_occurrence_by_name(self,name,component):
        occurrences = component.occurrences
        for i in range(0, occurrences.count):
            occ = occurrences.item(i)
            if name+":" in occ.name:
                return occ

        return None


    def get_body(self):
        # Step 1: Ensure the component has a body
        bodies = self.component.bRepBodies
        if bodies.count == 0:
            return None

        return bodies.item(0)  # Assume we use the first body

    def create_extruded_box_with_fusion_parameters_and_coincident_constraint(self,design,component, job_stock):

        app = adsk.core.Application.get()
        ui = app.userInterface

        if not self.fusion.isParametricDesign():
            result = ui.messageBox(
                'In order to create a parametric stock body, Fusion needs to be in parametric design mode. Would you like to continue in parametric mode?\n', 
                'Confirm Action',
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType
            )

            if result == adsk.core.DialogResults.DialogYes:
                self.design.designType = adsk.fusion.DesignTypes.ParametricDesignType
                print('Proceeding with parametric stock.')
            else:
                raise(Exception("Could not create stock in non-parametric mode"))
                
                
                                                                
        #length = 1,width = 1, height = 2):
        center = job_stock["center"]#could replace with self. vars
        xdir=job_stock["xdirection"]
        ydir=job_stock["ydirection"]
        
        length,width, height = size = job_stock["size"] #in cm
        self.compute_transform(length,width,height)
        
        # Create user parameters for width, height, and depth
        user_parameters = design.userParameters
        unitsMgr = design.unitsManager
        length_default = unitsMgr.formatValue(length, unitsMgr.
        defaultLengthUnits)
        width_default = unitsMgr.formatValue(width, unitsMgr.defaultLengthUnits)
        height_default = unitsMgr.formatValue(height, unitsMgr.defaultLengthUnits)
        # Create parameters for width, height, and depth
        x_name = self.stock_x_name
        length_param = user_parameters.itemByName(x_name)
        if length_param is None:
            length_param = user_parameters.add(x_name, adsk.core.ValueInput.createByString(length_default), unitsMgr.defaultLengthUnits, 'Length of the box')
        y_name =  self.stock_y_name
        width_param = user_parameters.itemByName(y_name)
        if width_param is None:
            width_param = user_parameters.add(y_name, adsk.core.ValueInput.createByString(width_default), unitsMgr.defaultLengthUnits, 'Width of the box')
        z_name = self.stock_z_name
        height_param = user_parameters.itemByName(z_name)
        if height_param is None:
            height_param = user_parameters.add(z_name, adsk.core.ValueInput.createByString(height_default), unitsMgr.defaultLengthUnits, 'Height of the box')
        
        if "shape" in job_stock.keys():
            self.has_offset_params = True
            top_offset = job_stock["shape"]["stock_top_offset"]
            self.ref_top_offset = unitsMgr.formatValue(top_offset, unitsMgr.defaultLengthUnits)
            dx_default = unitsMgr.formatValue(0.0, unitsMgr.defaultLengthUnits)
            dy_default = unitsMgr.formatValue(0.0, unitsMgr.defaultLengthUnits)
            dz_default = unitsMgr.formatValue(top_offset, unitsMgr.defaultLengthUnits)
            dz_name = self.stock_dz_name
            top_oversize_param = user_parameters.itemByName(dz_name)
            if top_oversize_param is None:
                top_oversize_param = user_parameters.add(dz_name, adsk.core.ValueInput.createByString(dz_default), unitsMgr.defaultLengthUnits, 'Amount of stock above the part')
            
            dx_name = self.stock_dx_name
            x_offset_param = user_parameters.itemByName(dx_name)
            if x_offset_param is None:
                x_offset_param = user_parameters.add(dx_name, adsk.core.ValueInput.createByString(dx_default), unitsMgr.defaultLengthUnits, 'x axis shift of the stock relative to the part')

            dy_name = self.stock_dy_name
            y_offset_param = user_parameters.itemByName(dy_name)
            if y_offset_param is None:
                y_offset_param = user_parameters.add(dy_name, adsk.core.ValueInput.createByString(dy_default), unitsMgr.defaultLengthUnits, 'y axis shift of the stock relative to the part')


        length_value = length_param.value
        width_value = width_param.value
        height_value = height_param.value


        if self.get_body() is None:
            # Create a new sketch on the X-Y plane of the component
            rootComp = component
            sketches = rootComp.sketches
            planes = rootComp.constructionPlanes
            xy_plane = rootComp.xYConstructionPlane
            sketch = sketches.add(xy_plane)


            sketchPoints = sketch.sketchPoints
            
            # Draw a rectangle in the sketch (based on parameters)
            lines = sketch.sketchCurves.sketchLines
            rect_corners = [
                adsk.core.Point3D.create(0, 0, 0),  # Origin point (corner of the box)
                adsk.core.Point3D.create(length_value, 0, 0),  # Width
                adsk.core.Point3D.create(length_value, width_value, 0),  # Height
                adsk.core.Point3D.create(0, width_value, 0)  # Opposite corner
            ]  
                
            # Create the rectangle
            lines.addByTwoPoints(rect_corners[0], rect_corners[1])
            lines.addByTwoPoints(rect_corners[1], rect_corners[2])
            lines.addByTwoPoints(rect_corners[2], rect_corners[3])
            lines.addByTwoPoints(rect_corners[3], rect_corners[0])

            # Apply a Coincidence Constraint between the first corner (origin) and the origin point
            sketch.geometricConstraints.addCoincident(lines[0].startSketchPoint, sketch.originPoint)
            sketch.geometricConstraints.addCoincident(lines[0].endSketchPoint, lines[1].startSketchPoint)
            sketch.geometricConstraints.addCoincident(lines[1].endSketchPoint, lines[2].startSketchPoint)
            sketch.geometricConstraints.addCoincident(lines[2].endSketchPoint, lines[3].startSketchPoint)
            sketch.geometricConstraints.addCoincident(lines[3].endSketchPoint, lines[0].startSketchPoint)
        
            sketch.geometricConstraints.addHorizontal(lines[0])
            sketch.geometricConstraints.addHorizontal(lines[2])
            sketch.geometricConstraints.addVertical(lines[1])
            sketch.geometricConstraints.addVertical(lines[3])
                

            # Create dimensions for the rectangle, linking to user parameters
            d1 = sketch.sketchDimensions.addDistanceDimension(
                lines[0].startSketchPoint, lines[0].endSketchPoint, adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, adsk.core.Point3D.create(1,1,0)
            )
                        
            # Create dimensions for the rectangle, linking to user parameters
            d2 = sketch.sketchDimensions.addDistanceDimension(
                lines[3].startSketchPoint, lines[3].endSketchPoint, adsk.fusion.DimensionOrientations.AlignedDimensionOrientation, adsk.core.Point3D.create(1,5,0)
            )

            d1.parameter.expression = x_name
            d2.parameter.expression = y_name
            distance = adsk.core.ValueInput.createByString(z_name)


            # Extrude the rectangle to create a 3D box (depth), linking to user parameter
            profile = sketch.profiles.item(0)
            extrudes = rootComp.features.extrudeFeatures
            extrudeInput = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            extrudeInput.startExtent = adsk.fusion.ProfilePlaneStartDefinition.create()

            # Uses no optional arguments.
            extrude = extrudes.addSimple(profile,  distance,  adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            
            # Create a collection of entities for move
            bodies = adsk.core.ObjectCollection.create()
            bodies.add(extrude.bodies.item(0))


            features = rootComp.features
            moveFeats = features.moveFeatures
            moveFeatureInput = moveFeats.createInput2(bodies)
            moveFeatureInput.defineAsFreeMove(self.transform)
            moveFeats.add(moveFeatureInput)

        return
    
    def compute_transform(self,length,width,height):
        
        transform = adsk.core.Matrix3D.create()
        tmpx = adsk.core.Vector3D.create(1.0,0.0,0.0)
        transform.setToRotateTo(tmpx,self.xdir)
        
        tmpy = adsk.core.Vector3D.create(0.0,1.0,0.0)
        tmpy2 = rotate_vector_by_matrix(tmpy, transform)
        transform2 = adsk.core.Matrix3D.create()
        transform2.setToRotateTo(tmpy2,self.ydir)
        transform.transformBy(transform2)

        tmp_center = adsk.core.Vector3D.create(length/2.0, width/2.0,height/2.0)
        tmp_center2 = rotate_vector_by_matrix(tmp_center, transform)
        delta_center = adsk.core.Vector3D.create(self.center[0], self.center[1],self.center[2])
        delta_center.subtract(tmp_center2)

        transform.translation = delta_center
        transform.transformBy(self.part_transform)
        self.transform = transform

        top_center = adsk.core.Vector3D.create(length/2.0, width/2.0,height)
        top_center_pt = adsk.core.Point3D.create(top_center.x,top_center.y,top_center.z)
        top_center_vec = rotate_vector_by_matrix(top_center,transform)
        top_center_vec.add(delta_center)

        self.top_center = adsk.core.Point3D.create(top_center_vec.x,top_center_vec.y,top_center_vec.z)

        return
        
    #TODO this is dead code right now, but I might need it for something scott moyse suggests, so I am going to keep it for no. CAM
    def create_joint_origin_at_sketch_centroid(self, joint_name):
        sketch = self.sketch
        target_comp = self.component

        profile = sketch.profiles.item(0)  # Assume we use the first closed profile

        # Step 3: Create Joint Origin at Centroid
        joint_origins = target_comp.jointOrigins
        joint_geometry = adsk.fusion.JointGeometry.createByProfile(profile, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
        joint_origin_input = joint_origins.createInput(joint_geometry)
        joint_origin = joint_origins.add(joint_origin_input)

        # Step 4: Name the Joint Origin
        joint_origin.name = joint_name
        if joint_origin.isLightBulbOn:
            joint_origin.isLightBulbOn = False
        #ui.messageBox(f"Joint Origin created at sketch centroid!")
        return joint_origin

    def create_joint_origin_at_face_centroid(self, direction, joint_name="tmp"):
        target_comp = self.component
        face = select_face_by_direction(target_comp,self.center,direction)

        # Step 3: Create Joint Origin at Centroid
        joint_origins = target_comp.jointOrigins

        joint_origin = joint_origins.itemByName(joint_name)
        if joint_origin is None:
            joint_geometry = adsk.fusion.JointGeometry.createByPlanarFace(face, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
            joint_origin_input = joint_origins.createInput(joint_geometry)
            joint_origin = joint_origins.add(joint_origin_input)

            # Step 4: Name the Joint Origin
            joint_origin.name = joint_name
        if joint_origin.isLightBulbOn:
            joint_origin.isLightBulbOn = False
        return joint_origin
    
    def create_joint_origin_at_vertex(self,joint_name = "tmp"):
        #We need to rotate the pick point so that it is in the correct orientation for the parametric stock definitions in the joint
        tmp_point = adsk.core.Vector3D.create(1000.0,1000.0, 10000.0)
        tmp_vec = rotate_vector_by_matrix(tmp_point,self.transform)
        target_point = adsk.core.Point3D.create(tmp_vec.x,tmp_vec.y,tmp_vec.z)
        joint_vertex = find_closest_vertex(self.get_body(), target_point)

        # Create a Joint Origin at the target vertex
        joint_origins = self.component.jointOrigins
        joint_origin = joint_origins.itemByName(joint_name)
        if joint_origin is None:
            joint_origin_input = joint_origins.createInput(
                    adsk.fusion.JointGeometry.createByPoint(joint_vertex)
                )
            joint_origin = joint_origins.add(joint_origin_input)
            joint_origin.name = joint_name
        if joint_origin.isLightBulbOn:    
            joint_origin.isLightBulbOn = False
        vertex_point = joint_vertex.geometry
    
        return vertex_point

    
class UserPart:
    def __init__(self, design, fusion, name="Part", part=None, testing=False, enableJoints=True, deferJointOrigins=False):
        self.name = name
        self.design = design
        self.fusion = fusion
        self.jointVertex = None
        self.jointVertex2 = None
        self._canCreateJoints = False
        self.validPartCreated = False
        self.testing = testing
        self.part_body = part

        if isinstance(part, adsk.fusion.BRepBody):
            if part.assemblyContext is None:
                # this indicates that the body is in the root component, so we can't get an occurrence.
                # If we want joints we need to move the body to a component.
                if enableJoints:
                    body_moved = self.check_and_move_body_to_component(design, part)
                    if body_moved:
                        self._canCreateJoints = True
                    else:
                        return
                else:
                    self.occurrence = None
                    self._canCreateJoints = False
                    self.component = None
                    self.validPartCreated = True
            else:
                self.occurrence = part.assemblyContext
                self._canCreateJoints = True
                self.component = self.occurrence.component
                self.validPartCreated = True
        else:
            self.occurrence = part
            self._canCreateJoints = True
            self.component = self.occurrence.component
            self.validPartCreated = True

        if name is not None and self.component is not None:
            self.component.name = name

        # Create joint origins now unless deferred
        if self._canCreateJoints and not deferJointOrigins:
            self.create_joint_origins()

    def create_joint_origins(self):
        """Create joint origins for this part. Can be called later if deferJointOrigins=True."""
        if self._canCreateJoints and self.jointVertex is None:
            self.jointVertex = self.create_joint_origin_at_vertex("Part Vertex")
            self.jointVertex2 = self.create_joint_origin_at_origin("Part Origin")
        
    def check_and_move_body_to_component(self,design,part):
        app = adsk.core.Application.get()
        ui = app.userInterface
        raise(Exception("DEAD CODE"))
        if not self.fusion.isParametricDesign():
            if not self.testing:
                response = ui.messageBox(
                        "Toolpath does not support importing to a document where the part body is in the root component. Also the timeline is not active. Click ok to activate the timeline and move the part body to a component, click cancel to exit.",
                        'Confirmation',
                        adsk.core.MessageBoxButtonTypes.OKCancelButtonType,
                        adsk.core.MessageBoxIconTypes.QuestionIconType
                        )
            else:
                response = None
            if response == adsk.core.DialogResults.DialogOK or self.testing:
                self.activate_timeline()
                self.move_body_to_component(design,part)
                self.validPartCreated = True
                return True #createJoints = True
            else:
                return False
        else:
            if not self.testing:
                response = ui.messageBox(
                            "Toolpath does not support importing to a document where the part body is in the root component. Click ok to move the part body to a component, click cancel to exit.",
                            'Confirmation',
                            adsk.core.MessageBoxButtonTypes.OKCancelButtonType,
                            adsk.core.MessageBoxIconTypes.QuestionIconType
                        )
            else:
                response = None
            if response == adsk.core.DialogResults.DialogOK or self.testing:
                self.move_body_to_component(design,part)
                self.validPartCreated = True
                return True #createJoints = True
            else:
                return False

    def activate_timeline(self):
        self.design.designType = adsk.fusion.DesignTypes.ParametricDesignType

    def move_body_to_component(self,design,part):
        rootComp = design.rootComponent
        # Create a new component for bodyA
        self.occurrence = add_component(rootComp,"Part")
        self.component = self.occurrence.component
        # Cut/paste body from sub component 1 to sub component 2
        cutPasteBody = self.component.features.cutPasteBodies.add(part)
        

    def get_body(self):
        # Step 1: Ensure the component has a body
        body = find_first_body(self.component)

        return body
    
    def get_number_of_bodies(self):
        if self.component is None:
            return None
        bodies = self.component.bRepBodies
        return bodies.count
    
    def get_occurrence(self):
        return self.occurrence

    def create_joint_origin_at_vertex(self,joint_name = "tmp"):
        # TODO us a part bounding box here? to scale the target point location
        target_point = adsk.core.Point3D.create(1000.0,1000.0, 10000.0)
        joint_vertex = find_closest_vertex(self.get_body(), target_point)

        # Create a Joint Origin at the target vertex
        joint_origins = self.component.jointOrigins
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
    
    def create_joint_origin_at_origin(self,joint_name = "tmp"):
        # TODO us a part bounding box here? to scale the target point location
        comp = self.component
        sketches = comp.sketches
        axes = comp.constructionAxes
   
        # Step 1: Create a new sketch on the XY plane
        sketch : adsk.fusion.Sketch = sketches.add(comp.xYConstructionPlane) 
        sketch.name = "Origin Joint"
        joint_vertex = sketch.sketchPoints.add(adsk.core.Point3D.create(0, 0, 0))
        # target_point = adsk.core.Point3D.create(0.0,.0, 0.0)
        # joint_vertex = find_closest_vertex(self.get_body(), target_point)

        # Create a Joint Origin at the target vertex
        joint_origins = self.component.jointOrigins
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
        
    
class Joints:
    def __init__(self,design,fusion,part,stock,workholding=None):
        self.design = design
        self.fusion = fusion
        self.part = part
        self.stock = stock
        self.workholding = workholding

        pass

    def add_workholding_joint(self,fixture_plate_target = None,vise_target = None, name = "tmp"):
        target_fixture_plate_occurrence = self.workholding.get_joint_target(fixture_plate_target,sub_component_type="fixture_plate")
        target_vise_occurrence = self.workholding.get_joint_target(vise_target,sub_component_type="vise")

        return self.create_rigid_joint_between_components(self.workholding.component, target_fixture_plate_occurrence, target_vise_occurrence,name)


    def add_base_joint(self,first_comp_type,first_comp_target,second_comp_type,second_comp_target,name, isPlanar = False,isFlipped = False,offset=None, angle=None,isRevolute=False,create_assembly_context=True):
        idx1 = 0
        idx2 = 0
        if first_comp_type == "vise":
            target_first_comp_occurrence = self.workholding.get_joint_target(first_comp_target,sub_component_type="vise")
        elif first_comp_type == "Stock":
            target_first_comp_occurrence = self.stock.occurrence
            idx1 = self.stock.joint_index_map[first_comp_target]

        if second_comp_type == "Stock":
            target_second_comp_occurrence = self.stock.occurrence
            idx2 = self.stock.joint_index_map[second_comp_target]
        elif second_comp_type == "Part":
            # target_second_comp_occurrence = self.part.occurrence
            target_second_comp_occurrence = self.part.get_occurrence()
        elif second_comp_type == "vise":
            target_second_comp_occurrence = self.workholding.get_joint_target(second_comp_target,sub_component_type="vise")

        return self.create_rigid_joint_between_components(self.design.rootComponent, target_first_comp_occurrence, target_second_comp_occurrence,name,isPlanar,idx1,idx2,isFlipped,offset,angle,isRevolute,first_comp_target=first_comp_target,second_comp_target=second_comp_target,create_assembly_context=create_assembly_context)

    #TODO clean up this function, we don't need the idx2 or idx2 parameters
    def create_rigid_joint_between_components(self,root_comp, comp1, comp2,joint_name, isPlanar = False,idx1 = 0, idx2 = 0,isFlipped = False,offset=None,angle=None,isRevolute=False,first_comp_target=None,second_comp_target=None,create_assembly_context=True):
        try:
            # Get the joint origins in each component (assuming they are named correctly)
            joint_origins1 = comp1.component.jointOrigins
            joint_origins2 = comp2.component.jointOrigins


            if joint_origins1.count == 0 or joint_origins2.count == 0:
                adsk.core.Application.get().userInterface.messageBox("One or both components have no joint origins.")
                return None

            if first_comp_target is None:
                joint_origin_1 = joint_origins1.item(0)
            else:
                joint_origin_1 = joint_origins1.itemByName(first_comp_target)

            if second_comp_target is None:
                joint_origin_2 = joint_origins2.item(0)
            else:
                joint_origin_2 = joint_origins2.itemByName(second_comp_target)

            if joint_origin_1 is None or joint_origin_2 is None:
                # Missing joint origins is expected for existing documents - silently skip
                return None

            if create_assembly_context:
                try:
                    joint_origin_1 = joint_origin_1.createForAssemblyContext(comp1)
                    joint_origin_2 = joint_origin_2.createForAssemblyContext(comp2)
                except Exception:
                    # Assembly context creation can fail for existing documents - silently skip
                    return None

            # Create the Rigid Joint
            joints = root_comp.joints
            new_joint = joints.itemByName(joint_name)
            if new_joint is None:
                joint_input = joints.createInput(joint_origin_1, joint_origin_2)

                if isPlanar:
                    joint_input.setAsPlanarJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
                elif isRevolute:
                    joint_input.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
                else:
                    joint_input.setAsRigidJointMotion()  # Set joint type to Rigid

                joint_input.isFlipped = isFlipped

                try:
                    new_joint = joints.add(joint_input)
                    new_joint.name = joint_name
                except RuntimeError:
                    # Joint creation can fail for existing documents where occurrence paths
                    # are invalid - this is expected and non-fatal
                    return None

            if offset is not None:
                if isPlanar:
                    if new_joint.offset:
                        exp_str = f"{self.stock.stock_x_name}/2"
                        new_joint.offset.expression =exp_str 
                else:
                    if new_joint.offsetX:
                        if self.stock.has_offset_params:
                            exp_str = f"{self.stock.transform.getCell(0,0)}*({self.stock.stock_x_name}/2+{self.stock.stock_dx_name})+{self.stock.transform.getCell(0,1)}*({self.stock.stock_y_name}/2+{self.stock.stock_dy_name})-{self.stock.transform.getCell(0,2)}*({self.stock.ref_top_offset}-{self.stock.stock_dz_name})+{offset.x}"
                        else:
                            exp_str = f"{self.stock.transform.getCell(0,0)}*{self.stock.stock_x_name}/2+{self.stock.transform.getCell(0,1)}*{self.stock.stock_y_name}/2+ {offset.x}"
                        
                        new_joint.offsetX.expression = exp_str + " cm"
                    if new_joint.offsetY:
                        if self.stock.has_offset_params:
                            exp_str = f"{self.stock.transform.getCell(1,0)}*({self.stock.stock_x_name}/2+{self.stock.stock_dx_name})+{self.stock.transform.getCell(1,1)}*({self.stock.stock_y_name}/2+{self.stock.stock_dy_name})-{self.stock.transform.getCell(1,2)}*({self.stock.ref_top_offset}-{self.stock.stock_dz_name})+{offset.y}"
                        else:
                            exp_str = f"{self.stock.transform.getCell(1,0)}*{self.stock.stock_x_name}/2+{self.stock.transform.getCell(1,1)}*{self.stock.stock_y_name}/2+ {offset.y}"
                        
                        new_joint.offsetY.expression = exp_str + " cm"
                    if new_joint.offset:
                        if self.stock.has_offset_params:
                            exp_str = f"{self.stock.transform.getCell(2,0)}*({self.stock.stock_x_name}/2+{self.stock.stock_dx_name})+{self.stock.transform.getCell(2,1)}*({self.stock.stock_y_name}/2+{self.stock.stock_dy_name})-{self.stock.transform.getCell(2,2)}*({self.stock.ref_top_offset}-{self.stock.stock_dz_name})+{offset.z}"
                        else:
                            exp_str = f"{self.stock.transform.getCell(2,0)}*{self.stock.stock_x_name}/2+{self.stock.transform.getCell(2,1)}*{self.stock.stock_y_name}/2+ {offset.z}"

                        
                        new_joint.offset.expression =exp_str + " cm"
            return new_joint

        except Exception as e:
            adsk.core.Application.get().userInterface.messageBox(f"Error:\n{traceback.format_exc()}")
            return None

class FusionFullPath():
    def __init__(self):
        pass

    def maybe_find_resp_model(self, design : adsk.fusion.Design, resp) -> adsk.fusion.BRepBody | None:
        entityToken = resp.get("model_entityToken", None)
        if entityToken is None:
            return None
        bodies = design.findEntityByToken(entityToken)
        if len(bodies) == 1:
            return bodies[0]
        else: 
            return None
        
    def get_models(self, setup : adsk.cam.Setup):
        assert isinstance(setup, adsk.cam.Setup)
        models = setup.parameters.itemByName('job_model').value.value
        return models

    def get_model(self,setup : adsk.cam.Setup):
        models = self.get_models(setup)
        if len(models) == 0:
            raise Exception("No model found in setup. Please select a model.")
        elif len(models) == 1:
            model = models[0]
        else:
            raise Exception(f"Found {len(models)} models in setup. Only a single model is supported")
        
        return model
    
    def get_setup_body_occurrence(self, setup : adsk.cam.Setup):
        """Returns a tuple of (body, occurrence) from the setup.

        The body is the actual BRepBody selected in the setup.
        The occurrence is its assembly context (parent occurrence).
        """
        bodies = self.get_bodies(setup)
        body = bodies[0]
        occurrence = self.get_occurence(body)
        return (body, occurrence)

    def get_native_body(self,body):
        obj = body.nativeObject
        if obj is None:
            return body
        else:
            return obj

    def extract_bodies_and_transforms_from_occurence(self, out : list, occurrence : adsk.fusion.Occurrence, *, all=False):
        T = occurrence.transform2
        for b in occurrence.bRepBodies:
            out.append((b, T))
        if (len(out) == 0) or all:
            # this seems a bit magical. What we mainly want is not recurse into
            # stuff like BRepBodies that we generated for touch/avoid selection
            for c in occurrence.childOccurrences:
                if is_tp_component_name(c.name): 
                    continue
                self.extract_bodies_and_transforms_from_occurence(out, c, all=all)
        return out

    def extract_bodies_and_transforms_from_component(self, comp : adsk.fusion.Component):
        assert isinstance(comp, adsk.fusion.Component)
        return [(b, self.extract_transform_from_body(b)) for b in comp.bRepBodies]# if b.isVisible]


    def extract_transform_from_body(self, body : adsk.fusion.BRepBody):
        assert isinstance(body, adsk.fusion.BRepBody)
        occ = body.assemblyContext
        if occ is None:
            T = adsk.core.Matrix3D.create()
        else:
            assert isinstance(occ, adsk.fusion.Occurrence)
            T = occ.transform2
        return T

    def extract_bodies_and_transforms_from_body(self,body : adsk.fusion.BRepBody):
        assert isinstance(body, adsk.fusion.BRepBody)
        T = self.extract_transform_from_body(body)
        return [(body, T)]

    def extract_bodies_and_transforms(self, model : adsk.fusion.Occurrence, **kwargs) -> list:
        if isinstance(model, adsk.fusion.Occurrence):
            return self.extract_bodies_and_transforms_from_occurence([], model, **kwargs)
        elif isinstance(model, adsk.fusion.Component):
            return self.extract_bodies_and_transforms_from_component(model)
        elif isinstance(model, adsk.fusion.BRepBody):
            return self.extract_bodies_and_transforms_from_body(model)
        else:
            raise Exception(f"Expected an Occurrence/Component/BRepBody, got {type(model)} instead. Please select a model in the setup.")

    def extract_body_and_transform(self,model):
        results = self.extract_bodies_and_transforms(model)
        if len(results) == 0:
            raise Exception(f"Expected a body in the setup selection, got none. Please modify your setup")

        if len(results) > 1:
            results = [(b,T) for (b,T) in results if b.isVisible]

        if len(results) > 1:
            raise Exception(f"Found visible {len(results)} bodies in setup. Only a single visible body is supported.")
        assert len(results) == 1
        return results[0]

    # def has_model(setup : adsk.cam.Setup) -> bool:
    #     models = get_models(setup)
    #     return len(models) > 0

    def get_bodies(self,setup : adsk.cam.Setup):
        ret = [body for (body, transform) in self.get_bodies_and_transforms(setup)]
        return ret

    def get_bodies_and_transforms(self, setup : adsk.cam.Setup):
        models = self.get_models(setup)
        coll = []
        for model in models:
            coll.extend(self.extract_bodies_and_transforms(model))
        # the same body could be selected multiple times. E.g. once by component and once directly
        ret = []
        for (body, transform) in coll:
            if not body.isSolid:
                # we don't want e.g. touch avoid surfaces
                continue
            skip = False
            for (body2, transform2) in ret:
                if transform.isEqualTo(transform2) and (body == body2):
                    skip = True
                    break
            if not skip:
                ret.append((body, transform))
        return ret
        
    def get_body_and_transform(self,setup : adsk.cam.Setup) -> adsk.fusion.BRepBody:
        """
        Return the body to machine from setup, along with the transform
        that gives the bodies coordinate system in terms of world coordinates.
        """
        results = self.get_bodies_and_transforms(setup)
        if len(results) == 0:
            raise Exception("Expected a body in the setup selection, got none. Please modify your setup")
        elif len(results) > 1:
                raise Exception(f"Found {len(results)} bodies in setup. Only a single body is supported.")
        else:
            return results[0]
        
    def get_component(self,obj) -> adsk.fusion.Component:
        if isinstance(obj, adsk.fusion.Component):
            return obj
        else:
            return self.get_occurence(obj).component

    def get_occurence(self,obj) -> adsk.fusion.Occurrence:
        if isinstance(obj,adsk.fusion.Occurrence):
            return obj
        elif isinstance(obj,adsk.fusion.BRepBody):
            return obj.assemblyContext
        else:
            raise Exception(f"TODO {obj}")

    def get_body(self,obj): #-> adsk.fusion.BRepBody or nothing
        if isinstance(obj, adsk.fusion.BRepBody):
            return obj
        elif isinstance(obj, adsk.fusion.Occurrence):
            bodies = obj.bRepBodies
            if bodies.count == 0:
                return None
            return obj.bRepBodies.item(0)
        elif isinstance(obj, adsk.fusion.Component):
            bodies = obj.bRepBodies
            if bodies.count == 0:
                return None
            return obj.bRepBodies.item(0)
        else:
            raise Exception(f"TODO {obj}")

