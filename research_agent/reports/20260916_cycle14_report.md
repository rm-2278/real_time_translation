# リサーチエージェント サイクル14 レポート (2026-09-16)

## 今回やったこと(概要)

前回サイクル13の`REFLECT`の判断どおり、サイクル14は`SEARCH_PAPERS`から
着手されていました(`SEARCH_PAPERS`は前回セッションで完了済み、3件の
論文が`found`状態でした)。今回のセッションは`EXTRACT_PAPERS`から再開し、
パイプライン全体を`REFLECT`直前まで一気に進めました。

1. **EXTRACT_PAPERS**: 見つかっていた3件の論文の要旨を抽出。
2. **READ_PAPERS**: 要約(2〜4文)と`key_findings`を執筆、既存の86件の
   実験ファイルと突き合わせ。
3. **GENERATE_HYPOTHESES**: 新規仮説`h-compression-actions-prompt-
   instruction`を1件追加。
4. **HUMAN_APPROVAL**: `check-budget 0.05` → `AUTO_APPROVE`で自動承認。
5. **RUN_EXPERIMENTS**: 環境確認 → `ffmpeg`のインストールに成功したが
   Deepgram WebSocketは引き続きブロック。ライブパイプライン実行の代わりに
   Gemini限定のリプレイ実験を実装・実行($0.01)。
6. **ANALYZE_RESULTS**: config差分チェック後、結果を分析。
7. 本レポート執筆(`WRITE_REPORT`)。

## 文献レビューの結果

今回はWebFetch(arxiv.orgやaclanthology.org等への直接アクセス)が
このサンドボックスのネットワークプロキシに**すべてブロックされました**
(`EGRESS_BLOCKED`)。これは過去のサイクルで報告されていたDeepgram/Gemini
のホスト単位のブロックとは別の、より広範なブロックです。WebSearch自体は
問題なく動作したため、WebSearchが返す要旨レベルの情報で代替し、抽出を
完了させました。

- **incremental2026**(`Redefining Machine Simultaneous Interpretation`,
  ACL 2026 Findings): READ/WRITEの二値ポリシーだけでは同時翻訳の
  品質・遅延トレードオフに限界があると主張し、decoder-only LLMベースの
  同時翻訳に4つの追加アクション(SENTENCE_CUT, DROP,
  PARTIAL_SUMMARIZATION, PRONOMINALIZATION)を導入。ACL60/60の英中/英独で
  COMET-KIWIと遅延の両方を改善。**この論文が今回の新規仮説の起点です。**
- **causal-alignment2026**(`Hikari`): READ/WRITEポリシーを排し、確率的な
  WAITトークンをデコード自体に組み込んだend-to-endモデル。英→日を含む
  3言語対で新SOTA。ただしモデルの再学習が前提のアーキテクチャであり、
  本リポジトリ(固定ASR + プロンプトのみのLLM翻訳)にはそのまま応用
  できません。
- **speechllm2026**(Samsung AI Center Cambridge): 既存のSpeechLLMの多くが
  「発話全体を待つ」か「固定間隔で出力する」かのどちらかで、真の
  ストリーミングになっていないと指摘。出力タイミングの決定自体を学習
  させる手法で1〜2秒の低遅延を達成。これも学習が前提で直接移植は
  できませんが、遅延の目標値として参考になります。

3件とも「学習ベースのアーキテクチャ」で、既存のランナーのフラグ変更
だけで再現できるものではありません。唯一、具体的にテスト可能だったのは
incremental2026のアクション空間を「プロンプト指示」として再解釈する
アイデアで、これが新規仮説になりました。

## 新規仮説: `h-compression-actions-prompt-instruction`(完了)

**内容**: `incremental2026`のSENTENCE_CUT/DROP/PARTIAL_SUMMARIZATION/
PRONOMINALIZATIONを、本リポジトリの`llm_translator.py`のシステム
プロンプトに追加指示として実装したらどうなるか、というテストです。

**重要な発見(実験設計中)**: 本リポジトリには既に非常に近い機能が
あることが分かりました。`Config.reading_speed_budget_translation`
(コミット`33faed4`、2026-09-15追加)は、発話の音声長から文字数バジェット
を計算し、「同時通訳者のように圧縮せよ」というソフトな指示をプロンプトに
注入する機能です。さらに直後のコミット`c4fd798`(同日)で、その機能に
「情報保持スコア(LLM審査員による0-100点)」ツールが追加され、
**圧縮には実際に忠実度コストがある**ことが判明済みでした(verbatim:85点、
budget=6.0:60点、budget=3.0:75点。特に技術用語「subword」を「音」
(sound)と誤訳する具体的な事例あり)。

この発見を受けて、当初の仮説の実装計画(ライブパイプラインでの
新規実験)を、この既存の知見と直接比較できる設計に修正しました。

**やったこと**:
1. `LLMTranslator`にオプトインの`extra_system_instructions`パラメータを
   追加(デフォルト`None`で本番挙動は変化なし、8つの既存ルールは無変更)。
2. `compression_actions_replay.py`を新規実装。ライブのDeepgram接続が
   今回もブロックされていた(下記)ため、`reading_speed_budget_translation`
   の検証に使われた実験(`20260915_budget_translation_baseline.json`)の
   既に記録済みのASR区間(23件)を、`LLMTranslator`に2条件で流し直す
   リプレイ方式を採用: (a) `extra_system_instructions=None`(この
   スクリプト自身のペア比較用ベースライン)、(b) 新しい圧縮指示付き。
   新規Deepgram呼び出しはゼロ、Gemini呼び出しのみ($0.01)。
