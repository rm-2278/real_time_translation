# AGENTS.md

このプロジェクトはuvを使用したPythonプロジェクト管理を行っています。

## プロジェクト構造

```
real_time_translation/
├── src/real_time_translation/   # メインパッケージ（モノリシック実行）
│   ├── audio/                   # 音声取得モジュール
│   ├── transcription/           # Deepgram文字起こし
│   ├── translation/             # LLM翻訳・辞書・ドメインパック
│   ├── preload/                 # スライド/動画からの事前用語抽出
│   ├── captions/                # 字幕の整形（オフライン）とライブ表示
│   ├── experiments/             # 精度実験ランナー・分析スクリプト（下記参照）
│   ├── web/                     # オーバーレイ字幕UI (overlay.html)
│   ├── config.py                # 設定管理
│   ├── pipeline.py              # パイプライン統合
│   ├── gradio_demo.py           # Gradio Webデモ
│   └── main.py                  # CLIエントリーポイント
├── services/                    # マイクロサービス実行（Docker Compose）
│   ├── asr/                     # Deepgram文字起こしサービス
│   ├── translator/              # LLM翻訳サービス
│   └── ws/                      # WebSocket配信サービス
│   # services/* は src/ と意図的にコード共有していない独立デプロイ単位
│   # （機能が分岐しているため、構造リファクタで自動マージはしない）
├── scripts/                     # スタンドアロンの運用/デバッグツール
├── docs/                        # 設計書・変更履歴（コードから参照されない文書）
├── experiments/                 # 実験結果 (JSON/CSV/字幕) の出力先（データのみ）
├── dictionaries/                # ドメイン別・セッション別の用語辞書CSV
├── tests/                       # テストファイル
├── pyproject.toml               # プロジェクト設定
└── .env.example                 # 環境変数テンプレート
```

## パッケージマネージャ: uv

```bash
# 依存関係のインストール
uv sync --extra dev

# 依存関係の追加
uv add <package-name>
uv add --dev <package-name>

# スクリプトの実行
uv run real-time-translation
uv run pytest
uv run ruff check .
```

## 主要コンポーネント

| モジュール                         | 役割                             |
| ---------------------------------- | -------------------------------- |
| `audio/capture.py`                 | Zoom RTMS SDKからの音声取得      |
| `transcription/deepgram_client.py` | Deepgram WebSocket文字起こし     |
| `translation/llm_translator.py`    | LangChain + Gemini/OpenAI翻訳    |
| `translation/prompts.py`           | LLMに渡すプロンプト文面（純粋な文字列） |
| `transcription/dangling.py`        | ASR確定チャンク末尾の宙ぶらりん語の検出 |
| `fragment_queue.py`                | 翻訳待ちチャンクの有界FIFO（peek付き） |
| `pipeline.py`                      | 音声→文字起こし→翻訳のフロー制御 |
| `config.py`                        | 環境変数からの設定読み込み       |

## コーディング規約

- **フォーマッター**: Ruff (line-length: 88)
- **リンター**: Ruff (E, F, I, UP, B, SIM)
- **型ヒント**: 必須
- **非同期**: asyncio使用

## 注意事項

- `uv.lock`は自動生成、手動編集不可
- 依存関係は`uv add`コマンドで追加
- APIキーは`.env`ファイルで管理（gitignored）

## experiments/ の構成（意図的にフラット）

`src/real_time_translation/experiments/` は分野別のサブパッケージに分けない。
過去の実験JSONと `research_agent/` の履歴が
`python -m real_time_translation.experiments.<name>` という再現コマンドを
記録しており、モジュールを動かすとそれらが壊れるため。

共通処理は次の3つに集約している（スクリプト間でコピーしない）：

| モジュール   | 内容                                           |
| ------------ | ---------------------------------------------- |
| `stats.py`   | mean / median / stdev / pearson_r（空→None）   |
| `media.py`   | 時刻パース、ffmpegでPCM抽出                    |
| `results_io.py` | experiments/ のJSON・CSVの読み書き、chrF    |

ルートの `dictionary.csv` は残す: Dockerfile / docker-compose / 多数の実験の
既定パスが参照しているため。
