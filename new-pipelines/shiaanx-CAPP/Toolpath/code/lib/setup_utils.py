import adsk.core
import adsk.cam
import adsk.fusion
from types import SimpleNamespace
from .fusion_utils import Fusion
from .fusion_utils import import_part_from_step, light_bulb_off, add_component
from .general_utils import set_parameter, set_parameters, get_parameter, log
from .component_utils import Workholding
from .coord_utils import Matrix3D_from_json, construct_coord_system

def stockMode_from_str(s):
    if s == "FixedBoxStock":
        return adsk.cam.SetupStockModes.FixedBoxStock
    elif s == "RelativeBoxStock":
        return adsk.cam.SetupStockModes.RelativeBoxStock
    elif s == "FixedCylinderStock":
        return adsk.cam.SetupStockModes.FixedCylinderStock
    elif s == "RelativeCylinderStock":
        return adsk.cam.SetupStockModes.RelativeCylinderStock
    elif s == "FixedTubeStock":
        return adsk.cam.SetupStockModes.FixedTubeStock
    elif s == "RelativeTubeStock":
        return adsk.cam.SetupStockModes.RelativeTubeStock
    elif s == "SolidStock":
        return adsk.cam.SetupStockModes.SolidStock
    elif s == "PreviousSetupStock":
        return adsk.cam.SetupStockModes.PreviousSetupStock
    else:
        raise Exception(f"Unknown stock mode: {s}")


def set_machine_attachment(setup,fixtures):
    assert type(fixtures) == Workholding
    fixture_plate_target = "Machine Model"
    machine_occurrence = fixtures.get_joint_target(fixture_plate_target,sub_component_type="fixture_plate")
    
    jointOrigin = machine_occurrence.component.jointOrigins.itemByName("Machine Model Attachment")
    jointOrigin = jointOrigin.createForAssemblyContext(machine_occurrence)
    
    setup.parameters.itemByName('job_fixtureAttachment').value.value = [jointOrigin]

    return setup

def set_input_fixtures_wcs(
    setup,
    fixtures,
):

    setup.parameters.itemByName("wcs_orientation_mode").expression = "'coordinateSystem'"
    assert type(fixtures) == Workholding
    fixture_plate_target = "WCS"
    wcs_occurrence = fixtures.get_joint_target(fixture_plate_target,sub_component_type="fixture_plate")
    
    jointOrigin = wcs_occurrence.component.jointOrigins.itemByName("WCS Attachment")
    jointOrigin = jointOrigin.createForAssemblyContext(wcs_occurrence)
    
    setup.parameters.itemByName('wcs_orientation_cSys').value.value = [jointOrigin]
    
    return setup

def set_input_AbsoluteCoordDef(
    setupInput,
    coorddef,
    occman,
    *,
    use_stock_box_point=False,
):
    """
    Set up the WCS orientation for an AbsoluteCoordDef.

    If use_stock_box_point=True, only sets the orientation (axes) - the origin
    will be set later via set_wcs_origin_stock_box_point() after stock is configured.

    If use_stock_box_point=False, sets both orientation and origin using a
    construction point (legacy behavior).
    """
    wcs_occurrence = occman.get_tp_root_occ()
    setupInput.parameters.itemByName("wcs_orientation_mode").expression = "'axesZX'"
    assert coorddef["subtypekey"] == "AbsoluteCoordDef"
    wcs_from_comp = Matrix3D_from_json(coorddef['workCoordinateSystem_mm'])

    origin, xaxis, yaxis, zaxis = wcs_from_comp.getAsCoordinateSystem()
    c = construct_coord_system(wcs_occurrence, origin, xaxis, zaxis)

    originPointParam: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_origin_point').value
    points = originPointParam.value
    if wcs_occurrence is not None:
        points.append(c.originPoint.createForAssemblyContext(wcs_occurrence))
    else:
        points.append(c.originPoint)
    originPointParam.value = points

    # Set orientation axes (always needed)
    cadObjectParamZAxis: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_orientation_axisZ').value
    zAxes = cadObjectParamZAxis.value
    if wcs_occurrence is not None:
        zAxes.append(c.zAxis.createForAssemblyContext(wcs_occurrence))
    else:
        zAxes.append(c.zAxis)
    cadObjectParamZAxis.value = zAxes

    cadObjectParamXAxis: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_orientation_axisX').value
    xAxes = cadObjectParamXAxis.value
    if wcs_occurrence is not None:
        xAxes.append(c.xAxis.createForAssemblyContext(wcs_occurrence))
    else:
        xAxes.append(c.xAxis)
    cadObjectParamXAxis.value = xAxes

    # Set origin point (only if not using stock box point)
    if not use_stock_box_point:
        setupInput.parameters.itemByName("wcs_origin_mode").expression = "'point'"
        originPointParam: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_origin_point').value
        points = originPointParam.value
        if wcs_occurrence is not None:
            points.append(c.originPoint.createForAssemblyContext(wcs_occurrence))
        else:
            points.append(c.originPoint)
        originPointParam.value = points

    return setupInput


