#  Copyright 2022 by Autodesk, Inc.
#  Permission to use, copy, modify, and distribute this software in object code form
#  for any purpose and without fee is hereby granted, provided that the above copyright
#  notice appears in all copies and that both that copyright notice and the limited
#  warranty and restricted rights notice below appear in all supporting documentation.
#
#  AUTODESK PROVIDES THIS PROGRAM "AS IS" AND WITH ALL FAULTS. AUTODESK SPECIFICALLY
#  DISCLAIMS ANY IMPLIED WARRANTY OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR USE.
#  AUTODESK, INC. DOES NOT WARRANT THAT THE OPERATION OF THE PROGRAM WILL BE
#  UNINTERRUPTED OR ERROR FREE.

import sys
import hashlib
import re
from typing import Callable

import adsk.core
from .fusion_utils import Fusion, make_id
from .general_utils import CAM_TOOLPATH_PANEL_ID, CAM_WORKSPACE_ID 
from .general_utils import handle_error, log, resource_path
from .general_utils import addCommandToToolbar, removeCommandFromToolbar


# Global Variable to hold Event Handlers
_handlers = []

def command_id_from_name(name : str) -> str:
    return make_id(f"Toolpath_{name}", 3248002)

def add_handler(
        event: adsk.core.Event,
        callback: Callable,
        *,
        name: str = None,
        local_handlers: list = None
):
    """Adds an event handler to the specified event.

    Arguments:
    event -- The event object you want to connect a handler to.
    callback -- The function that will handle the event.
    name -- A name to use in logging errors associated with this event.
            Otherwise the name of the event object is used. This argument
            must be specified by its keyword.
    local_handlers -- A list of handlers you manage that is used to maintain
                      a reference to the handlers so they aren't released.
                      This argument must be specified by its keyword. If not
                      specified the handler is added to a global list and can
                      be cleared using the clear_handlers function. You may want
                      to maintain your own handler list so it can be managed
                      independently for each command.

    :returns:
        The event handler that was created.  You don't often need this reference, but it can be useful in some cases.
    """
    module = sys.modules[event.__module__]
    handler_type = module.__dict__[event.add.__annotations__['handler']]
    handler = _create_handler(handler_type, callback,
                              event, name, local_handlers)
    event.add(handler)
    return handler


def clear_handlers():
    """Clears the global list of handlers.
    """
    global _handlers
    _handlers = []


def _create_handler(
        handler_type,
        callback: Callable,
        event: adsk.core.Event,
        name: str = None,
        local_handlers: list = None
):
    handler = _define_handler(handler_type, callback, name)()
    (local_handlers if local_handlers is not None else _handlers).append(handler)
    return handler


def _define_handler(handler_type, callback, name: str = None):
    name = name or handler_type.__name__

    class Handler(handler_type):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            try:
                callback(args)
            except:
                handle_error(name, show_message_box=True)

    return Handler

class SimpleCommand:
    """
    Create new commands like this to save lots of boilerplate:
    class HelloWorld(SimpleCommand):
        def __init__(self):
            super().__init__(name='Hello World', description='Display Hello World')

        def run(self,fusion : futil.Fusion):
            # here goes your implementation
            fusion.ui.messageBox("Hello World!")
    
    """
    def __init__(self, name, description):
        # we make members all caps, in order to not collide with 
        # possible subclass members
        self.CMD_NAME = name
        self.CMD_ID = command_id_from_name(self.CMD_NAME)
        self.CMD_Description = description
        self.IS_PROMOTED = False
        self.WORKSPACE_ID = CAM_WORKSPACE_ID
        self.PANEL_ID = CAM_TOOLPATH_PANEL_ID
        self.ICON_FOLDER = resource_path("toolpath_logo", '')
        self.LOCAL_HANDLERS = []

    def start(self):
        cmd_def = addCommandToToolbar(self.CMD_ID, self.CMD_NAME, self.CMD_Description, self.ICON_FOLDER, self.IS_PROMOTED)
        add_handler(cmd_def.commandCreated, self.command_created)
        

    def stop(self):
        removeCommandFromToolbar(self.CMD_ID)

    def command_created(self, args: adsk.core.CommandCreatedEventArgs):
        log(f'{self.CMD_NAME} Command Created Event')
        add_handler(args.command.execute, self.command_execute,
                        local_handlers=self.LOCAL_HANDLERS)
        add_handler(args.command.destroy, self.command_destroy,
                        local_handlers=self.LOCAL_HANDLERS)

    def run(self, fusion : Fusion):
        raise NotImplementedError()

    def command_execute(self, args: adsk.core.CommandEventArgs):
        log(f'{self.CMD_NAME} Command Execute Event')
        fusion = Fusion()
        try:
            self.run(fusion)
        except:
            handle_error(self.CMD_NAME)

    def command_destroy(self,args: adsk.core.CommandEventArgs):
        log(f'{self.CMD_NAME} Command Destroy Event')

        self.LOCAL_HANDLERS = []
