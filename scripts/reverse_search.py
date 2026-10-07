"""Build and invoke the bounded reverse beam search helper."""

import json
from pathlib import Path
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


def reverse_search(cards, seconds, width=10000):
    started = time.monotonic()
    build_helper()
    remaining = seconds - (time.monotonic() - started)
    if remaining <= 0:
        return {'status': 'timeout', 'order': [], 'visited': 0, 'bestDepth': 0}
    types = {kind: i for i, kind in enumerate(sorted({card.type for card in cards}))}
    request = f'{len(cards)} {width} {remaining}\n' + '\n'.join(
        f'{types[card.type]} {card.x} {card.y} {card.layer}' for card in cards)
    try:
        process = subprocess.run([str(HELPER)], input=request, text=True,
                                 capture_output=True, check=True, timeout=remaining + 2)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'order': [], 'visited': 0, 'bestDepth': 0}
    result = json.loads(process.stdout)
    result['backend'] = 'reverse_beam'
    return result
