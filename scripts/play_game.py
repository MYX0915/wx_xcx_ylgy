#!/usr/bin/env python3
"""Fetch a typed map, solve it, and optionally click using the fixed window layout."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import time

from map_capture import Reqable, apply_type_names, get_current_map
from solve_map import load_cards, replay, solve


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/game_window'
BASE_WINDOW_SIZE = (350, 665)
MODE_LABELS = {'daily': '普通关卡', 'world': '羊羊大世界'}
MODE_LAYOUTS = {'daily': (50, 146, 4.5), 'world': (50, 146, 4.5)}


def coordinate_model(window, mode):
    bounds = window['bounds']
    sx = bounds['Width'] / BASE_WINDOW_SIZE[0]
    sy = bounds['Height'] / BASE_WINDOW_SIZE[1]
    if abs(sx - sy) > 0.02:
        raise ValueError('Game window aspect ratio differs from the supported layout')
    ox, oy, scale = MODE_LAYOUTS[mode]
    return {'mode': mode, 'xScale': scale * sx, 'xOffset': ox * sx,
            'yScale': scale * sy, 'yOffset': oy * sy}


def card_point(card, model):
    return (card.x * model['xScale'] + model['xOffset'],
            card.y * model['yScale'] + model['yOffset'])


def native(*args):
    result = subprocess.run([str(HELPER), *map(str, args)], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Window helper failed')
    return json.loads(result.stdout)


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def select_window(info, ident):
    candidates = [w for w in info['windows'] if '羊了个羊' in w['title']]
    if ident is not None:
        candidates = [w for w in candidates if w['id'] == ident]
    if len(candidates) != 1:
        raise ValueError('Expected one game window; use --window-id if necessary')
    return candidates[0]


def prepare(client, args):
    game_map = get_current_map(client)
    source = game_map['_source']
    print(f"识别模式：{MODE_LABELS[source['mode']]}；请求 {source['recordId']}；"
          f"牌型来源：{source['typeSource']}", flush=True)
    for existing in (ROOT / 'runs').glob(f"{source['recordId']}-*/map.json"):
        saved_source = json.loads(existing.read_text()).get('_source', {})
        if saved_source.get('recordUid') != source['recordUid']:
            continue
        saved_progress = json.loads((existing.parent / 'progress.json').read_text())
        if saved_progress.get('nextStep', 0) or saved_progress.get('inFlight'):
            raise ValueError(f'This game already has clicks; resume its run or start a new game: {existing.parent}')
    cards = load_cards(game_map)
    result = solve(cards, args.seconds)
    status, order = result['status'], result['order']
    if status != 'solved':
        raise ValueError('No verified solution: ' + status)
    result.update(status=status, operations=[cards[i].id for i in order], order=order)
    print(f"完整解已验证：{len(order)} 步；求解 {result['elapsedSeconds']} 秒；"
          f"方法 {result['backend']}", flush=True)
    folder = ROOT / 'runs' / (str(game_map['_source']['recordId']) + '-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True, exist_ok=False)
    save(folder / 'map.json', game_map)
    save(folder / 'solution.json', result)
    progress = {'nextStep': 0, 'inFlight': False, 'status': 'prepared', 'runDirectory': str(folder)}
    save(folder / 'progress.json', progress)
    return folder, game_map, cards, result, progress


def resume(folder):
    game_map = json.loads((folder / 'map.json').read_text())
    apply_type_names(game_map, game_map.get('_source', {}).get('mode', 'daily'))
    cards = load_cards(game_map)
    result = json.loads((folder / 'solution.json').read_text())
    result.update(replay(cards, result['order']))
    progress = json.loads((folder / 'progress.json').read_text())
    if progress['inFlight']:
        raise ValueError('Previous click was not verified; inspect the game before attempting a new run')
    return folder, game_map, cards, result, progress


def type_label(cards, card_type):
    return next((card.name for card in cards if card.type == card_type and card.name),
                f'类型{card_type}')


def check_stop(folder, pointer):
    if (folder / 'STOP').exists():
        raise InterruptedError('STOP file found')
    current = native('pointer')
    if abs(current['x']-pointer['x']) > 3 or abs(current['y']-pointer['y']) > 3:
        raise InterruptedError('Mouse moved by user')


def execute(client, window, folder, game_map, cards, result, progress, model, args):
    native('focus', window['id'])
    pointer = native('pointer')
    print('Clicking starts in 3 seconds. Move the mouse or press Ctrl-C to stop.', flush=True)
    time.sleep(3)
    stop_at = min(len(cards), progress['nextStep'] + args.max_clicks)
    while progress['nextStep'] < stop_at:
        step = progress['nextStep']
        check_stop(folder, pointer)
        current_window = select_window(native('inspect'), window['id'])
        if current_window['bounds'] != window['bounds'] or current_window['pid'] != window['pid']:
            raise ValueError('Game window moved, resized, or restarted')
        client.ensure_current_game(game_map['_source'])
        i = result['order'][step]
        x, y = card_point(cards[i], model)
        bounds = window['bounds']
        x += bounds['X']
        y += bounds['Y']
        progress.update(inFlight=True, status='clicking')
        save(folder / 'progress.json', progress)
        try:
            native('click', window['id'], x, y)
        except RuntimeError as exc:
            if str(exc) in {
                'Click target is covered by another window',
                'Click target window does not match the game bounds',
                'Game window lost focus',
                'Cannot verify the window at the click target',
                'Invalid click coordinates or missing accessibility permission',
            }:
                progress.update(inFlight=False, status='stopped', error=str(exc))
                save(folder / 'progress.json', progress)
            raise
        pointer = {'x': x, 'y': y}
        progress.update(nextStep=step+1, inFlight=False, status='running')
        save(folder / 'progress.json', progress)
        tray = result['steps'][step]['trayAfter']
        clicked = type_label(cards, cards[i].type)
        tray_labels = '、'.join(type_label(cards, card_type) for card_type in tray) or '空'
        print(f"已点击【{clicked}】；当前槽位（推算）【{tray_labels}】（{step+1}/{len(cards)}）", flush=True)
        time.sleep(args.delay)
        check_stop(folder, pointer)
    progress['status'] = 'sequence_completed' if stop_at == len(cards) else 'paused'
    progress['winScreenConfirmed'] = False
    save(folder / 'progress.json', progress)


def run(args):
    client = Reqable()
    folder = progress = None
    try:
        info = native('inspect')
        window = select_window(info, args.window_id)
        if args.resume:
            folder, game_map, cards, result, progress = resume(args.resume.resolve())
        else:
            folder, game_map, cards, result, progress = prepare(client, args)
        client.ensure_current_game(game_map['_source'])
        mode = game_map['_source'].get('mode', 'daily')
        model = coordinate_model(window, mode)
        save(folder / 'coordinate-model.json', model)
        preflight = {'runDirectory': str(folder), 'source': game_map['_source'],
                     'steps': len(cards), 'window': window, 'coordinateModel': model,
                     'accessibility': info['accessibility'],
                     'clickingEnabled': args.execute}
        save(folder/'preflight.json', preflight)
        print(json.dumps(preflight, ensure_ascii=False), flush=True)
        if args.execute:
            if not info['accessibility']:
                raise PermissionError('Enable macOS Accessibility for the launching terminal or Codex before --execute')
            execute(client, window, folder, game_map, cards, result, progress, model, args)
    except (Exception, KeyboardInterrupt) as exc:
        if folder and progress is not None:
            progress.update(status='stopped', error=str(exc) or 'Interrupted by user')
            save(folder/'progress.json', progress)
        raise
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Actually click; default only checks and plans')
    parser.add_argument(
        '--resume', type=Path,
        help='Resume after confirming the previous click completed in the game',
    )
    parser.add_argument('--window-id', type=int)
    parser.add_argument('--max-clicks', type=int, default=500)
    parser.add_argument('--seconds', type=float, default=180)
    parser.add_argument('--delay', type=float, default=0.5)
    args = parser.parse_args()
    if args.max_clicks < 1 or args.seconds <= 0 or args.delay < 0.3:
        parser.error('max-clicks and seconds must be positive; delay must be at least 0.3 seconds')
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
