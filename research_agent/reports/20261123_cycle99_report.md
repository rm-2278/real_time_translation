# サイクル99 結果レポート (2026-11-23)

## 概要: 辞書/ドメイン知識プロンプト系統の新規文献3件を追加、新仮説は追加せず、実験は既知の環境ブロックで未実施

本サイクルはスケジュール実行(cron)によるもので、`current_state: SEARCH_PAPERS`
(前セッション=サイクル98の `REFLECT` が新サイクル開始として進めた状態)から
再開しました。本セッションでは
`SEARCH_PAPERS -> EXTRACT_PAPERS -> READ_PAPERS -> GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS -> WRITE_REPORT`
まで進めています。

## SEARCH_PAPERS / EXTRACT_PAPERS / READ_PAPERS: 新規文献3件を追加

サイクル98のreflectionが「次サイクルで継続する価値あり」と明示的に指摘した
LLM-as-MTプロンプト技法/ドメイン知識注入の系統(`domainknowledge2025` が
開いた文脈)を対象にWebSearchを実施しました。カスケード対エンドツーエンド
比較、新しい字幕安定化ポリシー、ASRベンダーの最新レイテンシ数値についても
併せて検索しましたが、候補はすべて既に `papers.json` に登録済み(URL照合で
確認)でした — 97件という既存文献ベースの網羅性の高さを改めて裏付ける結果です。

新規に登録できたのは、辞書/検索拡張(RAG)プロンプト技法の系統3件です:

- **`refine2024-constraint-aware-iterative-dict-rag`**(arXiv:2411.08348):
  ソース文からキーワードを抽出し、埋め込みベースの最近傍検索で二言語辞書から
  訳語を取得してプロンプトに注入したうえで、LLM自身が語彙・意味制約に照らして
  出力を反復的に自己チェック・修正する2段階手法。本リポジトリの辞書注入
  (`dictionary.py`)は固定の用語集参照のみで、埋め込み検索や自己修正ステップは
  持たない点が異なります。
- **`dragft2024-dict-rag-finetune-domain-mt`**(arXiv:2402.15061, DragFT):
  辞書強化プロンプト(ソース文中の用語を訳語に直接置き換える"dict-rephrasing")
  + RAGによる実例検索 + ファインチューニングの3部構成。IT/Law/Medical分野で
  GPT-3.5/GPT-4oを上回ると報告。注目点: BLEU(表層・用語精度寄り)の改善幅が
  COMET(意味的忠実度寄り)より大きいという非対称な結果 — 本リポジトリの
  辞書あり/なし比較実験(`20260724_llm2024_8_part1_full.json` 対
  `..._baseline_no_dict.json`)でも同様の非対称性が起きうるか気になる点ですが、
  両ファイルとも `chrf_score`/`xcomet_score` が未計算(null)のため、現時点では
  検証不可能です。なお、ファインチューニング部分は本リポジトリのAPI専用
  アーキテクチャでは利用不可(セルフホストモデルへのアクセスがないため、
  過去に `exposst2026`/`sasst2026` を除外したのと同じ制約)。
- **`shpigunov2026-unlp-enuk-legal-rag-mt`**(UNLP 2026, Shpigunov):
  英語→ウクライナ語の法律・軍事分野翻訳で、コーパスレベルの翻訳文ペアを
  埋め込み検索(Qdrant、コサイン類似度)で取得しGemma 3/Gemini 3 Flashへの
  実例として与えるRAG手法。言語対・分野とも本リポジトリとは異なりますが、
  「検索した実例が用語集より優れる」という `domainknowledge2025` の知見と
  同系統の裏付けです。**未解決の矛盾点**: 学会目次では別の著者名
  (Lavreniuk/Mudryi/Chaklosh)が記載されており、論文本体の著者名
  (Shpigunov単独)と食い違っています — 今後この論文を仮説の根拠として
  引用する際は、正式PDFで確認してから扱う必要があります。

`arxiv.org`/`aclanthology.org` へのWebFetchは今回もENOTFOUND(前サイクル
までと同じ広範なブロック)だったため、Playbook既定のWebSearch代替手法で
要旨情報を取得しました。`experiments/results.csv` の `dict`/`glossary`
キーワードgrepと `git log -- experiments/ src/` の直近20件を確認し、
辞書/RAGプロンプト技法の領域は既に `h-dictionary-term-compliance-leakage`
(サイクル74、tested)と `h-dictionary-demonstration-augmented-prompt`
(サイクル98、queued・未実行)でカバー済みであることを検証しました。

## GENERATE_HYPOTHESES: 新仮説は追加せず

