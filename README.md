# GMO Coin 自動取引ボット (統合版)

## 概要

GMO Coin 自動取引ボットは、GMO CoinのFX APIを使用した高度な自動取引システムです。Discord連携、リスク管理、詳細なログ機能を備え、24時間無人運転での取引を可能にします。

## 主要機能

### 🔄 自動取引機能
- **スケジュール取引**: `trades.csv`で指定した時刻に自動エントリー・決済
- **Jitter機能**: エントリー時刻にランダムなゆらぎを追加して市場への影響を最小化
- **日を跨ぐ取引対応**: 23:30エントリー→00:05決済などの日を跨ぐ取引を適切に処理
- **自動ロット計算**: 証拠金残高に基づく安全なロットサイズの自動計算

### 🛡️ リスク管理
- **スプレッド監視**: 指定した閾値を超えるスプレッド時はエントリーをスキップ
- **ストップロス/テイクプロフィット**: 自動決済機能
- **銘柄別取引数量制限**: 一日の取引数量上限を設定
- **重複建玉防止**: 同一方向の重複ポジションを防止
- **レート制限管理**: API制限に応じた動的な調整
- **定期ポジション監視**: 指定時間ごとにポジションを監視し、trades.csvの時間外ポジションを自動決済

### 📊 監視・分析機能
- **リアルタイム監視**: ポジションの含み損益をリアルタイム監視
- **パフォーマンス分析**: 詳細な取引統計とレポート生成
- **メモリ管理**: 自動メモリクリーンアップと使用量監視
- **ヘルスチェック**: システム全体の健全性監視

### 🤖 Discord連携
- **リアルタイム通知**: 取引実行状況をDiscordに通知
- **Bot機能**: Discordコマンドによる遠隔操作
- **詳細レポート**: パフォーマンス統計の表示

### 🔧 システム管理
- **自動再起動**: 指定時刻での自動再起動機能
- **バックアップ**: 設定とデータの自動バックアップ
- **ログ管理**: ローテーション機能付き詳細ログ
- **設定管理**: 実行中の設定変更と再読み込み

## 定期ポジション監視機能

### 🔍 **監視機能の詳細**
- **監視間隔**: `position_check_interval_minutes`で設定（デフォルト: 10分）
- **監視対象**: 全通貨ペアのポジション
- **判定基準**: trades.csvで指定されたエントリー～決済時間外のポジション

### ⏰ **監視ロジック**
1. **スケジュール時間の前後5秒は監視をスキップ**（エントリー/決済処理の干渉を防止）
2. **trades.csvの時間外でポジションが存在する場合に自動決済**
3. **Discordに強制決済通知を送信**

### 🚨 **自動決済機能**
- **全ポジションの強制決済**: 時間外ポジションを検出した場合
- **損益計算**: 決済時の損益を自動計算
- **詳細通知**: Discordに決済結果と損益情報を送信

### 📋 **設定方法**
```json
{
  "position_check_interval_minutes": 10
}
```

## システム要件

### 必要な環境
- Python 3.8以上
- GMO Coin APIアカウント
- Discord Webhook URL（オプション）
- Discord Bot Token（オプション）

### 必要なパッケージ
```
requests>=2.25.1
discord.py>=2.0.0
psutil>=5.8.0
```

## インストール・セットアップ

### 1. リポジトリのクローン
```bash
git clone <repository-url>
cd gmocoinbot_updated
```

### 2. 依存関係のインストール
```bash
pip install -r requirements.txt
```

### 3. 設定ファイルの作成
初回実行時に`config.json`が自動生成されます。以下の項目を設定してください：

