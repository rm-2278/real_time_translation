# サイクル75 結果レポート(2026-10-26, スケジュール実行)

## 今回やったこと

セッション起動時、パイプラインは`SEARCH_PAPERS`状態でした(サイクル74の
`REFLECT`が新サイクルとして遷移した直後)。今回は`SEARCH_PAPERS →
EXTRACT_PAPERS → READ_PAPERS → GENERATE_HYPOTHESES → HUMAN_APPROVAL →
RUN_EXPERIMENTS → ANALYZE_RESULTS → WRITE_REPORT`まで、1サイクル分を
通しで実行しました(PLAYBOOKが許す「余裕があれば次の状態まで続ける」
方針に従っています)。

## SEARCH_PAPERS / EXTRACT_PAPERS / READ_PAPERS: 新規文献3件

WebSearchで同時通訳・ストリーミングMT文献とLLM-as-judge信頼性文献、
ASRベンダーのレイテンシ情報を調査しました。`aclanthology.org`への
WebFetchは今回も`getaddrinfo ENOTFOUND`で遮断されていましたが(サイクル
14/73と同じパターン)、PLAYBOOKの指示通りWebSearchへフォールバックし、
十分な抄録レベルの情報を得られました。

新たに追加した3件:

1. **CUHKSZ Qwen3-Omni (IWSLT 2026)**:
   `cuhksz2026-iwslt-qwen3omni-wait-token`。英中・英独の同時音声翻訳を
   Qwen3-Omni-30B-A3Bで実装し、別立てのポリシーモジュールではなく、
   モデル自身に「待つ/出す」の判断(wait token)を学習させる方式。
   En→Zh 40.5 BLEU @1.95秒などの数値を報告。本リポジトリはAPI利用のみ
   でファインチューニング不可のため、この手法自体は移植できませんが、
   「ポリシーを外部ゲートではなく生成そのものに組み込む」という発想は
   示唆的でした。
2. **Pinch-AST (IWSLT 2026)**:
   `pinchast2026-iwslt-cascade-lcp-retranslation`。カスケード型システムで、
   文字単位の最長共通接頭辞(LCP)再翻訳をストリーミング方針に採用。
   本リポジトリ既存のprefix-lock/continuation-anchor系仮説群と同じ問題設定
   であり、新技術ではなく既存路線を裏付ける文献として記録しました。
3. **アラビア語→タイ語 医療翻訳のjudge信頼性研究**:
   `openwho-arabicthai-medical-judge-reliability-2026`。抄録レベルでしか
   内容を確認できず、具体的な知見(エージェント間一致率など)は未確認
   のまま。英日ペアとは異なる設定のため、本リポジトリのjudge信頼性系
   仮説(`h-judge-cross-model-agreement-check`など)への直接的な裏付けには
   まだ使えません。

`experiments/results.csv`・`experiments/*.json`・直近コミット履歴を
grepして既存実験との重複を確認し、重複なしを確認しました。

## GENERATE_HYPOTHESES: 新規仮説1件

`h-combined-translate-completeness-signal`(grounded_in: CUHKSZ論文)を
追加しました。本リポジトリには既に`h-semantic-completeness-gating`
(サイクル17頃)という、ASR中間結果が「意味的に完結しているか」を
**別立ての分類器呼び出し**で判定する仕組みがありますが、その
`result_summary`は「レイテンシが悪化した」と結論しており、原因は
分類器呼び出しが本番翻訳呼び出しと同じレート制限枠を奪い合っていた
ことだとpipeline.pyのコメントが明記しています。CUHKSZ論文のように、
この判定を**翻訳そのものと同じ1回の呼び出しに統合**できれば、この
レート制限競合そのものを構造的に解消できるのではないか、という仮説
です。承認コスト$0.10、`uses_existing_clips=true`。これで未処理
バックログは5/6(上限6の範囲内)。

## HUMAN_APPROVAL: 自動承認

`check-budget(0.10)`は`AUTO_APPROVE`。`approval: auto_approved` /
`status: queued`への書き込みは今回、環境の権限分類器から拒否されません
でした。

## RUN_EXPERIMENTS: 実装前に2つの誤った前提を発見・修正

実装に入る前に、仮説文に書いた前提をコード・データで確認したところ、
**2つとも誤り**であることが分かりました(PLAYBOOKが繰り返し指摘する
「経験的に確認せず抽象的に推論しない」教訓の、また別の実例です):

1. `h-semantic-completeness-gating`の実験JSON
   (`experiments/20260917_h_semantic_gating_test_v2.json`)には、
   分類器呼び出しの個別のTrue/False判定が**記録されていませんでした**
   (成長するASR中間テキストのスナップショットのみ)。
2. `translate_stream`(本番の翻訳呼び出し)には構造化出力スキーマが
   **存在しませんでした**(プレーンなストリーミングテキストのみ)。
   そのため「既存スキーマに1フィールド追加する」という計画は前提が
   崩れていました。

この2点を踏まえ、計画を修正: 過去の判定を再利用するのではなく、
サンプリングした31件のASR中間スナップショットそれぞれに対して、
(1)既存の分類器プロンプトを**そのまま複製した新規呼び出し**と、
(2)新たに考案した「翻訳+完結性タグ」を1回で返すアドホックな形式の
呼び出し、の2本を新規実行して判定一致率を比較する方式に変更しました
(本番コードは一切変更していません)。

`combined_completeness_signal_replay.py`を新規実装し実行(31サンプル
× 2呼び出し = 62件、gemini-3-flash-preview、$0.03、Deepgram呼び出し
なし)。

## ANALYZE_RESULTS: 判定一致率100%、ただし本番のレイテンシ効果は未検証

結果: **31件すべて(100%)で2つの判定が一致**しました。判定の内訳は
INCOMPLETE 27件・COMPLETE 4件と実際にばらつきがあり(全部同じ答えに
なる退化したケースではない)、両呼び出し形式ともパース失敗はゼロ
でした。

これは「1回の呼び出しに完結性判定を統合しても、別立て分類器と同じ
判定が得られる」という前提部分を支持する結果です。ただし、本仮説が
本来motivateしていた**本番でのレイテンシ改善効果**(別呼び出しを
削除することで、レート制限競合が実際に解消されるか)は、今回の
リプレイでは意図的に検証していません。それにはpipeline.pyの実際の
force-finalizeパスに統合したライブA/Bテストが必要で、今後の別サイクル
でのRUN_EXPERIMENTSの候補として記録しました(人間の承認が必要な
本番パイプライン変更になります)。仮説は`tested`としてクローズ。

サンプル数31件は1本のクリップのみからの抽出であり、一般化の主張は
していません。

## 予算

今サイクルの支出: $0.03(合計 $3.83)。日次上限$7.00に対し十分な余裕
があります。

## 次のサイクルへの引き継ぎ

- バックログ: queued 4件(`h-judge-cross-model-agreement-check`、
  `h-review-short-acronym-keyterm-false-positive`、
  `h-continuous-rating-cps-calibration`、今回tested済みの
  `h-combined-translate-completeness-signal`は除く)+ proposed/
  needs_human 1件(`h-judge-repeat-independent-batches-corrected-design`、
  rm-2278様の確認待ち、継続して保留中)。
- 次の良い候補: `h-combined-translate-completeness-signal`の
  ライブA/B版(pipeline.py統合、要承認)。
