# サイクル60 結果レポート(2026-10-16, スケジュール実行)

## 今回やったこと

サイクル59のリフレクションで「新しい仮説を書く前に、残っている$0の実行可能な
キュー済み仮説 `h-computation-aware-laal-enja` を先に処理する」と決めていた
方針に従い、GENERATE_HYPOTHESESでは新規仮説を追加せず、この仮説を
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS まで実行しました。

### バックログの状態(GENERATE_HYPOTHESES / HUMAN_APPROVAL)

キュー済み4件(`h-judge-cross-model-agreement-check`,
`h-review-short-acronym-keyterm-false-positive`, `h-computation-aware-laal-enja`,
`h-continuous-rating-cps-calibration`)はすべて既に`approval: approved_by_human`
が付いており、`pending_approval.json`も空だったため、HUMAN_APPROVALでの新たな
承認判断は不要でした(`check-budget 0.0`は`AUTO_APPROVE`)。バックログは上限
(~6件)を超えていないため、PLAYBOOKの指示通り新規仮説は追加していません。

### h-computation-aware-laal-enja の検証

**仮説**: `quality_latency_joint.py`は「AL/LAAL/YAALはトークン単位の
ソース対応付けデータが必要で、このパイプラインのイベントログにはない」という
理由で意図的に実装を見送っていました。しかし音声入力(テキストではなく)の
場合、トークン対応付けは不要で、出力の各文字が「音声時間軸上でいつ出たか」
だけで計算できます(`simulseamless2024-fbk-iwslt`はIWSLT 2024の英→日で
文字単位のSimulEval計算アウェアAL=4.018秒を報告)。実際の実験JSONの
`results.events`は既に全イベントに`playback_offset`(壁時計時間)を
持っています。

**実装**: 新スクリプト
`src/real_time_translation/experiments/simul_latency_metrics.py`を作成。
発話単位で翻訳イベント列をまとめ(`flicker_metrics.group_translation_by_utterance`
と同じ`(asr_start_time, asr_end_time)`キー+`is_utterance_end`連鎖)、
「ある文字位置が最終テキストと二度と食い違わなくなった最初の時刻」を求める
`settle_times()`関数で各文字の確定時刻を計算し、実行全体を1つの連続した
文書として連結、`ma2019-stacl-waitk`のAverage Lagging定義
(理想ペース線との差の平均、ソースを読み切った時点`tau`で打ち切り)を
文字単位・計算アウェアで適用しています。実装前にPLAYBOOKの指示通り
`playback_offset`の意味を`video_segment.py`で確認し(`translation_partial`は
LLMストリーミングの各チャンクごとの本物のタイムスタンプ、
`translation_complete`は順序保証バッファから配信された時刻)、
手計算トイ例に対するユニットテスト5件(`tests/test_simul_latency_metrics.py`)
で検証しました。新規API呼び出しはゼロで、イベントログを持つ全102実験に対して
実行し、`experiments/simul_latency_metrics.csv`に保存、
`quality_latency_joint.py`にも列(`ca_al_char_s`,
`ca_al_vtt_refrate_char_s`)として結合しています。

**結果(仮説は部分的に確認されたが、実装中に見つけた問題が2つある)**:

1. **見つけて修正した問題**: 理想ペース線をクリップの先頭(t=0)に固定すると、
   講義系の実験では最初の発話まで約75秒の無音(話し始め前の空白)があるため、
   すべての文字が「約80〜430秒遅れている」ように見えてしまい、本来の信号を
   完全に埋没させていました(修正前の全体平均は**-6.0秒**、範囲は
   **-271秒〜+237秒**)。理想ペース線の原点を「音声区間
   (最初の`asr_start_time`〜最後の`asr_end_time`)」に合わせるよう修正し、
   この問題は解消しました(モジュールのdocstringに経緯を記載)。
2. **修正後の結果**: 全102実験の`ca_al_char_s`平均は**3.98秒**で、
   SimulSeamlessが報告する計算アウェアAL**4.018秒**と偶然近い値になりましたが、
   これは誤解を招きます。中央値は**0.78秒**で、分布は非常に偏っており
   (最小-39秒、最大199秒)、特に全長75分の講義実験2本のうち1本
   (`llm2025_ep8_zenhan_full_lecture`系)だけが**179〜199秒**という極端な
   値を出しています。
