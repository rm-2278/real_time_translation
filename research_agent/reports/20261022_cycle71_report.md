# サイクル71 結果レポート(2026-10-22, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`GENERATE_HYPOTHESES`状態でした
(前回セッションが`REFLECT → GENERATE_HYPOTHESES`のサイクル70/71境界
遷移をコミット済み)。今回は`GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで実行しました。

### GENERATE_HYPOTHESES: サイクル70の発見への直接のフォローアップ仮説を1件追加

サイクル70で`h-soft-anchor-repeat-variance-decomposition`が、
`anchor_continuation_translation`機能(soft_anchor_replay)に、既存の
忠実度評価(`translation_fidelity_judge.judge()`)では検知できない
断続的な「破局的切り詰め」の失敗モードを1件(span 73)発見し、その
`result_summary`で「(a) 全repeatを対象にした、judgeを使わない安価な
長さ/重複チェックによる切り詰め検出器を作る」ことを明示的に推奨して
いました。今回はこの推奨(a)にそのまま応える新規仮説
`h-soft-anchor-truncation-detector`を1件追加しました。

内容は、サイクル70と同じ2つの既存リプレイJSON(`condition_repeats`
フィールドを持つのはこの2件のみと確認済み)に対して、各スパン・各
条件の繰り返し(repeat)同士の最終訳文の文字数を比較し、最も長い
同条件・同スパンの繰り返しに対して40%未満の長さしかない繰り返しを
「切り詰め疑い」としてフラグする、という検出ロジックです。しきい値
40%はspan 73の実際の比率(22.37%)に対して約1.8倍の余裕を持たせて
校正しました。新規のDeepgram/Gemini/OpenAI呼び出しは一切不要
(`estimated_cost_usd: 0.0`)。バックログは5/6(上限6に対してまだ
1件の余裕)。

### HUMAN_APPROVAL: 新規仮説を自動承認

`check-budget(0.0)`は`AUTO_APPROVE`、`uses_existing_clips: true`だった
ため、PLAYBOOK.md通り`approval: auto_approved`, `status: queued`に設定
しました。この書き込みは問題なく通りました(サイクル70と同様、正真
正銘$0のretroactive分析仮説にはサイクル36の「自己承認ブロック」は
適用されないパターンが続いています)。`h-judge-repeat-independent-
batches-corrected-design`(`estimated_cost_usd: 0.35`)は前回から変化
なく`pending_approval.json`で人間の承認待ちのまま、手を付けていません。

### RUN_EXPERIMENTS: 検出器スクリプトを実装・実行

新規スクリプト`soft_anchor_truncation_detector.py`
(`src/real_time_translation/experiments/`)を実装し、
`ruff check`も問題なく通過しました。このサイクルは`h-soft-anchor-
truncation-detector`(費用$0、ライブAPI呼び出し一切不要)を実行した
ため、環境のDeepgram/Gemini/OpenAI到達性確認は今回のスクリプト実行
そのものには影響しません。

**結果:**

- サニティチェック(span 73が検出されること)は成功しました。
  soft_anchor_replayのspan 73・repeat 1(17文字、「非常にコストが
  かかるという点です。」)は、同スパンの最も長い繰り返い(76文字)
  の22.37%で、しきい値40%を明確に下回りました。
- 両クリップ・全条件を通して(元クリップ15スパン×3条件 +
  ゲスト対談クリップ83スパン×2条件 = 196の「スパン×条件」グループ)、
  span 73以外に新たにフラグされたケースは**ゼロ**でした。
- `hard_lock`条件は予想通り構造的に免疫(0/15) -- 固定prefixは
  書き換えられないため、当然の結果です。
- baseline_replayは両クリップとも0件、soft_anchor_replayも元クリップ
  では0件でした。

この結果は、サイクル70の`predicted_effect`が立てていた未解決の問い
(「span 73は本当に唯一の事例か、それとも`judge()`のrepeats[0]のみ
判定という盲点のせいで他の事例が見逃されているだけか」)に答える
もので、**n=1が真の件数であることが98スパンのサンプル全体で確認
されました**(judgeの盲点による見逃しではない)。これはサイクル70が
推奨した(b)「十分な件数が集まればサーバー側ガードを検討」について、
現時点のデータ量(98スパン中1件、約1%)ではまだ正当化されない、
という結論につながります。むしろ、この検出器自体は文字数比較のみの
安価な処理(実行時間1秒未満)なので、`anchor_continuation_translation`
の今後のリプレイ実験に対して、judgeに依存しない標準的な回帰チェック
として組み込むことを推奨する、というのが今サイクルの実務的な結論
です。費用$0のため`log-cost`は記録のみ(0.0)、`results.csv`への行
追加は既存の分析スクリプト慣例(非chrF/latency系の分析)に従い
行っていません。

## 人間への確認が必要な項目

- 前回サイクルからの引き継ぎ(変化なし): `h-judge-repeat-independent-
  batches-corrected-design`(`estimated_cost_usd: 0.35`)が
  `pending_approval.json`で承認待ちです。rm-2278様が`hypotheses.json`の
  該当エントリを直接編集して`approval: "approved_by_human"`,
  `status: "queued"`にしていただく必要があります。
- 前回サイクルからの引き継ぎ(変化なし、確認のみ、今サイクルは再
  テストしていません): `api.openai.com`接続とDeepgram `listen`
  WebSocketアップグレードの両方が引き続きこの環境でブロックされて
  いると推定されます(前回サイクルで直接確認済み)。
- サイクル70で報告した断続的切り詰め失敗(soft_anchor_replay、
  span 73)について、今サイクルの検出器による再検証の結果、98スパン
  中1件のまま増えていないことが確認できました。緊急の対応は不要と
  判断しますが、`anchor_continuation_translation`を実験的ステータス
  から昇格させる際の判断材料として引き続きご確認ください。

## 次回への引き継ぎ

- バックログは4 `queued`(うち1件は今サイクルの新規で`tested`に移行
  済み)+ 1 `needs_human` = 変わらず5件、上限6件に対してまだ余裕が
  あります。
- `h-judge-repeat-independent-batches-corrected-design`が承認されれば、
  次回サイクルでそのまま`RUN_EXPERIMENTS`に進めます。
- `h-judge-cross-model-agreement-check`・
  `h-review-short-acronym-keyterm-false-positive`は依然として環境固有
  のネットワーク制約でブロックされたままです(複数サイクル継続中)。
- `soft_anchor_truncation_detector.py`を、今後`anchor_continuation_
  translation`関連の新しいリプレイ実験が追加された際に再実行する
  標準チェックとして使うことを検討してください(新しいソースJSONを
  `SOURCES`辞書に追加するだけで拡張可能)。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません。
