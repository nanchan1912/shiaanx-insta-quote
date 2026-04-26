import adsk.core
from ...lib.event_utils import add_handler
from ...lib.fusion_utils import Fusion
from ...lib.general_utils import load_json, addin_code_rpath, addin_root_rpath,log,save_json,get_addin_version,persistent_state_path

import json
import os
from . import logic
from types import SimpleNamespace
import shutil


DEFAULT_MATERIAL = "Generic Aluminum";

# Milling
FT_Type_ball_end_mill = "ball end mill"
FT_Type_bull_nose_end_mill = "bull nose end mill"
FT_Type_flat_end_mill = "flat end mill"
FT_Type_face_mill = "face mill"
FT_Type_tapered_mill = "tapered mill"
FT_Type_radius_mill = "radius mill"
FT_Type_chamfer_mill = "chamfer mill"
FT_Type_dovetail_mill = "dovetail mill"
FT_Type_lollipop_mill = "lollipop mill"
FT_Type_slot_mill = "slot mill"
FT_Type_thread_mill = "thread mill"
FT_Type_form_mill = "form mill" # does not show up in UI?
# Hole making
FT_Type_boring_bar = "boring bar"
FT_Type_counter_bore = "counter bore"
FT_Type_drill = "drill"
FT_Type_center_drill = "center drill"
FT_Type_spot_drill = "spot drill"
FT_Type_reamer = "reamer"
FT_Type_counter_sink = "counter sink"
FT_Type_tap_left_hand = "tap left hand"
FT_Type_tap_right_hand = "tap right hand"
# Turning
FT_Type_turning_general = "turning general"
FT_Type_turning_boring = "turning boring"
FT_Type_turning_grooving = "turning grooving"
FT_Type_turning_threading = "turning threading"
# Cutting
FT_Type_waterjet = "waterjet"
FT_Type_laser_cutter = "laser cutter"
FT_Type_plasma_cutter = "plasma cutter"
# Probe
FT_Type_probe = "probe"
# Holder
FT_Type_holder = "holder"

TOOL_TYPES_BY_PURPOSE = {
    "tp_face"         : [FT_Type_face_mill, FT_Type_flat_end_mill, FT_Type_bull_nose_end_mill],       
    "tp_wall_finish"  : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill],
    "tp_floor_finish" : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill],
    "tp_special"      : [FT_Type_chamfer_mill, FT_Type_dovetail_mill,  FT_Type_lollipop_mill, FT_Type_slot_mill, ],
    "tp_surface"      : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill, FT_Type_chamfer_mill],    
    "tp_bore_rough"   : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill],       
    "tp_bore_finish"  : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill],
    "tp_slot"         : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill],
    "tp_drill"        : [FT_Type_center_drill, FT_Type_spot_drill, FT_Type_drill],
    "tp_face_rough"       : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill, FT_Type_face_mill],
    "tp_traditional_rough": [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill, FT_Type_face_mill],
    "tp_adaptive_rough"   : [FT_Type_flat_end_mill, FT_Type_ball_end_mill, FT_Type_bull_nose_end_mill, FT_Type_face_mill],
    "tp_engrave"          : [],
}

PRESET_PURPOSES = list(TOOL_TYPES_BY_PURPOSE.keys())

def is_tool_usable_for_purpose(tool_json, purpose, preset_names) -> bool:
    if not tool_json["type"] in TOOL_TYPES_BY_PURPOSE[purpose]:
        return False
    for name in get_preset_names_from_tool_json(tool_json):
        if name in preset_names:
            return True
    return False

def get_preset_names_from_tool_json(tool_json) -> set[str]:
    assert isinstance(tool_json, dict)
    start_values = tool_json.get("start-values", None)
    if start_values is None:
        return set([])

    presets = start_values.get("presets", None)
    if presets is None:
        return set([])
    preset_names = set([preset["name"] for preset in presets])
    return preset_names

def get_preset_names_from_toollib_json(toollib_json) -> list[str]:
    acc = set([])
    for tool_json in toollib_json["data"]:
        names = get_preset_names_from_tool_json(tool_json)
        acc.update(names)
    return list(acc)

