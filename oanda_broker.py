#!/usr/bin/env python3
"""
OANDA Broker Implementation
Version: 1.0.0
License: MIT License
Copyright (c) 2024 Trading Bot

OANDA specific implementation of the broker interface.
"""

import oandapyV20
from oandapyV20.endpoints import accounts, orders, pricing, positions
from oandapyV20 import V20Error
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from broker_base import BrokerBase, Balance, Order, Position, Ticker


class OANDABroker(BrokerBase):
    """
    OANDA用ブローカー実装
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        初期化
        
        Args:
            config: OANDA設定辞書
        """
        super().__init__(config)
        
        # OANDA API設定
        self.account_id = config.get('account_id', '')
        self.access_token = config.get('access_token', '')
        self.environment = config.get('environment', 'practice')  # 'practice' or 'live'
        
        # OANDA APIクライアント初期化
        self.client = oandapyV20.API(
            access_token=self.access_token,
            environment=self.environment
        )
        
        # レート制限管理
        self.last_request_time = 0
        self.request_count = 0
        self.current_rate_limit = 120  # OANDAは120回/分
    
    def get_balance(self) -> Optional[Balance]:
        """口座残高を取得"""
        try:
            r = accounts.AccountDetails(accountID=self.account_id)
            response = self.client.request(r)
            
            if 'account' in response:
                account = response['account']
                return Balance(
                    available_amount=float(account.get('NAV', 0)),
                    total_balance=float(account.get('balance', 0)),
                    currency=account.get('currency', 'USD')
                )
            
            return None
            
        except V20Error as e:
            logging.error(f"OANDA残高取得エラー: {e}")
            return None
        except Exception as e:
            logging.error(f"残高取得エラー: {e}")
            return None
    
    def create_order(self, symbol: str, side: str, size: Optional[float] = None, 
                    leverage: Optional[float] = None) -> Optional[Order]:
        """注文を作成"""
        try:
            # OANDAの通貨ペア形式に変換（USD_JPY → USD_JPY）
            oanda_symbol = symbol
            
            # 注文データ作成
            order_data = {
                "order": {
                    "type": "MARKET",
                    "instrument": oanda_symbol,
                    "units": str(int(size)) if size else "1000",  # デフォルト1000ユニット
                    "side": side.lower()  # OANDAは小文字
                }
            }
            
            r = orders.OrderCreate(accountID=self.account_id, data=order_data)
            response = self.client.request(r)
            
            if 'orderFillTransaction' in response:
                order_fill = response['orderFillTransaction']
                return Order(
                    order_id=order_fill.get('id', ''),
                    symbol=oanda_symbol,
                    side=side,
                    size=float(order_fill.get('units', 0)),
                    price=float(order_fill.get('price', 0)),
                    status="FILLED"
                )
            
            return None
            
        except V20Error as e:
            logging.error(f"OANDA注文作成エラー: {e}")
            return None
        except Exception as e:
            logging.error(f"注文作成エラー: {e}")
            return None
    
    def close_position(self, symbol: str, position_id: str, size: float, side: str) -> Optional[float]:
        """ポジションを決済"""
        try:
            # OANDAの通貨ペア形式に変換
            oanda_symbol = symbol
            
            # 決済データ作成
            close_data = {
                "longUnits": str(int(size)) if side == "BUY" else "0",
                "shortUnits": str(int(size)) if side == "SELL" else "0"
            }
            
            r = positions.PositionClose(accountID=self.account_id, instrument=oanda_symbol, data=close_data)
            response = self.client.request(r)
            
            if 'longOrderFillTransaction' in response:
                order_fill = response['longOrderFillTransaction']
                return float(order_fill.get('price', 0))
            elif 'shortOrderFillTransaction' in response:
                order_fill = response['shortOrderFillTransaction']
                return float(order_fill.get('price', 0))
            
            return None
            
        except V20Error as e:
            logging.error(f"OANDA決済エラー: {e}")
            return None
        except Exception as e:
            logging.error(f"決済エラー: {e}")
            return None
    
    def get_tickers(self, symbols: List[str]) -> Optional[Dict[str, Ticker]]:
        """ティッカー情報を取得"""
        try:
            tickers = {}
            
            for symbol in symbols:
                # OANDAの通貨ペア形式に変換
                oanda_symbol = symbol
                
                r = pricing.PricingInfo(accountID=self.account_id, params={"instruments": oanda_symbol})
                response = self.client.request(r)
                
                if 'prices' in response and len(response['prices']) > 0:
                    price_data = response['prices'][0]
                    tickers[symbol] = Ticker(
                        symbol=symbol,
                        bid=float(price_data.get('bids', [{}])[0].get('price', 0)),
                        ask=float(price_data.get('asks', [{}])[0].get('price', 0)),
                        timestamp=price_data.get('time', '')
                    )
            
            return tickers
            
        except V20Error as e:
            logging.error(f"OANDAティッカー取得エラー: {e}")
            return None
        except Exception as e:
            logging.error(f"ティッカー取得エラー: {e}")
            return None
    
    def check_current_positions(self, symbol: str) -> List[Position]:
        """現在のポジションを取得"""
        try:
            # OANDAの通貨ペア形式に変換
            oanda_symbol = symbol
            
            r = positions.OpenPositions(accountID=self.account_id)
            response = self.client.request(r)
            
            positions_list = []
            if 'positions' in response:
                for pos_data in response['positions']:
                    if pos_data.get('instrument') == oanda_symbol:
                        long_units = float(pos_data.get('long', {}).get('units', 0))
                        short_units = float(pos_data.get('short', {}).get('units', 0))
                        
                        if long_units > 0:
                            positions_list.append(Position(
                                position_id=pos_data.get('long', {}).get('id', ''),
                                symbol=oanda_symbol,
                                side="BUY",
                                size=long_units,
                                price=float(pos_data.get('long', {}).get('price', 0)),
                                open_time=pos_data.get('long', {}).get('openTime', ''),
                                unrealized_pnl=float(pos_data.get('long', {}).get('unrealizedPL', 0))
                            ))
                        
                        if short_units > 0:
                            positions_list.append(Position(
                                position_id=pos_data.get('short', {}).get('id', ''),
                                symbol=oanda_symbol,
                                side="SELL",
                                size=short_units,
                                price=float(pos_data.get('short', {}).get('price', 0)),
                                open_time=pos_data.get('short', {}).get('openTime', ''),
                                unrealized_pnl=float(pos_data.get('short', {}).get('unrealizedPL', 0))
                            ))
            
            return positions_list
            
        except V20Error as e:
            logging.error(f"OANDAポジションチェックエラー: {e}")
            return []
        except Exception as e:
            logging.error(f"ポジションチェックエラー: {e}")
            return []
    
    def get_all_positions(self) -> List[Position]:
        """全ポジションを取得"""
        try:
            r = positions.OpenPositions(accountID=self.account_id)
            response = self.client.request(r)
            
            positions_list = []
            if 'positions' in response:
                for pos_data in response['positions']:
                    symbol = pos_data.get('instrument', '')
                    long_units = float(pos_data.get('long', {}).get('units', 0))
                    short_units = float(pos_data.get('short', {}).get('units', 0))
                    
                    if long_units > 0:
                        positions_list.append(Position(
                            position_id=pos_data.get('long', {}).get('id', ''),
                            symbol=symbol,
                            side="BUY",
                            size=long_units,
                            price=float(pos_data.get('long', {}).get('price', 0)),
                            open_time=pos_data.get('long', {}).get('openTime', ''),
                            unrealized_pnl=float(pos_data.get('long', {}).get('unrealizedPL', 0))
                        ))
                    
                    if short_units > 0:
                        positions_list.append(Position(
                            position_id=pos_data.get('short', {}).get('id', ''),
                            symbol=symbol,
                            side="SELL",
                            size=short_units,
                            price=float(pos_data.get('short', {}).get('price', 0)),
                            open_time=pos_data.get('short', {}).get('openTime', ''),
                            unrealized_pnl=float(pos_data.get('short', {}).get('unrealizedPL', 0))
                        ))
            
            return positions_list
            
        except V20Error as e:
            logging.error(f"OANDA全ポジション取得エラー: {e}")
            return []
        except Exception as e:
            logging.error(f"全ポジション取得エラー: {e}")
            return []
    
    def get_position_by_order_id(self, order_data: List[Dict[str, Any]]) -> Optional[Position]:
        """注文IDからポジション情報を取得"""
        try:
            if not order_data or not isinstance(order_data, list) or len(order_data) == 0:
                logging.error("無効な注文データ形式")
                return None
                
            order_id = order_data[0].get('orderId')
            if not order_id:
                logging.error("注文IDが存在しません")
                return None

            # OANDAでは注文IDから直接ポジションを取得するAPIがないため、
            # 全ポジションから該当するものを検索
            all_positions = self.get_all_positions()
            for pos in all_positions:
                # 注文IDとポジションIDの関連付けはOANDAの仕様に依存
                # ここでは簡易的な実装
                if pos.position_id == order_id:
                    return pos

            logging.error("ポジション情報が見つかりません")
            return None

        except Exception as e:
            logging.error(f"ポジション情報取得エラー: {e}")
            return None
    
    def get_execution_fee(self, order_id: str) -> float:
        """約定手数料を取得"""
        try:
            # OANDAでは手数料は別途計算が必要
            # ここでは簡易的に0を返す
            return 0.0
            
        except Exception as e:
            logging.error(f"手数料取得エラー: {e}")
            return 0.0
    
    def get_execution_price(self, order_id: str) -> float:
        """約定価格を取得"""
        try:
            # OANDAでは注文IDから約定価格を直接取得するAPIがないため、
            # 全ポジションから該当するものを検索
            all_positions = self.get_all_positions()
            for pos in all_positions:
                if pos.position_id == order_id:
                    return pos.price

            return 0.0
            
        except Exception as e:
            logging.error(f"約定価格取得エラー: {e}")
            return 0.0
    
    def validate_config(self) -> bool:
        """設定の妥当性を検証"""
        if not self.account_id:
            return False
        if not self.access_token:
            return False
        if self.environment not in ['practice', 'live']:
            return False
        return True 