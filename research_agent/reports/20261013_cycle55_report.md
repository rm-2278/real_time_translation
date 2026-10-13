# サイクル55 結果レポート (2026-10-13)

## 概要: schematic adherence診断の3度目の挑戦 → ついに結論が出た

本サイクルはスケジュール実行(cron)によるもので、`GENERATE_HYPOTHESES` 状態
から再開しました。サイクル53・54でともに「分散ゼロ」の結論不能な結果と
なっていた schematic adherence(ルーブリック分解)診断について、サイクル
54の `reflections.md`/レポートが明示的に推奨していた「文脈のある複数セグ
メントのスパンを、既に非天井効果のfidelity差が確認済みの実験からサンプリ
ングする」方針に沿って3度目の挑戦を行い、今回初めて意味のある結果が得られ
ました。

## GENERATE_HYPOTHESES: 新規仮説1件を追加

`h-judge-schematic-adherence-sliding-context-windows` を追加しました。
対象データは `experiments/20260916_compression_actions_prompt.json`
(h-compression-actions-prompt-instruction、23セグメントの同一120秒クリッ
プを `baseline_replay`/`compression_actions` の2条件で逐次翻訳したリプレ
イ実験)です。この実験は既存の一括(holistic)judge呼び出しで既に
`retention_score` 75 対 85、条件ごとに異なる `dropped_or_distorted` リス
トという、天井効果ではない実差が記録済みでした。4セグメント連続のスライ
ディングウィンドウ(ソース文・訳文をウィンドウ内で連結)を各条件20個、合
計40点抽出する設計とし、バックログはこれと `h-judge-cross-model-agree
ment-check`(承認済み・$0.05、未実行)の2件のみ、上限6件に対して十分余裕
がありました。

## HUMAN_APPROVAL: 自動承認($0.02)

`check-budget 0.02` は `AUTO_APPROVE`、`uses_existing_clips: true` のため
通常どおり自動承認しました(`h-judge-cross-model-agreement-check` は今回
も見送り)。

## RUN_EXPERIMENTS: 40件の新規judge呼び出しを実行($0.02)

環境チェック: `DEEPGRAM_API_KEY`・`GOOGLE_API_KEY` 設定済み、`ffmpeg` 利
用可能、Gemini REST到達性200。本仮説はライブASRを使わないため、これで十
分です。

新スクリプト `judge_schematic_adherence_replay_context_windows.py` を実
装しました。サイクル54の `judge_schematic_adherence_replay_knowncost.py`
と同様、`judge_schematic_adherence_replay.py` の `judge_decomposed()`・
`_pairwise_correlations()`・`_ols_r_squared()`・`SUB_CRITERIA` をそのまま
インポートして再利用し、サンプル抽出部分だけ新規実装しました。40件全て
が正常終了し、実コストは$0.02と記録しました(`uv run ruff check .` 相当
の `python3 -m ruff check` も通過)。

## ANALYZE_RESULTS: ついに結論が出た

ライブパイプライン設定を伴わない純粋なリプレイのため、configドリフトの
チェック対象はありません。

結果は意図した検証に**結論を出すことができました**。40点中、
`numbers_and_statistics` はこのクリップにほぼ数値情報が無いためほぼ100
のままでしたが、`named_entities_and_technical_terms`・
`causal_logical_relationships`・`overall_completeness` の3基準では、
0から100まで本物の分散が見られました。低スコアのウィンドウ(両条件とも
window_start 7-10・17-19)は judge のノイズではなく、ソースJSONを直接確
認したところ、これらは実際に乱れた/不明瞭なASR原文("we just have a
small three"、"token errors"、21・22番セグメントで"FFN layer. Normally,
FFN layer"が2回連続するなど)に対応しており、同じ実験の既存の一括judge
呼び出しがすでに `dropped_or_distorted` として指摘していた箇所と一致し
ていました。

2つの診断結果:
1. 下位基準間の相関は全てが0.9超というわけではありませんでした
   (`numbers_and_statistics` と他3基準はほぼゼロ〜わずかに負、
   `named_entities` と `causal_logical`/`overall_completeness` は
   中程度の0.51/0.62、`causal_logical_relationships` と
   `overall_completeness` のみ高い0.95でした — 因果関係の欠落は通常
   全体的な完全性の低下も伴うため、これは納得のいく結果です)。
2. `schematic_adherence_r_squared=0.996` という、実数でnullではない非常
   に高い値が得られました。つまり、一括スコア(`score`)はほぼ完全に4つの
   下位基準で説明できており、このサンプルにおいてはルーブリックが「崩壊」
   しておらず、スキーマとして整合的であることが確認されました。

結論: arxiv2509-20293のルーブリック分解診断は、本リポジトリのjudgeに対し
て(a)ソース・訳文の両方に複数文にわたる本物の文脈があり、かつ(b)サンプル
自体に実際の品質差(乱れたASR区間など)が含まれている場合には、有効に機能
することが確認されました。サイクル53・54でそれぞれ診断された2つの異なる
根本原因(収束済み最終テキスト/文脈のない単独断片)は両方とも回避できてい
ます。ただし、この結果は今回の1クリップの乱れ方に特有のものであり、他の
クリップ/ドメインへの一般化は未確認です。`hypotheses.json` の `status` は
`tested` です。

## コスト

今サイクルの支出: $0.02(新規Gemini judge呼び出し40件分)。本日の累計
$0.02 / 日次上限$7.00。バッチ上限$3.00にも十分収まっています。

## 人間への申し送り事項

- `h-judge-cross-model-agreement-check`($0.05、`queued`/`approved_by_human`)
  は今回も見送りました。緊急性は低く、PushNotificationは送っていません。
- schematic adherence診断の3サイクルにわたる調査はこれで一区切りです。
  次に発展させる場合は、他のクリップ/ドメインへの一般化確認、または
  このR^2/相関構造自体を使って実際のjudgeプロンプト改善(例:
  `causal_logical_relationships` が低いときにholisticスコアを自動的に
  下げるようガイドする、など)につなげることを検討できます。

## 次サイクルへの申し送り

- バックログは `h-judge-cross-model-agreement-check`(未実行・$0.05)
  1件のみ。文献探索はサイクル53が最後(2サイクル前)で、やや古くなって
  きています。`REFLECT` で、次を新規文献探索(SEARCH_PAPERS)にするか、
  このschematic adherenceの結果を踏まえた新しい具体的な仮説
  (GENERATE_HYPOTHESES直行)にするかを検討します。
