import adsk.core
import adsk.cam

import os
import tempfile
from ..lib.event_utils import SimpleCommand
from ..lib.fusion_utils import Fusion
from ..lib.general_utils import log, julia_test_data_path
from .command_RequestFusionOps import UserSpecifiedSetips, AutoSetips
from .command_open_request import import_request_to_new_doc
import time
import json
import traceback
from types import SimpleNamespace

def wait_operations(setup : adsk.cam.Setup):
    assert isinstance(setup, adsk.cam.Setup)
    for ope in setup.operations:
        while True:
            if not ope.isGenerating:
                break  # Exit the loop when generation is complete
            else:
                time.sleep(0.1)  # Wait for a one second before checking again

def print_red(text):
    print("\033[91m" + text + "\033[0m")

def print_green(text):
    print("\033[92m" + text + "\033[0m")

def op_has_problems(op):
    if op_is_manual(op):
        return not op.name.startswith(("Debug",))
    else:
        return op.hasWarning or op.hasError or not op.hasToolpath or not op.isToolpathValid

def op_is_manual(op):
    return op.strategy == 'manual'
    

def run_request(fusion : Fusion, req_path):
    assert os.path.exists(req_path)
    assert os.path.splitext(req_path)[1] == ".json"

    with tempfile.TemporaryDirectory() as tmpdir:
        fusion = Fusion()
        try:
            item = import_request_to_new_doc(fusion, req_path, use_f3d=True)
            doc = item.doc
            request = item.request
            request.config["generate_toolpaths"] = True
            request.config["run_QA_with_CAM"] = False
            request.execute()

            failed_ops = []
            nops = 0
            if isinstance(request.setips, UserSpecifiedSetips):
                setups = [s.obj for s in request.setips.setips if s.compute_fusionops]
            elif isinstance(request.setips, AutoSetips):
                setups = fusion.getCAM().setups
            else:
                raise Exception("Unreachable")
            for setup in setups:
                wait_operations(setup)
                for op in setup.operations:
                    nops += 1
                    if op_has_problems(op):
                        print_red("Operation Failed: " + str(op.name))
                        failed_ops.append(op)

            if any(failed_ops):
                return False
            else:
                print_green(f"Successful operations: {nops}")
                doc.close(False)
            return True
            
        except:
            traceback.print_exc()
            print_red("Error occurred while executing " + str(req_path))
            return False


class Cmd(SimpleCommand):
    def __init__(self):
        super().__init__(name='run fusion requests', description='Executes all the fusion test requests.')

    def run(self,fusion : Fusion):
        dir = julia_test_data_path("RequestFusionOps")

        break_after_nfails = 100

        filenames_skip = [
            # "2x4_lego_simplified.json", # crashes fusion https://github.com/toolpath/ToolpathPackages/issues/1790
            "aluminum_part.json",   # too slow and complicated, fine to skip
            # "AdaptiveWOCIssue103.json", # broken https://github.com/toolpath/FusionTP.jl/issues/401
            # "OuterFillet.json",         # broken https://github.com/toolpath/FusionTP.jl/issues/401
            "polycarb_part_simplified.json",
            "Slots.json",
            "NestedComponentsAutoSetup.json", # TODO
            "TwoToolLibs.json",               # TODO 
            "BikeClampCAB.json", # TODO linked external references
            "BoxFarAwayFromOrigin.json",
            "Pocket2dRestMachining.json", # TODO
            "SetupsDistinctOccurrencesOfSamePart.json", # TODO hangs
        ] 
        failed_requests = []
        
        # skip_until is useful, if you don't want to run the whole test suite.
        skip_until = None # "NestedPocket.json"
        # skip_until = "SetupsDistinctOccurrencesOfSamePart.json"
        for filename in os.listdir(dir):
            if skip_until is not None:
                if filename == skip_until:
                    skip_until = None
                else:
                    log(f"Skipping {filename} until {skip_until}")
                    continue

            if filename in filenames_skip:
                log(f"Skipping {filename}")
                continue

            path = os.path.join(dir, filename)
            if not os.path.splitext(path)[1] == ".json":
                continue
            
            log(f"Executing {path}")
            if not run_request(fusion, path):
                failed_requests.append(path)
            if len(failed_requests) >= break_after_nfails:
                print_red("Early stopping execution because of too many failures")
                break
        
        if (any(failed_requests)):
            print_red("Failed requests:")
            for req in failed_requests:
                print_red(req)
        else:
            print_green("All requests are successful.")
