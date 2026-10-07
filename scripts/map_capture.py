"""Read map responses through Reqable's local stdio MCP server."""

from __future__ import annotations

import base64
import json
import os
import re
import selectors
import subprocess
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import urlopen

from analyze_captures import decode_map_file
from solve_map import load_cards


MCP_SERVER = '/Applications/Reqable.app/Contents/Helpers/mcp-server'
GAME_HOST = 'cat-match.easygame2021.com'
STATIC_HOST = 'cat-match-static.easygame2021.com'
NAMES = {2: '胡萝卜', 3: '玉米', 4: '树桩', 5: '叉子', 6: '白菜', 7: '羊毛',
         8: '刷子', 9: '剪刀', 10: '奶瓶', 11: '水桶', 12: '手套', 13: '铃铛',
         14: '篝火', 15: '粉红线团'}


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
                        {'type': 'keyword', 'pattern': '/map_info_ex'}])
        for ident in sorted(ids, reverse=True):
            record = self.call('capture_live_get_by_id', {'id': ident})
            if urlsplit(record['url']).path == '/sheep/v1/game/map_info_ex':
                return record
        raise ValueError('No captured map_info_ex; start a new challenge while Reqable records')


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


def get_current_map(client):
    record = client.latest_map_record()
    payload = json.loads(response_bytes(record))
    if payload.get('err_code') != 0:
        raise ValueError('Game rejected the map request')
    data = payload['data']
    states = decompress_match(data.get('match_data', ''))
    if len(states) != 2 or len(data['map_md5']) != 2:
        raise ValueError('Expected a two-level daily challenge')
    state = states[1]
    if state['crushMapInfo']['gameType'] != 3 or not state['fullSync']:
        raise ValueError('Only full daily-challenge maps are supported')
    runtime = state['gameState']
    if (runtime['crushedBlockCount'] or runtime['crushAreaBlocks']
            or any(runtime['moveOutAreaBlocks'])):
        raise ValueError('Captured game has already progressed')
    game_map = read_static_map(client, data['map_md5'][1])
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
                    metaData=b['metaData'], AreaType=b['AreaType'], typeName=NAMES.get(b['type']))
    cards = load_cards(game_map)
    for kind, groups in game_map['blockTypeData'].items():
        if sum(c.type == int(kind) for c in cards) != groups*3:
            raise ValueError('Runtime type counts differ from static map')
    game_map['_source'] = {'recordId': record['id'], 'recordUid': record['uid'],
                           'capturedAt': record['connection']['timestamp'],
                           'mapMd5': data['map_md5'][1]}
    return game_map
