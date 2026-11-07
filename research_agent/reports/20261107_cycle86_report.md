# サイクル86 結果レポート(2026-11-07, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル85の
`REFLECT`が前回のセッションで完了済み)。今回も
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES →
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで
実行しました。

## SEARCH_PAPERS: 新規1件を発見(IWSLT 2026のtest-time adaptation論文)

一般的な新着探索として2回のWebSearchを実行しました
(1. SimulST/wait-k/LocalAgreement/チャンキング方針、
2. LLM judgeの翻訳品質較正/確信度推定)。ほとんどの結果は既存の
`papers.json`に登録済みの論文(DOA/papi2026、Hikari/causal-alignment2026、
MLLP-VRAIN、CUNI pocket-offline、wmt2026-verbalized-confidence-judge)
でしたが、新規候補1件を発見しました:

`xing2026-iwslt-test-time-adaptation-offline-mfm-simulst`
(ACL Anthology `2026.iwslt-1.27`, Xing・Yu・Liu・Meng, IWSLT 2026)。

この論文は、最初の検索結果ではIDが`2026.iwslt-1.24`と誤表記されており
(既存登録済みのMLLP-VRAIN論文の実際の番号と同じ番号だったため)、
追加の絞り込み検索(`wait-(k,s,n)`、`Silero VAD`、`test-time adaptation`
などの固有表現で検索)で正しいID(`2026.iwslt-1.27`)を特定し、
MLLP-VRAIN論文とは別の、独立したシステムであることを確認しました。
既存の`papers.json`の全URLと突き合わせ、重複でないことを確認済みです。

その他の候補(arXiv 2508.06225の「LLM judgeの過信」論文、
arXiv 2606.01136のPali→英語監査論文、ICML2026の信頼区間報告論文、
CalibJudge)は、翻訳judge特有のメカニズムを持たない一般的な手法、
または対象言語ペアが異なる個別事例、あるいはarXiv/ACL等のPLAYBOOK
推奨ソースに該当しないため、あえて追加しませんでした。

## EXTRACT_PAPERS → READ_PAPERS: 薄い抽出(ベースモデルの正体が未確認)

`aclanthology.org`への`WebFetch`は今回も`EGRESS_BLOCKED`
(DNS解決失敗)でした。これまでの全サイクルと同じ現象です。PLAYBOOK
記載のフォールバック通りWebSearchで代替しましたが、今回は数値的な
結果(BLEU/latency等)やベースとなる基盤モデルの具体的な名称は
検索スニペットからは回収できませんでした。回収できた内容:

- ハイブリッドチャンキング: Silero VADによる無音区間検出と、
  固定の最小/最大チャンク長を組み合わせた、3つ目の方式
  (本リポジトリ自身のendpointingベースのチャンキングとも異なる)。
- `wait-(k,s,n)`方針: wait-kを一般化した3パラメータの決定的な
  read/writeポリシー(各パラメータの正確な定義はスニペットからは
  回収不可)。
- マルチターン会話形式でのプロンプティング: 音声チャンクごとに
  新しい会話ターンとして基盤モデルに与える構造。ファインチューニング
  ではなく、自然言語の指示と「厳格な生成制御」のみで適応させている
  点が特徴。

**未解決の重要な論点**: 適応対象となる「オフラインのマルチモーダル
基盤モデル」の具体的な名称が、検索スニペットからは特定できません
でした。近隣のIWSLT 2026論文に出てくる他の候補(CUHKSZのQwen3-30B-
Instruct、CUNIのCanary-1B-v2)とは異なることは確認できましたが、
それ以上の特定はできていません。このモデルがAPI経由で利用可能か、
自前ホスティングが必要な自社学習済みモデルかが分からない限り、
本リポジトリのAPIオンリー構成(Gemini/OpenAI)でこの論文の手法が
そもそも実行可能かどうか判断できません。

