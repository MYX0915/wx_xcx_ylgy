import base64
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from world_snapshot import decode_frame, latest_record, message, neighborhood, snapshot


REQUEST = bytes.fromhex('02044c020a06089362109d4719aefca4')
ACK = bytes.fromhex('05044d120a0a06089362109d471009120a0a06089362109c471009120a0a06089462109c471009120a0a06089262109d471009120a0a06089262109c471009120a0a06089462109d471009120a0a06089262109e471009120a0a06089362109e471009120a0a06089462109e4710091a06089362109d47')


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def scalar(number, value):
    return varint(number << 3) + varint(value)


def nested(number, value):
    return varint(number << 3 | 2) + varint(len(value)) + value


def point(x, y):
    return scalar(1, x) + scalar(2, y)


def frame(data, flow=1, timestamp=1000):
    return {'flow': flow, 'timestamp': timestamp,
            'payload': {'type': 3, 'buffer': base64.b64encode(data).decode()}}


def move_request(x=12563, y=9117, sequence=3):
    raw = b'\x02\x04\x4c' + bytes([sequence]) + nested(1, point(x, y))
    return raw + zlib.crc32(raw).to_bytes(4, 'little')


def record():
    location = point(12563, 9118)
    nodes = b''.join(nested(2, nested(1, point(x, y)) + scalar(2, 9))
                     for x in range(12562, 12565) for y in range(9117, 9120))
    login = (b'\x00\x03\xeb' + nodes + nested(3, nested(6, location) + scalar(7, 2))
             + scalar(4, 13600) + scalar(5, 15600))
    return {'id': 1, 'uid': 'fixture', 'protocol': 'websocket',
            'messages': [frame(login)]}


def gift_frame(status=0, opcode=1120, owner=False, x=12562, y=9118):
    gift = nested(3, point(x, y)) + scalar(4, status)
    if status in (1, 2):
        gift += scalar(1, 2) + scalar(2, 3221)
    if owner:
        gift += nested(5, nested(1, b'private-uid') + nested(7, b'private-name'))
    return frame(b'\x01' + opcode.to_bytes(2, 'big') + nested(1, gift), timestamp=1150)


