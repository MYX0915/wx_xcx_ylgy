"""Navigate the overworld by UI clicks confirmed by captured move acknowledgments."""

import argparse
from datetime import datetime
import time

from map_capture import Reqable
from play_game import ROOT, check_stop, native, save, select_window
from world_route import Navigator, WorldLayout, direction, distance, point
from world_snapshot import MovementPending, latest_record, snapshot


PLAN_LABELS = {'known_route': '已知区域最短路', 'layout_lookahead': '布局预判，逐步确认',
               'frontier': '边界探索'}


def plan_description(plan):
    label = PLAN_LABELS[plan['method']]
    if 'margin' in plan:
        return f"{label}，扩展 {plan['margin']} 格"
    reasons = {'search_limit': '预判范围或预算内未找到路线',
               'waypoint_blocked': '分段目标不可进入', 'unverified_next': '预判下一跳未确认'}
    reason = reasons.get(plan.get('fallbackReason'))
    return f'{label}，{reason}' if reason else label


def read_current(client, source=None):
    record = latest_record(client)
    if source is not None and (record['uid'] != source['recordUid'] or record['id'] != source['recordId']):
        raise ValueError('大世界连接已变化，请确认当前位置后重新启动导航')
    return snapshot(record)


def verify_unchanged(previous, current, check_nodes=True):
    if (previous['source'] != current['source'] or previous['current'] != current['current']
            or len(previous['movements']) != len(current['movements'])
            or (check_nodes and previous['nodes'] != current['nodes'])):
        raise ValueError('位置或地图在点击前发生变化，导航已停止')


def confirmed_move(before, after, target):
    previous_count = len(before['movements'])
    if len(after['movements']) == previous_count:
        verify_unchanged(before, after, check_nodes=False)
        return False
    if len(after['movements']) != previous_count + 1:
        raise ValueError('出现非预期的额外移动，导航已停止')
    move = after['movements'][-1]
    if (move['opcode'] != 1100 or move['from'] != before['current']
            or point(move['target']) != target):
        raise ValueError('实际点击目标与计划不符，导航已停止')
    if move['code'] != 0:
        raise ValueError(f"服务器拒绝移动：code={move['code']}；停止，不自动重试")
    if point(after['current']) != target:
        raise ValueError('服务器确认的位置与目标不一致')
    return True


