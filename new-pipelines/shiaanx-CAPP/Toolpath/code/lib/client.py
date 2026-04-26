# simple http client for json based communication with a server
from .fusion_utils import Fusion, get_f3d_content_base64
from .general_utils import load_config, save_json, log
from .general_utils import get_addin_version, addin_root_rpath
import urllib.request
import ssl
import json
import time
import os
from datetime import datetime, timezone
import adsk.core

class Client():
    def __init__(self, config=load_config()):
        self.config = config
        self.addin_version = get_addin_version()

    def use_local_server(self, data):
        if not self.config["use_FusionTP_server"]:
            return False
        subtypekey = data["subtypekey"]
        if subtypekey == "RequestDesignAdvisor":
            return False
        elif subtypekey == "RequestQuoteAssistant":
            if data["product"] == "CA":
                return True
            elif data["product"] == "QA":
                return False
            else:
                raise Exception("Unknown product: " + data["product"])
        elif subtypekey == "RequestPluginVersion":
            return False
        elif subtypekey == "RequestPluginUpdate":
            return True
        elif subtypekey == "RequestProgramProgress":
            return False
        else:
            raise Exception("Unknown subtypekey: " + subtypekey)
            return True

    def build_request_url(self, data):
        if self.use_local_server(data):
            return self.config["server_url"]

        app_environment = self.config["app_environment"]
        if app_environment == "production":
            prefix = "https://app.toolpath.com/"
        elif app_environment == "staging":
            prefix = "https://app.staging.toolpath.com/"
        else:
            prefix = f"{app_environment}/"

        subtypekey = data["subtypekey"]
        if subtypekey in ["RequestDesignAdvisor", "RequestQuoteAssistant"]:
            return prefix + "api/fusion-plugin/upload-part"
        elif subtypekey in ["RequestFusionOpsQA", "RequestProgramProgress"]:
            return data["polling_link"]
        elif subtypekey == "RequestImportProgram":
            return prefix + "api/app-server/programs/access-key"
        else:
            return prefix + "api/fusion-plugin/app-server-proxy"

    def build_request_json(self, data : dict) -> dict:
        data["plugin_version"] = get_addin_version()
        if self.use_local_server(data):
            return data

        user = Fusion().getUser()
        subtypekey = data["subtypekey"]
        if subtypekey in ["RequestDesignAdvisor", "RequestQuoteAssistant","RequestFusionOpsQA"]:
            ret = {
                "appServerHost" : self.config["appServerHost"],
                "fusionUserId" : user.userId,
                "fusionUserEmail" : user.email,
            }
            ret.update(data)
            return ret
        elif subtypekey == "RequestImportProgram":
            return data
        elif subtypekey == "RequestProgramProgress":
            # Polling endpoint just needs POST to URL, minimal body
            return {}
        req_json = {
            "request" : {
                "request" : data,
                "fusionUserId" : user.userId,
                "fusionUserEmail" : user.email,
            },
            "appServerHost" : self.config["appServerHost"],
            "action" : "fusion_request",
        }
        return req_json

    def request(self, data, method=None):
        url = self.build_request_url(data)
        req_json = self.build_request_json(data)

        req_json_str = json.dumps(req_json)
        req_json_bytes = req_json_str.encode('utf-8')
        headers = {
            'Content-Length': len(req_json_bytes)
        }
        subtypekey = data["subtypekey"]
        if subtypekey == "RequestImportProgram":
            headers["Content-Type"] = "text/plain"
        else:
            headers["Content-Type"] = "application/json"
        if not self.use_local_server(data):
            api_key = self.config["api_key"]
            if api_key is None:
                # this key has restricted access
                api_key = "ZxcOW1WWDGucHnc8kfM4rlWertq59bsJAHFzMlUxiKHTfJyJ2HMycMYQLtxKDmJt"
            headers["api-key"] = api_key
        log(f"Send request {url =}")
        request = urllib.request.Request(url, data=req_json_bytes, headers=headers, method=method)
        save_json(addin_root_rpath("last_request.json"), req_json)
        need_save_history : bool = self.config["save_request_history"]
        if need_save_history:
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S-%f') # format to get valid windows directory name
            filename_request_history = f"{timestamp}_request.json"
            filename_response_history = f"{timestamp}_response.json"
            log(f"Saving request history {filename_request_history}.")
            save_json(addin_root_rpath("requests_history", filename_request_history), req_json)

        ctx=None # safer to assume None
        if self.config["use_SSL_verify"]==False:
            ctx=ssl._create_unverified_context() # turns off SSL verification!!! ONLY FOR DEVELOPMENT

        response = urllib.request.urlopen(request, timeout=3600, context=ctx)
        status_code = response.getcode()
        if not status_code == 200:
            raise Exception(f"Bad status_code: {status_code}")

        resp_json_str = response.read().decode('utf-8')
        resp_json = json.loads(resp_json_str)
        save_json(addin_root_rpath("last_response.json"), resp_json)
        if need_save_history:
            log(f"Saving response history {filename_response_history}.")
            save_json(addin_root_rpath("requests_history", filename_response_history), resp_json)

        # handle error
        if resp_json.get("error", False):
            self.handle_error(resp_json, req_json)

        return resp_json


    def handle_error(self, resp_json, req_json):
        if "msg" in resp_json.keys():
            msg = resp_json["msg"]
        else:
            msg = json.dumps(resp_json)

        skip_bug_report = self.config["use_FusionTP_server"]
        if skip_bug_report:
            raise Exception(msg)
        else:
            log(msg)

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S') # format to get valid windows directory name
        dir = addin_root_rpath("bug_reports", timestamp)
        os.makedirs(dir)
        with open(os.path.join(dir, "error.txt"), "w") as file:
            file.write(msg)

        path = os.path.join(dir, "response.json")
        save_json(path, resp_json)

        if self.config["request_include_f3d"]:
            fusion = Fusion()
            f3d_content = get_f3d_content_base64(fusion)
            if "request" in req_json.keys():
                req_json["request"]["request"]["f3d_content_base64"] = f3d_content
            elif "payload_specific_data" in req_json.keys():
                req_json["payload_specific_data"]["f3d_content_base64"] = f3d_content

        path = os.path.join(dir, "request.json")
        save_json(path, req_json)


        raise Exception(f"""
        Error: A bug report can be found here:
        {dir}
        You can upload that directory to:
        https://github.com/toolpath/FusionTP.jl/issues
        """
        )