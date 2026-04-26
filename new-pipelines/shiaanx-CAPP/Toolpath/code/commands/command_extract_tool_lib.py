#from ..lib import fusion_360_utils as futil
from ..lib.event_utils import SimpleCommand
from ..lib.fusion_utils import Fusion, get_active_setup
from ..lib.general_utils import desktop_path
import adsk.cam as cam
import json

def get_tool_guid(tool : cam.Tool) -> str:
    return json.loads(tool.toJson())["guid"]

class Cmd(SimpleCommand):
    def __init__(self):
        super().__init__(name='extract tool lib', description='Extract all tools from selected setup and save as json file')

    def run(self,fusion : Fusion):
        setup: adsk.cam.Setup = get_active_setup(fusion)
        if setup is None:
            fusion.ui.messageBox("""Please select a setup.""")
            return

        # deduplicate
        tools = {}
        for op in setup.operations:
            tool = op.tool
            guid = get_tool_guid(tool)
            tools[guid] = tool

        toollib = cam.ToolLibrary.createEmpty()
        for (guid, tool) in tools.items():
            toollib.add(tool)

        path = desktop_path("tools.json")
        with open(path, "w") as file:
            s = toollib.toJson()
            file.write(s)
        msg = f"""
        Saved {len(tools)} tools from setup 
        
            "{setup.name}"

        to the following path:

            {path}
        """

        fusion.ui.messageBox(msg)