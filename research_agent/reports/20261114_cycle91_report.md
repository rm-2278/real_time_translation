# サイクル91 結果レポート (2026-11-14)

## 概要: 新規論文2件を読了、仮説バックログは既に6/6上限、3件の実験キューは全て環境/人手待ちで変化なし

本サイクルはスケジュール実行(cron)によるもので、前回セッション
(同日 2026-11-14 00:08 UTC)が `READ_PAPERS -> GENERATE_HYPOTHESES` まで
進めた状態から再開しました。本セッションでは
`GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS`
を一通り進め、本レポート(`WRITE_REPORT`)を作成しています。

## 前回までの読了論文(cycle 91 READ_PAPERS、本セッション開始前)

- **thompson2609**(arXiv:2609.11408): DHH(ろう・難聴)視聴者216名による
  ライブTVキャプション評価調査。標準的なテキスト精度指標(WER/ACE2/NER)は
  技術(人間の速記 vs ASR生成)によって視聴者評価との対応がずれること、
  表示遅延自体がテキスト精度と独立に知覚品質に影響することを示す。本リポジ
  トリの既存レイテンシ品質スレッド(`ja-subtitle-reading-speed-norms` 等)の
  動機付けとしては有用だが、直接実装可能な新しいメカニズムではない。
- **uspat11715475**(米国特許): MeetDotと同型の「単語推測ゲーム」方式による
  外的(タスクベース)評価手法。正解語を見せて話者に説明させ、その説明を
  翻訳して一致度を測り、応答時間と融合した単一スコアを作る。本リポジトリ
  には chrF/NER のテキスト精度指標とflicker指標しかなく、こうした
  accuracy+latency融合型の外的評価は未着手の方向性。LLMを説明者/判定役の
  代わりに使えば本リポジトリのAPIのみの構成でも実装可能そうだが、MeetDot
  本体の論文/コードでの検証が必要。

`experiments/results.csv`・`src/` を "meetdot"・"word-guess"・"extrinsic"・
"dhh"・"ace2" 等でgrepした結果、該当する既存実装はゼロ件を確認済み
(papers.json `_read_cross_reference_note_cycle91`)。

## GENERATE_HYPOTHESES: 新規仮説は追加せず(バックログが既に6/6上限)

カウントした結果、`hypotheses.json` は既に
`queued` 3件 + `proposed` 3件 = 6件で、PLAYBOOK.mdの上限(約6件)に
達していました:

- `queued`(`approval: approved_by_human`、全て環境/人手で実行待ち):
  `h-judge-cross-model-agreement-check`、
  `h-review-short-acronym-keyterm-false-positive`、
  `h-continuous-rating-cps-calibration`
- `proposed`(`approval: needs_human`、既に `pending_approval.json` に
  記録済みで、PLAYBOOKの自己承認禁止ルールにより人間の判断待ち):
  `h-judge-repeat-independent-batches-corrected-design`、
  `h-soft-anchor-gate-exempt-utterance-end`、
  `h-judge-cross-gemini-model-agreement-check`

6件は全て `approval` が設定済み(`null` のまま放置されたものはゼロ)で、
未処理のHUMAN_APPROVAL作業ではないことを確認しました。PLAYBOOKの
depth-over-breadth方針に従い、本サイクルは新規仮説を書きませんでした。
`uspat11715475`(単語推測ゲーム型の外的評価)は、次に枠が空いたときの
有力な候補として記録しました — 具体的で実装可能性が高く、まだどの仮説の
`grounded_in` にも使われていません。

## HUMAN_APPROVAL / RUN_EXPERIMENTS: 3件のキューは全て変化なし、実験ゼロ件

環境チェック(本サイクルは新しいコンテナでの実行):
`DEEPGRAM_API_KEY`・`GOOGLE_API_KEY`・`OPENAI_API_KEY` は全て設定済み、
`ffmpeg` も利用可能、`experiments/refs/audio/` にキャッシュ済みクリップも
存在を確認しました。

ただし、PLAYBOOKの「reduced-cadence」規定(同一のブロック結果が5回以上
連続再現している場合、直前のライブ確認が10サイクル以内なら再確認を省略
してよい)に従い、ネットワーク到達性の再プローブは行いませんでした。
前回のライブ確認はサイクル90(2026-11-12、わずか1サイクル前)で、以下が
確認済みです:

- `api.openai.com`: プロキシによるCONNECTトンネル拒否(403)— 10サイクル
  以上連続で同一シグネチャ
- Deepgramの `listen` WebSocketアップグレード: `HTTP 400 Connection header
  did not include upgrade` — サイクル5以降、毎サイクル同一シグネチャ

この2件はキーの有無やREST到達性(どちらも200で到達可能)とは独立の、
プロキシ/ホストレベルの既知のブロックです。3件目の
`h-continuous-rating-cps-calibration` はネットワークとは無関係に、
rm-2278さん自身の視聴・評価時間(20-30分)と、参照先の元動画
(`video/LLM2025...mp4`)そのものを必要とします。本コンテナにも
`video/` ディレクトリ・該当ファイルは存在しないことを `find`/`ls` で
再確認しました。

結果として、キュー中の3件全てが今回も実行不可でした。新規実験は
ゼロ件、新規API消費もゼロドルです(`log-cost 0.0` で記録)。

## ANALYZE_RESULTS: パススルー

新規実験が無いため、分析対象もありません。PLAYBOOKの注記通り、
`RUN_EXPERIMENTS` から `WRITE_REPORT` への直接遷移は許可されていない
ため、`ANALYZE_RESULTS` を経由してここに到達しています。

## 人間への確認依頼(変更なし)

以下は引き続き rm-2278 さんの判断/作業待ちです:

1. `pending_approval.json` の3件(上記 `proposed` 仮説)— 自己承認が
   実行環境のポリシーでブロックされているため、承認するかどうかの
   判断をお願いします。
2. `h-continuous-rating-cps-calibration` — 20-30分ほどの視聴・評価時間と、
   元動画ファイル(`video/LLM2025...mp4`、またはすでにリポジトリに
   存在する別クリップへの再対応)をご用意いただけると実行できます。

## 次サイクルへの申し送り

- バックログは依然6/6上限。`uspat11715475`(単語推測ゲーム型外的評価)
  が次に枠が空いた際の最有力候補。
- ネットワークブロック2件は次回も reduced-cadence 規定により再確認を
  省略可能(次のライブ再確認は遅くともサイクル96〜97頃、10サイクル
  tripwireに該当する前に)。
