"""
lobster_solver.py  -  Moltbook verification challenge solver

Moltbook challenges always contain TWO numbers and ONE operation (+, -, *, /),
hidden in text with random caps, stray symbols and shattered words.

How this solves it:
  1. Deterministic parser  - rebuilds number words, keeps them in reading order,
     and detects the operation. A standalone symbol such as ' * ' or ' / ' IS
     the operator (this was the cause of the 32 * 2 failure), so it is kept.
  2. LLM extraction        - Ollama is asked only to READ the problem and return
     {"a": .., "op": .., "b": ..}. Python does the arithmetic, because a 3B
     model reads well but calculates badly.
  3. Candidates            - if both agree, one answer. If they disagree, both
     are returned, best first, so the caller can try the second on a miss.
"""
import json
import re

# ---------------------------------------------------------------------------
# Number words
# ---------------------------------------------------------------------------
ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALES = {"hundred": 100, "thousand": 1000}


def _collapse(w):
    """'thhreee' -> 'thre'. Applied to BOTH the challenge and the dictionary,
    so 'three' and 'fifteen' still match after repeated letters are squashed."""
    return re.sub(r"([a-z])\1+", r"\1", w)


NUM_WORDS = {_collapse(k): v for k, v in {**ONES, **TENS, **SCALES}.items()}


# Real words that contain number words and get shattered ("an ten na").
DECOYS = {_collapse(w) for w in [
    # longer lobster/physics words that hide a number word when shattered;
    # short ones like 'none' or 'done' were removed: "the n one" must stay 1
    "antenna", "antennas", "antennae", "attention", "tension", "extend",
    "content", "potent", "tender", "intend", "lobster", "lobsters",
    "weight", "height", "freight", "tentacle", "tentacles",
    "sixth", "eighth", "ninth", "tenth", "twentieth",
    # one letter from "eight"; "dominance fight" appears in many real challenges
    "fight", "fights", "night", "light", "right", "might", "sight", "tight",
]}

MAX_JOIN = 4  # max fragments one shattered word can be split into


OP_SYMBOLS = {"*", "×", "÷", "+"}  # trusted standalone operators; '/' and '-' are noise
FILLERS = {"um", "uh", "uhm", "like"}  # spoken filler (collapsed); never "er", it is a word fragment


def clean_tokens(text, with_breaks=False):
    """Tokens with symbols deleted. `breaks` = token indexes that come right
    after a standalone operator symbol, so 'sixty / four' is not merged to 64."""
    toks, breaks = [], set()
    for raw in text.lower().split():
        if raw in OP_SYMBOLS:
            breaks.add(len(toks))
            continue
        t = _collapse(re.sub(r"[^a-z0-9]", "", raw))  # DELETE symbols inside words
        if t and t not in FILLERS:
            toks.append(t)
    return (toks, breaks) if with_breaks else toks


