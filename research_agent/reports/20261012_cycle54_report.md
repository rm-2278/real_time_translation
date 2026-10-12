# サイクル54 結果レポート (2026-10-12)

## 概要: サイクル53の宿題(リサンプル版schematic adherence検証)を実行 → 再び結論不能、ただし原因は別物

本サイクルはスケジュール実行(cron)によるもので、`HUMAN_APPROVAL` 状態から
再開しました。既存の2件の `queued` 仮説のうち、より安価な
`h-judge-schematic-adherence-knowncost-resample`(推定$0.02)を先に実行
しました(`h-judge-cross-model-agreement-check` は$0.05でPLAYBOOKの
「安い方を優先」方針通り今回は見送り、引き続き `queued`/`approved_by_human`
のまま)。

## HUMAN_APPROVAL: 新規仮説1件を自動承認

`h-judge-schematic-adherence-knowncost-resample`(サイクル53の
`reflections.md` が要求した「既知のfidelityコストがあるサンプルで
再検証する」版)は `approval` 未設定・`status: proposed` のままでした。
`check-budget 0.02` は `AUTO_APPROVE` を返し、`uses_existing_clips: true`
のため、通常どおり `approval: auto_approved` / `status: queued` として
自動承認しました。

## RUN_EXPERIMENTS: 20件の新規judge呼び出しを実行($0.01)

環境チェック: `DEEPGRAM_API_KEY`・`GOOGLE_API_KEY` は設定済み、`ffmpeg` も
利用可能。この仮説はライブASRを使わない(既存の録音済み翻訳テキストへの
再judge呼び出しのみ)ため、Gemini REST到達性(200)のみ確認しました。

新スクリプト `judge_schematic_adherence_replay_knowncost.py` を実装しました。
サイクル53の `judge_schematic_adherence_replay.py` の `judge_decomposed()`・
`_pairwise_correlations()`・`_ols_r_squared()`・`SUB_CRITERIA` をそのまま
インポートして再利用し(プロンプトや統計コードは複製しない)、サンプル
抽出部分だけを新規実装しました。対象は
`experiments/20260916_compression_repeated_sampling_segment5.json`
(単一の固定ソースセグメントに対する `baseline_replay`/`compression_actions`
各10回の繰り返し翻訳、`truncation_rate_heuristic` が
`compression_actions` で0.4・`baseline_replay` で0.0という、既に記録済みの
fidelityコスト差がある実験)。20点(各条件10回ずつ)全てに対して新規
decomposed-judge呼び出しを実行し、20/20件が正常終了、実コストは$0.01と
記録しました(`uv run ruff check .` も通過)。

## ANALYZE_RESULTS: 再び結論不能 — ただし天井効果の原因はサイクル53と別物

`config` ドリフトのチェック: 本仮説はライブパイプライン設定を伴わない
(既存翻訳テキストへの再judge呼び出しのみ)ため、該当するconfig比較対象は
ありません。

結果は意図した検証には再び**結論不能**でした。20点全てで
`numbers_and_statistics`・`named_entities_and_technical_terms`・
`causal_logical_relationships` が100、`overall_completeness`/`score` も
19/20点で100(1点のみ90)と、分散がほぼゼロでした。結果、
`schematic_adherence_r_squared` は特異行列でnull、6つの下位基準間相関も
全てnullになり、サイクル53と同じ退化した形になりました。

ただし根本原因は異なります。ソースJSONを直接確認したところ、予測通り
`compression_actions` の10回中3回(インデックス0・1・5、`output_chars=12`、
`covers_both_clauses_heuristic=False`)は「course」節を欠落させた、まさに
検証したかった分散を持つケースでした — サンプル自体は正しく分散を含んで
いました。しかし本リプレイのdecomposed-judgeは、これら3件も含めて全て
100点を付けました。原因は、`input.target_segment_text` 自体が
`"course. So I think you already know what's"` という、前後の文脈を
持たない単独のASR断片であることです(サイクル53のスクリプトと同じ流儀で
`context_lines` を渡していません)。judgeは、既に曖昧な断片の中で情報が
欠落していても、それを「復元不能な文脈」として扱い、スコアできる欠落とは
見なさなかったようです。

興味深い点として、同じソースJSONに記録されている元の3件スポットチェック
(`judge_results`、単一ホリスティックスコアの旧式judge())は、全く同じ
切り捨てられた翻訳の1件を欠落ありとして50点と判定していました。つまり、
同一入力に対して単一呼び出しjudgeと分解ルーブリックjudgeが異なる結論を
出したことになります — これはサイクル19-21で既に確立されている
「judge呼び出しの再サンプリングノイズ」の一例として読むのが妥当で、
本手法固有の欠陥ではないと判断しました。

`hypotheses.json` の `status` は `tested`(手法の実装自体は健全で、文脈
なしの単独断片という選んだサンプルが今回の問いに不向きだった、という
結論自体は有効なため、`abandoned` ではありません)。

## コスト

今サイクルの支出: $0.01(新規Gemini judge呼び出し20件分)。本日の累計
$0.01 / 日次上限$7.00。バッチ上限$3.00にも十分収まっています。

## 人間への申し送り事項

- `h-judge-cross-model-agreement-check`($0.05、`queued`/`approved_by_human`)
  は今回も実行を見送りました(安価な方を優先したため)。緊急性は低く、
  PushNotificationは送っていません。
- schematic adherence診断は2サイクル連続で結論不能です。次に再挑戦する
  場合は、(a) `context_lines` を含む複数セグメントのスパンをサンプリング
  対象にする、かつ (b) 既知のfidelityコストが記録済みの実験
  (`h-compression-actions-prompt-instruction` や
  `h-semantic-completeness-gating` 系)から、孤立した断片ではなく文脈の
  ある翻訳をサンプリングすることを推奨します。

## 次サイクルへの申し送り

- バックログは `h-judge-cross-model-agreement-check`(ブロック中・$0.05)
  1件と、今回 `tested` になった
  `h-judge-schematic-adherence-knowncost-resample` のみ。`REFLECT` で
  次の一手(新規文献探索か、文脈ありサンプルでの3度目の試行を新仮説として
  書くか)を検討する。
