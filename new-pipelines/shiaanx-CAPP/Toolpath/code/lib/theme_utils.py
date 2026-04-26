import adsk.core
import sys
from .general_utils import log


def get_system_dark_mode():
    """Detect if the operating system is using dark mode.
    
    Returns:
        bool: True if system is in dark mode, False if light mode
    """
    try:
        if sys.platform == "darwin":
            # macOS: Check AppleInterfaceStyle default
            import subprocess
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True
            )
            # Returns "Dark" if dark mode, error/empty if light mode
            return result.stdout.strip().lower() == "dark"
        
        elif sys.platform == "win32":
            # Windows: Check registry for AppsUseLightTheme
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            # 0 = dark mode, 1 = light mode
            return value == 0
        
        else:
            # Linux/other: Default to dark mode
            return True
            
    except Exception as e:
        log(f"Error detecting system dark mode: {e}", force_console=True)
        # Default to dark mode on error
        return True


def get_theme():
    """Get Fusion 360 UI theme, resolving MatchDeviceTheme to actual light/dark.

    Returns:
        str: "LightGray", "DarkBlue", "ClassicFusion", or "unknown_<enum_value>"
        Note: "MatchDeviceTheme" is resolved to "DarkBlue" or "LightGray" based on system preference
    """
    theme = "unknown"
    try:
        app = adsk.core.Application.get()
        prefs = app.preferences
        general_prefs = prefs.generalPreferences
        theme_enum = general_prefs.userInterfaceTheme

        # Map enum values to readable names
        # Fusion 360 themes: Light Gray, Dark Blue, Classic Fusion, Match Device Theme
        theme_map = {
            adsk.core.UserInterfaceThemes.LightGrayUserInterfaceTheme: "LightGray",
            adsk.core.UserInterfaceThemes.DarkBlueUserInterfaceTheme: "DarkBlue",
            adsk.core.UserInterfaceThemes.ClassicUserInterfaceTheme: "ClassicFusion",
            4: "MatchDeviceTheme",
        }

        theme = theme_map.get(theme_enum, f"unknown_{theme_enum}")
        
        # Resolve MatchDeviceTheme to actual theme based on system preference
        if theme == "MatchDeviceTheme":
            is_dark = get_system_dark_mode()
            theme = "DarkBlue" if is_dark else "LightGray"
            
    except Exception as e:
        log(f"Error getting theme: {e}", force_console=True)
    return theme
