#!/usr/bin/env python3
"""
OANDA口座のポジション一覧を表示するスクリプト（REST API v20対応版）
"""

import os
import json
import requests
from typing import Dict


def load_config() -> Dict:
    """設定ファイルを読み込み"""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    cfg_path = os.path.join(root, 'config.json')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def make_request(url: str, token: str) -> Dict:
    """APIリクエスト実行"""
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"APIリクエストエラー: {e}")
        return {}


def main():
    cfg = load_config()
    account_id = cfg.get('oanda_account_id')
    token = cfg.get('oanda_access_token')
    env = cfg.get('oanda_environment', 'practice')

    # APIエンドポイント設定
    if env == 'live':
        base_url = "https://api-fxtrade.oanda.com/v3"
    else:
        base_url = "https://api-fxpractice.oanda.com/v3"

    # 現在のポジションを取得
    positions_url = f"{base_url}/accounts/{account_id}/positions"
    resp = make_request(positions_url, token)

    positions = resp.get('positions', [])
    if not positions:
        print('現在のポジション: (なし)')
        return

    print('現在のポジション:')
    for p in positions:
        instrument = p.get('instrument')
        long_data = p.get('long', {})
        short_data = p.get('short', {})

        long_units = float(long_data.get('units', 0))
        short_units = float(short_data.get('units', 0))
        long_price = long_data.get('averagePrice', 'N/A')
        short_price = short_data.get('averagePrice', 'N/A')

        # 損益情報
        long_pnl = float(long_data.get('unrealizedPL', 0))
        short_pnl = float(short_data.get('unrealizedPL', 0))

        print(f"- {instrument}:")
        if long_units > 0:
            print(f"  ロング: {long_units} 単位 @ {long_price} (損益: {long_pnl})")
        if short_units > 0:
            print(f"  ショート: {short_units} 単位 @ {short_price} (損益: {short_pnl})")


if __name__ == '__main__':
    main()


