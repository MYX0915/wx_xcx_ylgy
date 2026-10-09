"""UFO rules, captured operation parity, planning and click boundaries."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_captures import decode_map_file
from lightning import (LABELS, LightningBoard, identify_mechanics, map_kind,
                       replay_lightning, solve_lightning)
from map_capture import get_current_map
from play_game import execute, resume, save, UFO_WAIT_SECONDS
from seed_map import assign_types
from solve_map import load_cards
from test_lightning_map import lightning_map
from test_map_capture import Captures, record


def small_map(threshold=2):
    m = lightning_map()
    m['goldBlockData'] = {'collectCount': threshold, 'blockList': [{'val': 17, 'count': 0}]}
    return assign_types(m, [1, 2, 3, 4])


def captured_fixture():
    fixture = json.loads((Path(__file__).parent / 'fixtures/lightning-1071.json').read_text())
    m = {'goldBlockData': fixture['goldBlockData'], '_shuffleState': fixture['shuffleState'],
         'levelData': {}}
    for card_id, kind, x, y, z in fixture['cards']:
        m['levelData'].setdefault(str(z), []).append(
            dict(id=f'{z}-{x}-{y}', cardId=card_id, type=kind, rolNum=x, rowNum=y, layerNum=z))
    return fixture, m


class ClassificationTests(unittest.TestCase):
    def test_three_map_labels(self):
        self.assertEqual(set(LABELS.values()), {'普通局', '大世界普通局', '闪电飞碟局'})
        ordinary = lightning_map()
        for mode in ('daily', 'world'):
            self.assertEqual(map_kind(ordinary, mode), mode + '_ordinary')
            self.assertEqual(map_kind(small_map(), mode), 'lightning_ufo')
        with self.assertRaises(ValueError):
            map_kind(ordinary, 'unknown')

    def test_parser_preserves_ufo_configuration(self):
        raw = b'.map' + bytes(17) + bytes.fromhex('3206080a12020811')
        m = decode_map_file(SimpleNamespace(read_bytes=lambda: raw, name='test.map'))
        self.assertEqual(m['goldBlockData'],
                         {'collectCount': 10, 'blockList': [{'val': 17, 'count': 0}]})
        self.assertEqual(identify_mechanics(m)['kind'], 'lightning_ufo')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            decode_map_file(SimpleNamespace(read_bytes=lambda: raw + raw[21:], name='test.map'))

    def test_invalid_configuration_and_random_state_fail_closed(self):
        for gold in ({}, {'collectCount': True}, {'collectCount': -1, 'blockList': []},
                     {'collectCount': 10, 'blockList': [{'val': 18}]},
                     {'collectCount': 10, 'blockList': []}):
            with self.subTest(gold=gold), self.assertRaises(ValueError):
                identify_mechanics({'goldBlockData': gold})
        m = small_map()
        del m['_shuffleState']
        with self.assertRaises(ValueError):
            LightningBoard(m, load_cards(m))

    def test_runtime_requires_verified_random_state_and_initial_gold(self):
        m = small_map()
        nodes = m['levelData']['1']
        blocks = [dict(type=n['type'], colNum=n['rolNum'], rowNum=0, layerNum=1,
                       moldType=1, blockId=i, metaType=3 if n['type'] == 17 else 0,
                       metaData=4 if n['type'] == 17 else 0, AreaType=0)
                  for i, n in enumerate(nodes)]
        state = {'crushMapInfo': {'gameType': 6}, 'fullSync': True,
                 'gameState': {'crushedBlockCount': 0, 'crushAreaBlocks': [],
                               'moveOutAreaBlocks': [], 'allBlockRuntimeData': blocks}}
        static = lightning_map()
        static['goldBlockData'] = m['goldBlockData']
        payload = {'err_code': 0, 'data': {'map_md5': ['a'*32, 'b'*32],
                   'match_data': 'runtime', 'map_seed': [1, 2, 3, 4]}}
        client = Captures([record(1, 'world/game_start', 6, payload=payload)])
        with patch('map_capture.read_static_map', side_effect=lambda *a: deepcopy(static)), \
             patch('map_capture.decompress_match', return_value=[{}, state]):
            result = get_current_map(client)
            self.assertEqual(result['_source']['mapKind'], 'lightning_ufo')
            self.assertEqual(result['_source']['mode'], 'world')
            self.assertEqual(result['_source']['typeSource'], 'match_data')
            self.assertEqual(result['_shuffleState'], m['_shuffleState'])
            blocks[1]['blockId'] = 100
            with self.assertRaisesRegex(ValueError, 'differs'):
                get_current_map(client)
        for meta, energy in ((2, 4), (3, 2)):
            bad = deepcopy(m)
            bad['levelData']['1'][0].update(metaType=meta, metaData=energy)
            with self.assertRaises(ValueError):
                load_cards(bad)


class LightningRulesTests(unittest.TestCase):
    def test_captured_first_145_clicks_and_ufo_prefix(self):
        fixture, m = captured_fixture()
        cards = load_cards(m)
        board = LightningBoard(m, cards)
        for i in fixture['order'][:-1]:
            self.assertIsNone(board.click(i)['ufo'])
        step = board.click(fixture['order'][-1])
        expected = fixture['capturedUfo']
        self.assertEqual(step['step'], 145)
        self.assertEqual(step['ufo']['randomDraws'], expected['randomDraws'])
        self.assertEqual(step['ufo']['removedCardIds'][:8], expected['removedPrefix'])
        self.assertEqual(len(step['ufo']['removedCardIds']), 12)
        self.assertEqual(step['trayAfter'], [])
        self.assertEqual(step['remainingCards'], 90)
        self.assertFalse(board.energy)
        self.assertTrue(board.used)

    def test_empty_tray_ufo_has_extra_random_draw(self):
        m = small_map(3)
        board = LightningBoard(m, load_cards(m))
        for i in (0, 3, 8):
            step = board.click(i)
        self.assertEqual(step['ufo']['randomDraws'], 4)
        self.assertEqual(len(step['ufo']['removedCardIds']), 3)
        self.assertEqual(len(board.remaining), 3)

    def test_energy_expires_without_changing_type(self):
        m = small_map(3)
        board = LightningBoard(m, load_cards(m))
        for i in (1, 2, 4, 5):
            board.click(i)
        board.click(0)
        self.assertEqual(board.collected, 0)
        self.assertEqual(board.cards[0].type, 17)
        self.assertFalse(board.used)

    def test_replan_removes_board_and_tray_cards(self):
        m = small_map()
        cards = load_cards(m)
        # Trigger on the second lightning; the third is removed without a click.
        first = {'status': 'solved', 'order': [0, 3, 8, 1, 2, 4, 5, 6, 7]}
        from solve_map import solve
        calls = []
        def solve_stage(remaining, seconds):
            calls.append(len(remaining))
            return first if len(calls) == 1 else solve(remaining, seconds)
        with patch('solve_map.solve', side_effect=solve_stage):
            result = solve_lightning(m, cards, 5)
        self.assertEqual(calls, [9, 6])
        self.assertEqual(result['status'], 'solved')
        self.assertEqual(result['ufoEvents'], 1)
        self.assertEqual(result['automaticRemovedCards'], 3)
        self.assertEqual(len(result['order']), 8)
        self.assertNotIn(8, result['order'])
        self.assertTrue(replay_lightning(m, cards, result['order'])['verified'])
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            replay_lightning(m, cards, result['order'][:-1])
        with self.assertRaisesRegex(ValueError, 'Missing'):
            replay_lightning(m, cards, [0, 3, 8])

    def test_timeout_never_returns_partial_order(self):
        m = small_map()
        with patch('solve_map.solve', side_effect=[
                {'status': 'solved', 'order': [0, 3, 8]},
                {'status': 'timeout', 'order': []}]):
            result = solve_lightning(m, load_cards(m), 10)
        self.assertEqual(result, {'status': 'timeout', 'order': []})

    def test_no_ufo_trigger_retains_full_plan(self):
        m = small_map(10)
        result = solve_lightning(m, load_cards(m), 5)
        self.assertTrue(result['verified'])
        self.assertEqual(len(result['order']), 9)
        self.assertEqual(result['automaticBoardRemovedCards'], 0)
        self.assertEqual(result['ufoEvents'], 0)

    def test_seventh_non_matching_card_is_rejected(self):
        m = small_map(10)
        cards = load_cards(m)
        board = LightningBoard(m, cards)
        board.tray = [1, 1, 2, 2, 4, 4]
        with self.assertRaisesRegex(ValueError, 'Seven cards'):
            board.click(0)


class LightningExecutionTests(unittest.TestCase):
    def test_old_plan_cannot_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            save(folder / 'map.json', small_map())
            with self.assertRaisesRegex(ValueError, '旧版闪电局'):
                resume(folder)

    def test_new_plan_resumes_with_ufo_replay(self):
        m = small_map()
        m['_mechanics'] = identify_mechanics(m)
        m['_source'] = {'mode': 'world'}
        cards = load_cards(m)
        result = solve_lightning(m, cards, 5)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            save(folder / 'map.json', m)
            save(folder / 'solution.json', result)
            save(folder / 'progress.json', {'nextStep': 1, 'inFlight': False})
            _, _, _, restored, progress = resume(folder)
            self.assertEqual(restored['order'], result['order'])
            self.assertEqual(restored['automaticRemovedCards'], result['automaticRemovedCards'])
            self.assertEqual(progress['nextStep'], 1)
            result['mechanicsVersion'] = 0
            save(folder / 'solution.json', result)
            with self.assertRaisesRegex(ValueError, '旧版飞碟计划'):
                resume(folder)

    def test_wait_before_next_click_and_interrupted_wait_is_uncertain(self):
        for interrupt in (False, True):
            with self.subTest(interrupt=interrupt), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = {'id': 1, 'pid': 10, 'title': '羊了个羊',
                          'bounds': {'X': 0, 'Y': 0, 'Width': 350, 'Height': 665}}
                cards = [SimpleNamespace(x=i, y=0, type=17, name='闪电') for i in range(3)]
                result = {'order': [0, 1], 'steps': [
                    {'trayAfter': [], 'ufo': {'removedCardIds': [2]}}, {'trayAfter': []}]}
                progress = {'nextStep': 0, 'inFlight': False}
                trace = []
                def native(command, *args):
                    if command == 'click':
                        trace.append('click')
                    return {'windows': [window]} if command == 'inspect' else {'x': 0, 'y': 0}
                def sleep(seconds):
                    if seconds == 1:
                        trace.append('wait')
                        if interrupt:
                            raise KeyboardInterrupt()
                with patch('play_game.native', side_effect=native), \
                     patch('play_game.check_stop'), patch('play_game.time.sleep', side_effect=sleep), \
                     patch('builtins.print'):
                    args = (Mock(), window, folder, {'_source': {}}, cards, result, progress,
                            {'xScale': 1, 'yScale': 1, 'xOffset': 0, 'yOffset': 0},
                            SimpleNamespace(max_clicks=500, delay=0.5))
                    if interrupt:
                        with self.assertRaises(KeyboardInterrupt):
                            execute(*args)
                        self.assertEqual(trace.count('click'), 1)
                        self.assertTrue(progress['inFlight'])
                        self.assertIn('pendingUfo', progress)
                    else:
                        execute(*args)
                        self.assertEqual(trace, ['click'] + ['wait'] * UFO_WAIT_SECONDS + ['click'])
                        self.assertEqual(progress['nextStep'], 2)
                        self.assertEqual(progress['status'], 'sequence_completed')
                        self.assertFalse(progress['inFlight'])


if __name__ == '__main__':
    unittest.main()
