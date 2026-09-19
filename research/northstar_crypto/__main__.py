import argparse
import json
from pathlib import Path

from .runner import run
from .config import Config


def main():
    parser = argparse.ArgumentParser(description='Northstar Crypto research and OKX demo-only execution')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'output')
    parser.add_argument('--input', type=Path, help='Replay captured public data; never changes the forward ledger')
    parser.add_argument('--observations', type=Path, help='Timestamped LLM observations JSONL; no position effect')
    parser.add_argument('--compare', action='store_true', help='Produce historical diagnostics for three variants')
    parser.add_argument('--replay-capital', type=float, help='Explicit USDT capital for archived-input diagnostics only')
    parser.add_argument('--demo-execute', action='store_true', help='Execute eligible signals on OKX DEMO spot only')
    args = parser.parse_args()
    snapshot = json.loads(args.input.read_text(encoding='utf-8')) if args.input else None
    if args.replay_capital is not None and snapshot is None:
        parser.error('--replay-capital requires --input; forward funding always comes from the OKX demo account')
    if args.demo_execute and args.input:
        parser.error('--demo-execute cannot be used with archived --input')
    pointer, artifact = run(args.output, snapshot=snapshot, observations=args.observations,
                            comparison=args.compare, config=Config(initial_cash=args.replay_capital), execute_demo=args.demo_execute)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    return 0 if artifact['status'] == 'COMPLETE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
