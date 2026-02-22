#!/usr/bin/env python3
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Print JSON array values one per line")
    parser.add_argument("json_array", help="JSON array string")
    args = parser.parse_args()

    values = json.loads(args.json_array)
    if not isinstance(values, list):
        raise SystemExit("Input must be a JSON array")

    for value in values:
        print(str(value))


if __name__ == "__main__":
    main()
