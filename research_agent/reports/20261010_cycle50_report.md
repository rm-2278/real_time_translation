# サイクル50 結果レポート (2026-10-10)

## 概要: 人間の承認を確認し `RUN_EXPERIMENTS` を再開 -- 1件の仮説を実行・分析完了

サイクル49までの16日間、`pending_approval.json` の3件の仮説が
`WAITING_APPROVAL` 状態で止まっていましたが、今サイクル開始時に
`pipeline_state.json` の履歴を確認したところ、人間(rm-2278)が
別のClaude Codeセッション(2026-10-10)で既に3件すべてを承認し、
`WAITING_APPROVAL -> RUN_EXPERIMENTS` へ遷移済みでした
(`pending_approval.json` も空に戻っています)。サイクル36で確定した
「エージェント自身による自己承認は環境の権限レイヤーに拒否される」
というブロックは、今回も自動では突破しておらず、実際に人間の操作で
解除されています。

今サイクルでは、3件のうち最も焦点が絞られていた
`h-soft-anchor-gate-min3-erasure-decomposition`(コスト$0.00、既存クリップの
再利用のみ)を実行し、`ANALYZE_RESULTS` まで完了させました。残り2件
(`h-judge-verbosity-length-bias-retroactive` $0.00、
`h-judge-cross-model-agreement-check` $0.05)は次サイクル以降に持ち越します
(プレイブックの「深さを広さより優先する」方針に沿って、今サイクルは
1件を丁寧に仕上げました)。

## 実行した仮説: h-soft-anchor-gate-min3-erasure-decomposition

### 背景

サイクル23の `h-soft-anchor-gate-min-words-3`(`GATE_MIN_WORDS=3` での
disfluencyゲート再テスト)は、guest-talkクリップで `gated_soft_anchor` の
集計NE(normalized erasure、字幕の書き換え量の指標)が0.377と、
`soft_anchor_replay` の0.089より明らかに悪化するという、意図と逆方向の
結果を報告していました。その `result_summary` は「ゲートが実際に発火した
バッチ vs 発火していないバッチでNEを分解する、焦点を絞った追跡調査」を
次サイクルで行うよう明示的に推奨していました。本仮説はその追跡調査です。

この仮説を書き起こす際、スパン単位(span-level)の粗い分解は既に
事前検証されており、該当する125スパン中、ゲートが実際に発火した42個の
guest-talkスパンに悪化がほぼ集中していることが確認済みでした。残された
より細かい問題は、スパン内の「遷移(transition)」単位での分解です:
ゲートが直接保留したバッチへの遷移そのものにコストが局在しているのか、
それとも同じスパン内の、ゲートが発火していない後続の遷移まで不安定化
させる「副作用」が存在するのか、という二択です。

### 実装

新規スクリプト
`src/real_time_translation/experiments/soft_anchor_gate_erasure_decomposition.py`
を作成しました。新規API呼び出しは一切なく、既存の
`experiments/20260922_h_soft_anchor_gate_min3_replay.json` に記録済みの
`condition_repeats`(各リピート・各バッチの累積翻訳テキスト)を再利用する
純粋な事後分析です。`flicker_metrics._longest_common_prefix_len` を
そのまま再利用し、各遷移 t ( t=0..バッチ数-2 、バッチ t+1 への遷移)ごとに
`erasure_t = len(outs[t]) - lcp(outs[t], outs[t+1])` を計算。
遷移 t は、その遷移先バッチ(t+1)が `gated_batch_indices` に含まれていれば
`gated_transition`、それ以外は `nongated_transition_in_gated_span` と
ラベル付けしました。

### 結果

guest-talkソースの42個のゲート発火スパンについて、`gated_soft_anchor` と
`soft_anchor_replay` の正規化erasure(各リピートの最終テキスト長で正規化)を
比較:

| ラベル | n | gated_soft_anchor 平均 | soft_anchor_replay 平均 | 差分(超過erasure) |
|---|---|---|---|---|
| gated_transition(直接保留した遷移) | 84 | 0.615 | 0.067 | **+0.549** |
| nongated_transition_in_gated_span(同スパン内の非ゲート遷移) | 50 | 0.077 | 0.036 | **+0.040** |

ゲート自体の遷移での超過erasure(+0.549)は、同スパン内の非ゲート遷移での
超過erasure(+0.040)の約13.6倍にあたります。これは、サイクル23の仮説が
提示した2つの競合する予測のうち、「局所的な再表示(contained re-reveal)」
を支持し、「不安定化(destabilization、ゲートの影響が同スパンの後続翻訳にも
広がる)」を支持しない結果です。つまり、このゲーティング手法のコストは
概ねゲートが直接保留したバッチに閉じており、スパン全体の品質を広く
損なうものではないと言えます。

ただし1件だけ明確な外れ値があります。スパン34(非ゲート遷移4個)は、
超過erasureが0.358と他のスパンより一桁大きく、将来的に個別調査する価値が
あります(全体の結論は変えません)。

詳細は `experiments/soft_anchor_gate_erasure_decomposition.json`
(スパン別の明細を含む)、サマリー行は `experiments/results.csv` の
`h_soft_anchor_gate_erasure_decomposition` 行を参照してください。

### コスト

$0.00(新規Deepgram/LLM API呼び出しなし、既存JSONの再読み込みのみ)。
`budget.json` に記録済みです。

## パイプラインの状態

`RUN_EXPERIMENTS -> ANALYZE_RESULTS -> WRITE_REPORT`
まで今サイクルで完了しました。`hypotheses.json` の
`h-soft-anchor-gate-min3-erasure-decomposition` は `status: tested` に
更新済みです。次は `REFLECT` に進みます。

## 次サイクルへの申し送り

- 残り2件の承認済み仮説(`h-judge-verbosity-length-bias-retroactive` $0.00、
  `h-judge-cross-model-agreement-check` $0.05)が `queued` のまま残って
  います。次回以降の `RUN_EXPERIMENTS` で実行してください
  (プレイブックの「$0の事後分析を優先」の方針に従えば、
  `h-judge-verbosity-length-bias-retroactive` が次点)。
- スパン34(guest-talk、`h-soft-anchor-gate-min3-erasure-decomposition` の
  外れ値)は、時間があれば個別調査する価値がありますが、現時点では
  ブロッカーではありません。
- 人間の承認が得られたことで `WAITING_APPROVAL` の長期滞留は解消しました。
  次に `needs_human` な仮説が出た場合に備え、サイクル36/48/49の知見
  (自己承認は環境により拒否される、エスカレーションは間隔を空けて1回)
  は引き続き有効な方針として扱ってください。
