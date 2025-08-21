#!/usr/bin/env python3
"""
OANDA REST API v20 Broker Implementation
Version: 3.0.0
License: MIT License
Copyright (c) 2024 Trading Bot

OANDA REST API v20 specific implementation of the broker interface.
Uses direct HTTP requests for better control and latest API features.
"""

import requests
import json
import logging
import time
from typing import Optional, List, Dict, Any
from datetime import datetime

from broker_base import BrokerBase, Balance, Order, Position, Ticker


class OANDABroker(BrokerBase):
    """
    OANDA REST API v20 用ブローカー実装
    最新のOANDA API v20仕様に準拠
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初期化

        Args:
            config: OANDA設定辞書
        """
        super().__init__(config)

        # OANDA API設定
        self.account_id = config.get('oanda_account_id', '')
        self.access_token = config.get('oanda_access_token', '')
        self.environment = config.get('oanda_environment', 'practice')  # 'practice' or 'live'

        # API エンドポイント設定
        if self.environment == 'live':
            self.base_url = "https://api-fxtrade.oanda.com/v3"
        else:
            self.base_url = "https://api-fxpractice.oanda.com/v3"

        # HTTPセッション
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

        # レート制限管理 (120回/分)
        self.last_request_time = 0
        self.request_count = 0
        self.current_rate_limit = 120

    def _rate_limit_wait(self):
        """レート制限を管理する"""
        current_time = time.time()
        time_diff = current_time - self.last_request_time

        # 1分ごとにカウンターをリセット
        if time_diff >= 60:
            self.request_count = 0
            self.last_request_time = current_time

        # レート制限チェック
        if self.request_count >= self.current_rate_limit:
            wait_time = 60 - time_diff
            if wait_time > 0:
                logging.info(f"[{self.name}] レート制限待機中: {wait_time:.1f}秒")
                time.sleep(wait_time)
            self.request_count = 0

        self.request_count += 1

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """APIリクエストを実行する"""
        self._rate_limit_wait()

        url = f"{self.base_url}{endpoint}"

        try:
            if method == 'GET':
                response = self.session.get(url, params=data, timeout=30)
            elif method == 'POST':
                response = self.session.post(url, json=data, timeout=30)
            elif method == 'PUT':
                response = self.session.put(url, json=data, timeout=30)
            elif method == 'DELETE':
                response = self.session.delete(url, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logging.error(f"[{self.name}] APIリクエストエラー: {e}")
            return None
        except json.JSONDecodeError as e:
            logging.error(f"[{self.name}] JSONパースエラー: {e}")
            return None

    def entry(self, trade: List[str]) -> bool:
        """
        OANDAでエントリー注文を実行
        
        Args:
            trade: トレードデータ [date, time, symbol, side, size, entry_time, exit_time]
            
        Returns:
            bool: 成功時はTrue
        """
        try:
            if len(trade) < 5:
                logging.error(f"[{self.name}] 無効なトレードデータ: {trade}")
                return False
            
            date, time_str, symbol, side, size_str = trade[:5]
            
            # サイズを数値に変換
            try:
                size = float(size_str) if size_str else None
            except ValueError:
                logging.error(f"[{self.name}] 無効なサイズ: {size_str}")
                return False
            
            # エントリー注文実行
            order = self.create_order(symbol, side, size, self.leverage)
            
            if order:
                # Discord通知
                msg = f"エントリー注文実行: {symbol} {side} {size}ロット"
                self.notify(msg)
                
                logging.info(f"[{self.name}] エントリー注文成功: {symbol} {side} {size}ロット")
                return True
            else:
                error_msg = f"エントリー注文失敗: {symbol} {side} {size}ロット"
                self.notify(error_msg)
                logging.error(f"[{self.name}] {error_msg}")
                return False
                
        except Exception as e:
            error_msg = f"エントリー処理エラー: {e}"
            self.notify(error_msg)
            logging.error(f"[{self.name}] {error_msg}")
            return False
    
    def exit(self, trade: List[str]) -> bool:
        """
        OANDAで決済注文を実行
        
        Args:
            trade: トレードデータ [date, time, symbol, side, size, entry_time, exit_time]
            
        Returns:
            bool: 成功時はTrue
        """
        try:
            if len(trade) < 5:
                logging.error(f"[{self.name}] 無効なトレードデータ: {trade}")
                return False
            
            date, time_str, symbol, side, size_str = trade[:5]
            
            # サイズを数値に変換
            try:
                size = float(size_str) if size_str else None
            except ValueError:
                logging.error(f"[{self.name}] 無効なサイズ: {size_str}")
                return False
            
            # 現在のポジションを取得
            positions = self.check_current_positions(symbol)
            
            if not positions:
                msg = f"決済対象ポジションなし: {symbol}"
                self.notify(msg)
                logging.warning(f"[{self.name}] {msg}")
                return True  # ポジションがない場合は成功として扱う
            
            # 決済方向を決定（エントリーと逆方向）
            exit_side = "SELL" if side == "BUY" else "BUY"
            
            success_count = 0
            for position in positions:
                if position.side == side:  # 同じ方向のポジションのみ決済
                    # 決済注文実行
                    exit_price = self.close_position(symbol, position.position_id, position.size, exit_side)
                    
                    if exit_price:
                        success_count += 1
                        # 損益計算
                        profit_pips = self.calculate_profit_pips(position.price, exit_price, side, symbol)
                        profit_amount = self.calculate_profit_amount(position.price, exit_price, side, symbol, position.size)
                        
                        # Discord通知
                        msg = f"決済完了: {symbol} {position.size}ロット 損益: {profit_pips}pips (¥{profit_amount})"
                        self.notify(msg)
                        
                        logging.info(f"[{self.name}] 決済成功: {symbol} 損益: {profit_pips}pips (¥{profit_amount})")
                    else:
                        error_msg = f"決済失敗: {symbol} {position.size}ロット"
                        self.notify(error_msg)
                        logging.error(f"[{self.name}] {error_msg}")
            
            if success_count > 0:
                return True
            else:
                return False
                
        except Exception as e:
            error_msg = f"決済処理エラー: {e}"
            self.notify(error_msg)
            logging.error(f"[{self.name}] {error_msg}")
            return False
    
    def get_balance(self) -> Optional[Balance]:
        """口座残高を取得"""
        try:
            response = self._make_request('GET', f'/accounts/{self.account_id}')

            if response and 'account' in response:
                account = response['account']
                return Balance(
                    available_amount=float(account.get('NAV', 0)),
                    total_balance=float(account.get('balance', 0)),
                    currency=account.get('currency', 'USD'),
                    leverage=float(account.get('marginRate', 1)) ** -1  # マージンレートからレバレッジを計算
                )

            return None

        except Exception as e:
            logging.error(f"[{self.name}] 残高取得エラー: {e}")
            return None
    
    def create_order(self, symbol: str, side: str, size: Optional[float] = None,
                    leverage: Optional[float] = None) -> Optional[Order]:
        """注文を作成"""
        try:
            # OANDAの通貨ペア形式に変換
            oanda_symbol = symbol.replace('_', '/')

            # デフォルトサイズ設定
            if size is None:
                size = 1000  # デフォルト1000ユニット

            # サイド変換 (BUY/SELL -> ユニット数)
            if side.upper() == 'SELL':
                units = -int(size)
            else:
                units = int(size)

            # 注文データ作成
            order_data = {
                "order": {
                    "type": "MARKET",
                    "instrument": oanda_symbol,
                    "units": units,
                    "timeInForce": "FOK"  # Fill or Kill
                }
            }

            response = self._make_request('POST', f'/accounts/{self.account_id}/orders', order_data)

            if response and 'orderFillTransaction' in response:
                order_fill = response['orderFillTransaction']
                return Order(
                    order_id=order_fill.get('id', ''),
                    symbol=oanda_symbol,
                    side=side.upper(),
                    size=abs(float(order_fill.get('units', 0))),
                    price=float(order_fill.get('price', 0)),
                    status="FILLED"
                )

            return None

        except Exception as e:
            logging.error(f"[{self.name}] 注文作成エラー: {e}")
            return None
    
    def close_position(self, symbol: str, position_id: str, size: float, side: str) -> Optional[float]:
        """ポジションを決済"""
        try:
            # OANDAの通貨ペア形式に変換
            oanda_symbol = symbol.replace('_', '/')

            # 決済用のユニット数を決定
            if side.upper() == 'BUY':
                # ロングポジションを決済（売り）
                units = -int(size)
            else:
                # ショートポジションを決済（買い）
                units = int(size)

            # 決済注文データ作成
            order_data = {
                "order": {
                    "type": "MARKET",
                    "instrument": oanda_symbol,
                    "units": units,
                    "timeInForce": "FOK",
                    "positionFill": "REDUCE_ONLY"  # ポジションを減らすのみ
                }
            }

            response = self._make_request('POST', f'/accounts/{self.account_id}/orders', order_data)

            if response and 'orderFillTransaction' in response:
                order_fill = response['orderFillTransaction']
                return float(order_fill.get('price', 0))

            return None

        except Exception as e:
            logging.error(f"[{self.name}] 決済エラー: {e}")
            return None
    
    def get_tickers(self, symbols: List[str]) -> Optional[Dict[str, Ticker]]:
        """ティッカー情報を取得"""
        try:
            tickers = {}

            # OANDAの通貨ペア形式に変換
            oanda_symbols = [symbol.replace('_', '/') for symbol in symbols]
            instruments = ','.join(oanda_symbols)

            params = {'instruments': instruments}
            response = self._make_request('GET', f'/accounts/{self.account_id}/pricing', params)

            if response and 'prices' in response:
                for price_data in response['prices']:
                    oanda_symbol = price_data.get('instrument', '')
                    # 内部形式に戻す
                    internal_symbol = oanda_symbol.replace('/', '_')

                    if 'bids' in price_data and 'asks' in price_data:
                        tickers[internal_symbol] = Ticker(
                            symbol=internal_symbol,
                            bid=float(price_data['bids'][0]['price']),
                            ask=float(price_data['asks'][0]['price']),
                            timestamp=price_data.get('time', '')
                        )

            return tickers

        except Exception as e:
            logging.error(f"[{self.name}] ティッカー取得エラー: {e}")
            return None
    
    def check_current_positions(self, symbol: str) -> List[Position]:
        """現在のポジションを取得"""
        try:
            # OANDAの通貨ペア形式に変換
            oanda_symbol = symbol.replace('_', '/')

            response = self._make_request('GET', f'/accounts/{self.account_id}/positions')

            positions_list = []
            if response and 'positions' in response:
                for pos_data in response['positions']:
                    if pos_data.get('instrument') == oanda_symbol:
                        long_data = pos_data.get('long', {})
                        short_data = pos_data.get('short', {})

                        long_units = float(long_data.get('units', 0))
                        short_units = float(short_data.get('units', 0))

                        if long_units > 0:
                            positions_list.append(Position(
                                position_id=long_data.get('tradeIDs', [''])[0] if long_data.get('tradeIDs') else '',
                                symbol=symbol,
                                side="BUY",
                                size=long_units,
                                price=float(long_data.get('averagePrice', 0)),
                                open_time=long_data.get('openTime', ''),
                                unrealized_pnl=float(long_data.get('unrealizedPL', 0))
                            ))

                        if short_units > 0:
                            positions_list.append(Position(
                                position_id=short_data.get('tradeIDs', [''])[0] if short_data.get('tradeIDs') else '',
                                symbol=symbol,
                                side="SELL",
                                size=short_units,
                                price=float(short_data.get('averagePrice', 0)),
                                open_time=short_data.get('openTime', ''),
                                unrealized_pnl=float(short_data.get('unrealizedPL', 0))
                            ))

            return positions_list

        except Exception as e:
            logging.error(f"[{self.name}] ポジションチェックエラー: {e}")
            return []
    
    def get_all_positions(self) -> List[Position]:
        """全ポジションを取得"""
        try:
            response = self._make_request('GET', f'/accounts/{self.account_id}/positions')

            positions_list = []
            if response and 'positions' in response:
                for pos_data in response['positions']:
                    oanda_symbol = pos_data.get('instrument', '')
                    symbol = oanda_symbol.replace('/', '_')  # 内部形式に変換

                    long_data = pos_data.get('long', {})
                    short_data = pos_data.get('short', {})

                    long_units = float(long_data.get('units', 0))
                    short_units = float(short_data.get('units', 0))

                    if long_units > 0:
                        positions_list.append(Position(
                            position_id=long_data.get('tradeIDs', [''])[0] if long_data.get('tradeIDs') else '',
                            symbol=symbol,
                            side="BUY",
                            size=long_units,
                            price=float(long_data.get('averagePrice', 0)),
                            open_time=long_data.get('openTime', ''),
                            unrealized_pnl=float(long_data.get('unrealizedPL', 0))
                        ))

                    if short_units > 0:
                        positions_list.append(Position(
                            position_id=short_data.get('tradeIDs', [''])[0] if short_data.get('tradeIDs') else '',
                            symbol=symbol,
                            side="SELL",
                            size=short_units,
                            price=float(short_data.get('averagePrice', 0)),
                            open_time=short_data.get('openTime', ''),
                            unrealized_pnl=float(short_data.get('unrealizedPL', 0))
                        ))

            return positions_list

        except Exception as e:
            logging.error(f"[{self.name}] 全ポジション取得エラー: {e}")
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

            # 取引履歴から該当する取引を検索
            response = self._make_request('GET', f'/accounts/{self.account_id}/trades')

            if response and 'trades' in response:
                for trade in response['trades']:
                    if trade.get('id') == order_id:
                        oanda_symbol = trade.get('instrument', '')
                        symbol = oanda_symbol.replace('/', '_')

                        return Position(
                            position_id=trade.get('id', ''),
                            symbol=symbol,
                            side="BUY" if trade.get('currentUnits', 0) > 0 else "SELL",
                            size=abs(float(trade.get('currentUnits', 0))),
                            price=float(trade.get('price', 0)),
                            open_time=trade.get('openTime', ''),
                            unrealized_pnl=float(trade.get('unrealizedPL', 0))
                        )

            logging.error("ポジション情報が見つかりません")
            return None

        except Exception as e:
            logging.error(f"[{self.name}] ポジション情報取得エラー: {e}")
            return None

    def get_execution_fee(self, order_id: str) -> float:
        """約定手数料を取得"""
        try:
            # OANDAの取引履歴から手数料を取得
            response = self._make_request('GET', f'/accounts/{self.account_id}/transactions')

            if response and 'transactions' in response:
                for transaction in response['transactions']:
                    if (transaction.get('type') == 'ORDER_FILL' and
                        transaction.get('id') == order_id):
                        return float(transaction.get('commission', 0))

            return 0.0

        except Exception as e:
            logging.error(f"[{self.name}] 手数料取得エラー: {e}")
            return 0.0

    def get_execution_price(self, order_id: str) -> float:
        """約定価格を取得"""
        try:
            # 取引履歴から約定価格を取得
            response = self._make_request('GET', f'/accounts/{self.account_id}/transactions')

            if response and 'transactions' in response:
                for transaction in response['transactions']:
                    if (transaction.get('type') == 'ORDER_FILL' and
                        transaction.get('id') == order_id):
                        return float(transaction.get('price', 0))

            return 0.0

        except Exception as e:
            logging.error(f"[{self.name}] 約定価格取得エラー: {e}")
            return 0.0

    def validate_config(self) -> bool:
        """設定の妥当性を検証"""
        parent_valid = super().validate_config()
        if not parent_valid:
            return False

        if not self.account_id:
            logging.error(f"[{self.name}] OANDAアカウントIDが設定されていません")
            return False
        if not self.access_token:
            logging.error(f"[{self.name}] OANDAアクセストークンが設定されていません")
            return False
        if self.environment not in ['practice', 'live']:
            logging.error(f"[{self.name}] 無効な環境設定: {self.environment}")
            return False

        logging.info(f"[{self.name}] OANDA設定検証成功")
        return True 