def ask_user_stop_if_missing_presets(toollibs_json, preset_naming) -> bool:
    # we run this check on json, because it is much faster
    # than iterating over the classes
    purposes_lacking_presets = []
    for purpose in PRESET_PURPOSES:
        preset_names = preset_naming[purpose]
        skip_to_next_purpose = False
        for toollib in toollibs_json:
            for tool in toollib["data"]:
                if is_tool_usable_for_purpose(tool, purpose, preset_names):
                    skip_to_next_purpose = True
                    break
                if skip_to_next_purpose:
                    break
            if skip_to_next_purpose:
                break
        if skip_to_next_purpose:
            continue
        purposes_lacking_presets.append(purpose)

    if len(purposes_lacking_presets) == 0:
        return False
    elif len(purposes_lacking_presets) == 1:
        if purposes_lacking_presets[0] == "tp_engrave":
            return False

    per_purpose_msgs = []
    for purpose in purposes_lacking_presets:
        names = preset_naming[purpose]
        msg = f"""
        purpose = {purpose}\n
        possible preset names = {", ".join(names)}\n
        possible tool types = {", ".join(TOOL_TYPES_BY_PURPOSE[purpose])}
        """
        per_purpose_msgs.append(msg)
    
    msg = f"""
    We found a potential issue with the combination of tool library and material selection. Your tool library does not contain tools with presets for all purposes. Depending on the geometry of your part this may or may not be an issue.

    Should we continue anyway?

    The following purposes are lacking presets:

    {"".join(per_purpose_msgs)}
    """
    ui = Fusion().getUI()
    dialog_result = ui.messageBox(msg, "Continue anyway?",
        adsk.core.MessageBoxButtonTypes.YesNoButtonType,
    )
    if dialog_result == adsk.core.DialogResults.DialogYes:
        return False
    elif dialog_result == adsk.core.DialogResults.DialogNo:
        return True
    else:
        raise Exception(f"Unexpected dialog result {dialog_result}.")

def read_preset_naming_legacy(json_file : dict):
    ret = {}
    for (material, preset_naming) in json_file.items():
        new_preset_naming = {}
        for purpose in PRESET_PURPOSES:
            if purpose in preset_naming.keys():
                new_preset_naming[purpose] = preset_naming[purpose]
            elif purpose in ("tp_wall_finish", "tp_floor_finish", "tp_special"):
                if "tp_finish" in preset_naming.keys():
                    new_preset_naming[purpose] = preset_naming["tp_finish"]
                else:
                    new_preset_naming[purpose] = []
            elif purpose == "tp_drill":
                new_preset_naming["tp_drill"] = preset_naming[DEFAULT_MATERIAL]
            elif purpose == "tp_light_rough":
                new_preset_naming["tp_light_rough"] = preset_naming["tp_rough"]
            else:
                msg = f"""
                Could not determine preset names for purpose.
                purpose = {purpose}
                material = {material}
                """
                raise Exception(msg)
        ret[material] = new_preset_naming 
    return ret

def read_preset_naming_latest(json_file):
    return json_file["data"]

def validate_preset_naming(preset_naming):
    for (material, material_presets) in preset_naming.items():
        for (key,vals) in material_presets.items():
            if not key in PRESET_PURPOSES:
                msg = f"""
                Got unsupported preset purpose:
                material = {material}
                unsupported purpose = {key}
                supported purposes = {PRESET_PURPOSES}
                """
                log(msg)

            if not isinstance(vals, list):
                msg = f"""
                f"Expected a list of strings as preset names. Got:
                preset names = {vals}
                purpose = {key}
                material = {material}
                """
                raise Exception(msg)
            for val in vals:
                if not isinstance(val, str):
                    msg = f"""
                    Expected a list of strings as preset names. Got:
                    preset names = {vals}
                    purpose = {key}
                    material = {material}
                    """
                    raise Exception(msg)

        for purpose in PRESET_PURPOSES:
            if not purpose in material_presets.keys():
                msg = f"""
                Preset purpose is missing:
                material = {material}
                missing purpose = {purpose}
                """
                raise Exception(msg)