class WorldReader:
    def __init__(self, client, source, guard):
        self.client, self.source, self.guard = client, source, guard

    def wait_for_move(self, before, target, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.guard()
            try:
                state = read_current(self.client, self.source)
            except MovementPending:
                state = None
            if state is not None and confirmed_move(before, state, target):
                return state
            time.sleep(0.1)
        raise TimeoutError('未收到预期移动确认，已停止；请检查游戏界面，不会盲目重发')

    def wait_for_neighborhood(self, state, timeout):
        deadline = time.monotonic() + timeout
        latest = state
        while time.monotonic() < deadline:
            self.guard()
            verify_unchanged(state, latest, check_nodes=False)
            if all(n.get('giftStatusKnown') is True for n in latest['nodes']):
                return latest
            time.sleep(0.1)
            latest = read_current(self.client, self.source)
        raise TimeoutError('附近奖励/挑战状态尚未到齐，已停止；不会将未知状态当成空岛')

    def settle_after_move(self, after, seconds, timeout):
        deadline = time.monotonic() + seconds
        # Neighborhood pushes arrive during camera movement; do not wait twice.
        self.wait_for_neighborhood(after, timeout)
        controlled_wait(max(0, deadline - time.monotonic()), self.guard)
        settled = self.wait_for_neighborhood(read_current(self.client, self.source), timeout)
        verify_unchanged(after, settled, check_nodes=False)
        return settled


def controlled_wait(seconds, guard):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        guard()
        time.sleep(min(0.15, max(0, deadline - time.monotonic())))


def execute(client, window, state, navigator, folder, args, progress):
    native('focus', window['id'])
    pointer = native('pointer')

    def guard():
        check_stop(folder, pointer)
        current_window = select_window(native('inspect'), window['id'])
        if current_window['bounds'] != window['bounds'] or current_window['pid'] != window['pid']:
            raise ValueError('游戏窗口移动、缩放或重启，导航已停止')

    reader = WorldReader(client, state['source'], guard)
    print('3 秒后开始导航；移动鼠标、Ctrl-C 或创建本次目录的 STOP 文件可停止。', flush=True)
    controlled_wait(3, guard)
    for step in range(1, args.max_steps + 1):
        current = point(state['current'])
        if current == navigator.target:
            return state
        fresh = read_current(client, state['source'])
        verify_unchanged(state, fresh, check_nodes=False)
        state = reader.wait_for_neighborhood(fresh, args.timeout)
        navigator.update(state, count_visit=False)
        target = navigator.next_step(current)
        candidate = next((n for n in state['nodes'] if point(n) == target), None)
        if candidate is None or not candidate['navigationCandidate']:
            raise ValueError('下一跳不是当前回包中的普通相邻地块')
        x, y = navigator.layout.click_point(current, target)
        guard()
        verify_unchanged(state, read_current(client, state['source']))
        progress.update(status='clicking', inFlight=True, nextTarget=list(target), plan=navigator.plan)
        save(folder / 'progress.json', progress)
        x, y = x + window['bounds']['X'], y + window['bounds']['Y']
        native('click', window['id'], x, y)
        pointer = {'x': x, 'y': y}
        after = reader.wait_for_move(state, target, args.timeout)
        state = reader.settle_after_move(
            after, navigator.layout.settle_seconds(current, target), args.timeout)
        navigator.update(state)
        progress.update(status='moving', inFlight=False, steps=step, current=list(target))
        save(folder / 'snapshot.json', state)
        save(folder / 'progress.json', progress)
        print(f'已移动【{direction(current, target)}】到 {target}；已走 {step} 步；'
              f'距离下限 {distance(target, navigator.target)} 步；'
              f'选路【{plan_description(navigator.plan)}】；周边已刷新', flush=True)
    if point(state['current']) != navigator.target:
        raise ValueError(f'达到本次 {args.max_steps} 步上限，已停止，可从当前位置重新规划')
    return state


def run(args):
    client = Reqable()
    folder = progress = None
    try:
        state = read_current(client)
        state = WorldReader(client, state['source'], lambda: None).wait_for_neighborhood(state, args.timeout)
        window = select_window(native('inspect'), args.window_id)
        layout = WorldLayout(window['bounds'], state['mapMaxY'])
        target, current = tuple(args.navigate), point(state['current'])
        navigator = Navigator(target, layout, state['mapMaxX'], state['mapMaxY'])
        navigator.update(state)
        next_target = navigator.next_step(current)
        print(f'当前位置：{current}；目标：{target}；方向：{direction(current, target)}；'
              f'无障碍距离下限 {distance(current, target)} 步（不是预计实走步数）', flush=True)
        print(f'下一跳：{next_target}；未探索地块不能保证可达或全程最短。', flush=True)
        if next_target is not None:
            print(f'选路【{plan_description(navigator.plan)}】', flush=True)
        if current == target or not args.execute:
            return
        if state['stateAtLogin'] == 1:
            raise ValueError('登录时人物处于绑定礼物状态，请完成当前挑战并重进大世界后导航')
        folder = ROOT / 'runs' / ('navigate-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        folder.mkdir(parents=True)
        progress = {'target': list(target), 'current': list(current), 'steps': 0,
                    'inFlight': False, 'status': 'prepared', 'source': state['source'],
                    'plan': navigator.plan}
        save(folder / 'progress.json', progress)
        save(folder / 'snapshot.json', state)
        print(f'导航记录：{folder}', flush=True)
        execute(client, window, state, navigator, folder, args, progress)
        progress.update(status='arrived', inFlight=False)
        save(folder / 'progress.json', progress)
        print(f"已到达 {target}，实际移动 {progress['steps']} 步。", flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        if folder and progress is not None:
            progress.update(status='stopped', error=str(exc) or '用户中断')
            save(folder / 'progress.json', progress)
        raise
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--navigate', type=int, nargs=2, required=True, metavar=('X', 'Y'))
    parser.add_argument('--window-id', type=int)
    parser.add_argument('--max-steps', type=int, default=200)
    parser.add_argument('--timeout', type=float, default=10)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.max_steps <= 2000 or not 1 <= args.timeout <= 60:
        parser.error('max-steps 必须为 1..2000，timeout 必须为 1..60 秒')
    try:
        run(args)
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
