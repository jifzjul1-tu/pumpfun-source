"""Time exact distance on captured AMM sell tokens; no build or VM execution."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--module', type=Path, help='Optional baseline sell_score.py')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    root = args.repo.resolve()
    source = args.module or root / 'scripts/amm_sell/sell_score.py'
    sys.path.insert(0, str(root / 'scripts'))
    spec = importlib.util.spec_from_file_location('benchmark_sell_score', source)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    raw = (root / 'evidence/original/pump_amm/deployed.so').read_bytes()
    if hashlib.sha256(raw).hexdigest() != tool.ORIGINAL_SHA:
        raise ValueError('original image hash mismatch')
    image = type('Image', (), {'b': raw})()
    rows = tool.listing(image, tool.SELL_START, tool.SELL_SIZE)
    original = tool.normalized_tokens(rows)
    # Change one immediate in a copy of an actual instruction near the middle.
    # This is a synthetic negative control, never a compiled candidate or replay.
    movs = [r for r in rows if r['fields'][0] == 'mov' and r['fields'][2] is None]
    row = min(movs, key=lambda r: abs(r['off'] - tool.SELL_SIZE // 2))
    changed = bytearray(raw)
    changed[tool.SELL_START + row['off'] + 4] ^= 1
    modified = tool.normalized_tokens(tool.listing(
        type('Image', (), {'b': bytes(changed)})(), tool.SELL_START, tool.SELL_SIZE))
    results = {}
    for name, other, expected in [('identical', list(original), 0),
                                  ('one_immediate_changed', modified, 1)]:
        times = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            distance = tool.alignment_distance(original, other)
            times.append(time.perf_counter() - start)
            if distance != expected:
                raise ValueError(f'{name}: expected distance {expected}, got {distance}')
        results[name] = {'distance': distance, 'median_seconds': statistics.median(times)}
    print(json.dumps({'image_sha256': hashlib.sha256(raw).hexdigest(),
                      'tool_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'tokens': len(original), 'repeats': args.repeats,
                      'changed_instruction_offset': row['off'], 'results': results}, indent=2))


if __name__ == '__main__':
    main()