def _edit1(a, b):
    """True if a and b differ by at most one insert, delete or substitute."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = diff = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            diff += 1
            if diff > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
        else:
            i += 1
            j += 1
    return diff + (len(b) - j) <= 1


def _lookup(word, fuzzy=False):
    if word in DECOYS:
        return "decoy"
    if word in NUM_WORDS:
        return NUM_WORDS[word]
    if fuzzy and len(word) >= 6:  # fuzzy match only long words, to avoid false hits
        for k, v in NUM_WORDS.items():
            if len(k) >= 6 and _edit1(word, k):
                return v
    return None


def _drop_restatement(out):
    """Remove numbers that only restate earlier ones at the end of a challenge.
    `out` items are [value, end, start]. Only acts when there are 3+ numbers."""
    vals = [x[0] for x in out]
    if len(vals) <= 2:
        return out
    # 1. the whole list is one group repeated: [23, 5, 23, 5] -> [23, 5]
    for k in range(2, len(vals) // 2 + 1):
        if len(vals) % k == 0 and vals == vals[:k] * (len(vals) // k):
            return out[:k]
    # 2. the tail copies an earlier run: [1, 29, 1, 29, 1] -> [1, 29, 1]
    for k in range(len(vals) // 2, 1, -1):
        tail = vals[-k:]
        head = vals[:-k]
        if any(head[i:i + k] == tail for i in range(len(head) - k + 1)):
            return out[:-k]
    # 3. the last number is an earlier "sixty" + "one" pair merged: [60, 1, 61] -> [60, 1]
    last = vals[-1]
    for i in range(len(vals) - 2):
        a, b = vals[i], vals[i + 1]
        if a >= 20 and a % 10 == 0 and 1 <= b <= 9 and a + b == last:
            return out[:-1]
    return out


def extract_numbers(tokens, breaks=(), consumed=None):
    """Numbers in reading order, rebuilding shattered words. If `consumed` is a
    set, the indexes of tokens read as numbers (or decoy words) are added to it."""
    vals = []  # (value, start, end)
    i = 0
    while i < len(tokens):
        if tokens[i].isdigit():
            if consumed is not None:
                consumed.add(i)
            vals.append((int(tokens[i]), i, i + 1))
            i += 1
            continue
        hit = None
        # exact matches first (longest join first); fuzzy spelling only on a single token
        for fuzzy in (False, True):
            for k in range(1 if fuzzy else min(MAX_JOIN, len(tokens) - i), 0, -1):
                if any(j in breaks for j in range(i + 1, i + k)):
                    continue  # never join fragments across an operator symbol
                v = _lookup(_collapse("".join(tokens[i:i + k])), fuzzy)
                if v is not None:
                    hit = (v, k)
                    break
            if hit:
                break
        if hit is None and i > 0 and len(tokens[i]) >= 4 and tokens[i - 1] \
                and NUM_WORDS.get(_collapse(tokens[i - 1]), 0) >= 20 \
                and tokens[i][0] == tokens[i - 1][-1]:
            # last letter of a tens word repeated onto the next: "thirty ytwo" = 32
            v = _lookup(_collapse(tokens[i][1:]))
            if v is not None and v != "decoy":
                hit, fuzzy = (v, 1), False
        if hit is None:
            i += 1
            continue
        v, k = hit
        if consumed is not None:
            consumed.update(range(i, i + k))
            if fuzzy and v != "decoy":
                consumed.add("fuzzy")   # a spelling-tolerant match was needed
        if v != "decoy":
            vals.append((v, i, i + k))
        i += k

    # Merge compounds only when the words are directly adjacent:
    # 'thirty two' -> 32, 'three hundred twenty five' -> 325,
    # but 'twenty meters ... five' stays [20, 5].
    out = []  # [value, end, start]
    for v, s, e in vals:
        if out and out[-1][1] == s and s not in breaks:
            prev = out[-1][0]
            if v in (100, 1000) and prev < v:
                out[-1] = [prev * v, e, out[-1][2]]
                continue
            if 1 <= v <= 9 and prev >= 20 and prev % 10 == 0 and prev % 100 != 0:
                out[-1] = [prev + v, e, out[-1][2]]
                continue
            if v < 100 and prev >= 100 and prev % 100 == 0:
                out[-1] = [prev + v, e, out[-1][2]]
                continue
        out.append([v, e, s])

    # Some challenges restate the numbers at the end ("... what's the total
    # force + twenty three - five??"), giving [23, 5, 23, 5]. If the whole list
    # is one group of 2+ numbers repeated, keep a single copy: [23, 5].
    # This runs before the "one" rule below, so "... by one ... + sixty one + one"
    # keeps its real "one".
    out = _drop_restatement(out)

    # Challenges have exactly two numbers. "One claw is ... the other claw is ..."
    # uses 'one' as a word, not a number: drop unmerged 1s until two remain.
    while len(out) > 2:
        ones = [x for x in out if x[0] == 1 and x[1] - x[2] == 1]
        if not ones:
            break
        out.remove(ones[0])
    return [x[0] for x in out]


# ---------------------------------------------------------------------------
# Operation detection, in priority order
# ---------------------------------------------------------------------------
# 1. Strong words: verbs that clearly name the operation.
STRONG_OPS = [
    ("*", ["times", "multipl", "product", "doubl", "tripl", "quadrupl"]),
    ("/", ["divid", "split", "shared", "quotient", "ratio"]),
    ("-", ["minus", "subtract", "slow", "loses", "lost", "reduc", "decreas", "decelerat",
           "drop", "fewer", "lesthan", "remain", "diferen", "weaken"]),
    ("+", ["plus", "adds", "added", "gain", "increas", "speedsup",
           "acelerat", "boost", "strengthen"]),
]
# 2. A standalone symbol between words:  'newtons * two'
#    Only symbols that have NOT been seen as noise. '/' and '-' appear as
#    scramble noise all the time (' / ', ' - '), so they are never trusted.
SYMBOL_OPS = {"*": "*", "×": "*", "÷": "/", "+": "+"}
# 3. Weak words: question phrasing that suggests a sum when nothing else does.
WEAK_ADD = ["total", "combin", "sum", "together", "altogether"]


def detect_op(raw_text, tokens):
    """Returns (op, how_found): 'word', 'symbol', 'weak' or 'default'.

    Checked against 112 real challenges:
    - A '*' ANYWHERE (standalone or stuck to a word, "dominance*") means multiply,
      even when a word says otherwise ("increases that force* by four" = 12 x 4).
    - Multiplication words win over other verbs: "accelerates by seven times" = x 7.
    - A count of claws ("there are two claws", "it has two claws") multiplies."""
    if "*" in raw_text or "×" in raw_text:
        return "*", "symbol"
    # Build the collapsed, spaceless text and remember where each word starts,
    # so keywords only count at a word start ("had seventy" must not match "adds").
    flat, starts = "", set()
    for t in tokens:
        if flat and t and flat[-1] == t[0]:
            starts.add(len(flat) - 1)   # 'is'+'split' collapses to 'isplit'
            flat += t[1:]
        else:
            starts.add(len(flat))
            flat += t
    hits = []
    for op, keys in STRONG_OPS:
        key = None
        for key in keys:
            k = _collapse(key)
            for m in re.finditer(re.escape(k), flat):
                if m.start() in starts:
                    hits.append((m.start(), op))
                    break
    if any(op == "*" for _, op in hits):
        return "*", "word"
    if hits:
        return sorted(hits)[0][1], "word"
    # "thirty newtons per claw and it has two claws": a number of claws multiplies
    for i in range(len(tokens) - 1):
        if tokens[i] in NUM_WORDS and _collapse(tokens[i + 1]) in ("claws", "clawss"):
            if NUM_WORDS[tokens[i]] in (2, 3, 4):
                return "*", "word"

    for tok in raw_text.split():
        if tok in SYMBOL_OPS:
            return SYMBOL_OPS[tok], "symbol"

    if any(_collapse(w) in flat for w in WEAK_ADD):
        return "+", "weak"
    return "+", "default"


def _num_words(n):
    """23 -> 'twentythree', 300 -> 'threehundred' (up to 999)."""
    n = int(n)
    inv1 = {v: k for k, v in ONES.items()}
    inv10 = {v: k for k, v in TENS.items() if k != "fourty"}
    if n < 20:
        return inv1.get(n, "")
    if n < 100:
        t, o = divmod(n, 10)
        return inv10[t * 10] + (inv1[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return inv1[h] + "hundred" + (_num_words(r) if r else "")
    return ""


def number_in_text(n, text):
    """True if the number n is written in the scrambled challenge, allowing for
    shattered words, doubled letters and one wrong/missing letter."""
    try:
        if float(n) != int(n) or not 0 <= int(n) < 1000:
            return False
    except (TypeError, ValueError):
        return False
    w = _collapse(_num_words(n))
    if not w:
        return False
    flat = _collapse("".join(clean_tokens(text)))
    if w in flat or str(int(n)) in flat:
        return True
    for size in (len(w) - 1, len(w), len(w) + 1):
        for i in range(0, max(0, len(flat) - size) + 1):
            if _edit1(flat[i:i + size], w):
                return True
    if len(w) >= 8:   # long words ("ninetyfour") may have lost two letters
        for size in (len(w) - 2, len(w) - 1, len(w), len(w) + 1):
            for i in range(0, max(0, len(flat) - size) + 1):
                if _edit_le2(flat[i:i + size], w):
                    return True
    return False


def compute(a, op, b):
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return None if b == 0 else a / b  # real division, not //
    return None


def fmt(x):
    return f"{x:.2f}"


# Ordinary words ending in "ty" that are not mangled numbers
TY_WORDS = {_collapse(w) for w in [
    "velocity", "gravity", "quantity", "density", "capacity", "ability", "activity",
    "city", "party", "empty", "pretty", "duty", "safety", "entity", "property",
    "intensity", "elasticity", "viscosity", "buoyancy", "mighty", "nasty", "salty",
    "dirty", "tasty", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety", "fourty", "plenty", "beauty", "loyalty", "royalty", "certainty",
]}


def _edit_le2(a, b):
    """True if Levenshtein distance between a and b is at most 2."""
    if abs(len(a) - len(b)) > 2:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > 2:
            return False
        prev = cur
    return prev[-1] <= 2


_COMMON_SHORT = {"even", "seen", "been", "tent", "event", "sent", "then", "than", "them",
                 "these", "those", "there", "their", "twin", "town", "teen", "into",
                 "fine", "fire", "force", "forc", "four", "foot", "fort", "from", "front",
                 "neon", "nine", "none", "sixth", "eight", "ninth", "tenth", "fifth", "fourth"}

_PREFIXES = {w[:n] for w in NUM_WORDS if len(w) >= 6 for n in (4, 5) if n <= len(w) - 2}


def _suspicious_fragment(tokens, consumed=frozenset()):
    """True if some run of 1-3 tokens is one letter away from a number word of 5+
    letters without being one (e.g. "tw en ny" ~ "twenty"). Then a number may have
    been misread, so the parser should not be confident."""
    if "fuzzy" in consumed:
        return True
    # R6: two single-digit number words side by side ("eight one" = eighty one?)
    for i in range(len(tokens) - 1):
        a, b = _collapse(tokens[i]), _collapse(tokens[i + 1])
        if (i in consumed and i + 1 in consumed and a in NUM_WORDS and b in NUM_WORDS
                and 1 <= NUM_WORDS[a] <= 9 and 1 <= NUM_WORDS[b] <= 9):
            return True
    words = [w for w in NUM_WORDS if len(w) >= 5]
    long_words = [w for w in NUM_WORDS if len(w) >= 7]
    for i, t in enumerate(tokens):
        if i in consumed:
            continue
        # R3: a common start of a long number word left unread: "thir", "twen", "eigh"
        if len(t) >= 4 and t in _PREFIXES:
            return True
        # R4: a short unread bit glued to a number we did read, which together are one
        #     letter from a DIFFERENT number word: "nine y" ~ ninety, "four en" ~ fourteen
        if len(t) <= 4:
            for nb in (i - 1, i + 1):
                if 0 <= nb < len(tokens) and nb in consumed and _collapse(tokens[nb]) in NUM_WORDS:
                    pair = tokens[nb] + t if nb < i else t + tokens[nb]
                    pair = _collapse(pair)
                    if pair not in NUM_WORDS and any(
                            _edit1(pair, w) and w != _collapse(tokens[nb])
                            for w in words if abs(len(w) - len(pair)) <= 1):
                        return True
                    # two letters lost from a long number word: "sen ten" ~ seventeen
                    if len(pair) >= 6 and pair not in NUM_WORDS and any(
                            _edit_le2(pair, w) and w != _collapse(tokens[nb]) for w in long_words):
                        return True
        # R9: right after "hundred", only a number makes sense: "two hundred even" ~ eleven
        if (i > 0 and (i - 1) in consumed and _collapse(tokens[i - 1]) in ("hundred", "hundre", "thousand")
                and len(t) >= 3 and t not in NUM_WORDS
                and any(_edit_le2(t, w) for w in NUM_WORDS if len(w) >= 3)):
            return True
        # R8: a short unread run two letters from a 6-8 letter number word with the
        #     same first letter: "elen" ~ eleven, "ften" ~ fiften
        for k in (1, 2):
            if i + k > len(tokens) or any(x in consumed for x in range(i, i + k)):
                break
            j = _collapse("".join(tokens[i:i + k]))
            if 4 <= len(j) <= 5 and j not in _COMMON_SHORT and j not in NUM_WORDS and any(
                    w[0] == j[0] and 6 <= len(w) <= 8 and _edit_le2(j, w) for w in NUM_WORDS):
                return True
        # R5: an unread 6-8 letter run two letters from a long number word: "senten"
        for k in (1, 2):
            if i + k > len(tokens) or any(x in consumed for x in range(i, i + k)):
                break
            j = _collapse("".join(tokens[i:i + k]))
            if 6 <= len(j) <= 9 and j not in DECOYS and j not in TY_WORDS and any(
                    _edit_le2(j, w) for w in long_words):
                return True
    for i in range(len(tokens)):
        # a fragment ending in "ty" that is not a number word: "senty", "nety", "fity"
        for k in (1, 2, 3):
            if i + k > len(tokens) or any(t in consumed for t in range(i, i + k)):
                break
            j = _collapse("".join(tokens[i:i + k]))
            if (j.endswith("ty") and 3 <= len(j) <= 8 and j not in NUM_WORDS
                    and j not in TY_WORDS and not any(j.endswith(t) for t in TY_WORDS if len(t) >= 6)):
                return True
        for k in (1, 2, 3):
            if i + k > len(tokens) or any(t in consumed for t in range(i, i + k)):
                break
            j = _collapse("".join(tokens[i:i + k]))
            if len(j) < 5 or j in NUM_WORDS or j in DECOYS:
                continue
            if any(_edit1(j, w) for w in words if abs(len(w) - len(j)) <= 1):
                # ignore if the run is just exact number words glued together
                if not all(_collapse(t) in NUM_WORDS for t in tokens[i:i + k]):
                    return True
    return False


def parse_challenge(text):
    toks, breaks = clean_tokens(text, with_breaks=True)
    consumed = set()
    nums = extract_numbers(toks, breaks, consumed)
    op, how = detect_op(text, toks)
    res = {"cleaned": " ".join(toks), "numbers": nums, "op": op, "how": how,
           "answer": None, "confident": False}
    if len(nums) >= 2:
        r = compute(nums[0], op, nums[1])
        if r is not None:
            res["answer"] = fmt(r)
            res["suspicious"] = (_suspicious_fragment(toks, consumed)
                                 or (len(nums) >= 2 and nums[0] == nums[1]))
            res["confident"] = (len(nums) == 2 and how in ("word", "symbol")
                                and not res["suspicious"])
    # "sure": safe to submit without the model. Also allows a plain "what is the
    # total" question with two numbers (all 11 real cases of that passed).
    res["sure"] = bool(res.get("answer") and len(nums) == 2 and how in ("word", "symbol", "weak")
                       and not res.get("suspicious"))
    return res


# ---------------------------------------------------------------------------
# LLM extraction (reading only, Python does the maths)
# ---------------------------------------------------------------------------
# The examples deliberately use numbers that do NOT appear in real challenges
# often, and none of them is 32/18, so the model cannot copy an example.
LLM_PROMPT = """You decode scrambled maths word problems about lobsters.
The scrambling adds random CaPiTaLs, stray symbols like ] ^ ~ < and splits words
into pieces. Every problem has exactly TWO numbers and ONE operation.

