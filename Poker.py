#!/usr/bin/env python3
# Hold'em Equity Calculator — Chat/Console Version
# Adapted from your HTML/JS app (supports: pX/Y, hole cards, flop/turn/river, random opponents)
#
# Examples (type lines when running the script):
#   p3/8
#   1AhKd
#   2QsJc
#   39h9s
#   flop Ah6c9d
#   turn Td
#   river Jh
#
# Or in one go via code: run_scenario("p3/8; 1AhKd; 2QsJc; 39h9s; flop Ah6c9d; turn Td; river Jh", sims=20000)
#
from itertools import combinations
import random, math, sys, re
from collections import Counter

RANKS = "23456789TJQKA"
SUITS = "cdhs"
RANK_VAL = {r:i+2 for i,r in enumerate(RANKS)}
CAT_NAME = {
    8:"Straight Flush",7:"Four of a Kind",6:"Full House",5:"Flush",
    4:"Straight",3:"Three of a Kind",2:"Two Pair",1:"Pair",0:"High Card"
}

def trunc2pct(x: float) -> str:
    return f"{math.floor(x*100)/100.0:.2f}"

def is_card(c: str) -> bool:
    return isinstance(c,str) and len(c)==2 and c[0] in RANKS and c[1] in SUITS

def norm_card(token: str):
    if token is None: return None
    t = token.strip().lower().replace(" ","")
    if len(t)==3 and t[:2] in ('10','t0'):
        r,s='t',t[2]
    elif len(t)==2:
        r,s=t[0],t[1]
    else:
        return None
    r=r.upper()
    return (r+s) if (r in RANKS and s in SUITS) else None

def deck_without(seen:set[str]):
    return [r+s for r in RANKS for s in SUITS if (r+s) not in seen]

def straight_high_from(desc_unique):
    # desc_unique: sorted high->low list of ranks (ints). Return high of straight or 5 for wheel, else None.
    if len(desc_unique) < 5: return None
    for i in range(len(desc_unique)-4):
        block = desc_unique[i:i+5]
        if block[0]-block[4] == 4:
            return block[0]
    s = set(desc_unique)
    if {14,5,4,3,2}.issubset(s):
        return 5
    return None

def rank_key5(cards5):
    # Comparable key for a 5-card hand: [category, tie-breakers...], larger is better
    ranks = sorted([RANK_VAL[c[0]] for c in cards5], reverse=True)
    suits = [c[1] for c in cards5]
    counts = Counter(ranks)
    by_count = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    uniq = sorted(set(ranks), reverse=True)

    is_flush = (len(set(suits))==1)
    sh = straight_high_from(uniq)
    if is_flush and sh is not None:
        return [8, sh]  # Straight flush

    counts_sorted = sorted(counts.values(), reverse=True)
    if counts_sorted[0]==4:
        four = by_count[0][0]
        kick = max([r for r in ranks if r!=four])
        return [7, four, kick]  # Quads
    if counts_sorted[0]==3 and counts_sorted[1]==2:
        trips = [rk for rk,c in by_count if c==3][0]
        pair  = [rk for rk,c in by_count if c==2][0]
        return [6, trips, pair]  # Full House
    if is_flush:
        return [5] + ranks  # Flush
    if sh is not None:
        return [4, sh]  # Straight
    if counts_sorted[0]==3:
        trips = [rk for rk,c in by_count if c==3][0]
        kickers = sorted([r for r in ranks if r!=trips], reverse=True)[:2]
        return [3, trips] + kickers  # Trips
    if counts_sorted[0]==2 and counts_sorted[1]==2:
        pairs = sorted([rk for rk,c in by_count if c==2], reverse=True)
        kick = max([r for r in ranks if r not in pairs])
        return [2, pairs[0], pairs[1], kick]  # Two Pair
    if counts_sorted[0]==2:
        pr = [rk for rk,c in by_count if c==2][0]
        ks = sorted([r for r in ranks if r!=pr], reverse=True)[:3]
        return [1, pr] + ks  # One Pair
    return [0] + ranks  # High Card

