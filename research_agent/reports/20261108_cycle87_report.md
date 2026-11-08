# サイクル87 結果レポート(2026-11-08, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル86の
`REFLECT`が前回のセッションで完了済み)。今回も
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES →
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで
実行しました。

## SEARCH_PAPERS: xing2026論文のベースモデル問題を解決(Qwen3-Omni)

サイクル86の振り返りで提案されていた角度に沿って、前回「未解決」の
まま`read`登録されていた`xing2026-iwslt-test-time-adaptation-offline-
mfm-simulst`(IWSLT 2026、`2026.iwslt-1.27`)の適応対象基盤モデルの
正体を特定するため、2通りの異なる言い回しでWebSearchを実行しました
(著者名を直接指定する検索、およびIWSLT 2026の各システムのベース
モデル一覧を探す検索)。両方とも独立に**Qwen3-Omni**という同一の
モデル名に到達し、さらに2回目の検索では「CUHKSZの別チーム(すでに
`cuhksz2026-iwslt-qwen3omni-wait-token`として登録済み)も同じ
Qwen3-Omniファミリーを使っているが、LoRAファインチューニングという
別のアプローチ」という形で、xing2026論文とは明確に別物であることも
確認できました。

Qwen3-Omniはオープンウェイトの自己ホスト型モデル(CUHKSZの論文も
vLLM上でこのモデルをセルフホストしています)であるため、この論文の
基盤モデル適応手法そのもの、および付随するハイブリッドVADチャンキング
/`wait-(k,s,n)`方針は、本リポジトリのAPIオンリー構成(Gemini/OpenAI)
では再現不可能であることが判明しました -- `exposst2026`/`sasst2026`/
`StreamUni`/`SimulMEGA`と同じ「適用不可」カテゴリです。一方、
マルチターン会話形式でのプロンプティングという角度は、この論文の
特定の基盤モデルに依存せず本リポジトリ自身のAPIモデルに対して試せる
ため、次回バックログに空きが出た際の候補として引き続き有効です。

なお`aclanthology.org`への`WebFetch`は今回も再試行しましたが
`EGRESS_BLOCKED`(DNS解決失敗)でした。そのため今回の結論もWebSearch
スニペットベースですが、2つの独立した言い回しの検索が同一の具体的な
モデル名に一致したことは、前回の単発・未確定の結果より強い根拠と
判断しました。`papers.json`にこの解決内容を追記しています。

新着文献の一般探索も1件実行しましたが、ヒットはすべて既存登録済み
(SASST、CUHKSZ-1.13、DOA/papi2026、prefix2prefix2026、
cmu-nvidia2026-HPO)か、過去にファインチューニング必須という理由で
あえて見送った論文(StreamUni、FASST、EASiST)のいずれかで、新規
stub登録はありませんでした。

## EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES: パススルー

新規に`found`状態の論文、`extracted`状態の論文がなかったため、両状態
とも素通りしました。`GENERATE_HYPOTHESES`では、バックログが`queued`
3件・`proposed`3件の計6件でPLAYBOOKの上限(6件)に到達したままのため
(サイクル79-86と同じ状況)、新規仮説は追加しませんでした。`proposed`
3件もすべて`approval`が`needs_human`で確定済みであり、未処理の
`HUMAN_APPROVAL`案件はありませんでした。

## HUMAN_APPROVAL: 処理対象なし → RUN_EXPERIMENTS へ

6件すべてに`approval`が既に設定済みで、`approved_by_human`の`queued`
3件が残っていたため、PLAYBOOKのルールに従い`RUN_EXPERIMENTS`へ
進みました。

## RUN_EXPERIMENTS: h-continuous-rating-cps-calibrationでコード側の
## ブロッカーを1つ解消(人間の視聴待ちのみに整理)