IMPORTANT:
- A standalone * between words IS multiplication.
- The symbols / - ^ ] [ {{ }} < > ~ | . are almost always noise. Ignore them.
- If the problem has a verb such as adds, slows, loses, times, divided, use it.
- If it only states two amounts and asks for the total or combined amount, add them.

Example 1
Scrambled: A] lOb-StEr SwImS aT^ tWeN tY sEvEn MeTeRs, Um sLoWs By] nI nE, wHaT iS tHe NeW sPeEd?
JSON: {{"plain": "A lobster swims at twenty seven meters and slows by nine. What is the new speed?", "a": 27, "op": "-", "b": 9}}

Example 2
Scrambled: LoBsTeR] cLaW pUsHeS fOuR tEeN~ nEwToNs * tHrEe < cLaWs, HoW mUcH tOtAl FoRcE?
JSON: {{"plain": "A lobster claw pushes fourteen newtons times three claws. How much total force?", "a": 14, "op": "*", "b": 3}}

Example 3
Scrambled: A lOoBsTeR hAs SiX tY^ nEwToNs AnD- gAiNs ElE vEn MoRe, WhAt Is ThE tOtAl?
JSON: {{"plain": "A lobster has sixty newtons and gains eleven more. What is the total?", "a": 60, "op": "+", "b": 11}}

