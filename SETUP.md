# セットアップガイド

## 初回セットアップ

### 1. リポジトリのクローン
```bash
git clone <repository-url>
cd gmocoinbot_updated
```

### 2. 依存関係のインストール
```bash
pip install -r requirements.txt
```

### 3. 設定ファイルの準備

#### 方法1: サンプルファイルをコピー（推奨）
```bash
# 設定ファイルのサンプルをコピー
cp config_example.json config.json

# 取引スケジュールのサンプルをコピー
cp trades_example.csv trades.csv
```

#### 方法2: GUI設定エディターを使用
```bash
python config_editor.py
```

### 4. 設定ファイルの編集

`config.json`を編集して以下の項目を設定してください：

```json
{
  "api_key": "YOUR_GMO_API_KEY_HERE",
  "api_secret": "YOUR_GMO_API_SECRET_HERE",
  "discord_webhook_url": "YOUR_DISCORD_WEBHOOK_URL_HERE",
  "discord_bot_token": "YOUR_DISCORD_BOT_TOKEN_HERE"
}
```

### 5. 取引スケジュールの設定

`trades.csv`を編集して取引スケジュールを設定してください：

```csv
取引番号,売買方向,通貨ペア,エントリー時刻,決済時刻,ロット数
1,買,USD/JPY,09:30:00,10:00:00,
2,売,EUR/JPY,14:00:00,14:30:00,
```

## 環境変数の設定（オプション）

```bash
export GMO_API_KEY="your_api_key"
export GMO_API_SECRET="your_api_secret"
export DISCORD_WEBHOOK_GMO="your_webhook_url"
export DISCORD_BOT_TOKEN="your_bot_token"
export TRADES_CSV="trades.csv"
export TIME_BUFFER="5"
export LOG_LEVEL="INFO"
```

## システム起動

### 基本的な起動
```bash
python main_integrated.py
```

### Windows用バッチファイル
```bash
trade.bat
```

## セキュリティ注意事項

### ⚠️ 重要な注意事項

1. **APIキーの管理**
   - `config.json`は絶対にGitにコミットしないでください
   - APIキーは定期的に更新してください
   - 環境変数を使用することを推奨します

2. **設定ファイルの保護**
   - `config.json`の権限を適切に設定してください
   - 機密情報を含むファイルは安全な場所に保管してください

3. **ログファイルの管理**
   - ログファイルには機密情報が含まれる場合があります
   - 定期的にログファイルを確認・削除してください

## トラブルシューティング

### よくある問題

#### 設定ファイルが見つからない
```bash
# サンプルファイルをコピー
cp config_example.json config.json
cp trades_example.csv trades.csv
```

#### API接続エラー
- APIキーとシークレットが正しく設定されているか確認
- GMO Coin APIの稼働状況を確認

#### Discord通知が届かない
- Webhook URLが正しく設定されているか確認
- Discordサーバーの権限設定を確認

## サポート

問題が発生した場合は、以下を確認してください：

1. ログファイルの確認（`logs/`ディレクトリ）
2. 設定ファイルの検証
3. システム要件の確認
4. Discordコマンド `health` でのヘルスチェック 