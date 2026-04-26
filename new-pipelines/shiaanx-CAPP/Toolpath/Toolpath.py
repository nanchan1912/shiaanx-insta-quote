import shutil
import os

def messageBox(msg):
    import adsk.core
    app = adsk.core.Application.get()
    ui = app.userInterface
    ui.messageBox(msg)

plugin_root = os.path.dirname(os.path.abspath(__file__))
assert os.path.isdir(plugin_root)
path_new_launcher = os.path.join(plugin_root, "new_code", "launcher.py")
path_current_launcher = os.path.join(plugin_root, "code", "launcher.py")
fail = False
if os.path.exists(path_new_launcher):
    src = path_new_launcher
elif os.path.exists(path_current_launcher):
    src = path_current_launcher
else:
    fail = True
    messageBox(f"Failed to locate launcher. You may need to reinstall the plugin.")

dst = os.path.join(plugin_root, "launcher.py")

try:
    if (not fail) and os.path.exists(dst):
        os.remove(dst)
except Exception as err:
    fail = True
    msg = f"Failed to remove {dst} launcher.py. You may need to manually delete that file, or reinstall the plugin."
    messageBox(msg)

try:
    if not fail:
        shutil.copy(src, dst) # TODO could windows have issues here? Could dst be locked, say by another open fusion?
except Exception as err:
    fail = True
    msg = "Failed to copy launcher.py. You may need to reinstall the plugin."
    messageBox(msg)


if not fail:
    from .launcher import run, stop