def read_preset_naming(json_file):
    if not "version" in json_file.keys():
        ret = read_preset_naming_legacy(json_file)
    else:
        ret = read_preset_naming_latest(json_file)
    validate_preset_naming(ret)
    return ret

def load_preset_naming() -> dict[str, dict[str,list[str]]]:
    file_json = load_json(addin_code_rpath("preset_naming_template.json"))
    ret = read_preset_naming(file_json)
    assert DEFAULT_MATERIAL in ret.keys()
    path_preset_naming = addin_root_rpath("preset_naming.json")
    need_save = False
    if os.path.exists(path_preset_naming):
        json_file = load_json(path_preset_naming)
        d = read_preset_naming(json_file)
        if DEFAULT_MATERIAL in d.keys():
            if not d[DEFAULT_MATERIAL] == ret[DEFAULT_MATERIAL]:
                msg = f"""DEFAULT_MATERIAL should not be overwritten."""
                log(msg)
                need_save = True
            d.pop(DEFAULT_MATERIAL)
        ret.update(d)
    else:
        need_save = True
    default_material_presets = ret[DEFAULT_MATERIAL]
    for key in PRESET_PURPOSES:
        if not key in default_material_presets.keys():
            raise Exception(f'{key} missing for DEFAULT_MATERIAL')

    for (material, material_presets) in ret.items():
        if material == DEFAULT_MATERIAL:
            continue
        key_deprecated = "Default preset"
        key = "tp_drill"
        if key_deprecated in material_presets.keys():
            # TODO delete this deprecation
            log(f"key = {key_deprecated} is deprecated in preset naming.")
            if not "tp_drill" in material_presets.keys():
                material_presets[key] = material_presets[key_deprecated]
                log(f"Adding {key} to preset naming for material {material}.")
                need_save = True

        for key in PRESET_PURPOSES:
            if not key in material_presets.keys():
                msg = f"""
                f"Material {material} is missing {key}. Please adjust:
                    {path_preset_naming} For instance you could add:
                    "{key}" : {default_material_presets[key]}
                """
                raise Exception(msg)
            vals = material_presets[key]
    if need_save:
        save_preset_naming(ret)
    return ret

def save_preset_naming(preset_naming: dict[str, dict[str,list[str]]]):
    path_preset_naming = addin_root_rpath("preset_naming.json")

    json_file = {
        "version" : get_addin_version(),
        "data" : preset_naming,
    }
    save_json(path_preset_naming, json_file)



def load_cutdata():
    path  = persistent_state_path("CutConfig.json")
    if not os.path.exists(path) :
        if os.path.exists(addin_root_rpath("preset_naming.json")):
            cutdata =  load_cutdata_from_preset_naming()
            save_cutdata(cutdata)
        else:
            directory = os.path.dirname(__file__)
            path_defaults = os.path.join(directory,"DefaultCutConfig.json")
            shutil.copy(path_defaults, path)

    if not os.path.isfile(path):
        raise Exception(f"File not found: {path}")
    res =  load_json(path)
    return res["data"]

def save_cutdata(data):
    path  = persistent_state_path("CutConfig.json")
    json_file = {
        "version" : get_addin_version(),
        "data" : {
            "selectedBundleIndex" : data["selectedBundleIndex"],
            "bundles" : data["bundles"],
        }
    }
    save_json(path, json_file)

def load_cutdata_from_preset_naming():
    # TODO
    preset_naming = load_preset_naming()
    def make_bundle(key, val):
        bdl = {}
        bdl["name"] = key
        bdl["selectedToollibURLs"] = []
        bdl["preset_naming"] = val
        return bdl

    bundles = [make_bundle(key, val) for (key,val) in preset_naming.items()]
    default_material_index = -1
    for i in range(len(bundles)):
        name = bundles[i]["name"]
        if name == DEFAULT_MATERIAL:
            default_material_index = i
            break

    if default_material_index == -1:
        raise Exception("Could not find initial selected material")
    bundles[default_material_index]["selectedToollibURLs"] = [
        "toollibraryroot://Local/Toolpath Default - Do Not Edit"
    ]
    ret = {
        "selectedBundleIndex" : default_material_index,
        "bundles" : bundles
    }
    return ret

