# Anki Japanese Tutor

A casual-Japanese tutor that lives in Claude and keeps its memory in an Anki deck. It quizzes you 3 words at a time, goes after the words you keep missing first, saves every answer to Anki's scheduler, and turns lines from shows you're watching into flashcards.

It runs as a small server you host once. You add it to Claude as a **custom connector**, and then it works in Claude on your phone, your Surface, and the web.

```
Claude (phone / Surface / web)
   │  custom connector (HTTPS, secret URL)
   ▼
Tailscale Funnel ──► anki-jp-tutor (Docker on your VM)
                         │  own Anki collection: deck "Japanese Tutor"
                         ▼
                      AnkiWeb (optional) ◄──► Anki / AnkiDroid / AnkiMobile
```

## Setup (about 15 minutes)

**1. A small always-on Linux box.** An Oracle Cloud free-tier VM works: *Create instance → Ubuntu 24.04 → Ampere (ARM)*. Any Ubuntu machine with Docker will do.

**2. Tailscale (free).** This gives the server a public HTTPS address without opening any ports.
- Sign up at tailscale.com. In the admin console go to **DNS** and turn on **HTTPS certificates**.
- Go to **Settings → Keys → Generate auth key** and copy the key.

**3. Put this folder on the VM and run the setup script.**
```bash
unzip anki-jp-tutor.zip && cd anki-jp-tutor    # or: git clone <your repo> && cd anki-jp-tutor
bash setup.sh
```
It asks for the Tailscale key and, optionally, your AnkiWeb login. Then it builds and starts everything and prints your **connector URL**:
```
https://jp-tutor.<your-tailnet>.ts.net/mcp/<long-secret>
```
Treat that URL like a password: anyone who has it can use your deck.

**4. Add it to Claude.** On claude.ai go to **Customize → Connectors → + → Add custom connector**, name it "Japanese Tutor", paste the URL and click **Add**. Custom connectors added there also show up in the Claude desktop and mobile apps. (The Free plan allows one custom connector, so if another connector is already using that slot you'd need to swap it out.)

**5. Use it.** In a chat, turn the connector on with **+ → Connectors**, then say *"let's do Japanese"*. You can also pick the **japanese_tutor** prompt from the same menu.

## Keeping it fast

- **The server is already fast.** Each tool call takes about 5 ms, and each quiz turn is exactly one call (`jp_answer` saves your answer *and* returns the next card). Nearly all the wait you notice is the model writing its reply.
- **Pick the fastest model.** For tutor chats, choose **Haiku** in the model picker if your plan shows it. The quiz loop is simple, so a small model handles it well.
- **Make a Project.** Create a "Japanese" project with this instruction: *"Always start by calling jp_start_session and follow its rules."* Every chat in that project then goes straight into the quiz.

## Learning from shows (sentence mining)

Paste a line of dialogue you heard (or a screenshot of a manga panel) and say *"break this down"*. The tutor explains it briefly, picks 1–3 useful words, and saves them. The line becomes the card's example sentence, and the show goes in the **Source** field, so later you'll see "from Frieren S1E3" on the card. Keep it to the one line you're studying; the tutor only stores what you give it.

## Anki apps (optional)

If you entered an AnkiWeb login, the **Japanese Tutor** deck syncs to every Anki app you use: Anki on Windows (free), AnkiDroid (free), AnkiMobile (iPhone, paid), or ankiweb.net in a browser. Reviews count both ways. The tutor pulls from AnkiWeb at the start of each session and pushes about 20 seconds after you answer.

- **Before the first setup,** sync the Anki app on your phone and your computer so nothing is waiting to upload.
- **Card audio on Windows:** go to *Settings → Time & language → Language & region → add Japanese* and include speech. Cards read the word aloud with the system voice.
- **No AnkiWeb?** That's fine. The tutor keeps its own deck on the server ("sync off" mode).

## What the cards look like

- **Front:** the word in kana, big, since you're building hiragana reading speed. If the word has kanji, the kanji appear small underneath.
- **Back:** romaji, the meaning, one casual tip (e.g. "guys say 俺"), an example sentence with furigana, audio, and the source show.
- **English → Japanese cards** are off by default. Turn them on for every word with `JP_TUTOR_PRODUCTION_CARDS=true`, or the tutor can turn them on per word.

## Starter curriculum

The starter curriculum is 60 casual, high-frequency words in batches of 3. It starts with 水 / はい / 食べる, then 飲む / 人 / 今, and moves through casual yes/no, 俺, やばい / めっちゃ / マジ, school and daily-life words, and "let's go" and "see ya". When nothing is due, `jp_start_session` automatically unlocks the next batch.

## Tools

| Tool | What it does |
|---|---|
| `jp_start_session` | Pulls from AnkiWeb and builds the queue (weakest words, then due reviews, then new words). Returns the rules and the first card. |
| `jp_answer` | Saves the result (again / hard / good / easy) to Anki and returns the next card. A missed card comes back a couple of cards later. |
| `jp_next_batch` | Unlocks the next 3 starter words. |
| `jp_add_words` | Adds your own words or words from a show, skipping duplicates. |
| `jp_weak_words` | Lists your shakiest words and why they're shaky. |
| `jp_find_words` | Searches by Japanese, kana, romaji, English or source show. |
| `jp_progress` | Shows counts, what's due, your streak, batch progress and sync status. |
| `jp_end_session` | Gives a session summary and syncs. |

## Config (`.env`)

| Variable | Default | |
|---|---|---|
| `TS_AUTHKEY` | — | Tailscale auth key (only needed the first time) |
| `TS_HOSTNAME` | `jp-tutor` | The first part of your URL |
| `ANKIWEB_USERNAME` / `ANKIWEB_PASSWORD` | empty | Used once to log in; `setup.sh` blanks the password afterwards |
| `JP_TUTOR_DECK` | `Japanese Tutor` | Deck name |
| `JP_TUTOR_PRODUCTION_CARDS` | `false` | Also make English → Japanese cards |
| `JP_TUTOR_SYNC_ENDPOINT` | AnkiWeb | Use a self-hosted Anki sync server instead |
| `JP_TUTOR_FULL_SYNC_CONFLICT` | `download` | If a rare full-sync conflict happens, AnkiWeb wins |

## Security

- **Nothing is exposed except the tunnel.** No ports are open on the VM, and the only way in is through Tailscale Funnel.
- **The secret URL path is the login.** To get a new URL, run `docker compose exec tutor rm /data/secret.txt && docker compose restart tutor`, then run `bash setup.sh` again to print it.
- **Your AnkiWeb password is never saved.** Only the sync key is stored, in the `tutor-data` volume.

## Troubleshooting

- **`curl https://…/health` fails.** Funnel might not be allowed on your tailnet. In Tailscale go to *Access controls* and make sure the policy has `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`, and that HTTPS certificates are on. Check `docker compose logs tailscale`.
- **Sync errors.** Check `docker compose logs tutor`. To log in again, put the password back in `.env` and run `docker compose up -d`.
- **"full download" in the logs.** That's normal on first start: the server grabs your AnkiWeb collection.
- **Local testing without a VM.** Run `pip install . && anki-jp-tutor --stdio` (stdio MCP) or plain `anki-jp-tutor` (HTTP on :8000).

## Development

```bash
pip install .
python tests/e2e_test.py
```
The end-to-end test uses real Anki plus a local Anki sync server standing in for AnkiWeb. It drives every tool over MCP, checks that a second "phone" collection receives the words and reviews, tests restarts and fresh installs, and reports how long each call takes.