def set_wcs_origin_stock_box_point(setup):
    """
    Set the WCS origin to stock box point mode (top center).

    This should be called AFTER the stock is configured on the setup.
    Places the origin at the center of the top face of the stock.
    """
    # Set origin mode to stock box point
    origin_mode_param = setup.parameters.itemByName("wcs_origin_mode")
    origin_mode_param.expression = "'stockPoint'"

    # Set the box point to top center
    box_point_param = setup.parameters.itemByName("wcs_origin_boxPoint")
    box_point_param.expression = "'top center'"

def set_input_FacetCoordDef(
        setupInput,
        coorddef,
        body,
        facet_id_table,
    ):
    assert facet_id_table is not None
    assert coorddef["subtypekey"] == "FacetCoordDef"
    setupInput.parameters.itemByName("wcs_orientation_mode").expression = "'axesZX'"
    setupInput.parameters.itemByName("wcs_origin_mode").expression = "'point'"
    
    originPointParam: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_origin_point').value
    points = originPointParam.value
    vertex = facet_id_table.get_vertex_by_id(body, coorddef["vertex_id"])
    points.append(vertex)
    originPointParam.value = points

    cadObjectParamZAxis: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_orientation_axisZ').value
    zAxes = cadObjectParamZAxis.value
    z_facet = facet_id_table.get_facet_by_id(body, coorddef["zdirection"]["id"])
    zAxes.append(z_facet)
    cadObjectParamZAxis.value = zAxes

    cadObjectParamXAxis: adsk.cam.CadObjectParameterValue = setupInput.parameters.itemByName('wcs_orientation_axisX').value
    xAxes = cadObjectParamXAxis.value
    x_facet = facet_id_table.get_facet_by_id(body, coorddef["xdirection"]["id"])
    xAxes.append(x_facet)
    cadObjectParamXAxis.value = xAxes

    return setupInput


def setup_JobStockFromStepFile(setup : adsk.cam.Setup, json, fusion : Fusion, *, stock_entityToken=None):
    set_parameter(setup, "job_stockMode", "solid")
    design = fusion.getDesign()

    # Try to reuse existing body by entity token
    stock_body = None
    if stock_entityToken is not None:
        entities = design.findEntityByToken(stock_entityToken)
        if len(entities) == 1:
            stock_body = entities[0]

    if stock_body is not None:
        # Existing body found — use it directly, no STEP import needed
        p = get_parameter(setup, "job_stockSolid")
        p.value.value = [stock_body]
        return

    # Fall back to importing from STEP content
    step_file_content = json["step_file_content"]
    occurrence = import_part_from_step(step_file_content, design, fusion)
    body = None
    name = json["name"]
    for b in occurrence.bRepBodies:
        if b.name == name:
            body = b
        light_bulb_off(b)

    p = get_parameter(setup, "job_stockSolid")
    if body is None:
        p.value.value = [occurrence]
    else:
        p.value.value = [body]
        
def get_operationId(setup : adsk.cam.Setup):
    return hex(setup.operationId)
def get_setup(fusion : Fusion, operationId : str) -> adsk.cam.Setup:
    cam = fusion.getCAM()
    setups = cam.setups
    for setup in setups:
        if get_operationId(setup) == operationId:
            return setup
    raise Exception(f"Could not find setup with operationId {operationId}")

