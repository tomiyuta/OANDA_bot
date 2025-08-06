# trading_time.py
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from typing import List, Protocol, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")          # デフォルトタイムゾーン
TRADING_DAY_START = time(6, 0)       # 取引日境界（06:00 JST）


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """テスト容易性向上のための依存性注入可能な時計"""
    def __init__(self, tz: ZoneInfo = JST):
        self._tz = tz

    def now(self) -> datetime:
        return datetime.now(self._tz)


@dataclass(frozen=True)
class TradeWindow:
    entry: time
    exit: time
    buffer: timedelta

    @staticmethod
    def from_strings(entry_str: str, exit_str: str, buffer_seconds: int = 5) -> "TradeWindow":
        return TradeWindow(_parse_time(entry_str),
                           _parse_time(exit_str),
                           timedelta(seconds=buffer_seconds))

    def window_for(self, ref: datetime) -> tuple[datetime, datetime]:
        """ref の取引日における entry/exit の絶対値 (aware) を返す"""
        trading_day = _trading_day_of(ref)
        entry_dt = datetime.combine(trading_day, self.entry, ref.tzinfo)
        exit_dt  = datetime.combine(trading_day, self.exit,  ref.tzinfo)
        if exit_dt <= entry_dt:               # 日跨ぎ補正
            exit_dt += timedelta(days=1)
        return entry_dt, exit_dt

    def is_open(self, now: datetime) -> bool:
        entry_dt, exit_dt = self.window_for(now)
        return entry_dt - self.buffer <= now <= exit_dt + self.buffer

    def is_entry_point(self, now: datetime) -> bool:
        entry_dt, _ = self.window_for(now)
        return abs((now - entry_dt).total_seconds()) <= self.buffer.total_seconds()

    def is_exit_point(self, now: datetime) -> bool:
        _, exit_dt = self.window_for(now)
        return abs((now - exit_dt).total_seconds()) <= self.buffer.total_seconds()


@dataclass
class TradeData:
    """取引データを表すクラス"""
    trade_number: str
    direction: str
    symbol: str
    entry_time: time
    exit_time: time
    lot_size: Optional[str] = None

    @staticmethod
    def from_csv_row(row: List[str]) -> "TradeData":
        """CSV行からTradeDataを作成"""
        if len(row) < 5:
            raise ValueError(f"CSV行の列数が不足しています: {len(row)}")
        
        return TradeData(
            trade_number=row[0].strip(),
            direction=row[1].strip(),
            symbol=row[2].strip(),
            entry_time=_parse_time(row[3].strip()),
            exit_time=_parse_time(row[4].strip()),
            lot_size=row[5].strip() if len(row) > 5 else None
        )


def _parse_time(ts: str) -> time:
    """'HH:MM[:SS]' を許容し、不足分は 0 で補完"""
    parts = [int(p) for p in ts.strip().split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(*parts[:3])


def _trading_day_of(dt: datetime) -> date:
    """取引日を返す（境界時刻前は前日扱い）"""
    if dt.timetz() < TRADING_DAY_START:
        return (dt - timedelta(days=1)).date()
    return dt.date()


class TradeSchedule:
    def __init__(self, trades: List[TradeData], clock: Clock | None = None):
        self._trades = trades
        self._clock = clock or SystemClock()

    @classmethod
    def from_csv(cls, path: str, buffer_seconds: int = 5,
                 clock: Clock | None = None) -> "TradeSchedule":
        """元のtrades.csv形式からTradeScheduleを作成"""
        trades: List[TradeData] = []
        
        with open(path, newline="", encoding='utf-8') as fh:
            reader = csv.reader(fh)
            header = next(reader)  # ヘッダー行をスキップ
            
            for row_num, row in enumerate(reader, start=2):
                if not row or len(row) < 5:
                    continue
                
                try:
                    # 必須フィールドの検証
                    if not all([row[0].strip(), row[1].strip(), row[2].strip(), 
                               row[3].strip(), row[4].strip()]):
                        continue
                    
                    # 時刻形式の検証
                    _parse_time(row[3].strip())
                    _parse_time(row[4].strip())
                    
                    trade_data = TradeData.from_csv_row(row)
                    trades.append(trade_data)
                    
                except (ValueError, IndexError) as e:
                    print(f"行{row_num}: データ形式エラー - {e}")
                    continue
        
        return cls(trades, clock)

    # ----- 外部 API -----
    def now(self) -> datetime:
        return self._clock.now()

    def get_trades_for_today(self) -> List[TradeData]:
        """今日の取引データを取得"""
        now = self.now()
        today_trades = []
        
        for trade in self._trades:
            # エントリー時刻を今日の日付でdatetimeに変換
            entry_dt = datetime.combine(now.date(), trade.entry_time, now.tzinfo)
            exit_dt = datetime.combine(now.date(), trade.exit_time, now.tzinfo)
            
            # 日を跨ぐ取引の場合は翌日に調整
            if exit_dt <= entry_dt:
                exit_dt += timedelta(days=1)
            
            # 現在時刻より前の場合は翌日に調整
            if entry_dt < now:
                entry_dt += timedelta(days=1)
                exit_dt += timedelta(days=1)
            
            today_trades.append((entry_dt, trade))
        
        # エントリー時刻でソート
        today_trades.sort(key=lambda x: x[0])
        return [trade for _, trade in today_trades]

    def should_enter(self) -> bool:
        """エントリー条件をチェック"""
        now = self.now()
        today_trades = self.get_trades_for_today()
        
        for trade in today_trades:
            entry_dt = datetime.combine(now.date(), trade.entry_time, now.tzinfo)
            if entry_dt < now:
                entry_dt += timedelta(days=1)
            
            # バッファ時間内かチェック
            time_diff = abs((now - entry_dt).total_seconds())
            if time_diff <= 5:  # 5秒のバッファ
                return True
        
        return False

    def should_exit(self) -> bool:
        """決済条件をチェック"""
        now = self.now()
        today_trades = self.get_trades_for_today()
        
        for trade in today_trades:
            exit_dt = datetime.combine(now.date(), trade.exit_time, now.tzinfo)
            if exit_dt < now:
                exit_dt += timedelta(days=1)
            
            # バッファ時間内かチェック
            time_diff = abs((now - exit_dt).total_seconds())
            if time_diff <= 5:  # 5秒のバッファ
                return True
        
        return False

    def get_next_trade(self) -> Optional[TradeData]:
        """次の取引を取得"""
        today_trades = self.get_trades_for_today()
        return today_trades[0] if today_trades else None

    def get_active_trades(self) -> List[TradeData]:
        """現在アクティブな取引を取得"""
        now = self.now()
        active_trades = []
        
        for trade in self._trades:
            entry_dt = datetime.combine(now.date(), trade.entry_time, now.tzinfo)
            exit_dt = datetime.combine(now.date(), trade.exit_time, now.tzinfo)
            
            # 日を跨ぐ取引の場合は翌日に調整
            if exit_dt <= entry_dt:
                exit_dt += timedelta(days=1)
            
            # 現在時刻より前の場合は翌日に調整
            if entry_dt < now:
                entry_dt += timedelta(days=1)
                exit_dt += timedelta(days=1)
            
            # 現在時刻が取引時間内かチェック
            if entry_dt <= now <= exit_dt:
                active_trades.append(trade)
        
        return active_trades 