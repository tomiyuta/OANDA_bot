#!/usr/bin/env python3
"""
Broker Base Class for Trading Bot
Version: 2.0.0
License: MIT License
Copyright (c) 2024 Trading Bot

Abstract base class for broker implementations with multi-broker support.
Provides a unified interface for different broker APIs.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from discord import SyncWebhook
import csv
import logging


@dataclass
class Balance:
    """口座残高情報"""
    available_amount: float
    total_balance: float
    currency: str
    leverage: Optional[float] = None


@dataclass
class Order:
    """注文情報"""
    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    size: float
    price: Optional[float] = None
    status: str = "PENDING"
    leverage: Optional[float] = None


@dataclass
class Position:
    """ポジション情報"""
    position_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    size: float
    price: float
    open_time: str
    unrealized_pnl: Optional[float] = None


@dataclass
class Ticker:
    """ティッカー情報"""
    symbol: str
    bid: float
    ask: float
    timestamp: str


class BrokerBase(ABC):
    """
    ブローカー抽象基底クラス（マルチブローカー対応）
    各ブローカー（GMO Coin、OANDA等）の実装で継承する
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        初期化
        
        Args:
            config: ブローカー設定辞書
        """
        # 基本設定
        self.name = config.get("name", "unknown")
        self.type = config.get("type", "unknown")
        self.trade_csv = config.get("trade_csv", "trades.csv")
        self.discord_webhook_url = config.get("discord_webhook_url", "")
        
        # Discord Webhook初期化
        if self.discord_webhook_url:
            try:
                self.webhook = SyncWebhook.from_url(self.discord_webhook_url)
            except Exception as e:
                logging.error(f"[{self.name}] Discord Webhook初期化エラー: {e}")
                self.webhook = None
        else:
            self.webhook = None
        
        # ブローカー固有設定
        self.config = config
        self.api_key = config.get('api_key', '')
        self.api_secret = config.get('api_secret', '')
        self.base_url = config.get('base_url', '')
        
        # 取引設定
        self.leverage = config.get('leverage', 10)
        self.risk_ratio = config.get('risk_ratio', 1.0)
        self.autolot = config.get('autolot', 'TRUE').upper() == 'TRUE'
        self.symbol_daily_volume_limit = config.get('symbol_daily_volume_limit', 15000000)
    
    def get_trades(self) -> List[List[str]]:
        """
        CSVファイルを読み込み、トレードデータを返す
        
        Returns:
            List[List[str]]: トレードデータのリスト
        """
        trades = []
        try:
            with open(self.trade_csv, encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].startswith("#"):  # コメント行スキップ
                        continue
                    trades.append(row)
            logging.info(f"[{self.name}] {len(trades)}件のトレードデータを読み込みました")
        except FileNotFoundError:
            logging.warning(f"[{self.name}] トレードファイルが見つかりません: {self.trade_csv}")
        except Exception as e:
            logging.error(f"[{self.name}] トレードファイル読み込みエラー: {e}")
        
        return trades
    
    def notify(self, msg: str) -> bool:
        """
        Discordに通知を送信
        
        Args:
            msg: 送信メッセージ
            
        Returns:
            bool: 送信成功時はTrue
        """
        if not self.webhook:
            logging.warning(f"[{self.name}] Discord Webhookが設定されていません")
            return False
        
        try:
            # ブローカー名をプレフィックスとして追加
            formatted_msg = f"[{self.name}] {msg}"
            self.webhook.send(formatted_msg)
            logging.info(f"[{self.name}] Discord通知送信成功: {msg[:50]}...")
            return True
        except Exception as e:
            logging.error(f"[{self.name}] Discord通知エラー: {e}")
            return False
    
    def get_broker_info(self) -> Dict[str, Any]:
        """
        ブローカー情報を取得
        
        Returns:
            Dict[str, Any]: ブローカー情報
        """
        return {
            "name": self.name,
            "type": self.type,
            "trade_csv": self.trade_csv,
            "leverage": self.leverage,
            "risk_ratio": self.risk_ratio,
            "autolot": self.autolot,
            "symbol_daily_volume_limit": self.symbol_daily_volume_limit
        }
    
    @abstractmethod
    def get_balance(self) -> Optional[Balance]:
        """
        口座残高を取得
        
        Returns:
            Balance: 口座残高情報、取得失敗時はNone
        """
        raise NotImplementedError()
    
    @abstractmethod
    def create_order(self, symbol: str, side: str, size: Optional[float] = None, 
                    leverage: Optional[float] = None) -> Optional[Order]:
        """
        注文を作成
        
        Args:
            symbol: 通貨ペア（例: "USD_JPY"）
            side: 売買方向（"BUY" or "SELL"）
            size: ロット数（Noneの場合は自動計算）
            leverage: レバレッジ
            
        Returns:
            Order: 注文情報、作成失敗時はNone
        """
        raise NotImplementedError()
    
    @abstractmethod
    def close_position(self, symbol: str, position_id: str, size: float, side: str) -> Optional[float]:
        """
        ポジションを決済
        
        Args:
            symbol: 通貨ペア
            position_id: ポジションID
            size: 決済ロット数
            side: 決済方向（"BUY" or "SELL"）
            
        Returns:
            float: 決済価格、決済失敗時はNone
        """
        raise NotImplementedError()
    
    @abstractmethod
    def get_tickers(self, symbols: List[str]) -> Optional[Dict[str, Ticker]]:
        """
        ティッカー情報を取得
        
        Args:
            symbols: 通貨ペアのリスト
            
        Returns:
            Dict[str, Ticker]: シンボルをキーとしたティッカー情報、取得失敗時はNone
        """
        raise NotImplementedError()
    
    @abstractmethod
    def check_current_positions(self, symbol: str) -> List[Position]:
        """
        現在のポジションを取得
        
        Args:
            symbol: 通貨ペア
            
        Returns:
            List[Position]: ポジション情報のリスト
        """
        raise NotImplementedError()
    
    @abstractmethod
    def get_all_positions(self) -> List[Position]:
        """
        全ポジションを取得
        
        Returns:
            List[Position]: 全ポジション情報のリスト
        """
        raise NotImplementedError()
    
    @abstractmethod
    def get_position_by_order_id(self, order_data: List[Dict[str, Any]]) -> Optional[Position]:
        """
        注文IDからポジション情報を取得
        
        Args:
            order_data: 注文データ
            
        Returns:
            Position: ポジション情報、取得失敗時はNone
        """
        raise NotImplementedError()
    
    @abstractmethod
    def get_execution_fee(self, order_id: str) -> float:
        """
        約定手数料を取得
        
        Args:
            order_id: 注文ID
            
        Returns:
            float: 手数料
        """
        raise NotImplementedError()
    
    @abstractmethod
    def get_execution_price(self, order_id: str) -> float:
        """
        約定価格を取得
        
        Args:
            order_id: 注文ID
            
        Returns:
            float: 約定価格
        """
        raise NotImplementedError()
    
    def format_symbol(self, symbol: str) -> str:
        """
        通貨ペアの形式を正規化
        
        Args:
            symbol: 元の通貨ペア（例: "USDJPY", "USD/JPY"）
            
        Returns:
            str: 正規化された通貨ペア（例: "USD_JPY"）
        """
        if "/" in symbol:
            return symbol.replace("/", "_")
        elif len(symbol) == 6:  # USDJPY, EURUSD など
            return f"{symbol[:3]}_{symbol[3:]}"
        else:
            return symbol
    
    def calculate_pip_value(self, symbol: str) -> float:
        """
        通貨ペアのpip値を計算
        
        Args:
            symbol: 通貨ペア
            
        Returns:
            float: pip値
        """
        if symbol.endswith("JPY"):
            return 0.01
        else:
            return 0.0001
    
    def calculate_profit_pips(self, entry_price: float, exit_price: float, 
                            side: str, symbol: str) -> float:
        """
        損益pipsを計算
        
        Args:
            entry_price: エントリー価格
            exit_price: 決済価格
            side: 売買方向
            symbol: 通貨ペア
            
        Returns:
            float: 損益pips
        """
        pip_value = self.calculate_pip_value(symbol)
        if side == "BUY":
            return round((exit_price - entry_price) / pip_value, 2)
        else:
            return round((entry_price - exit_price) / pip_value, 2)
    
    def calculate_profit_amount(self, entry_price: float, exit_price: float,
                              side: str, symbol: str, size: float) -> float:
        """
        損益金額を計算
        
        Args:
            entry_price: エントリー価格
            exit_price: 決済価格
            side: 売買方向
            symbol: 通貨ペア
            size: ロット数
            
        Returns:
            float: 損益金額
        """
        pip_value = self.calculate_pip_value(symbol)
        profit_pips = self.calculate_profit_pips(entry_price, exit_price, side, symbol)
        return round(profit_pips * size * pip_value, 2)
    
    def format_price(self, price: float, symbol: str) -> str:
        """
        価格をフォーマット
        
        Args:
            price: 価格
            symbol: 通貨ペア
            
        Returns:
            str: フォーマットされた価格
        """
        if symbol.endswith("JPY"):
            return f"{price:.3f}"
        else:
            return f"{price:.5f}"
    
    def validate_config(self) -> bool:
        """
        設定の妥当性を検証
        
        Returns:
            bool: 妥当な場合はTrue
        """
        if not self.name:
            logging.error(f"[{self.name}] ブローカー名が設定されていません")
            return False
        if not self.type:
            logging.error(f"[{self.name}] ブローカータイプが設定されていません")
            return False
        if not self.trade_csv:
            logging.error(f"[{self.name}] トレードCSVファイルが設定されていません")
            return False
        
        # ブローカータイプ別の検証
        if self.type == "gmo":
            if not self.api_key or not self.api_secret:
                logging.error(f"[{self.name}] GMO API設定が不完全です")
                return False
        elif self.type == "oanda":
            if not self.config.get('oanda_account_id') or not self.config.get('oanda_access_token'):
                logging.error(f"[{self.name}] OANDA API設定が不完全です")
                return False
        
        return True
    
    def get_broker_name(self) -> str:
        """
        ブローカー名を取得
        
        Returns:
            str: ブローカー名
        """
        return self.name 