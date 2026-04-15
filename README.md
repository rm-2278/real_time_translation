# Real-time Translation

Zoomミーティングの音声をリアルタイムに文字起こし・翻訳するシステム

## システム構成

```
Zoom RTMS → AudioCapture → Deepgram WebSocket → LLM (Gemini/OpenAI) → 翻訳出力
```

## セットアップ

```bash
# 環境変数の設定
cp .env.example .env
# .envファイルを編集してAPIキーを設定
```

## 環境変数

| 変数名                      | 説明                        | 必須         |
| --------------------------- | --------------------------- | ------------ |
| `ZOOM_CLIENT_ID`            | Zoom RTMS Client ID         | 今回は不要   |
| `ZOOM_CLIENT_SECRET`        | Zoom RTMS Client Secret     | 今回は不要   |
| `DEEPGRAM_API_KEY`          | Deepgram APIキー            | ✓            |
| `DEEPGRAM_MODEL`            | Deepgramモデル名            |              |
| `DEEPGRAM_ENDPOINTING`      | 無音検知の確定(ms)          |              |
| `DEEPGRAM_UTTERANCE_END_MS` | 発話終了検知(ms)            |              |
| `DEEPGRAM_INTERIM_RESULTS`  | Interim出力有無             |              |
| `DEEPGRAM_SMART_FORMAT`     | smart_format有無            |              |
| `DEEPGRAM_VAD_EVENTS`       | VADイベント有無             |              |
| `LLM_PROVIDER`              | `gemini` または `openai`    | ✓            |
| `GOOGLE_API_KEY`            | Google AI APIキー           | Gemini使用時 |
| `OPENAI_API_KEY`            | OpenAI APIキー              | OpenAI使用時 |
| `GEMINI_MODEL`              | Geminiモデル名              |              |
| `OPENAI_MODEL`              | OpenAIモデル名              |              |
| `SOURCE_LANGUAGE`           | 元言語コード (例: `en`)     |              |
| `TARGET_LANGUAGE`           | 翻訳先言語コード (例: `ja`) |              |
| `CONTEXT_WINDOW_SIZE`       | 文脈保持の文数              |              |
| `TRANSLATION_QUEUE_SIZE`    | 翻訳キューサイズ            |              |
| `DICTIONARY_PATH`           | 用語辞書CSVパス             |              |

`DEEPGRAM_UTTERANCE_END_MS` を設定すると、UtteranceEndイベントで
直近のinterim結果を確定として扱い、文脈のまとまりを優先できます。

### マイクロサービス用追加環境変数

| 変数名                        | 説明                         | 必須 |
| ----------------------------- | ---------------------------- | ---- |
| `RTMP_URL`                    | NMSのRTMP入力URL             | ✓    |
| `WS_PUBLISH_URL`              | WSサービスへのPublish URL    | ✓    |
| `TRANSLATION_API_URL`         | 翻訳API URL                  | ✓    |
| `DICTIONARY_PATH`             | 用語辞書CSVパス（keyterm用） |      |
| `ZOOM_CAPTION_URL`            | Zoom字幕API URL              |      |
| `ZOOM_CAPTION_LANG`           | Zoom字幕の言語コード         |      |
| `ASR_SEND_INTERIM`            | ASRのinterimをWSへ送信       |      |
| `TRANSLATE_INTERIM`           | interimを翻訳へ送信          |      |
| `ASR_PARTIAL_MIN_INTERVAL_MS` | interim送信間隔(ms)          |      |
| `TRANSLATION_CONCURRENCY`     | 翻訳同時実行数               |      |
| `HTTP_TIMEOUT`                | HTTPタイムアウト(秒)         |      |

