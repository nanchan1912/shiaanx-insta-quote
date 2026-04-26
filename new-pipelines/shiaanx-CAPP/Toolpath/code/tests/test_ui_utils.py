import unittest
from unittest.mock import Mock, patch
import sys

from .mock_adsk import setup_adsk_modules
setup_adsk_modules()

from ..lib.ui_utils import validate_key


class TestValidateKey(unittest.TestCase):
    @patch('Toolpath.code.lib.ui_utils.isdebug', return_value=False)
    def test_valid_key_lowercase(self, mock_isdebug):
        valid_key = "tpB2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0U1V2W3X4Y5Z6A7B8C9D0E1F2"
        self.assertTrue(validate_key(valid_key))

    @patch('Toolpath.code.lib.ui_utils.isdebug', return_value=False)
    def test_valid_key(self, mock_isdebug):
        valid_key = "TPB2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0U1V2W3X4Y5Z6A7B8C9D0E1F2"
        self.assertTrue(validate_key(valid_key))

    @patch('Toolpath.code.lib.ui_utils.isdebug', return_value=False)
    def test_invalid_key_no_tp(self, mock_isdebug):
        valid_key = "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0U1V2W3X4Y5Z6A7B8C9D0E1F2"
        self.assertFalse(validate_key(valid_key))

    @patch('Toolpath.code.lib.ui_utils.isdebug', return_value=False)
    def test_invalid_key_too_short(self, mock_isdebug):
        short_key = "A1B2C3"
        self.assertFalse(validate_key(short_key))

    @patch('Toolpath.code.lib.ui_utils.isdebug', return_value=False)
    def test_invalid_key_special_chars(self, mock_isdebug):
        key_with_symbols = "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0U1V2W3X4Y5Z6A7B8C9D0E1$*"
        self.assertFalse(validate_key(key_with_symbols))

    def test_invalid_key_not_string(self):
        not_a_string = 1234567890123456789012345678901234567890123456789012345678901234
        self.assertFalse(validate_key(not_a_string))

    @patch('Toolpath.code.lib.ui_utils.isdebug', return_value=False)
    def test_invalid_key_empty(self, mock_isdebug):
        self.assertFalse(validate_key(""))

    def test_invalid_key_none(self):
        self.assertFalse(validate_key(None))