def create_setup(fusion : Fusion, setup_params, *,
    body=None,
    n_bodies=None,
    occman,
    stock=None,
    fixtures=None,
    facet_id_table=None,
    stock_entityToken=None,

    ) -> adsk.cam.Setup:
    # TODO clean this up
    # There is an aweful lot of backwards compat stuff
    # here not needed anymore

    cam = fusion.getCAM()
    setups = cam.setups
    setupInput = setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    body_occurrence = occman.get_part_occ()
    if body_occurrence is None:
        body_selection = body
    else:
        if n_bodies is not None:
            if n_bodies > 1:
                body_selection = body
            else:
                body_selection = body_occurrence
        else:
            body_selection = body_occurrence
    setupInput.models = [body_selection]
    if fixtures is not None:
        setupInput.fixtureEnables = True
        setupInput.fixtures = [fixtures.occurrence]

    # Define the setup properties and parameters.
    setupInput.name = setup_params.get("name", "SetupTP")
    stockMode_str = setup_params.get("stockMode", "RelativeBoxStock")
    setupInput.stockMode = stockMode_from_str(stockMode_str)

    coorddef = setup_params["coorddef"]
    subtypekey = coorddef["subtypekey"]

    # Use stock box point for origin on all AbsoluteCoordDef setups (without full Workholding fixtures)
    # Simple imported fixtures should still use stock box point for WCS origin
    job_stock = setup_params.get("job_stock", None)
    is_full_workholding = type(fixtures) == Workholding
    use_stock_box_point = (
        not is_full_workholding and
        subtypekey == "AbsoluteCoordDef"
    )

    if fixtures is not None and is_full_workholding:
        pass
    elif subtypekey == "AbsoluteCoordDef":
        set_input_AbsoluteCoordDef(
            setupInput,
            coorddef=coorddef,
            occman=occman,
            use_stock_box_point=use_stock_box_point,
        )
    elif subtypekey == "FacetCoordDef":
        #TODO this appears to have a missing ref to body...is this dead code?
        set_input_FacetCoordDef(
            setupInput,
            coorddef=coorddef,
            body=body,
            facet_id_table=facet_id_table,
        )
    else:
        raise Exception(f"Unknown subtypekey: {subtypekey}")

    # Create the setup.
    setup = setups.add(setupInput)
    # Only call WCS and machine attachment for full Workholding objects (not simple fixture occurrences)
    if fixtures is not None and is_full_workholding:
        setup = set_input_fixtures_wcs(setup,
            fixtures=fixtures)
        setup = set_machine_attachment(setup,fixtures=fixtures)
    elif subtypekey == "FacetCoordDef":
        set_parameter(setup,"wcs_orientation_flipX", coorddef["xdirection"]["isflipped"])
        set_parameter(setup,"wcs_orientation_flipZ", coorddef["zdirection"]["isflipped"])
    elif subtypekey == "AbsoluteCoordDef":
        pass
    else:
        raise Exception(f"Unknown subtypekey: {subtypekey}")

    continueMachining = setup_params.get("job_continueMachining", True)
    set_parameter(setup,"job_continueMachining", continueMachining)
    if "job_stockMode" in setup_params:
        job_stockMode = setup_params["job_stockMode"]
    elif continueMachining:
        job_stockMode = "previoussetup"
    else:
        job_stockMode = "default"
    set_parameter(setup, "job_stockMode", job_stockMode)

    # job_stock was already retrieved earlier for use_stock_box_point check
    if job_stock is None:
        # TODO delete this
        if "setup_parameters" in setup_params.keys():
            set_parameters(setup, setup_params["setup_parameters"])
        # Still set WCS origin to stock box point if applicable
        if use_stock_box_point:
            set_wcs_origin_stock_box_point(setup)
        return setup

    job_stock_subtypekey = job_stock["subtypekey"]
    if job_stock_subtypekey in ["JobStockFixedBox","JobStockFixedCylinder","JobStockFromPrevious",
                      "JobStockFromSetupParameters"]:
        set_parameters(setup, job_stock["parameters"])
    elif job_stock_subtypekey == "JobStockCreateModel":
        setup_JobStockCreateModel(setup, job_stock,stock, occman=occman)
    elif job_stock_subtypekey == "JobStockFromStepFile":
        setup_JobStockFromStepFile(setup, job_stock["fusion_stock_solid"], fusion,
                                   stock_entityToken=stock_entityToken)
    else:
        raise Exception("Unreachable: Unknown subtypekey: " + job_stock_subtypekey)

    # Set WCS origin to stock box point (top center) for all AbsoluteCoordDef setups
    if use_stock_box_point:
        set_wcs_origin_stock_box_point(setup)

    return setup

def Vector3d_from_itr(itr) -> adsk.core.Vector3D:
    x,y,z = itr
    return adsk.core.Vector3D.create(x,y,z)

