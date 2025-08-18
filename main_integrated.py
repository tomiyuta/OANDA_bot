#!/usr/bin/env python3
"""
GMO Coin Automated Trading Bot (Integrated Version)
Version: 2.1.0
License: MIT License
Copyright (c) 2024 GMO Coin Bot

A sophisticated automated trading system for GMO Coin cryptocurrency exchange.
Features include risk management, Discord integration, and GUI configuration.
Integrated with robust time handling and dependency injection.

For more information, see README.md
"""

import os
import csv
import json
import time
import threading
import logging
import logging.handlers
import random
import sys
import signal
from threading import Lock
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List
from discord import SyncWebhook
import discord
from discord.ext import commands
import traceback
import psutil
import gc

# 新しい時刻判定ロジックをインポート
from trading_time import TradeSchedule, SystemClock, JST

# OANDA APIインポート
import oandapyV20
from oandapyV20.endpoints import accounts, orders, pricing, positions
from oanda_broker import OANDABroker

# ===============================
# グローバル変数
# ===============================
trade_results = []  # 取引結果を保存するリスト
total_api_fee = 0   # 累計API手数料
fee_records = []    # 各注文で発生した手数料の履歴 [{'date': date, 'fee': float}]
performance_metrics = {
    'api_calls': 0,
    'api_errors': 0,
    'trades_executed': 0,
    'start_time': datetime.now()
}

# OANDAレート制限管理（120回/分）
oanda_rate_limit_state = {
    'last_request_time': 0,
    'request_count': 0,
    'window_start': time.time(),
    'max_requests_per_minute': 120
}

# 設定ファイル管理
CONFIG_FILE = os.environ.get('CONFIG_FILE', 'config.json')

# 自動再起動管理用
restart_count = 0
max_restarts = 5
restart_cooldown = 300  # 5分
last_restart_time = 0

# 取引結果管理用
symbol_daily_volume = {}  # 銘柄別の一日の取引数量を追跡

# ===============================
# 設定ファイル管理（詳細版）
# ===============================
def load_config():
    """設定ファイルを読み込む（環境変数対応）"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
                # 環境変数からの設定読み込み（優先度: 環境変数 > 設定ファイル）
                config['discord_webhook_url'] = os.environ.get('DISCORD_WEBHOOK_GMO') or config.get('discord_webhook_url', '')
                # Discord有効/無効トグル
                env_enabled = os.environ.get('DISCORD_ENABLED')
                if env_enabled is not None:
                    config['discord_enabled'] = str(env_enabled).lower() in ['1','true','on','yes']
                else:
                    config['discord_enabled'] = config.get('discord_enabled', False)
                config['discord_bot_token'] = os.environ.get('DISCORD_BOT_TOKEN') or config.get('discord_bot_token', '')
                
                # OANDA設定の環境変数読み込み
                config['oanda_account_id'] = os.environ.get('OANDA_ACCOUNT_ID') or config.get('oanda_account_id', '')
                config['oanda_access_token'] = os.environ.get('OANDA_ACCESS_TOKEN') or config.get('oanda_access_token', '')
                config['oanda_environment'] = os.environ.get('OANDA_ENVIRONMENT') or config.get('oanda_environment', 'practice')
                config['broker_type'] = os.environ.get('BROKER_TYPE') or config.get('broker_type', 'oanda')
                
                return config
        except Exception as e:
            logging.error(f"設定ファイル読み込みエラー: {e}")
            return {}
    return {}

def save_config(config):
    """設定ファイルを保存する"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"設定ファイル保存エラー: {e}")
        return False

def validate_config(config):
    """設定ファイルのバリデーション（詳細版）"""
    errors = []
    
    # Discordは任意（有効時のみURL必須）
    if config.get('discord_enabled'):
        if not config.get('discord_webhook_url'):
            errors.append("'discord_webhook_url' が設定されていません（Discord通知が有効です）")
    
    # 数値項目の範囲チェック
    numeric_ranges = {
        'spread_threshold': (0.001, 1.0),
        'jitter_seconds': (0, 60),
        'entry_order_retry_interval': (1, 60),
        'max_entry_order_attempts': (1, 10),
        'exit_order_retry_interval': (1, 60),
        'max_exit_order_attempts': (1, 10),
        'stop_loss_pips': (0, 1000),
        'take_profit_pips': (0, 1000),
        'position_check_interval': (1, 60),
        'position_check_interval_minutes': (1, 99),
        'leverage': (1, 100),
        'risk_ratio': (0.1, 1.0)
    }
    
    for field, (min_val, max_val) in numeric_ranges.items():
        value = config.get(field)
        if value is not None:
            try:
                num_value = float(value)
                if not (min_val <= num_value <= max_val):
                    errors.append(f"'{field}' の値 ({num_value}) が範囲外です ({min_val}～{max_val})")
            except (ValueError, TypeError):
                errors.append(f"'{field}' の値が数値ではありません: {value}")
    
    # 自動再起動時間の検証
    auto_restart_hour = config.get('auto_restart_hour')
    if auto_restart_hour is not None:
        try:
            hour_value = int(auto_restart_hour)
            if not (0 <= hour_value <= 23):
                errors.append(f"'auto_restart_hour' の値 ({hour_value}) が範囲外です (0～23)")
        except (ValueError, TypeError):
            errors.append(f"'auto_restart_hour' の値が数値ではありません: {auto_restart_hour}")
    
    # autolot設定の検証
    autolot_value = config.get('autolot')
    if autolot_value is not None and str(autolot_value).upper() not in ['TRUE', 'FALSE']:
        errors.append("'autolot' は 'TRUE' または 'FALSE' である必要があります")
    
    # OANDA設定の検証
    if not config.get('oanda_account_id'):
        errors.append("'oanda_account_id' が設定されていません")
    if not config.get('oanda_access_token'):
        errors.append("'oanda_access_token' が設定されていません")
    
    oanda_env = config.get('oanda_environment', 'practice')
    if oanda_env not in ['practice', 'live']:
        errors.append("'oanda_environment' は 'practice' または 'live' である必要があります")
    
    if errors:
        print("設定ファイルにエラーがあります:")
        for error in errors:
            print(f"  - {error}")
        print("config.jsonを修正してから再実行してください。")
        return False
    
    return True

def reload_config():
    """設定を動的に再読み込み"""
    try:
        global config, GMO_API_KEY, GMO_API_SECRET, DISCORD_WEBHOOK_URL, BASE_URL
        global SPREAD_THRESHOLD, JITTER_SECONDS, ENTRY_ORDER_RETRY_INTERVAL, MAX_ENTRY_ORDER_ATTEMPTS
        global EXIT_ORDER_RETRY_INTERVAL, MAX_EXIT_ORDER_ATTEMPTS, STOP_LOSS_PIPS, TAKE_PROFIT_PIPS
        global POSITION_CHECK_INTERVAL, POSITION_CHECK_INTERVAL_MINUTES, LEVERAGE, RISK_RATIO
        global AUTOLOT, AUTO_RESTART_HOUR, SYMBOL_DAILY_VOLUME_LIMIT
        
        new_config = load_config()
        if validate_config(new_config):
            config = new_config
            
            # 設定値を更新
            GMO_API_KEY = config.get('api_key')
            GMO_API_SECRET = config.get('api_secret')
            DISCORD_WEBHOOK_URL = config.get('discord_webhook_url')
            BASE_URL = 'https://forex-api.coin.z.com/private'
            
            SPREAD_THRESHOLD = config.get('spread_threshold', 0.01)
            JITTER_SECONDS = config.get('jitter_seconds', 5)
            ENTRY_ORDER_RETRY_INTERVAL = config.get('entry_order_retry_interval', 3)
            MAX_ENTRY_ORDER_ATTEMPTS = config.get('max_entry_order_attempts', 3)
            EXIT_ORDER_RETRY_INTERVAL = config.get('exit_order_retry_interval', 3)
            MAX_EXIT_ORDER_ATTEMPTS = config.get('max_exit_order_attempts', 3)
            STOP_LOSS_PIPS = config.get('stop_loss_pips', 0)
            TAKE_PROFIT_PIPS = config.get('take_profit_pips', 0)
            POSITION_CHECK_INTERVAL = config.get('position_check_interval', 5)
            POSITION_CHECK_INTERVAL_MINUTES = config.get('position_check_interval_minutes', 10)
            LEVERAGE = config.get('leverage', 10)
            RISK_RATIO = config.get('risk_ratio', 1.0)
            AUTOLOT = str(config.get('autolot', 'TRUE')).upper()
            AUTO_RESTART_HOUR = config.get('auto_restart_hour')
            SYMBOL_DAILY_VOLUME_LIMIT = config.get('symbol_daily_volume_limit', 15000000)
            
            logging.info("設定を再読み込みしました")
            return True
        else:
            logging.error("設定の再読み込みに失敗しました")
            return False
    except Exception as e:
        logging.error(f"設定再読み込みエラー: {e}")
        return False

def create_default_config():
    """デフォルト設定ファイルを作成"""
    default_config = {
        "discord_webhook_url": "",
        "spread_threshold": 0.01,
        "jitter_seconds": 5,
        "entry_order_retry_interval": 3,
        "max_entry_order_attempts": 3,
        "exit_order_retry_interval": 3,
        "max_exit_order_attempts": 3,
        "stop_loss_pips": 50,
        "take_profit_pips": 100,
        "position_check_interval": 30,
        "position_check_interval_minutes": 5,
        "leverage": 10,
        "risk_ratio": 0.02,
        "autolot": "TRUE",
        "auto_restart_hour": 6,
        "symbol_daily_volume_limit": 15000000,  # 銘柄別の一日の最大取引数量（1500万ロット）
        "broker_type": "oanda",
        "oanda_account_id": "",
        "oanda_access_token": "",
        "oanda_environment": "practice"
    }
    
    if save_config(default_config):
        print("設定ファイルを作成しました。config.jsonを編集してAPIキーを設定してください。")
        print("設定後、プログラムを再実行してください。")
        return True
    else:
        print("設定ファイルの作成に失敗しました。")
        return False

# ===============================
# 設定の読み込みと検証
# ===============================
config = load_config()

if not config:
    if not create_default_config():
        sys.exit(1)
    sys.exit(0)

if not validate_config(config):
    sys.exit(1)

# 設定値の取得
DISCORD_WEBHOOK_URL = config.get('discord_webhook_url')
DISCORD_ENABLED = bool(config.get('discord_enabled', False))

# OANDA設定
OANDA_ACCOUNT_ID = config.get('oanda_account_id')
OANDA_ACCESS_TOKEN = config.get('oanda_access_token')
OANDA_ENV = config.get('oanda_environment', 'practice')

# ブローカー選択
BROKER_TYPE = config.get('broker_type', 'oanda')  # デフォルトをOANDAに変更

# 取引設定
SPREAD_THRESHOLD = config.get('spread_threshold', 0.01)   # 許容スプレッド（例: 0.01=1pip, USD/JPY想定）
JITTER_SECONDS = config.get('jitter_seconds', 5)          # エントリー時刻のゆらぎ（秒）
ENTRY_ORDER_RETRY_INTERVAL = config.get('entry_order_retry_interval', 3)  # エントリー注文リトライ間隔（秒）
MAX_ENTRY_ORDER_ATTEMPTS = config.get('max_entry_order_attempts', 3)        # エントリー注文最大リトライ回数
EXIT_ORDER_RETRY_INTERVAL = config.get('exit_order_retry_interval', 3)      # 決済注文リトライ間隔（秒）
MAX_EXIT_ORDER_ATTEMPTS = config.get('max_exit_order_attempts', 3)          # 決済注文最大リトライ回数
STOP_LOSS_PIPS = config.get('stop_loss_pips', 0)                  # ストップロス閾値（pips）0なら無効
TAKE_PROFIT_PIPS = config.get('take_profit_pips', 0)                # テイクプロフィット閾値（pips）0なら無効
POSITION_CHECK_INTERVAL = config.get('position_check_interval', 5)         # ポジション監視間隔（秒）
POSITION_CHECK_INTERVAL_MINUTES = config.get('position_check_interval_minutes', 10)
LEVERAGE = config.get('leverage', 10)  # デフォルト10倍
RISK_RATIO = config.get('risk_ratio', 1.0)  # 口座残高の何割を使うか（1.0=全額）
AUTOLOT = str(config.get('autolot', 'TRUE')).upper()  # "TRUE"で自動ロット
AUTO_RESTART_HOUR = config.get('auto_restart_hour')  # 自動再起動時間（0-24時、Noneで無効）
SYMBOL_DAILY_VOLUME_LIMIT = config.get('symbol_daily_volume_limit', 15000000)  # 銘柄別の一日の最大取引数量（1500万ロット）

