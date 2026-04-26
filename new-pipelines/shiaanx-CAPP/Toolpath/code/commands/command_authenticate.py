import adsk.core, adsk.fusion, adsk.cam, traceback
import threading, time, json 
from datetime import datetime
import ctypes
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.general_utils import Fusion, resource_path
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar


handlers = []
_app = adsk.core.Application.cast(None)
_ui = adsk.core.UserInterface.cast(None)
_tableInput = adsk.core.TableCommandInput.cast(None)
_cmdDef = adsk.core.CommandDefinition.cast(None)
_workerThread = None
auth_status_event_id = 'auth_status_event_id'

CMD_NAME = 'Authenticate'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Authenticate to be able to use fully use the toolpath Add-In.'
IS_PROMOTED = False
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []

def start():
    try:
        global _app, _ui, _cmdDef
        _app = adsk.core.Application.get()
        _ui  = _app.userInterface

        fusion = Fusion()
        cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED)

        # Connect the command created handler to the event.
        onCommandCreated = CommandCreatedHandler()
        cmd_def.commandCreated.add(onCommandCreated)
        handlers.append(onCommandCreated)
    except:
        if _ui:
            _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def stop():
    try:
        removeCommandFromToolbar(CMD_ID)

    except:
        if _ui:
            _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

# The event handler that responds when the custom event is fired.
class AuthStatusChangeHandler(adsk.core.CustomEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            eventArgs = adsk.core.CustomEventArgs.cast(args)

            # Get the data passed through event.  In this case it is
            # formatted as JSON so it extracts the values named
            # "label" and "value".    
            dialogData = eventArgs.additionalInfo
            valueData = json.loads(dialogData)
            label = valueData['label']
            value = valueData['value']
            
            # Set the value of a string value input using the data passed in.
            stringInput = adsk.core.StringValueCommandInput.cast(_tableInput.getInputAtPosition(int(label), 1))
            stringInput.value = value
        except:
            if _ui:
                _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
            

# The worker thread class. 
class CheckAuthStatusThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.isStopped = False
    def run(self):
        try:
            # Iterate 5 steps to fill each of the 5 rows in the table.
            for i in range(5):
                # Check to see if the thread has been stopped.
                if not self.isStopped:
                    # Simulate calling a web service that will take some
                    # time and returns some data by sleeping and building
                    # some data using the current time.
                    time.sleep(2)

                    date_object = datetime.now()
                    current_time = date_object.strftime('%H:%M:%S')
                    returnInfo = {'label': str(i), 'value': current_time}
                    returnJson = json.dumps(returnInfo)

                    # Fire the custom event to allow the add-in to update the dialog.    
                    _app.fireCustomEvent(auth_status_event_id, returnJson)                    
                else:
                    return
        except:
            ctypes.windll.user32.MessageBoxW(0, 'Failed:\n{}'.format(traceback.format_exc()), "Failed", 1)

    # Method to allow the thread to be stopped.                
    def stop(self):
        self.isStopped = True


# Event handler that is called when the add-in is destroyed. The custom event is
# unregistered here and the thread is stopped.
class DestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:
            eventArgs = adsk.core.CommandEventArgs.cast(args)
            _app.unregisterCustomEvent(auth_status_event_id)
            _workerThread.stop()
        except:
            if _ui:
                _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        
# Event handler to handle when the command is run by the user.
class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        try:

            eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
            cmd = eventArgs.command

            # Registration
            exec_handler = CommandExecuteHandler()
            cmd.execute.add(exec_handler)
            local_handlers.append(exec_handler)
            eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
            inputs = eventArgs.command.commandInputs
            
            # Register the custom event and connect the handler.
            customEvent = _app.registerCustomEvent(auth_status_event_id)
            onAuthStatusChange = AuthStatusChangeHandler()
            customEvent.add(onAuthStatusChange)
            handlers.append(onAuthStatusChange)
            
            # Connect a handler to the command destroyed event.
            onDestroy = DestroyHandler()
            inputs.command.destroy.add(onDestroy)
            handlers.append(onDestroy)        
    
            # Start the seperate thread that will collect the data to populate
            # the second column of the dialog.
            global _workerThread
            _workerThread = CheckAuthStatusThread()
            _workerThread.start()
        except:
            if _ui:
                _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))
        
        