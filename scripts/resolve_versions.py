#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def parse_tokens(raw: str):
    return [token.strip() for token in re.split(r"[\s,]+", raw) if token.strip()]


def main():
    parser = argparse.ArgumentParser(description="Resolve benchmark version list")
    parser.add_argument("--raw", default="", help="Optional raw version input")
    parser.add_argument("--config", default="config/versions.txt", help="Fallback config file")
    args = parser.parse_args()

    tokens = parse_tokens(args.raw.strip()) if args.raw.strip() else []

    if not tokens:
        config = Path(args.config)
        if config.exists():
            for line in config.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens.append(line)

    if not tokens:
        raise SystemExit("No versions configured. Set workflow input versions or edit config/versions.txt")

    print(json.dumps(tokens))


if __name__ == "__main__":
    main()