```json
{
  "api_key": "YOUR_GMO_API_KEY",
  "api_secret": "YOUR_GMO_API_SECRET",
  "discord_webhook_url": "YOUR_DISCORD_WEBHOOK_URL",
  "discord_bot_token": "YOUR_DISCORD_BOT_TOKEN",
  "spread_threshold": 0.01,
  "jitter_seconds": 3,
  "entry_order_retry_interval": 5,
  "max_entry_order_attempts": 3,
  "exit_order_retry_interval": 10,
  "max_exit_order_attempts": 3,
  "stop_loss_pips": 0,
  "take_profit_pips": 0,
  "position_check_interval": 5,
  "position_check_interval_minutes": 10,
  "leverage": 10,
  "risk_ratio": 1.0,
  "autolot": "TRUE",
  "auto_restart_hour": null,
  "symbol_daily_volume_limit": 15000000
}
```

### 4. 取引スケジュールの設定
`trades.csv`ファイルを作成し、取引スケジュールを設定：

```csv
取引番号,売買方向,通貨ペア,エントリー時刻,決済時刻,ロット数
1,買,USD/JPY,09:30:00,10:00:00,
2,売,EUR/JPY,14:00:00,14:30:00,
3,買,GBP/JPY,15:30:00,16:00:00,
```

### 5. 環境変数の設定（オプション）
```bash
export GMO_API_KEY="your_api_key"
export GMO_API_SECRET="your_api_secret"
export DISCORD_WEBHOOK_GMO="your_webhook_url"
export DISCORD_BOT_TOKEN="your_bot_token"
export TRADES_CSV="trades.csv"
export TIME_BUFFER="5"
export LOG_LEVEL="INFO"
```

## 使用方法

### 基本的な起動
```bash
python main_integrated.py
```

### Windows用バッチファイル
```bash
trade.bat
```

### 設定エディターの使用
```bash
python config_editor.py
```

## 設定項目詳細

### 基本設定
- **api_key/api_secret**: GMO Coin API認証情報
- **discord_webhook_url**: Discord通知用Webhook URL
- **discord_bot_token**: Discord Bot用トークン

### 取引設定
- **spread_threshold**: 許容スプレッド（pips）
- **jitter_seconds**: エントリー時刻のゆらぎ（秒）
- **leverage**: レバレッジ倍率
- **risk_ratio**: 証拠金使用率（0.1-1.0）
- **autolot**: 自動ロット計算（TRUE/FALSE）

### リスク管理
- **stop_loss_pips**: ストップロス（pips、0で無効）
- **take_profit_pips**: テイクプロフィット（pips、0で無効）
- **symbol_daily_volume_limit**: 銘柄別一日取引数量制限

### システム設定
- **auto_restart_hour**: 自動再起動時刻（0-23、nullで無効）
- **position_check_interval**: ポジション監視間隔（秒）
- **position_check_interval_minutes**: 定期監視間隔（分）

## Discord Bot コマンド

### 基本コマンド
- `kill` - 全ポジションを即座に決済（緊急時）
- `stop` - ボットを停止
- `restart` - ボットを再起動
- `position` - 現在のポジションを表示
- `status` - システムステータスを表示
- `health` - ヘルスチェックを実行

### 分析コマンド
- `performance [日数]` - パフォーマンスレポートを表示
- `all` - 全情報を表示
- `schedule` - 取引スケジュールを表示

### 管理コマンド
- `backup` - 手動バックアップを実行
- `memory` - メモリ使用量を表示
- `cleanup` - メモリクリーンアップを実行
- `reload` - 設定を再読み込み
- `testlot [通貨ペア] [売買方向]` - ロット計算テスト
- `debuglot [通貨ペア] [売買方向]` - ロット計算デバッグ

## ファイル構成

```
gmocoinbot_updated/
├── main_integrated.py      # メイン実行ファイル
├── trading_time.py         # 時刻管理モジュール
├── config_editor.py        # 設定エディター
├── config_example.json     # 設定ファイルサンプル
├── trades_example.csv      # 取引スケジュールサンプル
├── requirements.txt        # 依存関係
├── SETUP.md               # セットアップガイド
├── logs/                  # ログディレクトリ
│   ├── main.log          # メインログ
│   ├── error.log         # エラーログ
│   ├── trade.log         # 取引ログ
│   └── api.log           # APIログ
├── backups/               # バックアップディレクトリ
├── daily_results/         # 日次結果ディレクトリ
└── README.md             # このファイル
```

