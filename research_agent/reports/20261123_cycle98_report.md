# サイクル98 結果レポート (2026-11-23)

## 概要: 新規文献3件を追加し新仮説1件を生成、ただし環境ブロックにより実験は未実施(過去サイクルと同じ既知ブロックを再確認)

本サイクルはスケジュール実行(cron)によるもので、`current_state: SEARCH_PAPERS`
(前セッション=サイクル97の `REFLECT` が新サイクル開始として進めた状態)
から再開しました。本セッションでは
`SEARCH_PAPERS -> EXTRACT_PAPERS -> READ_PAPERS -> GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS -> ANALYZE_RESULTS -> WRITE_REPORT`
まで進めています。

## SEARCH_PAPERS / EXTRACT_PAPERS / READ_PAPERS: 新規文献3件を追加

Playbookが明示する4つの探索ギャップ(カスケード対エンドツーエンド比較、
新しい字幕安定化/フリッカー対策、新しいLLM-as-MT プロンプト技法、
ASRベンダーの最新レイテンシ数値)を対象にWebSearchを実施しました。
カスケード/フリッカー/ベンダーレイテンシ領域で見つかった候補
(NeMo IWSLT2026カスケード、AlignAtt4LLM、MLLP-VRAIN、CUNI pocket offline、
stable-prefix training、biased-draft-verificationフリッカー論文、
Hearing-to-Translateなど)は、URLベースで既存 `papers.json` と照合した結果
**すべて既に登録済み**でした(97件という既存文献ベースの網羅性の高さを
改めて裏付ける結果)。

新規に登録できたのは「LLM-as-MTプロンプト技法」領域の3件のみです:

- **`domainknowledge2025-retrieval-vs-generation-inference`**
  (Li, Luo, Briakou, Cherry; KnowledgeNLP 2025 / arXiv:2503.05010):
  推論時にLLMへ分野知識を与える方法として「用語集(terminology)」「実例
  (demonstrations)」「検索(retrieval)」「生成(generation)」を比較し、
  実例が用語集より優れ、検索が生成より優れることを報告。
  本リポジトリの `dictionary.csv` は純粋な用語集(用語→訳語の一覧)のみで、
  この論文の分類では最も弱いとされる手法に相当することを確認しました。
- **`grammamt2024-grammar-informed-icl`**(ACL 2025 / arXiv:2410.18702):
  Interlinear Glossed Text を使ったプロンプト技法。+17 BLEU の報告があるが、
  グロス(文法注釈)の質に依存する条件付きの効果であり、低資源言語向けの
  前提が本リポジトリのEN→JA(高資源)とは噛み合わないため、背景知識止まりと
  判断しました。
- **`manyshot2026-icl-retrieval-lowresource-mt`**(arXiv:2604.02596):
  低資源10言語でのmany-shot ICL実証研究。BM25検索による実例選定が
  ランダム選定より大幅に効率的という知見ですが、同様に低資源言語向けの
  前提のため背景知見止まり。

`arxiv.org` へのWebFetchは今回もENOTFOUND(前サイクルと同じ広範なブロック)
だったため、Playbook既定のWebSearch代替手法で要旨情報を取得しました。
`experiments/results.csv` と `git log -- experiments/ src/` を実際に
grep/確認し、「分野知識の注入方法そのもの(用語集 vs 実例 vs 生成)」を
比較した実験が本リポジトリに一件も存在しないことを検証済みです。

## GENERATE_HYPOTHESES: 新仮説1件を追加

`h-dictionary-demonstration-augmented-prompt` を追加しました。
`domainknowledge2025` の知見(実例+検索が用語集+生成より優れる)を、
本リポジトリ自身が既に確認済みの不具合——
`h-dictionary-term-compliance-leakage`(サイクル74、tested)で見つかった
"attention"→「アテンション」の英語残留(leak、133件中20件)——に対して
直接検証する設計です。LLM生成の例文ではなく、`experiments/refs/` に
既にある人間翻訳の正解データから実例文を1件「検索」して
`extra_system_instructions`(既存のフック、コード変更不要)経由で
プロンプトに追加し、同じクリップ・同じ辞書で再実行してleak率の変化を
比較します。バックログは `queued` 4件 + `proposed` 1件(`needs_human`、
変更なし)の計5件で、上限(~6件)内です。

## HUMAN_APPROVAL: 新仮説を自動承認

新仮説は `check-budget 0.15` → `AUTO_APPROVE` かつ `uses_existing_clips: true`
のため、通常の自動承認パスで `approval: auto_approved` / `status: queued`
に設定しました(サイクル36で判明した「`pending_approval.json` 経由の
自己承認はブロックされる」件とは別の、新規仮説に対する通常の承認パスです)。

## RUN_EXPERIMENTS: 4件すべて既知の環境ブロックで実行不可(新規ライブ実験なし)

環境チェック: `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY` すべて設定済み、
`ffmpeg` も利用可能でした。本セッションのプロキシ設定に
「api.deepgram.com を許可・認証情報を注入」という通知があり、これは
サイクル93で実際のライブ再チェックを促した合図と同種のものだったため、
キャッシュされた結果の再利用ではなく、改めてライブで再確認しました:

- **Deepgram listen-websocket**: 依然として `HTTP 400 "Connection header
  did not include 'upgrade'"` で失敗(サイクル5以来一貫した同一の signature)。
  REST (`/v1/projects`) は200で到達可能ですが、実際の音声認識が使う
  WebSocketアップグレードのみがプロキシ側で破壊されています。
- **api.openai.com**: 依然として `CONNECT` レベルで403拒否
  (前回のライブ確認はサイクル86で、12サイクル経過しPlaybookの
  「10サイクルで再チェック」規定に抵触したため、こちらもライブで再確認)。

この結果、4件の `queued` 仮説すべてが実行不可でした:

| 仮説 | ブロック要因 |
|---|---|
| `h-judge-cross-model-agreement-check` | OpenAI CONNECT拒否 |
| `h-review-short-acronym-keyterm-false-positive` | Deepgram WS アップグレード失敗 |
| `h-continuous-rating-cps-calibration` | rm-2278本人の視聴・評価時間(20-30分)が必要、かつ元動画ファイル(`video/LLM2025...mp4`)が本コンテナに存在せず |
| `h-dictionary-demonstration-augmented-prompt`(新規) | Deepgram WS アップグレード失敗(フルパイプライン実行が必要) |

いずれも架空の実験結果は作成せず、各仮説の `blocked_reason`/`result_summary`
にこのサイクルの再確認結果を追記しました。`log-cost 0.0` で記録済みです
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

## 次のサイクルへ

`REFLECT` に進み、本サイクルの振り返りを記録します。
