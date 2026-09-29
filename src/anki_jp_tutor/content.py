"""Static content: note type, starter curriculum, and tutor rules."""

from __future__ import annotations

NOTETYPE_NAME = "JP Tutor Vocab"

# Order matters: the first field is Anki's sort/duplicate-check field.
FIELDS = [
    "Word",            # natural written form: 食べる, はい, マジ
    "Kana",            # reading, only when Word contains kanji: たべる
    "Romaji",          # taberu
    "Meaning",         # to eat
    "Type",            # verb (ru) / noun / slang ...
    "Note",            # casual-usage tip
    "Example",         # casual sentence in Anki furigana syntax:  水[みず] 飲[の]みたい。
    "ExampleEnglish",  # I want to drink water.
    "Source",          # where the line came from, e.g. "Frieren S1E3"
    "Production",      # non-empty => also make an English -> Japanese card
]

_WORD_BLOCK = (
    '{{#Kana}}<div class="big">{{Kana}}</div><div class="sub">{{Word}}</div>{{/Kana}}'
    '{{^Kana}}<div class="big">{{Word}}</div>{{/Kana}}'
)

_DETAILS = """
<div class="romaji">{{Romaji}}</div>
<div class="meaning">{{Meaning}}</div>
{{#Type}}<div class="type">{{Type}}</div>{{/Type}}
<div class="tts">{{tts ja_JP:Word}}</div>
{{#Note}}<div class="note">{{Note}}</div>{{/Note}}
{{#Example}}<div class="ex">{{furigana:Example}}</div><div class="ex-en">{{ExampleEnglish}}</div>{{/Example}}
{{#Source}}<div class="src">from {{Source}}</div>{{/Source}}
"""

TEMPLATES = [
    {
        "name": "Recognition",
        "front": f'<div class="card-jp">{_WORD_BLOCK}</div>',
        "back": '{{FrontSide}}<hr id="answer">' + _DETAILS,
    },
    {
        "name": "Production",
        # Wrapped in {{#Production}} so Anki only generates this card when the field is filled.
        "front": '{{#Production}}<div class="big-en">{{Meaning}}</div>'
        '<div class="hint">say it in Japanese</div>{{/Production}}',
        "back": '{{FrontSide}}<hr id="answer"><div class="card-jp">'
        + _WORD_BLOCK
        + "</div>"
        + _DETAILS,
    },
]

CSS = """
.card { font-family: "Hiragino Sans", "Yu Gothic UI", "Noto Sans JP", system-ui, sans-serif;
        text-align: center; font-size: 20px; color: #1d1d1f; background: #fafaf7; padding: 12px; }
.nightMode.card, .card.nightMode { color: #ececec; background: #1e1e1e; }
.big { font-size: 56px; line-height: 1.2; }
.sub { font-size: 22px; opacity: .6; margin-top: 4px; }
.big-en { font-size: 34px; }
.hint { font-size: 15px; opacity: .55; margin-top: 8px; }
.romaji { font-size: 22px; opacity: .75; }
.meaning { font-size: 28px; font-weight: 600; margin: 6px 0; }
.type { font-size: 14px; opacity: .5; }
.note { font-size: 17px; margin: 14px auto 0; max-width: 32em; opacity: .9; }
.ex { font-size: 24px; margin-top: 16px; }
.ex-en { font-size: 16px; opacity: .65; }
.src { font-size: 13px; opacity: .45; margin-top: 10px; }
ruby rt { font-size: .5em; opacity: .8; }
"""

TAG_ROOT = "jp-tutor"
TAG_MISSED = "jp-tutor::missed"
TAG_MINED = "jp-tutor::mined"


def batch_tag(n: int) -> str:
    return f"jp-tutor::batch-{n:02d}"


