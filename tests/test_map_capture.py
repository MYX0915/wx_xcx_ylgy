"""Cross-mode freshness, captured seed binding, and initial-board regressions."""

import base64
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from map_capture import Reqable, game_mode, get_current_map, merge_runtime
from seed_map import assign_types, decode_seed_ack, recover_seed


def record(ident, endpoint, match_type=None, body=None, payload=None):
    query = {'t': 'synthetic-session'}
    if match_type is not None:
        query['matchType'] = str(match_type)
    return {'id': ident, 'uid': f'uid-{ident}',
            'url': f'https://cat-match.easygame2021.com/sheep/v1/game/{endpoint}?' + urlencode(query),
            'request': {'body': {'encoding': 'utf8', 'text': urlencode(body)} if body else None},
            'response': {'code': 200, 'body': {'encoding': 'utf8', 'text': json.dumps(payload or {})}}}


class Captures(Reqable):
    def __init__(self, records):
        self.records = {r['id']: r for r in records}

    def call(self, name, arguments):
        return self.records[arguments['id']]

    def ids(self, filters):
        pattern = next((f['pattern'] for f in filters if f['type'] == 'keyword'), '')
        return [i for i, r in self.records.items() if re.search(pattern, r['url'])]


def small_map():
    return {'widthNum': 8, 'heightNum': 10, 'blockTypeData': {'1': 1},
            'levelData': {'1': [dict(id=f'1-{x}-0', type=0, rolNum=x, rowNum=0,
                                     layerNum=1, moldType=1) for x in (0, 8, 16)]}}


def seed_fixture():
    # Independent known wire bytes and URI-encoded client payload; no real credentials.
    initial = 'abcdefghijk'
    settlement = {'rank_state': '2', 'rank_time': '31', 'map_seed_2': '123',
                  'version': '1182', 'play_info': 'AA==', 'removed': '10',
                  'rand_times': '256', 'encrypt_key_version': '60'}
    text = ('{"rank_state":2,"rank_time":31,"map_seed_2":"123","version":"1182",'
            '"play_info":"AA==","removed":10,"rand_times":256,"watch_ad_count":0}')
    plain = quote(text, safe="~!*'()-._").encode()
    stream = bytes((i * 37 + 17) % 256 for i in range(len(plain)))
    xor = lambda data: bytes(a ^ b for a, b in zip(data, stream))
    settlement['encrypt_data'] = base64.b64encode(xor(plain)).decode()
    request = {'encryptKeyVersion': '60', 'info': base64.b64encode(
        b'\x01\x75\xbf' + xor(b'\x0a\x0b' + initial.encode())).decode()}
    response = xor(b'\x08\x01\x12\x04\x01\x02\x03\x04\x1a\x03123')
    return initial, request, response, settlement