# 環境変数による設定
SCHEDULE_CSV = os.getenv("TRADES_CSV", "trades.csv")
BUFFER_SECONDS = int(os.getenv("TIME_BUFFER", "5"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Discord Webhook初期化
webhook = None
if DISCORD_ENABLED and DISCORD_WEBHOOK_URL:
    try:
        webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
    except Exception as e:
        logging.error(f"Discord Webhook初期化エラー: {e}")

# API呼び出し回数を削減するキャッシュ機構
ticker_cache = {}
CACHE_TTL = 5  # 5秒キャッシュ保持

# OANDAブローカー初期化
logging.info(f"OANDAブローカーを初期化しました: {OANDA_ENV}")

# OANDA API初期化
oanda_api = oandapyV20.API(
    access_token=OANDA_ACCESS_TOKEN,
    environment=OANDA_ENV
)

# ===============================
# OANDA用関数（直接コピペ）
# ===============================
def get_tickers(symbols):
    # OANDAレート制限チェック
    oanda_rate_limit()
    
    # symbol表記をOANDA形式に変換（USDJPY → USD_JPY）
    oanda_symbols = []
    for symbol in symbols:
        if len(symbol) == 6 and not "_" in symbol:  # USDJPY形式
            oanda_symbol = f"{symbol[:3]}_{symbol[3:]}"
        else:
            oanda_symbol = symbol
        oanda_symbols.append(oanda_symbol)
    
    instruments = ",".join(oanda_symbols)
    r = pricing.PricingInfo(accountID=OANDA_ACCOUNT_ID, params={"instruments": instruments})
    resp = oanda_api.request(r)
    # OANDAの"bids"/"asks"形式をGMO風の'data'配列に合わせてパース
    data = []
    for p in resp["prices"]:
        data.append({
            "symbol": p["instrument"],
            "bid": float(p["bids"][0]["price"]),
            "ask": float(p["asks"][0]["price"])
        })
    return {"data": data}

def get_fx_balance():
    # OANDAレート制限チェック
    oanda_rate_limit()
    
    r = accounts.AccountDetails(OANDA_ACCOUNT_ID)
    resp = oanda_api.request(r)
    balance = float(resp["account"]["NAV"])
    return {"data": [{"availableAmount": balance}]}

def send_order(symbol, side, size, leverage=None):
    # OANDAレート制限チェック
    oanda_rate_limit()
    
    # symbol表記をOANDA形式に変換（USDJPY → USD_JPY）
    if len(symbol) == 6 and not "_" in symbol:  # USDJPY形式
        oanda_symbol = f"{symbol[:3]}_{symbol[3:]}"
    else:
        oanda_symbol = symbol
    
    units = int(size) if side == "BUY" else -int(size)
    data = {
        "order": {
            "instrument": oanda_symbol,
            "units": str(units),
            "type": "MARKET",
            "timeInForce": "FOK",
            "positionFill": "DEFAULT"
        }
    }
    r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=data)
    resp = oanda_api.request(r)
    # 必要に応じてレスポンスパース
    order_id = resp["orderFillTransaction"]["id"]
    return {"data": [{"orderId": order_id}]}, abs(units)

def close_position(symbol, position_id, size, side):
    # OANDAレート制限チェック
    oanda_rate_limit()
    
    # symbol表記をOANDA形式に変換（USDJPY → USD_JPY）
    if len(symbol) == 6 and not "_" in symbol:  # USDJPY形式
        oanda_symbol = f"{symbol[:3]}_{symbol[3:]}"
    else:
        oanda_symbol = symbol
    
    # OANDAは片側のみ指定してクローズする必要がある
    if str(side).upper() == "SELL":
        # ロングポジションを閉じる
        data = {"longUnits": "ALL"}
    elif str(side).upper() == "BUY":
        # ショートポジションを閉じる
        data = {"shortUnits": "ALL"}
    else:
        # 不明時は現在のポジションから判定
        try:
            r_chk = positions.OpenPositions(OANDA_ACCOUNT_ID)
            resp_chk = oanda_api.request(r_chk)
            data = {"longUnits": "ALL"}
            for p in resp_chk.get("positions", []):
                if p.get("instrument") == oanda_symbol:
                    long_units = float(p.get('long', {}).get('units', 0) or 0)
                    short_units = float(p.get('short', {}).get('units', 0) or 0)
                    data = {"longUnits": "ALL"} if long_units > 0 else {"shortUnits": "ALL"} if short_units > 0 else {"longUnits": "ALL"}
                    break
        except Exception:
            data = {"longUnits": "ALL"}

    r = positions.PositionClose(OANDA_ACCOUNT_ID, instrument=oanda_symbol, data=data)
    resp = oanda_api.request(r)
    # price取得（なければNoneで返す）
    try:
        if 'longOrderFillTransaction' in resp:
            price = float(resp['longOrderFillTransaction'].get('price', 0))
        elif 'shortOrderFillTransaction' in resp:
            price = float(resp['shortOrderFillTransaction'].get('price', 0))
        elif 'orderFillTransaction' in resp:
            price = float(resp['orderFillTransaction'].get('price', 0))
        else:
            price = None
    except:
        price = None
    return {"data": {"price": price}}

def check_current_positions(symbol):
    # symbol表記をOANDA形式に変換（USDJPY → USD_JPY）
    if len(symbol) == 6 and not "_" in symbol:  # USDJPY形式
        oanda_symbol = f"{symbol[:3]}_{symbol[3:]}"
    else:
        oanda_symbol = symbol
    
    positions = broker.get_all_positions()
    out = []
    for p in positions:
        if p.symbol == oanda_symbol:
            out.append(p)
    return out

# ===============================
# ブローカー初期化
# ===============================
def initialize_broker():
    """設定に基づきブローカーインスタンスを生成"""
    broker_type = config.get('broker_type', 'oanda')
    if broker_type == 'oanda':
        broker_config = {
            "name": "oanda",
            "type": "oanda",
            "trade_csv": os.getenv("TRADES_CSV", "trades.csv"),
            "discord_webhook_url": config.get('discord_webhook_url', ''),
            "oanda_account_id": config.get('oanda_account_id', ''),
            "oanda_access_token": config.get('oanda_access_token', ''),
            "oanda_environment": config.get('oanda_environment', 'practice'),
            "leverage": config.get('leverage', 10),
            "risk_ratio": config.get('risk_ratio', 1.0),
            "autolot": config.get('autolot', 'TRUE'),
            "symbol_daily_volume_limit": config.get('symbol_daily_volume_limit', 15000000)
        }
        broker = OANDABroker(broker_config)
        if not broker.validate_config():
            raise ValueError("OANDA設定が不完全です")
        return broker
    raise NotImplementedError(f"未対応ブローカー: {broker_type}")

# ブローカーインスタンス作成
try:
    broker = initialize_broker()
except Exception as e:
    logging.error(f"ブローカー初期化エラー: {e}")
    sys.exit(1)

# ===============================
# ロギング設定（詳細版）
# ===============================
def setup_logging():
    """詳細なログ設定を初期化"""
    # ログディレクトリ作成
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # メインログ設定（ローテーション付き）
    main_log_handler = logging.handlers.RotatingFileHandler(
        'logs/main.log', 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,  # 5世代保持
        encoding='utf-8'
    )
    main_log_handler.setLevel(logging.INFO)
    main_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # エラーログ設定（ローテーション付き）
    error_log_handler = logging.handlers.RotatingFileHandler(
        'logs/error.log', 
        maxBytes=5*1024*1024,   # 5MB
        backupCount=3,  # 3世代保持
        encoding='utf-8'
    )
    error_log_handler.setLevel(logging.ERROR)
    error_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # 取引ログ設定（ローテーション付き）
    trade_log_handler = logging.handlers.RotatingFileHandler(
        'logs/trade.log', 
        maxBytes=5*1024*1024,   # 5MB
        backupCount=3,  # 3世代保持
        encoding='utf-8'
    )
    trade_log_handler.setLevel(logging.INFO)
    trade_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # APIログ設定（ローテーション付き）
    api_log_handler = logging.handlers.RotatingFileHandler(
        'logs/api.log', 
        maxBytes=5*1024*1024,   # 5MB
        backupCount=3,  # 3世代保持
        encoding='utf-8'
    )
    api_log_handler.setLevel(logging.INFO)
    api_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # コンソール出力設定
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # ロガー設定
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 既存のハンドラーをクリア
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 新しいハンドラーを追加
    logger.addHandler(main_log_handler)
    logger.addHandler(error_log_handler)
    logger.addHandler(trade_log_handler)
    logger.addHandler(api_log_handler)
    logger.addHandler(console_handler)
    
    # 特定のロガーを設定
    trade_logger = logging.getLogger('trade')
    trade_logger.addHandler(trade_log_handler)
    trade_logger.setLevel(logging.INFO)
    
    api_logger = logging.getLogger('api')
    api_logger.addHandler(api_logger)
    api_logger.setLevel(logging.INFO)
    
    logging.info("詳細ログ設定を初期化しました")

# ログ設定を実行
setup_logging()

# ===============================
# 新しい時刻判定システムの初期化
# ===============================
def initialize_trading_schedule():
    """取引スケジュールを初期化"""
    try:
        if not os.path.exists(SCHEDULE_CSV):
            logging.error(f"取引スケジュールファイルが見つかりません: {SCHEDULE_CSV}")
            return None
        
        # 新しい時刻判定システムを使用
        schedule = TradeSchedule.from_csv(SCHEDULE_CSV, BUFFER_SECONDS)
        logging.info(f"取引スケジュールを初期化しました: {SCHEDULE_CSV}, バッファ: {BUFFER_SECONDS}秒")
        return schedule
    except Exception as e:
        logging.error(f"取引スケジュール初期化エラー: {e}")
        return None

# ===============================
# グレースフルシャットダウン
# ===============================
class GracefulShutdown:
    def __init__(self):
        self.shutdown_requested = threading.Event()
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        logging.info(f"シャットダウンシグナルを受信: {signum}")
        self.shutdown_requested.set()
    
    def is_shutdown_requested(self) -> bool:
        return self.shutdown_requested.is_set()

# ===============================
# メトリクス管理
# ===============================
@dataclass
class TradingMetrics:
    entry_count: int = 0
    exit_count: int = 0
    last_entry: Optional[datetime] = None
    last_exit: Optional[datetime] = None
    start_time: datetime = None
    
    def __post_init__(self):
        if self.start_time is None:
            self.start_time = datetime.now(JST)

# ===============================
# システム管理関数
# ===============================
def get_memory_usage():
    """現在のメモリ使用量を取得（詳細版）"""
    process = psutil.Process()
    memory_info = process.memory_info()
    return {
        'rss': memory_info.rss / 1024 / 1024,  # MB
        'vms': memory_info.vms / 1024 / 1024,  # MB
        'percent': process.memory_percent(),
        'available': psutil.virtual_memory().available / 1024 / 1024,  # MB
        'total': psutil.virtual_memory().total / 1024 / 1024  # MB
    }

def check_memory_usage():
    """メモリ使用量をチェックし、必要に応じてGCを実行（詳細版）"""
    memory_usage = get_memory_usage()
    
    # メモリ使用量が100MBを超えた場合にログ出力
    if memory_usage['rss'] > 100:
        logging.warning(f"メモリ使用量が高くなっています: {memory_usage['rss']:.1f}MB ({memory_usage['percent']:.1f}%)")
        
        # 200MBを超えた場合は強制GC実行
        if memory_usage['rss'] > 200:
            logging.warning("メモリ使用量が200MBを超えました。ガベージコレクションを実行します。")
            gc.collect()
            
            # GC後のメモリ使用量を再チェック
            after_gc = get_memory_usage()
            logging.info(f"GC実行後のメモリ使用量: {after_gc['rss']:.1f}MB ({after_gc['percent']:.1f}%)")
            
            # メモリ使用量が依然として高い場合は警告
            if after_gc['rss'] > 150:
                logging.error(f"GC実行後もメモリ使用量が高い状態です: {after_gc['rss']:.1f}MB")
                send_discord_message(f"⚠️ メモリ使用量警告: {after_gc['rss']:.1f}MB ({after_gc['percent']:.1f}%)")
    
    # システム全体のメモリ使用量もチェック
    if memory_usage['available'] < 100:  # 100MB未満
        logging.warning(f"システムメモリ残量が少なくなっています: {memory_usage['available']:.1f}MB")
        send_discord_message(f"⚠️ システムメモリ残量警告: {memory_usage['available']:.1f}MB")
    
    return memory_usage

def cleanup_memory():
    """メモリクリーンアップを実行"""
    try:
        # ガベージコレクション実行
        collected = gc.collect()
        logging.info(f"メモリクリーンアップ実行: {collected}個のオブジェクトを回収")
        
        # メモリ使用量を再チェック
        memory_usage = get_memory_usage()
        logging.info(f"クリーンアップ後のメモリ使用量: {memory_usage['rss']:.1f}MB ({memory_usage['percent']:.1f}%)")
        
        return memory_usage
    except Exception as e:
        logging.error(f"メモリクリーンアップエラー: {e}")
        return None

def oanda_rate_limit():
    """OANDA APIレート制限管理（120回/分）"""
    global oanda_rate_limit_state
    now = time.time()
    
    # 1分間のウィンドウをチェック
    if now - oanda_rate_limit_state['window_start'] >= 60:
        oanda_rate_limit_state['request_count'] = 0
        oanda_rate_limit_state['window_start'] = now
    
    # レート制限チェック
    if oanda_rate_limit_state['request_count'] >= oanda_rate_limit_state['max_requests_per_minute']:
        wait_time = 60 - (now - oanda_rate_limit_state['window_start'])
        if wait_time > 0:
            logging.warning(f"OANDAレート制限により{wait_time:.1f}秒待機します")
            time.sleep(wait_time)
        oanda_rate_limit_state['request_count'] = 0
        oanda_rate_limit_state['window_start'] = time.time()
    
    oanda_rate_limit_state['request_count'] += 1
    oanda_rate_limit_state['last_request_time'] = now

def get_oanda_rate_limit_status():
    """OANDAレート制限の現在の状態を取得"""
    return {
        'requests_this_minute': oanda_rate_limit_state['request_count'],
        'max_requests_per_minute': oanda_rate_limit_state['max_requests_per_minute'],
        'window_start': oanda_rate_limit_state['window_start']
    }

# ===============================
# 基本API関数（元のmain.pyから統合）
# ===============================
# GMO固有関数 - ブローカー抽象化により削除
# def generate_timestamp():
#     """GMOコインAPI用のタイムスタンプ（ミリ秒）を生成"""
#     return '{0}000'.format(int(time.time()))

# def generate_signature(timestamp, method, path, body=''):
#     """GMOコインAPI用のリクエスト署名を生成"""
#     if not GMO_API_SECRET:
#         raise ValueError("APIシークレットが設定されていません")
#     text = timestamp + method + path + body
#     return hmac.new(GMO_API_SECRET.encode('ascii'), text.encode('ascii'), hashlib.sha256).hexdigest()

# GMO固有関数 - ブローカー抽象化により削除
# def retry_request(method, url, headers, params=None, data=None):
#     """リトライ機能付きAPIリクエスト"""
#     global performance_metrics
#     
#     # API呼び出しカウンター
#     performance_metrics['api_calls'] += 1
#     
#     base_delay = 1
#     max_delay = 60
#     for attempt in range(3):
#         try:
#             rate_limit(method)
#             if method == 'GET':
#                 response = requests.get(url, headers=headers, params=params, timeout=15)
#             elif method == 'POST':
#                 response = requests.post(url, headers=headers, json=data, timeout=15)
#                 
#             response.raise_for_status()
#             res_json = response.json()
#             
#             if res_json.get('status') != 0:
#                 error_code = res_json.get('messages', [{}])[0].get('message_code')
#                 performance_metrics['api_errors'] += 1
#                 adjust_rate_limit(error_code)  # レートリミット調整
#                 if error_code == 'ERR-5003':  # レートリミットエラー特定
#                     backoff = min((2 ** attempt) + random.random(), max_delay)
#                     time.sleep(backoff)
#                     continue
#                     
#             return res_json
#             
#         except requests.exceptions.RequestException as e:
#             performance_metrics['api_errors'] += 1
#             sleep_time = min(base_delay * (2 ** attempt) + random.random(), max_delay)
#             time.sleep(sleep_time)
#             
#     raise Exception("Max retries exceeded")

def send_discord_message(content):
    """Discordにメッセージを送信"""
    try:
        if DISCORD_ENABLED and DISCORD_WEBHOOK_URL:
            webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_URL)
            webhook.send(content)
    except Exception as e:
        logging.error(f"Discord送信エラー: {e}")

# GMO固有関数 - OANDA用関数に置き換え済み
# def get_fx_balance():
#     """FX口座残高を取得（OANDA版）"""
#     try:
#         r = accounts.AccountDetails(OANDA_ACCOUNT_ID)
#         resp = oanda_api.request(r)
#         balance = float(resp["account"]["NAV"])
#         return {"data": [{"availableAmount": balance}]}
#     except Exception as e:
#         logging.error(f"残高取得エラー: {e}")
#         return None

# GMO固有関数 - OANDA用関数に置き換え済み
# def send_order(symbol, side, size=None, leverage=None):
#     """注文を送信（OANDA版）"""
#     # ... 既存のコード ...

# GMO固有関数 - OANDA用関数に置き換え済み
# def close_position(symbol, position_id, size, side):
#     """ポジションを決済（OANDA版）"""
#     # ... 既存のコード ...

def get_tickers_optimized(symbols):
    """キャッシュ機能付きティッカー取得"""
    current_time = time.time()
    uncached_symbols = [s for s in symbols if ticker_cache.get(s, {}).get('expiry', 0) < current_time]
    
    if uncached_symbols:
        fresh_data = get_tickers(uncached_symbols)
        for data in fresh_data.get('data', []):
            ticker_cache[data['symbol']] = {
                'bid': data['bid'],
                'ask': data['ask'],
                'expiry': current_time + CACHE_TTL
            }
    
    return {s: ticker_cache[s] for s in symbols if s in ticker_cache}

# GMO固有関数 - OANDA用関数に置き換え済み
# def get_tickers(symbols):
#     """ティッカー情報を取得（OANDA版）"""
#     # ... 既存のコード ...

def format_price(price, symbol):
    """価格をフォーマット"""
    if "JPY" in symbol:
        return f"{price:.3f}"
    else:
        return f"{price:.5f}"

def calculate_profit_pips(entry_price, exit_price, side, symbol):
    """エントリー・決済価格から損益pipsを計算"""
    pip_value = 0.01 if "JPY" in symbol else 0.0001
    if side == "BUY":
        return round((exit_price - entry_price) / pip_value, 2)
    else:
        return round((entry_price - exit_price) / pip_value, 2)

def calculate_current_profit_pips(entry_price, current_price, side, symbol):
    """現在の価格から含み損益pipsを計算"""
    pip_value = 0.01 if "JPY" in symbol else 0.0001
    
    try:
        # 型変換の統一化
        if isinstance(current_price, dict) and 'bid' in current_price and 'ask' in current_price:
            bid = float(current_price['bid'])
            ask = float(current_price['ask'])
        else:
            logging.error(f"無効な価格データ形式: {current_price}")
            return 0.0
            
        entry_price = float(entry_price)
        
        if side == "BUY":
            profit_pips = (bid - entry_price) / pip_value
        else:
            profit_pips = (entry_price - ask) / pip_value
            
        return round(profit_pips, 2)
        
    except (ValueError, TypeError, KeyError) as e:
        logging.error(f"損益計算エラー: {e}, entry_price={entry_price}, current_price={current_price}")
        return 0.0

def calculate_profit_amount(entry_price, exit_price, side, symbol, size):
    """GMOコインの仕様に基づいた正確な損益計算"""
    pip_value = 0.01 if "JPY" in symbol else 0.0001
    
    # pips計算
    if side == "BUY":
        profit_pips = (exit_price - entry_price) / pip_value
    else:
        profit_pips = (entry_price - exit_price) / pip_value
    
    # 損益（USD建て or 円建て）
    profit = profit_pips * float(size) * pip_value
    
    # USD建て通貨ペアの場合は円換算
    if not ("JPY" in symbol):
        try:
            tickers = get_tickers(["USD_JPY"])
            usdjpy_rate = None
            if tickers and 'data' in tickers:
                for item in tickers['data']:
                    if item['symbol'] == 'USD_JPY':
                        usdjpy_rate = float(item['bid'])
                        break
            if usdjpy_rate and usdjpy_rate > 0:
                profit = profit * usdjpy_rate
        except Exception as e:
            logging.error(f"USD/JPYレート取得・円換算エラー: {e}")
            # レート取得失敗時はそのままUSD金額を返す
    
    # デバッグ情報
    logging.info(f"損益計算: エントリー={entry_price}, 決済={exit_price}, 方向={side}, ロット={size}, pips={profit_pips:.2f}, 損益={profit:.2f}")
    
    return round(profit, 2)

def calc_auto_lot_gmobot2(balance, symbol, side, leverage):
    """
    GMOコインの仕様に基づいた正確なロット計算
    GMOコイン: 1lot = 1通貨
    証拠金必要額 = 取引額 / レバレッジ
    安全マージン0.95を適用
    """
    try:
        # 入力値の検証
        if not balance or float(balance) <= 0:
            raise ValueError(f"無効な証拠金: {balance}")
        
        if not leverage or float(leverage) <= 0:
            raise ValueError(f"無効なレバレッジ: {leverage}")
        
        if not symbol:
            raise ValueError("通貨ペアが指定されていません")
        
        if side not in ["BUY", "SELL"]:
            raise ValueError(f"無効な売買方向: {side}")
        
        # ティッカーデータ取得
        tickers = get_tickers([symbol])
        logging.info(f"ティッカーデータ取得結果: {tickers}")
        
        if not tickers or 'data' not in tickers:
            raise ValueError("ティッカーデータの取得に失敗しました")
        
        # 通貨ペアのレート取得
        rate_data = None
        for item in tickers['data']:
            if item['symbol'] == symbol:
                rate_data = item
                break
        
        if not rate_data:
            raise ValueError(f"{symbol}のレート情報の取得に失敗しました")
        
        # 売買方向に応じたレート選択
        if side == "BUY":
            rate = float(rate_data['ask'])  # 買い注文はask
        else:
            rate = float(rate_data['bid'])  # 売り注文はbid
        
        if rate <= 0:
            raise ValueError(f"無効なレート: {rate}")
        
        # 安全マージンを適用
        safety_margin = 0.95
        balance_float = float(balance)
        leverage_float = float(leverage)
        
        # 安全な計算（ゼロ除算防止）
        if rate == 0:
            raise ValueError("レートが0のため計算できません")
        
        # GMOコインの正しいロット計算式
        # 証拠金必要額 = 取引額 ÷ レバレッジ
        # 取引額 = ロット数 × レート
        
        # リスク管理のための証拠金使用割合
        risk_percentage = RISK_RATIO  # 設定ファイルのrisk_ratioを使用
        available_balance = balance_float * risk_percentage * safety_margin
        
        # 通貨ペアに応じた計算
        if "JPY" in symbol:
            # JPYペアの場合：1lot = 1通貨（円基準）
            # 証拠金は円なので、そのまま計算可能
            volume = int((available_balance * leverage_float) / rate)
        else:
            # USDペアの場合：1lot = 1通貨（USD基準）
            # 証拠金を円からUSDに変換してから計算
            # USD/JPYレートを取得して円→USD変換
            usdjpy_tickers = get_tickers(['USD_JPY'])
            if usdjpy_tickers and 'data' in usdjpy_tickers:
                usdjpy_rate = None
                for item in usdjpy_tickers['data']:
                    if item['symbol'] == 'USD_JPY':
                        usdjpy_rate = float(item['bid'])  # 円売りレート（USDを買う）
                        break
                
                if usdjpy_rate and usdjpy_rate > 0:
                    # 円証拠金をUSDに変換
                    available_balance_usd = available_balance / usdjpy_rate
                    # USD基準でロット計算
                    volume = int((available_balance_usd * leverage_float) / rate)
                    logging.info(f"USDペア計算: 円証拠金={available_balance}, USD/JPY={usdjpy_rate}, USD証拠金={available_balance_usd}, 計算結果={volume}")
                else:
                    # USD/JPYレート取得失敗時は円基準で計算（フォールバック）
                    volume = int((available_balance * leverage_float) / rate)
                    logging.warning(f"USD/JPYレート取得失敗、円基準で計算: {volume}")
            else:
                # USD/JPYレート取得失敗時は円基準で計算（フォールバック）
                volume = int((available_balance * leverage_float) / rate)
                logging.warning(f"USD/JPYレート取得失敗、円基準で計算: {volume}")
        
        # 最小ロット数チェック
        if volume < 1:
            volume = 1
            logging.warning(f"計算されたロット数が1未満のため、最小値1に設定しました")
        
        # 最大ロット数制限（GMOコインの制限に基づく）
        max_lot = 500000  # 50万ロット制限（一回の注文上限）
        if volume > max_lot:
            volume = max_lot
            logging.warning(f"計算されたロット数が最大制限を超えたため、{max_lot}に制限しました")
        
        # デバッグ情報
        if "JPY" in symbol:
            logging.info(f"ロット計算詳細(JPYペア): 証拠金={balance_float}, リスク割合={risk_percentage}, 利用可能額={available_balance}, レバレッジ={leverage_float}, レート={rate}, 安全マージン={safety_margin}, 計算結果={volume}")
        else:
            logging.info(f"ロット計算詳細(USDペア): 証拠金={balance_float}, リスク割合={risk_percentage}, 利用可能額={available_balance}, レバレッジ={leverage_float}, レート={rate}, 安全マージン={safety_margin}, 計算結果={volume}")
        
        return volume
    except Exception as e:
        logging.error(f"自動ロット計算エラー: {e}")
        raise

