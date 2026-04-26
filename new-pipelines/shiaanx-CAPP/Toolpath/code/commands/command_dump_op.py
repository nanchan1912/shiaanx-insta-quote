from datetime import datetime
import adsk.core, adsk.cam, traceback

from ..lib.event_utils import add_handler
from ..lib.fusion_utils import Fusion, get_active_setup
from ..lib.general_utils import COMPANY_NAME, ADDIN_NAME, resource_path, log, handle_error
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar, CAM_WORKSPACE_ID, CAM_TOOLPATH_PANEL_ID



CMD_NAME = 'dump_operation'
CMD_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_{CMD_NAME}'
CMD_Description = 'Dump selected setup or operation'
IS_PROMOTED = False
WORKSPACE_ID = CAM_WORKSPACE_ID
PANEL_ID = CAM_TOOLPATH_PANEL_ID
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

def start():
    cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED)

    add_handler(cmd_def.commandCreated, command_created)

def stop():
    removeCommandFromToolbar(CMD_ID)

def command_created(args: adsk.core.CommandCreatedEventArgs):
    log(f'{CMD_NAME} Command Created Event')
    add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)

def command_execute(args: adsk.core.CommandEventArgs):
    log(f'{CMD_NAME} Command Execute Event')
    fusion = Fusion()
    try:
        run(fusion)
    except:
        handle_error(CMD_NAME)

def command_destroy(args: adsk.core.CommandEventArgs):
    log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []

def run(fusion : Fusion):
    ui = fusion.getUI()
    try:
        # There's a current limitation where CAM objects can't be
        # retrieved from the active selection. This is a workaround
        # to get the active setup or an operation if one is selected.
        activeSetup: adsk.cam.Setup = get_active_setup(fusion)
        activeOp = None
        for op in activeSetup.operations:
            if op.isSelected:
                activeOp = op
                break

        # Give the user the option of only showing editable parameters.
        result = ui.messageBox(f'Only write editable parameters?',
                                'Only Write Editable Parameters',
                                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                                adsk.core.MessageBoxIconTypes.QuestionIconType)
        if result == adsk.core.DialogResults.DialogNo:
            onlyEditable = False
        else:
            onlyEditable = True

        # If an operation was found, process it. If not process the setup. 
        if activeOp:
            result = ui.messageBox(f'The parameters will be exported for the selected operation: {activeOp.name}\nContinue?',
                                  'Export Parameters',
                                  adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                                  adsk.core.MessageBoxIconTypes.QuestionIconType)
            if result == adsk.core.DialogResults.DialogNo:
                return

            headerInfo = f'Parameter information for operation "{activeOp.name}" using strategy "{activeOp.strategy}".'            
            paramList = GetParameters(activeOp.parameters, onlyEditable)
        elif activeSetup:
            result = ui.messageBox(f'The parameters will be exported for the selected setup: {activeSetup.name}\nContinue?',
                                  'Export Parameters',
                                  adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                                  adsk.core.MessageBoxIconTypes.QuestionIconType)
            if result == adsk.core.DialogResults.DialogNo:
                return
            
            headerInfo = f'Parameter information for setup "{activeSetup.name}"'
            paramList = GetParameters(activeSetup.parameters, onlyEditable)

        # Get the filename to export the results to.
        fileDialog = ui.createFileDialog()
        fileDialog.filter = "Text File (*.txt)"
        if fileDialog.showSave() == adsk.core.DialogResults.DialogOK:
            filename = fileDialog.filename

            paramList = f'== {headerInfo} ==\n{paramList}' 
            text_file = open(filename, 'w')
            text_file.write(paramList)
            text_file.close()
    except:
        ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