## ログシステム

### ログファイル
- **main.log**: 一般的なシステムログ
- **error.log**: エラー専用ログ
- **trade.log**: 取引関連ログ
- **api.log**: API呼び出しログ

### ログローテーション
- メインログ: 10MB、5世代保持
- エラーログ: 5MB、3世代保持
- 取引ログ: 5MB、3世代保持
- APIログ: 5MB、3世代保持

## セキュリティ

### API認証
- HMAC-SHA256署名による認証
- タイムスタンプベースのリクエスト検証
- 環境変数による機密情報管理

### リスク管理
- 取引数量制限
- スプレッド監視
- 自動ストップロス
- 重複建玉防止
- 定期ポジション監視

### ⚠️ セキュリティ注意事項
- **config.jsonは絶対にGitにコミットしないでください**
- APIキーは定期的に更新してください
- 環境変数を使用することを推奨します
- ログファイルには機密情報が含まれる場合があります

## トラブルシューティング

### よくある問題

#### API接続エラー
- APIキーとシークレットが正しく設定されているか確認
- ネットワーク接続を確認
- GMO Coin APIの稼働状況を確認

#### Discord通知が届かない
- Webhook URLが正しく設定されているか確認
- Discordサーバーの権限設定を確認
- ネットワーク接続を確認

#### 取引が実行されない
- `trades.csv`の形式が正しいか確認
- 時刻設定が正しいか確認
- スプレッドが閾値を超えていないか確認

#### メモリ使用量が高い
- `cleanup`コマンドでメモリクリーンアップを実行
- ログファイルのサイズを確認
- システムリソースを確認

### ログの確認
```bash
# エラーログの確認
tail -f logs/error.log

# 取引ログの確認
tail -f logs/trade.log

# メインログの確認
tail -f logs/main.log
```

## パフォーマンス最適化

### メモリ管理
- 定期的なガベージコレクション
- ログローテーション
- 不要なデータの自動削除

### API最適化
- レート制限の動的調整
- リトライ機能
- キャッシュ機能

### 監視最適化
- 効率的なポジション監視
- バックグラウンド処理
- 非同期処理

## 開発・カスタマイズ

### 新しい機能の追加
1. 機能を`main_integrated.py`に実装
2. 必要に応じて設定項目を追加
3. Discordコマンドを追加
4. ログ出力を追加
5. テストを実行

### 設定のカスタマイズ
- `config.json`で各種パラメータを調整
- 環境変数で動的な設定変更
- `config_editor.py`でGUI設定

## ライセンス

MIT License

## サポート

### ドキュメント
- このREADMEファイル
- `SETUP.md` - 詳細なセットアップガイド
- コード内のコメント
- ログファイル

### 問題報告
- GitHub Issuesを使用
- 詳細なログを添付
- 再現手順を記載

## 更新履歴

### v2.1.0 (最新)
- 統合版リリース
- Discord Bot機能追加
- 詳細なリスク管理機能
- パフォーマンス分析機能
- 自動バックアップ機能
- メモリ管理機能
- ヘルスチェック機能
- **定期ポジション監視機能追加**

### v2.0.0
- 基本自動取引機能
- Discord通知機能
- リスク管理機能

### v1.0.0
- 初期リリース
- 基本的なAPI連携

## 注意事項

### リスク警告
- 自動取引にはリスクが伴います
- 十分なテストを行ってから本番運用してください
- 資金管理を適切に行ってください
- 市場状況に応じて設定を調整してください

### 法的注意
- 各国の金融規制を遵守してください
- 税務申告を適切に行ってください
- 利用規約を確認してください

### 技術的注意
- 定期的なバックアップを実行してください
- システム監視を継続してください
- セキュリティアップデートを適用してください
- ログファイルを定期的に確認してください
- **機密情報を含むファイルは絶対にGitにコミットしないでください** 