`DICTIONARY_PATH`で指定したCSVファイルの`source_term`列がDeepgramの[Keyterm Prompting](https://developers.deepgram.com/docs/keyterm)として渡され、専門用語の認識精度が向上します。

`ZOOM_CAPTION_URL`を設定すると、翻訳結果がZoomミーティングの字幕として表示されます。URLはZoomミーティング内で「字幕を有効化」→「APIトークンをコピー」から取得できます。詳細は[Zoomサポート](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060372)を参照。

## 使い方

```bash
# Zoom RTMS向けCLI
uv run real-time-translation

# Webデモ (Gradio / マイク入力)
uv run real-time-translation-demo

# マイクロサービス単体起動（各サービスのディレクトリで実行）
cd services/ws
uv sync
uv run real-time-translation-ws

cd ../translator
uv sync
uv run real-time-translation-translate

cd ../asr
uv sync
uv run real-time-translation-asr
```

WebデモはZoom認証なしで動作します。`DEEPGRAM_API_KEY` と `LLM_PROVIDER`
に応じたAPIキーのみ設定してください。

Geminiのモデルを最新のPro系にしたい場合は、`.env` に以下を設定して再起動してください。

```bash
GEMINI_MODEL=gemini-3.1-pro-preview
```

## Docker マイクロサービス構成

```bash
# Docker Composeで起動
docker compose up --build
```

各サービスは `services/<name>/pyproject.toml` を持つ独立パッケージです。

### エンドポイント

| サービス            | URL                               |
| ------------------- | --------------------------------- |
| RTMP入力            | `rtmp://localhost:1935/live/zoom` |
| 字幕WebSocket       | `ws://localhost:8001/ws/caption`  |
| NMS管理画面         | `http://localhost:8000/admin`     |
| ngrokダッシュボード | `http://localhost:4040`           |

### 主なサービス

| サービス   | 説明                                     |
| ---------- | ---------------------------------------- |
| `nms`      | Node-Media-Server (RTMP受信)             |
| `ngrok`    | RTMPポートをインターネットに公開         |
| `deepgram` | RTMP → Deepgram ASR、結果をWS/翻訳へ中継 |
| `gemini`   | 翻訳API (FastAPI)                        |
| `ws`       | 字幕配信用WebSocket (FastAPI)            |

### ngrok設定（Zoomカスタムストリーミング用）

Zoomのカスタムストリーミングをローカル環境で受信するには、ngrokでRTMPポートを公開する必要があります。

1. **ngrokアカウント設定**
   - [ngrokダッシュボード](https://dashboard.ngrok.com/get-started/your-authtoken)から認証トークンを取得
   - [設定ページ](https://dashboard.ngrok.com/settings#id-verification)でクレジットカードを登録（TCPトンネル利用に必要、課金なし）

2. **環境変数設定**
   ```bash
   # .envファイルに追加
   NGROK_AUTHTOKEN=your_token_here
   ```

3. **トンネルURLの確認**
   ```bash
   # ngrokダッシュボードでトンネルURLを確認
   curl -s http://localhost:4040/api/tunnels | jq '.tunnels[0].public_url'
   # 例: tcp://0.tcp.jp.ngrok.io:12345
   ```

4. **Zoomでの設定**
   - Streaming URL: `rtmp://0.tcp.jp.ngrok.io:12345/live`
   - Streaming Key: `zoom`

### デバッグモード

WebSocketに流れるメッセージを確認するためのデバッグサービスを起動できます。

```bash
# デバッグモードで起動
docker compose --profile debug up -d

# デバッグログを確認
docker compose logs -f ws-debug

# デバッグモードを含めてすべて停止
docker compose --profile debug down
```

> **Note**:
> `ws-debug`は`profiles: [debug]`で定義されているため、通常の`docker compose down`では停止されません。必ず`--profile debug`を付けて停止してください。


### ローカルテスト（マイク → 字幕表示）

Zoomを使わずにローカルマイクから字幕を表示するテスト方法です。

#### 必要なもの
- ffmpeg（ホストマシンにインストール）
- Dockerが起動していること (docker compose build)

#### 手順

1. **サービスを起動**
   ```bash
   docker compose up -d
   ```

2. **マイク音声をRTMPにストリーム**
   ```bash
   # Linux (PulseAudio)
   ./scripts/stream_mic.sh
   # または直接実行
   ffmpeg -f pulse -i default -ac 2 -ar 44100 -c:a aac -b:a 128k -f flv rtmp://localhost:1935/live/zoom

   # macOS (AVFoundation)
   ffmpeg -f avfoundation -i ":0" -ac 2 -ar 44100 -c:a aac -b:a 128k -f flv rtmp://localhost:1935/live/zoom

   # Windows (dshow) - デバイス名は環境により異なる
   ffmpeg -f dshow -i audio="Microphone" -ac 2 -ar 44100 -c:a aac -b:a 128k -f flv rtmp://localhost:1935/live/zoom
   ```

3. **字幕ページを開く**
   ```bash
   open scripts/captions.html        # macOS
   xdg-open scripts/captions.html    # Linux
   start scripts/captions.html       # Windows

   # デフォルトのアプリがブラウザでない場合
   google-chrome scripts/captions.html
   firefox scripts/captions.html
   ```

4. **話しかけてテスト**
   - マイクに向かって英語で話す
   - 数秒後に日本語字幕が表示される
   - ページ上部にラグ統計が表示される

#### 字幕ページの見方

| 表示 | 意味 |
|------|------|
| Current Lag | 現在の遅延（秒） |
| Avg Lag | 平均遅延 |
| Max Lag | 最大遅延 |
| Messages | 受信メッセージ数 |
| Queue (proc/drop/sum) | 処理済/破棄/要約 の件数 |

ラグの色:
- 🟢 緑 (<3秒): 正常
- 🟡 黄 (3-7秒): 注意
- 🔴 赤 (>7秒): 問題あり

```

マイクロサービス用の環境変数例は `.env.example` に追加済みです。

Gemini利用時は `google.genai` (google-genai) のContext Cachingで
システムプロンプト/辞書をキャッシュし、LangChainの
`ChatGoogleGenerativeAI(cached_content=...)` 経由で参照します。 Context
Cacheは最小トークン数の制約があるため、プロンプトが小さい場合は 自動的に
`<cache_padding>` を付与してキャッシュを作成します。

翻訳はStructured Outputで `latest_slide`（最新の翻訳）と `kept_terms`
（固有名詞/曖昧語として保持した語）を返し、Webデモでは直近 `CONTEXT_WINDOW_SIZE`
文のスライドウィンド表示を行います。

## 開発

```bash
# テストの実行
uv run pytest

# コードフォーマット
uv run ruff format .

# リント
uv run ruff check .
```

## プロジェクト構造

```
src/real_time_translation/
├── audio/           # 音声取得モジュール
│   ├── __init__.py
│   └── capture.py   # AudioCapture, ZoomRTMSCapture
├── transcription/   # 文字起こしモジュール
│   ├── __init__.py
│   └── deepgram_client.py  # DeepgramTranscriber
├── translation/     # 翻訳モジュール
│   ├── __init__.py
│   └── llm_translator.py   # LLMTranslator (Gemini/OpenAI)
├── __init__.py
├── config.py        # 設定管理
├── gradio_demo.py   # Gradioデモ
├── main.py          # CLIエントリーポイント
└── pipeline.py      # パイプライン統合
services/
├── asr/
│   ├── pyproject.toml
│   └── src/asr_service/      # ASRサービス実装
├── translator/
│   ├── pyproject.toml
│   └── src/translator_service/ # 翻訳サービス実装
└── ws/
    ├── pyproject.toml
    └── src/ws_service/       # WebSocket配信サービス実装
```