class WorldSnapshotTests(unittest.TestCase):
    def test_latest_connection_uses_metadata_filters_and_exact_path(self):
        client = Mock()
        client.ids.return_value = [1, 3, 2]
        current = {'id': 2, 'url': 'wss://cat-match.easygame2021.com/gateway/ws?token=private'}
        client.call.side_effect = [
            {'id': 3, 'url': 'https://cat-match.easygame2021.com/other?next=/gateway/ws'}, current]
        self.assertIs(latest_record(client), current)
        self.assertEqual([c.args[1]['id'] for c in client.call.call_args_list], [3, 2])
        filters = client.ids.call_args.args[0]
        self.assertNotIn('keyword', [f['type'] for f in filters])
        self.assertIn({'type': 'method', 'methods': ['GET']}, filters)

    def test_latest_connection_does_not_fall_back_when_closed_or_incomplete(self):
        for protocol in ('websocket', 'http'):
            client = Mock()
            client.ids.return_value = [1, 2]
            current = {'id': 2, 'url': 'wss://cat-match.easygame2021.com/gateway/ws',
                       'protocol': protocol, 'messages': [{'payload': {'type': 6}}]}
            client.call.return_value = current
            self.assertIs(latest_record(client), current)
            client.call.assert_called_once_with('capture_live_get_by_id', {'id': 2})

    def test_no_world_connection_reports_missing_capture(self):
        client = Mock()
        client.ids.return_value = []
        with self.assertRaisesRegex(ValueError, 'No captured'):
            latest_record(client)

    def test_overlapping_same_target_keeps_success_and_rejection(self):
        capture = record()
        capture['messages'] += [frame(REQUEST, 0, 1100),
                                frame(move_request(), 0, 1120),
                                frame(ACK, 1, 1133), gift_frame(),
                                frame(b'\x06\x04\x4d\x08\x03', 1, 1160)]
        result = snapshot(capture, now_ms=1200)
        self.assertEqual(result['current']['y'], 6483)
        self.assertEqual([m['code'] for m in result['movements']], [0, 3])
        self.assertEqual([m['requestedAt'] for m in result['movements']], [1100, 1120])
        self.assertTrue(any(n['giftStatusKnown'] for n in result['nodes']))

    def test_duplicate_request_stays_pending_until_both_responses(self):
        capture = record()
        capture['messages'] += [frame(REQUEST, 0, 1100),
                                frame(move_request(), 0, 1120), frame(ACK, 1, 1133)]
        with self.assertRaisesRegex(ValueError, 'awaiting'):
            snapshot(capture, now_ms=1200)

    def test_overlapping_different_targets_are_rejected(self):
        capture = record()
        capture['messages'] += [frame(REQUEST, 0, 1100),
                                frame(move_request(x=12564), 0, 1120)]
        with self.assertRaisesRegex(ValueError, 'different targets'):
            snapshot(capture, now_ms=1200)

    def test_gift_states_are_merged_without_personal_fields(self):
        for status, reason in ((0, None), (1, 'gift_ownerless'), (2, 'gift_occupied'),
                               (3, None), (9, 'gift_unknown_state')):
            capture = record()
            capture['messages'].append(gift_frame(status, owner=status == 2))
            result = snapshot(capture, now_ms=1200)
            node = next(n for n in result['nodes'] if (n['x'], n['y']) == (12562, 6482))
            self.assertEqual(node['navigationBlock'], reason)
            self.assertEqual(node['navigationCandidate'], reason is None)
            self.assertEqual(node['gift']['giftState'], status)
            self.assertNotIn('private-', str(result))

    def test_missing_gift_data_is_not_a_safe_candidate(self):
        result = snapshot(record(), now_ms=1200)
        self.assertEqual(sum(n['terrainMoveCandidate'] for n in result['nodes']), 8)
        self.assertFalse(any(n['navigationCandidate'] for n in result['nodes']))

    def test_incremental_change_and_full_replacement(self):
        capture = record()
        capture['messages'] += [gift_frame(2), gift_frame(3, opcode=1109)]
        node = next(n for n in snapshot(capture, now_ms=1200)['nodes'] if n['x'] == 12562 and n['y'] == 6482)
        self.assertTrue(node['navigationCandidate'])
        capture['messages'].append(gift_frame(0, x=12564))
        node = next(n for n in snapshot(capture, now_ms=1200)['nodes'] if n['x'] == 12562 and n['y'] == 6482)
        self.assertFalse(node['giftStatusKnown'])

    def test_movement_discards_old_gift_status_until_new_push(self):
        capture = record()
        capture['messages'] += [gift_frame(), frame(REQUEST, 0, 1160), frame(ACK, 1, 1180)]
        result = snapshot(capture, now_ms=1200)
        self.assertFalse(any(n['giftStatusKnown'] for n in result['nodes']))
        capture['messages'].append(gift_frame(2, opcode=1109))
        result = snapshot(capture, now_ms=1200)
        occupied = next(n for n in result['nodes'] if n['x'] == 12562 and n['y'] == 6482)
        self.assertEqual(occupied['navigationBlock'], 'gift_occupied')

    def test_real_move_frame_and_ack_refresh_the_neighborhood(self):
        capture = record()
        capture['messages'] += [frame(REQUEST, 0, 1100), frame(ACK, 1, 1133)]
        result = snapshot(capture, now_ms=1200)
        self.assertEqual(result['current'], {'x': 12563, 'y': 6483, 'protocolY': 9117})
        self.assertEqual(len(result['nodes']), 9)
        self.assertEqual(sum(n['terrainMoveCandidate'] for n in result['nodes']), 8)
        self.assertEqual({n['y'] for n in result['nodes']}, {6482, 6483, 6484})
        self.assertEqual(result['movements'][0]['code'], 0)
        self.assertEqual(result['movements'][0]['from']['y'], 6482)

    def test_error_response_does_not_advance_position(self):
        capture = record()
        capture['messages'] += [frame(REQUEST, 0, 1100), frame(b'\x05\x04\x4d\x08\x03', 1, 1133)]
        result = snapshot(capture, now_ms=1200)
        self.assertEqual(result['current']['y'], 6482)
        self.assertEqual(result['movements'][0]['code'], 3)
        self.assertEqual(result['movements'][0]['nodes'], [])

    def test_pending_stale_closed_and_unmatched_captures_are_rejected(self):
        cases = []
        pending = record()
        pending['messages'].append(frame(REQUEST, 0, 1100))
        cases.append((pending, 1200, 'awaiting'))
        cases.append((record(), 17000, 'recent'))
        closed = record()
        closed['messages'].append({'flow': 1, 'timestamp': 1100,
                                   'payload': {'type': 6, 'code': 1000, 'reason': ''}})
        cases.append((closed, 1200, 'closed'))
        unmatched = record()
        unmatched['messages'].append(frame(ACK))
        cases.append((unmatched, 1200, 'matching'))
        missing = record()
        missing['messages'] = [frame(b'\x03\xea\x08\x01')]
        cases.append((missing, 1200, 'login'))
        for capture, now, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                snapshot(capture, now_ms=now)

    def test_coordinate_origin_comes_from_login_dimensions(self):
        capture = record()
        raw = base64.b64decode(capture['messages'][0]['payload']['buffer'])
        capture['messages'][0] = frame(raw.replace(scalar(5, 15600), scalar(5, 16000)))
        self.assertEqual(snapshot(capture, now_ms=1200)['current']['y'], 6882)

    def test_terrain_and_discoveries_are_not_boolean_province_states(self):
        current = {'x': 10, 'y': 10, 'protocolY': 90}
        entries = [nested(1, point(10, 90)) + scalar(2, 9),
                   nested(1, point(11, 90)),
                   nested(1, point(9, 90)) + scalar(2, 9) + scalar(3, 7),
                   nested(1, point(10, 89)) + scalar(2, 9)]
        nodes = neighborhood({2: entries}, current, 100)
        kinds = {(n['x'], n['y']): n['kind'] for n in nodes}
        self.assertEqual(kinds[(11, 10)], 'locked_terrain')
        self.assertEqual(kinds[(9, 10)], 'discovery')
        self.assertEqual(kinds[(10, 11)], 'ordinary_neighbor')

    def test_corrupt_frames_and_truncated_protobuf_are_rejected(self):
        corrupt = bytearray(REQUEST)
        corrupt[10] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC32'):
            decode_frame(frame(corrupt, 0))
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            message(b'\x0a\x06\x08')
        capture = record()
        capture['messages'] += [frame(REQUEST, 0, 1100), frame(ACK[:-1] + b'\x46', 1, 1133)]
        with self.assertRaisesRegex(ValueError, 'differs'):
            snapshot(capture, now_ms=1200)


if __name__ == '__main__':
    unittest.main()