def best_of_n(cards):
    """Works for 5, 6, or 7 cards. Returns {'key': key, 'name': category_name}."""
    valid = [c for c in cards if is_card(c)]
    n = len(valid)
    if n < 5:
        return {'key':[0,0,0,0,0,0], 'name':'N/A'}
    if n == 5:
        k = rank_key5(valid)
        return {'key':k, 'name':CAT_NAME[k[0]]}
    best = None
    for five in combinations(valid[:7], 5):
        k = rank_key5(five)
        if best is None or k > best:
            best = k
    return {'key':best, 'name':CAT_NAME[best[0]]}

def simulate_equity(known_hands: dict[int, tuple[str,str]],
                    total_seats: int,
                    sims: int = 20000,
                    board: list[str] | None = None,
                    rng_seed: int | None = 12345):
    """Monte Carlo simulation.
       known_hands: seat -> (c1,c2), seats start at 1..activeX
       total_seats: <=9
       board: 0..5 community cards (valid, no dups)
       Returns rows: list of dicts (Player, Hand, Status, Win %, Tie %, Equity %, Made Hand (now))
    """
    if rng_seed is not None:
        random.seed(rng_seed)
    active_seats = sorted(known_hands.keys())
    activeX = len(active_seats)
    assert 2 <= total_seats <= 9, "total_seats must be 2..9"

    seen = set()
    for seat, (c1,c2) in known_hands.items():
        assert is_card(c1) and is_card(c2) and c1!=c2, f"Invalid cards for seat {seat}"
        assert c1 not in seen and c2 not in seen, "Duplicate in known hands"
        seen.add(c1); seen.add(c2)

    board = board or []
    for c in board:
        assert is_card(c) and c not in seen, "Invalid or duplicate board card"
        seen.add(c)

    base_deck = deck_without(seen)
    miss = 5 - len(board)
    unknown_seats = list(range(activeX+1, total_seats+1))

    win = Counter(); tie = Counter(); eq = Counter()

    for _ in range(sims):
        deck = base_deck[:]
        random.shuffle(deck)
        # sample unknown holes
        sampled = {}
        for s in unknown_seats:
            c1 = deck.pop(); c2 = deck.pop()
            sampled[s] = (c1, c2)
        # draw remaining board
        board_draw = deck[-miss:] if miss>0 else []

        best_key = None
        winners = []

        # evaluate known seats
        for seat in active_seats:
            h = known_hands[seat]
            seven = [h[0],h[1]] + board + board_draw
            k = best_of_n(seven)['key']
            if best_key is None or k > best_key:
                best_key = k; winners = [seat]
            elif k == best_key:
                winners.append(seat)

        # evaluate unknown seats
        for seat in unknown_seats:
            h = sampled[seat]
            seven = [h[0],h[1]] + board + board_draw
            k = best_of_n(seven)['key']
            if k > best_key:
                best_key = k; winners = [seat]
            elif k == best_key:
                winners.append(seat)

        if len(winners)==1:
            w = winners[0]; win[w]+=1; eq[w]+=1.0
        else:
            share = 1.0/len(winners)
            for w in winners:
                tie[w]+=1; eq[w]+=share

    # compose rows
    rows = []
    # current made hand labels for known seats (street)
    for seat in active_seats:
        h = known_hands[seat]
        made = best_of_n([h[0],h[1]] + board)['name']
        rows.append({
            "Player": str(seat),
            "Hand": f"{h[0]} {h[1]}",
            "Status": "Active",
            "Win %": trunc2pct((win[seat]/sims)*100),
            "Tie %": trunc2pct((tie[seat]/sims)*100),
            "Equity %": trunc2pct((eq[seat]/sims)*100),
            "Made Hand (now)": made
        })

    if unknown_seats:
        uw = sum(win[s] for s in unknown_seats)
        ut = sum(tie[s] for s in unknown_seats)
        ue = sum(eq[s]  for s in unknown_seats)
        rows.append({
            "Player": f"Random Hands ({len(unknown_seats)})",
            "Hand": "?? ??",
            "Status": "Unknown",
            "Win %": trunc2pct((uw/sims)*100),
            "Tie %": trunc2pct((ut/sims)*100),
            "Equity %": trunc2pct((ue/sims)*100),
            "Made Hand (now)": "N/A"
        })
    return rows

