from ..lib.event_utils import SimpleCommand
from ..lib.fusion_utils import Fusion,get_step_file_content
from ..lib.client import Client
from ..lib import geometry as geom


class Cmd(SimpleCommand):
    def __init__(self):
        super().__init__(name='RequestDebugGeometry', description='Send the geometry to julia for debugging')
    def run(self,fusion : Fusion):
        

        body = fusion.get_body()
        step_file_content, _ = get_step_file_content(fusion, body.parentComponent)
        table = geom.FacetIdTable(body).jsonify()
        payload = {
            "subtypekey": "RequestDebugGeometry",
            "step_file_content": step_file_content,
            "geometry": table,
        }
        
        client = Client()
        response = client.request(payload)
