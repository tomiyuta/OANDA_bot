#!/usr/bin/env python3
"""
OANDA口座の既存ポジションをすべてクリアするスクリプト
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
        
        if not positions:
            print("クリアするポジションはありません")
            return
        
        # すべてのポジションをクリア
        for pos in positions:
            try:
                print(f"ポジションをクリア中: {pos.symbol} {pos.side} {pos.size}")
                result = broker.close_position(pos.symbol, pos.position_id, pos.size, pos.side)
                if result:
                    print(f"✓ ポジションクリア成功: {pos.symbol}")
                else:
                    print(f"✗ ポジションクリア失敗: {pos.symbol}")
            except Exception as e:
                print(f"✗ ポジションクリアエラー: {pos.symbol} - {e}")
        
        # 最終確認
        final_positions = broker.get_all_positions()
        print(f"\nクリア後のポジション数: {len(final_positions)}")
        
    except Exception as e:
        print(f"ポジション操作エラー: {e}")

if __name__ == "__main__":
    main()