# Starter curriculum: casual, high-frequency, 3 words per batch.
# (word, kana, romaji, meaning, type, note, example, example_english)
_C = [
    # 1
    ("水", "みず", "mizu", "water", "noun", "",
     " 水[みず] 飲[の]みたい。", "I want to drink some water."),
    ("はい", "", "hai", "yes", "response",
     "Polite-ish yes. With friends it's usually うん (un).",
     "はい、どうぞ。", "Here you go."),
    ("食べる", "たべる", "taberu", "to eat", "verb (ru)",
     "Past: 食べた (tabeta). Negative: 食べない (tabenai).",
     " 何[なに] 食[た]べる？", "What are you gonna eat?"),
    # 2
    ("飲む", "のむ", "nomu", "to drink", "verb (u)",
     "Past: 飲んだ (nonda). Negative: 飲まない (nomanai).",
     " 何[なに] 飲[の]む？", "What do you want to drink?"),
    ("人", "ひと", "hito", "person / people", "noun",
     "As a suffix it reads じん: 日本人 (nihonjin) = Japanese person.",
     "あの 人[ひと]、 誰[だれ]？", "Who's that person?"),
    ("今", "いま", "ima", "now", "time word",
     "Not a verb, just a time word like 'now' or 'today'.",
     " 今[いま] 何[なに]してる？", "What are you doing right now?"),
    # 3
    ("行く", "いく", "iku", "to go", "verb (u)",
     "Past is irregular: 行った (itta). 'Let's go' = 行こう (ikou).",
     "コンビニ 行[い]く？", "You going to the convenience store?"),
    ("見る", "みる", "miru", "to see / to watch / to look", "verb (ru)",
     "見た = saw/watched. 見て！ = look!",
     "このアニメ 見[み]た？", "Did you watch this anime?"),
    ("する", "", "suru", "to do", "verb (irregular)",
     "Glues onto nouns to make verbs: 勉強する = to study.",
     " 何[なに]する？", "What do you wanna do?"),
    # 4
    ("うん", "", "un", "yeah / yes (casual)", "response",
     "The yes you'll actually hear with friends.",
     "うん、いいよ。", "Yeah, sure."),
    ("ううん", "", "uun", "nah / no (casual)", "response",
     "Casual no. いいえ is the textbook no people rarely say casually.",
     "ううん、 大丈夫[だいじょうぶ]。", "Nah, I'm good."),
    ("何", "なに", "nani", "what", "question word",
     "Becomes なん before だ/の and counters: 何だ (nanda), 何時 (nanji).",
     "え、 何[なに]？", "Huh, what?"),
    # 5
    ("俺", "おれ", "ore", "I / me (casual, guys)", "pronoun",
     "What guys use with friends. 僕 (boku) is softer; 私 (watashi) is neutral/polite.",
     " 俺[おれ]も 行[い]く。", "I'm going too."),
    ("友達", "ともだち", "tomodachi", "friend", "noun",
     "", " 友達[ともだち]と 遊[あそ]ぶ。", "Hanging out with friends."),
    ("うち", "", "uchi", "my place / home / house", "noun",
     "What people say for 'my place'. 家 (ie) is more 'the house' as a building.",
     "うち 来[く]る？", "Wanna come over to my place?"),
    # 6
    ("やばい", "", "yabai", "insane / crazy (good or bad)", "slang (adjective)",
     "Works for amazing AND terrible. Casual: やばっ！ / やべー.",
     "これ、やばくない？", "Isn't this insane?"),
    ("めっちゃ", "", "meccha", "super / really / very", "slang (adverb)",
     "Originally Kansai, now everywhere with young people.",
     "めっちゃ 美味[おい]しい！", "Super tasty!"),
    ("すごい", "", "sugoi", "amazing / incredible / awesome", "adjective (i)",
     "Guys often say すげー (sugee).",
     "すごいじゃん！", "That's awesome!"),
    # 7
    ("好き", "すき", "suki", "like / into (something)", "adjective (na)",
     "It's an adjective, not a verb: Xが好き = X is liked. 大好き = love it.",
     "このゲーム、 好[す]き？", "Do you like this game?"),
    ("大丈夫", "だいじょうぶ", "daijoubu", "okay / fine / no thanks", "adjective (na)",
     "Double duty: 'I'm fine' OR 'no thanks' when offered something.",
     " 大丈夫[だいじょうぶ]？", "You okay?"),
    ("いい", "", "ii", "good / fine / okay", "adjective (i)",
     "Irregular: past is よかった, negative is よくない.",
     "いいじゃん！", "Nice! / That's good!"),
    # 8
    ("本当", "ほんとう", "hontou", "really / true / for real", "noun / adverb",
     "In speech it's usually shortened to ほんと (honto).",
     "ほんとに？", "For real?"),
    ("マジ", "", "maji", "seriously / for real", "slang",
     "マジで？ = for real? マジか = no way.",
     "マジで？", "Seriously?"),
    ("分かる", "わかる", "wakaru", "to understand / get it", "verb (u)",
     "わかる～ = I feel that. Casual negative: わかんない (wakannai).",
     "わかる、それ！", "Right?! I get that!"),
    # 9
    ("今日", "きょう", "kyou", "today", "time word",
     "Special reading, just memorize it as きょう.",
     " 今日[きょう] 暇[ひま]？", "You free today?"),
    ("明日", "あした", "ashita", "tomorrow", "time word",
     "", "また 明日[あした]！", "See you tomorrow!"),
    ("後で", "あとで", "atode", "later", "adverb",
     "あとでね = later / catch you later.",
     " 後[あと]でね。", "Later."),
    # 10
    ("学校", "がっこう", "gakkou", "school", "noun",
     "", " 学校[がっこう] 行[い]きたくない。", "I don't wanna go to school."),
    ("先生", "せんせい", "sensei", "teacher", "noun",
     "Also used for doctors, lawyers, authors: anyone with expertise.",
     " 先生[せんせい] 来[き]た！", "Teacher's here!"),
    ("勉強", "べんきょう", "benkyou", "studying / study", "noun (+する)",
     "Add する to make it a verb: 勉強する = to study.",
     " 日本語[にほんご]を 勉強[べんきょう]してる。", "I'm studying Japanese."),
    # 11
    ("言う", "いう", "iu", "to say", "verb (u)",
     "Pronounced 'yuu' in speech. 言ってた = was saying.",
     " 何[なに] 言[い]ってんの？", "What are you even saying?"),
    ("来る", "くる", "kuru", "to come", "verb (irregular)",
     "Irregular: past 来た (kita), negative 来ない (konai).",
     " 来[く]る？", "You coming?"),
    ("帰る", "かえる", "kaeru", "to go home / return", "verb (u)",
     "Looks like a ru-verb but isn't: 帰った (kaetta), 帰らない (kaeranai).",
     "もう 帰[かえ]る。", "I'm heading home now."),
    # 12
    ("寝る", "ねる", "neru", "to sleep / go to bed", "verb (ru)",
     "おやすみ (oyasumi) = goodnight.",
     "もう 寝[ね]る。", "I'm going to bed."),
    ("起きる", "おきる", "okiru", "to wake up / be awake", "verb (ru)",
     "", " 起[お]きてる？", "You awake?"),
    ("疲れた", "つかれた", "tsukareta", "(I'm) tired", "verb (past of 疲れる)",
     "Past form, but it means 'I'm tired' right now.",
     "あー、 疲[つか]れた。", "Ugh, I'm beat."),
    # 13
    ("お腹すいた", "おなかすいた", "onaka suita", "I'm hungry", "phrase",
     "Guys often say 腹減った (hara hetta).",
     "お 腹[なか]すいた～", "I'm starving~"),
    ("ご飯", "ごはん", "gohan", "meal / rice / food", "noun",
     "Means both rice and a meal. 朝ご飯 = breakfast.",
     "ご 飯[はん] 行[い]こう。", "Let's go get food."),
    ("美味しい", "おいしい", "oishii", "tasty / delicious / good (food)", "adjective (i)",
     "Guys also say うまい (umai) / うめー (umee).",
     "これ 美味[おい]しい！", "This is good!"),
    # 14
    ("楽しい", "たのしい", "tanoshii", "fun", "adjective (i)",
     "Past: 楽しかった (tanoshikatta).",
     "めっちゃ 楽[たの]しかった！", "That was so fun!"),
    ("つまらない", "", "tsumaranai", "boring", "adjective (i)",
     "In speech it's usually つまんない (tsumannai).",
     "この 映画[えいが]、つまんない。", "This movie's boring."),
    ("面白い", "おもしろい", "omoshiroi", "funny / interesting", "adjective (i)",
     "Kansai-ish casual: おもろい (omoroi).",
     "それ、 面白[おもしろ]い！", "That's hilarious!"),
    # 15
    ("ゲーム", "", "geemu", "game / video game", "noun (katakana)",
     "You 'do' games: ゲームする.",
     "ゲームしよう！", "Let's play games!"),
    ("アニメ", "", "anime", "anime", "noun (katakana)",
     "", " 何[なん]のアニメ 見[み]てる？", "What anime are you watching?"),
    ("映画", "えいが", "eiga", "movie / film", "noun",
     "〜ない？ at the end = casual 'wanna…?' invite.",
     " 映画[えいが] 見[み]に 行[い]かない？", "Wanna go see a movie?"),
    # 16
    ("待って", "まって", "matte", "wait! / hold on", "verb (te-form of 待つ)",
     "Command-ish form of 待つ (matsu, to wait).",
     "ちょっと 待[ま]って！", "Hold on!"),
    ("ちょっと", "", "chotto", "a little / kinda / hold on", "adverb",
     "Also a soft no: それはちょっと… = eh, I'd rather not.",
     "それはちょっと…", "That's kinda… (no)."),
    ("早く", "はやく", "hayaku", "hurry up / quickly / fast", "adverb",
     "", " 早[はや]く！", "Hurry up!"),
    # 17
    ("知ってる", "しってる", "shitteru", "(I) know", "verb (知る)",
     "'I don't know' is 知らない (shiranai), NOT 知ってない.",
     "それ 知[し]ってる！", "I know that!"),
    ("欲しい", "ほしい", "hoshii", "want (a thing)", "adjective (i)",
     "For things. For actions use verb + たい: 食べたい = want to eat.",
     "これ 欲[ほ]しい！", "I want this!"),
    ("行こう", "いこう", "ikou", "let's go", "verb (volitional of 行く)",
     "The -ou ending means 'let's'.",
     "よし、 行[い]こう！", "Alright, let's go!"),
    # 18
    ("誰", "だれ", "dare", "who", "question word",
     "", "それ 誰[だれ]？", "Who's that?"),
    ("どこ", "", "doko", "where", "question word",
     "", " 今[いま]どこ？", "Where are you right now?"),
    ("いつ", "", "itsu", "when", "question word",
     "", "いつ 行[い]く？", "When are we going?"),
    # 19
    ("なんで", "", "nande", "why (casual)", "question word",
     "どうして (doushite) is the softer version.",
     "え、なんで？", "Wait, why?"),
    ("どう", "", "dou", "how", "question word",
     "", "どうだった？", "How was it?"),
    ("これ", "", "kore", "this (near me)", "pronoun",
     "これ (near me) / それ (near you) / あれ (over there).",
     "これ 何[なに]？", "What's this?"),
    # 20
    ("ありがとう", "", "arigatou", "thanks / thank you", "phrase",
     "Bro-ish slang: あざす / あざっす (azasu).",
     "マジありがとう！", "Seriously, thanks!"),
    ("ごめん", "", "gomen", "sorry (casual)", "phrase",
     "ごめんなさい is fuller; すみません is polite / for strangers.",
     "ごめん、 遅[おく]れた！", "Sorry, I'm late!"),
    ("じゃあね", "", "jaa ne", "see ya / see you / bye", "phrase",
     "またね (mata ne) = see you again.",
     "じゃあね、また 明日[あした]！", "See ya, tomorrow!"),
]