環境チェック:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`: いずれも設定済み。
  `ffmpeg`も利用可能。キャッシュ済みクリップも存在。
- Deepgram REST: **200**(到達可能)。Gemini REST: **200**(到達可能)。
- OpenAI REST: **CONNECT拒否(403)**。サイクル72以降と完全に一致する
  再現で、**今回で10回連続**です(`h-judge-cross-model-agreement-check`
  のブロッカー)。
- Deepgram listen-websocketハンドシェイクは、サイクル86で追加した
  PLAYBOOKの「再チェック頻度低減」条項(5回以上連続で同一結果の場合、
  10サイクル以内なら前回確認結果を再利用してよい)に従い、今回は
  実機再チェックを省略し、サイクル86の確認結果(HTTP 400
  "Connection header did not include upgrade")を再利用しました
  (`h-review-short-acronym-keyterm-false-positive`のブロッカー)。

この2件は今回も環境制約により実行不可でしたが、3件目の
`h-continuous-rating-cps-calibration`については、環境ブロッカーでは
なく人間の視聴時間が必要な仮説である一方、サイクル83で指摘されていた
別の問題(`caption_video_demo.py`のフォントパスがmacOS専用で
Linux上では動かない)が未修正のまま残っていたため、今回はこの
コード側の問題に実際に着手しました:

1. `FONT_PATH`をLinux環境でも動くよう修正 -- このサンドボックスに
   実在する`/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf`
   (IPAGothic)へのフォールバックを追加。
2. `caption_video_demo.py`に`--reading-units-per-sec`オプションを
   新規追加 -- この仮説が必要とする4 CPS対8 CPSの比較には、
   従来ハードコードされていた`format_readable`のデフォルト値(4.0)を
   上書きする手段がなく、これも未実装でした。
3. 使い捨ての合成実験JSON(本物の実験ではなく、コードの動作確認専用。
   `results.csv`には記録せず、リポジトリにも残していません)を使い、
   既存の短いクリップ`experiments/clips/llm2025_cmp/A.mp4`に対して
   両方の`reading_units_per_sec`値で実際にレンダリングし、ffmpegが
   正常終了すること、SRTの字幕時間が想定通り変化すること、出力
   フレームの日本語文字が文字化け(tofu)なく正しく表示されることを
   目視確認しました。`ruff check`も通過しています。

ただし、この仮説が元々指定している実験JSON
(`20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json`)が参照する
元動画ファイル(`video/LLM2025...mp4`、rm-2278さんのローカル講義
収録)はこのクラウドコンテナには存在せず(`experiments/refs/`の
キャッシュ済みYouTubeクリップと、小さな比較用クリップ
`experiments/clips/llm2025_cmp/{A,B,C}.mp4`のみ存在)、フォント問題
とは別の、まだ解消されていない環境ギャップとして記録しました。
キー入力ロギング用の評価ツール本体(1-4のレーティングを記録する
スクリプト)は、今回あえて作成していません -- 実際に人間が操作して
初めて妥当性を検証できるものであり、人間不在のスケジュール実行で
未検証のまま作るリスクの方が大きいと判断したためです。

結果として、この仮説の残課題は「フォント/CLIツール」から「(a)
rm-2278さん自身の約20-30分の視聴・評価時間」と「(b)元動画ファイルを
将来のセッションに用意するか、または既存の小さなクリップに合わせて
プロトコルを調整するか」の2点に整理されました。

新規の実験・翻訳API呼び出しは一切行っていないため、コストログには
$0を記録しました。

## 環境面のメモ(OpenAIブロックが10回連続に到達)

サイクル86の振り返りで決めた「再チェック頻度低減」条項が今回初めて
実際に機能し、Deepgram listen-websocketの再チェックを省略できました
(ツール呼び出しの節約につながりました)。OpenAI側の制約は
サイクル72・78・80・81・82・83・84・85・86・87の**10回連続**で
同一の再現を示しています。10回目に到達したこと自体は、サイクル86の
条項追加ですでに想定・対応済みのため、今回のREFLECTで新たな
PLAYBOOK変更が必要かどうかはこの先の振り返りで判断します。

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

また、`h-continuous-rating-cps-calibration`の実施には、
(a) rm-2278さんご自身の約20-30分の視聴・評価時間、
(b) 元の講義動画ファイル(`video/LLM2025応用編_第8回前半_260128.mp4`)
をクラウドセッションに用意いただくか、代わりに既存の短いクリップで
プロトコルを調整する判断、のいずれかが必要です。

## 次のステップ

`REFLECT`に進み、今回の振り返り(xing2026論文のベースモデル問題を
解決できたこと、`h-continuous-rating-cps-calibration`のコード側
ブロッカーを1つ解消できたこと、OpenAIブロックが10回連続に達したが
サイクル86の条項ですでに対応済みであること、バックログが依然上限で
埋まっていること)を`reflections.md`に記録したうえで、次サイクルの
方針を決定します。
