"""
test_solver.py - stress test for lobster_solver.py. Run this after ANY change
to the solver, before going live:   python test_solver.py
It generates thousands of challenges in Moltbook's documented scrambling style
(random caps, doubled letters, stray symbols, shattered words, filler words)
and checks every answer. Must print 100% with 0 confident-but-wrong.
"""
import random, re, sys
from lobster_solver import parse_challenge
ONES="zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS={20:"twenty",30:"thirty",40:"forty",50:"fifty",60:"sixty",70:"seventy",80:"eighty",90:"ninety"}
def words(n):
    if n<20: return ONES[n]
    t,o=divmod(n,10); return TENS[t*10]+("" if o==0 else " "+ONES[o])
UNIT=["newtons","meters","centimeters","meters per second","newtons of force"]
T={"+":["a lobster claw exerts {a} {u} and adds {b} {u}, what is the total force",
        "a lobster swims at {a} {u} and speeds up by {b}, what is the new speed",
        "a lobster has {a} {u} plus {b} {u}, what is it",
        "one claw is {a} {u} and the other claw is {b} {u}, what is the total force",
        "a lobster claw force is {a} {u} and it gains {b} {u}, how much combined force",
        "lobster claw pushes {a} {u} and antenna pushes {b} {u}, how much total force"],
   "-":["a lobster swims at {a} {u} and slows by {b}, what is the new speed",
        "a lobster claw has {a} {u} but loses {b} {u}, what remains",
        "a lobster has {a} {u} minus {b} {u}, what is it",
        "lobster force is {a} {u} and it reduces by {b}, what is the new force",
        "a lobster swims {a} {u} but slows down by {b} {u}, what is the new velocity"],
   "*":["a lobster claw exerts {a} {u} times {b}, what is the total force",
        "lobster claw force is {a} {u} * {b} claws, how much total force",
        "a lobster has {a} {u} multiplied by {b}, what is the product"],
   "/":["a lobster claw force of {a} {u} is divided by {b}, what is each part",
        "lobster has {a} {u} split between {b} claws, how much per claw",
        "a lobster force {a} {u} divided by {b} what is it"]}
NOISE=list("]^~{}<>|/-.[")
FILL=["um","uh","umm","like"]
def scramble(s, rng):
    out=[]
    NUMW=set(ONES)|set(TENS.values())
    words_=[]
    for w in s.split():
        if w in NUMW and len(w)>=6 and rng.random()<0.15:   # drop a letter ("tweny")
            i=rng.randrange(len(w)); d=w[:i]+w[i+1:]
            if re.sub(r"([a-z])\1+", r"\1", d) not in {re.sub(r"([a-z])\1+", r"\1", x) for x in NUMW}:
                w=d   # skip drops that make another real number ("eighty" -> "eight")
        words_.append(w)
    s=" ".join(words_)
    for w in s.split():
        if w=="*": out.append("*"); continue
        if rng.random()<0.15: out.append(rng.choice(FILL))
        if rng.random()<0.35 and len(w)>3:      # shatter
            k=rng.randint(1,len(w)-1); parts=[w[:k],w[k:]]
        else: parts=[w]
        nw=[]
        for p in parts:
            p="".join(c*2 if rng.random()<0.2 else c for c in p)          # doubled letters
            p="".join(c.upper() if rng.random()<0.5 else c for c in p)     # caps
            if rng.random()<0.3: i=rng.randint(0,len(p)); p=p[:i]+rng.choice(NOISE)+p[i:]
            nw.append(p)
        out.append(rng.choice([" ",""+rng.choice(NOISE)," "]).join(nw) if len(nw)>1 else nw[0])
        if rng.random()<0.15: out.append(rng.choice(NOISE))     # standalone noise
    return "A] "+" ".join(out)+"?"
