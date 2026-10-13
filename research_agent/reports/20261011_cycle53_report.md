# サイクル53 結果レポート (2026-10-11)

## 概要: 久しぶりの文献探索 → 新規仮説1件を生成・実行・分析まで完了

サイクル52の `reflections.md` の申し送り通り、バックログに残っていた
`h-judge-cross-model-agreement-check`(`api.openai.com` のegressブロックで
止まっている件)を待つだけでなく、`REFLECT -> SEARCH_PAPERS` で新サイクルを
開始しました。`SEARCH_PAPERS` の実質的な実行はサイクル23(2026-09-25)以来
初めてです(その間のサイクルは `REFLECT -> GENERATE_HYPOTHESES` の
ショートカットを使っていました)。

## SEARCH_PAPERS: 新しい文献は見つからず

3件のWebSearchクエリ(一般的なsimultaneous speech translation/LLMの
最新動向、字幕の安定性/flicker/revision policy、cascade対cascade比較の
最新動向)を実行しましたが、いずれも既存コーパスに既に収録済みの論文か、
サーベイ以前の古い研究しか出てきませんでした。新規の "found" スタブは
追加していません。これは探索の失敗ではなく、正直な「今回は収穫なし」
という結果として `papers.json` に記録しました。

## READ_PAPERS: サイクル22の宿題を再検討、1件を仮説化・1件は保留

サイクル22の `_read_cross_reference_note_cycle22` には、まだ書き起こされて
いない2つの候補方向が残っていました:
1. `arxiv2509-20293` の "schematic adherence"(ルーブリック崩壊)診断を
   既存のjudge出力に事後的に適用する
2. `arxiv2607-02235` が指摘する人間 vs judge のスポットチェック(En->Ja
   judgeの妥当性検証)

方向(1)を実際に書き起こす前に `translation_fidelity_judge.py` を直接
確認したところ、重要な誤りに気づきました: この論文の診断手法は**複数の
名前付きルーブリック基準**が必要ですが、現行の `judge()` は単一の総合
スコア(`score`、0-100)しか返しません。既存データには分解する対象が
存在しないため、「既存出力への事後適用」という当初の枠組みは機能しません。
代わりに、新しいプロンプト(4つの名前付き下位基準 + 既存の総合スコアを
**同じ1回の呼び出しで**返す)でリプレイする、より小規模な新仮説として
再構成しました。

方向(2)(人間によるスポットチェック)は、この無人・スケジュール実行
セッションでは実行できない(実際の人間の評価が必要)ため、今回は書き起こ
さず、将来の人間同席セッションのための未解決ギャップとして明記しました。

## GENERATE_HYPOTHESES → HUMAN_APPROVAL: 新規仮説1件を追加・自動承認

`h-judge-rubric-decomposition-schematic-adherence` を追加しました。概要:
既存の `translation_fidelity_judge.judge()` の裏に隠れたルーブリック崩壊が
ないかを、4つの下位基準(数値・固有表現/専門用語・因果関係・全体的完全性)
+ 総合スコアを1回の呼び出しで返す新しいプロンプトでリプレイして検証する
もの。`uses_existing_clips: true`、推定コスト $0.05。`check-budget 0.05` は
`AUTO_APPROVE` を返したため、`approval: auto_approved` / `status: queued`
として自動承認しました。

## RUN_EXPERIMENTS: 60件の新規judge呼び出しを実行($0.02)

`DEEPGRAM_API_KEY`・`GOOGLE_API_KEY`・`OPENAI_API_KEY` は全て設定済みで、
Gemini REST(200)・Deepgram REST(200)は到達可能でした
(`api.openai.com` は依然CONNECT 403でブロック中、変化なし -- この仮説には
無関係なので実行には影響なし)。

新スクリプト `judge_schematic_adherence_replay.py` を実装し、
`experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json`
から決定的にサンプリングした60点(span_index % 5 == 0、baseline_replay/
soft_anchor_replay/gated_soft_anchor各20点、repeats[0]のみ)に対して、
新しい分解ルーブリックのjudge呼び出しを実行しました。60/60件が正常に完了し、
実コストは$0.02と記録しました(`uv run ruff check .` も通過)。

## ANALYZE_RESULTS: 結論不能(天井効果)、しかし意味のある副産物あり

結果は意図した「schematic adherence」の検証には**結論不能**でした。原因は
サンプルの深刻な天井効果です: `causal_logical_relationships` は60/60点全て
100(分散ゼロ)、他の基準もほぼ全て80以上、総合スコアも全60点で85以上
(60点中47点が100)。この結果、プールしたOLS(schematic_adherence_r_squared)
は特異行列となりnullに、6つの下位基準間の相関のうち3つもゼロ分散でnullに
なりました。

実際のサンプルデータを直接確認して根本原因を特定しました: このリプレイは
各条件の**最終**累積翻訳テキストを判定しています(`judge_verbosity_bias_check.py`
と同じ慣習)。その時点では、gatingが途中で発生したかどうかに関わらず、
3条件とも内容がほぼ完全に収束していることが多いためです。gating系の仮説
(h-soft-anchor-*シリーズ)は一貫して「表示タイミング・flicker・erasureの
トレードオフ」として枠組みされており、情報保持への悪影響は主張していません
でした -- 今回の結果はその枠組みの一貫性を裏付ける、意外と有用な副産物です。
ただし、judgeのルーブリックそのものが整合的かという本来の問いには答えて
いません。次回は、実際に情報欠落が記録されている実験(compression_actions
やsemantic-completeness-gating系)からサンプリングするか、最終テキストでは
なく部分的・初期バッチのテキストを判定対象にするべきです。

`hypotheses.json` の `status` は `tested`(仮説自体の実装は健全で、
データの選び方が今回の問いには不向きだったという結論自体は有効な結果の
ため、`abandoned` ではありません)。

## コスト

今サイクルの支出: $0.02(新規Gemini judge呼び出し60件分)。本日の累計
$0.02 / 日次上限$7.00。バッチ上限$3.00にも十分収まっています。

## 人間への申し送り事項

- `api.openai.com` のegressブロックは変化なし。`h-judge-cross-model-agreement-check`
  ($0.05)は依然 `queued`/`approved_by_human` のまま保留中です。緊急性は
  低く、PushNotificationは送っていません。
- 人間によるjudgeスポットチェック(方向2、`arxiv2607-02235`由来)は
  このセッションでは実行不可能です。人間同席のセッションで改めて検討を
  お願いします。

## 次サイクルへの申し送り

- `h-judge-rubric-decomposition-schematic-adherence` を再実行する場合は、
  最終累積テキストではなく、既知のfidelityコストが記録済みの実験
  (`h-compression-actions-prompt-instruction`、`h-semantic-completeness-gating`
  関連のJSON)からサンプリングするか、部分バッチのテキストを使うよう
  変更すること。
- バックログは `h-judge-cross-model-agreement-check`(ブロック中)1件と、
  今回 `tested` になった `h-judge-rubric-decomposition-schematic-adherence`
  のみ。`REFLECT` で次の一手(再探索か、上記の改良版リプレイを新仮説として
  書くか)を検討する。
