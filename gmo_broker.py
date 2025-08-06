#!/usr/bin/env python3
"""
GMO Coin Broker Implementation
Version: 1.0.0
License: MIT License
Copyright (c) 2024 Trading Bot

GMO Coin specific implementation of the broker interface.
"""

import hmac
import hashlib
import time
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from broker_base import BrokerBase, Balance, Order, Position, Ticker


class GMOCoinBroker(BrokerBase):
    """
    GMOコイン用ブローカー実装
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        初期化
        
        Args:
            config: GMOコイン設定辞書
        """
        super().__init__(config)
        self.base_url_private = 'https://forex-api.coin.z.com/private'
        self.base_url_public = 'https://forex-api.coin.z.com/public'
        
        # レート制限管理
        self.last_request_time = 0
        self.request_count = 0
        self.current_rate_limit = 20  # 20回/秒
    
    def generate_timestamp(self) -> str:
        """GMOコインAPI用のタイムスタンプ（ミリ秒）を生成"""
        return '{0}000'.format(int(time.time()))
    
    def generate_signature(self, timestamp: str, method: str, path: str, body: str = '') -> str:
        """GMOコインAPI用のリクエスト署名を生成"""
        if not self.api_secret:
            raise ValueError("APIシークレットが設定されていません")
        text = timestamp + method + path + body
        return hmac.new(self.api_secret.encode('ascii'), text.encode('ascii'), hashlib.sha256).hexdigest()
    
    def rate_limit(self, method: str):
        """APIレート制限管理"""
        import random
        now = time.time()
        
        if method == 'POST':
            wait = 1.0/self.current_rate_limit - (now - self.last_request_time)
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.1))
            self.last_request_time = time.time()
        elif method == 'GET':
            wait = 1.0/self.current_rate_limit - (now - self.last_request_time)
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.05))
            self.last_request_time = time.time()
    
    def retry_request(self, method: str, url: str, headers: Dict[str, str], 
                     params: Optional[Dict] = None, data: Optional[str] = None) -> Optional[Dict]:
        """リトライ機能付きAPIリクエスト"""
        import requests
        import random
        
        base_delay = 1
        max_delay = 60
        
        for attempt in range(3):
            try:
                self.rate_limit(method)
                if method == 'GET':
                    response = requests.get(url, headers=headers, params=params, timeout=15)
                elif method == 'POST':
                    response = requests.post(url, headers=headers, json=json.loads(data) if data else None, timeout=15)
                    
                response.raise_for_status()
                res_json = response.json()
                
                if res_json.get('status') != 0:
                    error_code = res_json.get('messages', [{}])[0].get('message_code')
                    if error_code == 'ERR-5003':  # レートリミットエラー
                        backoff = min((2 ** attempt) + random.random(), max_delay)
                        time.sleep(backoff)
                        continue
                        
                return res_json
                
            except Exception as e:
                logging.error(f"APIリクエストエラー (試行 {attempt+1}): {e}")
                sleep_time = min(base_delay * (2 ** attempt) + random.random(), max_delay)
                time.sleep(sleep_time)
                
        return None
    
    def get_balance(self) -> Optional[Balance]:
        """FX口座残高を取得"""
        try:
            timestamp = self.generate_timestamp()
            method = 'GET'
            path = '/v1/account/assets'
            url = self.base_url_private + path
            headers = {
                "API-KEY": self.api_key,
                "API-TIMESTAMP": timestamp,
                "API-SIGN": self.generate_signature(timestamp, method, path)
            }
            
            response = self.retry_request(method, url, headers)
            
            if not response or 'data' not in response or not response['data']:
                logging.error(f"証拠金取得APIレスポンスエラー: {response}")
                return None
            
            # 証拠金データの形式を判定して取得
            balance_data = response['data']
            if isinstance(balance_data, list) and len(balance_data) > 0:
                balance_item = balance_data[0]
                available_amount = float(balance_item.get('availableAmount', 0))
                total_balance = float(balance_item.get('balance', 0))
            elif isinstance(balance_data, dict):
                available_amount = float(balance_data.get('availableAmount', 0))
                total_balance = float(balance_data.get('balance', 0))
            else:
                logging.error(f"無効な証拠金データ形式: {balance_data}")
                return None
            
            return Balance(
                available_amount=available_amount,
                total_balance=total_balance,
                currency="JPY"
            )
            
        except Exception as e:
            logging.error(f"残高取得エラー: {e}")
            return None
    
    def create_order(self, symbol: str, side: str, size: Optional[float] = None, 
                    leverage: Optional[float] = None) -> Optional[Order]:
        """注文を作成"""
        try:
            timestamp = self.generate_timestamp()
            path = "/v1/order"
            
            # サイズをintでAPIに送信
            if size is not None:
                body = {"symbol": symbol, "side": side, "size": str(int(size)), "executionType": "MARKET"}
            else:
                body = {"symbol": symbol, "side": side, "size": None, "executionType": "MARKET"}
            
            if leverage:
                body["leverage"] = leverage
            
            body_str = json.dumps(body)
            signature = self.generate_signature(timestamp, "POST", path, body_str)
            
            headers = {
                "API-KEY": self.api_key,
                "API-SIGN": signature,
                "API-TIMESTAMP": timestamp,
                "Content-Type": "application/json"
            }
            
            response = self.retry_request("POST", f"{self.base_url_private}{path}", headers, data=body_str)
            
            if not response or 'data' not in response or not response['data']:
                logging.error(f"注文作成APIレスポンスエラー: {response}")
                return None
            
            order_data = response['data'][0]
            return Order(
                order_id=order_data.get('orderId', ''),
                symbol=symbol,
                side=side,
                size=size or 0,
                leverage=leverage
            )
            
        except Exception as e:
            logging.error(f"注文作成エラー: {e}")
            return None
    
    def close_position(self, symbol: str, position_id: str, size: float, side: str) -> Optional[float]:
        """ポジションを決済"""
        try:
            timestamp = self.generate_timestamp()
            path = "/v1/closeOrder"
            
            body = {
                "symbol": symbol,
                "side": side,
                "executionType": "MARKET",
                "settlePosition": [
                    {
                        "positionId": position_id,
                        "size": str(size)
                    }
                ]
            }
            
            body_str = json.dumps(body)
            signature = self.generate_signature(timestamp, "POST", path, body_str)
            
            headers = {
                "API-KEY": self.api_key,
                "API-SIGN": signature,
                "API-TIMESTAMP": timestamp,
                "Content-Type": "application/json"
            }
            
            response = self.retry_request("POST", f"{self.base_url_private}{path}", headers, data=body_str)
            
            if response and 'data' in response and len(response['data']) > 0:
                order_id = response['data'][0]['orderId']
                return self.get_execution_price(order_id)
            else:
                logging.error("決済注文に失敗しました")
                return None
                
        except Exception as e:
            logging.error(f"決済エラー: {e}")
            return None
    
    def get_tickers(self, symbols: List[str]) -> Optional[Dict[str, Ticker]]:
        """ティッカー情報を取得"""
        try:
            timestamp = self.generate_timestamp()
            method = 'GET'
            path = '/v1/ticker'
            url = self.base_url_public + path
            params = {'symbol': ','.join(symbols)}
            headers = {"API-TIMESTAMP": timestamp}
            
            response = self.retry_request(method, url, headers, params)
            
            if not response or 'data' not in response:
                logging.error(f"ティッカー取得APIレスポンスエラー: {response}")
                return None
            
            tickers = {}
            for item in response['data']:
                symbol = item['symbol']
                tickers[symbol] = Ticker(
                    symbol=symbol,
                    bid=float(item['bid']),
                    ask=float(item['ask']),
                    timestamp=item.get('timestamp', '')
                )
            
            return tickers
            
        except Exception as e:
            logging.error(f"ティッカー取得エラー: {e}")
            return None
    
    def check_current_positions(self, symbol: str) -> List[Position]:
        """現在のポジションを取得"""
        try:
            timestamp = self.generate_timestamp()
            path = f"/v1/openPositions?symbol={symbol}"
            signature = self.generate_signature(timestamp, "GET", path)
            
            headers = {
                "API-KEY": self.api_key,
                "API-SIGN": signature,
                "API-TIMESTAMP": timestamp
            }
            
            response = self.retry_request("GET", f"{self.base_url_private}{path}", headers)
            
            positions = []
            if response and 'data' in response and 'list' in response['data']:
                for pos_data in response['data']['list']:
                    positions.append(Position(
                        position_id=pos_data.get('positionId', ''),
                        symbol=pos_data.get('symbol', ''),
                        side=pos_data.get('side', ''),
                        size=float(pos_data.get('size', 0)),
                        price=float(pos_data.get('price', 0)),
                        open_time=pos_data.get('openTime', '')
                    ))
            
            return positions
            
        except Exception as e:
            logging.error(f"ポジションチェックエラー: {e}")
            return []
    
    def get_all_positions(self) -> List[Position]:
        """全ポジションを取得"""
        try:
            timestamp = self.generate_timestamp()
            method = 'GET'
            path = '/v1/openPositions'
            url = self.base_url_private + path
            headers = {
                "API-KEY": self.api_key,
                "API-TIMESTAMP": timestamp,
                "API-SIGN": self.generate_signature(timestamp, method, path)
            }
            
            response = self.retry_request(method, url, headers)
            
            positions = []
            if response and 'data' in response and 'list' in response['data']:
                for pos_data in response['data']['list']:
                    positions.append(Position(
                        position_id=pos_data.get('positionId', ''),
                        symbol=pos_data.get('symbol', ''),
                        side=pos_data.get('side', ''),
                        size=float(pos_data.get('size', 0)),
                        price=float(pos_data.get('price', 0)),
                        open_time=pos_data.get('openTime', '')
                    ))
            
            return positions
            
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

            # 約定情報取得
            timestamp = self.generate_timestamp()
            method = 'GET'
            path = '/v1/executions'
            url = self.base_url_private + path
            params = {"orderId": order_id}
            headers = {
                "API-KEY": self.api_key,
                "API-TIMESTAMP": timestamp,
                "API-SIGN": self.generate_signature(timestamp, method, path)
            }
            
            response = self.retry_request(method, url, headers, params)
            if not response or 'data' not in response or 'list' not in response['data']:
                logging.error("約定情報取得に失敗")
                return None

            # Position ID抽出
            position_id = None
            execution_time = None
            for exec_data in response['data']['list']:
                if 'positionId' in exec_data:
                    position_id = exec_data['positionId']
                    execution_time = datetime.fromisoformat(
                        exec_data.get('timestamp', datetime.now().isoformat()).replace('Z', '+00:00')
                    )
                    break
                    
            if not position_id:
                logging.error("Position IDが見つかりません")
                return None

            # ポジション情報取得
            positions = self.get_all_positions()
            for pos in positions:
                if pos.position_id == position_id:
                    return pos

            logging.error("ポジション情報が見つかりません")
            return None

        except Exception as e:
            logging.error(f"ポジション情報取得エラー: {e}")
            return None
    
    def get_execution_fee(self, order_id: str) -> float:
        """約定手数料を取得"""
        try:
            timestamp = self.generate_timestamp()
            method = 'GET'
            path = '/v1/executions'
            url = self.base_url_private + path
            params = {"orderId": order_id}
            headers = {
                "API-KEY": self.api_key,
                "API-TIMESTAMP": timestamp,
                "API-SIGN": self.generate_signature(timestamp, method, path)
            }
            
            response = self.retry_request(method, url, headers, params)
            
            if response and 'data' in response and 'list' in response['data']:
                for exec_data in response['data']['list']:
                    if 'fee' in exec_data:
                        return float(exec_data['fee'])
            
            return 0.0
            
        except Exception as e:
            logging.error(f"手数料取得エラー: {e}")
            return 0.0
    
    def get_execution_price(self, order_id: str) -> float:
        """約定価格を取得"""
        try:
            timestamp = self.generate_timestamp()
            method = 'GET'
            path = '/v1/executions'
            url = self.base_url_private + path
            params = {"orderId": order_id}
            headers = {
                "API-KEY": self.api_key,
                "API-TIMESTAMP": timestamp,
                "API-SIGN": self.generate_signature(timestamp, method, path)
            }
            
            response = self.retry_request(method, url, headers, params)
            
            if response and 'data' in response and 'list' in response['data']:
                for exec_data in response['data']['list']:
                    if 'price' in exec_data:
                        return float(exec_data['price'])
            
            return 0.0
            
        except Exception as e:
            logging.error(f"約定価格取得エラー: {e}")
            return 0.0 