def make(rng):
    op=rng.choice("+-*/")
    if op=="/":
        b=rng.randint(2,9); a=b*rng.randint(2,11) if rng.random()<.6 else rng.randint(10,99)
    elif op=="*": a=rng.randint(2,40); b=rng.randint(2,9)
    else: a=rng.randint(10,99); b=rng.randint(1,49)
    ans={"+":a+b,"-":a-b,"*":a*b,"/":a/b}[op]
    plain=rng.choice(T[op]).format(a=words(a),b=words(b),u=rng.choice(UNIT))
    if rng.random()<0.25:   # Moltbook sometimes restates the numbers at the end
        plain += f" {rng.choice('+-')} {words(a)} {rng.choice('+-')} {words(b)}"
    return scramble(plain,rng), f"{ans:.2f}", plain

PHRASES2 = {"+":["a lobster has {a} newtons and another lobster has {b} newtons, how much force together",
        "the sum of {a} and {b} newtons is what",
        "a lobster accelerates from {a} meters per second by {b}, what is the new velocity",
        "a lobster claw increases its force of {a} newtons by {b} newtons, what is the force now",
        "what is {a} plus {b}"],
   "-":["a lobster decelerates from {a} meters per second by {b}, what is the speed",
        "a lobster force of {a} newtons decreases by {b}, what is left",
        "a lobster had {a} newtons, then {b} newtons were subtracted, what is the force",
        "a lobster has {a} newtons and a weaker lobster has {b} fewer, what does the weaker one have",
        "what is {a} minus {b}"],
   "*":["what is the product of {a} and {b}", "{a} newtons multiplied by {b} claws gives what total"],
   "/":["a lobster force of {a} newtons is shared equally among {b} claws, how much each",
        "what is {a} divided by {b}", "{a} newtons split evenly across {b} legs, what is per leg"]}


def big(n):
    if n >= 100:
        h, r = divmod(n, 100)
        return words(h) + " hundred" + ("" if r == 0 else " " + words(r))
    return words(n)


def run(seed, N, unseen=False):
    rng = random.Random(seed)
    wrong = confident_wrong = 0
    examples = []
    for _ in range(N):
        if unseen:
            op = rng.choice("+-*/")
            if op == "/": b = rng.randint(2, 9); a = b * rng.randint(2, 40)
            elif op == "*": a = rng.randint(2, 60); b = rng.randint(2, 12)
            else: a = rng.randint(10, 499); b = rng.randint(1, 99)
            exp = f"{ {'+': a+b, '-': a-b, '*': a*b, '/': a/b}[op]:.2f}"
            ch = scramble(rng.choice(PHRASES2[op]).format(a=big(a), b=big(b)), rng)
        else:
            ch, exp, _ = make(rng)
        p = parse_challenge(ch)
        if p["answer"] != exp:
            wrong += 1
            confident_wrong += p["sure"]
            if p["sure"] and len(examples) < 5:
                examples.append((exp, p["answer"], ch))
    return wrong, confident_wrong, examples


if __name__ == "__main__":
    # Quick run (default): 8,000 challenges, under a minute.
    # Full run:  python test_solver.py --full   (40,000 challenges, a few minutes)
    import time
    full = "--full" in sys.argv
    seeds, per = (range(1, 6), 4000) if full else (range(1, 3), 2000)
    blocks = 2 * len(seeds)
    print(f"Testing {blocks * per:,} challenges in {blocks} batches "
          f"({'full' if full else 'quick'} run)...", flush=True)
    total = bad = cbad = 0
    t0 = time.time()
    n = 0
    for unseen in (False, True):
        for seed in seeds:
            w, c, ex = run(seed, per, unseen)
            total += per; bad += w; cbad += c
            n += 1
            print(f"  batch {n}/{blocks} done ({time.time() - t0:.0f}s) - confidently wrong so far: {cbad}",
                  flush=True)
            for e in ex:
                print("CONFIDENTLY WRONG: expected", e[0], "got", e[1], "|", e[2])
    handed = bad - cbad
    print(f"\n{total} challenges: parser alone right {1 - bad/total:.2%}; "
          f"{handed} unclear ones handed to the model (with its numbers checked against the text); "
          f"confident-but-wrong {cbad}")
    print("✅ SAFE TO GO LIVE (the parser was never confidently wrong)" if cbad == 0
          else "❌ DO NOT GO LIVE - the parser was confidently wrong; fix the cases above first")
