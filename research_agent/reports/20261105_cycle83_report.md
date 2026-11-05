# サイクル83 結果レポート(2026-11-05, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル82の
`REFLECT`で「次は新規の文献探索から」と決定された通り)。今回は
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES →
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで
実行しました。

## SEARCH_PAPERS: WebSearchを6回実行、新規2件をスタブ登録

サーベイが指摘していたギャップ(安定性/フリッカー方針の新研究、
cascade対end-to-end比較、LLMプロンプティング手法、ASRベンダーの
最新レイテンシ数値、judge信頼性、Gemini Flashのストリーミング翻訳
プロンプティング)を狙って6回のWebSearchを実行しました。

サイクル82のPLAYBOOK修正(新規スタブ前に既存`papers.json`全エントリの
`url`フィールドと突き合わせる)を忠実に実施したところ、見つかった
候補のほとんど(REINA-TAN、DOA、prefix-to-prefix、NeMo@IWSLT2026、
Conversational SimulMT、EAST、AA-WER Streamingなど)は既存エントリと
URLが完全一致する既知の論文でした。タイトルの言い回しが検索ごとに
微妙に変わっていても、URLで突き合わせたので重複スタブは作成せずに
済みました(サイクル82が修正した問題そのものが今回機能した形です)。

新規にスタブ登録したのは2件:

1. `wmt2026-verbalized-confidence-judge`
   (`aclanthology.org/2026.wmt-main.107/`) -- WMT2026の共有タスク
   システム論文。judgeに数値スコアだけでなく言語化された確信度
   (verbalized confidence)も出させるアプローチと思われます。
2. `compactqe2026-small-open-weight-qe`
   (`arxiv.org/abs/2605.15763`, CompactQE) -- 30B未満のオープンウェイト
   LLMで翻訳品質推定(スコア+MQM風エラー注釈+訂正+ポスト編集を単一
   プロンプトで出力)を行う手法。

## EXTRACT_PAPERS / READ_PAPERS: 1件抽出・既読に、1件は意図的に保留

`arxiv.org`と`aclanthology.org`への`WebFetch`がこのセッションでは
`EGRESS_BLOCKED`(`getaddrinfo ENOTFOUND`)だったため、WebSearchの
フォールバックで抽出を試みました。

- **CompactQE**: 2つの独立した二次情報源(arxiv.org/htmlミラーの
  スニペットとACL Anthology `2026.eamt-1.9`の記載)でタイトル・著者・
  内容が一致したため、信頼できると判断し`status: "extracted"`→
  `"read"`まで進めました。`experiments/*.json`とgit logを確認した
  ところ、オープンウェイト/ローカルQEモデルを試した実験は本リポジトリに
  一件もなく(全仮説がGemini API経由のgemini-3-flash-preview /
  gemini-3.1-flash-liteのみ使用)、既存研究との重複はありませんでした。
  本リポジトリのアーキテクチャ(API専用、自前モデルホスティングなし)
  には直接は適用できませんが、「セグメント/スパン単位のQEはモデル
  サイズを問わず精度が低い」という知見は、本リポジトリ自身のjudge
  ノイズに関する発見(reflections.md サイクル19-21)と独立した角度から
  一致しており、将来のレポート/振り返りで引用する価値があります。
- **`wmt2026-verbalized-confidence-judge`**: WebSearchで最有力候補
  (Servajean, Sohrab, Kunitomo-Jacquin, Rikters著、WMT26プログラムに
  掲載)を見つけましたが、著者名を指定した再確認検索ではこの論文が
  一件もヒットしませんでした。IDとタイトルの対応が独立に確認できて
  いないため、PLAYBOOKが繰り返し強調する「抽象的推論でなく実証的に
  確認する」という教訓に従い、`status`を`"extracted"`に進めず
  `"found"`のまま保留しました。将来、`aclanthology.org`への直接
  WebFetchが可能になった時、または別の方法で確証が取れた時に
  抽出を完了する想定です。

## GENERATE_HYPOTHESES: 新規仮説なし(バックログが上限に到達済み)

