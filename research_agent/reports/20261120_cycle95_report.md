# サイクル95 結果レポート (2026-11-20)

## 概要: `content_ready_wallclock` 欠損(前サイクルで発見)の原因を特定・修正し、依存する2分析を再計算した結果、一部の数値がサイクル94の報告から有意にシフト

本サイクルはスケジュール実行(cron)によるもので、`current_state: GENERATE_HYPOTHESES`
(サイクル94の `REFLECT` が `GENERATE_HYPOTHESES` へ直接進めた直後、
バックログに1枠の空きがある状態)から再開しました。本セッションでは
`GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS`
まで進め、本レポート(`WRITE_REPORT`)を作成しています。

## GENERATE_HYPOTHESES: `h-content-ready-wallclock-nearest-interim-fallback` を追加

サイクル94の`REFLECT`が「次サイクルの高優先度候補」として明示していた
2つの角度(rpm=60でも `avoidable_fraction` が0.5未満になる5件の原因、
および `content_ready_wallclock` の約44%欠損の原因)のうち、後者を
採用しました(現在の全分析結果の信頼性に直接関わるため)。

コードを読む前に実データで検証(PLAYBOOK.mdの標準手順)した結果:

- `mt_latency_decomposition.py` の `_content_ready_lookup()` は、
  バッチの `asr_end_time` と**完全一致**する `asr_interim` イベントのみ
  参照する設計でした。
- `video_segment.py`/`youtube_segment.py` の `on_result()` は
  `result.is_final` が `False` のときのみ `kind="asr_interim"` を発行する
  ため、Deepgramが発話を確定(finalize)した時点の `asr_end_time` が、
  それ以前のどのinterimの `asr_end_time` よりも少し先(エンドポインティング
  の「確定待ち」区間ぶん)に進んでいると、完全一致する interim が
  存在しません。
- 全110本の実験JSON・12,380バッチを実測: `is_utterance_end=False` の
  バッチは一度も欠損せず(0/2,839)、`is_utterance_end=True` は86%
  (4,674/5,426)、未設定は41%(1,704/4,115)が欠損。欠損6,378件のうち
  **98%(6,249件)は直前のinterimが実在**し、そのinterimとの
  `asr_end_time` の差は中央値約0.6秒(`deepgram_max_interim_duration`=2.5秒
  を超えるのはわずか0.22%)。本当に回復不能(直前interimが一切ない)なのは
  全バッチの約1.0%(129件、すべて実行の最初のバッチ相当)のみでした。

この実測結果をもとに、「直前の最も近いinterimの `playback_offset` へ
フォールバックする」修正方針を仮説としてまとめ、`hypotheses.json` に
追加しました($0、既存clip再利用のみ、retroactive分析)。
バックログは `queued` 5件 + `proposed` 1件 = 6件(上限)に到達しました。

## HUMAN_APPROVAL: 自動承認

- `check-budget 0.0` → `AUTO_APPROVE`、`uses_existing_clips: true`
- これは「自己承認ブロック」の前例(`pending_approval.json`からの
  `needs_human`解除)とは異なる、通常の初回AUTO_APPROVE書き込みのため
  (サイクル93で確認済みの区別)、問題なく `approval: auto_approved`,
  `status: queued` に更新しました。
- `pending_approval.json` 記載の別件
  (`h-judge-repeat-independent-batches-corrected-design`)は対象外、
  変更なし。

## RUN_EXPERIMENTS: フォールバック実装 + 全151実験への再適用

`mt_latency_decomposition.py` の `batch_latencies()`/
`_content_ready_lookup()` を修正:

- 完全一致が見つからない場合、バッチの `asr_end_time` より**小さい**
  `asr_end_time` を持つ直前のinterimを二分探索で検索し、見つかれば
  そのinterimの `playback_offset` を `content_ready_wallclock` として
  採用。
- 新規フィールド `content_ready_is_exact`(完全一致=True、フォールバック
  =False、両方とも見つからない=None)を追加し、以降の分析で両者を
  混在させないようにしました。
- ユニットテスト4件(完全一致・フォールバック・直前interimなし・
  「バッチより後のinterimは誤ってフォールバックに使わない」の確認)を
  新規追加、既存テスト全63件 + `ruff check` も合格を確認。

`mt_latency_decomposition.py` と、それをインポートして再利用する
`caal_decomposition.py`(ロジックの重複なし)を全151本の実験JSON
(イベントを持つ151本すべて)に対して再実行しました。$0、新規
Deepgram/LLM API呼び出しなし(`log-cost 0` で記録)。

## ANALYZE_RESULTS: 予測通り欠損はほぼ解消、ただし下流の数値は無視できない幅でシフト

- `queue_wait_s` を持つバッチ数: 6,002→**12,251**/12,380
  (欠損率 51.5%→**1.0%**)。残る129件はすべて「直前のinterimが
  本当に存在しない」ケースと確認済み(フォールバックの不具合ではない)。
- `caal_decomposition.csv` の `avoidable_fraction`:
  平均 **0.852→0.743**、中央値 **0.950→0.840**
  (サイクル94の約56%サンプル → 本サイクルの約99%サンプル)。
  0.5を超える実行の割合も **94/105(89.5%)→78/105(74.3%)** に減少。
- 「本リポジトリのレイテンシギャップの大半は変更可能な
  スケジューリング/タイマーポリシーに起因する」という定性的な結論
  自体は今回も支持されていますが、**サイクル94で報告した具体的な数値は
  今回の値に置き換えるべき**です(サンプルが約2倍、ほぼセンサスに
  近づいたため)。

**今回意図的に手を付けなかった点**: `batch_latencies()` を利用する
他の2スクリプト(`word_order_reordering_proxy.py`、
`post_gap_queue_wait.py`)も同じ修正の影響を受けるはずで、実際に
試しに再実行すると出力CSVが変化することを確認しましたが、これら3仮説
(`h-enja-word-order-reordering-proxy-latency`,
`h-mt-queue-wait-post-gap-residual`,
`h-post-gap-queue-wait-persistence`)は既に`status: tested`で
古い結合ロジックに基づく結果がレポート済みのため、レビューなしに
出力ファイルだけ上書きコミットするのは「サイレントな結果の変化」を
招く危険があると判断し、`git checkout` で元に戻しました。次サイクルの
候補として申し送ります。

`hypotheses.json` の該当エントリは `status: tested`、詳細な数値を含む
`result_summary` を記載済みです。

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

- `word_order_reordering_proxy.py` / `post_gap_queue_wait.py` を
  修正済みの結合ロジックで再実行し、既存の `tested` な3仮説の数値が
  どの程度変わるかを確認・再報告する候補を、次回
  `GENERATE_HYPOTHESES` で優先的に検討する価値があります($0)。
- サイクル94から引き続き未解明の「rpm=60でも `avoidable_fraction` が
  0.5未満になる5件」の原因調査も、依然として$0の候補として残っています
  (今回の `avoidable_fraction` 再計算により、この5件の値自体も
  変わっている可能性があるため、着手時は最新のCSVを使うこと)。
- バックログは `queued` 4件 + `proposed` 1件 = 5件(本サイクルで1件が
  `tested` に移行し、1枠の空きがあります)。
