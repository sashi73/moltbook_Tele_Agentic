import os
import re
import json
import time
import uuid
import ollama
import requests
from dotenv import load_dotenv
from memory import save_knowledge, log_moltbook_post, update_moltbook_status
from lobster_solver import solve_candidates

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

MODEL_NAME        = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
MOLTBOOK_BASE_URL = os.environ.get("MOLTBOOK_URL", "https://www.moltbook.com")
MOLTBOOK_API_KEY  = os.environ.get("MOLTBOOK_API_KEY")
MOLTBOOK_SUBMOLT  = os.environ.get("MOLTBOOK_SUBMOLT", "general")
API = f"{MOLTBOOK_BASE_URL}/api/v1"

# Safety stop: Moltbook suspends an account after 10 failed checks in a row.
# After FAIL_LIMIT failures in a row this agent stops posting by itself,
# far below Moltbook's limit. One success resets the count.
FAIL_LIMIT = int(os.environ.get("MOLTBOOK_FAIL_LIMIT", "3"))
FAIL_FILE = os.path.join(SCRIPT_DIR, "moltbook_failures.json")


def _fail_count():
    try:
        with open(FAIL_FILE) as f:
            return int(json.load(f).get("consecutive_failures", 0))
    except (OSError, ValueError):
        return 0


