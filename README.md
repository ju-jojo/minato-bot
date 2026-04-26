# minato-bot

Telegram Bot for posting to Threads (@minato_kakusei).

## アーキテクチャ

```
[ユーザー] → Telegram → [XServer Webhook (CGI)] → Threads API → [@minato_kakusei]
```

- **チャネル**: Telegram Bot (`@minato_threads_bot`)
- **Webhook受け口**: XServer `https://on-mitsu.jp/minato-bot/webhook.cgi`
- **画像生成**: ユーザーがスマホChatGPTアプリで実行→Telegramに転送

## 使い方

| コマンド | 動作 |
|---|---|
| `/post 投稿文` | 投稿文を保存 |
| `/prompt プロンプト` | プロンプト保存＋ChatGPT起動リンク返信 |
| 画像送信 | 保存済み投稿文＋画像で Threads 投稿 |
| `/status` | 現在のセッション内容確認 |
| `/reset` | セッションクリア |
| `/help` | ヘルプ表示 |

## デプロイ

このリポジトリは `ju-jojo/threads-taishoku` の `deploy-minato-bot.yml` workflow からデプロイされます（既存のXServer SSH設定を共有するため）。

```bash
gh workflow run deploy-minato-bot.yml --repo ju-jojo/threads-taishoku
```

## ファイル構成

```
minato-bot/
├── webhook.cgi          # CGI エントリポイント
├── lib/
│   ├── config.py        # 環境変数ロード
│   ├── telegram.py      # Telegram Bot API クライアント
│   ├── threads.py       # Threads Graph API クライアント
│   ├── session.py       # ユーザーセッション管理
│   └── handler.py       # メッセージハンドラ
├── .htaccess            # CGI実行 + アクセス制限
├── state/               # セッションJSON
├── logs/                # ログ
└── images/              # Threads投稿画像（公開）
```
