# サイクル88 結果レポート(2026-11-09, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル87の
`REFLECT`が前回のセッションで完了済み)。今回も
`SEARCH_PAPERS → EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES →
HUMAN_APPROVAL → RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで
実行しました。

なお、今回はこのリポジトリの`research_agent/`が`main`ブランチではなく
`feat/rm2278/async-containers`ブランチにあることを確認し、そのブランチ
上で作業・コミットしました(PLAYBOOK.mdのStep 0にすでに記載されている
既知の事情です)。

## SEARCH_PAPERS: LLM判定者(judge)の信頼性に関する新着論文3件を発見

PLAYBOOKが明示する探索ギャップ(新しい安定性/フリッカー対策、cascade対
end-to-endの新しい比較、LLM-as-MTプロンプト手法、ベンダーASRレイテンシの
更新)を狙って3件のWebSearchクエリを実行しました。

1. 「同時音声翻訳・再翻訳・フリッカー安定化ポリシー」クエリは既読の
   論文(arivazhagan2019-erasure、chen2024-revision-controllable-decoding、
   Googleのライブ字幕安定性ブログ)のみで、新規登録なし。
2. 「LLM-as-judge機械翻訳評価の信頼性・新手法」クエリで、まだ本リポジトリ
   に未登録の論文を2件発見:
   - **M-MAD**(`mmad2412-multiagent-debate-mt-eval`、arXiv:2412.20127、
     ACL 2025): MQM評価基準を次元ごとに分割し、各次元で2体のLLM
     エージェントが賛成/反対で討論(debate)し、別の統合エージェントが
     最終スコアにまとめる手法。GPT-4o-miniのような比較的弱いモデルでも
     既存のLLM-as-judge手法を上回り、参照ベースの最先端自動指標と同等の
     性能に達すると報告しています。
   - **WAT2026特許クレーム翻訳judge論文**
     (`wat2026-rubric-judge-patent-claims`、ACL Anthology 2026.wat-1.6):
     特許クレーム翻訳という専門領域向けに、5つの評価軸・最大16項目の
     チェックリストでGPTモデルに判定させる手法が、COMETKiwi/MetricXより
     人間評価との相関が高いと報告。
3. 「cascade対end-to-end比較」クエリは2020-2023年の旧い研究のみで、
   既存の`papi2026-hearing-to-translate-cascade-vs-speechllm`(2026年の
   大規模比較)で既に上書きされている内容でした。
   このクエリから派生して3件目の新規論文も発見:
   - **arxiv2510-reasoning-models-translation-evaluators**
     (arXiv:2510.20780、NeurIPS 2025): 推論系LLM(Large Reasoning
     Models)をMT判定者として使う際の系統的な問題点(簡単な事例での
     過剰分析・スコア過大評価、スコア変換メカニズム自体の欠陥)を
     指摘し、合成的な人間らしい推論過程で再学習する`ThinMQM`という
     ファインチューニング手法で「思考予算」を約35倍削減しつつ評価性能
     を改善すると報告。

3件とも既存の`papers.json`のURLと重複していないことを確認したうえで
`found`として追加しました。

## EXTRACT_PAPERS → READ_PAPERS: 3件とも抽出・要約完了

`arxiv.org`/`aclanthology.org`への`WebFetch`は今回も`EGRESS_BLOCKED`
(DNS解決失敗)だったため、PLAYBOOKのフォールバック通りWebSearchで
具体的な主張・数値を抽出しました。

特に重要な検証として、`arxiv2510-reasoning-models-translation-evaluators`
の知見(推論系LLM特有の過大評価バイアス)が本リポジトリの実際のjudgeに
当てはまるかどうかを、`translation_fidelity_judge.py`を直接読んで確認
しました。本リポジトリのjudgeは`gemini-3.1-flash-lite`という軽量・
非推論系モデルを使用しており、この論文が指摘する「推論系LLM特有の」
問題は現状のjudgeには直接は当てはまらない可能性が高いことが分かりました
(将来judgeモデルを推論系に変更する判断をする際の背景情報として記録)。

`experiments/results.csv`・`experiments/*.json`・直近の`git log`を
grepして既存実験との重複もチェックし、3件とも本リポジトリの既存judge
関連の仮説(単一LLM呼び出しでサブスコアを返す`judge_decomposed()`系)
とは異なる、新しい角度であることを確認しました。特に**M-MAD**の
マルチエージェント討論方式は、既存の分解ルーブリック路線と明確に
異なる仕組みであり、次回`GENERATE_HYPOTHESES`で空きが出た際の最有力
候補です(ただし判定1回あたりのコストが2倍以上になる点は要注意)。

## GENERATE_HYPOTHESES: 新規仮説なし(バックログが上限)

バックログは`queued`3件+`proposed`3件の計6件でPLAYBOOKの上限(6件)に
到達したままでした(サイクル79以降と同じ状況)。`proposed`3件も全て
`approval`が`needs_human`で確定済みであり、未処理の`HUMAN_APPROVAL`
案件はなかったため、新規仮説は追加しませんでした。

## HUMAN_APPROVAL: 処理対象なし → RUN_EXPERIMENTS へ

6件すべてに`approval`が既に設定済みで、`approved_by_human`の`queued`
3件が残っていたため、PLAYBOOKのルールに従い`RUN_EXPERIMENTS`へ
進みました。

## RUN_EXPERIMENTS: 3件とも継続してブロック(新規実験なし)

環境チェック:

- `DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`/`OPENAI_API_KEY`: いずれも設定済み。
  `ffmpeg`も利用可能。キャッシュ済みクリップも存在。
- Deepgram REST: **200**(到達可能)。Gemini REST: **200**(到達可能)。
- OpenAI REST(`h-judge-cross-model-agreement-check`のブロッカー)、
  およびDeepgram listen-websocketハンドシェイク
  (`h-review-short-acronym-keyterm-false-positive`のブロッカー)は、
  どちらも直近の実機確認からまだ1〜2サイクルしか経っておらず、
  サイクル86で追加した「再チェック頻度低減」条項(5回以上連続で同一
  結果の場合、10サイクル以内なら前回確認結果を再利用してよい)に従い、
  今回は実機再チェックを省略し、前回までの確認結果(OpenAI:
  CONNECT拒否403、10回連続一致/Deepgram WS: HTTP 400 "Connection
  header did not include upgrade")をそのまま再利用しました。
- `h-continuous-rating-cps-calibration`は環境要因ではなく、サイクル87で
  整理された通り、(a) rm-2278さん自身の約20-30分の視聴・評価時間、
  (b) 元動画ファイルの未提供、の2点が未解消のままで、今回新たに着手
  できる作業はありませんでした(キー入力評価ツール本体も、人間不在の
  まま検証できないものを作るリスクを避け、今回も作成を見送りました)。

新規の実験・翻訳API呼び出しは一切行っていないため、コストログには
$0を記録しました。

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
(b) 元の講義動画ファイルをクラウドセッションに用意いただくか、代わりに
既存の短いクリップでプロトコルを調整する判断、のいずれかが必要です。

## 次のステップ

`REFLECT`に進み、今回の振り返り(judgeの信頼性に関する新着論文3件を
発見・精読できたこと、M-MADのマルチエージェント討論方式が次回の
有力な仮説候補であること、3件のブロック状況に変化がないこと)を
`reflections.md`に記録したうえで、次サイクルの方針を決定します。
