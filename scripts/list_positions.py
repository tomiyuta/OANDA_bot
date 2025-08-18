#!/usr/bin/env python3
import os
import json
from typing import Dict

import oandapyV20
from oandapyV20.endpoints import positions as oanda_positions


def load_config() -> Dict:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    cfg_path = os.path.join(root, 'config.json')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    cfg = load_config()
    account_id = cfg.get('oanda_account_id')
    token = cfg.get('oanda_access_token')
    env = cfg.get('oanda_environment', 'practice')

    api = oandapyV20.API(access_token=token, environment=env)
    r = oanda_positions.OpenPositions(accountID=account_id)
    resp = api.request(r)

    positions = resp.get('positions', [])
    if not positions:
        print('Open positions: (none)')
        return

    print('Open positions:')
    for p in positions:
        instrument = p.get('instrument')
        long_units = float(p.get('long', {}).get('units', 0) or 0)
        short_units = float(p.get('short', {}).get('units', 0) or 0)
        long_price = p.get('long', {}).get('averagePrice')
        short_price = p.get('short', {}).get('averagePrice')
        print(f"- {instrument}: long={long_units} @ {long_price} | short={short_units} @ {short_price}")


if __name__ == '__main__':
    main()


