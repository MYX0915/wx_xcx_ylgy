"""Build and invoke the bounded reverse beam search helper."""

import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time


SOURCE = Path(__file__).with_name('solve_map_native.cpp')
HELPER = SOURCE.with_suffix('')


def build_helper():
    if HELPER.is_file() and HELPER.stat().st_mtime >= SOURCE.stat().st_mtime:
        return
    compiler = shutil.which('clang++') or shutil.which('c++')
    if not compiler:
        raise RuntimeError('缺少 C++ 编译器，请安装 Xcode Command Line Tools')
    with tempfile.TemporaryDirectory(prefix='sheep-solver-', dir=SOURCE.parent) as folder:
        output = Path(folder) / HELPER.name
        subprocess.run([compiler, '-O3', '-std=c++17', str(SOURCE), '-o', str(output)],
                       check=True, capture_output=True, text=True, timeout=60)
        output.replace(HELPER)


def reverse_search(cards, seconds, width=10000, seed=0):
    started = time.monotonic()
    build_helper()
    remaining = seconds - (time.monotonic() - started)
    if remaining <= 0:
        return {'status': 'timeout', 'order': [], 'visited': 0, 'bestDepth': 0}
    types = {kind: i for i, kind in enumerate(sorted({card.type for card in cards}))}
    request = f'{len(cards)} {width} {remaining} {seed}\n' + '\n'.join(
        f'{types[card.type]} {card.x} {card.y} {card.layer}' for card in cards)
    try:
        process = subprocess.run([str(HELPER)], input=request, text=True,
                                 capture_output=True, check=True, timeout=remaining + 2)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'order': [], 'visited': 0, 'bestDepth': 0}
    result = json.loads(process.stdout)
    result['backend'] = 'reverse_beam'
    return result


def parallel_reverse_search(cards, seconds, workers=4, seed=0, validate=None):
    started = time.monotonic()
    build_helper()
    deadline = started + seconds
    if seconds <= 0 or time.monotonic() >= deadline:
        return {'status': 'timeout', 'order': [], 'visited': 0, 'bestDepth': 0,
                'attempts': 0, 'backend': 'reverse_beam_parallel'}
    workers = max(1, min(workers, os.cpu_count() or 1))
    types = {kind: i for i, kind in enumerate(sorted({card.type for card in cards}))}
    card_rows = '\n'.join(
        f'{types[card.type]} {card.x} {card.y} {card.layer}' for card in cards)
    initial_widths = (10000, 50000, 30000, 20000)
    retry_widths = (10000, 20000, 30000, 50000)
    attempts_by_worker = [0] * workers
    jobs = {}
    completed = 0
    visited = 0
    best_depth = 0
    statuses = []

    def launch(slot):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        attempt = attempts_by_worker[slot]
        width = (initial_widths[slot] if attempt == 0
                 else retry_widths[(attempt + slot) % len(retry_widths)])
        worker_seed = seed + slot + attempt * workers
        request = f'{len(cards)} {width} {remaining} {worker_seed}\n{card_rows}'
        process = subprocess.Popen([str(HELPER)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        process.stdin.write(request)
        process.stdin.close()
        jobs[slot] = {'process': process, 'width': width, 'seed': worker_seed}
        attempts_by_worker[slot] += 1
        return True

    def stop_jobs():
        processes = [job['process'] for job in jobs.values()]
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    try:
        for slot in range(workers):
            if not launch(slot):
                break
        while jobs and time.monotonic() < deadline:
            finished = []
            for slot, job in list(jobs.items()):
                process = job['process']
                if process.poll() is None:
                    continue
                stdout = process.stdout.read()
                stderr = process.stderr.read()
                process.wait()
                process.stdout.close()
                process.stderr.close()
                if process.returncode:
                    raise RuntimeError(stderr.strip() or 'Native solver failed')
                result = json.loads(stdout)
                completed += 1
                visited += result.get('visited', 0)
                best_depth = max(best_depth, result.get('bestDepth', 0))
                statuses.append(result['status'])
                if result['status'] == 'solved' and (validate is None or validate(result['order'])):
                    result.update(backend='reverse_beam_parallel', attempts=completed,
                                  visited=visited, bestDepth=best_depth,
                                  workers=workers,
                                  elapsedSeconds=round(time.monotonic() - started, 3))
                    jobs.pop(slot)
                    stop_jobs()
                    return result
                if result['status'] == 'unsatisfiable':
                    result.update(backend='reverse_beam_parallel', attempts=completed,
                                  visited=visited, bestDepth=best_depth,
                                  workers=workers,
                                  elapsedSeconds=round(time.monotonic() - started, 3))
                    jobs.pop(slot)
                    stop_jobs()
                    return result
                finished.append(slot)
            for slot in finished:
                jobs.pop(slot, None)
                if time.monotonic() < deadline:
                    launch(slot)
            if not finished:
                time.sleep(0.05)
    finally:
        stop_jobs()

    status = 'timeout' if time.monotonic() >= deadline or 'timeout' in statuses else 'search_exhausted'
    return {'status': status, 'order': [], 'visited': visited, 'bestDepth': best_depth,
            'attempts': completed, 'workers': workers, 'backend': 'reverse_beam_parallel',
            'elapsedSeconds': round(time.monotonic() - started, 3)}
