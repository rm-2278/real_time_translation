# リサーチエージェント サイクル16 レポート (2026-09-17)

## 今回やったこと(概要)

サイクル15の`REFLECT`の判断どおり、今回も`SEARCH_PAPERS`を飛ばして
`GENERATE_HYPOTHESES`から再開しました(サイクル15のレポート自身が
「重複していない、通常の長い/複雑な区間で同じ検証をする」という
具体的な次の一手をすでに特定していたため)。パイプライン全体を
`REFLECT`直前まで一気に進めました。

1. **GENERATE_HYPOTHESES**: 新規仮説`h-compression-nonduplicate-
   longsegment`を1件追加(サイクル15で「区間5の40%欠落は重複ASR
   区間という特殊ケース固有かもしれない」と分かったため、重複して
   いない長い区間で同じ検証をやり直すもの)。
2. **HUMAN_APPROVAL**: `check-budget 0.05` → `AUTO_APPROVE`で自動承認。
3. **RUN_EXPERIMENTS**: 環境確認(`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は
   設定済み、Gemini REST到達可能(200)。`ffmpeg`は今回も未インストール
   でしたが、この仮説はGemini限定のリプレイ実験なので無関係)。新規
   スクリプト`compression_repeated_sampling_longsegment.py`を実装・
   実行($0.01)。
4. **ANALYZE_RESULTS**: config差分チェック後、結果を分析。
5. 本レポート執筆(`WRITE_REPORT`)。

## 新規仮説: `h-compression-nonduplicate-longsegment`(完了)

**背景**: サイクル15の`h-compression-repeated-sampling-segment5`で、
区間5(「course. So I think you already know what's」)の内容欠落は
40%の確率で再現する実際の挙動だと確認しましたが、同時に「区間5の
原文はコンテキスト内の区間4と完全に重複しているため、このDROPは
指示文自身の『重複内容のみ省略してよい』というルールの正当な適用
かもしれず、一般的なガードレール違反とは言えない」という重要な
再解釈も出てきました。この再解釈は「重複していない通常の区間では
どうなるか」を検証しないと確かめられないため、今回それを実行しました。

**やったこと**:
1. 既存の86件の実験ファイルから、重複していない・長い・複数節から
   なる区間を探索。`experiments/20260903_asr_keyterms_off.json`の
   区間15(202文字、「which describes any model where you're using
   data to somehow to somehow determine how a model behaves. What I
   mean by that is let's say you want a function that takes in an
   image and it produces a label」)を選定しました。この区間の
   コンテキスト(区間12〜14)およびその次の区間16のいずれとも
   テキストが一致しないことをコード上でも実行時アサーションでも
   確認済みです。
2. `compression_repeated_sampling_longsegment.py`を新規実装(サイクル
   15の`compression_repeated_sampling.py`と同じ設計)。区間15の原文を、
   固定コンテキスト(区間12・13・14)のもとで2条件×各10回、計20回
   `LLMTranslator.translate_stream()`に流しました。各回`context_lines`
   を明示的に固定し`update_context=False`とすることで、複数回の呼び
   出しが互いに影響しないようにしています。
3. 評価は区間5のときと違い、文字数だけでなく「2つ目の節(関数/画像/
   ラベルという具体的な内容)が訳文に含まれているか」というキーワード
   ヒューリスティックを使用(区間15は2つの節の内容差が明確なため、
   文字数だけより直接的な信号になります)。
4. 忠実度審査員は各条件10回中3回のみ抜き取りで実行、コストを抑制。

**config差分チェック**: 2条件は同一スクリプト・同一コードパスで実行され、
`extra_system_instructions`以外(モデル、辞書、コンテキスト行、原文
テキスト)はすべて完全一致。交絡なし。

**結果**:

- **両条件とも内容欠落はゼロ件でした。** ベースライン条件は10回中10回、
  圧縮指示ありの条件も10回中10回、いずれも2つ目の節(関数/画像/ラベル)
  を含む完全な翻訳でした。
- 出力の長さは圧縮指示ありの方がわずかに短い(平均87.4文字 vs
  ベースライン平均93.0文字、約6%減)ものの、これは節を落とすDROPでは
  なく、SENTENCE_CUT/PARTIAL_SUMMARIZATIONのような穏やかな言い換えに
  よるものと見られます。
- 忠実度審査員による抜き取り検証(各条件3件、計6件)はすべて100点
  でした。これは区間15自体が完結した2節の発話であり(区間5のような
  文の断片ではない)、サイクル15で指摘した「審査員が断片を正しく
  評価できない」という設計上の限界がここでは当てはまらないためです。

**結論**: `status`は`tested`としました。今回の結果は、サイクル15の
再解釈を裏付けるものです。すなわち区間5で見られた40%の内容欠落は、
「長い/複雑な区間全般に対する圧縮指示の一般的なリスク」ではなく、
「重複したASR区間に対して指示文自身のDROPルールが(不安定にでは
あるが)適用される」という、より狭い、入力固有の現象である可能性が
高いということです。ただし今回検証したのは区間1件(各条件10回)
のみであり、他の区間で絶対に内容欠落が起きないことまで証明された
わけではない点は明記しておきます。`h-compression-actions-prompt-
instruction`の本番採用に対する推奨(現状では採用しない)自体は
変わりません — 6%の短縮効果は控えめであり、区間5のような重複ASR
区間が入力された場合の不安定な挙動(40%の確率)への対策(上流での
重複ASR区間検出など)なしに本番投入するのはまだリスクが高いためです。

## 環境状況

`DEEPGRAM_API_KEY`/`GOOGLE_API_KEY`は設定済み、Gemini REST APIは
到達可能(200)でした。`ffmpeg`は今回のセッションでも未インストール
でしたが、今回の仮説はライブのDeepgram/ffmpegを一切必要としない
Gemini限定のリプレイ設計だったため無関係でした(Deepgram WebSocketの
生死は今回も確認していません — この仮説には不要だったため、サイクル
5以降続いているプロキシのWebSocketアップグレード問題が解消したか
どうかは依然として不明です)。

## バックログの状態(queued/proposedは0件、上限6件以内)

今回追加した`h-compression-nonduplicate-longsegment`は同一サイクル内
で`tested`まで完了したため、現在queued/proposedの仮説はありません。

## 人間(rm-2278)へのお願い

1. **判断不要、共有のみ**: `h-compression-actions-prompt-instruction`
   系の一連の検証(サイクル14〜16)がまとまりました。結論は「本番
   プロンプトへの圧縮指示の追加は、現状のままでは推奨しない」で
   据え置きですが、その理由は当初の「ガードレール違反」という
   説明から、「重複ASR区間という特殊な入力に対する不安定なDROP
   適用」という、より正確で限定的な説明に置き換わっています。
2. **環境ブロッカー(継続)**: Deepgram Listen WebSocketの状況は
   サイクル5以降ずっとブロックされたままの可能性があります。今回も
   未確認のため、解消していれば`h-masking-holdback`等のライブ
   パイプライン実験を優先的に再開したいところです。

## 次のサイクルでやること

- `h-compression-*`系の狭いスレッドは一区切りついたため、次の
  `REFLECT`では新しいテーマの探索(`SEARCH_PAPERS`の再実行、または
  Deepgram WebSocketの再確認)に戻ることを検討します。
- バックログが空(0件、上限6件)のため、`GENERATE_HYPOTHESES`で新規
  仮説を追加する余地は大きい。
- Deepgram WebSocketが復旧していれば、ライブパイプラインでの本格的な
  検証(masking-holdback系)を最優先候補にする。
