from . import commands
from .lib.fusion_utils import Fusion
from .lib.general_utils import log, handle_error
from .lib.event_utils import clear_handlers

def run(context):
    try:
        # Run the start function in commands/__init__.py
        app = Fusion().getApplication()
        ui = app.userInterface

        commands.start()

        log("Started all commands")
     
    except:
        handle_error('run')


def stop(context):
    try:
        # Remove all of the event handlers we created
        clear_handlers()

        # Run the start function in commands/__init__.py
        commands.stop()
        log("Stopped all commands")

    except:
        handle_error('stop')