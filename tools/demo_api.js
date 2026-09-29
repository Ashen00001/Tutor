// In-browser stand-in for the tutor server, used only by the preview build (tools/build_demo.py).
// Mirrors the real API's shapes; grading is a JS port of grade.py.
window.JPT_DEMO = (() => {
  const CUR = __CURRICULUM__;

  // ---------------------------------------------------------------- grading
  const STOP = new Set(["to", "a", "an", "the", "be", "is", "are", "am", "it", "its", "im", "i", "my", "of", "for", "you", "your"]);
  const ALIAS = { ok: "okay", k: "okay", yup: "yeah", yep: "yeah", ya: "yeah", nope: "nah", thx: "thanks", u: "you", rn: "now" };
  const stem = t => t.length > 5 && t.endsWith("ing") ? t.slice(0, -3) : /(ches|shes|xes)$/.test(t) ? t.slice(0, -2)
    : t.length > 3 && t.endsWith("s") && !t.endsWith("ss") ? t.slice(0, -1) : t.length > 4 && t.endsWith("ed") ? t.slice(0, -2) : t;
  const enTok = s => {
    const raw = (s.toLowerCase().replace(/[’']/g, "").match(/[a-z]+/g) || []).map(t => ALIAS[t] || t).join(" ").split(" ").filter(Boolean);
    const kept = raw.filter(t => !STOP.has(t));
    return (kept.length ? kept : raw).map(stem);
  };
  const ratio = (a, b) => {
    if (!a.length && !b.length) return 1;
    const dp = Array(b.length + 1).fill(0);
    for (let i = 1; i <= a.length; i++) {
      let prev = 0;
      for (let j = 1; j <= b.length; j++) { const tmp = dp[j]; dp[j] = a[i - 1] === b[j - 1] ? prev + 1 : Math.max(dp[j], dp[j - 1]); prev = tmp; }
    }
    return 2 * dp[b.length] / (a.length + b.length);
  };
  const split = s => s.split(/\s*(?:\/|,|;|\bor\b)\s*/).filter(p => p.trim()).map(enTok).filter(t => t.length);
  const matchTok = (u, c) => {
    if (!u.length) return "wrong";
    const U = new Set(u), C = new Set(c);
    if (U.size === C.size && [...U].every(x => C.has(x))) return "correct";
    if ([...C].every(x => U.has(x)) && [...U].filter(x => !C.has(x)).length <= 2) return "correct";
    const r = ratio(u.join(" "), c.join(" "));
    if (r >= .86) return "correct";
    if ([...U].every(x => C.has(x)) && U.size * 2 >= C.size) return "close";
    if (r >= (c.join(" ").length <= 5 ? .8 : .72)) return "close";
    return "wrong";
  };
  const checkEn = (text, meaning) => {
    const u = enTok(text), aux = [...meaning.matchAll(/\(([^)]*)\)/g)].map(m => m[1]).join(" ");
    let best = "wrong";
    for (const c of split(meaning.replace(/\([^)]*\)/g, " "))) { const v = matchTok(u, c); if (v === "correct") return v; if (v === "close") best = v; }
    if (best === "wrong" && split(aux).some(a => matchTok(u, a) === "correct")) best = "close";
    return best;
  };
  const hira = s => s.normalize("NFKC").replace(/[ァ-ヶ]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0x60));
  const jaNorm = s => hira(s).replace(/[\s、。！？!?.,…・「」『』（）()～〜ー\-]/g, "");
  const FIX = [["sy", "sh"], ["si", "shi"], ["zy", "j"], ["jy", "j"], ["zi", "ji"], ["di", "ji"], ["ty", "ch"], ["ti", "chi"], ["tu", "tsu"], ["hu", "fu"], ["cy", "ch"], ["ou", "o"], ["oo", "o"], ["uu", "u"], ["aa", "a"], ["ii", "i"], ["ee", "e"], ["ei", "e"]];
  const roNorm = s => { s = s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z]/g, "").replace(/m(?=[bp])/g, "n"); for (const [a, b] of FIX) s = s.split(a).join(b); return s; };
  const checkJa = (text, v) => {
    if (/[a-z]/i.test(text) && !/[぀-ヿ一-鿿]/.test(text)) {
      const u = roNorm(text), r = roNorm(v.romaji);
      if (!u) return "wrong";
      if (u === r || (r && u.includes(r) && u.length - r.length <= 3)) return "correct";
      return ratio(u, r) >= .65 ? "close" : "wrong";
    }
    const u = jaNorm(text); if (!u) return "wrong";
    const targets = [...new Set([jaNorm(v.word), jaNorm(v.kana)])].filter(Boolean);
    for (const t of targets) if (u === t || (u.includes(t) && u.length - t.length <= 3)) return "correct";
    return Math.max(0, ...targets.map(t => ratio(u, t))) >= .6 ? "close" : "wrong";
  };

  // ---------------------------------------------------------------- lines
  const pick = a => a[Math.floor(Math.random() * a.length)];
  const RIGHT = ["Yep!", "Boom.", "Nailed it.", "There it go.", "Clean.", "Easy.", "Yesss.", "Let's go.", "Perfect."];
  const CLOSE = ["Close enough, I'll count it.", "Basically, yeah.", "Pretty much!"];
  const WRONG = ["Ahh, not quite.", "Close, but nah.", "Not that one.", "Nope, tricky one."];
  const DUNNO = ["All good.", "No stress.", "That's what practice is for."];
  const HYPE = { 3: "Three in a row!", 5: "Five straight, you're cooking.", 8: "Eight in a row. Locked in.", 12: "Twelve straight. Actually cracked." };
  const jp = v => v.kana || v.word, first = m => m.split("/")[0].trim(), seg = (t, lang = "en") => ({ t, lang });
  const qSpeech = v => v.direction === "recognition" ? [seg(jp(v), "ja")] : [seg("How do you say"), seg(first(v.meaning) + "?")];
  const introSpeech = cs => [seg("New words."), ...cs.flatMap(v => [seg(jp(v), "ja"), seg(first(v.meaning) + ".")])];

  // ---------------------------------------------------------------- state
  const cards = new Map(); let nextId = 1790000000001, unlocked = 0;
  let queue = [], log = [], requeues = {}, started = Date.now(), answersTotal = 0;
  const unlock = () => {
    const b = CUR[unlocked]; if (!b) return null; unlocked++;
    return b.map(w => { const c = { ...w, card_id: nextId++, direction: "recognition", status: "new", lapses: 0, source: "" }; cards.set(c.card_id, c); return c; });
  };
  const enqueueNew = (cs, front) => {
    const items = [{ kind: "intro", ids: cs.map(c => c.card_id) }, ...cs.map(c => ({ kind: "quiz", id: c.card_id }))];
    queue = front ? [...items, ...queue] : [...queue, ...items];
  };
  const pop = () => {
    while (queue.length) {
      const it = queue.shift();
      if (it.kind === "intro") { const cs = it.ids.map(i => ({ ...cards.get(i) })); return { kind: "intro", cards: cs, speech: introSpeech(cs), first: pop() }; }
      const c = cards.get(it.id); if (!c) continue;
      return { kind: "quiz", card: { ...c }, left: queue.filter(q => q.kind === "quiz").length, speech: qSpeech(c) };
    }
    return { kind: "empty" };
  };
  const stats = () => ({ words: cards.size, not_started: [...cards.values()].filter(c => c.status === "new").length,
    due: [...cards.values()].filter(c => c.status === "learning" || c.status === "relearning").length, today: answersTotal, streak: answersTotal ? 1 : 0 });

  const routes = {
    "/config": () => ({ ask: false, deck: "Japanese Tutor", sync: false, demo: true }),
    "/start": () => {
      queue = []; log = []; requeues = {}; started = Date.now();
      let unlockedNow = null;
      const review = [...cards.values()].filter(c => c.status === "learning" || c.status === "relearning").map(c => ({ kind: "quiz", id: c.card_id }));
      queue.push(...review);
      if (!review.length) { const cs = unlock(); if (cs) { unlockedNow = unlocked; enqueueNew(cs, false); } }
      return { stats: stats(), unlocked: unlockedNow, item: pop() };
    },
    "/check": b => {
      const v = cards.get(b.card_id); if (!v) throw new Error("Unknown card");
      const texts = [b.text, ...(b.alts || [])].filter(t => t && t.trim());
      let verdict = "wrong";
      if (!b.gave_up && texts.length) {
        for (const t of texts) {
          const g = v.direction === "recognition" ? checkEn(t, v.meaning) : checkJa(t, v);
          if (g === "correct") { verdict = g; break; } if (g === "close") verdict = g;
        }
        if (verdict === "wrong" && v.direction === "recognition" && checkJa(texts[0], v) === "correct")
          return { verdict: "retry", result: null, line: "That's how you say it. Now what does it mean?", speech: [seg("That's how you say it. What does it mean?")], card: { ...v } };
      }
      const result = { correct: b.hinted ? "hard" : "good", close: "hard", wrong: "again" }[verdict];
      const line = verdict === "correct" ? pick(RIGHT) : verdict === "close" ? pick(CLOSE) : pick(b.gave_up ? DUNNO : WRONG);
      const speech = verdict === "correct" ? [seg(line)] : [seg(line), seg("It's"), seg(jp(v), "ja"), seg(first(v.meaning) + ".")];
      return { verdict, result, line, speech, card: { ...v } };
    },
    "/answer": b => {
      const c = cards.get(b.card_id); if (!c) throw new Error("Unknown card");
      log.push([c.card_id, b.result]); answersTotal++;
      let requeued = false, next = "1d";
      if (b.result === "again") {
        c.status = c.status === "review" ? "relearning" : "learning"; if (c.status === "relearning") c.lapses++; next = "~1m";
        if ((requeues[c.card_id] || 0) < 2) { requeues[c.card_id] = (requeues[c.card_id] || 0) + 1; queue.splice(Math.min(2, queue.length), 0, { kind: "quiz", id: c.card_id }); requeued = true; }
      } else { c.status = c.status === "new" ? "learning" : "review"; next = c.status === "learning" ? "~10m" : "1d"; }
      let inARow = 0; for (let i = log.length - 1; i >= 0 && log[i][1] !== "again"; i--) inARow++;
      return { saved: { word: c.word, result: b.result, next_review: next, requeued, in_a_row: inARow }, next: pop(), hype: b.result !== "again" ? HYPE[inARow] || null : null };
    },
    "/batch": () => { const cs = unlock(); if (!cs) return { added: [], batches: [], item: null }; enqueueNew(cs, true); return { added: cs.map(c => c.word), batches: [unlocked], item: pop() }; },
    "/progress": () => ({ ...stats(), learning: stats().due, in_review: 0, solid: 0, batches_unlocked: unlocked, batches_total: 20, mined: 0,
      shakiest: [...cards.values()].filter(c => log.some(([id, r]) => id === c.card_id && r === "again")).map(c => c.word).slice(0, 3), sync: "preview: sample words, nothing is saved" }),
    "/end": () => {
      const firstTry = new Map(); for (const [id, r] of log) if (!firstTry.has(id)) firstTry.set(id, r);
      const d = { cards: firstTry.size, answers: log.length, right_first_try: [...firstTry.values()].filter(r => r !== "again").length,
        minutes: Math.max(1, Math.round((Date.now() - started) / 60000)), missed: [...firstTry].filter(([, r]) => r === "again").map(([id]) => cards.get(id).word), sync: "sync off" };
      log = []; queue = []; return d;
    },
  };
  return async (path, body) => {
    await new Promise(r => setTimeout(r, 60));
    const fn = routes[path]; if (!fn) throw new Error("Not available in the preview");
    return JSON.parse(JSON.stringify(fn(body || {})));
  };
})();
