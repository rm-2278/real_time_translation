# サイクル96 結果レポート (2026-11-20)

## 概要: サイクル95の `content_ready_wallclock` 修正を、積み残しだった下流3仮説に反映。結論の方向性は維持だが、具体的な数値・一部の定量的主張は更新が必要

本サイクルはスケジュール実行(cron)によるもので、`current_state: GENERATE_HYPOTHESES`
(サイクル95の `REFLECT` が `GENERATE_HYPOTHESES` へ直接進めた直後、バックログに
1枠の空きがある状態)から再開しました。本セッションでは
`GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS`
まで進め、本レポート(`WRITE_REPORT`)を作成しています。

## GENERATE_HYPOTHESES: `h-content-ready-fallback-downstream-revalidation` を追加

サイクル95の`REFLECT`が明示していた申し送り事項(`word_order_reordering_proxy.py`/
`post_gap_queue_wait.py` の再実行候補)を採用しました。この2スクリプトは
いずれも `mt_latency_decomposition.batch_latencies()` を直接importしており
(ロジックの重複なし、本サイクルで両ファイルのimportを再確認済み)、
サイクル95で修正された結合ロジック(直前interimへのフォールバック)の
影響をそのまま受けます。

対象となる既存の3仮説(`h-enja-word-order-reordering-proxy-latency`,
`h-mt-queue-wait-post-gap-residual`, `h-post-gap-queue-wait-persistence`)は
いずれも自身の `result_summary` の中で「サンプルが少ない」「検出力不足」
といった、今回の修正が直接解消する限界を明示していたため、再検証の
優先度が高いと判断しました。$0・既存clip再利用のみ・retroactive分析として
`hypotheses.json` に追加し、バックログは `queued` 5件 + `proposed` 1件 = 6件
(上限)に到達しました。

## HUMAN_APPROVAL: 自動承認

- `check-budget 0.0` → `AUTO_APPROVE`、`uses_existing_clips: true`
- 通常の初回AUTO_APPROVE書き込み(サイクル93で確認済みの「自己承認ブロック」
  とは異なるケース)のため、問題なく `approval: auto_approved`,
  `status: queued` に更新しました。
- `pending_approval.json` 記載の別件
  (`h-judge-repeat-independent-batches-corrected-design`)は対象外、変更なし。

## RUN_EXPERIMENTS: コード変更なしで2スクリプトを再実行

`post_gap_queue_wait.py`・`word_order_reordering_proxy.py` 自体への
コード変更は不要(既に `batch_latencies()` を再利用する設計のため)。
全151本の実験JSONに対してそのまま再実行し、`experiments/post_gap_queue_wait.csv`,
`experiments/post_gap_queue_wait_persistence.csv`,
`experiments/word_order_reordering_proxy.csv` を再生成しました。
$0、新規Deepgram/LLM API呼び出しなし(`log-cost 0` で記録)。

## ANALYZE_RESULTS: 定性的な結論は維持されるが、一部の定量的主張は後退

**`h-mt-queue-wait-post-gap-residual`(ギャップ直後1バッチのみ)**:

- `llm2025_ep8_zenhan_full_lecture`: ギャップ解決数 5/8→**6/8**、
  post-gap平均 7.92秒→6.84秒。ただし **baseline平均がほぼ倍増**
  (1.35秒→2.28秒、フォールバック由来のバッチが平均して高めの
  `queue_wait_s` を持つという、サイクル95の予測通りの効果)。
  結果、残差説明率は **54.9%→45.8%** に低下。
- `_v2_dict`: ギャップ解決数 6/7→**7/7(完全解決)**、post-gap平均
  6.15秒→5.52秒、baseline平均 1.26秒→2.21秒、残差説明率
  **39.7%→31.5%** に低下。

**`h-post-gap-queue-wait-persistence`(複数バッチへの累積)**:

- `llm2025_ep8_zenhan_full_lecture`: 累積説明率 **74.6%→60.5%**
  (過半数は維持)。
- `_v2_dict`: 累積説明率 **54.7%→42.1%**
  (**過半数を下回った** — 従来の報告「両方の実行で過半数を説明」は
  **1件のみ**に後退)。

