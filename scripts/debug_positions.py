#!/usr/bin/env python3
"""
OANDA口座のポジション情報を詳しく確認するデバッグスクリプト
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oanda_broker import OANDABroker
import json

def main():
    # 設定ファイルを読み込み
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("設定ファイル config.json が見つかりません")
        return
    
    # OANDAブローカーを初期化
    try:
        broker = OANDABroker(config)
        print("OANDAブローカー初期化完了")
    except Exception as e:
        print(f"ブローカー初期化エラー: {e}")
        return
    
    # 現在のポジションを取得
    try:
        positions = broker.get_all_positions()
        print(f"\n現在のポジション数: {len(positions)}")
        
        if positions:
            print("\n=== ポジション詳細（デバッグ） ===")
            for i, pos in enumerate(positions):
                print(f"ポジション {i+1}:")
                print(f"  通貨ペア: {pos.symbol}")
                print(f"  サイド: {pos.side}")
                print(f"  サイズ: {pos.size}")
                print(f"  エントリープライス: {pos.price}")
                print(f"  ポジションID: '{pos.position_id}'")
                print(f"  ポジションIDの型: {type(pos.position_id)}")
                print(f"  ポジションIDの長さ: {len(str(pos.position_id)) if pos.position_id else 0}")
                print(f"  全属性: {dir(pos)}")
                print("---")
        else:
            print("現在ポジションはありません")
            
    except Exception as e:
        print(f"ポジション取得エラー: {e}")

if __name__ == "__main__":
    main()