def _set_fail_count(n, last_challenge=None):
    try:
        with open(FAIL_FILE, "w") as f:
            json.dump({"consecutive_failures": n, "last_failed_challenge": last_challenge,
                       "updated": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
    except OSError as e:
        print(f"[Moltbook] ⚠️ Could not save failure count: {e}")


def reset_failures():
    """Call this (or delete moltbook_failures.json) after you have fixed the solver."""
    _set_fail_count(0)


def _headers():
    return {
        "Authorization": f"Bearer {MOLTBOOK_API_KEY}",
        "Content-Type": "application/json",
    }


def _ask(prompt, num_predict=768, temperature=0.3):
    r = ollama.generate(model=MODEL_NAME, prompt=prompt,
                        options={"num_predict": num_predict, "temperature": temperature})
    return r["response"].strip()


# ===========================================================================
# 1. ANSWER THE USER'S QUESTION  (returned to Telegram straight away)
# ===========================================================================

def answer_question(subject):
    subject = (subject or "").strip()
    if not subject:
        return "No question provided."

    print(f"[Agent] Querying Ollama '{MODEL_NAME}': {subject}")
    print("[Agent] (Can take 1-3 minutes on CPU. Please wait.)")
    t0 = time.time()
    answer = _ask(f"""Answer the following question as accurately and completely as you can.
Use short paragraphs. If the question uses an informal or ambiguous name for something
(for example a policy, law or event), state which one you take it to mean.
If facts are uncertain or debated, say so rather than guessing.

Question: {subject}

Answer:""")
    print(f"[Agent] ✅ Ollama responded in {time.time() - t0:.1f}s")

    try:
        save_knowledge(subject, answer)
    except Exception as e:
        print(f"[Memory] ⚠️ Could not save knowledge: {e}")
    return answer


# ===========================================================================
# 3. BUILD AN ANSWERABLE MOLTBOOK POST
# ===========================================================================

def build_moltbook_post(subject, agent_answer):
    """Ask the model for the pieces, then assemble the post in Python so the
    layout is always the same and other agents know exactly how to reply."""
    text = _ask(f"""A user asked: "{subject}"

A draft answer was written:
---
{agent_answer[:3000]}
---

Write a forum post that asks other experts to improve this answer.
Respond in EXACTLY this format and nothing else:

TITLE: <one specific question, under 100 characters, ending with ?>
CONTEXT: <2 or 3 sentences summarising what the draft answer says>
Q1: <a specific sub-question that is missing or uncertain in the draft>
Q2: <another specific sub-question>
Q3: <a question asking which claim in the draft is most likely wrong or oversimplified>
""", num_predict=400, temperature=0.4)

    def grab(key):
        m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    title = grab("TITLE") or f"{subject.rstrip('?.! ')}?"
    title = title.strip('"* ')
    if not title.endswith("?"):
        title = title.rstrip(".!") + "?"
    if len(title) > 120:
        title = title[:117].rstrip() + "...?"

    context = grab("CONTEXT") or agent_answer[:400].strip()
    questions = [q for q in (grab("Q1"), grab("Q2"), grab("Q3")) if q]
    if not questions:
        questions = [f"What are the most important facts about: {subject}?"]

    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    body = (
        f"**Original question:** {subject}\n\n"
        f"**What I have so far:**\n{context}\n\n"
        f"**What I'd like help with:**\n{numbered}\n\n"
        f"**How to answer:**\n"
        f"Reply in the comments. Start each point with its number (for example "
        f"\"2: ...\") so answers can be matched to questions. Corrections to the "
        f"summary above are just as welcome as new information, and please name a "
        f"source, date or figure where you can."
    )
    return title, body


# ===========================================================================
# 2. VERIFICATION (maths challenge)
# ===========================================================================

def _post_status(post_id):
    try:
        r = requests.get(f"{API}/posts/{post_id}", headers=_headers(), timeout=10)
        d = r.json()
        return (d.get("post") or d).get("verification_status")
    except Exception:
        return None


CHALLENGE_LOG = os.path.join(SCRIPT_DIR, "moltbook_challenges.jsonl")


def _record_challenge(challenge, candidates, answer, result, response=""):
    """Permanent history of every maths challenge (never erased). Each line is one
    JSON record; failed ones are the cases to add to SELF_TEST_CHALLENGES."""
    try:
        with open(CHALLENGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "result": result,              # "passed", "failed", "unsolved", "error"
                "submitted": answer,
                "candidates": candidates,
                "challenge": challenge,
                "response": response[:300],
            }, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[Moltbook] ⚠️ Could not write {CHALLENGE_LOG}: {e}")


def complete_moltbook_verification(verification, post_id=None):
    challenge = verification.get("challenge_text") or ""
    code = verification.get("verification_code") or ""
    if not challenge or not code:
        print(f"[Moltbook] ⚠️ Missing challenge fields. Keys: {list(verification)}")
        return False

    print(f"[Moltbook] 🧩 Challenge: {challenge}")
    candidates = solve_candidates(challenge, model=MODEL_NAME, generate=ollama.generate)
    if not candidates:
        print("[Moltbook] ❌ Could not solve the challenge.")
        _record_challenge(challenge, [], None, "unsolved")
        return False

    # Moltbook accepts ONE answer per challenge (a second try returns 409),
    # so only the best candidate is submitted.
    answer = candidates[0]
    print(f"[Moltbook] 📤 Submitting: {answer}")
    try:
        r = requests.post(f"{API}/verify", headers=_headers(), timeout=15,
                          json={"verification_code": code, "answer": answer})
    except requests.RequestException as e:
        print(f"[Moltbook] ❌ Network error: {e}")
        _record_challenge(challenge, candidates, answer, "error", str(e))
        return False
    try:
        body = r.json()
    except ValueError:
        body = {}

    if r.ok and body.get("success", True):
        print("[Moltbook] ✅ Verification accepted.")
        _record_challenge(challenge, candidates, answer, "passed")
        return True
    if r.status_code == 409:
        ok = post_id and _post_status(post_id) == "verified"
        print(f"[Moltbook] {'✅' if ok else '❌'} Code already used (409).")
        _record_challenge(challenge, candidates, answer, "passed" if ok else "error", r.text)
        return bool(ok)
    print(f"[Moltbook] ❌ Rejected ({r.status_code}): {r.text[:200]}")
    _record_challenge(challenge, candidates, answer, "failed", r.text)
    print(f"[Moltbook] 📝 Saved to moltbook_challenges.jsonl. Send the failed lines to Claude.")
    if len(candidates) > 1:
        print(f"[Moltbook] ℹ️ The other candidate was {candidates[1]}. If that was right, "
              f"add this challenge to SELF_TEST_CHALLENGES with that answer.")
    return False


# ===========================================================================
# POST TO MOLTBOOK
# ===========================================================================

def post_to_moltbook(subject, agent_answer):
    """Returns a dict the Telegram bot can report back:
    {ok, status, post_id, url, title, error}"""
    result = {"ok": False, "status": "not_posted", "post_id": None,
              "url": None, "title": None, "error": None}
    if not MOLTBOOK_API_KEY:
        result["error"] = "MOLTBOOK_API_KEY is not set."
        return result

    fails = _fail_count()
    if fails >= FAIL_LIMIT:
        result["error"] = (f"Posting paused for safety: the last {fails} maths checks failed "
                           f"(Moltbook suspends at 10). Add the failed challenge to the "
                           f"self-test, fix it, then delete moltbook_failures.json to resume.")
        print(f"[Moltbook] 🛑 {result['error']}")
        return result

    title, body = build_moltbook_post(subject, agent_answer)
    result["title"] = title
    print(f"[Moltbook] Title: {title}")

    payload = {"submolt_name": MOLTBOOK_SUBMOLT, "title": title, "content": body}
    try:
        r = requests.post(f"{API}/posts", json=payload, headers=_headers(), timeout=20)
    except requests.RequestException as e:
        result["error"] = f"Network error: {e}"
        return result

    if r.status_code == 429:
        try:
            d = r.json()
        except ValueError:
            d = {}
        wait = d.get("retry_after_minutes") or d.get("retry_after_seconds")
        unit = "minutes" if d.get("retry_after_minutes") else "seconds"
        result["error"] = (f"Moltbook only allows 1 post every 30 minutes "
                           f"(2 hours for new agents). Try again in {wait} {unit}."
                           if wait else "Moltbook rate limit reached. Try again later.")
        return result
    if r.status_code >= 400:
        result["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
        return result

    try:
        data = r.json()
    except ValueError:
        result["error"] = f"Non-JSON response: {r.text[:200]}"
        return result

    post = data.get("post") or {}
    post_id = post.get("id") or data.get("id") or str(uuid.uuid4())
    verification = post.get("verification") or data.get("verification")
    result["post_id"] = post_id
    result["url"] = f"{MOLTBOOK_BASE_URL}/post/{post_id}"

    if verification:
        print("[Moltbook] 🔐 Verification required.")
        verified = complete_moltbook_verification(verification, post_id)
        result["status"] = "verified" if verified else "pending"
        result["ok"] = verified
        if verified:
            _set_fail_count(0)
        else:
            n = _fail_count() + 1
            _set_fail_count(n, verification.get("challenge_text"))
            result["error"] = (f"The maths check failed, so the post is hidden. "
                               f"Failures in a row: {n}/{FAIL_LIMIT} before this bot pauses itself. "
                               f"The challenge is saved in moltbook_failures.json.")
    else:
        result["status"] = "verified"
        result["ok"] = True

    print(f"[Moltbook] {'✅ Live' if result['ok'] else '⚠️ Not live'}: {result['url']}")
    try:
        log_moltbook_post(title, str(post_id))
        update_moltbook_status(str(post_id), result["status"])
    except Exception as e:
        print(f"[Memory] ⚠️ Could not log post: {e}")
    return result


def get_replies(post_id, limit=10):
    """Comments other agents left on our post: list of (author, text)."""
    r = requests.get(f"{API}/posts/{post_id}/comments",
                     params={"sort": "new", "limit": limit},
                     headers=_headers(), timeout=15)
    r.raise_for_status()
    out = []

    def walk(items):
        for c in items or []:
            author = (c.get("author") or {}).get("name") or "unknown"
            out.append((author, (c.get("content") or "").strip()))
            walk(c.get("replies"))

    walk(r.json().get("comments"))
    return out


def think_and_learn(subject):
    """Old single-call entry point, kept for compatibility."""
    answer = answer_question(subject)
    post_to_moltbook(subject, answer)
    return answer


# ===========================================================================
# DIAGNOSTICS + SELF-TEST
# ===========================================================================

SELF_TEST_CHALLENGES = [
    # (challenge, correct answer). Add every real challenge you see here.
    # 9 Oct, rejected 1.39 (parser trusted a noise '/'); 55 is the correct reading
    ("A] lO.oBbSsTtEr LcLaAwW FfOoRrCeE iIsS ^ tHhIiRrTtYy TtWwOo NnOoOoTtOoNnSs, uMm / aNd] tThHeE oOtThHeErR ClLaAwW iIsS { tTtWwEeNnTtYy TtHhRrEe NnOoOoTtOoNnSs - wHhAaTt] iIsS ^ tToOtTaAlL FfOoRrCeE < uHh?", "55.00"),
    # 9 Oct, rejected 14.00; 64 (32 * 2) is the likely reading, not yet confirmed by Moltbook
    ("LoObStEr] ClAw- FoRcE^ Is ThIrTy TwO} NeOwToNs~ * TwO < DoMiNaNcE sHaKkS, LoO.bSsTtErS/ TuG aNtEnNaS, HoW MuCh ToTaL fOrCe?|", "64.00"),
    ("A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, wH-aTs] ThE/ nEw^ SpE[eD?", "15.00"),
    ("ThIs] LoO bSt-ErR^ Um| ClAw- FoRcE/ Is^ ThIrtY- TwO~ NeOwToNs{ PlUs] FiF tEeN^ NeOwToNs<, WhAt/ Is]", "47.00"),
    ("A] LoOoBbSsTtEeRr^ SwImS/ aT] tWeNtY ThReE- mEeTtEeRrS^ PeR SeCoNd, Um] BuT- sLoWwSs] DoWn^ bY/ sEeVvEeN- mEeTtEeRrS, WhAt] Is] ThE] NeW] VeLoCiTy", "16.00"),
    ("A] lO^bStErS sWiiMmInG vElAwCiTee In] tHe WaTeR hAvE cLaW FoRcE oF^ tHiRrTy tWo NeWtOnS - aNd- AdDs fOuRrTeEn NeWtOnS, wHaT Is ThE ToTaL FoRcE", "46.00"),
    ("A] Lo^b-StEr ClA w ExErTs ThIrTy TwO NooOtOnS Um, AnD ThE rEaLly Lo.oOoNg Er ClAw AdDs EiGhTeeN NootOns ~ WhAt Is ThE ToTaL FoR|cE?", "50.00"),
]


def run_self_test(use_llm=False):
    print("=" * 70)
    print("SELF-TEST" + (" (parser + LLM)" if use_llm else " (parser only)"))
    print("=" * 70)
    passed = 0
    for i, (challenge, expected) in enumerate(SELF_TEST_CHALLENGES, 1):
        print(f"\n--- Challenge {i} (expected {expected}) ---")
        cands = solve_candidates(challenge,
                                 model=MODEL_NAME if use_llm else None,
                                 generate=ollama.generate if use_llm else None)
        got = cands[0] if cands else None
        passed += got == expected
        print(("✅ PASS" if got == expected else "❌ FAIL") + f" got {got}")
    print("\n" + "=" * 70)
    print(f"Result: {passed}/{len(SELF_TEST_CHALLENGES)} correct")
    print("=" * 70)
    return passed == len(SELF_TEST_CHALLENGES)


def check_moltbook_connection():
    if not MOLTBOOK_API_KEY:
        print("[Moltbook] ❌ MOLTBOOK_API_KEY is not set.")
        return False
    try:
        r = requests.get(f"{API}/agents/me", headers=_headers(), timeout=10)
    except requests.RequestException as e:
        print(f"[Moltbook] ⚠️ {e}")
        return False
    if r.ok:
        name = (r.json().get("agent") or {}).get("name", "unknown")
        print(f"[Moltbook] ✅ Connected as '{name}'")
        return True
    print(f"[Moltbook] ❌ Auth check failed ({r.status_code})")
    return False


if __name__ == "__main__":
    mode = os.environ.get("MODE", "selftest").lower()
    if mode == "selftest":
        run_self_test()
    elif mode == "llmtest":
        run_self_test(use_llm=True)
    elif mode == "live":
        check_moltbook_connection()
        q = os.environ.get("QUESTION", "Is Gandhi and Mandela similar?")
        ans = answer_question(q)
        print("\n[ANSWER]\n" + ans)
        print("\n[MOLTBOOK]", post_to_moltbook(q, ans))
    else:
        print(f"Unknown MODE={mode!r}. Use selftest, llmtest or live.")
