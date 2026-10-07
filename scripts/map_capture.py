"""Read map responses through Reqable's local stdio MCP server."""

from __future__ import annotations

import base64
from collections import Counter
import json
import os
import re
import selectors
import subprocess
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from analyze_captures import decode_map_file
from solve_map import load_cards
from seed_map import assign_types, recover_seed


MCP_SERVER = '/Applications/Reqable.app/Contents/Helpers/mcp-server'
GAME_HOST = 'cat-match.easygame2021.com'
STATIC_HOST = 'cat-match-static.easygame2021.com'
GAME_MODES = {
    '/sheep/v1/game/map_info_ex': ('daily', 3),
    '/sheep/v1/game/world/game_start': ('world', 6),
}
NAMES = {1: '草', 2: '胡萝卜', 3: '玉米', 4: '树桩', 5: '叉子', 6: '白菜', 7: '羊毛',
         8: '刷子', 9: '剪刀', 10: '奶瓶', 11: '水桶', 12: '手套', 13: '铃铛',
         14: '篝火', 15: '粉红线团'}
MODE_NAMES = {'daily': NAMES, 'world': {}}
GAME_ENDS = {'/sheep/v1/game/game_over_ex', '/sheep/v1/game/world/game_over'}


class Reqable:
    def __init__(self):
        self.proc = subprocess.Popen([MCP_SERVER], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buffer = b''
        self.sequence = 0
        try:
            self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                   'clientInfo': {'name': 'sheep-local-player', 'version': '1'}})
            self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        except Exception:
            self.close()
            raise

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message).encode() + b'\n')
        self.proc.stdin.flush()

    def rpc(self, method, params):
        self.sequence += 1
        ident = self.sequence
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        deadline = time.monotonic() + 15
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if b'\n' not in self.buffer:
                    if not selector.select(max(0, deadline-time.monotonic())):
                        break
                    chunk = os.read(self.proc.stdout.fileno(), 65536)
                    if not chunk:
                        raise RuntimeError('Reqable MCP disconnected')
                    self.buffer += chunk
                    continue
                line, self.buffer = self.buffer.split(b'\n', 1)
                if not line.strip():
                    continue
                message = json.loads(line)
                if message.get('id') != ident:
                    continue
                if 'error' in message:
                    raise RuntimeError('Reqable MCP request failed: ' + method)
                return message['result']
        raise TimeoutError('Reqable MCP timed out: ' + method)

    def call(self, name, arguments):
        result = self.rpc('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            raise RuntimeError('Reqable tool failed: ' + name)
        if 'structuredContent' in result:
            return result['structuredContent']
        texts = [c['text'] for c in result.get('content', []) if c.get('type') == 'text']
        return json.loads('\n'.join(texts))

    def ids(self, filters):
        result = self.call('capture_live_filter', {'filters': filters})
        return result['items'] if isinstance(result, dict) else result

    def latest_map_record(self):
        ids = self.ids([{'type': 'host', 'hosts': [GAME_HOST]},
                        {'type': 'keyword', 'pattern': '/map_info|/game_start|/game/start',
                         'regex': True}])
        for ident in sorted(ids, reverse=True):
            record = self.call('capture_live_get_by_id', {'id': ident})
            path = urlsplit(record['url']).path
            if path.endswith(('/map_info_ex', '/game_start', '/game/start', '/map_info')):
                game_mode(record)
                return record
        raise ValueError('No captured game start; enter a game while Reqable records')

    def ensure_current_game(self, source):
        latest = self.latest_map_record()
        if latest['uid'] != source['recordUid']:
            raise ValueError('A different game or mode has started; refusing the saved map')
        ids = self.ids([{'type': 'host', 'hosts': [GAME_HOST]},
                        {'type': 'keyword', 'pattern': '/game_over'}])
        for ident in sorted((i for i in ids if i > latest['id']), reverse=True):
            record = self.call('capture_live_get_by_id', {'id': ident})
            if urlsplit(record['url']).path in GAME_ENDS:
                raise ValueError('This game has ended; enter a fresh board before starting')


def game_mode(record):
    url = urlsplit(record['url'])
    if url.path not in GAME_MODES:
        raise ValueError('Latest game mode is unsupported; refusing to use an older map')
    mode, match_type = GAME_MODES[url.path]
    query = parse_qs(url.query)
    if query.get('matchType', [str(match_type)]) != [str(match_type)]:
        raise ValueError('Game endpoint and matchType disagree')
    return mode, match_type


def decompress_match(text):
    from lzstring import _decompress
    if not text:
        raise ValueError('Latest response has no match_data; refusing to reuse an older game')
    decoded = _decompress(len(text), 16384, lambda i: ord(text[i])-32)
    return json.loads(decoded)


def response_bytes(record):
    response = record.get('response') or {}
    if response.get('code') != 200:
        raise ValueError('Capture does not have a successful HTTP response')
    body = response.get('body') or {}
    if body.get('encoding') == 'base64':
        return base64.b64decode(body['text'], validate=True)
    if body.get('encoding') == 'utf8':
        return body['text'].encode('utf-8')
    raise ValueError('Unsupported captured body encoding')


def read_static_map(client, md5):
    if not re.fullmatch(r'[a-fA-F0-9]{32}', md5):
        raise ValueError('Unexpected static map ID')
    url = f'https://{STATIC_HOST}/maps/{md5}.map'
    ids = client.ids([{'type': 'url', 'urls': [url]}])
    if ids:
        raw = response_bytes(client.call('capture_live_get_by_id', {'id': max(ids)}))
    else:
        with urlopen(url, timeout=15) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Unexpectedly large static map')
    return decode_map_file(SimpleNamespace(name=md5, read_bytes=lambda: raw))


def form_values(record):
    body = record['request'].get('body') or {}
    if body.get('encoding') != 'utf8':
        raise ValueError('Expected captured form text')
    fields = parse_qs(body['text'], keep_blank_values=True)
    if any(len(values) != 1 for values in fields.values()):
        raise ValueError('Duplicate form fields')
    return {key: values[0] for key, values in fields.items()}


def session_token(record):
    values = parse_qs(urlsplit(record['url']).query).get('t', [])
    return values[0] if len(values) == 1 else None


def captured_seed(client, start, data):
    ids = client.ids([{'type': 'host', 'hosts': [GAME_HOST]},
                      {'type': 'keyword', 'pattern': '/map_info_ex_seed'}])
    candidates = []
    for ident in sorted((i for i in ids if i > start['id']), reverse=True):
        record = client.call('capture_live_get_by_id', {'id': ident})
        if (urlsplit(record['url']).path == '/sheep/v1/game/map_info_ex_seed'
                and session_token(record) and session_token(record) == session_token(start)):
            candidates.append(record)
    if not candidates:
        raise ValueError('缺少本局种子请求，请在 Reqable 捕获期间重新进入关卡')
    over_ids = client.ids([{'type': 'host', 'hosts': [GAME_HOST]},
                           {'type': 'keyword', 'pattern': '/game_over'}])
    for ident in sorted((i for i in over_ids if i < start['id']), reverse=True):
        over = client.call('capture_live_get_by_id', {'id': ident})
        if urlsplit(over['url']).path not in GAME_ENDS or session_token(over) != session_token(start):
            continue
        for record in candidates:
            try:
                seed, _ = recover_seed(data['map_seed_2'], form_values(record),
                                       response_bytes(record), form_values(over))
            except (KeyError, ValueError, UnicodeError):
                continue
            return seed, {'seedRecordId': record['id'], 'seedRecordUid': record['uid'],
                          'settlementRecordId': over['id'],
                          'encryptKeyVersion': form_values(record)['encryptKeyVersion']}
    raise ValueError('本局种子无法验证：需要同一会话、同一加密版本的已完成对局结算记录。'
                     '保留 Reqable 记录，手动完成一局后重新开局；不会回退旧地图')


def merge_runtime(game_map, states, match_type):
    if len(states) != 2:
        raise ValueError('Expected two complete level states')
    state = states[1]
    if state['crushMapInfo']['gameType'] != match_type or not state['fullSync']:
        raise ValueError('Runtime mode differs from the selected game, or state is incomplete')
    runtime = state['gameState']
    if (runtime['crushedBlockCount'] or runtime['crushAreaBlocks']
            or any(runtime['moveOutAreaBlocks'])):
        raise ValueError('Captured game has already progressed')
    blocks = {f"{b['layerNum']}-{b['colNum']}-{b['rowNum']}": b
              for b in runtime['allBlockRuntimeData']}
    nodes = [n for ns in game_map['levelData'].values() for n in ns]
    if len(blocks) != len(runtime['allBlockRuntimeData']) or set(blocks) != {n['id'] for n in nodes}:
        raise ValueError('Runtime and static coordinates differ')
    for node in nodes:
        b = blocks[node['id']]
        if node['moldType'] != b['moldType']:
            raise ValueError('Runtime and static card structures differ')
        node.update(type=b['type'], cardId=b['blockId'], metaType=b['metaType'],
                    metaData=b['metaData'], AreaType=b['AreaType'])
    return game_map


def get_current_map(client):
    record = client.latest_map_record()
    mode, match_type = game_mode(record)
    source = {'recordId': record['id'], 'recordUid': record['uid'],
              'mode': mode, 'matchType': match_type}
    client.ensure_current_game(source)
    payload = json.loads(response_bytes(record))
    if payload.get('err_code') != 0:
        raise ValueError('Game rejected the map request')
    data = payload['data']
    if not isinstance(data.get('map_md5'), list) or len(data['map_md5']) != 2:
        raise ValueError('Expected a two-level map response')
    game_map = read_static_map(client, data['map_md5'][1])
    if data.get('match_data'):
        game_map = merge_runtime(game_map, decompress_match(data['match_data']), match_type)
        source['typeSource'] = 'match_data'
    else:
        seed = data.get('map_seed', [])
        if data.get('need_seed'):
            seed, evidence = captured_seed(client, record, data)
            source.update(evidence)
        game_map = assign_types(game_map, seed)
        source['typeSource'] = 'seed'
    for nodes in game_map['levelData'].values():
        for node in nodes:
            node['typeName'] = MODE_NAMES[mode].get(node['type'])
    cards = load_cards(game_map)
    expected = {int(kind): groups * 3 for kind, groups in game_map['blockTypeData'].items() if groups}
    if Counter(c.type for c in cards) != expected:
        raise ValueError('Assigned type counts differ from static map')
    source.update(mapMd5=data['map_md5'][1])
    game_map['_source'] = source
    client.ensure_current_game(source)
    return game_map
