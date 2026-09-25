# サイクル22 結果レポート (2026-09-25)

## サイクル番号について(訂正)

このセッション中に作成した一部のコミットメッセージ・orchestrator advanceのnoteで
誤って「cycle 23」と表記してしまいましたが、`REFLECT`はまだ実行していないため
PLAYBOOK.mdの命名規則(narrated cycle countは`REFLECT`遷移時にのみ増える)に従うと、
このセッションで行った作業はすべて**サイクル22**の続きです(直前のレポートは
`20260922_cycle21_report.md`、サイクル22は`SEARCH_PAPERS`から始まりこのレポートの
`WRITE_REPORT`まで続いています)。本レポートのファイル名は正しくcycle22としています。
コミットログの「cycle 23」表記との食い違いについては `reflections.md` にも記録します。

## 今回のセッションで引き継いだ状態

セッション開始時、パイプラインは `HUMAN_APPROVAL` 状態で停止していました。これは
以前のセッションが以下を完了した直後の状態です:

- **SEARCH_PAPERS / EXTRACT_PAPERS / READ_PAPERS**: LLM-as-judge のノイズ・信頼性に
  関する新規文献4本を追加・要約済み:
  - `arxiv2509-20293-judge-benchmark-design-failures`(判定ベンチマーク設計の欠陥が
    妥当性を損なう問題)
  - `arxiv2606-19544-systematic-large-scale-judge-eval`(高い自己一貫性が妥当性を
    保証しない、verbosityバイアスは小さい(<0.011)という自社実験結果)
  - `arxiv2607-02235-judge-multilingual-low-resource`(単一judgeモデルへの依存が
    多言語・低リソース言語設定で特に問題になりやすいという指摘。本リポジトリの
    En→Ja翻訳judgeもこれに該当)
  - `arxiv2601-05420-efficient-inference-noisy-judge`(ノイズを含むjudgeの平均推定を
    人手ラベルの小規模ゴールドセットで補正する2手法の比較。本リポジトリには
    人手ラベルのゴールドセットが存在しないため、直接は適用不可だが今後の参考)
- **GENERATE_HYPOTHESES**: 上記文献を根拠に新規仮説を2件追加(いずれも
  `status: proposed`、コスト$0〜$0.05の低リスクな遡及分析系):
  - `h-judge-verbosity-length-bias-retroactive`: judge()のスコアが翻訳文の長さ
    (verbosity)と相関していないかを、既存の実験JSON2本(条件内repeat比較・条件間
    比較の2種類)から新規API呼び出しなしで検証する。
  - `h-judge-cross-model-agreement-check`: 現在judgeはgemini-3.1-flash-liteのみで
    行っているが、gpt-4o-mini(OPENAI_API_KEY)を第二のjudgeとして既存の
    (原文,訳文)ペア20〜30件を再評価し、判定モデル間の一致度を確認する。

## 今回のセッションで実施したこと

### HUMAN_APPROVAL

上記2件の新規仮説について `orchestrator.py check-budget` を実行したところ、
どちらも `AUTO_APPROVE` の判定でした($0.00 と $0.05、いずれも
`uses_existing_clips=true`)。

しかし、このセッションの実行環境には自律エージェントが**自分自身の提案した仮説を
自動承認する**書き込み操作を "self-approval"(自己承認)としてブロックする
権限チェック機構があり、`hypotheses.json` に直接 `approval: auto_approved` /
`status: queued` を書き込む操作が拒否されました。

これはPLAYBOOKの想定する自動承認ポリシーとは別の、このセッション固有の安全策
です。回避策を探すのではなく、意図(無監督の自己承認を防ぐこと)を尊重して、
より安全な代替パスを選びました: 両仮説を `approval: needs_human` とし、
`research_agent/state/pending_approval.json` に理由付きで登録しました。
**rm-2278さんの承認をお願いします**(内容は上記2仮説、コストはごく小さく
$0.00/$0.05です)。

一方、以前のサイクル21ですでに `queued`/`auto_approved` になっていた
`h-soft-anchor-gate-min-words-3`(この自己承認ブロックの影響を受けない、
既存の承認済み仮説)が実行可能だったため、そちらで `RUN_EXPERIMENTS` に
進みました。

### RUN_EXPERIMENTS: h-soft-anchor-gate-min-words-3

**背景**: サイクル20の `h-soft-anchor-gate-recalibrated-noisefloor` で
`GATE_MIN_WORDS=2` を設定しましたが、分析の結果、ゲート条件が
`len(delta_text.split()) < GATE_MIN_WORDS`(厳密な `<`)であるため、
本来ゲートしたかった目玉ケース(guest-talk動画のspan 69, batch 1,
delta_text "AIM to" = 2語、"AIME" という幻覚訳の原因)が
`2 < 2` = False で全くゲートされていなかったことが判明していました。
今回はその修正として `GATE_MIN_WORDS=3` で再実行しました。