def get_position_by_order_id(order_data, symbol=None, side=None, expected_units=None):
    """
    新規注文のorderIdから建玉情報（positionId等）を取得（完全版）
    MAX_RETRIES: 最大リトライ回数
    RETRY_DELAY: リトライ間隔（秒）
    """
    MAX_RETRIES = 5
    RETRY_DELAY = 2
    position_id = None
    execution_time = datetime.now()

    try:
        # 入力データ検証（型チェック強化版）
        if not order_data or not isinstance(order_data, list) or len(order_data) == 0:
            logging.error("無効な注文データ形式")
            send_discord_message("⚠️ ポジション取得エラー: 無効な注文データ形式")
            return None
            
        order_id = order_data[0].get('orderId')
        if not order_id:
            logging.error("注文IDが存在しません")
            send_discord_message("⚠️ ポジション取得エラー: 注文IDなし")
            return None

        # OANDA版: OpenPositionsから該当銘柄/方向のポジションを探す
        # symbolのOANDA形式へ
        target_symbol = symbol
        if symbol and len(symbol) == 6 and '_' not in symbol:
            target_symbol = f"{symbol[:3]}_{symbol[3:]}"

        for attempt in range(MAX_RETRIES):
            try:
                oanda_rate_limit()
                r = positions.OpenPositions(OANDA_ACCOUNT_ID)
                resp = oanda_api.request(r)
                if 'positions' in resp:
                    for pos in resp['positions']:
                        if target_symbol and pos.get('instrument') != target_symbol:
                            continue
                        long_units = float(pos.get('long', {}).get('units', 0) or 0)
                        short_units = float(pos.get('short', {}).get('units', 0) or 0)

                        # 候補を組み立て
                        candidates = []
                        if long_units > 0:
                            candidates.append(('BUY', long_units, float(pos.get('long', {}).get('averagePrice', 0) or 0), 'long'))
                        if short_units > 0:
                            candidates.append(('SELL', short_units, float(pos.get('short', {}).get('averagePrice', 0) or 0), 'short'))

                        # 方向マッチ優先
                        for cand_side, units, price, side_key in candidates:
                            if side and cand_side != side:
                                continue
                            # 期待数量があれば近いものを優先
                            if expected_units is not None and abs(units) + 1e-9 < float(expected_units):
                                continue

                            position_id = f"{pos.instrument}-{side_key}"
                            open_time = execution_time.isoformat(timespec='milliseconds') + 'Z'
                            return {
                                'positionId': position_id,
                                'symbol': pos.instrument,
                                'side': cand_side,
                                'price': price,
                                'size': float(units),
                                'openTime': open_time,
                                'entry_time': execution_time.strftime('%H:%M:%S')
                            }
                time.sleep(RETRY_DELAY)
            except Exception as e:
                logging.warning(f"OANDAポジション取得リトライ中: {e}")
                time.sleep(RETRY_DELAY)

        logging.error(f"{MAX_RETRIES}回リトライ後もOANDAポジションを検出できず")
        return None

    except Exception as e:
        logging.error(f"ポジション情報取得エラー: {e}")
        send_discord_message(f"⚠️ ポジション情報取得エラー: {str(e)}")
        return None

# GMO固有関数 - OANDA用関数に置き換え済み
# def check_current_positions(symbol):
#     """現在のポジションをチェック（OANDA版）"""
#     # ... 既存のコード ...

# ===============================
# 取引実行関数（元のロジックを統合）
# ===============================
def process_trades(trades):
    """
    trades.csvの取引指示に従い、エントリー・監視・決済を実行するメイン処理
    各処理の流れと目的を詳細コメントで明示
    """
    global trade_results, total_api_fee
    positions_to_monitor = []  # 監視対象の建玉リスト
    logging.info(f"取引処理開始: {len(trades)}件の取引データ")
    
    for i, trade in enumerate(trades):
        try:
            logging.info(f"取引データ {i+1} 処理開始: {trade}")
            
            # エントリー・決済予定時刻をdatetime型に変換
            now = datetime.now()
            
            # 調整済みのdatetimeオブジェクトがある場合はそれを使用
            if len(trade) >= 8 and isinstance(trade[6], datetime) and isinstance(trade[7], datetime):
                # 調整済みのdatetimeオブジェクトを使用
                entry_time = trade[6]
                exit_time = trade[7]
                logging.info(f"取引データ {i+1}: 調整済み時刻を使用 - entry_time={entry_time}, exit_time={exit_time}")
            else:
                # 従来の処理（後方互換性のため）
                entry_time = datetime.strptime(trade[3], '%H:%M:%S').replace(
                    year=now.year, month=now.month, day=now.day)
                exit_time = datetime.strptime(trade[4], '%H:%M:%S').replace(
                    year=now.year, month=now.month, day=now.day)
                
                # 日を跨ぐ取引の場合、exit_timeを適切に調整
                if exit_time <= entry_time:
                    exit_time = exit_time + timedelta(days=1)
            
            logging.info(f"取引データ {i+1}: 時刻設定 - entry_time={entry_time}, exit_time={exit_time}, now={now}")
            
            # 予定時刻を過ぎていたらスキップ
            current_time = datetime.now()
            if entry_time < current_time:
                # 日を跨ぐ取引の場合は、現在時刻が00:00-06:00の範囲で、エントリー時刻が00:00-06:00の場合は翌日として扱う
                if (current_time.hour < 6 and entry_time.hour < 6 and 
                    entry_time.date() == current_time.date()):
                    # 翌日に調整
                    entry_time = entry_time + timedelta(days=1)
                    exit_time = exit_time + timedelta(days=1)
                    logging.info(f"取引データ {i+1}: 日を跨ぐ取引として翌日に調整 - entry_time={entry_time}, exit_time={exit_time}")
                else:
                    skip_msg = f"取引データ {i+1} のエントリー時間が過ぎています。スキップします。entry_time={entry_time}, now={current_time}"
                    logging.warning(skip_msg)
                    send_discord_message(skip_msg)
                    continue

            logging.info(f"取引データ {i+1}: エントリー処理開始 - entry_time={entry_time}, exit_time={exit_time}")

            # --- JITTER（ゆらぎ）ロジック修正 ---
            now = datetime.now()
            if now < entry_time:
                jitter = random.uniform(0, JITTER_SECONDS)
                target_time = entry_time - timedelta(seconds=jitter)
                wait_time = (target_time - now).total_seconds()
                logging.info(f"取引データ {i+1}: エントリー時刻まで待機 - wait_time={wait_time}秒, target_time={target_time}")
                if wait_time > 0:
                    time.sleep(wait_time)
                # ここでエントリー実行（予定時刻-jitter～予定時刻の間で実行）

            # 売買方向・ロット数を設定
            # 漢字（買/売）と英語（long/short）の両方に対応（大文字・小文字対応）
            direction = trade[1].strip().lower()
            if direction in ["買", "long", "l"]:
                side = "BUY"
            elif direction in ["売", "short", "s"]:
                side = "SELL"
            else:
                error_msg = f"取引データ {i+1}: 無効な売買方向 '{trade[1]}' が指定されました。'買'/'売'/'long'/'short'/'l'/'s'のいずれかを指定してください。"
                logging.error(error_msg)
                send_discord_message(error_msg)
                continue
            
            # ロット数が空の場合はNone、そうでなければ数値に変換
            if trade[5].strip() == "":
                # ロット数未指定の場合
                lot_size = None
                # autolot=OFFでロット未指定の場合のみ18倍を使用
                if AUTOLOT == 'FALSE':
                    custom_leverage = 18
                else:
                    custom_leverage = LEVERAGE
            else:
                # ロット数の処理（空文字列の場合はNone、数値の場合はfloat）
                lot_str = trade[5].strip() if len(trade) > 5 else ""
                lot_size = float(lot_str) if lot_str else None
                custom_leverage = LEVERAGE
            
            # 通貨ペアの正規化（USDJPY → USD_JPY、USD/JPY → USD_JPY）
            pair_raw = trade[2].strip()
            if "/" in pair_raw:
                pair = pair_raw.replace("/", "_")
            else:
                # USDJPY → USD_JPY の変換
                if len(pair_raw) == 6:  # USDJPY, EURUSD など
                    pair = f"{pair_raw[:3]}_{pair_raw[3:]}"
                else:
                    pair = pair_raw  # その他の形式はそのまま
            
            logging.info(f"取引データ {i+1}: 取引設定 - pair={pair}, side={side}, lot_size={lot_size}, leverage={custom_leverage}")

            entry_success = False
            for attempt in range(MAX_ENTRY_ORDER_ATTEMPTS):
                logging.info(f"取引データ {i+1}: エントリー試行 {attempt+1}/{MAX_ENTRY_ORDER_ATTEMPTS}")
                
                # 最新レート取得
                ticker_data = get_tickers([pair])
                # ここでbid/ask/spreadを計算
                if not ticker_data or 'data' not in ticker_data or len(ticker_data['data']) == 0:
                    # エラー処理（例: Discord通知してcontinue）
                    logging.warning(f"取引データ {i+1}: ティッカーデータ取得失敗 - ticker_data={ticker_data}")
                    time.sleep(ENTRY_ORDER_RETRY_INTERVAL)
                    continue
                
                # 修正: symbol==pairのものを必ず参照
                rate_data = None
                for item in ticker_data['data']:
                    if item['symbol'] == pair:
                        rate_data = item
                        break
                if not rate_data:
                    logging.warning(f"取引データ {i+1}: {pair}のレート情報が見つかりませんでした - ticker_data={ticker_data}")
                    time.sleep(ENTRY_ORDER_RETRY_INTERVAL)
                    continue
                bid = float(rate_data['bid'])
                ask = float(rate_data['ask'])
                spread = ask - bid
                # 通貨ペアの正しい判定
                if pair.endswith("JPY"):
                    pip_value = 0.01
                else:
                    pip_value = 0.0001
                spread_pips = spread / pip_value
                
                logging.info(f"取引データ {i+1}: レート情報 - bid={bid}, ask={ask}, spread_pips={spread_pips}")
                
                # スプレッド判定
                if spread > SPREAD_THRESHOLD:
                    spread_msg = f"取引データ {i+1} (試行 {attempt+1}/{MAX_ENTRY_ORDER_ATTEMPTS}) のスプレッドが閾値を超えています ({spread:.3f} > {SPREAD_THRESHOLD:.3f})。再試行します。"
                    logging.warning(spread_msg)
                    send_discord_message(spread_msg)
                    time.sleep(ENTRY_ORDER_RETRY_INTERVAL)
                    continue
                try:
                    # デバッグ用ログ
                    logging.info(f"取引データ {i+1}: エントリー注文発注開始 - {pair} {side} lot_size={lot_size}")
                    print(f"エントリー試行: {pair} {side} lot_size={lot_size}")
                    # 新規注文発注
                    if lot_size is None:
                        response_order, actual_size = send_order(pair, side, None, custom_leverage)
                    else:
                        response_order, actual_size = send_order(pair, side, lot_size, custom_leverage)
                    logging.info(f"取引データ {i+1}: エントリー注文レスポンス - {response_order}")
                    
                    # 建玉情報取得
                    if 'data' in response_order and response_order['data']:
                        position_info = get_position_by_order_id(
                            response_order['data'], symbol=pair, side=side,
                            expected_units=actual_size if 'actual_size' in locals() else None
                        )
                    else:
                        logging.error(f"APIレスポンスに'data'がありません: {response_order}")
                        send_discord_message(f"エントリー注文エラー: APIレスポンスに'data'がありません: {response_order}")
                        continue
                    if position_info:
                        logging.info(f"取引データ {i+1}: ポジション情報取得成功 - {position_info}")
                        # 監視用情報を付与してリストに追加
                        position_info['exit_time'] = exit_time
                        position_info['auto_closed'] = False
                        position_info['trade_index'] = i+1
                        positions_to_monitor.append(position_info)
                        entry_success = True
                        # エントリー成功通知
                        entry_price = position_info['price']
                        actual_entry_time = datetime.now()  # ←ここで実際のエントリー時刻を取得
                        # 自動ロットが使用されたかどうかを判定
                        if AUTOLOT == 'TRUE':
                            lot_info = f"自動ロット={actual_size}"
                        else:
                            lot_info = f"ロット数={trade[5]}"
                        success_msg = f"エントリーしました: 通貨ペア={pair}, 売買方向={side}, {lot_info}, エントリー価格={entry_price}, Bid={format_price(bid, pair)}, Ask={format_price(ask, pair)}, スプレッド={spread_pips:.3f}pips, エントリー時間={actual_entry_time.strftime('%H:%M:%S')}, 決済予定時間={exit_time.strftime('%H:%M:%S')}"
                        logging.info(f"取引データ {i+1}: {success_msg}")
                        send_discord_message(success_msg)
                        break  # エントリー成功でリトライループ脱出
                    else:
                        logging.error(f"取引データ {i+1}: ポジション情報取得失敗")
                except Exception as e:
                    error_msg = f"エントリー注文エラー (試行 {attempt+1}/{MAX_ENTRY_ORDER_ATTEMPTS}): {e}"
                    logging.error(f"取引データ {i+1}: {error_msg}\n{traceback.format_exc()}")
                    print(f"DEBUG: {error_msg}")  # デバッグ用コンソール出力
                    send_discord_message(error_msg)
                    time.sleep(ENTRY_ORDER_RETRY_INTERVAL)
            
            # すべてのエントリー試行終了後に最終ポジションチェック
            if not entry_success:
                logging.warning(f"取引データ {i+1}: すべてのエントリー試行が失敗、最終ポジションチェック実行")
                positions = check_current_positions(pair)
                if positions:
                    for position in positions:
                        logging.warning(f"すべての試行でエラーが報告されましたが、ポジションが検出されました。")
                        send_discord_message(f"⚠️ 警告: エラー報告後にポジションを検出しました: {pair} {side}")
                        position['exit_time'] = exit_time
                        position['auto_closed'] = False
                        position['trade_index'] = i+1
                        positions_to_monitor.append(position)
                        entry_success = True
                        break
            if not entry_success:
                skip_msg = f"取引データ {i+1} は最大試行回数を超えたため、エントリーをスキップします。"
                logging.error(f"取引データ {i+1}: {skip_msg}")
                send_discord_message(skip_msg)
                # 念のため定期的にポジション確認を行う
                logging.info("念のため定期的なポジション確認を開始します")
                schedule_future_check = threading.Thread(
                    target=schedule_position_check, 
                    args=(pair, exit_time)
                )
                schedule_future_check.daemon = True
                schedule_future_check.start()
                continue

            logging.info(f"取引データ {i+1}: 決済監視開始 - exit_time={exit_time}")

            # --- 決済時jitter（前倒し）ロジック修正版 ---
            # 1. 決済予定時刻-jitterの時点で監視ループを終了する
            jitter = random.uniform(0, JITTER_SECONDS)
            target_time = exit_time - timedelta(seconds=jitter)

            # 2. target_timeまでポジション監視（ストップロス・テイクプロフィット自動決済対応）
            while datetime.now() < target_time:
                try:
                    monitor_and_close_positions(positions_to_monitor)
                except Exception as e:
                    logging.error(f"ポジション監視処理中のエラー: {e}\n{traceback.format_exc()}")
                    send_discord_message(f"⚠️ ポジション監視エラー: {e}")
                time.sleep(POSITION_CHECK_INTERVAL)

            # 3. target_timeになったら即決済（リトライ機能付き）
            for position in positions_to_monitor[:]:
                if position['trade_index'] == i+1 and not position['auto_closed']:
                    logging.info(f"取引データ {i+1}: 時間指定決済開始")
                    # 決済処理にリトライ機能を追加
                    for retry_attempt in range(MAX_EXIT_ORDER_ATTEMPTS):
                        try:
                            close_position_by_info(position, exit_time, auto_closed=False, trade_index=i+1)
                            positions_to_monitor.remove(position)
                            logging.info(f"取引データ {i+1}: 決済成功")
                            break  # 成功したらリトライループを抜ける
                        except Exception as e:
                            error_msg = f"決済処理エラー (試行 {retry_attempt+1}/{MAX_EXIT_ORDER_ATTEMPTS}): {e}"
                            logging.error(f"{error_msg}\n{traceback.format_exc()}")
                            send_discord_message(error_msg)
                            if retry_attempt < MAX_EXIT_ORDER_ATTEMPTS - 1:
                                time.sleep(EXIT_ORDER_RETRY_INTERVAL)
                            else:
                                # 最大リトライ回数に達した場合
                                send_discord_message(f"⚠️ 決済処理が最大試行回数を超えました: {position['symbol']} {position['side']}")
                                # 最終的に手動決済を試行
                                try:
                                    exit_side = "SELL" if position.side == "BUY" else "BUY"
                                    broker.close_position(position.symbol, position.position_id, position.size, exit_side)
                                    send_discord_message(f"⚠️ 手動決済を実行しました: {position.symbol} {position.side}")
                                    positions_to_monitor.remove(position)
                                except Exception as final_e:
                                    logging.error(f"手動決済も失敗: {final_e}\n{traceback.format_exc()}")
                                    send_discord_message(f"⚠️ 手動決済も失敗しました: {position['symbol']} {position['side']} - {final_e}")

        except Exception as e:
            # 取引データごとの例外もDiscord通知
            error_msg = f"取引データ {i+1} の処理中にエラーが発生しました: {e}"
            logging.error(f"{error_msg}\n{traceback.format_exc()}")
            send_discord_message(error_msg)

    logging.info("すべての取引処理完了")
    
    # 監視中のポジションがある場合は、それらが決済されるまで待機
    if positions_to_monitor:
        logging.info(f"監視中のポジションが{len(positions_to_monitor)}件あります。決済完了まで待機します。")
        send_discord_message(f"📊 監視中のポジションが{len(positions_to_monitor)}件あります。決済完了まで待機します。")
        
        # 各ポジションの決済予定時刻を確認
        for position in positions_to_monitor:
            exit_time = position.get('exit_time')
            if exit_time:
                logging.info(f"ポジション {position['symbol']} {position['side']} の決済予定時刻: {exit_time}")
                send_discord_message(f"⏰ ポジション {position['symbol']} {position['side']} の決済予定時刻: {exit_time.strftime('%H:%M:%S')}")
        
        # 最終決済は行わず、各ポジションが予定時刻に決済されるのを待つ
        # これにより、23:36エントリー00:05決済のような日を跨ぐ取引も適切に処理される
    else:
        logging.info("監視中のポジションはありません。")