Example 4
Scrambled: A] lO.bSsTtEr ClAaWw iIsS ^ fOrTtYy oNnEe NoOtToNs, uMm / aNd] tHe OtHeR iIsS {{ NiInNeE nEwToNs - wHaT] iS ^ tOoTtAaLl FoRcE?
JSON: {{"plain": "A lobster claw is forty one newtons and the other is nine newtons. What is the total force?", "a": 41, "op": "+", "b": 9}}

Now decode this one. Reply with ONE line of JSON only, in the same format.
Scrambled: {raw}
Hint (symbols removed, may have split words): {cleaned}
JSON:"""


def _parse_llm_json(raw):
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        a, b = float(d["a"]), float(d["b"])
        op = str(d["op"]).strip()
        op = {"x": "*", "×": "*", "÷": "/"}.get(op, op)
        if op not in "+-*/" or len(op) != 1:
            return None
        return a, op, b
    except (ValueError, KeyError, TypeError):
        return None


def llm_extract(text, cleaned, model, generate):
    """Returns (a, op, b) or None. `generate` is ollama.generate."""
    try:
        r = generate(model=model,
                     prompt=LLM_PROMPT.format(raw=text, cleaned=cleaned),
                     options={"num_predict": 120, "temperature": 0})
        out = r["response"].strip()
    except Exception as e:
        print(f"[Moltbook] ⚠️ LLM call failed: {e}")
        return None
    print(f"[Moltbook] 💬 Model reply: {out[:200]!r}")
    return _parse_llm_json(out)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def solve_candidates(challenge_text, model=None, generate=None, allow_model=True):
    """Returns a list of answers to try, best first (usually 1, at most 2).
    allow_model=False: answer only when the parser is sure; otherwise return []
    so the caller leaves the challenge unanswered (no wrong answer, no strike)."""
    p = parse_challenge(challenge_text)
    print(f"[Moltbook] 🔧 Cleaned: {p['cleaned'][:180]}")
    print(f"[Moltbook] 🔢 Parser: numbers={p['numbers']} op={p['op']} ({p['how']}) "
          f"answer={p['answer']} sure={p['sure']}")
    if not allow_model:
        if p["sure"]:
            print(f"[Moltbook] 💡 Submitting {p['answer']} (parser is sure)")
            return [p["answer"]]
        print("[Moltbook] ⏭️ Challenge is unclear; not answering it (a wrong answer "
              "would count against the account, an unanswered one does not).")
        return []

    llm_answer = None
    llm_ext = None
    if model and generate:
        ext = llm_ext = llm_extract(challenge_text, p["cleaned"], model, generate)
        if ext:
            a, op, b = ext
            # If the model invented a number not in the text, distrust it
            if p["numbers"] and not ({a, b} <= {float(n) for n in p["numbers"]}):
                print(f"[Moltbook] ⚠️ LLM numbers {a}, {b} not found in text by parser.")
            val = compute(a, op, b)
            if val is not None:
                llm_answer = fmt(val)
            print(f"[Moltbook] 🤖 LLM: {a} {op} {b} = {llm_answer}")

    # Moltbook accepts ONE answer per challenge, so pick the most trustworthy.
    # Tested on 80,000 generated + all real challenges, the parser was never wrong
    # when it found exactly two numbers and a clear signal (verb, '*', or a
    # "total"/"combined" question). The 3B model has invented numbers before, so
    # it decides only when the parser is unsure.
    pa = p["answer"]
    parser_sure = p["sure"]
    llm_nums_ok = llm_ext is not None and (
        len(p["numbers"]) < 2 or
        {llm_ext[0], llm_ext[2]} <= {float(n) for n in p["numbers"]} or
        (number_in_text(llm_ext[0], challenge_text) and number_in_text(llm_ext[2], challenge_text)))
    llm_numbers_differ = llm_ext is not None and len(p["numbers"]) >= 2 and \
        sorted([llm_ext[0], llm_ext[2]]) != sorted(float(n) for n in p["numbers"][:2])
    llm_numbers_real = llm_ext is not None and \
        number_in_text(llm_ext[0], challenge_text) and number_in_text(llm_ext[2], challenge_text)
    if parser_sure:
        order = [pa] + ([llm_answer] if llm_answer and llm_answer != pa else [])
        if llm_answer and llm_answer != pa:
            print("[Moltbook] ℹ️ Model disagrees; using the parser (more reliable in testing).")
    elif llm_answer and llm_nums_ok:
        order = [llm_answer] + ([pa] if pa and pa != llm_answer else [])
    else:
        order = [x for x in dict.fromkeys((pa, llm_answer)) if x]
    print(f"[Moltbook] 💡 Submitting {order[0] if order else None}"
          + (f" (other candidate {order[1]})" if len(order) > 1 else ""))
    return order


def solve_lobster_math(challenge_text, model=None, generate=None):
    c = solve_candidates(challenge_text, model, generate)
    return c[0] if c else None
