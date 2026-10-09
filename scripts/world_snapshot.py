"""Read the current world-map neighborhood from WeChat's captured WebSocket."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import zlib

from analyze_captures import read_varint
from map_capture import GAME_HOST, Reqable


class MovementPending(ValueError):
    pass


def message(data):
    if not isinstance(data, bytes):
        raise ValueError('Missing protobuf message')
    result, pos = {}, 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError('Invalid protobuf field number')
        if wire == 0:
            value, pos = read_varint(data, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = read_varint(data, pos)
            else:
                size = 8 if wire == 1 else 4
            if pos + size > len(data):
                raise ValueError('Truncated protobuf field')
            value, pos = data[pos:pos + size], pos + size
        else:
            raise ValueError('Unsupported protobuf wire type')
        result.setdefault(number, []).append(value)
    return result


def single(fields, number, default=None):
    values = fields.get(number, [default])
    if len(values) != 1:
        raise ValueError('Repeated singular protobuf field')
    return values[0]


def coord(data, max_y):
    fields = message(data)
    x, y = single(fields, 1, 0), single(fields, 2, 0)
    if not isinstance(x, int) or not isinstance(y, int) or not 0 <= y <= max_y:
        raise ValueError('Invalid world coordinate')
    return {'x': x, 'y': max_y - y, 'protocolY': y}


def neighborhood(fields, current, max_y):
    nodes, seen = [], set()
    for raw in fields.get(2, []):
        node = message(raw)
        point = coord(single(node, 1), max_y)
        key = point['x'], point['y']
        if key in seen:
            raise ValueError('Duplicate world node')
        seen.add(key)
        province = single(node, 2, 0)
        discovery = single(node, 3, 0)
        distance = max(abs(point['x'] - current['x']), abs(point['y'] - current['y']))
        reason = ('current' if distance == 0 else 'outside_neighborhood' if distance > 1
                  else 'locked_terrain' if province <= 0 else 'discovery' if discovery
                  else 'ordinary_neighbor')
        nodes.append({**point, 'province': province, 'discovery': discovery,
                      'cityId': single(node, 4, 0), 'kind': reason,
                      'terrainMoveCandidate': reason == 'ordinary_neighbor'})
    if (current['x'], current['y']) not in seen:
        raise ValueError('Current position missing from world-map response')
    return sorted(nodes, key=lambda node: (-node['y'], node['x']))


def gift_updates(fields, max_y):
    result = {}
    for raw in fields.get(1, []):
        fields_gift = message(raw)
        location = coord(single(fields_gift, 3), max_y)
        key = location['x'], location['y']
        if key in result:
            raise ValueError('Duplicate gift coordinate')
        values = [single(fields_gift, n, 0) for n in (1, 2, 4)]
        if any(type(v) is not int or v < 0 for v in values):
            raise ValueError('Invalid gift state fields')
        result[key] = dict(zip(('giftType', 'giftId', 'giftState'), values))
        result[key].update(hasOwner=5 in fields_gift, forbidHelp=bool(single(fields_gift, 9, 0)))
    return result


def attach_gifts(nodes, gifts):
    result = []
    for node in nodes:
        gift = gifts.get((node['x'], node['y']))
        status = gift['giftState'] if gift is not None else None
        reason = ('gift_unknown' if gift is None else 'gift_ownerless' if status == 1
                  else 'gift_occupied' if status == 2 else 'gift_unknown_state' if status not in (0, 3)
                  else 'gift_owner_present' if gift['hasOwner'] else None)
        result.append({**node, 'giftStatusKnown': gift is not None,
                       'gift': gift, 'navigationBlock': reason,
                       'navigationCandidate': node['terrainMoveCandidate'] and reason is None})
    return result


def decode_frame(frame):
    if frame['flow'] not in (0, 1):
        raise ValueError('Unknown frame direction')
    payload = frame['payload']
    if payload['type'] != 3:
        return None
    raw = base64.b64decode(payload.get('buffer', ''), validate=True)
    if frame['flow'] == 0:
        if len(raw) < 8 or raw[0] != 2:
            raise ValueError('Invalid client frame header')
        if zlib.crc32(raw[:-4]) != int.from_bytes(raw[-4:], 'little'):
            raise ValueError('Client frame CRC32 mismatch')
        opcode, body = int.from_bytes(raw[1:3], 'big'), raw[4:-4]
    else:
        if len(raw) < 3:
            raise ValueError('Invalid server frame header')
        if raw[:2] == b'\x03\xea':
            return None  # Heartbeat responses have no sequence byte.
        opcode, body = int.from_bytes(raw[1:3], 'big'), raw[3:]
    if opcode not in (1003, 1004, 1100, 1101, 1109, 1120, 1334, 1335):
        return None
    return opcode, message(body)


def snapshot(record, now_ms=None, max_age_seconds=15):
    if record.get('protocol') != 'websocket':
        raise ValueError('Expected a captured WebSocket')
    state, pending, moves, last_received = None, [], [], None
    gifts = {}
    for frame in record.get('messages', []):
        if frame['payload']['type'] == 6:
            raise ValueError('World connection is closed; re-enter the world')
        if frame['flow'] == 1:
            last_received = frame['timestamp']
        decoded = decode_frame(frame)
        if decoded is None:
            continue
        opcode, fields = decoded
        if opcode == 1004:
            raise ValueError('Server closed the world session; re-enter the world')
        if opcode == 1003 and frame['flow'] == 1:
            gifts = {}
            max_y = single(fields, 5, 0)
            if not max_y:
                raise ValueError('Missing world-map dimensions')
            user = message(single(fields, 3))
            current = coord(single(user, 6), max_y)
            state = {'mapMaxX': single(fields, 4, 0), 'mapMaxY': max_y,
                     'stateAtLogin': single(user, 7, 0), 'current': current,
                     'positionTimestamp': frame['timestamp'],
                     'nodes': neighborhood(fields, current, max_y)}
        elif opcode in (1109, 1120) and frame['flow'] == 1:
            if state is None:
                raise ValueError('Gift push arrived without captured login')
            if opcode == 1120:
                gifts = {}
            gifts.update(gift_updates(fields, state['mapMaxY']))
        elif opcode in (1100, 1334) and frame['flow'] == 0:
            if state is None:
                raise ValueError('Movement request has no captured login')
            request = {'opcode': opcode, 'requestedAt': frame['timestamp'],
                       'from': state['current'],
                       'target': coord(single(fields, 1), state['mapMaxY'])}
            # Repeated clicks can precede the first ACK; retain every result.
            if pending and (opcode != 1100 or any(
                    p['opcode'] != opcode or p['target'] != request['target']
                    for p in pending)):
                raise ValueError('Overlapping movement requests have different targets or modes')
            pending.append(request)
        elif opcode in (1101, 1335) and frame['flow'] == 1:
            if not pending or pending[0]['opcode'] + 1 != opcode:
                raise ValueError('Movement response has no matching captured request')
            request = pending.pop(0)
            code = single(fields, 1, 0)
            if code == 0:
                gifts = {}  # Require new neighborhood status after a confirmed movement.
                current = coord(single(fields, 3), state['mapMaxY'])
                if current != request['target']:
                    raise ValueError('Movement response differs from requested target')
                state.update(current=current, positionTimestamp=frame['timestamp'],
                             nodes=neighborhood(fields, current, state['mapMaxY']))
            moves.append({**request, 'code': code, 'receivedAt': frame['timestamp'],
                          'confirmedPosition': state['current'],
                          'nodes': state['nodes'] if code == 0 else []})
    if state is None:
        raise ValueError('No world login frame; keep capture running and re-enter the world')
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    if last_received is None or now_ms - last_received > max_age_seconds * 1000:
        raise ValueError('World connection has no recent received frames')
    if pending:
        raise MovementPending('Movement is awaiting server confirmation; read again after arrival')
    state['nodes'] = attach_gifts(state['nodes'], gifts)
    return {'source': {'recordId': record['id'], 'recordUid': record['uid']},
            **state, 'lastReceivedAt': last_received, 'movements': moves,
            'notice': 'Navigation excludes unknown, active or occupied gifts; server state can still change before arrival.'}


def latest_record(client):
    ids = client.ids([{'type': 'host', 'hosts': [GAME_HOST]},
                      {'type': 'application', 'name': '微信'},
                      {'type': 'method', 'methods': ['GET']}])
    # Keyword filtering can scan large captured bodies; inspect URL paths locally.
    for ident in sorted(ids, reverse=True):
        record = client.call('capture_live_get_by_id', {'id': ident})
        if urlsplit(record['url']).path == '/gateway/ws':
            return record
    raise ValueError('No captured WeChat world connection; re-enter the world while Reqable records')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write a sanitized JSON snapshot')
    parser.add_argument('--json', action='store_true', help='Print the full JSON snapshot')
    args = parser.parse_args()
    client = Reqable()
    try:
        result = snapshot(latest_record(client))
    finally:
        client.close()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    if args.json:
        print(text)
    else:
        current = result['current']
        candidates = [node for node in result['nodes'] if node['navigationCandidate']]
        print(f"大世界当前位置：({current['x']}, {current['y']})；请求记录：{result['source']['recordId']}")
        print('周边导航候选（已排除奖励/挑战及未知状态）：' + ('、'.join(f"({n['x']}, {n['y']})" for n in candidates) or '无'))
        blocked = [n for n in result['nodes'] if n['terrainMoveCandidate'] and n['navigationBlock']]
        if blocked:
            print('避开：' + '、'.join(f"({n['x']}, {n['y']}) {n['navigationBlock']}" for n in blocked))
        print(f"本连接已确认成功移动：{sum(m['code'] == 0 for m in result['movements'])} 次。实际可移动性以服务端回包为准。")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise SystemExit(str(exc)) from None