def enter_trade(trade_data):
    """エントリー処理（詳細なスプレッド管理と重複建玉防止付き）"""
    try:
        logging.info(f"エントリー処理開始: {trade_data.trade_number} {trade_data.direction} {trade_data.symbol}")
        
        # 売買方向の設定
        direction = trade_data.direction.strip().lower()
        if direction in ["買", "long", "l"]:
            side = "BUY"
        elif direction in ["売", "short", "s"]:
            side = "SELL"
        else:
            error_msg = f"無効な売買方向 '{trade_data.direction}' が指定されました。"
            logging.error(error_msg)
            send_discord_message(error_msg)
            return False
        
        # 通貨ペアの正規化
        pair_raw = trade_data.symbol.strip()
        if "/" in pair_raw:
            pair = pair_raw.replace("/", "_")
        else:
            if len(pair_raw) == 6:  # USDJPY, EURUSD など
                pair = f"{pair_raw[:3]}_{pair_raw[3:]}"
            else:
                pair = pair_raw
        
        # 重複建玉防止チェック
        positions = check_current_positions(pair)
        for pos in positions:
            if pos.side == side:
                logging.warning(f"重複建玉検出: {pair} {side} 既存建玉あり。再注文をスキップします。")
                send_discord_message(f"重複建玉検出: {pair} {side} 既存建玉あり。再注文をスキップします。")
                return True  # 重複建玉がある場合は成功として扱う
        
        # ロット数の設定
        if trade_data.lot_size is None or trade_data.lot_size.strip() == "":
            # ロット数未指定の場合
            lot_size = None
            if AUTOLOT == 'FALSE':
                custom_leverage = 18
            else:
                custom_leverage = LEVERAGE
        else:
            # ロット数の処理
            lot_str = trade_data.lot_size.strip()
            lot_size = float(lot_str) if lot_str else None
            custom_leverage = LEVERAGE
        
        logging.info(f"取引設定: pair={pair}, side={side}, lot_size={lot_size}, leverage={custom_leverage}")
        
        # 最新レート取得
        ticker_data = get_tickers([pair])
        if not ticker_data or 'data' not in ticker_data or len(ticker_data['data']) == 0:
            logging.warning(f"ティッカーデータ取得失敗: {ticker_data}")
            return False
        
        # レート情報の取得
        rate_data = None
        for item in ticker_data['data']:
            if item['symbol'] == pair:
                rate_data = item
                break
        
        if not rate_data:
            logging.warning(f"{pair}のレート情報が見つかりませんでした")
            return False
        
        bid = float(rate_data['bid'])
        ask = float(rate_data['ask'])
        spread = ask - bid
        
        # 通貨ペアの正しいpip値判定
        if pair.endswith("JPY"):
            pip_value = 0.01
        else:
            pip_value = 0.0001
        
        spread_pips = spread / pip_value
        
        logging.info(f"レート情報: bid={bid}, ask={ask}, spread_pips={spread_pips}")
        
        # 詳細なスプレッド判定
        if spread > SPREAD_THRESHOLD:
            spread_msg = f"スプレッドが閾値を超えています ({spread:.3f} > {SPREAD_THRESHOLD:.3f}, {spread_pips:.1f}pips)。エントリーをスキップします。"
            logging.warning(spread_msg)
            send_discord_message(spread_msg)
            return False
        
        # エントリー注文発注
        logging.info(f"エントリー注文発注: {pair} {side} lot_size={lot_size}")
        
        if lot_size is None:
            response_order, actual_size = send_order(pair, side, None, custom_leverage)
        else:
            response_order, actual_size = send_order(pair, side, lot_size, custom_leverage)
        
        if not response_order or 'data' not in response_order:
            logging.error(f"エントリー注文エラー: {response_order}")
            return False
        
        # ポジション情報取得
        position_info = get_position_by_order_id(
            response_order['data'], symbol=pair, side=side,
            expected_units=actual_size if 'actual_size' in locals() else None
        )
        if position_info:
            # エントリー成功通知
            entry_price = position_info['price']
            actual_entry_time = datetime.now(JST)
            
            if AUTOLOT == 'TRUE':
                lot_info = f"自動ロット={actual_size}"
            else:
                lot_info = f"ロット数={trade_data.lot_size}"
            
            success_msg = (f"エントリー成功: 取引番号{trade_data.trade_number}, "
                         f"通貨ペア={pair}, 売買方向={side}, {lot_info}, "
                         f"エントリー価格={entry_price}, "
                         f"Bid={format_price(bid, pair)}, Ask={format_price(ask, pair)}, "
                         f"スプレッド={spread_pips:.1f}pips, "
                         f"エントリー時間={actual_entry_time.strftime('%H:%M:%S')}")
            
            logging.info(success_msg)
            send_discord_message(success_msg)
            return True
        else:
            logging.error("ポジション情報取得失敗")
            return False
            
    except Exception as e:
        error_msg = f"エントリー処理エラー: {e}"
        logging.error(error_msg)
        send_discord_message(error_msg)
        return False

def exit_trade(trade_data):
    """決済処理（元のロジックを統合）"""
    try:
        logging.info(f"決済処理開始: {trade_data.trade_number} {trade_data.direction} {trade_data.symbol}")
        
        # 通貨ペアの正規化
        pair_raw = trade_data.symbol.strip()
        if "/" in pair_raw:
            pair = pair_raw.replace("/", "_")
        else:
            if len(pair_raw) == 6:
                pair = f"{pair_raw[:3]}_{pair_raw[3:]}"
            else:
                pair = pair_raw
        
        # 現在のポジションをチェック
        positions = check_current_positions(pair)
        if not positions:
            logging.info(f"{pair}のポジションが見つかりません")
            return True  # ポジションがない場合は成功として扱う
        
        # 各ポジションを決済
        for position in positions:
            try:
                position_id = getattr(position, 'position_id', None)
                size = getattr(position, 'size', None)
                side = getattr(position, 'side', None)
                
                if position_id and size and side:
                    logging.info(f"決済実行: {pair} {side} size={size}")
                    
                    response = broker.close_position(pair, position_id, size, side)
                    if response and 'data' in response:
                        # 決済成功通知
                        exit_price = response['data'].get('price', 'N/A')
                        actual_exit_time = datetime.now(JST)
                        
                        success_msg = (f"決済成功: 取引番号{trade_data.trade_number}, "
                                     f"通貨ペア={pair}, 売買方向={side}, "
                                     f"決済価格={exit_price}, "
                                     f"決済時間={actual_exit_time.strftime('%H:%M:%S')}")
                        
                        logging.info(success_msg)
                        send_discord_message(success_msg)
                    else:
                        logging.error(f"決済レスポンスエラー: {response}")
                        return False
                        
            except Exception as e:
                logging.error(f"個別ポジション決済エラー: {e}")
                return False
        
        return True
        
    except Exception as e:
        error_msg = f"決済処理エラー: {e}"
        logging.error(error_msg)
        send_discord_message(error_msg)
        return False

# ===============================
# 新しいメインループ
# ===============================
def main_loop(schedule: TradeSchedule):
    """新しい時刻判定ロジックを使用したメインループ（詳細なJitter機能付き）"""
    logger = logging.getLogger(__name__)
    shutdown = GracefulShutdown()
    metrics = TradingMetrics()
    
    logger.info("取引システムを開始しました")
    logger.info(f"スケジュールファイル: {SCHEDULE_CSV}")
    logger.info(f"バッファ秒数: {BUFFER_SECONDS}")
    logger.info(f"Jitter秒数: {JITTER_SECONDS}")
    send_discord_message("取引システムを開始しました")
    
    while not shutdown.is_shutdown_requested():
        try:
            current_time = schedule.now()
            
            # 今日の取引を取得
            today_trades = schedule.get_trades_for_today()
            
            if schedule.should_enter():
                logger.info(f"エントリー条件を検出: {current_time}")
                
                # 次の取引を取得
                next_trade = schedule.get_next_trade()
                if next_trade:
                    logger.info(f"エントリー取引: {next_trade.trade_number} {next_trade.direction} {next_trade.symbol}")
                    
                    # Jitter（ゆらぎ）ロジック
                    jitter = random.uniform(0, JITTER_SECONDS)
                    logger.info(f"Jitter計算: {jitter:.2f}秒")
                    
                    # 取引情報をDiscordに通知
                    trade_info = (f"エントリー実行: 取引番号{next_trade.trade_number}, "
                                f"{next_trade.direction}, {next_trade.symbol}, "
                                f"時刻{next_trade.entry_time.strftime('%H:%M:%S')}, "
                                f"Jitter: {jitter:.2f}秒")
                    send_discord_message(trade_info)
                
                if enter_trade(next_trade):
                    metrics.entry_count += 1
                    metrics.last_entry = current_time
                
            elif schedule.should_exit():
                logger.info(f"決済条件を検出: {current_time}")
                
                # アクティブな取引を取得
                active_trades = schedule.get_active_trades()
                if active_trades:
                    for trade in active_trades:
                        logger.info(f"決済取引: {trade.trade_number} {trade.direction} {trade.symbol}")
                        
                        # 決済時Jitter（前倒し）ロジック
                        jitter = random.uniform(0, JITTER_SECONDS)
                        logger.info(f"決済Jitter計算: {jitter:.2f}秒")
                        
                        # 取引情報をDiscordに通知
                        trade_info = (f"決済実行: 取引番号{trade.trade_number}, "
                                    f"{trade.direction}, {trade.symbol}, "
                                    f"時刻{trade.exit_time.strftime('%H:%M:%S')}, "
                                    f"Jitter: {jitter:.2f}秒")
                        send_discord_message(trade_info)
                        
                        # 各取引に対して決済処理を実行
                        if exit_trade(trade):
                            metrics.exit_count += 1
                            metrics.last_exit = current_time
                        else:
                            # フォールバック: 取引対象シンボルの残存ポジションを強制決済
                            try:
                                positions = broker.get_all_positions()
                                target_symbol = trade.symbol.replace('/', '_')
                                for pos in positions:
                                    if pos.symbol == target_symbol:
                                        exit_side = 'SELL' if pos.side == 'BUY' else 'BUY'
                                        broker.close_position(pos.symbol, pos.position_id, pos.size, exit_side)
                                        logger.info(f"フォールバック決済実行: {pos.symbol} {pos.side} size={pos.size}")
                                        metrics.exit_count += 1
                                        metrics.last_exit = current_time
                            except Exception as e:
                                logger.error(f"フォールバック決済エラー: {e}")
                else:
                    # アクティブ取引が検出できない場合のフォールバック: 今日の銘柄で残存ポジションをクローズ
                    try:
                        today_trades = schedule.get_trades_for_today()
                        symbols = set()
                        for t in today_trades:
                            s = t.symbol.replace('/', '_') if '/' in t.symbol else (f"{t.symbol[:3]}_{t.symbol[3:]}" if len(t.symbol) == 6 and '_' not in t.symbol else t.symbol)
                            symbols.add(s)
                        positions = broker.get_all_positions()
                        for pos in positions:
                            if pos.symbol in symbols:
                                exit_side = 'SELL' if pos.side == 'BUY' else 'BUY'
                                broker.close_position(pos.symbol, pos.position_id, pos.size, exit_side)
                                logger.info(f"フォールバック決済実行(アクティブなし): {pos.symbol} {pos.side} size={pos.size}")
                                metrics.exit_count += 1
                                metrics.last_exit = current_time
                    except Exception as e:
                        logger.error(f"フォールバック決済エラー(アクティブなし): {e}")
            
            # 定期的にメトリクスをログ出力
            if (metrics.entry_count + metrics.exit_count) % 10 == 0:
                logger.info(f"メトリクス: エントリー{metrics.entry_count}回, 決済{metrics.exit_count}回")
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"メインループでエラーが発生: {e}")
            time.sleep(5)  # エラー時は少し長めに待機
    
    logger.info("グレースフルシャットダウン完了")
    send_discord_message("取引システムを停止しました")

# ===============================
# Discord Bot機能
# ===============================
DISCORD_BOT_TOKEN = config.get('discord_bot_token', None)