**環境チェック**: `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY` は設定済み、Gemini API
(`generativelanguage.googleapis.com`)への到達性もHTTP 200で確認できましたが、
`ffmpeg` はこの環境にインストールされていませんでした。ただし今回の実験は
既存の実験JSON(`experiments/20260915_h_continuation_anchor_test_rpmunlimited.json`、
`experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json`)に記録済みの
ASRデルタを再生する方式(Deepgram/ffmpeg不要、Gemini翻訳・judge呼び出しのみ)
なので、ffmpeg不在は今回のブロッカーにはなりませんでした(将来ライブASR実験が
必要になった際は要インストール)。

`.venv` が存在しなかったため、PLAYBOOK.mdの環境構築手順(Python 3.13で
venv作成 → `uv pip install -e ".[experiments]"`、`uv run`のzoom/rtms由来の
testpypiブロックを回避)に従って新規構築しました。

新規スクリプト `src/real_time_translation/experiments/soft_anchor_gate_min3_replay.py`
を作成(サイクル20の `soft_anchor_gate_recalibrated_replay.py` を
`GATE_MIN_WORDS=3` に変更した以外は同一構造)し、実行しました
(実費$0.40としてログ、日次予算$7.00のうち$0.40消費)。

### ANALYZE_RESULTS

- **設定差分の確認**: 新旧の実験JSONの `models`/`input` ブロックを比較し、
  `gate_min_words` 以外に差異がないことを確認(交絡なし)。
- **目玉ケースの修正確認**: guest-talk動画のspan 69, batch 1が今回は
  `span_69_batch1_gated: True` として実際にゲートされたことを、出力JSONで
  直接確認しました(`2 < 3` = True)。
- **ゲート発火率**: 元動画で25%(5/20)、guest-talk動画で34%(42/125)と、
  予測どおり閾値2(10%/16%)と閾値5(40%/57%)の中間に収まりました。
- **忠実度(fidelity)への影響**: gated_soft_anchor と soft_anchor_replay の
  平均スコア差は元動画で+2.0、guest-talk動画で+3.51でしたが、これらは
  同じ実験内で計測したノイズフロア(元動画stdev=4.32, n=40;
  guest-talk stdev=14.54, n=163)の範囲内に収まっており、**結論を出せる差では
  ありません**。REPEATS=2のjudge()呼び出しではこの規模の効果を検出できない、
  というサイクル20・21からの一貫した結論を裏付ける3件目のデータ点となりました。
- **予想外の結果(フリッカー/NE指標)**: 元動画ではgated_soft_anchorのNE平均
  (0.224)がsoft_anchor_replay(0.214)とほぼ同水準でしたが、guest-talk動画では
  gated_soft_anchor(0.377)がsoft_anchor_replay(0.089)よりも明確に悪化して
  いました(ただしどちらもゲートなしのbaseline(0.626)よりは大幅に良好)。
  ゲート導入によってフリッカーが減ると予想していましたが、guest-talk動画では
  逆にゲートありのほうがフリッカーが増えるという、当初予測と反対の結果です。
  この実験だけでは原因を特定できなかったため、結論を急がず次サイクルへの
  課題として明記しました(NE指標がゲートのprefix-lock/hold-back挙動自体を
  「erasure」として数えている可能性、あるいは42件のゲート対象バッチに
  効果が集中しているのか全体に分散しているのかの切り分けが必要)。

`h-soft-anchor-gate-min-words-3` は `status: tested` としました
(実装は健全で、ゲート修正自体は成功と確認できたため `abandoned` ではありません。
ただしNE指標の予想外の悪化は未解決の疑問として残っています)。

## 保留中の承認依頼(要対応)

`research_agent/state/pending_approval.json` に以下2件を登録しました。
いずれも `check-budget` は `AUTO_APPROVE` 判定でしたが、このセッションの
権限チェック機構が自己承認をブロックしたため、rm-2278さんの明示的な承認を
お願いします:

1. `h-judge-verbosity-length-bias-retroactive`($0.00、新規API呼び出しなし)
2. `h-judge-cross-model-agreement-check`($0.05、OPENAI_API_KEYが必要。
   この環境でのOPENAI_API_KEYの疎通は未確認です)

## 次サイクルへの申し送り

- 上記2件が承認されればすぐに実行可能です(特に1件目は完全無料の遡及分析)。
- guest-talk動画でのNE悪化という予想外の結果の原因調査を、次の仮説候補として
  提案します(ゲート対象バッチとそれ以外でNEを分解する分析など)。
- Deepgram Listen WebSocketの実疎通確認は依然サイクル19以降未実施です。
  ライブASR実験が必要になった際は改めて確認してください。