def Point3D_from_itr(itr) -> adsk.core.Point3D:
    x,y,z = itr
    return adsk.core.Point3D.create(x,y,z)

def create_box_BRepBody(center, xdir, ydir, size, component : adsk.fusion.Component, name) -> adsk.fusion.BRepBody:
    length, width, height = size
    # Extract translation
    centerPoint = Point3D_from_itr(center)
    
    lengthDirection = Vector3d_from_itr(xdir)
    widthDirection = Vector3d_from_itr(ydir)
    assert lengthDirection.normalize()
    assert widthDirection.normalize()
    assert abs(lengthDirection.dotProduct(widthDirection)) < 1e-4

    obox = adsk.core.OrientedBoundingBox3D.create(centerPoint=centerPoint, lengthDirection=lengthDirection, widthDirection=widthDirection, length=length, width=width, height=height)
    tmpManager = adsk.fusion.TemporaryBRepManager.get()
    brepbox : adsk.fusion.BRepBody = tmpManager.createBox(obox)
    assert isinstance(brepbox, adsk.fusion.BRepBody)

    
    if Fusion().isParametricDesign():
        base = component.features.baseFeatures.add()
        base.startEdit()
        try:
            body = component.bRepBodies.add(brepbox, base)
            body.name = name
        finally:
            if base != None:
                base.finishEdit()
    else:
        body = component.bRepBodies.add(brepbox)
        body.name = name
    return body

def create_cylinder_BRepBody(shape, component: adsk.fusion.Component) -> adsk.fusion.BRepBody:
    #TODO need to add a part occurrence transform here!!!
    brepman : adsk.fusion.TemporaryBRepManager = adsk.fusion.TemporaryBRepManager.get()
    pointOne = Point3D_from_itr(shape["pointOne"])
    pointTwo = Point3D_from_itr(shape["pointTwo"])
    pointOneRadius = pointTwoRadius = shape["radius"]
    body : adsk.fusion.BRepBody = brepman.createCylinderOrCone(pointOne, pointOneRadius, pointTwo, pointTwoRadius)
    design = Fusion().getDesign()
    if design.designType != adsk.fusion.DesignTypes.DirectDesignType:
        # TODO make cylinder construction compatible with parametric design
        log("create_cylinder_BRepBody: changing desing type")
        design.designType = adsk.fusion.DesignTypes.DirectDesignType

    body = component.bRepBodies.add(body)
    body.name = shape["name"]
    return body


def setup_JobStockCreateModel(setup : adsk.cam.Setup, job_stock : dict, stock, *, occman):
    assert job_stock["subtypekey"] == "JobStockCreateModel"
    use_stock = stock is not None
    
    if use_stock:
        stock_body = stock.get_body()
        selection = stock_body
    else:
        parent_occ = occman.get_stock_occ()
        comp_occ = add_component(parent_occ.component, "OptimizedStock")
        shape = job_stock["shape"]
        subtypekey = shape.get("subtypekey", "StockShapeBox") # backward compat 
        if subtypekey == "StockShapeCylinder":
            # todo need to add the part occurrence transform here
            create_cylinder_BRepBody(shape, comp_occ.component)
        elif subtypekey == "StockShapeBox":
            create_box_BRepBody(
                center = job_stock["center"],
                xdir=job_stock["xdirection"],
                ydir=job_stock["ydirection"],
                size=job_stock["size"],
                component=comp_occ.component,
                name = job_stock["name"],
            )
        else:
            raise Exception(f"Unknown subtypekey: {subtypekey}")
        # if we use the root component, turning off the lightbulb
        # does not work properly
        light_bulb_off(comp_occ)
        # if we select the body instead, everything seems to work fine on first glance
        # however after switching to desing workspace and back to manufacturing 
        # the body becomes invalid and the setup errors
        selection = comp_occ
    # stock_body.isLightBulbOn = False
    set_parameters(setup, job_stock["parameters"])
    set_parameter(setup, "job_stockSolid", [selection])

def delete_operations(setup : adsk.cam.Setup):
    assert isinstance(setup, adsk.cam.Setup)
    # operations might have dependencies between them
    # we reverse the order, so that each operation
    # is deleted before its dependencies
    # fixes https://github.com/toolpath/FusionTP.jl/issues/187
    old_ops = list(setup.operations)
    old_ops.reverse()
    for op in old_ops:
        op.deleteMe()

def get_setup_selector_id(setup):
    id = setup.operationId
    return f"setup_selector_{id}"