`queued`3件・`proposed`3件(いずれも`approval: needs_human`、
`pending_approval.json`に記録済み)の計6件で、PLAYBOOKの上限
(6件)にちょうど到達していたため、新規仮説は追加しませんでした。

## HUMAN_APPROVAL: 処理対象なし

`approval`が未設定、または値が`"proposed"`の仮説は存在しませんでした。
`queued`な3件(いずれも`approval: approved_by_human`)が残っていたため、
PLAYBOOKのルールに従い`RUN_EXPERIMENTS`へ進みました。

## RUN_EXPERIMENTS: 既存の3件の`queued`仮説すべてが依然ブロック中

環境チェックを再実行しました:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`: いずれも環境に
  設定済み。`ffmpeg`も利用可能。
- Deepgram REST(`/v1/projects`): **200**(到達可能)。
- Gemini REST(`/v1beta/models`): **200**(到達可能)。
- OpenAI REST(`/v1/models`): **CONNECT拒否(403)**(`curl`終了コード
  56、プロキシのステータスで`connect_rejected`)。サイクル72以降と
  完全に一致する再現です。
- Deepgram **listen-websocket**ハンドシェイク: `python3.13`で新規に
  `.venv`を作成し(`uv pip install -e ".[experiments]"`、
  `websockets==17.4`)、実際のWebSocketアップグレードを試したところ、
  **HTTP 400 "Connection header did not include 'upgrade'"**で失敗。
  サイクル5以降繰り返し確認されている既知のブロックと完全に一致します。

既存の3件の`queued`仮説はいずれも今回も実行不可でした(各
`blocked_reason`にサイクル83分の再確認を追記済み):

1. `h-judge-cross-model-agreement-check` -- OpenAI接続拒否のため継続
   ブロック。
2. `h-review-short-acronym-keyterm-false-positive` -- Deepgram
   listen-websocket失敗のため継続ブロック。
3. `h-continuous-rating-cps-calibration` -- 今回初めて`RUN_EXPERIMENTS`
   を試行しました。これは環境の問題ではなく、rm-2278さん自身の
   視聴時間(20-30分程度)が必要な人間参加型の仮説です。付随する
   キー入力ロギングツールは今回は作成しませんでした:
   `caption_video_demo.py`がmacOS専用のシステムフォントパス
   (`/System/Library/Fonts/ヒラギノ角ゴシック...`)をハードコードして
   おり、このLinuxクラウドコンテナ上では動画の再レンダリング自体が
   そのままでは失敗するためです(フォントパスの修正が別途必要)。
   実行できない工程のためのツールを今回作っても使えないので、
   スコープを広げずに見送りました。

PLAYBOOKの指示(環境が実験を実行できない場合は結果を捏造せず、ギャップを
記録して報告に進む)に従い、新規の実験は一件も実行せず、`ANALYZE_RESULTS`
(分析対象なし)を経て本レポートの作成に進みました。

## 環境面のメモ(継続事項、変化なし)

サイクル82から変化なく、以下の2つの構造的制約が今回も再確認されました:

- OpenAI APIへの接続が明示的にブロックされている(ポリシーによるCONNECT
  拒否)。
- Deepgramのストリーミング(WebSocket)接続のアップグレードハンドシェイク
  がプロキシによって壊れている(RESTは問題ない)。

この2つの制約がある限り、`h-judge-cross-model-agreement-check`と
`h-review-short-acronym-keyterm-false-positive`はこのクラウド実行環境
では原理的に実行不可能です。`h-judge-cross-gemini-model-agreement-check`
(Geminiのみで完結する設計、`pending_approval.json`参照)が承認されれば、
この制約を回避して次サイクルで実行できる見込みです。

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

`REFLECT`に進み、今回の振り返り(URL突き合わせルールが実際に重複を
防いだことの確認、judge信頼性文献を2件追加できたこと、
`h-continuous-rating-cps-calibration`で初めて具体的なブロック理由
〈フォントパスの非互換性〉が記録されたこと、3件の`queued`仮説のうち
2件が継続して環境制約下にある状況)を`reflections.md`に記録したうえで、
次サイクルの方針を決定します。
