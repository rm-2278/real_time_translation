# サイクル65 結果レポート(2026-10-19, スケジュール実行)

## 今回やったこと

パイプラインは今回起動時点で`GENERATE_HYPOTHESES`状態でした(サイクル64の
`REFLECT`が「`papers.json`にはまだ一度も引用されていない論文が複数残っている
ので、新規の文献探索よりそちらを掘るべき」と明記していたため)。今回は
`GENERATE_HYPOTHESES → HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS
→ WRITE_REPORT`まで実行しました。SEARCH_PAPERS/EXTRACT_PAPERS/READ_PAPERSは
今回もスキップ(サイクル64と同じ理由で、PLAYBOOKが認める運用です)。

### GENERATE_HYPOTHESES: 未引用論文の棚卸しから新しい切り口

サイクル64のリフレクションが候補として挙げていた`papers.json`の未引用論文
(19件)を確認し、特に判定者(LLM-as-a-judge)の信頼性に関する2本
(`rubriceval2603`、`ruverbench2606`)に注目しました。両論文とも、過去の
サイクルで既に「本リポジトリの`translation_fidelity_judge.judge()`との
関連性が高い」とメモされていたにもかかわらず、どの仮説の`grounded_in`にも
実際には使われていませんでした。

`rubriceval2603`(RubricEval)の核心的な知見は「採点前に明示的な推論
(chain-of-thought)を求めると、判定者の信頼性が上がり、判定者間のばらつきが
減る」というものです。これを実際に検証する前に、まず
`translation_fidelity_judge.py`の実際のプロンプト(40-63行目)を直接
確認したところ、現状は`{"score": ..., "dropped_or_distorted": ...,
"notes": ...}`という順序でJSONを要求しており、採点より前に自由記述の
推論を書かせる仕組みは一度も試されていないことが分かりました(より新しい
`judge_schematic_adherence_replay.py`の4基準分解版でも同様で、数値基準が
並ぶだけで自由記述の推論フィールドはありません)。

さらに、サイクル19-21で測定済みの既存ノイズフロア(ガイディング文脈内で
`within_group_noise_stdev` 3.40 / 8.81)には一つの既知の混同があることに
気づきました:あの測定は「REPEATS回ごとに毎回"翻訳"自体を新規生成し、
その都度judge()を1回呼ぶ」方式だったため、翻訳生成自体のランダム性と
判定者自身の採点ノイズが混ざっていました。今回の新規仮説
`h-judge-reasoning-before-score-noise`では、同じ1つの翻訳テキスト
(各サンプル点で`repeats[0]`の最終累積テキストに固定)に対して、採点者を
K=2回呼ぶ設計にし、翻訳のランダム性を完全に排除した「純粋な判定者ノイズ」を
初めて測定できるようにしました。

要点:
- 既存の`experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_
  replay.json`から、ゲーティング効果がゼロだったスパン(`gated_batch_
  indices == []`)を`span_index % 4 == 0`で間引きサンプリング(34グループ、
  2条件 x 2プロンプト版 x K=2判定 = 136回の新規judge呼び出し)。
- `estimated_cost_usd: 0.05`、既存クリップ・既存翻訳テキストのみ使用
  (新規音声取得・新規翻訳生成ともになし)。

バックログは3 queued + 1 proposed = 4件(上限6件以内)。

### HUMAN_APPROVAL

`check-budget 0.05` → `AUTO_APPROVE`(`uses_existing_clips=true`)。新規$0.05
仮説の通常の自動承認パスなので、`approval: auto_approved`, `status: queued`
に設定。既存の3件(OpenAI REST到達不可/Deepgram WebSocket到達不可/人間の
視聴待ち)は引き続き変化なしのため再チェックしませんでした。

### RUN_EXPERIMENTS

今回は環境チェックの結果、`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_
API_KEY`がすべて設定済み、`ffmpeg`も利用可能、Gemini REST
(`generativelanguage.googleapis.com`)も200応答で到達可能でした。ただし
この仮説自体はDeepgramの新規音声取得も新規翻訳生成も不要な設計のため、
Deepgram側の到達性確認は実施していません(この実験に無関係)。