if DISCORD_BOT_TOKEN:
    intents = discord.Intents.default()
    intents.message_content = True  # メッセージ内容Intentを有効化
    bot = commands.Bot(command_prefix='', intents=intents, case_insensitive=True)

    @bot.event
    async def on_ready():
        """Bot起動時の処理"""
        logging.info(f'Discord Bot connected as {bot.user}')
        send_discord_message(f"🤖 Botが起動しました: {bot.user}")
        
    @bot.event
    async def on_command_error(ctx, error):
        """コマンドエラー時の処理"""
        if isinstance(error, commands.CommandNotFound):
            await ctx.send("❌ 不明なコマンドです。`command`でコマンド一覧を確認してください。")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ このコマンドを実行する権限がありません。")
        else:
            logging.error(f"Discord Bot コマンドエラー: {error}")
            await ctx.send(f"❌ コマンド実行中にエラーが発生しました: {str(error)}")

    @bot.command(name='kill')
    async def kill(ctx):
        """全ポジションを即座に決済（緊急時）"""
        global trade_results
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ このコマンドは管理者のみ実行可能です。")
            return
        await ctx.send('🚨 全通貨ペアの全ポジション決済を実行します...')
        logging.warning(f"Discord Bot: 全ポジション決済コマンド実行 by {ctx.author}")
        try:
            positions = broker.get_all_positions()
            if not positions:
                await ctx.send('✅ 現在ポジションはありません。')
                return
            closed = []
            success_count = 0
            error_count = 0
            for pos in positions:
                try:
                    if not isinstance(pos, dict) or not all(key in pos for key in ['symbol', 'positionId', 'size', 'side', 'price']):
                        closed.append(f"❌ 無効なポジション情報: {pos}")
                        error_count += 1
                        continue
                    exit_side = 'SELL' if pos.side == 'BUY' else 'BUY'
                    entry_price = float(pos.price)
                    size = float(pos.size)
                    symbol = pos.symbol
                    executed_price = broker.close_position(symbol, pos.position_id, size, exit_side)
                    profit_pips = calculate_profit_pips(entry_price, executed_price, pos.side, symbol)
                    profit_amount = calculate_profit_amount(entry_price, executed_price, pos.side, symbol, size)
                    closed.append(
                        f"✅ {symbol} {pos.side} {size}lot 決済\n"
                        f"エントリー価格: {entry_price}\n"
                        f"決済価格: {executed_price}\n"
                        f"損益: {profit_pips}pips ({profit_amount}円)"
                    )
                    # trade_resultsに追加
                    trade_results.append({
                        "symbol": symbol,
                        "side": pos.side,
                        "entry_price": entry_price,
                        "exit_price": executed_price,
                        "profit_pips": profit_pips,
                        "profit_amount": profit_amount,
                        "lot_size": size,
                        "entry_time": getattr(pos, 'openTime', ''),
                        "exit_time": datetime.now().strftime('%H:%M:%S'),
                        "entry_date": getattr(pos, 'entry_date', datetime.now().date()),
                        "exit_date": datetime.now().date(),
                    })
                    success_count += 1
                except Exception as e:
                    error_msg = f"❌ {pos.get('symbol', 'Unknown')} 決済失敗: {e}"
                    closed.append(error_msg)
                    error_count += 1
                    logging.error(f"ポジション決済エラー: {e}")
            result_msg = f"**決済結果**\n成功: {success_count}件, 失敗: {error_count}件\n\n"
            result_msg += '\n\n'.join(closed)
            if len(result_msg) > 2000:
                chunks = [result_msg[i:i+1900] for i in range(0, len(result_msg), 1900)]
                for i, chunk in enumerate(chunks):
                    await ctx.send(f"決済結果 (Part {i+1}/{len(chunks)}):\n{chunk}")
            else:
                await ctx.send(result_msg)
            positions_after = get_all_positions()
            if not positions_after:
                await ctx.send('✅ 全てのポジションが決済されました。')
            else:
                remaining_msg = '⚠️ 残存ポジション:\n'
                for pos in positions_after:
                    remaining_msg += f"{pos.symbol} {pos.side} {pos.size}\n"
                await ctx.send(remaining_msg)
        except Exception as e:
            error_msg = f'❌ 全ポジション決済中にエラーが発生しました: {e}'
            await ctx.send(error_msg)
            logging.error(f"全ポジション決済エラー: {e}")

    @bot.command(name='stop')
    async def stop(ctx):
        """ボットを停止"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ このコマンドは管理者のみ実行可能です。")
            return
        await ctx.send('🛑 ボットを停止します...')
        logging.warning(f"Discord Bot: 停止コマンド実行 by {ctx.author}")
        try:
            # 全ポジションを決済
            positions = get_all_positions()
            if positions:
                await ctx.send('⚠️ 残存ポジションを決済してから停止します...')
                for pos in positions:
                    try:
                        exit_side = 'SELL' if pos.side == 'BUY' else 'BUY'
                        broker.close_position(pos.symbol, pos.position_id, pos.size, exit_side)
                    except Exception as e:
                        logging.error(f"停止時のポジション決済エラー: {e}")
            await ctx.send('✅ ボットを停止しました。')
            sys.exit(0)
        except Exception as e:
            await ctx.send(f'❌ 停止中にエラーが発生しました: {e}')
            logging.error(f"停止エラー: {e}")

    @bot.command(name='restart')
    async def restart(ctx):
        """ボットを再起動"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ このコマンドは管理者のみ実行可能です。")
            return
        await ctx.send('🔄 ボットを再起動します...')
        logging.warning(f"Discord Bot: 再起動コマンド実行 by {ctx.author}")
        try:
            # 設定を再読み込み
            reload_config()
            await ctx.send('✅ 設定を再読み込みしました。')
            
            # ヘルスチェックを実行
            health_status = health_check()
            if health_status['overall_health']:
                await ctx.send('✅ システムは正常です。再起動完了。')
            else:
                await ctx.send('⚠️ システムに問題があります。詳細を確認してください。')
                error_items = [item for item, status in health_status.items() if not status and item != 'overall_health']
                await ctx.send(f'❌ 異常項目: {", ".join(error_items)}')
                
        except Exception as e:
            await ctx.send(f'❌ 再起動中にエラーが発生しました: {e}')
            logging.error(f"再起動エラー: {e}")

    @bot.command(name='position')
    async def position(ctx):
        """現在のポジションを表示"""
        try:
            positions = get_all_positions()
            if not positions:
                await ctx.send('📊 現在ポジションはありません。')
                return
            
            position_msg = "📊 **現在のポジション**\n"
            total_pnl = 0
            
            for pos in positions:
                try:
                    # 現在価格を取得
                    tickers = broker.get_tickers([pos.symbol])
                    current_price = None
                    if tickers and 'data' in tickers:
                        for item in tickers['data']:
                            if item['symbol'] == pos.symbol:
                                current_price = item
                                break
                    
                    if current_price:
                        # 含み損益を計算
                        if pos.side == 'BUY':
                            profit_pips = calculate_current_profit_pips(float(pos.price), current_price, 'BUY', pos.symbol)
                        else:
                            profit_pips = calculate_current_profit_pips(float(pos.price), current_price, 'SELL', pos.symbol)
                        
                        profit_amount = calculate_profit_amount(float(pos.price), 
                                                              float(current_price['bid']) if pos.side == 'BUY' else float(current_price['ask']), 
                                                              pos.side, pos.symbol, pos.size)
                        
                        position_msg += (f"**{pos.symbol}** {pos.side} {pos.size}lot\n"
                                       f"エントリー: {pos.price} | 現在: {current_price['bid']}/{current_price['ask']}\n"
                                       f"損益: {profit_pips}pips ({profit_amount}円)\n\n")
                        total_pnl += profit_amount
                    else:
                        position_msg += f"**{pos.symbol}** {pos.side} {pos.size}lot (価格取得失敗)\n\n"
                        
                except Exception as e:
                    position_msg += f"**{pos.symbol}** エラー: {e}\n\n"
            
            position_msg += f"**合計損益: {total_pnl:.2f}円**"
            
            if len(position_msg) > 2000:
                chunks = [position_msg[i:i+1900] for i in range(0, len(position_msg), 1900)]
                for i, chunk in enumerate(chunks):
                    await ctx.send(f"ポジション情報 (Part {i+1}/{len(chunks)}):\n{chunk}")
            else:
                await ctx.send(position_msg)
                
        except Exception as e:
            await ctx.send(f'❌ ポジション情報取得中にエラーが発生しました: {e}')
            logging.error(f"ポジション情報取得エラー: {e}")

    @bot.command(name='status')
    async def status(ctx):
        """システムステータスを表示（詳細版）"""
        try:
            status_info = get_system_status()
            if status_info:
                status_msg = "📈 **システムステータス（詳細版）**\n"
                status_msg += f"🕒 稼働時間: {status_info['uptime']}\n"
                status_msg += f"💾 メモリ使用量: {status_info['memory_usage']:.1f}MB ({status_info['memory_percent']:.1f}%)\n"
                status_msg += f"💿 ディスク空き容量: {status_info['disk_free_gb']:.1f}GB\n"
                status_msg += f"📊 API呼び出し: {status_info['api_calls']}回\n"
                status_msg += f"❌ APIエラー: {status_info['api_errors']}回\n"
                status_msg += f"⚡ レートリミット: {status_info['rate_limit']}回/秒\n"
                status_msg += f"⚠️ レートリミットエラー: {status_info['rate_limit_errors']}回\n"
                status_msg += f"💰 今日の取引: {status_info['today_trades']}回\n"
                status_msg += f"📈 今日の損益: {status_info['today_pnl']:.2f}円\n"
                status_msg += f"💸 累計API手数料: {status_info['total_api_fee']:.0f}円\n"
                status_msg += f"🔧 システム状態: {'✅ 正常' if status_info['overall_health'] else '⚠️ 注意'}"
                
                await ctx.send(status_msg)
            else:
                await ctx.send('❌ システム状態の取得に失敗しました。')
        except Exception as e:
            error_msg = f'❌ システム状態取得エラー: {e}'
            await ctx.send(error_msg)
            logging.error(f"システム状態取得エラー: {e}")

    @bot.command(name='health')
    async def health(ctx):
        """ヘルスチェックを実行（詳細版）"""
        try:
            await ctx.send('🔍 詳細ヘルスチェックを実行中...')
            health_status = health_check()
            if health_status:
                health_msg = "🏥 **詳細ヘルスチェック結果**\n"
                
                # 各項目の詳細表示
                checks = {
                    'api_connection': ('🌐 API接続', 'APIサーバーとの接続状態'),
                    'discord_connection': ('💬 Discord接続', 'Discord Webhookの接続状態'),
                    'memory_usage': ('💾 メモリ使用量', 'システムメモリの使用状況'),
                    'disk_space': ('💿 ディスク容量', 'ディスクの空き容量'),
                    'file_access': ('📁 ファイルアクセス', '重要ファイルのアクセス権限')
                }
                
                for check_key, (emoji, description) in checks.items():
                    if check_key in health_status:
                        status = "✅ 正常" if health_status[check_key] else "❌ 異常"
                        health_msg += f"{emoji} {description}: {status}\n"
                
                health_msg += f"\n**総合判定**: {'✅ 全項目正常' if health_status['overall_health'] else '❌ 異常項目あり'}"
                
                await ctx.send(health_msg)
            else:
                await ctx.send('❌ ヘルスチェックの実行に失敗しました。')
        except Exception as e:
            error_msg = f'❌ ヘルスチェックエラー: {e}'
            await ctx.send(error_msg)
            logging.error(f"ヘルスチェックエラー: {e}")

    @bot.command(name='backup')
    async def backup(ctx):
        """手動バックアップを実行（詳細版）"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ このコマンドは管理者のみ実行可能です。")
            return
        try:
            await ctx.send('💾 詳細バックアップを実行中...')
            backup_path = backup_config_and_data()
            if backup_path:
                # バックアップの整合性を検証
                is_valid, message = verify_backup(backup_path)
                if is_valid:
                    await ctx.send(f'✅ バックアップ完了: {backup_path}\n{message}')
                else:
                    await ctx.send(f'⚠️ バックアップ完了: {backup_path}\n⚠️ 検証警告: {message}')
            else:
                await ctx.send('❌ バックアップに失敗しました。')
        except Exception as e:
            await ctx.send(f'❌ バックアップ中にエラーが発生しました: {e}')
            logging.error(f"バックアップエラー: {e}")

    @bot.command(name='memory')
    async def memory(ctx):
        """メモリ使用量を表示"""
        try:
            memory_usage = get_memory_usage()
            memory_msg = "💾 **メモリ使用量詳細**\n"
            memory_msg += f"プロセス使用量: {memory_usage['rss']:.1f}MB ({memory_usage['percent']:.1f}%)\n"
            memory_msg += f"仮想メモリ: {memory_usage['vms']:.1f}MB\n"
            memory_msg += f"システム空き容量: {memory_usage['available']:.1f}MB\n"
            memory_msg += f"システム総容量: {memory_usage['total']:.1f}MB\n"
            
            # メモリクリーンアップボタン
            memory_msg += "\n🔄 メモリクリーンアップを実行するには `cleanup` コマンドを使用してください。"
            
            await ctx.send(memory_msg)
        except Exception as e:
            await ctx.send(f'❌ メモリ情報取得中にエラーが発生しました: {e}')
            logging.error(f"メモリ情報取得エラー: {e}")

    @bot.command(name='cleanup')
    async def cleanup(ctx):
        """メモリクリーンアップを実行"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ このコマンドは管理者のみ実行可能です。")
            return
        try:
            await ctx.send('🧹 メモリクリーンアップを実行中...')
            memory_usage = cleanup_memory()
            if memory_usage:
                cleanup_msg = "✅ **メモリクリーンアップ完了**\n"
                cleanup_msg += f"現在の使用量: {memory_usage['rss']:.1f}MB ({memory_usage['percent']:.1f}%)"
                await ctx.send(cleanup_msg)
            else:
                await ctx.send('❌ メモリクリーンアップに失敗しました。')
        except Exception as e:
            await ctx.send(f'❌ メモリクリーンアップ中にエラーが発生しました: {e}')
            logging.error(f"メモリクリーンアップエラー: {e}")

    @bot.command(name='reload')
    async def reload_config_cmd(ctx):
        """設定を再読み込み"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ このコマンドは管理者のみ実行可能です。")
            return
        try:
            await ctx.send('🔄 設定を再読み込み中...')
            if reload_config():
                await ctx.send('✅ 設定の再読み込みが完了しました。')
            else:
                await ctx.send('❌ 設定の再読み込みに失敗しました。')
        except Exception as e:
            await ctx.send(f'❌ 設定再読み込み中にエラーが発生しました: {e}')
            logging.error(f"設定再読み込みエラー: {e}")

    @bot.command(name='performance')
    async def performance(ctx, days_offset: str = None):
        """取引パフォーマンスレポート（引数なし：今日、-1：前日、-2：2日前など）"""
        try:
            # 引数の解析
            days_offset_int = None
            if days_offset:
                try:
                    days_offset_int = int(days_offset)
                    if days_offset_int > 0:
                        await ctx.send(f"❌ 正の数は使用できません。負の数（例：-1、-2）または引数なしで今日の統計を表示してください。")
                        return
                except ValueError:
                    await ctx.send(f"❌ 無効な引数です。数字（例：-1、-2）または引数なしで今日の統計を表示してください。")
                    return
            
            # メッセージの生成
            if days_offset_int is not None:
                if days_offset_int == -1:
                    await ctx.send('📅 前日のパフォーマンスレポートを生成中...')
                elif days_offset_int == -2:
                    await ctx.send('📅 2日前のパフォーマンスレポートを生成中...')
                else:
                    await ctx.send(f'📅 {abs(days_offset_int)}日前のパフォーマンスレポートを生成中...')
                report = get_performance_report(use_today_only=False, days_offset=days_offset_int)
            else:
                await ctx.send('📅 今日のパフォーマンスレポートを生成中...')
                report = get_performance_report(use_today_only=True)
            
            # 長いレポートは分割
            if len(report) > 2000:
                if days_offset_int is not None:
                    if days_offset_int == -1:
                        title = "前日のパフォーマンスレポート"
                    elif days_offset_int == -2:
                        title = "2日前のパフォーマンスレポート"
                    else:
                        title = f"{abs(days_offset_int)}日前のパフォーマンスレポート"
                else:
                    title = "今日のパフォーマンスレポート"
                
                chunks = [report[i:i+1900] for i in range(0, len(report), 1900)]
                for i, chunk in enumerate(chunks):
                    await ctx.send(f"{title} (Part {i+1}/{len(chunks)}):\n{chunk}")
            else:
                await ctx.send(report)
        except Exception as e:
            error_msg = f'❌ パフォーマンスレポート取得エラー: {e}'
            await ctx.send(error_msg)
            logging.error(f"パフォーマンスレポート取得エラー: {e}")

    @bot.command(name='all')
    async def all(ctx):
        """全情報を表示"""
        try:
            # ステータス
            status_info = get_system_status()
            # ポジション
            positions = get_all_positions()
            # パフォーマンス
            report = get_performance_report(use_today_only=True)
            
            all_msg = "📋 **全情報サマリー**\n\n"
            
            # ステータス
            all_msg += "📈 **システムステータス**\n"
            all_msg += f"稼働時間: {status_info['uptime']}\n"
            all_msg += f"メモリ使用量: {status_info['memory_usage']:.1f}MB\n"
            all_msg += f"API呼び出し: {status_info['api_calls']}回\n"
            all_msg += f"システム状態: {'✅ 正常' if status_info['overall_health'] else '⚠️ 注意'}\n\n"
            
            # ポジション
            all_msg += "📊 **現在のポジション**\n"
            if positions:
                for pos in positions:
                    all_msg += f"{pos.symbol} {pos.side} {pos.size}lot\n"
            else:
                all_msg += "ポジションなし\n"
            all_msg += "\n"
            
            # パフォーマンス
            if report:
                all_msg += "📈 **今日のパフォーマンス**\n"
                all_msg += f"取引回数: {report['total_trades']}回\n"
                all_msg += f"勝率: {report['win_rate']:.1f}%\n"
                all_msg += f"損益: {report['total_pnl']:.2f}円\n"
            
            if len(all_msg) > 2000:
                chunks = [all_msg[i:i+1900] for i in range(0, len(all_msg), 1900)]
                for i, chunk in enumerate(chunks):
                    await ctx.send(f"全情報 (Part {i+1}/{len(chunks)}):\n{chunk}")
            else:
                await ctx.send(all_msg)
                
        except Exception as e:
            await ctx.send(f'❌ 全情報取得中にエラーが発生しました: {e}')
            logging.error(f"全情報取得エラー: {e}")

    @bot.command(name='testlot')
    async def testlot(ctx, symbol: str = "USD_JPY", side: str = "BUY"):
        """ロット計算テスト"""
        try:
            await ctx.send(f'🧮 {symbol} {side} のロット計算テストを実行中...')
            
            # 残高取得
            balance_data = broker.get_balance()
            if not balance_data or 'data' not in balance_data:
                await ctx.send('❌ 残高取得に失敗しました。')
                return
            
            balance = float(balance_data['data'][0]['balance'])
            
            # ロット計算
            lot_size = calc_auto_lot_gmobot2(balance, symbol, side, LEVERAGE)
            
            # 結果表示
            result_msg = f"🧮 **ロット計算結果**\n"
            result_msg += f"通貨ペア: {symbol}\n"
            result_msg += f"売買方向: {side}\n"
            result_msg += f"証拠金: {balance:,.0f}円\n"
            result_msg += f"レバレッジ: {LEVERAGE}倍\n"
            result_msg += f"計算ロット: {lot_size}lot"
            
            await ctx.send(result_msg)
            
        except Exception as e:
            await ctx.send(f'❌ ロット計算テスト中にエラーが発生しました: {e}')
            logging.error(f"ロット計算テストエラー: {e}")

    @bot.command(name='debuglot')
    async def debuglot(ctx, symbol: str = "USD_JPY", side: str = "BUY"):
        """ロット計算デバッグ"""
        try:
            await ctx.send(f'🔍 {symbol} {side} のロット計算デバッグを実行中...')
            
            # 詳細なロット計算デバッグ
            debug_result = test_auto_lot_debug()
            
            if debug_result:
                debug_msg = "🔍 **ロット計算デバッグ結果**\n"
                for key, value in debug_result.items():
                    debug_msg += f"{key}: {value}\n"
                
                if len(debug_msg) > 2000:
                    chunks = [debug_msg[i:i+1900] for i in range(0, len(debug_msg), 1900)]
                    for i, chunk in enumerate(chunks):
                        await ctx.send(f"デバッグ結果 (Part {i+1}/{len(chunks)}):\n{chunk}")
                else:
                    await ctx.send(debug_msg)
            else:
                await ctx.send('❌ デバッグ情報の取得に失敗しました。')
                
        except Exception as e:
            await ctx.send(f'❌ ロット計算デバッグ中にエラーが発生しました: {e}')
            logging.error(f"ロット計算デバッグエラー: {e}")

    @bot.command(name='schedule')
    async def show_schedule(ctx):
        """trades.csvのエントリー一覧を表示"""
        try:
            schedule_display = get_trades_schedule_for_display()
            
            # Discordのメッセージ制限（2000文字）を考慮して分割送信
            if len(schedule_display) > 1900:
                chunks = [schedule_display[i:i+1900] for i in range(0, len(schedule_display), 1900)]
                for i, chunk in enumerate(chunks):
                    await ctx.send(f"取引スケジュール (Part {i+1}/{len(chunks)}):\n{chunk}")
            else:
                await ctx.send(schedule_display)
                
        except Exception as e:
            await ctx.send(f'❌ スケジュール取得中にエラーが発生しました: {e}')
            logging.error(f"スケジュール表示エラー: {e}")

    @bot.command(name='command')
    async def command_list(ctx):
        """コマンド一覧を表示（詳細版）"""
        commands_msg = "📋 **利用可能なコマンド（詳細版）**\n\n"
        commands_msg += "**基本コマンド**\n"
        commands_msg += "`kill` - 全ポジションを即座に決済（緊急時）\n"
        commands_msg += "`stop` - ボットを停止\n"
        commands_msg += "`restart` - ボットを再起動\n"
        commands_msg += "`position` - 現在のポジションを表示\n"
        commands_msg += "`status` - システムステータスを表示（詳細版）\n"
        commands_msg += "`health` - ヘルスチェックを実行（詳細版）\n"
        commands_msg += "`performance [日数]` - パフォーマンスレポートを表示\n"
        commands_msg += "`all` - 全情報を表示\n"
        commands_msg += "`schedule` - 取引スケジュールを表示\n\n"
        commands_msg += "**管理コマンド**\n"
        commands_msg += "`backup` - 手動バックアップを実行（詳細版）\n"
        commands_msg += "`memory` - メモリ使用量を表示\n"
        commands_msg += "`cleanup` - メモリクリーンアップを実行\n"
        commands_msg += "`reload` - 設定を再読み込み\n"
        commands_msg += "`testlot [通貨ペア] [売買方向]` - ロット計算テスト\n"
        commands_msg += "`debuglot [通貨ペア] [売買方向]` - ロット計算デバッグ\n"
        commands_msg += "`command` - このコマンド一覧を表示\n\n"
        commands_msg += "**新機能**\n"
        commands_msg += "• 詳細な自動ロット計算（通貨ペア別）\n"
        commands_msg += "• API手数料管理と追跡\n"
        commands_msg += "• 銘柄別取引数量制限\n"
        commands_msg += "• 詳細なスプレッド管理\n"
        commands_msg += "• 重複建玉防止機能\n"
        commands_msg += "• 未認識ポジション処理\n"
        commands_msg += "• 詳細なログ管理（ローテーション付き）\n"
        commands_msg += "• 詳細なメモリ管理とクリーンアップ\n"
        commands_msg += "• 動的レート制限調整\n"
        commands_msg += "• 詳細なバックアップと整合性チェック\n"
        commands_msg += "• 詳細なヘルスチェック\n\n"
        commands_msg += "**例**\n"
        commands_msg += "`performance 7` - 過去7日間のパフォーマンス\n"
        commands_msg += "`testlot EUR_JPY SELL` - EUR/JPY売りのロット計算テスト\n"
        commands_msg += "`memory` - メモリ使用量の詳細表示"
        
        await ctx.send(commands_msg)

    def run_bot():
        """Discord Botを実行"""
        try:
            bot.run(DISCORD_BOT_TOKEN)
        except Exception as e:
            logging.error(f"Discord Bot実行エラー: {e}")
            send_discord_message(f"Discord Bot実行エラー: {e}")

# ===============================
# システム管理・分析関数
# ===============================
def get_all_positions():
    """全ポジションを取得"""
    try:
        # OANDAレート制限チェック
        oanda_rate_limit()
        
        r = positions.OpenPositions(OANDA_ACCOUNT_ID)
        resp = oanda_api.request(r)
        
        positions_list = []
        for p in resp["positions"]:
            for side in ["long", "short"]:
                units = float(p[side]["units"])
                if abs(units) > 0:
                    positions_list.append({
                        "symbol": p["instrument"],
                        "side": "BUY" if side == "long" else "SELL",
                        "positionId": f"{p['instrument']}-{side}",
                        "size": abs(units),
                        "price": float(p[side]["averagePrice"])
                    })
        return positions_list
    except Exception as e:
        logging.error(f"全ポジション取得エラー: {e}")
        return []

def close_position_by_info(position, exit_time, auto_closed=False, trade_index=None):
    """
    ポジション情報から決済注文を発行し、損益を記録・通知
    """
    global trade_results, total_api_fee
    exit_side = "SELL" if position.side == "BUY" else "BUY"
    # 決済時jitterのsleepはprocess_trades側で行うため、ここでは不要
    average_exit_price = broker.close_position(
        position.symbol, position.position_id, position.size, exit_side
    )
    profit_pips = calculate_profit_pips(
        float(position.price), average_exit_price, position.side, position.symbol
    )
    profit_amount = calculate_profit_amount(
        float(position.price), average_exit_price, position.side, position.symbol, position.size
    )
    trade_results.append({
        "symbol": position.symbol,
        "side": position.side,
        "entry_price": float(position.price),
        "exit_price": average_exit_price,
        "profit_pips": profit_pips,
        "profit_amount": profit_amount,
        "lot_size": position.size,
        "entry_time": getattr(position, 'entry_time', datetime.now().strftime('%H:%M:%S')),
        "exit_time": datetime.now().strftime('%H:%M:%S'),
        "entry_date": getattr(position, 'entry_date', datetime.now().date()),
        "exit_date": datetime.now().date(),
    })
    close_type = "自動決済" if auto_closed else "予定決済"
    # 証拠金残高取得
    balance_data = broker.get_balance()
    data = balance_data.get('data')
    if isinstance(data, list) and len(data) > 0:
        balance_amount = float(data[0].get('balance', 0))
    elif isinstance(data, dict):
        balance_amount = float(data.get('balance', 0))
    else:
        balance_amount = 0
    send_discord_message(
        f"{close_type}しました: 通貨ペア={position.symbol}, 売買方向={position.side}, "
        f"エントリー価格={position.price}, 決済価格={average_exit_price}, 損益pips={profit_pips} ({profit_amount}円), ロット数={position.size} "
        f"(決済時間: {datetime.now().strftime('%H:%M:%S')})\n"
        f"現在の証拠金残高: {balance_amount}円"
    )
    print(f"【決済完了】{close_type}: {position.symbol} {position.side} {position.price}→{average_exit_price} {profit_pips}pips ({profit_amount}円) ロット数:{position.size}")
    return profit_pips

def schedule_position_check(symbol, expected_close_time):
    """
    エントリー失敗後も定期的にポジションを確認し、あれば決済する
    """
    end_time = expected_close_time + timedelta(minutes=10)
    while datetime.now() < end_time:
        positions = check_current_positions(symbol)
        if positions:
            logging.warning(f"未認識のポジションが見つかりました。決済を実行します: {positions}")
            for position in positions:
                exit_side = "SELL" if position.side == "BUY" else "BUY"
                try:
                    broker.close_position(position.symbol, position.position_id, position.size, exit_side)
                    send_discord_message(f"⚠️ 未認識ポジションを検出し決済しました: {position.symbol} {position.side}")
                except Exception as e:
                    logging.error(f"未認識ポジション決済中のエラー: {e}")
            return True
        # 次の確認まで待機
        time.sleep(POSITION_CHECK_INTERVAL)
    return False

def health_check():
    """システムヘルスチェック（詳細版）"""
    try:
        health_status = {
            'api_connection': False,
            'discord_connection': False,
            'memory_usage': False,
            'disk_space': False,
            'file_access': False,
            'overall_health': False
        }
        
        # API接続チェック
        try:
            balance_data = broker.get_balance()
            if balance_data and 'data' in balance_data:
                health_status['api_connection'] = True
                logging.info("API接続: 正常")
            else:
                logging.warning("API接続: 異常")
        except Exception as e:
            logging.error(f"API接続チェックエラー: {e}")
        
        # Discord接続チェック
        try:
            if DISCORD_WEBHOOK_URL:
                # テストメッセージを送信（実際には送信しない）
                health_status['discord_connection'] = True
                logging.info("Discord接続: 正常")
            else:
                logging.warning("Discord接続: Webhook URL未設定")
        except Exception as e:
            logging.error(f"Discord接続チェックエラー: {e}")
        
        # メモリ使用量チェック
        try:
            memory_usage = get_memory_usage()
            if memory_usage['rss'] < 500:  # 500MB以下
                health_status['memory_usage'] = True
                logging.info(f"メモリ使用量: 正常 ({memory_usage['rss']:.1f}MB)")
            else:
                logging.warning(f"メモリ使用量: 高すぎる ({memory_usage['rss']:.1f}MB)")
        except Exception as e:
            logging.error(f"メモリ使用量チェックエラー: {e}")
        
        # ディスク容量チェック
        try:
            disk_usage = psutil.disk_usage('.')
            free_gb = disk_usage.free / (1024**3)
            if free_gb > 1:  # 1GB以上
                health_status['disk_space'] = True
                logging.info(f"ディスク容量: 正常 ({free_gb:.1f}GB 空き)")
            else:
                logging.warning(f"ディスク容量: 不足 ({free_gb:.1f}GB 空き)")
        except Exception as e:
            logging.error(f"ディスク容量チェックエラー: {e}")
        
        # ファイルアクセスチェック
        try:
            # 重要なファイルのアクセス権限をチェック
            important_files = [CONFIG_FILE, SCHEDULE_CSV]
            for file_path in important_files:
                if os.path.exists(file_path):
                    # 読み取り権限チェック
                    if not os.access(file_path, os.R_OK):
                        raise Exception(f"ファイル読み取り権限なし: {file_path}")
                else:
                    logging.warning(f"ファイルが存在しません: {file_path}")
            
            # ログディレクトリの書き込み権限チェック
            if not os.access('logs', os.W_OK):
                raise Exception("ログディレクトリの書き込み権限なし")
            
            health_status['file_access'] = True
            logging.info("ファイルアクセス: 正常")
        except Exception as e:
            logging.error(f"ファイルアクセスチェックエラー: {e}")
        
        # 総合判定
        health_status['overall_health'] = all([
            health_status['api_connection'],
            health_status['discord_connection'],
            health_status['memory_usage'],
            health_status['disk_space'],
            health_status['file_access']
        ])
        
        # ヘルスチェック結果をログ出力
        if health_status['overall_health']:
            logging.info("ヘルスチェック: 全項目正常")
        else:
            failed_items = [k for k, v in health_status.items() if not v and k != 'overall_health']
            logging.warning(f"ヘルスチェック: 異常項目あり - {failed_items}")
        
        return health_status
    except Exception as e:
        logging.error(f"ヘルスチェックエラー: {e}")
        return {'overall_health': False}

def get_system_status():
    """システムステータスを取得（詳細版）"""
    try:
        # 稼働時間計算
        uptime_seconds = (datetime.now() - performance_metrics['start_time']).total_seconds()
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{hours}時間{minutes}分"
        
        # メモリ使用量
        memory_usage = get_memory_usage()
        
        # ディスク使用量
        disk_usage = psutil.disk_usage('.')
        disk_free_gb = disk_usage.free / (1024**3)
        
        # 今日の取引数
        today_trades = len([t for t in trade_results if t.get('exit_date') == datetime.now().date()])
        
        # 今日の損益
        today_pnl = sum([t.get('profit_amount', 0) for t in trade_results if t.get('exit_date') == datetime.now().date()])
        
        # レート制限状態
        rate_limit_status = get_oanda_rate_limit_status()
        
        # ヘルスチェック
        health_status = health_check()
        
        return {
            'uptime': uptime_str,
            'memory_usage': memory_usage['rss'],
            'memory_percent': memory_usage['percent'],
            'disk_free_gb': disk_free_gb,
            'api_calls': performance_metrics['api_calls'],
            'api_errors': performance_metrics['api_errors'],
            'rate_limit': rate_limit_status.get('max_requests_per_minute'),
            'rate_limit_errors': performance_metrics['api_errors'],
            'today_trades': today_trades,
            'today_pnl': today_pnl,
            'total_api_fee': total_api_fee,
            'overall_health': health_status['overall_health'],
            'health_details': health_status
        }
    except Exception as e:
        logging.error(f"システムステータス取得エラー: {e}")
        return {}

def get_performance_report(use_today_only=False, days_offset=None):
    """パフォーマンスレポートを生成"""
    try:
        # collect_metrics関数を使用して詳細なメトリクスを取得
        metrics = collect_metrics(use_today_only, days_offset)
        
        if not metrics:
            return "📊 指定期間の取引データがありません。"
        
        # 詳細なレポートを生成
        report = f"📊 **{metrics['period']}のパフォーマンスレポート**\n\n"
        report += f"**基本統計**\n"
        report += f"取引回数: {metrics['total_trades']}回\n"
        report += f"勝率: {metrics['win_rate']:.1f}%\n"
        report += f"勝ち取引: {metrics['winning_trades']}回\n"
        report += f"負け取引: {metrics['losing_trades']}回\n\n"
        
        report += f"**損益統計**\n"
        report += f"総損益pips: {metrics['total_profit_pips']:.1f}\n"
        report += f"総損益金額: {metrics['total_profit_amount']:.0f}円\n"
        report += f"平均損益pips: {metrics['average_profit_pips']:.1f}\n"
        report += f"平均損益金額: {metrics['average_profit_amount']:.0f}円\n\n"
        
        report += f"**最大値・最小値**\n"
        report += f"最大利益: {metrics['max_profit']:.0f}円\n"
        report += f"最大損失: {metrics['max_loss']:.0f}円\n"
        report += f"最大ドローダウン: {metrics['max_drawdown_amount']:.0f}円\n\n"
        
        report += f"**パフォーマンス指標**\n"
        report += f"シャープレシオ: {metrics['sharpe_ratio']:.2f}\n"
        report += f"API呼び出し回数: {metrics['api_calls']}回\n"
        report += f"APIエラー回数: {metrics['api_errors']}回\n"
        report += f"累計API手数料: {metrics['total_api_fee']:.0f}円"
        
        return report
        
    except Exception as e:
        logging.error(f"パフォーマンスレポート生成エラー: {e}")
        return f"❌ パフォーマンスレポート生成エラー: {e}"

def backup_config_and_data():
    """設定とデータのバックアップ（詳細版）"""
    try:
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"backup_{timestamp}")
        os.makedirs(backup_path)
        
        # 設定ファイルのバックアップ
        if os.path.exists(CONFIG_FILE):
            import shutil
            shutil.copy2(CONFIG_FILE, os.path.join(backup_path, "config.json"))
            logging.info(f"設定ファイルをバックアップ: {CONFIG_FILE}")
        
        # 取引結果のバックアップ
        if trade_results:
            with open(os.path.join(backup_path, "trade_results.json"), 'w', encoding='utf-8') as f:
                json.dump(trade_results, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"取引結果をバックアップ: {len(trade_results)}件")
        
        # 手数料履歴のバックアップ
        if fee_records:
            with open(os.path.join(backup_path, "fee_records.json"), 'w', encoding='utf-8') as f:
                json.dump(fee_records, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"手数料履歴をバックアップ: {len(fee_records)}件")
        
        # 銘柄別取引数量のバックアップ
        if symbol_daily_volume:
            with open(os.path.join(backup_path, "symbol_daily_volume.json"), 'w', encoding='utf-8') as f:
                json.dump(symbol_daily_volume, f, indent=2, ensure_ascii=False)
            logging.info(f"銘柄別取引数量をバックアップ: {len(symbol_daily_volume)}銘柄")
        
        # ログファイルのバックアップ
        if os.path.exists('logs'):
            import shutil
            shutil.copytree('logs', os.path.join(backup_path, "logs"))
            logging.info("ログファイルをバックアップ")
        
        # 取引スケジュールファイルのバックアップ
        if os.path.exists(SCHEDULE_CSV):
            import shutil
            shutil.copy2(SCHEDULE_CSV, os.path.join(backup_path, "trades.csv"))
            logging.info(f"取引スケジュールをバックアップ: {SCHEDULE_CSV}")
        
        # バックアップの整合性チェック
        backup_size = sum(os.path.getsize(os.path.join(backup_path, f)) for f in os.listdir(backup_path) if os.path.isfile(os.path.join(backup_path, f)))
        logging.info(f"バックアップサイズ: {backup_size / 1024:.1f}KB")
        
        # 古いバックアップの自動削除
        cleanup_old_backups(backup_dir, days=30)
        
        logging.info(f"バックアップ完了: {backup_path}")
        return backup_path
    except Exception as e:
        logging.error(f"バックアップエラー: {e}")
        return None

def cleanup_old_backups(backup_dir, days=30):
    """古いバックアップを自動削除"""
    try:
        cutoff_date = datetime.now() - timedelta(days=days)
        deleted_count = 0
        
        for item in os.listdir(backup_dir):
            item_path = os.path.join(backup_dir, item)
            if os.path.isdir(item_path) and item.startswith("backup_"):
                try:
                    # バックアップディレクトリ名から日時を抽出
                    timestamp_str = item.replace("backup_", "")
                    backup_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                    
                    if backup_date < cutoff_date:
                        import shutil
                        shutil.rmtree(item_path)
                        logging.info(f"古いバックアップを削除: {item}")
                        deleted_count += 1
                except Exception as e:
                    logging.warning(f"バックアップ削除エラー ({item}): {e}")
        
        if deleted_count > 0:
            logging.info(f"{deleted_count}個の古いバックアップを削除しました")
            
    except Exception as e:
        logging.error(f"バックアップクリーンアップエラー: {e}")

def verify_backup(backup_path):
    """バックアップの整合性を検証"""
    try:
        if not os.path.exists(backup_path):
            return False, "バックアップディレクトリが存在しません"
        
        # 必須ファイルの存在チェック
        required_files = ["config.json"]
        for file in required_files:
            if not os.path.exists(os.path.join(backup_path, file)):
                return False, f"必須ファイルが存在しません: {file}"
        
        # ファイルサイズチェック
        total_size = 0
        for root, dirs, files in os.walk(backup_path):
            for file in files:
                file_path = os.path.join(root, file)
                total_size += os.path.getsize(file_path)
        
        if total_size == 0:
            return False, "バックアップサイズが0です"
        
        return True, f"バックアップ検証成功: {total_size / 1024:.1f}KB"
        
    except Exception as e:
        return False, f"バックアップ検証エラー: {e}"

def test_auto_lot_debug():
    """自動ロット計算のデバッグ情報"""
    try:
        debug_info = {}
        
        # 残高取得
        balance_data = broker.get_balance()
        if balance_data and 'data' in balance_data:
            balance = float(balance_data['data'][0]['balance'])
            debug_info['balance'] = balance
        else:
            debug_info['balance'] = "取得失敗"
            return debug_info
        
        # レバレッジ
        debug_info['leverage'] = LEVERAGE
        
        # リスク設定
        debug_info['risk_ratio'] = config.get('risk_ratio', 0.02)
        
        # テスト用のロット計算
        test_symbols = ["USD_JPY", "EUR_JPY", "GBP_JPY"]
        for symbol in test_symbols:
            try:
                lot_size = calc_auto_lot_gmobot2(balance, symbol, "BUY", LEVERAGE)
                debug_info[f"{symbol}_BUY"] = lot_size
            except Exception as e:
                debug_info[f"{symbol}_BUY"] = f"エラー: {e}"
        
        return debug_info
    except Exception as e:
        logging.error(f"自動ロットデバッグエラー: {e}")
        return {"error": str(e)}

# ===============================
# 定期position監視機能（main.pyから統合）
# ===============================
def load_trades_schedule():
    """
    trades.csvからエントリー・決済時刻のリストを取得
    戻り値: [(entry_datetime, exit_datetime), ...]
    """
    schedule = []
    try:
        with open(SCHEDULE_CSV, mode='r', encoding='utf-8') as file:
            reader = csv.reader(file)
            header = next(reader)
            now = datetime.now()
            for row in reader:
                if len(row) >= 5 and row[3].strip() and row[4].strip():
                    try:
                        entry_time = datetime.strptime(row[3].strip(), '%H:%M:%S').replace(year=now.year, month=now.month, day=now.day)
                        exit_time = datetime.strptime(row[4].strip(), '%H:%M:%S').replace(year=now.year, month=now.month, day=now.day)
                        
                        # 日を跨ぐ取引の場合、現在時刻を考慮して日付を調整
                        if exit_time <= entry_time:
                            exit_time += timedelta(days=1)
                            # 現在時刻が0:00-6:00の範囲で、エントリー時刻も0:00-6:00の場合は前日として扱う
                            if (now.hour < 6 and entry_time.hour < 6):
                                entry_time -= timedelta(days=1)
                                exit_time -= timedelta(days=1)
                            # さらに、現在時刻が決済時刻を過ぎていない場合も前日として扱う
                            elif now.hour < exit_time.hour:
                                entry_time -= timedelta(days=1)
                                exit_time -= timedelta(days=1)
                        
                        schedule.append((entry_time, exit_time))
                    except Exception:
                        continue
    except Exception as e:
        logging.error(f"trades.csvスケジュール取得エラー: {e}")
    return schedule

def get_trades_schedule_for_display():
    """
    trades.csvからエントリー一覧を取得してDiscord表示用のメッセージを生成
    戻り値: Discord表示用のメッセージ文字列
    """
    try:
        with open(SCHEDULE_CSV, mode='r', encoding='utf-8') as file:
            reader = csv.reader(file)
            header = next(reader)
            
            schedule_msg = "**今日の取引スケジュール**\n"
            schedule_msg += f"ファイル: {SCHEDULE_CSV}\n\n"
            
            trade_count = 0
            for row in reader:
                if len(row) >= 5:
                    trade_number = row[0].strip() if row[0].strip() else f"{trade_count + 1}"
                    direction = row[1].strip() if len(row) > 1 and row[1].strip() else "未設定"
                    symbol = row[2].strip() if len(row) > 2 and row[2].strip() else "未設定"
                    entry_time = row[3].strip() if len(row) > 3 and row[3].strip() else "未設定"
                    exit_time = row[4].strip() if len(row) > 4 and row[4].strip() else "未設定"
                    lot_size = row[5].strip() if len(row) > 5 and row[5].strip() else "auto"
                    
                    # 方向を英語に変換
                    direction_eng = "long" if direction == "買" else "short" if direction == "売" else direction
                    
                    # 通貨ペアの形式を変更（/を_に）
                    symbol_display = symbol.replace("/", "_")
                    
                    # ロット表示
                    if lot_size == "" or lot_size == "自動":
                        lot_display = "auto"
                    else:
                        lot_display = lot_size
                    
                    schedule_msg += f"{trade_number},{symbol_display} Lot:{lot_display} {entry_time}-{exit_time}\n"
                    
                    trade_count += 1
            
            if trade_count == 0:
                schedule_msg += "取引スケジュールが見つかりませんでした。\n"
                schedule_msg += "trades.csvファイルを確認してください。"
            else:
                schedule_msg += f"\n**合計: {trade_count}件の取引スケジュール**"
            
            return schedule_msg
            
    except FileNotFoundError:
        return f"ファイルが見つかりません: {SCHEDULE_CSV}"
    except Exception as e:
        logging.error(f"trades.csv表示用データ取得エラー: {e}")
        return f"trades.csvの読み込みエラー: {e}"

def is_in_trades_schedule(now, schedule):
    """
    現在時刻がtrades.csvのいずれかのエントリー～決済時間内か判定
    """
    for entry, exit in schedule:
        if entry <= now <= exit:
            return True
    return False

def is_near_schedule_time(now, schedule, buffer_seconds=5):
    """
    現在時刻がエントリー時間または決済時間の前後buffer_seconds秒以内か判定
    エントリー直後や決済直前の監視を避けるため
    """
    for entry, exit in schedule:
        # エントリー時間の前後buffer_seconds秒
        entry_start = entry - timedelta(seconds=buffer_seconds)
        entry_end = entry + timedelta(seconds=buffer_seconds)
        
        # 決済時間の前後buffer_seconds秒
        exit_start = exit - timedelta(seconds=buffer_seconds)
        exit_end = exit + timedelta(seconds=buffer_seconds)
        
        # 現在時刻がエントリー時間または決済時間の前後buffer_seconds秒以内
        if (entry_start <= now <= entry_end) or (exit_start <= now <= exit_end):
            return True
    return False

def force_kill_all_positions_and_notify():
    """
    全ポジションを強制決済し、損益情報をdiscord通知
    """
    positions = broker.get_all_positions()
    if not positions:
        return
    total_pips = 0
    total_amount = 0
    msg = "🚨 強制決済（kill）を実行しました\n"
    for pos in positions:
        try:
            entry_price = float(pos.price)
            size = float(pos.size)
            symbol = pos.symbol
            side = pos.side
            # 現在価格取得
            tickers = broker.get_tickers([symbol])
            if not tickers or 'data' not in tickers:
                continue
            rate_data = None
            for item in tickers['data']:
                if item['symbol'] == symbol:
                    rate_data = item
                    break
            if not rate_data:
                continue
            current_price = float(rate_data['bid']) if side == 'BUY' else float(rate_data['ask'])
            # 損益計算
            profit_pips = calculate_profit_pips(entry_price, current_price, side, symbol)
            profit_amount = calculate_profit_amount(entry_price, current_price, side, symbol, size)
            total_pips += profit_pips
            total_amount += profit_amount
            # 決済
            exit_side = 'SELL' if side == 'BUY' else 'BUY'
            broker.close_position(symbol, pos.position_id, size, exit_side)
            msg += f"{symbol} {side} {size}lot: {profit_pips:.1f}pips, {profit_amount:.0f}円\n"
        except Exception as e:
            logging.error(f"強制決済エラー: {e}")
    msg += f"\n合計損益: {total_pips:.1f}pips, {total_amount:.0f}円"
    send_discord_message(msg)

def periodic_position_check():
    """
    指定分ごとにposition監視。trades.csvの時間外でポジションがあればkill＆discord通知。
    エントリー時間と決済時間の前後5秒は監視を避ける。
    """
    def loop():
        while True:
            try:
                now = datetime.now()
                schedule = load_trades_schedule()
                positions = broker.get_all_positions()
                
                # エントリー時間または決済時間の前後5秒以内の場合は監視をスキップ
                if is_near_schedule_time(now, schedule, buffer_seconds=5):
                    logging.info(f"定期ポジション監視: スケジュール時間前後5秒のため監視をスキップ - {now.strftime('%H:%M:%S')}")
                    time.sleep(POSITION_CHECK_INTERVAL_MINUTES * 60)
                    continue
                
                # trades.csvの時間外でポジションが存在する場合のみkill
                if positions and not is_in_trades_schedule(now, schedule):
                    logging.warning(f"定期ポジション監視: スケジュール時間外のポジションを検出 - {now.strftime('%H:%M:%S')}")
                    force_kill_all_positions_and_notify()
                # 通常監視時はdiscord通知しない
            except Exception as e:
                logging.error(f"定期ポジション監視エラー: {e}")
            time.sleep(POSITION_CHECK_INTERVAL_MINUTES * 60)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

def monitor_and_close_positions(positions_to_monitor):
    """
    保有ポジションを監視し、ストップロス・テイクプロフィット条件で自動決済
    """
    if not positions_to_monitor:
        return
    
    try:
        # 監視対象の通貨ペアを重複排除して取得
        symbols = list(set(pos.symbol for pos in positions_to_monitor))
        
        # 最新のティッカー情報を一括取得
        tickers_data = broker.get_tickers(symbols)
        
        if not tickers_data or 'data' not in tickers_data:
            logging.error("ティッカー情報の取得に失敗しました")
            return
        
        # 価格データの型変換を強化（文字列→float）
        current_prices = {}
        for t in tickers_data['data']:
            try:
                current_prices[t['symbol']] = {
                    'bid': float(t['bid']),
                    'ask': float(t['ask'])
                }
            except (ValueError, KeyError) as e:
                logging.error(f"価格データ変換エラー ({t.get('symbol', 'unknown')}): {e}")
                continue
        
        # ポジションごとに損益計算と決済判定
        positions_to_remove = []  # 削除対象を記録
        for position in positions_to_monitor:
            symbol = position.symbol
            if symbol not in current_prices:
                continue
            
            try:
                # ポジション情報の型変換を強化
                entry_price = float(position.price)
                side = position.side
                current_price = current_prices[symbol]
                
                # 含み損益計算
                profit_pips = calculate_current_profit_pips(
                    entry_price, 
                    current_price, 
                    side, 
                    symbol
                )
                
                # ストップロス判定
                if STOP_LOSS_PIPS and profit_pips <= -STOP_LOSS_PIPS:
                    send_discord_message(
                        f"{symbol} {side} ポジションがストップロス条件に達しました: {profit_pips:.1f} pips"
                    )
                    close_position_by_info(position, datetime.now(), auto_closed=True)
                    positions_to_remove.append(position)
                
                # テイクプロフィット判定
                elif TAKE_PROFIT_PIPS and profit_pips >= TAKE_PROFIT_PIPS:
                    send_discord_message(
                        f"{symbol} {side} ポジションがテイクプロフィット条件に達しました: {profit_pips:.1f} pips"
                    )
                    close_position_by_info(position, datetime.now(), auto_closed=True)
                    positions_to_remove.append(position)
                    
            except KeyError as e:
                logging.error(f"ポジション情報のキー不足エラー: {e}")
                continue
            except ValueError as e:
                logging.error(f"数値変換エラー ({symbol}): {e}")
                continue
        
        # 削除対象のポジションを一括削除（スレッドセーフ）
        for position in positions_to_remove:
            try:
                positions_to_monitor.remove(position)
            except ValueError:
                # 既に削除されている場合は無視
                pass
                
    except Exception as e:
        logging.error(f"ポジション監視処理全体のエラー: {e}")
        send_discord_message(f"⚠️ ポジション監視システムエラー: {str(e)}")

def close_position_by_info(position, exit_time, auto_closed=False, trade_index=None):
    """
    ポジション情報から決済注文を発行し、損益を記録・通知
    """
    global trade_results, total_api_fee
    exit_side = "SELL" if position.side == "BUY" else "BUY"
    # 決済時jitterのsleepはprocess_trades側で行うため、ここでは不要
    average_exit_price = broker.close_position(
        position.symbol, position.position_id, position.size, exit_side
    )
    profit_pips = calculate_profit_pips(
        float(position.price), average_exit_price, position.side, position.symbol
    )
    profit_amount = calculate_profit_amount(
        float(position.price), average_exit_price, position.side, position.symbol, position.size
    )
    trade_results.append({
        "symbol": position.symbol,
        "side": position.side,
        "entry_price": float(position.price),
        "exit_price": average_exit_price,
        "profit_pips": profit_pips,
        "profit_amount": profit_amount,
        "lot_size": position.size,
        "entry_time": getattr(position, 'entry_time', datetime.now().strftime('%H:%M:%S')),
        "exit_time": datetime.now().strftime('%H:%M:%S'),
        "entry_date": getattr(position, 'entry_date', datetime.now().date()),
        "exit_date": datetime.now().date(),
    })
    close_type = "自動決済" if auto_closed else "予定決済"
    # 証拠金残高取得
    balance_data = broker.get_balance()
    data = balance_data.get('data')
    if isinstance(data, list) and len(data) > 0:
        balance_amount = float(data[0].get('balance', 0))
    elif isinstance(data, dict):
        balance_amount = float(data.get('balance', 0))
    else:
        balance_amount = 0
    send_discord_message(
        f"{close_type}しました: 通貨ペア={position.symbol}, 売買方向={position.side}, "
        f"エントリー価格={position.price}, 決済価格={average_exit_price}, 損益pips={profit_pips} ({profit_amount}円), ロット数={position.size} "
        f"(決済時間: {datetime.now().strftime('%H:%M:%S')})\n"
        f"現在の証拠金残高: {balance_amount}円"
    )
    print(f"【決済完了】{close_type}: {position.symbol} {position.side} {position.price}→{average_exit_price} {profit_pips}pips ({profit_amount}円) ロット数:{position.size}")
    return profit_pips

def schedule_position_check(symbol, expected_close_time):
    """
    エントリー失敗後も定期的にポジションを確認し、あれば決済する
    """
    end_time = expected_close_time + timedelta(minutes=10)
    while datetime.now() < end_time:
        positions = check_current_positions(symbol)
        if positions:
            logging.warning(f"未認識のポジションが見つかりました。決済を実行します: {positions}")
            for position in positions:
                exit_side = "SELL" if position.side == "BUY" else "BUY"
                try:
                    broker.close_position(position.symbol, position.position_id, position.size, exit_side)
                    send_discord_message(f"⚠️ 未認識ポジションを検出し決済しました: {position.symbol} {position.side}")
                except Exception as e:
                    logging.error(f"未認識ポジション決済中のエラー: {e}")
            return True
        # 次の確認まで待機
        time.sleep(POSITION_CHECK_INTERVAL)
    return False

# ===============================
# メイン関数
# ===============================
def main():
    """メイン関数（新機能統合版）"""
    try:
        # 初期化時のメモリチェック
        logging.info("=== システム初期化開始 ===")
        initial_memory = check_memory_usage()
        logging.info(f"初期メモリ使用量: {initial_memory['rss']:.1f}MB ({initial_memory['percent']:.1f}%)")
        
        # 初期ヘルスチェック
        logging.info("初期ヘルスチェックを実行中...")
        initial_health = health_check()
        if not initial_health['overall_health']:
            failed_items = [k for k, v in initial_health.items() if not v and k != 'overall_health']
            logging.warning(f"初期ヘルスチェックで異常を検出: {failed_items}")
            send_discord_message(f"⚠️ 初期ヘルスチェックで異常を検出: {failed_items}")
        else:
            logging.info("初期ヘルスチェック: 全項目正常")
        
        # 取引スケジュールを初期化
        schedule = initialize_trading_schedule()
        if schedule is None:
            logging.error("取引スケジュールの初期化に失敗しました")
            sys.exit(1)
        
        # 定期position監視を開始
        periodic_position_check()
        
        # 自動再起動スケジューラーを起動
        auto_restart_scheduler()
        
        # 取引数量リセットスケジューラーを起動
        daily_volume_reset_scheduler()
        
        # 初回バックアップ実行
        try:
            backup_path = backup_config_and_data()
            if backup_path:
                logging.info(f"初回バックアップ完了: {backup_path}")
                send_discord_message(f"初回バックアップ完了: {backup_path}")
        except Exception as e:
            logging.error(f"初回バックアップエラー: {e}")
        
        # 自動再起動設定の通知
        if AUTO_RESTART_HOUR is not None:
            send_discord_message(f"自動取引システムを開始しました。毎日継続実行します。\n🔄 自動再起動設定: 毎日{AUTO_RESTART_HOUR}時に再起動")
        else:
            send_discord_message("自動取引システムを開始しました。毎日継続実行します。\n🔄 自動再起動: 無効（連続運転）")
        
        # システム情報を通知
        system_info = get_system_status()
        if system_info:
            info_msg = f"📊 **システム情報**\n"
            info_msg += f"メモリ使用量: {system_info['memory_usage']:.1f}MB ({system_info['memory_percent']:.1f}%)\n"
            info_msg += f"ディスク空き容量: {system_info['disk_free_gb']:.1f}GB\n"
            info_msg += f"レートリミット: {system_info['rate_limit']}回/秒\n"
            info_msg += f"システム状態: {'✅ 正常' if system_info['overall_health'] else '⚠️ 注意'}"
            send_discord_message(info_msg)
        
        # trades.csvのエントリー一覧を通知
        schedule_display = get_trades_schedule_for_display()
        send_discord_message(schedule_display)
        
        # Discord Botが有効な場合は別スレッドで起動
        if DISCORD_BOT_TOKEN:
            bot_thread = threading.Thread(target=run_bot, daemon=True)
            bot_thread.start()
            logging.info("Discord Botを起動しました")
        
        # メインループを開始
        main_loop(schedule)
        
    except Exception as e:
        logging.error(f"アプリケーション起動エラー: {e}")
        send_discord_message(f"❌ アプリケーション起動エラー: {e}")
        sys.exit(1)

# ===============================
# パフォーマンス分析・自動再起動機能（main.pyから統合）
# ===============================
def get_today_trades():
    """当日（0:00から現在まで）の取引を取得"""
    today = datetime.now().date()
    today_trades = []
    
    for trade in trade_results:
        try:
            # exit_dateから日付を抽出
            if 'exit_date' in trade:
                trade_date = trade['exit_date']
                if isinstance(trade_date, str):
                    trade_date = datetime.strptime(trade_date, '%Y-%m-%d').date()
                elif isinstance(trade_date, datetime):
                    trade_date = trade_date.date()
                
                # 今日の取引かチェック
                if trade_date == today:
                    today_trades.append(trade)
            else:
                # 後方互換性のため、exit_timeから推定（ただし正確ではない）
                if 'exit_time' in trade and trade['exit_time']:
                    logging.warning(f"取引データに日付情報がありません: {trade}")
        except Exception as e:
            logging.warning(f"取引日付解析エラー: {e}, trade: {trade}")
            continue
    
    return today_trades

def get_trades_by_date_offset(days_offset):
    """指定日数前の取引を取得（例：-1で前日、-2で2日前）"""
    target_date = datetime.now().date() + timedelta(days=days_offset)
    target_trades = []
    
    for trade in trade_results:
        try:
            # exit_timeから日付を抽出
            if 'exit_time' in trade and trade['exit_time']:
                # exit_timeの形式: 'HH:MM:SS'
                exit_time_str = trade['exit_time']
                
                # 取引データに日付情報があるかチェック
                if 'exit_date' in trade:
                    # 日付情報がある場合はそれを使用
                    trade_date = trade['exit_date']
                    if isinstance(trade_date, str):
                        trade_date = datetime.strptime(trade_date, '%Y-%m-%d').date()
                    elif isinstance(trade_date, datetime):
                        trade_date = trade_date.date()
                else:
                    # 日付情報がない場合は、現在の日付を基準に推定
                    # ただし、これは正確ではないため、ログに警告を出力
                    logging.warning(f"取引データに日付情報がありません: {trade}")
                    continue
                
                # 指定日の取引かチェック
                if trade_date == target_date:
                    target_trades.append(trade)
        except Exception as e:
            logging.warning(f"取引日付解析エラー: {e}, trade: {trade}")
            continue
    
    return target_trades, target_date

def collect_metrics(use_today_only=False, days_offset=None):
    """取引メトリクスを収集"""
    global performance_metrics
    
    if use_today_only:
        trades = get_today_trades()
        period = "今日"
    elif days_offset:
        trades, target_date = get_trades_by_date_offset(days_offset)
        period = f"{target_date.strftime('%Y/%m/%d')}"
    else:
        trades = trade_results
        period = "全期間"
    
    if not trades:
        return None
    
    # 基本統計
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t.get('profit_amount', 0) > 0])
    losing_trades = len([t for t in trades if t.get('profit_amount', 0) < 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    # 損益統計
    total_profit_pips = sum([t.get('profit_pips', 0) for t in trades])
    total_profit_amount = sum([t.get('profit_amount', 0) for t in trades])
    average_profit_pips = total_profit_pips / total_trades if total_trades > 0 else 0
    average_profit_amount = total_profit_amount / total_trades if total_trades > 0 else 0
    
    # 最大・最小値
    profits = [t.get('profit_amount', 0) for t in trades if t.get('profit_amount', 0) > 0]
    losses = [t.get('profit_amount', 0) for t in trades if t.get('profit_amount', 0) < 0]
    
    max_profit = max(profits) if profits else 0
    max_loss = min(losses) if losses else 0
    
    # ドローダウン計算
    cumulative_pnl = 0
    max_cumulative = 0
    max_drawdown = 0
    max_drawdown_amount = 0
    
    for trade in trades:
        pnl = trade.get('profit_amount', 0)
        cumulative_pnl += pnl
        max_cumulative = max(max_cumulative, cumulative_pnl)
        drawdown = max_cumulative - cumulative_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_amount = drawdown
    
    # シャープレシオ（簡略版）
    if len(trades) > 1:
        returns = [t.get('profit_amount', 0) for t in trades]
        avg_return = sum(returns) / len(returns)
        variance = sum([(r - avg_return) ** 2 for r in returns]) / len(returns)
        sharpe_ratio = avg_return / (variance ** 0.5) if variance > 0 else 0
    else:
        sharpe_ratio = 0
    
    return {
        'period': period,
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'win_rate': win_rate,
        'total_profit_pips': total_profit_pips,
        'total_profit_amount': total_profit_amount,
        'average_profit_pips': average_profit_pips,
        'average_profit_amount': average_profit_amount,
        'max_profit': max_profit,
        'max_loss': max_loss,
        'max_drawdown_pips': max_drawdown,
        'max_drawdown_amount': max_drawdown_amount,
        'sharpe_ratio': sharpe_ratio,
        'api_calls': performance_metrics['api_calls'],
        'api_errors': performance_metrics['api_errors'],
        'total_api_fee': total_api_fee
    }

def auto_restart_scheduler():
    """毎日指定時刻に自動再起動するスレッド"""
    def loop():
        while True:
            try:
                # 設定を再読み込み（実行中に変更された場合のため）
                current_config = load_config()
                restart_hour = current_config.get('auto_restart_hour')
                
                if restart_hour is None:
                    # 自動再起動が無効な場合は1時間待機
                    time.sleep(3600)
                    continue
                
                now = datetime.now()
                next_restart = now.replace(hour=restart_hour, minute=0, second=0, microsecond=0)
                
                # 今日の指定時刻が既に過ぎている場合は明日に設定
                if now >= next_restart:
                    next_restart += timedelta(days=1)
                
                wait_seconds = (next_restart - now).total_seconds()
                
                logging.info(f"自動再起動スケジューラー: 次回再起動時刻 {next_restart.strftime('%Y/%m/%d %H:%M:%S')} (待機時間: {wait_seconds:.0f}秒)")
                
                # 指定時刻まで待機
                time.sleep(wait_seconds)
                
                # 再起動実行
                logging.warning(f"自動再起動時刻({restart_hour}時)に達しました。システムを再起動します。")
                send_discord_message(f"🔄 自動再起動時刻({restart_hour}時)に達しました。システムを再起動します。")
                
                # 再起動実行
                auto_restart_on_error()
                
            except Exception as e:
                logging.error(f"自動再起動スケジューラーエラー: {e}")
                time.sleep(3600)  # エラー時は1時間待機
    
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    logging.info("自動再起動スケジューラーを開始しました")

def daily_volume_reset_scheduler():
    """銘柄別取引数量を午前0時にリセットするスケジューラー"""
    def loop():
        while True:
            try:
                # 毎日午前0時に実行
                now = datetime.now()
                target_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if now >= target_time:
                    target_time += timedelta(days=1)
                
                wait_seconds = (target_time - now).total_seconds()
                logging.info(f"取引数量リセットスケジューラー: 次回リセット時刻 {target_time.strftime('%Y/%m/%d %H:%M:%S')} (待機時間: {wait_seconds:.0f}秒)")
                time.sleep(wait_seconds)
                
                # 銘柄別取引数量をリセット
                global symbol_daily_volume
                symbol_daily_volume = {}
                logging.info("銘柄別取引数量を午前0時にリセットしました")
                send_discord_message("🔄 銘柄別取引数量を午前0時にリセットしました")
                
            except Exception as e:
                logging.error(f"取引数量リセットスケジューラーエラー: {e}")
                time.sleep(3600)  # エラー時は1時間待機
    
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    logging.info("取引数量リセットスケジューラーを開始しました")

def auto_restart_on_error():
    """エラー時の自動再起動"""
    global restart_count, last_restart_time
    
    now = time.time()
    
    # クールダウン期間チェック
    if now - last_restart_time < restart_cooldown:
        logging.warning(f"再起動クールダウン期間中です。残り{restart_cooldown - (now - last_restart_time):.0f}秒")
        return False
    
    # 最大再起動回数チェック
    if restart_count >= max_restarts:
        logging.error(f"最大再起動回数({max_restarts}回)に達しました。手動介入が必要です。")
        send_discord_message(f"⚠️ 最大再起動回数({max_restarts}回)に達しました。手動介入が必要です。")
        return False
    
    restart_count += 1
    last_restart_time = now
    
    logging.warning(f"自動再起動を実行します (回数: {restart_count}/{max_restarts})")
    send_discord_message(f"🔄 自動再起動を実行します (回数: {restart_count}/{max_restarts})")
    
    try:
        # プロセス再起動
        os.execv(sys.executable, ['python'] + sys.argv)
    except Exception as e:
        logging.error(f"自動再起動エラー: {e}")
        return False
    
    return True

def save_daily_results():
    """日次取引結果をCSVファイルに保存"""
    global trade_results
    today = datetime.now().strftime('%Y-%m-%d')
    
    # daily_resultsディレクトリを作成
    daily_results_dir = 'daily_results'
    if not os.path.exists(daily_results_dir):
        os.makedirs(daily_results_dir)
        logging.info(f"daily_resultsディレクトリを作成しました: {daily_results_dir}")
    
    filename = os.path.join(daily_results_dir, f'daily_results_{today}.csv')
    
    if not trade_results:
        return
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['日付', '通貨ペア', '売買方向', 'エントリー価格', '決済価格', 'ロット数', '損益pips', '損益金額(円)', 'エントリー時刻', '決済時刻'])
            
            for trade in trade_results:
                writer.writerow([
                    today,
                    trade['symbol'],
                    trade['side'],
                    trade['entry_price'],
                    trade['exit_price'],
                    trade.get('lot_size', 'N/A'),
                    f"{trade['profit_pips']:.1f}",
                    f"{trade.get('profit_amount', 0):.0f}",
                    trade.get('entry_time', 'N/A'),
                    trade.get('exit_time', 'N/A')
                ])
        logging.info(f"日次結果を{filename}に保存しました")
    except Exception as e:
        logging.error(f"日次結果保存エラー: {e}")

def finalize_trades_for_day(target_date):
    """指定日の19:00までに決済された取引のみを集計・Discord通知"""
    global trade_results
    cutoff = datetime.combine(target_date, datetime.min.time()).replace(hour=19, minute=0, second=0)
    today_results = []
    remain_results = []
    for trade in trade_results:
        # 決済時刻をdatetime型に変換（タイムゾーン処理改善）
        exit_time_str = trade.get('exit_time')
        if exit_time_str:
            try:
                # 日付情報がなければtarget_dateを使う
                if 'T' in exit_time_str or '-' in exit_time_str:
                    # ISO形式の場合
                    exit_time_str_clean = exit_time_str.replace('Z', '+00:00')
                    exit_time = datetime.fromisoformat(exit_time_str_clean)
                else:
                    # HH:MM:SS形式の場合
                    time_obj = datetime.strptime(exit_time_str, '%H:%M:%S').time()
                    exit_time = datetime.combine(target_date, time_obj)
            except (ValueError, TypeError) as e:
                logging.error(f"決済時刻変換エラー: {e}, exit_time_str={exit_time_str}")
                exit_time = cutoff  # エラー時は当日19:00扱い
        else:
            exit_time = cutoff  # 万一なければ当日19:00扱い
        if exit_time < cutoff:
            today_results.append(trade)
        else:
            remain_results.append(trade)
    if not today_results:
        send_discord_message(f"{target_date.strftime('%Y/%m/%d')} 19:00までの取引はありませんでした。")
        trade_results = remain_results
        return
    total_profit_pips = sum(trade['profit_pips'] for trade in today_results)
    total_profit_amount = sum(trade.get('profit_amount', 0) for trade in today_results)
    
    # 口座残高取得（例外処理追加）
    try:
        balance_data = broker.get_balance()
        data = balance_data.get('data')
        if isinstance(data, list) and len(data) > 0:
            balance_amount = float(data[0].get('balance', 0))
        elif isinstance(data, dict):
            balance_amount = float(data.get('balance', 0))
        else:
            balance_amount = 0
    except Exception as e:
        logging.error(f"口座残高取得エラー: {e}")
        balance_amount = 0
    table_header = "| 通貨ペア | 売買方向 | エントリー価格 | 決済価格 | ロット数 | 損益pips | 損益金額(円) |\n|---|---|---|---|---|---|---|\n"
    table_rows = "\n".join(
        f"| {trade['symbol']} | {trade['side']} | {trade['entry_price']} | {trade['exit_price']} | {trade.get('lot_size', 'N/A')} | {trade['profit_pips']:.1f} | {trade.get('profit_amount', 0):.0f} |"
        for trade in today_results
    )
    message = (
        f"**{target_date.strftime('%Y/%m/%d')} 19:00までの取引結果**\n\n"
        f"{table_header}{table_rows}\n\n"
        f"**合計損益pips**: {total_profit_pips:.1f}\n"
        f"**本日の合計損益**: {total_profit_amount:.0f}円\n"
        f"**合計API手数料**: {round(total_api_fee)}円\n"
        f"**FX口座残高**: {balance_amount}円"
    )
    send_discord_message(message)
    # 日次結果を保存
    today = target_date.strftime('%Y-%m-%d')
    
    # daily_resultsディレクトリを作成
    daily_results_dir = 'daily_results'
    if not os.path.exists(daily_results_dir):
        os.makedirs(daily_results_dir)
        logging.info(f"daily_resultsディレクトリを作成しました: {daily_results_dir}")
    
    filename = os.path.join(daily_results_dir, f'daily_results_{today}.csv')
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['日付', '通貨ペア', '売買方向', 'エントリー価格', '決済価格', 'ロット数', '損益pips', '損益金額(円)', 'エントリー時刻', '決済時刻'])
            for trade in today_results:
                writer.writerow([
                    today,
                    trade['symbol'],
                    trade['side'],
                    trade['entry_price'],
                    trade['exit_price'],
                    trade.get('lot_size', 'N/A'),
                    f"{trade['profit_pips']:.1f}",
                    f"{trade.get('profit_amount', 0):.0f}",
                    trade.get('entry_time', 'N/A'),
                    trade.get('exit_time', 'N/A')
                ])
        logging.info(f"日次結果を{filename}に保存しました")
    except Exception as e:
        logging.error(f"日次結果保存エラー: {e}")
    # その日分をリセット
    trade_results = remain_results

def get_execution_fee(order_id):
    """
    注文IDから実際に発生した手数料（fee）を合計して返す
    """
    timestamp = generate_timestamp()
    method = 'GET'
    path = '/v1/executions'
    url = 'https://forex-api.coin.z.com/private' + path
    params = {"orderId": order_id}
    headers = {
        "API-KEY": GMO_API_KEY,
        "API-TIMESTAMP": timestamp,
        "API-SIGN": generate_signature(timestamp, method, path)
    }
    response = retry_request(method, url, headers, params=params)
    if 'data' in response and 'list' in response['data'] and len(response['data']['list']) > 0:
        # 複数約定がある場合はfeeを合計
        total_fee = sum(float(exe.get('fee', 0)) for exe in response['data']['list'])
        return total_fee
    else:
        raise ValueError("約定履歴から手数料情報を取得できませんでした")

def get_execution_price(order_id):
    """
    注文IDから約定価格（平均値）を取得
    """
    timestamp = generate_timestamp()
    method = 'GET'
    path = '/v1/executions'
    url = 'https://forex-api.coin.z.com/private' + path
    params = {"orderId": order_id}
    headers = {
        "API-KEY": GMO_API_KEY,
        "API-TIMESTAMP": timestamp,
        "API-SIGN": generate_signature(timestamp, method, path)
    }
    
    try:
        response = retry_request(method, url, headers, params=params)
        if 'data' in response and 'list' in response['data'] and len(response['data']['list']) > 0:
            prices = []
            for exe in response['data']['list']:
                try:
                    prices.append(float(exe['price']))
                except (KeyError, TypeError, ValueError) as e:
                    logging.error(f"約定価格変換エラー: {e}")
                    continue
            if not prices:
                raise ValueError("有効な価格データがありません")
            return sum(prices) / len(prices)
        else:
            raise ValueError("約定履歴から価格情報を取得できませんでした")
    except Exception as e:
        logging.error(f"約定価格取得エラー: {e}")
        raise

def execute_daily_trades():
    """
    1日の取引を実行（main.pyと同様の機能）
    """
    global trade_results, total_api_fee
    
    try:
        # trades.csvから取引指示を読み込む
        with open(SCHEDULE_CSV, mode='r', encoding='utf-8') as file:
            reader = csv.reader(file)
            header = next(reader)
            trades = []
            for row_num, row in enumerate(reader, start=2):  # 行番号を追跡（ヘッダーが1行目）
                # 空行や不完全な行をスキップ
                if len(row) >= 6 and row[1].strip() and row[2].strip() and row[3].strip() and row[4].strip():
                    # 時刻形式の検証
                    try:
                        datetime.strptime(row[3].strip(), '%H:%M:%S')
                        datetime.strptime(row[4].strip(), '%H:%M:%S')
                        trades.append(row)
                    except ValueError as e:
                        logging.warning(f"行{row_num}: 時刻形式エラー - {row[3]} または {row[4]}: {e}")
                else:
                    if row and any(cell.strip() for cell in row):  # 完全に空でない行のみログ出力
                        logging.warning(f"行{row_num}: 不完全な行をスキップ: {row}")
        
        if not trades:
            send_discord_message("trades.csvに取引データがありません。本日の取引をスキップします。")
            return True
        
        now = datetime.now()
        
        # 前日の最後の取引時刻を取得（日を跨いだ取引の連続性のため）
        last_trade_time = None
        if trade_results:
            # 前日の取引結果から最後の決済時刻を取得
            last_trades = [t for t in trade_results if t.get('exit_time')]
            if last_trades:
                # 最新の決済時刻を取得
                last_exit_time_str = max(last_trades, key=lambda x: x.get('exit_time', '')).get('exit_time')
                if last_exit_time_str:
                    try:
                        if 'T' in last_exit_time_str or '-' in last_exit_time_str:
                            # ISO形式の場合
                            last_exit_time_str_clean = last_exit_time_str.replace('Z', '+00:00')
                            last_trade_time = datetime.fromisoformat(last_exit_time_str_clean)
                        else:
                            # HH:MM:SS形式の場合（前日の日付を仮定）
                            time_obj = datetime.strptime(last_exit_time_str, '%H:%M:%S').time()
                            last_trade_time = datetime.combine(now.date() - timedelta(days=1), time_obj)
                        logging.info(f"前日の最後の取引時刻: {last_trade_time.strftime('%Y/%m/%d %H:%M:%S')}")
                    except (ValueError, TypeError) as e:
                        logging.warning(f"前日の最後の取引時刻取得エラー: {e}")
        
        adjusted_trades = []
        for i, trade in enumerate(trades):
            try:
                original_entry_time = datetime.strptime(trade[3].strip(), '%H:%M:%S').replace(year=now.year, month=now.month, day=now.day)
                entry_time = original_entry_time
            except ValueError as e:
                logging.error(f"取引{i+1}: エントリー時刻の解析エラー - {trade[3]}: {e}")
                continue
            
            # 前日の最後の取引時刻がある場合、連続性を考慮
            if last_trade_time and entry_time < last_trade_time:
                # 前日の最後の取引時刻より前の場合は翌日に設定
                entry_time = entry_time + timedelta(days=1)
                logging.info(f"取引{i+1}: 前日の最後の取引時刻({last_trade_time.strftime('%H:%M:%S')})を考慮し、エントリー時刻を翌日に調整: {original_entry_time.strftime('%H:%M:%S')} → {entry_time.strftime('%Y/%m/%d %H:%M:%S')}")
            elif entry_time < now:
                # 現在時刻より前の場合は翌日に設定
                entry_time = entry_time + timedelta(days=1)
                logging.info(f"取引{i+1}: 現在時刻({now.strftime('%H:%M:%S')})を考慮し、エントリー時刻を翌日に調整: {original_entry_time.strftime('%H:%M:%S')} → {entry_time.strftime('%Y/%m/%d %H:%M:%S')}")
            else:
                logging.info(f"取引{i+1}: エントリー時刻をそのまま使用: {entry_time.strftime('%Y/%m/%d %H:%M:%S')}")
            
            # 決済時刻も同様に調整
            try:
                original_exit_time = datetime.strptime(trade[4].strip(), '%H:%M:%S').replace(year=entry_time.year, month=entry_time.month, day=entry_time.day)
                exit_time = original_exit_time
            except ValueError as e:
                logging.error(f"取引{i+1}: 決済時刻の解析エラー - {trade[4]}: {e}")
                continue
            if exit_time <= entry_time:
                exit_time = exit_time + timedelta(days=1)
                logging.info(f"取引{i+1}: 決済時刻を翌日に調整: {original_exit_time.strftime('%H:%M:%S')} → {exit_time.strftime('%Y/%m/%d %H:%M:%S')}")
            
            # trade[3]とtrade[4]を書き換えた新リストを作成
            adjusted_trade = trade.copy()
            adjusted_trade[3] = entry_time.strftime('%H:%M:%S')
            adjusted_trade[4] = exit_time.strftime('%H:%M:%S')
            # 調整済みのdatetimeオブジェクトを追加で保存
            adjusted_trade.append(entry_time)  # インデックス6に調整済みentry_time
            adjusted_trade.append(exit_time)   # インデックス7に調整済みexit_time
            # ソート用にタプルを作成（entry_time, trade）
            adjusted_trades.append((entry_time, adjusted_trade))
        
        # entry_timeでソート
        adjusted_trades.sort(key=lambda x: x[0])
        # ソート済みのtradeのみを抽出
        filtered_trades = [t[1] for t in adjusted_trades]

        # 口座残高を取得
        try:
            balance_data = broker.get_balance()
            data = balance_data.get('data')
            if isinstance(data, list) and len(data) > 0:
                balance_amount = float(data[0].get('balance', 0))
            elif isinstance(data, dict):
                balance_amount = float(data.get('balance', 0))
            else:
                balance_amount = 0
        except Exception as e:
            logging.error(f"口座残高取得エラー: {e}")
            balance_amount = 0

        # エントリー予定一覧をDiscordに通知
        today_date = datetime.now().strftime("%Y/%m/%d")
        entry_list_message = f"{today_date}のエントリー一覧:\n"
        
        # 日を跨いだ取引の情報を追加
        if last_trade_time:
            entry_list_message += f"📅 前日の最後の取引時刻: {last_trade_time.strftime('%Y/%m/%d %H:%M:%S')}\n"
            entry_list_message += f"🔄 日を跨いだ取引の連続性を考慮して時刻を調整しました\n\n"
        
        for trade in filtered_trades:
            # 通貨ペアの正規化（表示用）
            pair_display = trade[2].strip()
            if "/" in pair_display:
                pair_display = pair_display.replace("/", "_")
            elif len(pair_display) == 6:  # USDJPY, EURUSD など
                pair_display = f"{pair_display[:3]}_{pair_display[3:]}"
            
            entry_list_message += (
                f"{pair_display} {trade[1]} "
                f"ロット数: {trade[5]} エントリー時間: {trade[3]} 決済時間: {trade[4]}\n"
            )
        entry_list_message += f"\nFX口座残高: {balance_amount}円"
        entry_list_message += f"\nレバレッジ: {LEVERAGE}倍"
        entry_list_message += f"\n自動ロット設定: {AUTOLOT}"
        entry_list_message += f"\nポジション確認: {POSITION_CHECK_INTERVAL_MINUTES}分毎"
        entry_list_message += f"\nストップロス: {STOP_LOSS_PIPS} pips"
        entry_list_message += f"\nテイクプロフィット: {TAKE_PROFIT_PIPS} pips"
        send_discord_message(entry_list_message)

        # 取引実行・監視・決済
        process_trades(filtered_trades)

        # 取引完了通知
        send_discord_message("本日の取引が完了しました")
        
        return True

    except FileNotFoundError:
        # trades.csvが存在しない場合のエラー通知
        error_message = f"{SCHEDULE_CSV} が見つかりませんでした。プログラムを終了します。"
        send_discord_message(error_message)
        return False
    except Exception as e:
        # その他予期しないエラーの通知
        error_msg = f"プログラム実行中に予期しないエラーが発生しました: {e}"
        logging.error(f"{error_msg}\n{traceback.format_exc()}")
        send_discord_message(error_msg)
        return False

if __name__ == "__main__":
    main() 