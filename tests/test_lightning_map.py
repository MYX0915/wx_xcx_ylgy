"""Fixed lightning tiles must not change seeded ordinary tile assignment."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from map_capture import apply_type_names, get_current_map
from seed_map import assign_types, static_type_counts
from solve_map import load_cards, replay, solve
from reverse_search import reverse_search
from test_map_capture import Captures, record


def lightning_map():
    nodes = [dict(id=f'1-{i * 8}-0', type=17 if i in (0, 3, 8) else 0,
                  rolNum=i * 8, rowNum=0, layerNum=1, moldType=1) for i in range(9)]
    return {'blockTypeData': {'1': 1, '2': 1}, 'levelData': {'1': nodes}}


class LightningMapTests(unittest.TestCase):
    def test_fixed_tiles_preserve_seed_assignment_and_unique_ids(self):
        original = lightning_map()
        saved = deepcopy(original)
        ordinary = deepcopy(original)
        ordinary['levelData']['1'] = [n for n in ordinary['levelData']['1'] if n['type'] == 0]
        expected = assign_types(ordinary, [1, 2, 3, 4])
        actual = assign_types(original, [1, 2, 3, 4])
        nodes = actual['levelData']['1']
        self.assertEqual([n['type'] for n in nodes if n['type'] != 17],
                         [n['type'] for n in expected['levelData']['1']])
        self.assertEqual([n['id'] for n in nodes if n['type'] == 17],
                         [n['id'] for n in original['levelData']['1'] if n['type'] == 17])
        self.assertEqual([n['cardId'] for n in nodes], list(range(9)))
        self.assertEqual(Counter(n['type'] for n in nodes), {1: 3, 2: 3, 17: 3})
        self.assertEqual(original, saved)

    def test_unknown_fixed_types_and_invalid_counts_remain_rejected(self):
        for kind in (1, 16, 18, '17', True):
            m = lightning_map()
            m['levelData']['1'][0]['type'] = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                assign_types(m, [1, 2, 3, 4])
        for index in (0, 1):
            m = lightning_map()
            m['levelData']['1'].pop(index)
            with self.subTest(removed=index), self.assertRaises(ValueError):
                assign_types(m, [1, 2, 3, 4])

    def test_fixed_lightning_is_not_counted_twice(self):
        m = lightning_map()
        m['blockTypeData']['17'] = 1
        with self.assertRaises(ValueError):
            static_type_counts(m)

    def test_lightning_can_also_appear_in_the_seed_pool(self):
        m = lightning_map()
        m['blockTypeData']['17'] = 1
        m['levelData']['1'].extend(
            dict(id=f'1-{i * 8}-0', type=0, rolNum=i * 8, rowNum=0, layerNum=1, moldType=1)
            for i in range(9, 12))
        self.assertEqual(static_type_counts(m), {1: 3, 2: 3, 17: 6})
        result = assign_types(m, [1, 2, 3, 4])
        self.assertEqual(Counter(n['type'] for n in result['levelData']['1']), {1: 3, 2: 3, 17: 6})

    def test_seed_pipeline_counts_and_names_fixed_lightning(self):
        payload = {'err_code': 0, 'data': {'map_md5': ['a'*32, 'b'*32],
                                          'map_seed': [1, 2, 3, 4], 'need_seed': False}}
        client = Captures([record(1, 'world/game_start', 6, payload=payload)])
        with patch('map_capture.read_static_map', return_value=lightning_map()):
            result = get_current_map(client)
        cards = load_cards(result)
        self.assertEqual(Counter(c.type for c in cards), {1: 3, 2: 3, 17: 3})
        self.assertTrue(all(c.name == '闪电' for c in cards if c.type == 17))
        self.assertEqual(result['_source']['typeSource'], 'seed')

    def test_runtime_pipeline_preserves_fixed_lightning_positions(self):
        nodes = assign_types(lightning_map(), [1, 2, 3, 4])['levelData']['1']
        blocks = [dict(type=n['type'], colNum=n['rolNum'], rowNum=0, layerNum=1,
                       moldType=1, blockId=i, metaType=0, metaData=0, AreaType=0)
                  for i, n in enumerate(nodes)]
        state = {'crushMapInfo': {'gameType': 3}, 'fullSync': True,
                 'gameState': {'crushedBlockCount': 0, 'crushAreaBlocks': [],
                               'moveOutAreaBlocks': [], 'allBlockRuntimeData': blocks}}
        payload = {'err_code': 0, 'data': {'map_md5': ['a'*32, 'b'*32], 'match_data': 'runtime'}}
        client = Captures([record(1, 'map_info_ex', 3, payload=payload)])
        with patch('map_capture.read_static_map', side_effect=lambda *args: lightning_map()), \
             patch('map_capture.decompress_match', return_value=[{}, state]):
            result = get_current_map(client)
            self.assertEqual(Counter(c.type for c in load_cards(result)), {1: 3, 2: 3, 17: 3})
            blocks[0]['type'], blocks[1]['type'] = blocks[1]['type'], blocks[0]['type']
            with self.assertRaisesRegex(ValueError, 'fixed lightning'):
                get_current_map(client)

    def test_both_solvers_eliminate_lightning_as_a_triple(self):
        m = assign_types(lightning_map(), [1, 2, 3, 4])
        apply_type_names(m, 'world')
        cards = load_cards(m)
        for solver in (solve, reverse_search):
            with self.subTest(solver=solver.__name__):
                result = solver(cards, 5)
                self.assertEqual(result['status'], 'solved')
                verified = replay(cards, result['order'])
                self.assertTrue(verified['verified'])
                self.assertEqual(verified['triples'], 3)
                self.assertEqual(len(verified['steps']), 9)
                self.assertEqual(verified['steps'][-1]['trayAfter'], [])


if __name__ == '__main__':
    unittest.main()