3. **その原因を特定**: もう1本の同種の講義実験(`llm2024_8_part1_full`系、
   こちらは-0.4秒/15.1秒と常識的な範囲)と比較したところ、
   `llm2025_ep8_zenhan_full_lecture`は発話間の無音ギャップが異常に大きい
   (最大104秒・90秒・55秒・30秒・18秒、無音の合計が全体の**16%**)のに対し、
   `llm2024_8_part1_full`は最大ギャップが8.2秒・無音合計**9.4%**でした。
   これはコードのバグではなく、「出力は一定レートで出るはず」という前提が
   講義中の長い無音(スライド切り替えや沈黙など)で大きく崩れるという、
   このメトリック自体の既知の弱点です。文献
   (`polak2026-meta-evaluation-latency-metrics`のSOFTSEGMENTER)が
   指摘している「長尺音声でのセグメンテーション依存性」を、このリポジトリ
   自身のデータで実際に確認した形になります。
4. **より信頼できる中心推定値**: 2本の長時間講義実験と、出力が極端に少ない
   実験(例: 日本語ホスト→英語講演の切り替えテストで出力わずか47文字、
   ノイズが大きすぎる)を除いた93実験(90〜860秒クリップ、出力200文字以上)
   では、平均**0.58秒**・中央値**0.68秒**・標準偏差**10.2秒**でした。
   実験ごとのばらつきはまだ非常に大きいものの、出力文字数が多い中規模の
   クリップ(`asr_keyterms_on`: 39.9秒/1114文字、
   `llm_course_ep8_guest_talk_10min`: 33.0秒/2783文字)はおおむね
   **10〜40秒**程度で、SimulSeamlessの4.018秒より一桁大きい値でした。
   これはAPIベースのカスケード構成(バッチ単位のLLM呼び出し+コミット方針の
   キュー)としては妥当で、既存の`h-mt-queue-wait-decomposition`の
   キュー待ち時間の知見とも整合します。
5. **LAALと呼ばなかった追加列について**: 参照字幕(`wjZofJX0v4M.ja.vtt`)の
   文字数から理想レートを計算する`ca_al_vtt_refrate_char_s`列も実装しましたが、
   検証中にこの値がむしろ自己参照AL(`ca_al_char_s`)より**さらに負(速く見える)**
   になることが分かりました。このパイプラインの日本語訳が参照字幕より
   長い(冗長)傾向があるため、これはPapi et al.のLAALが意図する
   「過剰生成へのペナルティ」の方向とは逆であり、Papi et al. 2022の
   元論文の正確な定式化(`tau`自体を参照レートで再定義する)を再現した
   ものではないことを明示するため、列名を意図的に`LAAL`とせず
   `ca_al_vtt_refrate_char_s`としています。信頼して使える状態ではなく、
   将来のREAD_PAPERSで元論文を確認するまでは参考値扱いとすべきです。

**結論**: 仮説の核心(音声入力なら文字単位・計算アウェアのALがトークン対応付け
なしで計算できる)は実装・検証できました。SimulSeamlessとの比較は
「位置付けの数値であり、厳密な比較ではない」と仮説自身が最初から認めていた
通りで、実際にその通りでした——むしろ予想以上に、特定の実験(長い無音を含む
講義)の内部構造に強く依存することが分かりました。`status`は`tested`
としています。

## 人間への確認が必要な項目

今回新たに人間の判断が必要な項目はありません。`h-continuous-rating-cps-calibration`
は`approved_by_human`/`queued`のままで、実行には引き続き人間(rm-2278)による
視聴・評価作業が必要です。

## 次回への引き継ぎ

- バックログは4件/上限6件です。次回のGENERATE_HYPOTHESESでは、まず残っている
  キュー済み仮説の処理を優先してください:
  `h-review-short-acronym-keyterm-false-positive`(Deepgram WebSocket到達性の
  再確認が必要)、`h-judge-cross-model-agreement-check`(OpenAI REST到達性の
  再確認が必要)。今回はこの2件に着手していません(選んだ`h-computation-
  aware-laal-enja`がライブAPIを必要としなかったため)。
- 今回はDeepgram WebSocket / OpenAI REST到達性を再確認していません。次回
  ライブ実験が必要な仮説を選ぶ際は、この環境での到達性を再確認してください。
- `ca_al_char_s`/`ca_al_vtt_refrate_char_s`の講義実験での極端な値は、無音
  区間の扱いという既知の弱点によるもので、追加の仮説候補になります:
  発話間ギャップ(例: 15秒以上)を理想ペース線から除外する
  セグメンテーション対応版(polak2026のSOFTSEGMENTERに近い発想)。
- `ca_al_vtt_refrate_char_s`の符号がPapi et al.のLAALの意図と逆に見える点は
  未解決です。次回READ_PAPERSで元論文(Papi et al. 2022)を確認する価値が
  あります。

## 変更したPLAYBOOK.md

今回はPLAYBOOK.mdの変更はありません(自己改善の判断は`reflections.md`の
今回のエントリに記載)。