`judge_reasoning_order_replay.py`を新規実装(`ruff check`クリーン)し、
バックグラウンドで実行(136回のGemini `gemini-3.1-flash-lite`判定呼び出し、
新規Deepgram消費ゼロ)。実測コスト$0.05を記録し、`experiments/results.csv`
に1行追記しました。

## 結果

- サンプル34グループ(各グループに2プロンプト版 x K=2判定、計136呼び出し)
  全てJSONパースに成功し、欠損ゼロでした。
- **現行の「採点が先」プロンプト**: 同一の固定翻訳テキストに対する
  K=2回の判定ノイズは`within_group_noise_stdev = 3.27`。
- **新規の「推論が先」プロンプト**: 同じ固定翻訳テキスト・同じサンプル点
  に対して`within_group_noise_stdev = 7.74`——現行方式の**約2.4倍**
  (分散比で約5.6倍)。
- ペアごとの差分(推論先 − 採点先)の平均は+1.66で、符号は推論先の方が
  ノイズが大きい方向に偏っていました。特に大きく効いた例:
  - `guest_talk`クリップのspan 28/`soft_anchor_replay`: 採点先は2回とも
    `[60, 60]`で完全一致、推論先は`[95, 50]`と同一テキストに対して
    大きく割れました。
  - 同クリップのspan 40/`gated_soft_anchor`: 採点先`[75, 70]`に対し、
    推論先`[100, 60]`。

**結論(`status: tested`)**: `rubriceval2603`が示した「採点前の明示的推論は
判定者間のばらつきを減らす」という知見は、本リポジトリの事実保持判定
タスクには**逆方向に**働くという、明確な否定的(反証)結果でした。
翻訳生成のランダム性を完全に排除した上での比較なので、交絡要因の疑いは
薄いと考えています。考えられる(未確証の)機序は2つ:(1)
RubricEvalの「推論」は各ルーブリック項目の満足/不満足を機械的に確認する
制約されたタスクですが、本判定者の推論フィールドは自由記述であり、呼び出し
ごとに異なる解釈の筋書き(「大筋は保たれている」寄りか「重要な数字が
欠落した」寄りか)にコミットしてしまい、その後のスコアが筋書きに追従する
ことでばらつきが増幅される可能性。(2)推論テキストを挟むことで採点トークン
が生成の後方に来ること自体が、推論の内容とは無関係に生成位置的な不安定性を
増やしている可能性(今回の実験ではこの2つを区別できません)。どちらにせよ、
**この結果に基づき、推論先プロンプトを本番の`translation_fidelity_judge.py`
に導入することは見送ります**。K=2は本リポジトリの標準REPEATS規約ですが、
1グループあたりでは検出力の低いチェックである点は留意が必要です(ただし
34グループの集計値としての2.4倍という差は、個々の外れ値だけに依存した
結論ではありません)。

## 人間への確認が必要な項目

今回新たに人間の判断が必要な項目はありません。`pending_approval.json`は
空です。既存の3件(`h-judge-cross-model-agreement-check`: OpenAI REST
到達不可、`h-review-short-acronym-keyterm-false-positive`: Deepgram
WebSocket到達不可、`h-continuous-rating-cps-calibration`: 人間の視聴時間
待ち)は引き続き環境・人間待ちのまま変化なしです。

## 次回への引き継ぎ

- バックログは3 queued + 1 tested(今回分)= 実質3 queued、上限6件に対して
  余裕があります。
- 今回「推論先プロンプトは逆効果」という明確な否定結果が出たため、
  判定者プロンプト設計の方向はこれ以上追わず、次回は別の未引用論文
  (`sasst2026-syntax-aware-chunking`、`exposst2026-adaptive-masking-
  positioning`など)、または`ruverbench2606`が示した別の論点(複数
  judge呼び出しの多数決による収穫減少カーブの検証——本リポジトリはK=2
  しか測定したことがない)を次の切り口として検討することを提案します。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません(自己改善の判断は`reflections.md`の
今回のエントリに記載)。
