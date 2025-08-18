#!/usr/bin/env python3
import os
import json
import sys
from typing import List, Dict

import oandapyV20
from oandapyV20.endpoints import positions as oanda_positions


def load_config() -> Dict:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    cfg_path = os.path.join(root, 'config.json')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def summarize_positions(resp: Dict) -> List[Dict]:
    out = []
    for p in resp.get('positions', []):
        instrument = p.get('instrument')
        long_units = float(p.get('long', {}).get('units', 0) or 0)
        short_units = float(p.get('short', {}).get('units', 0) or 0)
        out.append({
            'instrument': instrument,
            'long_units': long_units,
            'short_units': short_units,
            'long_price': float(p.get('long', {}).get('averagePrice', 0) or 0),
            'short_price': float(p.get('short', {}).get('averagePrice', 0) or 0),
        })
    return out


def main():
    cfg = load_config()
    account_id = cfg.get('oanda_account_id')
    token = cfg.get('oanda_access_token')
    env = cfg.get('oanda_environment', 'practice')

    if not account_id or not token:
        print('Config error: oanda_account_id or oanda_access_token missing', file=sys.stderr)
        sys.exit(2)

    api = oandapyV20.API(access_token=token, environment=env)

    # List open positions
    r = oanda_positions.OpenPositions(accountID=account_id)
    resp = api.request(r)
    positions = summarize_positions(resp)

    print('Open positions:')
    if not positions:
        print('(none)')
        return
    for pos in positions:
        print(f"- {pos['instrument']}: long={pos['long_units']} @ {pos['long_price']} | short={pos['short_units']} @ {pos['short_price']}")

    # Close all detected positions
    print('\nClosing detected positions...')
    for pos in positions:
        instrument = pos['instrument']
        if pos['long_units'] > 0:
            data = {"longUnits": "ALL"}
            try:
                rc = oanda_positions.PositionClose(accountID=account_id, instrument=instrument, data=data)
                close_resp = api.request(rc)
                print(f"Closed LONG {instrument}: {close_resp.get('longOrderFillTransaction') or close_resp}")
            except Exception as e:
                print(f"Error closing LONG {instrument}: {e}")
        if pos['short_units'] > 0:
            data = {"shortUnits": "ALL"}
            try:
                rc = oanda_positions.PositionClose(accountID=account_id, instrument=instrument, data=data)
                close_resp = api.request(rc)
                print(f"Closed SHORT {instrument}: {close_resp.get('shortOrderFillTransaction') or close_resp}")
            except Exception as e:
                print(f"Error closing SHORT {instrument}: {e}")


if __name__ == '__main__':
    main()