# Given a CAMParameters collection, this builds a string that contains
# information about the parameters.
def GetParameters(params: adsk.cam.CAMParameters, showOnlyEditable: bool) -> str:
    ui = Fusion().getUI()
    try:
        paramList = []
        param: adsk.cam.CAMParameter
        for param in params:
            if param.isEditable:
                i = 1

            # Only write out this parameter if it's editable or if the showOnlyEditable flag is False.
            if param.isEditable or (not param.isEditable and not showOnlyEditable) :
                result = ''

                # Display the property values.
                result += f'name: {param.name}\n'
                result += f'    title: {param.title}\n'
                result += f'    expression: "{param.expression}"\n'
                result += f'    isEditable: {param.isEditable}\n'
                result += f'    isEnabled: {param.isEnabled}\n'

                # Attempt to get the value of the parameter. This does fail in
                # some cases.
                value = None
                try:
                    value = param.value
                except:
                    result += f'    ** Failed to get value type.\n'

                # Create information for each specific value type.
                if value:
                    if value.objectType == adsk.cam.FloatParameterValue.classType():
                        floatVal: adsk.cam.FloatParameterValue = value
                        result += f'    FloatParameterValue\n'
                        result += f'        value: {floatVal.value}\n'
                    elif value.objectType == adsk.cam.ChoiceParameterValue.classType():
                        choiceVal: adsk.cam.ChoiceParameterValue = value
                        result += f'    ChoiceParameterValue\n'
                        result += f'        value: {choiceVal.value}\n'
                        result += f'        choices:\n'
                        (_, names, values) = choiceVal.getChoices()
                        for i in range(len(names)):
                            result += f'            name: {names[i]}, value: {values[i]}\n'
                    elif value.objectType == adsk.cam.StringParameterValue.classType():
                        stringVal: adsk.cam.StringParameterValue = value
                        result += f'    StringParameterValue\n'
                        result += f'        value: {stringVal.value}\n'
                    elif value.objectType == adsk.cam.BooleanParameterValue.classType():
                        boolVal: adsk.cam.BooleanParameterValue = value
                        result += f'    FloatParameterValue\n'
                        result += f'        value: {boolVal.value}\n'
                    elif value.objectType == adsk.cam.IntegerParameterValue.classType():
                        intVal: adsk.cam.IntegerParameterValue = value
                        result += f'    FloatParameterValue\n'
                        result += f'        value: {intVal.value}\n'
                    elif value.objectType == adsk.cam.CadObjectParameterValue.classType():
                        cadObjectVal: adsk.cam.CadObjectParameterValue = value
                        result += f'    CadObjectParameterValue\n'
                        if len(cadObjectVal.value) == 0:                       
                            result += f'        value: Empty\n'
                        else:
                            result += f'        value:\n'
                            result += printGeometry(cadObjectVal.value, '            ')
                    elif value.objectType == adsk.cam.CadContours2dParameterValue.classType():
                        cadContourVal: adsk.cam.CadContours2dParameterValue = value
                        result += f'    CadContours2dParameterValue\n'
                        curveSelections = cadContourVal.getCurveSelections()
                        if curveSelections.count == 0:                       
                            result += f'        value: Empty\n'
                        else:
                            for i in range( len(curveSelections)):                               
                                if curveSelections[i].objectType == adsk.cam.PocketSelection.classType():
                                    pocketSel: adsk.cam.PocketSelection = curveSelections[i]
                                    result += f'        Curve Selection {i+1}: PocketSelection\n'
                                    result += f'            extensionMethod: {pocketSel.extensionMethod}\n'
                                    result += f'            isSelectingSamePlaneFaces: {pocketSel.isSelectingSamePlaneFaces}\n'
                                    result += f'            inputGeometry:\n'
                                    result += printGeometry(pocketSel.inputGeometry, '                ')
                                    result += f'            value:\n'
                                    result += printGeometry(pocketSel.inputGeometry, '                ')
                                elif curveSelections[i].objectType == adsk.cam.ChainSelection.classType():
                                    chainSel: adsk.cam.ChainSelection = curveSelections[i]
                                    result += f'        Curve Selection {i+1}: ChainSelection\n'
                                    result += f'            startExtensionLength: {chainSel.startExtensionLength}\n'
                                    result += f'            endExtensionLength: {chainSel.endExtensionLength}\n'
                                    result += f'            extensionMethod: {chainSel.extensionMethod}\n'
                                    result += f'            isOpen: {chainSel.isOpen}\n'
                                    result += f'            isOpenAllowed: {chainSel.isOpenAllowed}\n'
                                    result += f'            endExtensionLength: {chainSel.endExtensionLength}\n'
                                    result += f'            isReverted: {chainSel.isReverted}\n'
                                    result += f'            extensionType: {chainSel.extensionType}\n'
                                    result += f'            isReverted: {chainSel.isReverted}\n'
                                    result += f'            inputGeometry:\n'
                                    result += printGeometry(chainSel.inputGeometry, '                ')
                                    result += f'            value:\n'
                                    result += printGeometry(chainSel.inputGeometry, '                ')
                                elif curveSelections[i].objectType == adsk.cam.SilhouetteSelection.classType():
                                    silSel: adsk.cam.SilhouetteSelection = curveSelections[i]
                                    result += f'        Curve Selection {i+1}: SilhouetteSelection\n'
                                    result += f'            isSetupModelSelected: {silSel.isSetupModelSelected}\n'
                                    result += f'            loopType: {silSel.loopType}\n'
                                    result += f'            sideType: {silSel.sideType}\n'
                                    result += f'            inputGeometry:\n'
                                    result += printGeometry(silSel.inputGeometry, '                ')
                                    result += f'            value:\n'
                                    result += printGeometry(silSel.inputGeometry, '                ')
                                elif curveSelections[i].objectType == adsk.cam.FaceContourSelection.classType():
                                    faceSel: adsk.cam.FaceContourSelection = curveSelections[i]
                                    result += f'        Curve Selection {i+1}: FaceContourSelection\n'
                                    result += f'            loopType: {faceSel.loopType}\n'
                                    result += f'            isSelectingSamePlaneFaces: {faceSel.isSelectingSamePlaneFaces}\n'
                                    result += f'            sideType: {faceSel.sideType}\n'
                                    result += f'            inputGeometry:\n'
                                    result += printGeometry(faceSel.inputGeometry, '                ')
                                    result += f'            value:\n'
                                    result += printGeometry(faceSel.inputGeometry, '                ')

                paramList.append([param.name, f'\n{result}'])

        # Sort the list so it will be in alphabetical order.
        def sortFunc(paramItem):
            return paramItem[0]

        paramList.sort(key = sortFunc)

        # Convert it to a string.
        fullResult = ''
        for paramData in paramList:
            fullResult += paramData[1]

        return fullResult
    except:
        ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        return '** Unexpected Failure getting parameter information.'
    

# Constructs a string representing a list of the geometry in the input list. 
def printGeometry(geomList: list, indent: str) -> str:
    result = ''
    for geom in geomList:
        aaa = ''
        aaa.split()
        typeName = geom.objectType.split('::')
        if result == '':
            result = f'{indent}{typeName[2]}'
        else:
            result += f'\n{indent}{typeName[2]}'

    return f'{result}\n' 