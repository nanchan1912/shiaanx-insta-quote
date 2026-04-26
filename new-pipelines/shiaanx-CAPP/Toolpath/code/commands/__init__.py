# Commands added to the add-in. Import, start and stop them here.

from ..lib.fusion_utils import Fusion
from ..lib.general_utils import (
    load_config,
    DESIGN_WORKSPACE_ID,
    DESIGN_SOLID_TAB_ID,
    DESIGN_ASSEMBLY_TAB_ID,
    CAM_WORKSPACE_ID,
    CAM_MILLING_TAB_ID,
    TOOLPATH_PANEL_NAME,
    DESIGN_TOOLPATH_PANEL_ID,
    DESIGN_ASSEMBLY_TOOLPATH_PANEL_ID,
    CAM_TOOLPATH_PANEL_ID,
)

# Fusion will automatically call the start() and stop() functions for each of these commands

commands = []
config = load_config()

# Automated CAM first (when enabled)
if config.get("enable_command_ai_cam", False):
    from . import command_ai_cam
    cmd = command_ai_cam.Cmd()
    commands.append(cmd)

from . import command_send_to_toolpath
cmd = command_send_to_toolpath.SendToToolpath()
commands.append(cmd)

if config["enable_legacy_workholding"]:
    from . import command_import_workholding_files
    commands.append(command_import_workholding_files)

if config["enable_command_PluginUpdate"]:
    from . import command_AddinUpdate
    commands.append(command_AddinUpdate)

if config["enable_command_create_stock"]:
    from . import command_create_stock
    cmd = command_create_stock.Cmd()
    commands.append(cmd)

if config["enable_command_make_softjaws"]:
    from . import command_make_softjaws
    cmd = command_make_softjaws.Cmd()
    commands.append(cmd)

if config.get("enable_command_setup_builder", False):
    from . import command_setup_builder
    cmd = command_setup_builder.Cmd()
    commands.append(cmd)

    from . import command_setup_rigger
    cmd = command_setup_rigger.Cmd()
    commands.append(cmd)

# if config["enable_command_authenticate"]:
#     from . import command_authenticate
#     commands.append(command_authenticate)

if config["enable_commands_playground"]:
    from . import command_RequestDebugGeometry
    cmd = command_RequestDebugGeometry.Cmd()
    commands.append(cmd)

    from . import command_RequestFusionOps
    cmd = command_RequestFusionOps.Cmd(product="QA")
    commands.append(cmd)

    from . import command_RequestFusionOps
    cmd = command_RequestFusionOps.Cmd(product="CA")
    commands.append(cmd)

    from . import command_create_stock_box
    commands.append(command_create_stock_box)

    from . import command_run_fusion_requests
    cmd = command_run_fusion_requests.Cmd()
    commands.append(cmd)

    from . import command_recreate_requests
    cmd = command_recreate_requests.Cmd()
    commands.append(cmd)

    from . import command_extract_tool_lib
    cmd = command_extract_tool_lib.Cmd()
    commands.append(cmd)

    from . import command_open_request
    cmd = command_open_request.Cmd()
    commands.append(cmd)

    from . import command_dump_op
    commands.append(command_dump_op)

    from . import command_export_step_file
    commands.append(command_export_step_file)

    from . import command_dump_parameters_by_strategy
    commands.append(command_dump_parameters_by_strategy)

    from . import command_dump_stepdown_expressions
    commands.append(command_dump_stepdown_expressions)

    from .import command_Hello
    commands.append(command_Hello)

    from .import command_inspect_edge
    commands.append(command_inspect_edge)

    from .import command_debug_setup
    commands.append(command_debug_setup)

    from . import command_import_default_tools
    commands.append(command_import_default_tools)

from . import command_Options
commands.append(command_Options)

from . import command_Help
commands.append(command_Help)

from . import command_About
commands.append(command_About)

# Test commands - added at the end with a separator
if config["enable_command_Tests"]:
    from .import command_Tests
    commands.append(command_Tests)

    from .import command_import_current_tests
    commands.append(command_import_current_tests)

    from .import command_import_new_doc_tests
    commands.append(command_import_new_doc_tests)

    from .import command_make_softjaws_tests
    commands.append(command_make_softjaws_tests)

    from .import command_step_export_tests
    commands.append(command_step_export_tests)

    from .import command_fixture_import_tests
    commands.append(command_fixture_import_tests)


def create_ui_panels():
    ui = Fusion().getUI()
    designWS = ui.workspaces.itemById(DESIGN_WORKSPACE_ID)

    # Solid tab
    tb_tab = designWS.toolbarTabs.itemById(DESIGN_SOLID_TAB_ID)
    tb_tab.toolbarPanels.add(DESIGN_TOOLPATH_PANEL_ID, TOOLPATH_PANEL_NAME)

    # Assembly tab
    tb_tab = designWS.toolbarTabs.itemById(DESIGN_ASSEMBLY_TAB_ID)
    if tb_tab:
        tb_tab.toolbarPanels.add(DESIGN_ASSEMBLY_TOOLPATH_PANEL_ID, TOOLPATH_PANEL_NAME)

    manufWS = ui.workspaces.itemById(CAM_WORKSPACE_ID)
    tb_tab = manufWS.toolbarTabs.itemById(CAM_MILLING_TAB_ID)
    tb_tab.toolbarPanels.add(CAM_TOOLPATH_PANEL_ID, TOOLPATH_PANEL_NAME)


def delete_ui_panels():
    ui = Fusion().getUI()
    designWS = ui.workspaces.itemById(DESIGN_WORKSPACE_ID)

    # Solid tab
    tb_tab = designWS.toolbarTabs.itemById(DESIGN_SOLID_TAB_ID)
    panel = tb_tab.toolbarPanels.itemById(DESIGN_TOOLPATH_PANEL_ID)
    if panel != None:
        panel.deleteMe()

    # Assembly tab
    tb_tab = designWS.toolbarTabs.itemById(DESIGN_ASSEMBLY_TAB_ID)
    if tb_tab:
        panel = tb_tab.toolbarPanels.itemById(DESIGN_ASSEMBLY_TOOLPATH_PANEL_ID)
        if panel != None:
            panel.deleteMe()

    manufWS = ui.workspaces.itemById(CAM_WORKSPACE_ID)
    tb_tab = manufWS.toolbarTabs.itemById(CAM_MILLING_TAB_ID)
    panel = tb_tab.toolbarPanels.itemById(CAM_TOOLPATH_PANEL_ID)
    if panel != None:
        panel.deleteMe()


# Each module MUST define a "start" function.
# The start function will be run when the add-in is started.
def start():
    create_ui_panels()
    for command in commands:
        command.start()


# Each module MUST define a "stop" function.
# The stop function will be run when the add-in is stopped.
def stop():
    for command in commands:
        command.stop()
    delete_ui_panels()