def is_valid_cutdata(data):
    bundle = get_current_bundle(data)
    if len(bundle["selectedToollibURLs"]) == 0:
        # something else
        return False
    return True

def get_current_bundle(data):
    index = data["selectedBundleIndex"]
    return data["bundles"][index]

class CuttingConfigTab:
    def __init__(self,
        tab,
        parent,
        cutdata,
        local_handlers,
        incomingFromHTML,
        config,
                 ) -> None:

        self.parent = parent
        self.config = config
        self.tab=tab
        self.cutdata = cutdata

        self.url_obj_by_str = {}
        self.toollib_by_url_str = {}
        self.toollib_json_by_url_str = {}
        self.node_by_url = {}
        self.preset_names_by_url_str = {}
        camManager = adsk.cam.CAMManager.get()
        self.fusionToolLibraries = camManager.libraryManager.toolLibraries
        self.build_toollib_forrest_and_initial_selection()


        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'CuttingConfigTab.html')
        assert os.path.exists(path)
        self.html_url = f"file:///{path}".replace("\\", "/")
        self.browser_command_input = tab.children.addBrowserCommandInput("id_ToolLibTabBrowser", "", self.html_url, 1000, 1000)
        self.local_handlers = local_handlers
        add_handler(incomingFromHTML, self.onHTMLEvent, local_handlers=local_handlers)

    def receiveMsg(self, msg):
        subtypekey = msg["subtypekey"]
        if subtypekey == "selectedBundleIndexSetFromSetupTab":
            selectedBundleIndex = msg["selectedBundleIndex"]
            self.cutdata["selectedBundleIndex"] = selectedBundleIndex
            self.browser_command_input.sendInfoToHTML(
                subtypekey, json.dumps(msg),
            )
        else:
            raise Exception(f"Unrecognized subtypekey: {subtypekey}")


    def onHTMLEvent(self, args):
        html_args = adsk.core.HTMLEventArgs.cast(args)
        if html_args.action == "CuttingConfigTab_getInitialData":
            args.returnData = json.dumps(self.cutdata)
        elif html_args.action == "MaterialTab_saveData":
            self.cutdata.update(json.loads(html_args.data))
            self.parent.receiveMsg({"subtypekey": "selectedBundleIndexSetFromMaterialTab", "selectedBundleIndex": self.cutdata["selectedBundleIndex"]})
            save_cutdata(self.cutdata)
        elif html_args.action == "ToolLibTab_selectionChanged":
            selected_urls = json.loads(html_args.data)
            bundle = get_current_bundle(self.cutdata)
            bundle["selectedToollibURLs"] = selected_urls
            nodes = []
            for url_str in selected_urls:
                node = self.node_by_url[url_str]
                nodes.append(node)
            save_cutdata(self.cutdata)
            self.parent.receiveMsg({"subtypekey": "ToolLibTab_selectionChanged"})
            args.returnData = json.dumps(nodes)
        elif html_args.action == "ToolLibTab_getPresetNames":
            urls = json.loads(html_args.data)
            preset_names_list = []
            for url_str in urls:
                node = self.node_by_url[url_str]
                self.add_preset_names_to_node(node)
                preset_names_list.append(node["preset_names"])
            args.returnData = json.dumps(preset_names_list)
        else:
            pass

    def get_results_and_update_config(self,inputs):
        bundle = get_current_bundle(self.cutdata)
        # toollibs
        toollibs = []
        toollibs_json = []
        for url in bundle["selectedToollibURLs"]:
            toollibs.append(self.get_toollib_by_url(url))
            toollibs_json.append(self.get_toollib_json_by_url(url))

        # material
        preset_naming=bundle["preset_naming"]
        material_name = bundle["name"]
        self.config["material_name"] = material_name

        return SimpleNamespace(toollibs_json=toollibs_json, 
                               material_name=material_name, 
                               preset_naming=preset_naming, 
                               toollibs=toollibs
                               )

    def selection_is_valid(self, args):
        bundle = get_current_bundle(self.cutdata)
        return len(bundle["selectedToollibURLs"]) > 0

    def resolve_url_obj(self, url):
        if isinstance(url, str):
            ret = self.url_obj_by_str.get(url, None)
            if ret is None:
                url *= ".tools"
                ret = self.url_obj_by_str[url]
        else:
            ret = url
            
        assert isinstance(ret, adsk.core.URL)
        return ret

    def resolve_url_str(self, url):
        if isinstance(url, adsk.core.URL):
            url = url.toString()
        assert isinstance(url, str)
        return url

    def get_toollib_by_url(self, url):
        url = self.resolve_url_str(url)
        toollib = self.toollib_by_url_str.get(url, None)
        if toollib is None:
            toollib = self.load_toollib_from_url(url)
            self.toollib_by_url_str[url] = toollib
        return toollib

    def get_toollib_json_by_url(self, url):
        url = self.resolve_url_str(url)
        toollib_json = self.toollib_json_by_url_str.get(url, None)
        if toollib_json is None:
            toolib = self.get_toollib_by_url(url)
            toollib_json = logic.jsonify_toollib(toolib)
            self.toollib_json_by_url_str[url] = toollib_json
        return toollib_json

    def get_preset_names_by_url(self, url):
        url = self.resolve_url_str(url)
        preset_names = self.preset_names_by_url_str.get(url, None)
        if preset_names is None:
            toollib_json = self.get_toollib_json_by_url(url)
            preset_names = get_preset_names_from_toollib_json(toollib_json)
            self.preset_names_by_url_str[url] = preset_names
        return preset_names

    def load_toollib_from_url(self, url):
        url = self.resolve_url_obj(url)
        toollib = self.fusionToolLibraries.toolLibraryAtURL(url)
        return toollib

    def init_tool_tree_node(self, url : adsk.core.URL, *, isLeaf : bool, name=None):
        assert isinstance(url, adsk.core.URL)
        self.url_obj_by_str[url.toString()] = url
        url_str = url.toString()
        if name is None:
            name = url.leafName
        if isLeaf:
            kind = "leaf"
        else:
            kind = "branch"

        node = {
            "name" : name,
            "url" : url_str,
            "kind" : kind,
        }
        self.node_by_url[url_str] = node
        if isLeaf:
            pass
        else:
            node["isExpanded"] = False
            node["children"] = []

        return node

    def add_preset_names_to_node(self, node):
        node["preset_names"] = self.get_preset_names_by_url(node["url"])
        return node

    def build_toollib_forrest_and_initial_selection(self):
        camManager = adsk.cam.CAMManager.get()
        tls = self.fusionToolLibraries

        root_nodes = []
        for (url, name) in [
            (tls.urlByLocation(adsk.cam.LibraryLocations.CloudLibraryLocation), "Cloud"), # pathName seems empty for Cloud
            (tls.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation), None),
            (tls.urlByLocation(adsk.cam.LibraryLocations.Fusion360LibraryLocation), "Fusion"),
        ]:
            if name is None:
                name = url.leafName

            root = self.init_tool_tree_node(url, isLeaf=False, name=name)
            self.add_tool_tree_children(root)
            root_nodes.append(root)

        self.cutdata["toollibForest"] = root_nodes
        return self

    def add_tool_tree_children(self, parent_node : dict):
        tls = self.fusionToolLibraries
        children = parent_node["children"]
        parent_url = self.url_obj_by_str[parent_node["url"]]

        for url in tls.childAssetURLs(parent_url):
            node = self.init_tool_tree_node(url, isLeaf=True)
            children.append(node)

        for url in tls.childFolderURLs(parent_url):
            node = self.init_tool_tree_node(url, isLeaf=False)
            self.add_tool_tree_children(node)
            children.append(node)
        return parent_node