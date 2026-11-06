# サイクル84 結果レポート(2026-11-06, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`READ_PAPERS`状態でした(サイクル84の
`SEARCH_PAPERS`と`EXTRACT_PAPERS`は前回のセッションで既に完了済み)。
今回は`READ_PAPERS → GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで実行しました。

## READ_PAPERS: 2件を要約し`status: "read"`に

前回のセッションが`"extracted"`のまま残していた2件の論文を、自分の
言葉で要約し直しました。

1. `wmt2026-verbalized-confidence-judge`
   (`aclanthology.org/2026.wmt-main.107/`) -- WMT2026の共有タスク
   システム論文。本文自体はこのセッションでも取得できませんでした
   (`aclanthology.org`への`WebFetch`が`EGRESS_BLOCKED`、サイクル83・
   84のEXTRACT_PAPERSと同じ現象)が、WebSearchのスニペットから
   メカニズムは十分に理解できました: judgeがMQM風のエラー種別ごとに
   言語化された確信度(verbalized confidence)を報告する、という
   アプローチです。具体的な相関/精度の数値は未確認のままです。
2. `calibrating-llm-judges-linear-probes-2026`
   (`arxiv.org/pdf/2512.22245`, Meta FAIR) -- judge LLMの内部隠れ層に
   対してBrierスコア損失で線形プローブを学習し、確信度を較正する手法。
   推論/数学/事実性/コーディング/人間選好タスクで評価されており、
   翻訳タスクは対象外です。この手法を実際に本リポジトリの
   `translation_fidelity_judge.judge()`に適用できるかを
   `translation_fidelity_judge.py`を直接読んで確認したところ、
   Geminiを公開API(`generativelanguage.googleapis.com`)経由で
   呼んでいるだけで隠れ層へのアクセスはなく、**この手法はそのままでは
   移植不可能**であることを確認しました。

両論文を既存の`h-judge-*`仮説群(block-order-position-bias、
reasoning-before-score-noise、rubric-decomposition-schematic-adherence、
repeat-count-diminishing-returns、holistic-vs-decomposed-repeat-noise、
cross-model/cross-gemini-model agreement、verbosity-length-bias)と
`experiments/`配下・`results.csv`・直近の`git log`に対して突き合わせ
ましたが、judgeの出力に「項目ごとの言語化された確信度」を追加する
という角度は、これまでどの仮説・実験にも含まれていないことを確認
しました。既存のrubric分解や推論順序の変更とは異なる、まだ手を
付けていない新しい方向性です。

## GENERATE_HYPOTHESES: 新規仮説なし(バックログが依然上限)

`queued`3件・`proposed`3件(いずれも`approval: needs_human`、
`pending_approval.json`に記録済み)の計6件で、PLAYBOOKの上限
(6件)に到達したままだったため、新規仮説は追加しませんでした
(サイクル79-83と同じ状況)。`wmt2026-verbalized-confidence-judge`
由来の「judge()にも項目ごとの確信度フィールドを追加し、低確信度の
judge呼び出しがサイクル19-21で測定済みの繰り返しノイズと相関するか
を見る」という仮説は、バックログに空きが出た次回以降の最有力候補
として記録しました。

## HUMAN_APPROVAL: 処理対象なし

`approval`が未設定、または値が`"proposed"`のまま未処理の仮説は
存在しませんでした(`proposed`の3件はいずれも既に`needs_human`に
エスカレーション済み)。`queued`な3件(いずれも
`approval: approved_by_human`)が残っていたため、PLAYBOOKのルールに
従い`RUN_EXPERIMENTS`へ進みました。

## RUN_EXPERIMENTS: 既存の3件の`queued`仮説すべてが依然ブロック中

環境チェックを再実行しました:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`: いずれも環境に
  設定済み。`ffmpeg`も利用可能。キャッシュ済みクリップも存在。
- Deepgram REST(`/v1/projects`): **200**(到達可能)。
- Gemini REST(`/v1beta/models`): **200**(到達可能)。
- OpenAI REST(`/v1/models`): **CONNECT拒否(403)**(`curl`終了コード
  56、プロキシのステータスで`connect_rejected`)。サイクル72以降と
  完全に一致する再現(今回で3回連続)です。
- Deepgram **listen-websocket**ハンドシェイク: `python3.13`で新規に
  `.venv`を作成し(`uv pip install -e ".[experiments]"`)、実際の
  WebSocketアップグレードを試したところ、**HTTP 400 "Connection
  header did not include 'upgrade'"**で失敗。サイクル5以降繰り返し
  確認されている既知のブロックと完全に一致します。

既存の3件の`queued`仮説はいずれも今回も実行不可でした(前2件は
`result_summary`にサイクル84分の再確認を追記済み):

1. `h-judge-cross-model-agreement-check` -- OpenAI接続拒否のため継続
   ブロック。
2. `h-review-short-acronym-keyterm-false-positive` -- Deepgram
   listen-websocket失敗のため継続ブロック。
3. `h-continuous-rating-cps-calibration` -- 環境の問題ではなく、
   rm-2278さん自身の視聴時間(20-30分程度)が必要な人間参加型の仮説
   のため、今回も新しい確認は行いませんでした(状況は前サイクルから
   変化なし)。

PLAYBOOKの指示(環境が実験を実行できない場合は結果を捏造せず、ギャップを
記録して報告に進む)に従い、新規の実験は一件も実行せず、`ANALYZE_RESULTS`
(分析対象なし)を経て本レポートの作成に進みました。

## 環境面のメモ(継続事項、変化なし)

サイクル83から変化なく、以下の2つの構造的制約が今回も再確認されました:

- OpenAI APIへの接続が明示的にブロックされている(ポリシーによるCONNECT
  拒否)。
- Deepgramのストリーミング(WebSocket)接続のアップグレードハンドシェイク
  がプロキシによって壊れている(RESTは問題ない)。

`h-judge-cross-model-agreement-check`のブロック理由に追記した通り、
この2つの制約はサイクル72・78・80・81・82・83・84の7回連続で同一の
再現を示しており、十分に安定した既知の制約として扱ってよい段階に
来ています。

## HUMAN_APPROVAL待ちの項目(rm-2278さんの確認が必要、変更なし)

`pending_approval.json`に3件あります(いずれも前サイクルから継続):

1. `h-judge-repeat-independent-batches-corrected-design`
   (2026-10-20付、$0.35)
2. `h-soft-anchor-gate-exempt-utterance-end`(2026-10-28付、$0.35)
3. `h-judge-cross-gemini-model-agreement-check`(2026-11-01付、$0.05)
   -- OpenAI/Deepgram WebSocketの制約とは無関係に実行できる可能性が
   高い項目です。

いずれも`check-budget`は`AUTO_APPROVE`、`uses_existing_clips=true`で、
通常のPLAYBOOKルールなら自動承認される内容です。rm-2278さんが
`hypotheses.json`を直接編集するか、何らかの形で承認の意思を示して
いただければ、次サイクルで`RUN_EXPERIMENTS`に進めます。

## 次のステップ

`REFLECT`に進み、今回の振り返り(judge信頼性文献を2件`read`まで進め、
「項目ごとの言語化された確信度」という新しい角度を発見できたこと、
OpenAI/Deepgram WebSocketブロックが7回連続で安定していること、
バックログが依然上限で埋まっていること)を`reflections.md`に記録した
うえで、次サイクルの方針を決定します。