class CaptureTests(unittest.TestCase):
    def test_world_start_supersedes_old_daily_and_seed_is_not_start(self):
        c = Captures([record(1, 'map_info_ex', 3), record(2, 'world/game_start', 6),
                      record(3, 'map_info_ex_seed')])
        self.assertEqual(c.latest_map_record()['id'], 2)
        self.assertEqual(game_mode(c.latest_map_record()), ('world', 6))
        with self.assertRaisesRegex(ValueError, 'different game'):
            c.ensure_current_game({'recordUid': 'uid-1'})

    def test_switch_back_selects_daily(self):
        c = Captures([record(1, 'world/game_start', 6), record(2, 'map_info_ex', 3)])
        self.assertEqual(game_mode(c.latest_map_record()), ('daily', 3))

    def test_unsupported_mode_and_wrong_match_type_fail_closed(self):
        for latest in [record(2, 'topic/game_start', 4), record(2, 'world/game_start', 3)]:
            c = Captures([record(1, 'map_info_ex', 3), latest])
            with self.assertRaises(ValueError):
                c.latest_map_record()

    def test_failed_latest_response_never_uses_older_map(self):
        latest = record(2, 'world/game_start', 6)
        latest['response']['code'] = 500
        c = Captures([record(1, 'map_info_ex', 3), latest])
        with self.assertRaisesRegex(ValueError, 'successful HTTP'):
            get_current_map(c)

    def test_completed_game_refuses_clicks(self):
        c = Captures([record(1, 'world/game_start', 6), record(2, 'world/game_over')])
        with self.assertRaisesRegex(ValueError, 'ended'):
            c.ensure_current_game({'recordUid': 'uid-1'})

    def test_missing_world_seed_does_not_load_old_daily_types(self):
        data = {'err_code': 0, 'data': {'map_md5': ['a' * 32, 'b' * 32],
                                      'map_seed': [0]*4, 'need_seed': True}}
        c = Captures([record(1, 'map_info_ex', 3), record(2, 'world/game_start', 6, payload=data)])
        with patch('map_capture.read_static_map', return_value=small_map()):
            with self.assertRaisesRegex(ValueError, '缺少本局种子'):
                get_current_map(c)

    def test_world_pipeline_recovers_own_seed_and_leaves_names_unmapped(self):
        initial, request, cipher, settlement = seed_fixture()
        payload = {'err_code': 0, 'data': {'map_md5': ['a' * 32, 'b' * 32],
                                         'map_seed_2': initial, 'need_seed': True}}
        seed_record = record(3, 'map_info_ex_seed', body=request)
        seed_record['response']['body'] = {'encoding': 'base64', 'text': base64.b64encode(cipher).decode()}
        c = Captures([record(1, 'world/game_over', body=settlement),
                      record(2, 'world/game_start', 6, payload=payload), seed_record])
        with patch('map_capture.read_static_map', return_value=small_map()):
            result = get_current_map(c)
        self.assertEqual(result['_source']['recordId'], 2)
        self.assertEqual(result['_source']['typeSource'], 'seed')
        self.assertTrue(all(n['type'] == 1 and n['typeName'] is None for n in result['levelData']['1']))
        c.records[1]['url'] = c.records[1]['url'].replace('synthetic-session', 'another-session')
        with patch('map_capture.read_static_map', return_value=small_map()):
            with self.assertRaisesRegex(ValueError, '无法验证'):
                get_current_map(c)

    def test_daily_runtime_keeps_types_and_rejects_wrong_mode_or_progress(self):
        game_map = small_map()
        nodes = [dict(type=1, colNum=x, rowNum=0, layerNum=1, moldType=1,
                      blockId=i, metaType=0, metaData=0, AreaType=0) for i, x in enumerate((0, 8, 16))]
        state = {'crushMapInfo': {'gameType': 3}, 'fullSync': True,
                 'gameState': {'crushedBlockCount': 0, 'crushAreaBlocks': [],
                               'moveOutAreaBlocks': [], 'allBlockRuntimeData': nodes}}
        result = merge_runtime(game_map, [{}, state], 3)
        self.assertEqual([n['type'] for n in result['levelData']['1']], [1, 1, 1])
        with self.assertRaises(ValueError):
            merge_runtime(small_map(), [{}, state], 6)
        state['gameState']['crushAreaBlocks'] = [1]
        with self.assertRaisesRegex(ValueError, 'progressed'):
            merge_runtime(small_map(), [{}, state], 3)


class SeedTests(unittest.TestCase):
    def test_known_plaintext_seed_recovery_is_bound_to_start_and_version(self):
        args = seed_fixture()
        self.assertEqual(recover_seed(*args), ([1, 2, 3, 4], '123'))
        wrong = list(deepcopy(args))
        wrong[0] = 'differentid'
        with self.assertRaises(ValueError):
            recover_seed(*wrong)
        wrong = list(deepcopy(args))
        wrong[1]['encryptKeyVersion'] = '61'
        with self.assertRaises(ValueError):
            recover_seed(*wrong)

    def test_seed_parser_rejects_corruption_truncation_and_extra_bytes(self):
        valid = b'\x08\x01\x12\x04\x01\x02\x03\x04\x1a\x03123'
        self.assertEqual(decode_seed_ack(valid), ([1, 2, 3, 4], '123'))
        for invalid in (valid[:-1], valid + b'\x00', b'\x08\x01\x12\x04\0\0\0\0\x1a\x03123'):
            with self.assertRaises(ValueError):
                decode_seed_ack(invalid)

    def test_zero_seeds_and_mixed_pretyped_nodes_rejected(self):
        with self.assertRaises(ValueError):
            assign_types(small_map(), [0]*4)
        game_map = small_map()
        game_map['levelData']['1'][0]['type'] = 1
        with self.assertRaises(ValueError):
            assign_types(game_map, [1, 2, 3, 4])


if __name__ == '__main__':
    unittest.main()
