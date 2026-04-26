
import os
from ..lib import geometry as geom
from ..lib.event_utils import SimpleCommand
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import load_json, save_json, julia_test_data_path, log

def recreate_request(fusion : Fusion, req_path):
    assert os.path.exists(req_path)
    assert os.path.splitext(req_path)[1] == ".json"

    # res = import_request_to_new_doc(fusion, req_path, use_f3d=False)
    # req_json = res.request.jsonify()
    # req_json = res.request_json

    req_json = load_json(req_path)
    req_json["preset_naming"]["tp_floor_finish"] = req_json["preset_naming"]["tp_finish"]
    req_json["preset_naming"]["tp_wall_finish"] = req_json["preset_naming"]["tp_finish"]
    req_json["preset_naming"]["tp_special"] = req_json["preset_naming"]["tp_finish"]
    req_json["preset_naming"]["tp_light_rough"] = req_json["preset_naming"]["tp_rough"]
    req_json["preset_naming"].pop("tp_finish")

    save_json(req_path, req_json)
    # doc.close(saveChanges=False)

class Cmd(SimpleCommand):
    def __init__(self):
        super().__init__(name='recreate requests', description='Recreate requests. Useful if the data send from fusion to julia changes')

    def run(self,fusion : Fusion):
        # raise Exception("Safeguard, comment me out")
        for dir in [
            julia_test_data_path("RequestFusionOps"),
            julia_test_data_path("RequestFusionOps_error"),
            ]:
            for filename in os.listdir(dir):
                path = os.path.join(dir, filename)
                if not os.path.splitext(path)[1] == ".json":
                    continue
                log(f"recreating {path}")
                recreate_request(fusion, path)