**定性的結論への影響**: 「ギャップ直後のMTキュー待ちは本物で、残差の
大部分を説明する」という方向性自体は維持されますが、「両方の実行で
過半数を説明する」という具体的な主張は1件のみに後退し、未説明の
残差は従来の報告(25-45%)より大きい(約40-55%)と見るべきです。

**副次的な注意点(本仮説の範囲外、対処はせず記録のみ)**: 付随的な
参考実行2件(`full_llm2025_zenhan_gemini3flash_s0`,
`full_llm2025_zenhan_gpt54mini_s0`)の残差説明率が、従来の0.18/0.25から
**1.015/0.990(100%超)** という不自然な値になりました。これは
baseline平均の上昇効果とギャップ件数の少なさ(5件・6件)が重なった
結果とみられ、本物の知見ではなくデータの性質によるアーティファクトと
判断しています。これらはもともと cycle-61 の本題とは無関係な
付随的事例として報告されていたものなので、今回は訂正のみ記録し、
深追いしていません。

**`h-enja-word-order-reordering-proxy-latency`**:

- 主結果(`verb_position_ratio` vs `stream_duration_s`、n=2586、
  `content_ready_wallclock` に依存しない指標)は予測通りほぼ不変
  (pearson_r 0.049→0.049、spearman_r 0.057→0.057) — 変わらず
  明確なnull結果。
- 副次結果(`verb_position_ratio` vs `queue_wait_s`)が今回の
  ハイライト: サンプル数が **n=157→n=2503**(約16倍、全体の解決率が
  約14%→約99%に上がったことに対応)に増え、2つの相関係数の**符号が
  初めて一致**(pearson_r=-0.027、spearman_r=-0.026、どちらも弱い負)。
  従来の「符号不一致・検出力不足」という曖昧な状態が、主指標と
  同じ明確なnull結果に解消されました。これは結論を**強化**するもので、
  逆転ではありません。

**まとめ**: 3仮説とも定性的な方向性(結論の大枠)は維持されますが、
`h-post-gap-queue-wait-persistence` の「両実行で過半数」という
定量的主張と `h-mt-queue-wait-post-gap-residual` の具体的な数値は
サイクル96のこの再計算結果に置き換えるべきです。一方
`h-enja-word-order-reordering-proxy-latency` は、曖昧だった副次結果が
明確なnullへと強化され、結論の確信度が上がりました。詳細な数値は
`hypotheses.json` の `h-content-ready-fallback-downstream-revalidation`
エントリの `result_summary` に記載済みです(元の3仮説自体の
`result_summary` は、サイレントな書き換えを避けるため今回は変更せず、
本レポートとこのエントリを「正」の再検証記録としています)。

## 人間への確認依頼

以下は引き続き rm-2278 さんの判断/作業待ちです(新規なし、前サイクル
からの継続):

1. `pending_approval.json` の1件
   (`h-judge-repeat-independent-batches-corrected-design`)— 自己承認が
   実行環境のポリシーでブロックされているため、承認するかどうかの
   判断をお願いします。
2. `h-continuous-rating-cps-calibration` — 視聴・評価時間と元動画ファイル
   をご用意いただけると実行できます。
3. `h-soft-anchor-gate-exempt-utterance-end`(Gemini系のみで実行可能、
   $0.35、未着手)の優先順位について。

## 次サイクルへの申し送り

- `h-mt-queue-wait-post-gap-residual` と `h-post-gap-queue-wait-persistence`
  自体の `result_summary` を、今回の再検証結果に合わせて更新するかどうかの
  判断(今回は意図的に元エントリへの書き換えは行わず、新規エントリと
  本レポートに留めた)。
- 付随実行2件の残差説明率が100%を超えた件について、原因をもう一段
  掘り下げる価値があるか(優先度は低いと判断)。
- サイクル94から引き続き未解明の「rpm=60でも `avoidable_fraction` が
  0.5未満になる5件」の原因調査は、依然として$0の候補として残っています。
- バックログは `queued` 5件 + `proposed` 1件 = 6件(上限)。次サイクルは
  新規仮説の追加より、既存の `proposed`/`queued` の処理を優先してください。