BATCH_SIZE = 3
CURRICULUM: list[list[dict[str, str]]] = []
for _i in range(0, len(_C), BATCH_SIZE):
    CURRICULUM.append(
        [
            dict(zip(
                ("word", "kana", "romaji", "meaning", "type", "note", "example", "example_en"),
                row,
            ))
            for row in _C[_i : _i + BATCH_SIZE]
        ]
    )


SERVER_INSTRUCTIONS = (
    "Casual-Japanese tutor backed by the learner's Anki deck. "
    "Call jp_start_session first: it returns the tutoring rules and the first card. "
    "After that, every quiz turn is exactly one jp_answer call."
)

TUTOR_RULES = """TUTOR RULES (follow exactly)
Goal: real conversational Japanese: casual/plain forms and what people actually say, not textbook keigo.

Pace + style
- Max 3 new words at a time. Never dump lists.
- ONE question per message, then wait.
- 1-3 short lines per message. No preamble, no recaps. Chill, hype-but-brief.
- Text chat: show the kana prompt (the learner is building hiragana reading speed); romaji only as a hint if stuck. Voice: just say the word.

Loop (one tool call per turn; that's what keeps it fast)
- INTRO item: teach those words in ONE short message (word, meaning, one casual tip each), then ask the FIRST QUIZ shown with it.
- After every answer: jp_answer(card_id, result). It saves to Anki and returns the next card. Ask that next card right away.
- Grading: wrong/blank = again · right but slow, hinted or partial = hard · right = good · instant = easy.
- Wrong: give the right answer + a one-line reason for the mix-up, have them say it back. The server re-queues missed cards later automatically.
- recognition = ask what the Japanese means. production = give the English, they say it in Japanese.
- Once they know a verb + a noun, sometimes build a 2-3 word casual sentence from them (e.g. 水飲む？) and ask what it means.

Other
- Line from an anime/manga/game/song they're into: break it down briefly, pick 1-3 useful words, save them with jp_add_words (example = that one line, source = the show). Only use the line they gave.
- Queue empty or they want more: jp_next_batch. Done: jp_end_session.
"""