# ---------- Parsing helpers to match your input style ----------
def parse_two(raw: str):
    s = raw.strip().lower().replace(" ","")
    if s in ("??","unknown",""):
        return ("??","??")
    # find two suits positions
    idx = [i for i,ch in enumerate(s) if ch in "cdhs"]
    if len(idx) < 2: return None
    a = norm_card(s[:idx[0]+1])
    b = norm_card(s[idx[0]+1:])
    if not (is_card(a) and is_card(b)) or a==b:
        return None
    return (a,b)

def parse_three(raw: str):
    # Accepts "Ah6c9d" OR "Ah 6c 9d" OR "qh,qd,qc"
    s = raw.strip()
    parts = re.split(r"[,\s\-]+", s)
    c = []
    if len(parts)==3:
        c = [norm_card(p) for p in parts]
    else:
        z = re.sub(r"\s+","", s).lower()
        idx = [i for i,ch in enumerate(z) if ch in "cdhs"]
        if len(idx) >= 3:
            c = [norm_card(z[:idx[0]+1]),
                 norm_card(z[idx[0]+1:idx[1]+1]),
                 norm_card(z[idx[1]+1:])]
    if len(c)!=3 or any(not is_card(x) for x in c) or len(set(c))!=3:
        return None
    return c

# ---------- High-level runner ----------
def run_scenario(script: str, sims: int = 20000, total_seats_limit: int = 9):
    """
    script: e.g. "p3/8; 1AhKd; 2QsJc; 39h9s; flop Ah6c9d; turn Td; river Jh"
    Returns rows (list of dicts) from simulate_equity.
    """
    tokens = [t.strip() for t in script.replace("\n",";").split(";") if t.strip()]
    ST_total = 9
    ST_activeX = None
    seats: dict[int, tuple[str,str] | None] = {}
    board: list[str] = []
    stage = "askXY"
    current_seat = 1

    for raw in tokens:
        low = raw.lower()
        if stage == "askXY":
            s = low.replace(" ","")
            if s.startswith("p"): s = s[1:]
            m = re.match(r"^(\d+)\/(\d+)$", s)
            if not m:
                raise ValueError("First token must be like pX/Y (e.g., p3/8)")
            x, y = int(m.group(1)), int(m.group(2))
            if not (1 <= x <= y <= total_seats_limit):
                raise ValueError("X must be 1..Y and Y<=9")
            ST_total, ST_activeX = y, x
            seats = {i: None for i in range(1, y+1)}
            stage = "askHands"
            current_seat = 1
            continue

        if stage == "askHands":
            # Allow forms like "1AhKd" or "AhKd" (implicitly next seat)
            m = re.match(r"^(\d+)\s*(.+)$", raw.strip())
            if m:
                target = int(m.group(1))
                payload = m.group(2)
            else:
                target = current_seat
                payload = raw

            pair = parse_two(payload)
            if pair is None:
                raise ValueError(f"Invalid hand for seat {target}: {payload}")

            if pair[0]=="??":
                seats[target] = None
            else:
                # collision check vs previous seats and board
                used = set()
                for s in range(1, ST_total+1):
                    if seats.get(s):
                        used.add(seats[s][0]); used.add(seats[s][1])
                for c in board:
                    used.add(c)
                if pair[0] in used or pair[1] in used:
                    raise ValueError("Card collision with existing cards/board.")
                seats[target] = pair

            if target == current_seat:
                current_seat += 1
            # auto move to flop once all active seats handled
            if current_seat > ST_activeX:
                stage = "askFlop"
            continue

        if stage == "askFlop" and low.startswith("flop"):
            trio = parse_three(raw[4:].strip())
            if not trio:
                raise ValueError("Bad flop.")
            # collision against known hands
            used = set()
            for s in range(1, ST_total+1):
                if seats.get(s):
                    used.add(seats[s][0]); used.add(seats[s][1])
            if any(c in used for c in trio):
                raise ValueError("Flop collides with players' cards.")
            board = trio[:]
            stage = "askTurn"
            continue

        if stage in ("askFlop","askTurn") and low.startswith("turn"):
            c = norm_card(raw[4:].strip())
            if not is_card(c):
                raise ValueError("Bad turn.")
            used = set(board)
            for s in range(1, ST_total+1):
                if seats.get(s):
                    used.add(seats[s][0]); used.add(seats[s][1])
            if c in used:
                raise ValueError("Turn collision.")
            if len(board) != 3:
                raise ValueError("Need flop before turn.")
            board.append(c)
            stage = "askRiver"
            continue

        if stage in ("askFlop","askTurn","askRiver") and low.startswith("river"):
            r = norm_card(raw[5:].strip())
            if not is_card(r):
                raise ValueError("Bad river.")
            used = set(board)
            for s in range(1, ST_total+1):
                if seats.get(s):
                    used.add(seats[s][0]); used.add(seats[s][1])
            if r in used:
                raise ValueError("River collision.")
            if len(board) != 4:
                raise ValueError("Need turn before river.")
            board.append(r)
            stage = "done"
            continue

        # ignore other tokens like "new/change" in this function
    # Build known_hands for active seats only
    known = {}
    for seat in range(1, ST_total+1):
        if seat <= ST_activeX:
            if seats.get(seat):
                known[seat] = seats[seat]
            else:
                # unknown active seat → treated as random hand
                pass

    rows = simulate_equity(known_hands=known, total_seats=ST_total, sims=sims, board=board)
    return rows

