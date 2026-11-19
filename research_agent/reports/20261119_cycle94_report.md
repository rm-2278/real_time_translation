# サイクル94 結果レポート (2026-11-19)

## 概要: 新規仮説(CAALの回避可能/不可能遅延分解)を承認・実行、$0のretroactive分析で「遅延の約85%は回避可能」という結果

本サイクルはスケジュール実行(cron)によるもので、`current_state: HUMAN_APPROVAL`
(サイクル93の `REFLECT` が `GENERATE_HYPOTHESES` へ直接進めた後、既に1件の
新規仮説が追加済みの状態)から再開しました。本セッションでは
`HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS` まで進め、本レポート
(`WRITE_REPORT`)を作成しています。

## HUMAN_APPROVAL: `h-caal-avoidable-unavoidable-al-decomposition` を自動承認

前サイクル(93)の `REFLECT` で候補として挙がっていた `caal2609` のCAAL
(Causality-Aware Average Lagging)分解アイデアが、サイクル94の
`GENERATE_HYPOTHESES` で実際に仮説化されていました(`approval: null`,
`status: proposed`)。

- `check-budget 0.0` → `AUTO_APPROVE`
- `uses_existing_clips: true`
- 新規API呼び出し・新規ダウンロードなし(既存の実験JSON群に対する
  retroactive分析のみ)

PLAYBOOK.md通常経路(AUTO_APPROVE + uses_existing_clips)に従い、
`approval: auto_approved`, `status: queued` に更新しました。
`pending_approval.json` に記録済みの別件
(`h-judge-repeat-independent-batches-corrected-design`、自己承認が
過去に実行環境のポリシーでブロックされた前例あり)は対象外(すでに
`needs_human` で確定済み)のため、本サイクルでは触れていません。

## RUN_EXPERIMENTS: CAAL分解スクリプトを実装・全105実験に適用

新規スクリプト `src/real_time_translation/experiments/caal_decomposition.py`
を実装。既存の3モジュールを再利用(再実装はしない)してバッチ単位で
結合:

- `mt_latency_decomposition.py` の `queue_wait_s`(スケジューリング待ち
  = 回避可能)・`stream_duration_s`(LLMトークン生成時間 = 回避不可能)
- `utterance_end_mix.py` の `is_utterance_end` 分類(固定タイマーによる
  soft-finalize = 回避可能、本物の発話終端検出 = 回避不可能)
- `simul_latency_metrics.py` の `ca_al_char_s`(クロスリファレンスのみ、
  計算には使わない)

**結合キーの実機検証(PLAYBOOK.mdの標準手順)**で、想定外の問題を発見・
修正しました: `batch_latencies()` は位置ベースでグループ化するのに対し
`classify_batches()` は辞書の同一性でグループ化するため、同一の
`(asr_start_time, asr_end_time)` キーが1実行内で非連続に再出現するケース
(110本中2本のJSONで実際に発生)で、両者のグループ化がズレて誤った
フラグ付けが起きる危険がありました。位置ベースの分類関数
(`_classify_contiguous_batches`)を新規実装し、`batch_latencies()`と
キー単位で完全一致することを実行時アサーションで保証する形に修正、
全105実験JSONでクリーンに通過することを確認しました。

ユニットテスト7件(soft-finalize/genuine-final両方のケースを含む合成
バッチ列)を追加、`uv run ruff check .` もクリア。$0のretroactive分析
のため `log-cost 0` で記録のみ。`hypotheses.json` の
`experiment_ids` を `experiments/caal_decomposition.csv` に設定し、
`status: testing` としてコミット(分析完了前にコミットする標準規則通り)。

## ANALYZE_RESULTS: 予測はおおむね確認、ただし新たな未解明の例外とデータ欠損の留保あり

既存の105本の実験JSON(イベントを持つもの全て)を対象に分析:

- `avoidable_fraction`(回避可能な遅延の割合): 平均 **0.852**、
  中央値 **0.950**、最小0.268、最大0.998
- **94/105件(89.5%)が0.5超**— 仮説の予測(「本リポジトリの
  レイテンシギャップの大半は、変更可能なスケジューリング/タイマー
  ポリシーに起因し、アーキテクチャ上の限界ではない」)をおおむね支持
- 予測されていた例外(厳しい `gemini_rpm_limit` がレート制限による
  人為的な `queue_wait_s` 膨張を起こし、これは「自由に選べる
  スケジューリング」とは異なる種類の「回避可能」である、という
  ケース)は、0.5未満の11件のうち6件(すべて `rpm=9`)で該当を確認。
  **残り5件は `rpm=60`(通常のレート制限下)でも0.5未満**であり、
  これは予測で想定されていなかった未解明の現象です。rpm以外の要因
  (該当run: `llm2024_8_part1_full`,
  `llm2024_8_part1_full_baseline_no_dict`, `post_dedup_fix_check`,
  `hardest_5min_gpt54nano`, `calibration_rpm60`)を、同じrpm制限の
  バケットに無理に押し込めず、別の未解決の問いとして明示的に
  切り分けました。
- **新たに発見したデータ完全性の留保**: 1run平均で全バッチの約
  **44.2%**(0〜93%の範囲)が、`mt_latency_decomposition.py` の
  `content_ready_wallclock` 検索が対応する `asr_interim` イベントを
  見つけられないという理由で、集計から除外されています。つまり
  `avoidable_fraction` は各runの全バッチの「センサス(全数)」では
  なく、残り半分程度の「サンプル」から計算された値である点を、
  今回の結果の読み方として明記しました。

`hypotheses.json` の該当エントリは `status: tested`、具体的な数値を
含む `result_summary` を記載済みです。

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

- 今回判明した **rpm=60でも avoidable_fraction が0.5未満になる5件**
  の原因調査は、$0のretroactive分析候補として次回GENERATE_HYPOTHESESで
  優先的に検討する価値があります(キュー枠が1件 `tested` になった
  ことで空きができています)。
- `content_ready_wallclock` が約44%のバッチで見つからない原因の調査も、
  同様に$0で着手可能な候補です(現状の分析結果の信頼性に直接関わる
  ため優先度は高めに検討)。
- バックログは `queued` 4件+`proposed` 0件(本サイクルで1件が
  `tested` に移行)。