3. `translation_fidelity_judge.py`(`c4fd798`で追加済み)を再利用し、
   両条件の情報保持スコアを測定。

**config差分チェック**: 2条件の`config`ブロックは`extra_system_
instructions`の値だけが異なり(モデル・辞書・コンテキスト窓・RPM制限は
完全一致)、交絡なし。

**結果**: **明確な勝ちではない、複雑な結果**でした(23区間・各条件1回の
生成・1回の審査員呼び出しのみなので、数値の細かい差は誤差の範囲として
過大評価しないよう注意)。

- 情報保持スコアは圧縮指示ありの方が高く出ました(ベースライン75点 →
  圧縮指示85点)。`reading_speed_budget_translation`で見つかった
  「圧縮すると忠実度が下がる」という傾向とは逆方向です。
- しかし出力の文字数削減はわずか2.4%(546→533文字)でした。
  `reading_speed_budget_translation`が達成した圧縮幅(CPS超過率が
  約71%→約9%まで改善)と比べるとはるかに小さく、今回のガードレールの
  強い指示は圧縮効果がほとんど発揮されていません。
- 23区間すべてを手作業で比較したところ、審査員のスコアには表れなかった
  **具体的な内容欠落を1件発見**しました。区間5「course. So I think you
  already know what's」の翻訳が、ベースラインでは完全な文
  (「コースですので、皆さんはすでに何が何であるかをご存知かと思います
  が、」)だったのに対し、圧縮指示ありでは文の途中で切れていました
  (「ので、皆さんはすでに何が」)。これは指示文中の明示的な「新しい
  情報のためにDROPを使うな」というガードレールに反する挙動です。
  審査員は文書全体をまとめて評価するため、この1区間の欠落を見逃した
  可能性があります。
- 両条件に共通する誤訳(「sound embeddings」/「音」)が1件ありました
  が、これは同じクリップで`c4fd798`が既に発見していたASR由来の
  既存の曖昧さであり、今回の圧縮指示が原因ではありません。

**結論**: `status`は`tested`としました(`abandoned`ではなく、判断に
使える結果が得られたため)。ただし**本番プロンプトへの採用は推奨しません
**。得られる圧縮効果が小さいのに対し、明示的な禁止指示があっても実際に
内容が欠落する事例が見つかったためです。これは
`reading_speed_budget_translation`の知見(「要点優先」という指示は
たとえ禁止文言を追加しても内容欠落のリスクを完全には防げない)と
矛盾しないどころか、補強する結果と言えます。サンプル数が小さいため
完全に否定はできませんが、追加投資は慎重にすべきという判断です。

## 環境状況

`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は設定済みで、REST APIは両方とも
到達可能(200)でした。**`ffmpeg`は今回`apt-get install`で問題なく
インストールできました**(過去のサイクルで報告されていたミラー404は
今回無関係なパッケージのみで、ffmpeg自体は正常にインストール完了)。

しかし**Deepgram Listen WebSocketは今回も同じ理由でブロックされたまま
です**(サイクル5以降と同じ症状:証明書検証ありだと鍵用途拡張エラー、
証明書検証を無効化した診断リクエストだとプロキシが「Connection header
did not include 'upgrade'」というHTTP 400を返す)。この問題は
サイクル5から今回(サイクル14)まで一貫して変化がなく、人間側の
プロキシ設定変更が必要です(後述)。

今回はこのブロッカーの影響を受けても、既存の記録済みASRデータを
再利用するリプレイ設計に切り替えることで、$0.01のみの新規APIコストで
実験を完了できました。

## バックログの状態(queued/proposedは0件、上限6件以内)

今回追加した`h-compression-actions-prompt-instruction`は同一サイクル内で
`tested`まで完了したため、現在queued/proposedの仮説はありません。

## 人間(rm-2278)へのお願い

1. **環境ブロッカー(継続、サイクル5から変化なし)**: `wss://
   api.deepgram.com/v1/listen`へのWebSocketアップグレードが、
   このサンドボックスのネットワークプロキシで正しく中継されていません。
   プロキシ設定でこのホストへのWebSocketアップグレード(Connection/
   Upgradeヘッダ)を許可いただけると、ライブのDeepgram実験が
   再開できます。
2. **新しい知見(判断不要、共有のみ)**: `reading_speed_budget_
   translation`と今回の`h-compression-actions-prompt-instruction`の
   両方で、「圧縮せよ」という指示(たとえ明示的な禁止文言を伴っても)
   がLLM翻訳の内容欠落リスクを伴うという一貫した傾向が見えてきました。
   本番導入を検討される際は、この忠実度コストを踏まえた判断を
   お勧めします。

## 次のサイクルでやること

- バックログが空(0件、上限6件)のため、`GENERATE_HYPOTHESES`で新規
  仮説を追加する余地は大きい。
- 圧縮指示の内容欠落リスク(区間5の事例)についての小規模な追加検証
  (同じ区間に対する複数回サンプリングなど)を、次の`GENERATE_
  HYPOTHESES`の候補として検討する価値がある。
- Deepgram WebSocketが復旧していれば、ライブパイプラインでの本格的な
  `h-compression-actions-prompt-instruction`の再検証(今回はリプレイの
  近似実験だった)を優先候補にする。
