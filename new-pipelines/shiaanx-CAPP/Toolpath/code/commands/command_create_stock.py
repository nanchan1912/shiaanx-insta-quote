from ..lib.event_utils import SimpleCommand
from ..lib.fusion_utils import Fusion,get_active_setup
import adsk.core, adsk.fusion, adsk.cam

class Cmd(SimpleCommand):
    def __init__(self):
        super().__init__(name='Create Stock', description='Create Stock')

    def run(self,fusion : Fusion):        
        if Fusion().isParametricDesign():
            Fusion().getUI().messageBox(f'"Parametric Design Type" is not supported!')
            return

        setup = get_active_setup(fusion)
        if setup.stockMode == adsk.cam.SetupStockModes.PreviousSetupStock:
            Fusion().getUI().messageBox('Active setup stock mode is "From preceding setup"! Please activate a setup with stock definition.')
            return

        groundStockModelOrigin = adsk.cam.BooleanParameterValue.cast(setup.parameters.itemByName("job_groundStockModelOrigin").value).value
        if groundStockModelOrigin:
            title = setup.parameters.itemByName("job_groundStockModelOrigin").title
            Fusion().getUI().messageBox(f'"{title}" option is not supported!')
            return
        stockAxisEnabled = adsk.cam.BooleanParameterValue.cast(setup.parameters.itemByName("job_stockAxisEnabled").value).value
        if stockAxisEnabled:
            title = setup.parameters.itemByName("job_stockAxisEnabled").title
            Fusion().getUI().messageBox(f'"{title}" option is not supported!')
            return

        if isBoxStock(setup.stockMode):
            createBoxStock(setup)
        elif isCylinderStock(setup.stockMode):
            createCylinderStock(setup)
        elif isTubeStock(setup.stockMode):
            createTubeStock(setup)
        elif setup.stockMode == adsk.cam.SetupStockModes.SolidStock:
            Fusion().getUI().messageBox("Solid stock is not supported!")
        else:
            Fusion().getUI().messageBox(f"TODO unknown {setup.stockMode =}")

    
def isBoxStock(stockMode:adsk.cam.SetupStockModes) -> bool:
    if stockMode == adsk.cam.SetupStockModes.FixedBoxStock:
        return True
    if stockMode == adsk.cam.SetupStockModes.RelativeBoxStock:
        return True
    return False


def isCylinderStock(stockMode:adsk.cam.SetupStockModes) -> bool:
    if stockMode == adsk.cam.SetupStockModes.FixedCylinderStock:
        return True
    if stockMode == adsk.cam.SetupStockModes.RelativeCylinderStock:
        return True
    return False


def isTubeStock(stockMode:adsk.cam.SetupStockModes) -> bool:
    if stockMode == adsk.cam.SetupStockModes.FixedTubeStock:
        return True
    if stockMode == adsk.cam.SetupStockModes.RelativeTubeStock:
        return True
    return False


def get_setup_wcs(setup:adsk.cam.Setup) -> adsk.core.Matrix3D:
    wcs = setup.workCoordinateSystem.copy()
    wcs.translation = adsk.core.Vector3D.create(
        wcs.translation.x / 10.0,
        wcs.translation.y / 10.0,
        wcs.translation.z / 10.0
    )
    return wcs
    


def readFloatParameter(parameter:adsk.cam.CAMParameter) -> float:
    return adsk.cam.FloatParameterValue.cast(parameter.value).value


def addSolidToRoot(solid):
    design = Fusion().getDesign()
    if Fusion().isParametricDesign():
        base = None
        base = design.rootComponent.features.baseFeatures.add()
        base.startEdit()
        try:
            body = design.rootComponent.bRepBodies.add(solid, base)
            body.name = "Stock"
        finally:
            if base != None:
                base.finishEdit()
    else:
        body = design.rootComponent.bRepBodies.add(solid)
        body.name = "Stock"


def createBoxStock(setup:adsk.cam.Setup):
    min_x = readFloatParameter(setup.parameters.itemByName("stockXLow"))
    max_x = readFloatParameter(setup.parameters.itemByName("stockXHigh"))
    min_y = readFloatParameter(setup.parameters.itemByName("stockYLow"))
    max_y = readFloatParameter(setup.parameters.itemByName("stockYHigh"))
    min_z = readFloatParameter(setup.parameters.itemByName("stockZLow"))
    max_z = readFloatParameter(setup.parameters.itemByName("stockZHigh"))

    mid_point = adsk.core.Point3D.create(
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        (min_z + max_z) / 2.0
    )
    obb = adsk.core.OrientedBoundingBox3D.create(
        mid_point,
        adsk.core.Vector3D.create(1.0, 0.0, 0.0),
        adsk.core.Vector3D.create(0.0, 1.0, 0.0),
        max_x - min_x,
        max_y - min_y,
        max_z - min_z
    )
    tmpManager = adsk.fusion.TemporaryBRepManager.get()
    box = tmpManager.createBox(obb)
    tmpManager.transform(box, get_setup_wcs(setup))
    addSolidToRoot(box)


def createCylinderStock(setup:adsk.cam.Setup):
    min_x = readFloatParameter(setup.parameters.itemByName("stockXLow"))
    max_x = readFloatParameter(setup.parameters.itemByName("stockXHigh"))
    min_y = readFloatParameter(setup.parameters.itemByName("stockYLow"))
    max_y = readFloatParameter(setup.parameters.itemByName("stockYHigh"))
    min_z = readFloatParameter(setup.parameters.itemByName("stockZLow"))
    max_z = readFloatParameter(setup.parameters.itemByName("stockZHigh"))
    dia = readFloatParameter(setup.parameters.itemByName("stockDiameter"))

    bottom_center = adsk.core.Point3D.create(
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        min_z
    )
    top_center = adsk.core.Point3D.create(
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        max_z
    )
    tmpManager = adsk.fusion.TemporaryBRepManager.get()
    cylinder = tmpManager.createCylinderOrCone(bottom_center, dia/2.0, top_center, dia/2.0)
    tmpManager.transform(cylinder, get_setup_wcs(setup))
    addSolidToRoot(cylinder)


def createTubeStock(setup:adsk.cam.Setup):
    min_x = readFloatParameter(setup.parameters.itemByName("stockXLow"))
    max_x = readFloatParameter(setup.parameters.itemByName("stockXHigh"))
    min_y = readFloatParameter(setup.parameters.itemByName("stockYLow"))
    max_y = readFloatParameter(setup.parameters.itemByName("stockYHigh"))
    min_z = readFloatParameter(setup.parameters.itemByName("stockZLow"))
    max_z = readFloatParameter(setup.parameters.itemByName("stockZHigh"))
    dia = readFloatParameter(setup.parameters.itemByName("stockDiameter"))
    dia_inner = readFloatParameter(setup.parameters.itemByName("stockDiameterInner"))

    bottom_center = adsk.core.Point3D.create(
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        min_z
    )
    top_center = adsk.core.Point3D.create(
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        max_z
    )
    tmpManager = adsk.fusion.TemporaryBRepManager.get()
    cylinder_outer = tmpManager.createCylinderOrCone(bottom_center, dia/2.0, top_center, dia/2.0)
    cylinder_inner = tmpManager.createCylinderOrCone(bottom_center, dia_inner/2.0, top_center, dia_inner/2.0)    
    tmpManager.booleanOperation(cylinder_outer, cylinder_inner, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    tmpManager.transform(cylinder_outer, get_setup_wcs(setup))
    addSolidToRoot(cylinder_outer)