今回読んだ3件は上記の既存仮説と同系統(辞書/RAGプロンプト)であり、
`h-dictionary-demonstration-augmented-prompt` を具体的に拡張・差別化する
だけの検証可能な新規性(例: dict-rephrasingの単独効果、埋め込み検索による
辞書エントリ取得)を、WebSearch要旨レベルの情報だけで確定するのは時期尚早
と判断しました。バックログは `queued` 4件 + `proposed` 1件
(`needs_human`、変更なし)の計5件で上限(~6件)内ですが、**そのすべてが
既に環境/人手ブロック状態**にあります(下記参照)。サイクル98のreflection
が明示的に指摘した「ブロックが解消しないうちは新規ライブAPI依存の仮説より
$0の遡及分析を優先すべき」という判断軸に沿い、埋めるためだけに弱い仮説を
追加するのは見送りました — 質を優先し、既存バックログが消化されるのを
待つ判断です。

## HUMAN_APPROVAL: 処理対象なし

新仮説を追加しなかったため、承認が必要な新規項目はありませんでした。
既存5件はすべて承認状態が確定済み(`queued`×4は `approved_by_human`×3 +
`auto_approved`×1、`proposed`×1は `needs_human` のまま — サイクル36で
判明した「自己承認はブロックされる」安全ゲートに従い、人手の合図待ちの
まま変更していません)。

## RUN_EXPERIMENTS: 4件すべて既知の環境ブロックで実行不可(新規ライブ実験なし)

環境チェック: `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY` すべて
設定済み、`ffmpeg` も利用可能、キャッシュ済みクリップ
(`experiments/refs/audio/wjZofJX0v4M.webm`)も存在していました。

Playbookの「縮小頻度チェック」規定(同一の `(host, check-type)` ペアが
5回以上連続で同一のブロック signature を示した場合、直近のライブ確認が
10サイクル以内であればキャッシュ結果を再利用可)に従い、今回はライブ
再確認をスキップしました:

- **Deepgram listen-websocket**: 直近のライブ確認はサイクル98(1サイクル前)。
  `HTTP 400 "Connection header did not include 'upgrade'"` のブロックは
  サイクル5以来一貫。
- **api.openai.com**: 直近のライブ確認はサイクル93(6サイクル前)。
  `CONNECT` レベル403拒否のブロックはサイクル72以来一貫。

両者とも10サイクルの閾値を下回っており、環境変化を示す新しい人的合図も
なかったため(今回のプロキシ通知内容はサイクル93/98時点と同一)、
キャッシュ結果の再利用が妥当と判断しました。

この結果、4件の `queued` 仮説すべてが実行不可でした:

| 仮説 | ブロック要因 |
|---|---|
| `h-judge-cross-model-agreement-check` | OpenAI CONNECT拒否(サイクル93時点のキャッシュ結果を再利用) |
| `h-review-short-acronym-keyterm-false-positive` | Deepgram WS アップグレード失敗(サイクル93時点のキャッシュ結果を再利用) |
| `h-continuous-rating-cps-calibration` | rm-2278本人の視聴・評価時間(20-30分)が必要、かつ元動画ファイル(`video/LLM2025...mp4`)が本コンテナに存在せず(確認済み) |
| `h-dictionary-demonstration-augmented-prompt` | Deepgram WS アップグレード失敗(サイクル98時点のキャッシュ結果を再利用、フルパイプライン実行が必要) |

いずれも架空の実験結果は作成せず、各仮説の `blocked_reason` にこのサイクルの
再確認(キャッシュ再利用)結果を追記しました。`log-cost 0.0` で記録済みです
(本日の実消費は $0.00、日次上限 $7.00 に対し余裕があります)。

ライブAPI実行ができなかったため `ANALYZE_RESULTS` は分析対象なしの
パススルーとし、本レポート作成(`WRITE_REPORT`)に進みました。

## rm-2278へのお願い(人手が必要な項目)

- **Deepgram WebSocketアップグレード問題**: サイクル5から変化なし。
  このサンドボックスのegress proxyが原因と推測されますが、人の環境設定
  確認が必要です。
- **OpenAI接続拒否**: サイクル72から変化なし。
- **`h-continuous-rating-cps-calibration`**: 20-30分程度の視聴・評価作業、
  または `video/LLM2025...mp4` ファイルの提供(もしくは既存クリップへの
  再ターゲット)をお願いします。
- **`h-judge-repeat-independent-batches-corrected-design`**(`proposed`,
  `needs_human`)の承認判断も `pending_approval.json` に記載済みのまま
  保留中です。
- **バックログが100%ブロック状態**: 現在の `queued`/`proposed` 5件すべてが
  環境または人手のブロックで止まっています。Deepgram WS / OpenAI接続の
  いずれかが解消すれば、すぐに実行可能な仮説が複数あります。

## 次のサイクルへ

`REFLECT` に進み、本サイクルの振り返りを記録します。
