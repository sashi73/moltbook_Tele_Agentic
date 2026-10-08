import os
import re
import uuid
import ollama
import requests
from dotenv import load_dotenv
from memory import save_knowledge, log_moltbook_post

# ---------------------------------------------------------------------------
# Load environment variables from .env (must happen BEFORE reading them)
# ---------------------------------------------------------------------------
load_dotenv()

MODEL_NAME        = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
MOLTBOOK_BASE_URL = os.environ.get("MOLTBOOK_URL", "https://www.moltbook.com")
MOLTBOOK_API_KEY  = os.environ.get("MOLTBOOK_API_KEY")


# ---------------------------------------------------------------------------
# Config sanity check
# ---------------------------------------------------------------------------
def print_config():
    print("=" * 70)
    print("[CONFIG] Loaded from .env")
    print(f"  Model            : {MODEL_NAME}")
    print(f"  Moltbook URL     : {MOLTBOOK_BASE_URL}")
    if MOLTBOOK_API_KEY:
        masked = MOLTBOOK_API_KEY[:12] + "..." + MOLTBOOK_API_KEY[-4:]
        print(f"  Moltbook API key : {masked}")
    else:
        print("  Moltbook API key : ❌ NOT SET")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Moltbook connection check
# ---------------------------------------------------------------------------
def check_moltbook_connection():
    print("[Moltbook] Checking connection...")

    if not MOLTBOOK_API_KEY:
        print("  ❌ MOLTBOOK_API_KEY is not set.")
        print("     Check that .env exists in this folder and contains the key.")
        return False

    masked = MOLTBOOK_API_KEY[:12] + "..." + MOLTBOOK_API_KEY[-4:]
    print(f"  Key loaded    : {masked}")

    endpoints = [
        f"{MOLTBOOK_BASE_URL}/api/v1/agents/me",
        f"{MOLTBOOK_BASE_URL}/api/v1/me",
    ]

    headers = {
        "X-Moltbook-App-Key": MOLTBOOK_API_KEY,
        "Authorization": f"Bearer {MOLTBOOK_API_KEY}",
        "Content-Type": "application/json",
    }

    for url in endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=10)
        except requests.RequestException as e:
            print(f"  ⚠️  Network error hitting {url}: {e}")
            continue

        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                data = {}
            name = (
                data.get("agent", {}).get("name")
                or data.get("name")
                or "unknown"
            )
            print(f"  ✅ Connected to Moltbook as '{name}'  ({url})")
            return True

        if r.status_code in (401, 403):
            print(f"  ❌ Authentication failed at {url} ({r.status_code})")
            print("     The API key is set but was rejected.")
            return False

        print(f"  ⚠️  {url} returned {r.status_code}; trying next endpoint...")

    print("  ⚠️  Could not confirm the connection (no endpoint returned 200).")
    print("     The key is set; the agent will attempt to post anyway.")
    return True


# ---------------------------------------------------------------------------
# Core agent logic
# ---------------------------------------------------------------------------
def think_and_learn(subject):
    if not subject or not subject.strip():
        return "No subject provided."

    prompt = f"""
    You are an autonomous learning agent. Evaluate your knowledge on the subject: '{subject}'.
    Provide a comprehensive summary if you know it well.

    CRITICAL: If you lack deep facts, specify exactly what you don't know,
    and start your response with '[GAP]' on its own line, before any other text.
    """

    response = ollama.generate(
        model=MODEL_NAME,
        prompt=prompt,
        options={"num_predict": 768, "temperature": 0.3},
    )
    output = response["response"].strip()

    gap_match = re.match(r"^\s*\[GAP\]\s*", output, re.IGNORECASE)
    if gap_match:
        question = output[gap_match.end():].strip()
        post_to_moltbook(question)
        return (
            f"I encountered a knowledge gap regarding '{subject}'. "
            f"I have posted a query to Moltbook to learn more."
        )
    else:
        save_knowledge(subject, output)
        return f"Successfully processed '{subject}':\n\n{output}"


def post_to_moltbook(question):
    if not MOLTBOOK_API_KEY:
        print("[Moltbook] No API key set; skipping post.")
        return None

    url = f"{MOLTBOOK_BASE_URL}/api/v1/posts"
    headers = {
        "X-Moltbook-App-Key": MOLTBOOK_API_KEY,
        "Authorization": f"Bearer {MOLTBOOK_API_KEY}",
        "Content-Type": "application/json",
    }

    short = question[:50] + ("..." if len(question) > 50 else "")
    payload = {
        "title": f"Seeking definition/context on: {short}",
        "body": question,
        "submolt": "m/todayilearned",
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        post_id = data.get("id") or data.get("post_id") or str(uuid.uuid4())
        log_moltbook_post(question, str(post_id))
        print(f"[Moltbook] ✅ Query deployed (post_id={post_id})")
        return post_id
    except requests.HTTPError as e:
        print(f"[Moltbook] ❌ HTTP error: {e} — body: {r.text[:200]}")
    except requests.RequestException as e:
        print(f"[Moltbook] ❌ Network error: {e}")
    except Exception as e:
        print(f"[Moltbook] ❌ Unexpected error: {e}")
    return None


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
def test_model_with_prompt(user_prompt: str):
    print("=" * 70)
    print(f"[TEST] Model   : {MODEL_NAME}")
    print(f"[TEST] Prompt  : {user_prompt}")
    print("=" * 70)
    print("Waiting for model response...\n")

    try:
        response = ollama.generate(
            model=MODEL_NAME,
            prompt=user_prompt,
            options={"num_predict": 512, "temperature": 0.3},
        )
        output = response.get("response", "").strip()

        print("----- MODEL RESPONSE -----")
        print(output if output else "<empty response>")
        print("----- END RESPONSE -------\n")

        print("[DIAGNOSTICS]")
        print(f"  Response length : {len(output)} characters")
        print(f"  Contains [GAP]? : {'[GAP]' in output.upper()}")
        print(f"  Model field     : {response.get('model', 'n/a')}")
        print(f"  Done reason     : {response.get('done_reason', 'n/a')}")
        print(f"  Eval count      : {response.get('eval_count', 'n/a')} tokens")
        print(f"  Total duration  : {response.get('total_duration', 0) / 1e9:.2f} s")
        print("=" * 70)

        if not output:
            print("⚠️  Model returned nothing — is Ollama running and the model pulled?")
        else:
            print("✅ Model is responding — the pipeline is working.")
        return output
    except Exception as e:
        print(f"❌ Model call failed: {e}")
        return None


if __name__ == "__main__":
    print_config()
    print()
    check_moltbook_connection()
    print()
    test_model_with_prompt("Is Gandhi and Mandela similar?")