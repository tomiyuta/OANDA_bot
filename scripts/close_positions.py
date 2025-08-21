#!/usr/bin/env python3
"""
OANDA口座の既存ポジションをすべてクリアするスクリプト（REST API v20対応版）
"""

import os
import json
import sys
import requests
from typing import List, Dict


def load_config() -> Dict:
    """設定ファイルを読み込み"""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    cfg_path = os.path.join(root, 'config.json')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def make_request(method: str, url: str, token: str, data: Dict = None) -> Dict:
    """APIリクエスト実行"""
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, timeout=30)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        print(f"APIリクエストエラー: {e}", file=sys.stderr)
        sys.exit(1)


def summarize_positions(resp: Dict) -> List[Dict]:
    """ポジション情報を整理"""
    out = []
    if 'positions' in resp:
        for p in resp['positions']:
            instrument = p.get('instrument')
            long_data = p.get('long', {})
            short_data = p.get('short', {})

            long_units = float(long_data.get('units', 0))
            short_units = float(short_data.get('units', 0))

            out.append({
                'instrument': instrument,
                'long_units': long_units,
                'short_units': short_units,
                'long_price': float(long_data.get('averagePrice', 0)),
                'short_price': float(short_data.get('averagePrice', 0)),
            })
    return out


def main():
    cfg = load_config()
    account_id = cfg.get('oanda_account_id')
    token = cfg.get('oanda_access_token')
    env = cfg.get('oanda_environment', 'practice')

    if not account_id or not token:
        print('設定エラー: oanda_account_id または oanda_access_token が設定されていません', file=sys.stderr)
        sys.exit(2)

    # APIエンドポイント設定
    if env == 'live':
        base_url = "https://api-fxtrade.oanda.com/v3"
    else:
        base_url = "https://api-fxpractice.oanda.com/v3"

    # 現在のポジションを取得
    try:
        positions_url = f"{base_url}/accounts/{account_id}/positions"
        resp = make_request('GET', positions_url, token)
        positions = summarize_positions(resp)

        print('現在のポジション:')
        if not positions:
            print('(なし)')
            return

        for pos in positions:
            print(f"- {pos['instrument']}: ロング={pos['long_units']} @ {pos['long_price']} | ショート={pos['short_units']} @ {pos['short_price']}")

        # すべてのポジションを決済
        print('\nポジションを決済中...')
        for pos in positions:
            instrument = pos['instrument']

            if pos['long_units'] > 0:
                # ロングポジションを決済（売り）
                order_data = {
                    "order": {
                        "type": "MARKET",
                        "instrument": instrument,
                        "units": -int(pos['long_units']),
                        "timeInForce": "FOK"
                    }
                }

                try:
                    order_url = f"{base_url}/accounts/{account_id}/orders"
                    close_resp = make_request('POST', order_url, token, order_data)
                    if 'orderFillTransaction' in close_resp:
                        print(f"✓ ロングポジション決済完了: {instrument}")
                    else:
                        print(f"✗ ロングポジション決済失敗: {instrument}")
                except Exception as e:
                    print(f"✗ ロングポジション決済エラー {instrument}: {e}")

            if pos['short_units'] > 0:
                # ショートポジションを決済（買い）
                order_data = {
                    "order": {
                        "type": "MARKET",
                        "instrument": instrument,
                        "units": int(pos['short_units']),
                        "timeInForce": "FOK"
                    }
                }

                try:
                    order_url = f"{base_url}/accounts/{account_id}/orders"
                    close_resp = make_request('POST', order_url, token, order_data)
                    if 'orderFillTransaction' in close_resp:
                        print(f"✓ ショートポジション決済完了: {instrument}")
                    else:
                        print(f"✗ ショートポジション決済失敗: {instrument}")
                except Exception as e:
                    print(f"✗ ショートポジション決済エラー {instrument}: {e}")

    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()


