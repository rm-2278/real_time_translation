# サイクル73 結果レポート(2026-10-24, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル72
の`REFLECT`が「文献ベースはまだ新鮮だが、バックログに余裕があるので
新しい検索から始める」と判断した直後)。今回は`SEARCH_PAPERS →
EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで一気に実行しました。

### SEARCH_PAPERS: cascade対end-to-end比較・flicker/安定性ポリシーの
文献探索

サーベイが指摘していたギャップ(cascade対end-to-end比較の更新、新しい
安定性/flickerポリシー、新しいLLM-as-MTプロンプト技術、ベンダーASR
レイテンシの最新値)を対象にWebSearchを3回実施:

- `stable-prefix-dynamic-lagging-2026`(arXiv 2609.05799): 再翻訳型
  同時翻訳で、コミット済み訳文の長さの「最大値」を保持し続け、後続の
  ソース語が来ても既に表示した訳文が縮まないようにする仕組み
  (dynamic lagging込み)。本リポジトリ自身のprefix-lock/soft-anchor
  系列と概念的に近い。
- `failure-aware-recoverable-llm-translation-2026`(arXiv 2608.09187):
  本番運用を想定したLLM翻訳の障害復旧プロトコル。finish_reason・空
  出力・「ソース言語残留(source-language residue)」の検出、大規模な
  訳抜け(large omission)のヒューリスティック、安定したモデル順序での
  リトライとフォールバックを含む。
- 候補として見つかった`MLLP-VRAIN UPV IWSLT2026`と`NeMo@IWSLT2026`
  (cascadeシステム)は、`papers.json`を実際にtitleで照合した結果、
  既に`mllp-vrain-iwslt2026-simulst`・`nemo-iwslt2026-cascade`として
  登録済み(status: read)だったため、重複として追加しませんでした。
- Deepgram Nova-3/Fluxの最新レイテンシ値も検索しましたが、相互に矛盾
  する(sub-300ms対450ms対250ms、根拠不明)マーケティング系ブログ
  しか見つからず、PLAYBOOK.mdが推奨するarXiv/ACL
  Anthology/Semantic Scholar/OpenReviewの基準を満たさないため、論文
  としては追加していません。

### EXTRACT_PAPERS / READ_PAPERS: 2件を要約・既存コードと照合

このセッションでもarxiv.orgへのWebFetchはEGRESS_BLOCKED
(getaddrinfo ENOTFOUND)だったため、PLAYBOOK.md記載のWebSearch
フォールバックで要約を作成しました。既存コード・実験ログとの照合
(`llm_translator.py`/`pipeline.py`をgrep、`experiments/results.csv`と
`src/real_time_translation/experiments/`をgrep)の結果:

- `stable-prefix-dynamic-lagging-2026`の「コミット長の非縮小不変式」
  は、本リポジトリの`h-hard-prefix-lock-continuation`/`h-soft-anchor-*`
  系列(既にテスト済み)と同種の仕組みであり、新規性はないが近接する
  既存研究として引用しておくべき位置づけ。
- `failure-aware-recoverable-llm-translation-2026`の「大規模訳抜け」
  検出は、`h-soft-anchor-truncation-detector`(サイクル71、テスト済み)
  で既にカバーされている。**一方**、「ソース言語残留」(英語がJA出力
  にそのまま残る)の検出は、本リポジトリのどこにも存在しないことを
  grepで確認済み — これは本物のギャップでした。

### GENERATE_HYPOTHESES: `h-translation-source-residue-detector`を追加

上記の確認済みギャップに基づき、1件の新規仮説(コスト$0、既存clip
のみ使用)を追加しました。`dictionary.csv`に46件の「意図的に英語の
まま」のtarget_term(最大35文字、複数語、例:「Rotary Positional
Embeddings (RoPE)」)が存在することを事前に確認し、検出ロジックが
これらを誤検出しないよう明記しました。バックログは3 queued + 2
proposed = 5/6で、上限内です。

### HUMAN_APPROVAL: 自動承認、RUN_EXPERIMENTSへ

`check-budget(0.0)`は`AUTO_APPROVE`、`uses_existing_clips=true`の
通常経路に従い承認・queued化。もう1件の`proposed`
(`h-judge-repeat-independent-batches-corrected-design`)は既に
`needs_human`として`pending_approval.json`に登録済みで、
「自己承認ブロック」(サイクル36で確認済みの環境レベルの安全策)に
より無人セッションからは動かせないため、今回も手を付けていません。
rm-2278様の確認をお待ちしています。

### RUN_EXPERIMENTS / ANALYZE_RESULTS: 残留英語検出、結果は「大きな
残留なし」

`source_residue_detector.py`を新規実装し、既存の実験JSON 25件
(`translation_complete`イベント合計7,484件)に対して実行しました。
ライブAPI呼び出しは一切なし(費用$0)。

一見すると535件(約7.1%)がフラグ付けされ、予測(「本物の残留はほぼ
ゼロ」)と矛盾するように見えました。しかし実際にフラグされた文字列
の語数を集計すると、80%(429件)が単語1つ、2語が156件、3語が48件、
**4語を超えるものは一件もありませんでした**(最長は"Free Local Edge
AI"など4語)。印字された具体例を目視確認したところ、これらはすべて
「Josh」「Jessam」「Google」「Salesforce」「Gemini」「Gemma」
「self attention」「speculative decoding」のような、人名・社名・
ML専門用語の正当な英語のまま(コードスイッチング)であり、文単位の
丸ごと未翻訳(今回参照した論文が検出対象とする本物の障害モード)は
一件も見つかりませんでした(5語以上のフラグは実際に0件だったことを
再確認済み)。

結論: 仮説は`tested`としてクローズしましたが、`abandoned`ではあり
ません。検出スクリプト自体は健全な基盤(dictionary.csv対応、イベント
内比較のみ)として今後の回帰チェックに使えますが、今回のクリップ群
では「本物のソース言語残留」の証拠は見つかりませんでした。副次的な
観察として、`dictionary.csv`に未登録の技術用語(chatbot、embedding、
word embedding、multilayer perceptronなど)が複数クリップで繰り返し
英語のまま出力されていることが分かりましたが、これは今回の仮説の
範囲外として深掘りはしていません(深さ優先の方針のため)。

## 人間への確認が必要な項目

- 前回から継続: `h-judge-repeat-independent-batches-corrected-design`
  が`needs_human`のまま待機中です。`pending_approval.json`を参照し、
  承認いただければ次サイクルで実行します。
- 新規: 今回見つかった「`dictionary.csv`未登録の繰り返し技術用語」
  (chatbot、embedding等)をdictionaryに追加すべきか、ご判断を
  いただければ今後の仮説として検討します(緊急性は低いです)。

## 環境メモ

DEEPGRAM_API_KEY・GOOGLE_API_KEYは共に設定済み、ffmpegも利用可能
でしたが、今回選んだ仮説はいずれもライブAPIを必要としない
retroactive分析だったため、実際のDeepgram/Gemini到達性チェックは
今回は省略しました(次回ライブ実験が必要な仮説を選ぶ際に再確認して
ください)。
