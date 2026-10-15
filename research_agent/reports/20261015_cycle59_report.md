# サイクル59 結果レポート(2026-10-15, スケジュール実行)

## 今回やったこと

サイクル58のリフレクションで「新しい仮説を書く前に、既存のキュー済み仮説
(`h-computation-aware-laal-enja`、`h-interim-source-preview-overlay`のStep 1)
を先に処理する」と決めていた方針に従い、GENERATE_HYPOTHESESでは新規仮説を
追加せず、`h-interim-source-preview-overlay`のStep 1を
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS まで実行しました。

### バックログの状態(GENERATE_HYPOTHESES / HUMAN_APPROVAL)

キュー済み5件(`h-judge-cross-model-agreement-check`,
`h-review-short-acronym-keyterm-false-positive`, `h-computation-aware-laal-enja`,
`h-continuous-rating-cps-calibration`, `h-interim-source-preview-overlay`)は
すべて既に`approval: approved_by_human`が付いており、`pending_approval.json`も
空だったため、HUMAN_APPROVALでの新たな承認判断は不要でした。環境を確認すると
`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`が両方設定済み・`ffmpeg`も利用可能でしたが、
今回選んだ仮説はライブAPI呼び出しを一切必要としない$0の再解析のため、
そのままRUN_EXPERIMENTSに進みました。

### h-interim-source-preview-overlay Step 1 の検証

**仮説**: 英語の暫定認識結果(ASR interim)は、同じ発話の日本語訳が表示される
よりずっと早く画面に出せるはずで、翻訳待ちの間だけ薄く表示するプレビュー行を
追加すれば「最初に何か見える」までの時間を約6秒から約1秒に短縮できる、という
ものです。この数値(中央値1.17秒 vs 6.01秒)はこれまで一度限りの実験
(`20260918_llm2025_ep8_zenhan_full_lecture_v2_dict`)に対して、ASR側と翻訳側を
別々に(発話ごとに対応付けずに)計算した概算でした。

**実装**: 新スクリプト
`src/real_time_translation/experiments/interim_preview_timing.py`を作成。
`flicker_metrics.group_asr_interim`/`group_translation_by_utterance`と同じ
`asr_start_time`キーで発話単位にグルーピングし、各発話の「最初のasr_interim
イベントの`wall_clock_lag`」と「最初のtranslation_partial/translation_complete
イベントの`wall_clock_lag`」を**同じ発話内で対応付けて**差分(プレビューに
よる短縮時間)を計算します。`wall_clock_lag`(`playback_offset - asr_start_time`)
は`TimedEvent`が既に持っているフィールドをそのまま使い、新たに再計算しては
いません。新規API呼び出しはゼロで、イベントログを持つ全97実験
(`experiments/*.json`)に対して実行し、結果は
`experiments/interim_preview_timing.csv`に保存しています。

**結果(仮説の前提が、きちんと対応付けた形で確認された)**:

1. 仮説が引用した実験(`20260918_llm2025_ep8_zenhan_full_lecture_v2_dict`、
   1036発話)に対する結果は、以前の概算と非常に近い値になりました:
   最初のASR interimまでの時間は中央値**1.166秒**・p90**2.105秒**
   (概算: 1.17秒/2.11秒)、最初の翻訳までの時間は中央値**6.035秒**・
   p90**6.396秒**(概算: 6.01秒/6.40秒)。概算が偶然ではなく妥当だったことが
   確認できました。
2. 発話単位で正しく対応付けた「プレビューによる短縮時間」(翻訳の遅延 - ASR
   interimの遅延)は、この実験では中央値**4.321秒**・平均**3.822秒**でした。
   これは概算が暗黙に想定していた「別々の中央値の単純な差」(6.035-1.166=
   4.869秒)とは一致しません。短い発話ほど両方の遅延が同時に小さくなる傾向
   があり、2つの分布が発話単位では独立でないためです。「別々に計算した
   分布の差」と「実際に対応付けた差」が食い違うという、この仮説自身が
   指摘していた懸念が実際に数値として表れた形です。
3. 1件の実験だけでなく全97実験・6638発話(両側のイベントが揃っているもの)
   で見ても効果は一般化しています: 全発話をまとめたプレビュー短縮時間は
   中央値**3.662秒**・平均**5.024秒**(実験ごとの中央値の平均は**6.651秒**
   で、実験間のばらつきは大きいものの、ほぼ全ての実験で複数秒のプラスの
   短縮効果がありました)。
4. プレビュー行自体の見栄え(英語行のちらつき、`flicker_metrics`の語単位
   normalized erasureを再利用): 全体平均**0.189**で、仮説が引用していた
   「flicker_metricsによる約0.20」とほぼ一致しました。

**結論**: この仮説の核心(英語のASR暫定結果を先に見せれば、日本語訳より
数秒早く「何か」を画面に出せる、ただしその行は視覚的にちらつく)は、1件の
実験の概算ではなく、97実験・6638発話にわたる正しく対応付けた測定で確認
されました。今回実装したのはStep 1(測定のみ)で、Step 2(`overlay.html`の
実際の変更とフラグ化、人間による視聴確認)は未実装です。`overlay.html`には
仮説自身が指摘していた通り人間による未コミットの変更が別途あるとされており、
着手前にrm-2278と調整が必要なため、今回は意図的にスコープ外としました。
`status`は`tested`としています(`abandoned`ではなく、測定自体は健全で
結論が出ています)。

## 人間への確認が必要な項目

今回新たに人間の判断が必要な項目はありません。`h-continuous-rating-cps-calibration`
は`approved_by_human`/`queued`のままで、実行には引き続き人間(rm-2278)による
約20〜30分の視聴・評価作業が必要です。`h-interim-source-preview-overlay`の
Step 2に進む前に、`overlay.html`への人間の未コミット変更との調整をお願いします。

## 次回への引き継ぎ

- バックログは5件/上限6件です。次回のGENERATE_HYPOTHESESでは、まず残っている
  キュー済み仮説の処理を優先してください:
  `h-computation-aware-laal-enja`(Step 1、$0、新スクリプトが必要)、
  `h-review-short-acronym-keyterm-false-positive`(Deepgram WebSocket到達性の
  再確認が必要)、`h-judge-cross-model-agreement-check`(OpenAI REST到達性の
  再確認が必要)。今回はこの3件のうちどれも着手していません(時間/スコープの
  都合でStep 1が完了していた`h-interim-source-preview-overlay`を優先したため)。
- 今回はDeepgram WebSocket / OpenAI REST到達性を再確認していません(選んだ
  仮説がライブAPIを必要としなかったため)。ただし今回の環境では
  `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は設定済み・到達可能な状態だったため、
  次回ライブ実験が必要な仮説(`h-review-short-acronym-keyterm-false-positive`
  など)を選ぶ際は、この環境でDeepgram WebSocket到達性が実際に変わっていないか
  再確認する価値があります。
- `h-interim-source-preview-overlay`のStep 2(overlay.html変更 + 視聴確認)は、
  `h-continuous-rating-cps-calibration`の視聴セッションに相乗りできる可能性が
  あります(仮説自身の記載通り)。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません。
