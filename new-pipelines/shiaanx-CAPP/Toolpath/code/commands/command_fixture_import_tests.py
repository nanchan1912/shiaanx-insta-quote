import adsk.core
from ..lib.event_utils import command_id_from_name, add_handler
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import resource_path, log
from ..lib.general_utils import addCommandToToolbar, removeCommandFromToolbar
import traceback
import unittest
import io
import contextlib
import re

CMD_NAME = 'Run Fixture Import Tests'
CMD_ID = command_id_from_name(CMD_NAME)
CMD_Description = 'Run fixture import integration tests with programmatic geometry creation.'
ICON_FOLDER = resource_path("toolpath_logo", '')
local_handlers = []


def start():
    ui = None
    try:
        fusion = Fusion()
        ui = fusion.getUI()
        cmd_def = addCommandToToolbar(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER, IS_PROMOTED=False)

        add_handler(cmd_def.commandCreated, onCommandCreated, local_handlers=local_handlers)
    except:
        log(traceback.format_exc())
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def stop():
    ui = None
    try:
        ui = Fusion().getUI()
        removeCommandFromToolbar(CMD_ID)

    except:
        log(traceback.format_exc())
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


def onCommandCreated(args):
    eventArgs = adsk.core.CommandCreatedEventArgs.cast(args)
    cmd = eventArgs.command
    inputs = cmd.commandInputs
    add_handler(cmd.execute, onCommandExecute, local_handlers=local_handlers)


def onCommandExecute(args):
    run_tests()


def clean_test_output(output):
    # Remove encoded paths in test headers
    output = re.sub(r'\(__main__[^)]*\)', '', output)

    # Remove long Windows paths and file encodings
    output = re.sub(r'C%3A[^)]*', '', output)

    # Remove double slashes and backslashes
    output = output.replace('\\', '/')

    # Trim excessive blank lines
    output = re.sub(r'\n{3,}', '\n\n', output)

    return output.strip()


def run_tests():
    from ..fusion_tests import fixture_import_tests
    suite = unittest.defaultTestLoader.loadTestsFromModule(fixture_import_tests)

    buffer = io.StringIO()

    with contextlib.redirect_stdout(buffer):
        unittest.TextTestRunner(stream=buffer, verbosity=2).run(suite)

    raw_output = buffer.getvalue()
    clean_output = clean_test_output(raw_output)

    app = adsk.core.Application.get()
    ui = app.userInterface

    # Log full output to console
    log(f"[command_fixture_import_tests.py]", force_console=True)
    log(f"results:{clean_output}", force_console=True)

    # Truncate for message box if needed
    display_output = clean_output
    if len(display_output) > 4000:  # Fusion message box limit
        display_output = display_output[:4000] + "\n\n... Output Truncated ..."

    ui.messageBox(f'{CMD_NAME}:\n\n{display_output}')
