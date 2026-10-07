"""Read-only regressions for captured-map decoding and click preconditions."""

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from PIL import Image

from board_vision import calibrate, load_templates, verify_screen
from map_capture import decompress_match
from play_game import check_stop, select_window
from solve_map import load_cards


class CaptureTests(unittest.TestCase):
    def test_standard_decoder_matches_client_decoder(self):
        source = json.loads((ROOT/'captures/82136-map-evidence.json').read_text())
        expected = json.loads((ROOT/'decoded/82136-match-data-decoded.json').read_text())
        self.assertEqual(decompress_match(source['mapInfo']['match_data']), expected)

    def test_empty_match_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, 'refusing to reuse'):
            decompress_match('')


class VisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = load_cards(json.loads((ROOT/'decoded/82136-level-900081-map-with-names.json').read_text()))
        cls.image = Image.open(ROOT/'captures/automation-window.png').convert('RGB')
        cls.calibration = calibrate(cls.image, cls.cards)
        cls.bank = load_templates(ROOT/'decoded/daily-sprite-templates.npz')
        cls.remaining = set(range(len(cls.cards)))

    def test_current_board_matches_reference(self):
        result = verify_screen(self.image, self.cards, self.remaining, [], self.calibration, self.bank)
        self.assertEqual(result['knownChecks'], 26)

    def test_wrong_card_type_is_rejected(self):
        changed = [replace(c, type=11) if c.id == '23-40-4' else c for c in self.cards]
        with self.assertRaisesRegex(ValueError, 'Card image differs'):
            verify_screen(self.image, changed, self.remaining, [], self.calibration, self.bank)

    def test_missing_click_is_rejected(self):
        remaining = self.remaining - {i for i, c in enumerate(self.cards) if c.id == '23-40-4'}
        with self.assertRaises(ValueError):
            verify_screen(self.image, self.cards, remaining, [5], self.calibration, self.bank)

    def test_nonempty_tray_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Tray count differs'):
            verify_screen(self.image, self.cards, self.remaining, [5], self.calibration, self.bank)

    def test_calibration_shift_is_rejected(self):
        wrong = {**self.calibration, 'x': [self.calibration['x'][0], self.calibration['x'][1]+20]}
        with self.assertRaisesRegex(ValueError, 'Missing visible card'):
            verify_screen(self.image, self.cards, self.remaining, [], wrong, self.bank)


class StopTests(unittest.TestCase):
    def test_ambiguous_game_windows_are_rejected(self):
        window = {'id': 1, 'title': '羊了个羊：星球'}
        with self.assertRaises(ValueError):
            select_window({'windows': [window, window]}, None)

    @patch('play_game.native', return_value={'x': 20, 'y': 30})
    def test_manual_mouse_move_stops(self, native):
        with self.assertRaisesRegex(InterruptedError, 'Mouse moved'):
            check_stop(ROOT/'unused-run-path', {'x': 10, 'y': 30})

if __name__ == '__main__':
    unittest.main()
