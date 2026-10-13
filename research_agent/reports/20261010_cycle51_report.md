# サイクル51 結果レポート (2026-10-10)

## 概要: 新規仮説なしで `GENERATE_HYPOTHESES` を通過、2件目の承認済み仮説を実行

今サイクル開始時点で `hypotheses.json` には `queued` かつ
`approved_by_human` の仮説が2件(`h-judge-verbosity-length-bias-retroactive`
$0.00、`h-judge-cross-model-agreement-check` $0.05)残っており、サイクル50の
`reflections.md` エントリも「この2件を消化するまで新規の文献探索・仮説生成は
後回しにすべき」と明示的に推奨していました。これに従い、今サイクルは
`GENERATE_HYPOTHESES` で新規仮説を追加せず(バックログは上限~6件に対して
十分に小さいままです)、`HUMAN_APPROVAL` も素通りして(承認待ちの仮説は
ゼロ、`pending_approval.json` も空)、`RUN_EXPERIMENTS` へ進みました。

今サイクルの実行環境では `DEEPGRAM_API_KEY`・`GOOGLE_API_KEY` の両方が
設定されており、`ffmpeg` も利用可能でしたが、今回選んだ仮説はコスト$0の
純粋な事後分析のため、ライブAPI接続の検証は不要でした(コスト順の方針に
従い、残り2件のうち安い方を選択)。

## 実行した仮説: h-judge-verbosity-length-bias-retroactive

### 背景

今サイクルの元になった文献(arxiv2606-19544「Reliability without
Validity」)は、LLM-as-judgeの採点が出力の長さ(verbosity)に引き寄せられる
バイアスを自分たちのコホートでは小さい(<0.011)と報告した一方、「この
リポジトリ自身の `translation_fidelity_judge.judge()` のルーブリックでは
未検証」と明言していました。arxiv2509-20293も、集計されたLLM-judgeスコアは
鵜呑みにする前に、提示されたルーブリックがどこまで判定を説明しているかを
検証すべきだと主張しています。

幸い、このリポジトリには新規API呼び出しなしで検証できるデータが既に
2つの実験JSONに揃っていました:
`experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json`
(リピートごとの judge スコアと翻訳テキストのペア)と
`experiments/20260920_h_soft_anchor_disfluency_gate_replay.json`
(条件あたり1つの judge スコア、repeats[0] のみ)です。

### 実装

新規スクリプト
`src/real_time_translation/experiments/judge_verbosity_bias_check.py`
を作成しました(新規API呼び出しは一切なし)。2つの異なる観点で分析しています:

- **サブチェック1(同一スパン+同一条件内、リピート間)**: noisefloor-replay
  ファイルのみ(リピートごとの judge スコアがある唯一のファイル)。
  同じ条件のリピート間では翻訳内容がほぼ一定なので、長さだけが偶発的に
  変動します。ここでスコアが長さと相関していれば、それは judge 自体の
  verbosityバイアスそのものです。
- **サブチェック2(同一スパン内、条件間)**: 両ファイルを合わせて利用。
  条件間では意図的に出力の長さが変わる(例: 圧縮・ゲート系の条件は
  短くなりうる)ため、ここでの相関は設計上想定される範囲であり、
  サブチェック1とは混同せず別個に報告しています。スパンごとの品質差が
  相関を歪めないよう、長さ・スコアの両方をスパンごとに中心化してから
  プールしました(サイクル21の between-span/within-span 分解と同じ考え方)。

実装中、一部の judge() 呼び出しが元データで既に失敗していた
(JSONDecodeError、score=None、guest-talkスパン17の `gated_soft_anchor`)
ことが判明したため、その1件は両サブチェックから除外しました(クラッシュ
させず、黙って0扱いにもしない)。

### 結果

| サブチェック | n | Pearson r | Spearman r |
|---|---|---|---|
| 1: 同一スパン+条件内、リピート間 | 587 | 0.041 | -0.076 |
| 2: 同一スパン内、条件間(スパンごと中心化) | 585 | 0.011 | -0.098 |

両サブチェックともn>=20で十分なサンプルサイズがあり、相関はほぼゼロでした。
特にサブチェック1(verbosityバイアスを直接測る、より厳密な検証)が
ほぼゼロだったことは、`judge()` のルーブリックが(プロンプトで明示的に
「日本語の自然さ・長さとは独立に採点する」と指示している通り)実際に
長さに引き寄せられていないことを示しており、arxiv2606-19544の
小バイアス知見をこのリポジトリ自身のデータでも再現する結果です。

**結論**: これまで・これから行う fidelity 比較(judge() スコアに基づく
結論)に対して、verbosityバイアスの補正は不要と判断してよい、という
安心材料が得られました。

詳細は `experiments/judge_verbosity_bias_check.json`、サマリー行は
`experiments/results.csv` の `h_judge_verbosity_length_bias_retroactive`
行を参照してください。

### コスト

$0.00(新規Deepgram/LLM API呼び出しなし、既存JSONの再読み込みのみ)。
`budget.json` に記録済みです。

## パイプラインの状態

`GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS
-> WRITE_REPORT` まで今サイクルで完了しました。`hypotheses.json` の
`h-judge-verbosity-length-bias-retroactive` は `status: tested` に
更新済みです。次は `REFLECT` に進みます。

## 次サイクルへの申し送り

- 残り1件の承認済み仮説 `h-judge-cross-model-agreement-check`($0.05、
  `OPENAI_API_KEY` が必要)が `queued` のまま残っています。今サイクルの
  環境では `OPENAI_API_KEY` が設定済みでしたが、`RUN_EXPERIMENTS` の
  環境確認手順(到達可能性チェック)は実行時に改めて行ってください。
  到達不能な場合はプレイブック通り「クラッシュではなくブロッカー」として
  記録し、スキップしてください。
- この仮説を消化した後は、承認済み・未実行の仮説がバックログからなくなる
  ため、次サイクルは新規の `SEARCH_PAPERS` パスを検討する良いタイミングに
  なります。