def print_table(rows):
    # Pretty-print a table in console
    cols = ["Player","Hand","Status","Win %","Tie %","Equity %","Made Hand (now)"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    def line(char="-"):
        return "+ " + " + ".join(char*(widths[c]+2) for c in cols) + " +"
    def fmt_row(r):
        return "| " + " | ".join(str(r[c]).ljust(widths[c]) for c in cols) + " |"
    print(line("-"))
    print("| " + " | ".join(c.ljust(widths[c]) for c in cols) + " |")
    print(line("="))
    for r in rows:
        print(fmt_row(r))
    print(line("-"))

def interactive():
    print("Hold'em Equity — Console (type 'help' for usage, 'run' to simulate, 'quit' to exit)")
    ST_total = None
    script_parts = []
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye"); return
        if not line: continue
        low = line.lower()
        if low in ("quit","exit"):
            print("bye"); return
        if low == "help":
            print("Usage:\n  pX/Y\n  <seat><cards> e.g. 1AhKd (or just AhKd to fill next seat)\n  flop Ah6c9d\n  turn Td\n  river Jh\n  run  (simulate with current inputs)\n  reset\n  sims 50000  (change simulations)\n")
            continue
        if low.startswith("sims"):
            parts = low.split()
            if len(parts)==2 and parts[1].isdigit():
                interactive.sims = int(parts[1])
                print(f"sims set to {interactive.sims}")
            else:
                print("Try: sims 25000")
            continue
        if low == "reset":
            script_parts = []
            ST_total = None
            print("reset.")
            continue
        if low == "run":
            script = "; ".join(script_parts)
            try:
                rows = run_scenario(script, sims=interactive.sims)
                print_table(rows)
            except Exception as e:
                print("Error:", e)
            continue
        # otherwise, accumulate script lines
        script_parts.append(line)

# default sims for interactive mode
interactive.sims = 20000

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Run one-shot from command-line argument
        script = " ".join(sys.argv[1:])
        rows = run_scenario(script, sims=20000)
        print_table(rows)
    else:
        # Interactive REPL
        interactive()
