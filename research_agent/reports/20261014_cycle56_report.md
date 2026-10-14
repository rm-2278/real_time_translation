# サイクル56 結果レポート(2026-10-14, スケジュール実行)

## 今回やったこと

今回は新しい実験は走らせず、パイプラインの「事務処理」的な部分(文献の抽出・読解、
停滞していたhypothesisバックログの整理)を進めました。

### 1. 論文3件の抽出・読解(EXTRACT_PAPERS → READ_PAPERS)

サイクル55で見つかっていた3件の論文について、要旨と具体的な知見を抽出しました
(`arxiv.org`へのWebFetchは今回もDNSブロックされていたため、WebSearchフォールバックを使用)。

- **SimulS2ST-Omni**(arXiv 2607.19810): エンドツーエンドのストリーミング音声→音声翻訳。
  テキストと音響コードを1本の「コミットメント経路」として学習し、書き込み位置を単調増加に
  制約する手法。本リポジトリはDeepgram-ASR→プロンプトLLM翻訳のカスケード構成で学習は行わない
  ため、手法自体は転用不可。概念的な背景情報として記録。
- **RuVerBench**(arXiv 2606.29920): エージェント的タスクにおけるLLM判定者のルーブリック検証
  信頼性を評価する初のベンチマーク。強い判定者でも無視できないノイズがあること、バッチ検証は
  精度と効率のトレードオフがあること、多数決投票は効果があるが収穫減少であることを報告。
- **RubricEval**(arXiv 2603.25133): ルーブリック項目単位でのLLM判定者の精度を評価。GPT-4oでも
  Hardサブセットでは55.97%しか当たらない、ルーブリック単位の評価はチェックリスト単位より優れる、
  明示的な推論(score前にreasoningを書かせる)が精度を上げる、という知見。

**コード確認で分かったこと**: 本リポジトリの`translation_fidelity_judge.py`の判定プロンプト
(ホリスティック版・分解版の両方)は、スコアを出す前に明示的な推論を求める構造になっていません
(JSONを直接返すだけ)。RubricEvalの知見が当てはまるなら改善余地がありますが、今回は確認のみで
実装はしていません。

### 2. バックログの見直し(GENERATE_HYPOTHESES → HUMAN_APPROVAL)

新しい仮説は追加しませんでした。理由: バックログ上限(~6件、queued+proposed)を数えてみたところ、
**すでに上限に達していた**ことが判明しました。サイクル55のリフレクションでは「バックログは1件まで
減った」と記録されていましたが、これは`queued`状態のものだけを数えていたためで、実際には
`approval`が未設定(null)のまま放置されていた`proposed`状態の仮説が4件存在していました:

- `h-review-short-acronym-keyterm-false-positive`(人間レビュー由来、AIMEキーワードの誤認識調査)
- `h-review-perceived-drops-short-cards`(人間レビュー由来、字幕の「抜け」に見える箇所の調査)
- `h-computation-aware-laal-enja`(IWSLT相当のLAAL/StreamLAAL計算、既存実験の再評価)
- `h-interim-source-preview-overlay`(英語の暫定ASRテキストを薄く表示するUIプレビュー案)

この4件すべてについて`check-budget`を実行し、全てAUTO_APPROVE(コスト\$0〜\$0.05、
`uses_existing_clips=true`)だったため、`approval=auto_approved`/`status=queued`に更新しました。

さらに、`h-continuous-rating-cps-calibration`(人間による視聴評価セッションが必要な仮説)は
以前のサイクルで既に`approval=needs_human`になっていたにもかかわらず、`pending_approval.json`
への記録が漏れていたことが分かりました。つまり**人間に一度も通知されていなかった**ことになります。
今回、この記録漏れを修正し、`pending_approval.json`にエントリを追加しました。

### 3. 実験環境の再確認(RUN_EXPERIMENTS)

`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は設定済み、`ffmpeg`も利用可能、REST APIも両方200 OKでした。
しかし、Deepgramのlisten-websocketハンドシェイクは今回も失敗し、サイクル5以来ずっと同じ
エラー(`HTTP 400: Connection header did not include 'upgrade'`)でした。これはこのサンドボックスの
通信プロキシがWebSocketのアップグレードヘッダーを壊してしまう既知の問題で、APIキーやコードの
問題ではありません。

この結果、新しく承認された4件のうち、ライブASRが必要な`h-review-short-acronym-keyterm-false-positive`
は今回実行できません。残り3件(\$0、既存データのみで実行可能)は実行可能なはずですが、今回のサイクルは
文献処理とバックログ整理という単位の作業としてすでに十分な範囲だったため、実際の実装(特に
`h-computation-aware-laal-enja`は新しいスクリプトの作成が必要)は次回サイクルに持ち越すことにしました。
新しい実験は走らせていないため、何も捏造していません。

## 人間への確認が必要な項目(要注目)

**`h-continuous-rating-cps-calibration`** が`pending_approval.json`に正式に記録されました。
これは約20〜30分の視聴評価(同じ映像を2つの設定で見て1-4のキー評価をつける)が必要な仮説です。
これまで記録漏れで通知されていなかったため、今回のレポートで明示します。対応が必要な場合は
`pending_approval.json`を編集するか、次回のやり取りで直接指示してください。

## 次回への引き継ぎ

- 実行可能でコスト\$0の仮説が3件キューにあります(`h-review-perceived-drops-short-cards`,
  `h-computation-aware-laal-enja`, `h-interim-source-preview-overlay`のStep 1)。次回サイクルは
  これらのいずれかを実装・実行するのが最優先です。
- Deepgram WebSocketのブロックは変わらず解消されていません(サイクル5から変化なし)。
- `translation_fidelity_judge`のプロンプトが推論を事前に求めていない件(RubricEvalの知見との関連)は、
  コード上確認済みですが、仮説化はまだしていません。
- RuVerBenchから得られた2つの未検証ギャップ(判定リピート数を2回より増やした場合の収穫減少、
  バッチ判定の精度・効率トレードオフ)も次回のGENERATE_HYPOTHESESでの候補です。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません。
