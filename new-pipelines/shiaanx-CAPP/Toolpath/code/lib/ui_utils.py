import adsk.core
import os
import subprocess
import re
import platform
from .general_utils import handle_error, isdebug

app = adsk.core.Application.get()
ui = app.userInterface


def open_file_dialog(filter: str, title: str) -> str:
    try:
        file_dialog = ui.createFileDialog()
        file_dialog.filter = filter
        file_dialog.title = title
        if file_dialog.showOpen() == adsk.core.DialogResults.DialogOK:
            return file_dialog.filename
        else:
            raise Exception('Could not load file')
    except Exception as e:
        handle_error(e)


def get_clipboard_text():
    try:
        system = platform.system()
        if system == 'Darwin':
            cmd = ['pbpaste']
        elif system == 'Windows':
            cmd = ['powershell', '-noprofile', '-command', 'Get-Clipboard']
            # Suppress window popup on Windows
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return subprocess.check_output(cmd, startupinfo=startupinfo, universal_newlines=True).strip()
        else:
            # Unable to use macOS or Windows clipboard, return None
            return None
        return subprocess.check_output(cmd, universal_newlines=True).strip()
    except Exception as e:
        from .general_utils import log
        log(f"Error getting clipboard text: {e}")
        return None


def clean_key(value: str) -> str:
    if not isinstance(value, str):
        return value
    return value.strip().strip("'\"")


def validate_key(share_key: str) -> bool:
    if not isinstance(share_key, str):
        return False
    if isdebug() and os.path.isfile(share_key):
        return True
    return bool(share_key and re.fullmatch(r'[Tt][Pp][A-Za-z0-9]{62}', share_key))


def redact_key_obscure_length(key: str, keep_start: int = 3, keep_end: int = 3, max_total_length: int = 10, mask_char: str = '*') -> str:
    """
    Redacts a key by keeping a few characters at the start and end,
    masking the middle with asterisks, and obscuring the original length
    by limiting the total output length.

    :param key: The original string key.
    :param keep_start: Number of characters to keep at the beginning.
    :param keep_end: Number of characters to keep at the end.
    :param max_total_length: Max length of the final redacted string.
    :param mask_char: Character used for redaction.
    :return: Redacted and length-obscured string.
    """
    if len(key) <= keep_start + keep_end:
        return mask_char * min(len(key), max_total_length)

    # Initial kept parts
    start = key[:keep_start]
    end = key[-keep_end:]

    # Max possible mask length given total constraint
    available_mask_length = max_total_length - keep_start - keep_end

    # If total length is too short to keep both start and end
    if available_mask_length < 0:
        # fallback: just return truncated key with some masking
        return (key[:max_total_length - 1] + mask_char) if max_total_length > 1 else mask_char

    middle = mask_char * available_mask_length
    return start + middle + end
