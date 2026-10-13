# プロジェクト定義書 (Project Definition)

> このドキュメントはプロジェクトの目的・スコープ・成功指標など、
> アーキテクチャ詳細（[DESIGN_DOC.md](DESIGN_DOC.md)）やセットアップ手順（[README.md](../README.md)）
> ではカバーされない「なぜ・何のために作るか」を定義する。

## 1. プロジェクト概要

| 項目 | 内容 |
| --- | --- |
| 名称 | Real-Time Translation (`real-time-translation`) |
| 一言で | Zoomミーティング / YouTube音声などをリアルタイムに文字起こし・翻訳し、低遅延で字幕表示するSimulSTパイプライン |
| 対象言語 | 既定: 英語(en) → 日本語(ja)（`SOURCE_LANGUAGE` / `TARGET_LANGUAGE` で変更可） |
| 主要技術 | Deepgram (ASR) + Gemini 2.5 Flash / OpenAI (LLM翻訳) + asyncio |

## 2. 目的・背景

専門用語（機械学習など）を含む英語音声の講義・会議を、聞き手が日本語でリアルタイムに理解できるようにする。
単純な逐語訳ではなく、直近の文脈を踏まえた翻訳と、固有名詞・専門用語辞書の活用により、
ASRの誤認識や曖昧語をカバーしながら実用的な精度と体感速度を両立させることを目指す。

## 3. スコープ

### 対象範囲 (In Scope)

- 音声入力: Zoom RTMS / Webマイク（Gradioデモ）/ YouTube指定区間（実験用）
- ASR: Deepgram WebSocketによるストリーミング文字起こし（`is_final`確定 + interim表示）
- 翻訳: LLM（Gemini or OpenAI）によるスライディングウィンドウ文脈翻訳、Structured Output
- 用語辞書: CSVベースのkeyterm/固有名詞管理（`dictionary.csv`）
- 配信: WebSocket字幕配信、Zoom字幕API連携
- マイクロサービス化: `services/asr`, `services/translator`, `services/ws` の独立デプロイ（Docker Compose）
- 精度・遅延評価: `experiments/` 配下のYouTubeセグメント実験ランナー、chrF計測、endpointing sweep

### 対象外 (Out of Scope, 現時点)

- 話者分離 (speaker diarization) — DESIGN_DOC.mdの拡張候補にとどまる
- 3言語以上の同時翻訳
- リアルタイム音声合成 (TTS) による吹き替え
- 会議の要約・議事録生成（Gemini拡張候補としては検討中）

## 4. 想定利用シーン / ステークホルダー

- 大学講義・セミナー（機械学習など専門分野）の同時通訳字幕
- Zoomミーティングでの多言語コミュニケーション支援
- 開発者: rm-2278

## 5. 成功指標

| 指標 | 目標 / 目安 | 計測方法 |
| --- | --- | --- |
| 字幕表示までのラグ | Current Lag < 3秒（緑）を維持 | `scripts/captions.html` のラグ統計、`/stats`エンドポイント |
| 翻訳品質 | chrFスコアで比較・退行検知 | `experiments/results.csv`（`--reference-ja`指定時） |
| 翻訳スループット | 詰まり・ドロップなし（キューサイズ0付近を維持） | `TranslationQueueManager` stats（processed/dropped/queue_size） |
| ASR確定の妥当性 | endpointing/utterance_end設定のsweep結果で選定 | `experiments/sweep_endpointing.sh`、`20260415_ep_sweep_*ms.json` |

実験を行った場合は [CLAUDE.md](CLAUDE.md) の実験記録ルールに従い、
`experiments/YYYYMMDD_<実験名>.json` の作成・`results.csv`への追記・コミットを必須とする。

## 6. システム構成（要約）

```
Zoom RTMS / Web Mic / YouTube
        ↓ (audio stream)
   Deepgram ASR (is_final確定)
        ↓ (final transcript)
   Prompt Builder (context window + dictionary)
        ↓
   Gemini 2.5 Flash / OpenAI (structured output)
        ↓
   WebSocket 字幕配信 / Gradio UI / Zoom字幕
```

モノリシック実行（`src/real_time_translation/`）と、マイクロサービス実行
（`services/asr`, `services/translator`, `services/ws` + Docker Compose）の
2通りの起動方法をサポートする。詳細アーキテクチャ・コンポーネント設計は
[DESIGN_DOC.md](DESIGN_DOC.md) を参照。

## 7. 制約・前提条件

- Python 3.13以上、パッケージ管理は `uv`（[AGENTS.md](AGENTS.md)参照）
- 必須APIキー: `DEEPGRAM_API_KEY`、`LLM_PROVIDER`に応じた `GOOGLE_API_KEY` または `OPENAI_API_KEY`
- ホストに `ffmpeg` が必要（マイクストリーミング・実験ランナー共通）
- Zoom RTMS利用時は `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` が必要（Webデモでは不要）
- Gemini利用時はContext Cachingが前提（辞書更新時はキャッシュ再生成が必要）
- YouTube等外部コンテンツの利用は各サービスの利用規約・権利関係を遵守すること

## 8. 現状のステータス

- 作業ブランチ: `feat/rm2278/async-containers`
- 直近の主な変更（新しい順）:
  - LLMシステムプロンプトのストリーミング断片最適化、実験へのDeepgram endpointing設定サポート追加
  - Deepgram SDK 3+への移行、タイミングメタデータの伝播、ASR誤り訂正を意識した翻訳プロンプト改善
  - Deepgramキープアライブを `ListenV1ControlMessage` / `send_control` 方式に更新
  - Geminiモデルを2.5-flashへアップグレード
- 2026-02-04〜05に翻訳遅延の蓄積問題（30分講義で10分以上の遅延）を並列ワーカー化で解消済み（詳細: [CHANGELOG_2026-02-04.md](CHANGELOG_2026-02-04.md)）

## 9. 今後の拡張候補

- RAG: 専門用語辞書・会議録のVector検索による動的用語注入
- 話者分離 (Speaker Diarization) の導入
- 代替ASR（Aqua Voice / AssemblyAIなど）の評価・切替可能な抽象化
- WebRTC化によるRTMP廃止、Redis/Kafkaによる多会議スケール
- Gemini等による要約・議事録生成

## 10. 関連ドキュメント

- [README.md](../README.md) — セットアップ・使い方・環境変数一覧
- [DESIGN_DOC.md](DESIGN_DOC.md) — アーキテクチャ詳細設計
- [AGENTS.md](AGENTS.md) — プロジェクト構造・開発規約（uv, Ruff等）
- [CLAUDE.md](CLAUDE.md) — 翻訳精度比較実験のロギングルール
- [CHANGELOG_2026-02-04.md](CHANGELOG_2026-02-04.md) — 遅延修正の変更履歴
