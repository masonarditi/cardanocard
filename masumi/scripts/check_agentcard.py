"""Presence check by default; --authenticate checks organization sandbox access only."""
import argparse
import json
from pathlib import Path

from cardano_card.agentcard_health import check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authenticate', action='store_true')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[2] / 'agentcard'
    report = check(directory, authenticate=args.authenticate)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] in {'configuration_present', 'organization_sandbox_verified'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
