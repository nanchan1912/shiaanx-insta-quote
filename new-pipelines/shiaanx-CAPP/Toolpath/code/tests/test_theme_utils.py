import unittest
from unittest.mock import Mock, patch, MagicMock
import sys

from .mock_adsk import setup_adsk_modules
setup_adsk_modules()

import adsk.core
from ..lib.theme_utils import get_theme


class TestGetTheme(unittest.TestCase):
    """Tests for get_theme function"""

    def setUp(self):
        # Reset mocks before each test
        self.mock_app = Mock()
        self.mock_prefs = Mock()
        self.mock_general_prefs = Mock()
        self.mock_app.preferences = self.mock_prefs
        self.mock_prefs.generalPreferences = self.mock_general_prefs

    @patch('Toolpath.code.lib.theme_utils.adsk.core.Application.get')
    def test_returns_light_gray(self, mock_get):
        """Test that LightGray theme is correctly identified"""
        mock_get.return_value = self.mock_app
        # Use the actual mock enum value
        self.mock_general_prefs.userInterfaceTheme = adsk.core.UserInterfaceThemes.LightGrayUserInterfaceTheme
        
        result = get_theme()
        
        self.assertEqual(result, "LightGray")

    @patch('Toolpath.code.lib.theme_utils.adsk.core.Application.get')
    def test_returns_dark_blue(self, mock_get):
        """Test that DarkBlue theme is correctly identified"""
        mock_get.return_value = self.mock_app
        self.mock_general_prefs.userInterfaceTheme = adsk.core.UserInterfaceThemes.DarkBlueUserInterfaceTheme
        
        result = get_theme()
        
        self.assertEqual(result, "DarkBlue")

    @patch('Toolpath.code.lib.theme_utils.adsk.core.Application.get')
    def test_returns_classic_fusion(self, mock_get):
        """Test that ClassicFusion theme is correctly identified"""
        mock_get.return_value = self.mock_app
        self.mock_general_prefs.userInterfaceTheme = adsk.core.UserInterfaceThemes.ClassicUserInterfaceTheme
        
        result = get_theme()
        
        self.assertEqual(result, "ClassicFusion")

    @patch('Toolpath.code.lib.theme_utils.get_system_dark_mode')
    @patch('Toolpath.code.lib.theme_utils.adsk.core.Application.get')
    def test_returns_match_device_theme(self, mock_get, mock_dark_mode):
        """Test that MatchDeviceTheme (enum value 4) resolves to DarkBlue when system is dark"""
        mock_get.return_value = self.mock_app
        mock_dark_mode.return_value = True  # System is in dark mode
        self.mock_general_prefs.userInterfaceTheme = 4
        
        result = get_theme()
        
        # MatchDeviceTheme resolves to DarkBlue when system is dark
        self.assertEqual(result, "DarkBlue")

    @patch('Toolpath.code.lib.theme_utils.adsk.core.Application.get')
    def test_returns_unknown_for_unrecognized_theme(self, mock_get):
        """Test that unknown themes return unknown_<value>"""
        mock_get.return_value = self.mock_app
        self.mock_general_prefs.userInterfaceTheme = 99
        
        result = get_theme()
        
        self.assertEqual(result, "unknown_99")

    @patch('Toolpath.code.lib.theme_utils.log')
    @patch('Toolpath.code.lib.theme_utils.adsk.core.Application.get')
    def test_returns_unknown_on_exception(self, mock_get, mock_log):
        """Test that exceptions are caught and 'unknown' is returned"""
        mock_get.side_effect = Exception("Test error")
        
        result = get_theme()
        
        self.assertEqual(result, "unknown")
        mock_log.assert_called_once()


if __name__ == '__main__':
    unittest.main()