`experiments/*.json`、`experiments/results.csv`、`git log`との
突き合わせにより、ハイブリッドVADチャンキング・`wait-(k,s,n)`方針・
マルチターン会話形式のいずれも、既存の仮説・実験には含まれていない
ことを確認しました(重複ではない、新しい角度です)。ただし、
マルチターン会話形式のプロンプティングのみは、ベースモデルの正体に
関わらず本リポジトリ自身の`llm_translator.py`のプロンプト構造に
対してそのまま検証可能なため、次回バックログに空きが出た際の最有力
候補として記録しました。チャンキング/`wait-(k,s,n)`の角度は、
ベースモデルの正体が確認できるまで保留とします。

## GENERATE_HYPOTHESES: 新規仮説なし(バックログが依然上限)

`queued`3件・`proposed`3件の計6件で、PLAYBOOKの上限(6件)に到達
したままのため、新規仮説は追加しませんでした(サイクル79-86と
同じ状況)。

## HUMAN_APPROVAL: 処理対象なし

6件すべてに`approval`が既に設定済みで、未処理の項目はありませんでした。
`approved_by_human`の`queued`3件が残っていたため、PLAYBOOKのルールに
従い`RUN_EXPERIMENTS`へ進みました。

## RUN_EXPERIMENTS: 既存の3件の`queued`仮説すべてが依然ブロック中

環境チェックを再実行しました:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`: いずれも環境に
  設定済み。`ffmpeg`も利用可能。キャッシュ済みクリップも存在。
- Deepgram REST(`/v1/projects`): **200**(到達可能)。
- Gemini REST(`/v1beta/models`): **200**(到達可能)。
- OpenAI REST(`/v1/models`): **CONNECT拒否(403)**(`curl`終了コード56)。
  サイクル72以降と完全に一致する再現で、**今回で9回連続**です。
- Deepgram **listen-websocket**ハンドシェイク: `python3.13`で新規に
  `.venv`を作成し、実際のWebSocketアップグレードを試したところ、
  **HTTP 400 "Connection header did not include 'upgrade'"**で失敗。
  サイクル5以降繰り返し確認されている既知のブロックと完全に一致します。

既存の3件の`queued`仮説はいずれも今回も実行不可でした
(`result_summary`/`blocked_reason`にサイクル86分の再確認を追記済み):

1. `h-judge-cross-model-agreement-check` -- OpenAI接続拒否のため継続
   ブロック(9回連続同一結果)。
2. `h-review-short-acronym-keyterm-false-positive` -- Deepgram
   listen-websocket失敗のため継続ブロック。
3. `h-continuous-rating-cps-calibration` -- 環境の問題ではなく、
   rm-2278さん自身の視聴時間(20-30分程度)が必要な人間参加型の仮説
   のため、今回も状況は変化ありません。

新規の実験は一件も実行せず、`ANALYZE_RESULTS`(分析対象なし)を経て
本レポートの作成に進みました。コストログには$0(到達性チェックのみ、
新規API呼び出しなし)を記録しました。

## 環境面のメモ(重要: OpenAIブロックが9回連続に到達)

サイクル85までと変化なく、以下の2つの構造的制約が今回も再確認され
ました:

- OpenAI APIへの接続が明示的にブロックされている(ポリシーによる
  CONNECT拒否)。
- Deepgramのストリーミング(WebSocket)接続のアップグレードハンドシェイク
  がプロキシによって壊れている(RESTは問題ない)。

OpenAI側の制約はサイクル72・78・80・81・82・83・84・85・86の
**9回連続**で同一の再現を示しています。サイクル85の振り返りで
「9回目か10回目の同一結果が出た時点で、PLAYBOOKに毎サイクルの
再チェックを省略する条項を追加するかどうかを実際に決めるべき」と
あらかじめ指摘されていたため、この判断は本サイクルの`REFLECT`で
行います。

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

`REFLECT`に進み、今回の振り返り(IWSLT 2026のtest-time adaptation
論文を発見し`read`まで進めたが、ベースモデルの正体という重要な
論点が未解決であること、OpenAIブロックが9回連続で安定し、PLAYBOOKの
再チェック省略条項について判断すべき時点に来たこと、バックログが
依然上限で埋まっていること)を`reflections.md`に記録したうえで、
次サイクルの方針を決定します。
