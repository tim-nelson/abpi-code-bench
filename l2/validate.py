"""Validate every L2 case against l2/schema.json. Non-zero exit = build fails.

Runs the JSON Schema, then the things a schema cannot express:

  * ONE KEY SIGNATURE across all cases -- the L1 contract, carried forward.
  * RECEIPTS AUDIT (SPEC §7.3): every filled canonical value has non-empty
    `sources`, every `basis` is a registered rule id or an adjudication id, and
    every adjudication is used at least once (dead-fix detection). Verdict rows
    carry receipts too, and are audited the same way.
  * RECONCILIATION (SPEC §7.5): at least 1,902 cases, every case number
    distinct, siblings symmetric (A lists B <=> B lists A) and consistent with
    the shared source file, and every L1 file accounted for by at least one
    case.
  * SEGMENTS RE-SLICED (SPEC §7.4): every segment ref is cut out of
    records.jsonl / pdf_records.jsonl again and its text_length and text_sha256
    are recomputed. A segment whose sha no longer matches is pointing at
    different text than the one the builder attested.
  * ATTEST RECHECK (SPEC §6/§7.4): all six leakage checks are recomputed here
    on the re-sliced text and compared field by field. This file holds its OWN
    reading of the ruling check (a token/window scan, NOT a regex -- see the
    long comment on it; a copied regex is what let R24 through), its own
    outcome-headline rule, its own whitespace-collapsing comparison, chip
    parser and PDF marker scan -- deliberately NOT imported from build.py,
    because an audit that borrows the builder's regex cannot detect the
    builder's regex being wrong. (`RULES` is imported: a registry of legal
    rule ids is a definition, not a witness.)
  * VERDICT INVARIANTS (SPEC §5/§6c): (clause, code_year, occurrence) unique
    per case, `final` never null, `flipped_on_appeal` consistent with the two
    attributed rulings, and an unappealed case never carrying an Appeal Board
    ruling. From l2.2 (DEFECTS D3) also: no row carries a retired back-fill
    basis, a `dual_ruling` row never carries a Panel ruling, and an attribution
    on an appealed case is backed by that body's own prose receipts.
  * RENDITION INDICES: in range, and pointing at a segment of the right kind.

Determinism is NOT checked here -- a validator that re-runs the builder shares
a witness with the thing it audits. It is checked externally, by building twice
and comparing bytes; the commands are printed at the end of a green run.

    uv run --with jsonschema python l2/validate.py
"""

import hashlib
import html as html_mod
import json
import pathlib
import re
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = ROOT / "data" / "l2" / "cases.jsonl"
SCHEMA = ROOT / "l2" / "schema.json"
ADJUDICATIONS = ROOT / "l2" / "adjudications.json"
L1_RECORDS = ROOT / "data" / "l1" / "records.jsonl"
L1_DERIVED = ROOT / "data" / "l1" / "derived.jsonl"
L1_PDF = ROOT / "data" / "l1" / "pdf_records.jsonl"
SLOT_CORRECTIONS = ROOT / "data" / "l2" / "clause_slot_corrections.jsonl"

MAX_REPORT = 20

# Independent copy of the closed prose-only verdict audit. This intentionally
# does not import build.PROSE_ONLY_VERDICT_READ: the validator must catch a
# builder entry added, removed or altered on only one side. The raw L1 report
# pane is re-hashed and every quote is re-found in `audit_segments`; accepted
# rows are compared field-for-field, while the refused row must stay absent.
V_PROSE_ONLY_VERDICT_READ = {
    ("AUTH/2337/7/10", "2"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "be983dbd1d631257a61b8afb150511ba45d05785238e889dc1915fc594cc6d8d",
        "quotes": ("Given that the item was not in its final form and had not been used as "
                   "described above the Panel ruled no breach of Clauses 2, 7.2, 9.10 and "
                   "22.1 of the Code.",),
    },
    ("AUTH/2337/7/10", "9.10"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "be983dbd1d631257a61b8afb150511ba45d05785238e889dc1915fc594cc6d8d",
        "quotes": ("Given that the item was not in its final form and had not been used as "
                   "described above the Panel ruled no breach of Clauses 2, 7.2, 9.10 and "
                   "22.1 of the Code.",),
    },
    ("AUTH/2337/7/10", "22.1"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "be983dbd1d631257a61b8afb150511ba45d05785238e889dc1915fc594cc6d8d",
        "quotes": ("Given that the item was not in its final form and had not been used as "
                   "described above the Panel ruled no breach of Clauses 2, 7.2, 9.10 and "
                   "22.1 of the Code.",),
    },
    ("AUTH/2220/3/09", "18.1"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "03cf6259f006761f4ab78bce44e57d50a6043bdd013310c38369da99c6c36d09",
        "quotes": ("The Panel ruled no breach of Clauses 18.1 and 18.4.",
                   "The Panel ruled no breach of Clauses 15.2 and 18.1 of the Code on this point.",
                   "No breach of Clauses 18.1 and 19.1 were ruled."),
    },
    ("AUTH/2316/5/10", "7.4"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "cb1087e2d7840b80c9b7032db2761194c213bf2fa736b702dd1c774ab2b11f02",
        "quotes": ("Although noting that extreme dissatisfaction was usually required before an "
                   "individual was moved to complain, on the basis of the information before it "
                   "the Panel ruled no breach of Clauses 7.2 and 7.4 of the Code.",),
    },
    ("AUTH/1855/6/06", "7.9"): {
        "decision": "accept", "final": "no_breach", "code_year": 2006,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "54dc428187e1761b5339053c2195a0754f5fe9153d1b96059e2a39f3f4753836",
        "quotes": ("Thus the Panel ruled no breach of Clauses 7.2, 7.8, 7.9 and 7.10 of the Code.",),
    },
    ("AUTH/1855/6/06", "9.1"): {
        "decision": "accept", "final": "no_breach", "code_year": 2006,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "54dc428187e1761b5339053c2195a0754f5fe9153d1b96059e2a39f3f4753836",
        "quotes": ("The Panel did not consider that the page failed to maintain a high standard "
                   "and thus no breach of Clause 9.1 of the Code was ruled.",
                   "The Panel did not consider that the pages were misleading and thus ruled no "
                   "breach of Clauses 7.2, 7.4 and 9.1 of the Code."),
    },
    ("AUTH/1884/8/06", "15.2"): {
        "decision": "accept", "final": "no_breach", "code_year": 2006,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "c6c4b3b86a3d43df69e2c9ea56b514ab61cee04dd7814cfd9b1df03837e479d4",
        "quotes": ("The Panel considered that the evidence before it was such that it was not "
                   "possible to determine whether on the balance of probabilities the "
                   "representative’s conduct amounted to a breach of Clauses 15.2 and 15.4 of "
                   "the Code and thus no breach of these clauses was ruled.",
                   "The Panel thus ruled no breach of Clauses 15.2 and 15.4 of the Code.",
                   "The Panel did not know where the truth lay and thus ruled no breach of "
                   "Clauses 15.2 and 15.4 of the Code.",
                   "The Panel ruled no breach of Clauses 15.2 and 15.4 of the Code."),
    },
    ("AUTH/2634/8/13", "15.9"): {
        "decision": "accept", "final": "no_breach", "code_year": 2012,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "97470a66bd1e6025ca39c4019741b678949f75e7a046e752180c0fea205b1e6e",
        "quotes": ("The Panel ruled no breach of Clauses 7.2, 7.4, 7.9, 15.2 and 15.9 of the Code.",),
    },
    ("AUTH/3587/12/21", "12.6"): {
        "decision": "accept", "final": "no_breach", "code_year": 2021,
        "panel": "no_breach", "appeal_board": "no_breach", "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "2bd43b24d74f45ca02a3a1cc2d6c149539dbb87abf75fe17123640fa9637a7d0",
        "quotes": ("It therefore ruled no breach of Clauses 12.1, 12.3, 12.4 and 12.6 of the 2021 Code.",
                   "The Appeal Board agreed with the Panel’s comments above and upheld its rulings "
                   "of no breach of Clauses 2, 3.3, 5.1, 12.1, 12.3, 12.4 and 12.6 of the 2021 Code."),
    },
    ("AUTH/2667/11/13", "2"): {
        "decision": "accept", "final": "no_breach", "code_year": None,
        "panel": "no_breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "3cc2fef8522111bd70ceee74d4f353053687d87c2662ad48bab93b08cb81babc",
        "quotes": ("Thus the Panel ruled no breach of Clauses 9.1 and 2 of the 2006 Code.",
                   "The Panel ruled no breach of Clauses 21.3 and consequently no breach of "
                   "Clauses 9.1 and 2 of the 2011 Code in relation to NCT00472290.",
                   "The results had been disclosed and the Panel considered that there was no "
                   "breach of Clause 2 and ruled accordingly."),
    },
    ("AUTH/3258/10/19", "7.9"): {
        "decision": "accept", "final": "breach", "code_year": 2019,
        "panel": "breach", "appeal_board": None, "dual_ruling": False,
        "dual_ruling_appeal_board": False,
        "source_sha256": "b0f3f0c2ba2929d9ab8fac9dd560488cd5ffb700ed115465940f068385ed3d26",
        "quotes": ("The available evidence was not reflected in the formulary decision guide "
                   "and the Panel therefore ruled a breach of the Code.",
                   "The Panel did not consider that the complainant had made an allegation with "
                   "regard to Clause 7.9 in this regard and therefore made no ruling."),
    },
    ("AUTH/3615/3/22", "9.1"): {
        "decision": "refuse",
        "source_sha256": "b02a3bd51cd6b114559c4e75b46b442af2f460c41723ab3061422bb3d7c61cbb",
        "quotes": ("The Panel consequently ruled no breach of Clauses 9.1 and 2 of the 2019 Code.",
                   "Turning to the case now before it, Case AUTH/3615/3/22, the Panel considered "
                   "that there was a difference to the previous case (Case AUTH/3504/4/21)."),
    },
}

# ---------------------------------------------------------------------------
# The validator's OWN implementation of the attest. Implemented from SPEC §6,
# not imported. If this disagrees with build.py the build fails, which is the
# whole point: two independent readings of the same spec, over the same bytes.
#
# THE RULING CHECK IS NOT A REGEX HERE, AND MUST NEVER BECOME ONE AGAIN.
# Until 2026-08-10 this file held `V_RULING_RE`, a copy of build.py's
# `RULING_RE` -- byte-identical, comment and all. So when the `[^.]{0,70}` in
# it turned out to be unable to cross the decimal point of a clause number
# (DEFECTS R24: "No breach of Clause 2 was ruled" matched, "No breach of
# Clause 9.2 was ruled" did not), BOTH witnesses were blind in exactly the same
# place. The build stayed green while four items shipped with the answer in
# their own prompt, two of them in the test split. A re-typed regex is one
# copy-paste from being the same object again; the independence has to come
# from the MECHANISM, so this reading is a token/window scan:
#
#   * cut the span into sentences at every '.' that is NOT followed by a digit
#     (a decimal point inside a clause number is not a sentence end -- that is
#     the whole of R24 stated positively);
#   * split each sentence into word tokens, keeping their offsets;
#   * recognise the two frames of SPEC §6 as TOKEN SEQUENCES with a character
#     distance between the two halves:
#
#       F1  'panel', or 'appeal' 'board', then within 90 characters a token
#           starting rul/consider/accept/uph/decid, or the token 'noted'
#       F2  'breach'|'breaches' 'of' ('clause'|'clauses'|['the'] 'code'), then
#           within 70 characters 'was'|'were' 'ruled'
#
#   * exempt an F2 hit -- and only F2, which names no ruling body -- when a
#     case number that is not this file's own sits within 120 characters of
#     it. That is another case's ruling quoted as precedent, the legitimate
#     class bench/generate.py's TRIPWIRE documents.
#
# Nothing above shares a line with build.py. A disagreement on any segment is
# reported as an attest failure, which is what a second reading is for.
# ---------------------------------------------------------------------------
V_F1_VERB_PREFIXES = ("rul", "consider", "accept", "uph", "decid")
V_F1_VERB_EXACT = ("noted",)
V_F1_WINDOW = 90
V_F2_WINDOW = 70
V_PRECEDENT_WINDOW = 120
V_CASE_NUM_RE = re.compile(r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b")


def v_case_number(m):
    prefix, serial, month, year = m.groups()
    return f"{prefix.upper()}/{int(serial)}/{int(month)}/{year[-2:]}"


def v_sentences(span):
    """(offset, text) for each sentence. A '.' ends a sentence only when the
    next character is not a digit."""
    out, start = [], 0
    for i, ch in enumerate(span):
        if ch != ".":
            continue
        if i + 1 < len(span) and span[i + 1].isdigit():
            continue
        out.append((start, span[start:i]))
        start = i + 1
    out.append((start, span[start:]))
    return out


def v_tokens(text):
    """(start, end, lowercased) for every maximal run of word characters."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i].isalnum() or text[i] == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append((i, j, text[i:j].lower()))
            i = j
        else:
            i += 1
    return out


def v_gap_is_space(text, a, b, single=False):
    """The characters between two tokens: whitespace only (and, where the spec
    writes a literal space rather than \\s+, exactly one)."""
    gap = text[a:b]
    return gap == " " if single else (bool(gap) and gap.isspace())


def v_ruling_hits(sentence):
    """[(frame, match_start, match_end)] for every ruling frame in ONE
    sentence, offsets relative to the sentence."""
    toks = v_tokens(sentence)
    hits = []
    # -- F1: the ruling body, then a ruling verb within 90 characters --------
    bodies = []
    for k, (s, e, w) in enumerate(toks):
        if w == "panel":
            bodies.append((s, e))
        elif w == "appeal" and k + 1 < len(toks) and toks[k + 1][2] == "board" \
                and v_gap_is_space(sentence, e, toks[k + 1][0], single=True):
            bodies.append((s, toks[k + 1][1]))
    verbs = [(s, e) for s, e, w in toks
             if w in V_F1_VERB_EXACT or w.startswith(V_F1_VERB_PREFIXES)]
    for bs, be in bodies:
        for vs, ve in verbs:
            if 0 <= vs - be <= V_F1_WINDOW:
                hits.append(("F1", bs, ve))
                break
    # -- F2: the breach statement, then 'was/were ruled' within 70 chars -----
    statements = []
    for k, (s, e, w) in enumerate(toks):
        if w not in ("breach", "breaches"):
            continue
        if k + 2 >= len(toks) or toks[k + 1][2] != "of":
            continue
        if not v_gap_is_space(sentence, e, toks[k + 1][0]):
            continue
        after = toks[k + 2]
        if not v_gap_is_space(sentence, toks[k + 1][1], after[0]):
            continue
        if after[2] in ("clause", "clauses", "code"):
            end = after[1]
        elif after[2] == "the" and k + 3 < len(toks) and toks[k + 3][2] == "code" \
                and v_gap_is_space(sentence, after[1], toks[k + 3][0]):
            end = toks[k + 3][1]
        else:
            continue
        # `(?:no\s+)?` in the spec: the match starts at the 'no' when there is
        # one, which is what the precedent window is measured from.
        start = s
        if k and toks[k - 1][2] == "no" and v_gap_is_space(sentence, toks[k - 1][1], s):
            start = toks[k - 1][0]
        statements.append((start, end))
    # 'was ruled', and 'was <one adverb> ruled' -- thus/also/therefore/again/
    # not/accordingly and ten more, 197 sentences corpus-wide. One slot, any
    # word: a fixed list would be a new hole the first time the PMCPA writes an
    # eleventh adverb.
    ruleds = []
    for k, (s, e, w) in enumerate(toks):
        if w not in ("was", "were"):
            continue
        for j in (k + 1, k + 2):
            if j >= len(toks) or toks[j][2] != "ruled":
                continue
            if all(v_gap_is_space(sentence, toks[t][1], toks[t + 1][0]) for t in range(k, j)):
                ruleds.append((s, toks[j][1]))
                break
    for ss, se in statements:
        for rs, re_ in ruleds:
            if 0 <= rs - se <= V_F2_WINDOW:
                hits.append(("F2", ss, re_))
                break
    return hits


# The validator's own statement of build.py's RULING_LANGUAGE_FALSE_MATCHES:
# hits that LOOK like ruling language and are not an adjudicator speaking.
#
# Duplicated deliberately, and it does not weaken the independence rule. What
# that rule forbids is the validator sharing the builder's READING -- which is
# why the scan above is a token walk and not a copy of RULING_RE. This table is
# not a reading; it is a hand DECISION about two named spans, of the same
# species as CLAUSE_WITNESS_READ and l2/adjudications.json. An independent
# recomputation cannot re-derive a decision, so for the two layers to agree
# both must state it, and stating it twice is what makes a silent divergence
# impossible: if the builder's row were deleted the renditions would go dirty
# and this file's `dead rows` check would fire on the same pass.
#
# AUTH-2465-12-11.html. The last word of a quoted advertisement heading
# ('Recommendations of the Consensus Panel') followed 44 characters later by
# the fourth word of the next quoted heading ('Qutenza may be considered ...').
# Two quoted headlines from the advert under complaint, with no adjudicator
# anywhere in the sentence. Re-read here against the source panes on
# 2026-08-10.
V_RULING_FALSE_MATCHES = {
    "AUTH-2465-12-11.html": (
        "Panel' was a diagram headed 'Qutenza may be considered",
        "Panel’ was a diagram headed ‘Qutenza may be considered",
    ),
}
V_RULING_FALSE_FIRED = set()


def v_ruling_language(span, own_cases, file=None):
    """True when the span carries ruling language that is not another case's
    precedent."""
    known_false = V_RULING_FALSE_MATCHES.get(file, ())
    for offset, sentence in v_sentences(span):
        for frame, s, e in v_ruling_hits(sentence):
            if sentence[s:e] in known_false:
                V_RULING_FALSE_FIRED.add((file, sentence[s:e]))
                continue
            if frame == "F2":
                lo = max(0, offset + s - V_PRECEDENT_WINDOW)
                hi = min(len(span), offset + e + V_PRECEDENT_WINDOW)
                cited = {v_case_number(m) for m in V_CASE_NUM_RE.finditer(span[lo:hi])}
                if cited - set(own_cases):
                    continue
            return True
    return False


def check_v_ruling_false_matches(failures):
    """A row that never fired is a row written against text that has moved."""
    for file, quotes in sorted(V_RULING_FALSE_MATCHES.items()):
        for q in quotes:
            if (file, q) not in V_RULING_FALSE_FIRED:
                failures.append((file, "attest", "V_RULING_FALSE_MATCHES",
                                 f"declared false ruling-language match {q!r} was never found; "
                                 f"re-read the case before trusting the row"))


# Independent statement of build.py's reviewed response-attest decisions.
# These are decisions, not shared parsing: the validator still computes all
# six checks itself, then requires the exact pre-decision failure set and the
# exact full-span hash before clearing it.  A stale or newly firing detector is
# therefore a validation failure rather than a silently broadened exemption.
V_RESPONSE_ATTEST_FALSE_POSITIVES = {
    ("AUTH-3796-7-23.html", "8825d7fa4b73c6b068b62cf6625b300fac35e63b1f4eebfdc4ce50cfeb1405d2"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3853-11-23.html", "7d3ffe87371ca959424338f35796cb9ff1ae108445da2658b13e2d77af58b2bb"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3864-12-23.html", "a35ddd15f3c31b1c61ac3c6f3cdebd7bfeff600cf806a33799564cd3c8b5c176"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3866-12-23.html", "6e2da3cd8943b95d5a2c2a6448edd06ed82c2764f3a2895b0623c07e8f20c671"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3867-12-23.html", "80b674b2e1828d9d3e4b4624ddf2305459342e73ca49a72b17e70ecf59dd4810"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3879-2-24.html", "d247132e47c4ed89ac3bf85b9594afc24fa9ea282255f50551a3ce34757501f0"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3889-4-24.html", "428a258f761478715ec55bea62fee396e221dedb23655be40314e0cd01d6b61c"): frozenset(("no_ruling_language",)),
    ("AUTH-3892-4-24.html", "93a641d644f10f1837fce6b1d4e9e44357240bba73536e1659924bd6642c9903"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("AUTH-3893-4-24.html", "1ae40fc4b569501789b3e1c21604d45f03722c12b3d117dac1e83f20baaef80b"): frozenset(("no_ruling_language",)),
    ("AUTH-3897-5-24.html", "b59cac1f10dcd51e012c1f06d3a651f9badb6f05f5ec51e1ab18691c21b9a263"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3899-5-24.html", "fce5c3380727b601698f8f1ad0bbb3db1e0e4be3a1bd47f9bfa5198fa97b52d5"): frozenset(("no_ruling_language",)),
    ("AUTH-3901-5-24.html", "f13c3aa3d7eed9f0dfead7f8f7a479074b880fff8056e124484b5d696d5e9785"): frozenset(("no_ruling_language",)),
    ("AUTH-3905-5-24.html", "28aaf2193700075ec39e210196147feac43b009c33ec03a9279bd202d6d4c756"): frozenset(("no_ruling_language",)),
    ("AUTH-3917-6-24.html", "7fbb8127947fb11d9ece739a92533bf96c4e6e20f003d2feb09462d26d44623a"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3918-6-24.html", "795b0022f0a627426a8495a00082073175bcdf7eba0ea188908ab0fc281bf7d8"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3919-6-24.html", "ea47ea57b1ae4d041af6c894ea82005d04e72bdf2e71503394b940236e5aa34a"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3920-6-24.html", "fbde191be2014159de37b6383801f5aa271402b30df7985cae0c0bd02b7f43b9"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("AUTH-3921-06-24.html", "10640a9a030dbe8808f5a762febd97640030828c8cdd203dbfc02e850686f747"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("AUTH-3924-6-24.html", "12250c31e62f2843a0f4c23af8f1dc71850d1976a38e07f0f0197fa8d6f7840b"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0209-06-24.html", "677340588f2cdf830ff0b37e258278c71e8f638c5352ad886755eef4228bbc59"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0247-07-24.html", "11657b6ddbfc0d509c36b8f265a9d92ca6814789176a8be368e2934ce00d9078"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0251-07-25.html", "9ae7983326c288a6607135cb36e562f91b345cc3bf290c319b5a4976a48b1209"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0253-08-24.html", "a203ed9bddfd4100434a260e00251545e79d3eb29f8a41039e0cff76c6c0d70e"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0270-08-24.html", "50204c03798359dc46facc91cca18dcbba4a26214d8562afd310a950fc6dea98"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0273-08-24.html", "e2277dc75540efdbf984055853925a3491c4132195ed16ba2aaea2f94ce23647"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0288-09-24.html", "1cc7b889bb13a5332dec28d1144a4d26a1bd8c1087199d5e5cb006a08f69caf6"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("CASE-0303-09-24.html", "8eb5b188d43136fbe06bb37d80e42415e6c6c350ec684ace669be29ad8b6da93"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0363-11-24.html", "53997d556f8d763397c3bfbd18172c4777076bad289d7895595058c92f655f35"): frozenset(("no_ruling_language",)),
    ("CASE-0381-11-24.html", "1b24a4e46dc19d7b08f7175f72234f5926b4c11df1a35a5bacc4b6e8ed8a54df"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("CASE-0387-12-24.html", "5817299699b5159757ae14b0f3c6a2a626482efceadcc085a13eeedf26999f12"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0392-12-24.html", "7957eefd905a2cfd8ad296a6687c6dd0c5c44bbf64ee01671388fc8d950d9b8c"): frozenset(("no_ruling_language",)),
    ("CASE-0437-01-25.html", "d7cd18ba3f1d3d9fd58d63b5a70a6938aba779da19866a84db48113f715059c2"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0446-01-25.html", "5d51937ed62a4952adac8b0cc7ff3ece49a57dd4206c31060dca579d898d29b3"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0471-02-25.html", "836c1b4a996c195b841c640b37e5275bb9dea2d0b70ce899b7496e4b2873a9ed"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0552-04-25.html", "40963204baf8163dd58838c53b3d8ae1e57aab03beaab86b9abb039e39916245"): frozenset(("no_ruling_language",)),
    ("CASE-0591-5-25.html", "68e8c7be30da17368193ae75b6d23a131afb9a7e46efca38a645f0e3026b4904"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("CASE-0596-05-25.html", "f50becf2a1c463036c4fac7b5437de27c4598f4706d7f2fd50e72bb064db4c30"): frozenset(("no_ruling_language",)),
    ("CASE-0599-05-25.html", "e52ec7097e605ad9038649c24fb50cce39c07f69d53e9dccff0c95f8e3da58dc"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0664-07-25.html", "4c1c59aeee3cc3cd2c5498b03da8cb1ae3c4edc24647b6fb007e29a48f914761"): frozenset(("no_ruling_language",)),
    ("CASE-0681-08-25.html", "c3704030aa146ee65c39eb81fcfde37ea23c310ce72a0f6b6d7ff104b4b6c2d1"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("CASE-0694-08-25.html", "088fb348c84a849deb54417712f18433e0e41a62469a860282120c88655ad1a4"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0710-08-25.html", "1ba35ef7a4d99113ec4d61947001cda8ec164105ba63a3cf0821f4589987207c"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0722-09-25.html", "a9709ce206bdb0481b9772a187c91a50a1fdd7fe6987be819a278e3e6810b110"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0761-10-25.html", "239457721a54afc2d97ae80bb194112549bd91f4c36929c97f8e0815dc47fe7c"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table", "no_ruling_language")),
    ("CASE-0777-10-25.html", "bfb7dfae5d8b3c061b5faa8565a38ff2406c339702868f4e58b4d2d25888e74d"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
    ("CASE-0836-12-25.html", "91fe19a8adf5e45a3d92e18bc58389040852bb4d6b8ce5f911b3075a6483d9d7"): frozenset(("no_outcome_banner", "no_outcome_heading", "no_outcome_table")),
}
V_RESPONSE_ATTEST_FALSE_POSITIVE_FIRED = set()
V_RESPONSE_ATTEST_FALSE_POSITIVE_ERRORS = set()


def v_apply_response_attest_false_positive(span, file, checks):
    digest = hashlib.sha256(span.encode("utf-8")).hexdigest()
    key = (file, digest)
    expected = V_RESPONSE_ATTEST_FALSE_POSITIVES.get(key)
    if expected is None:
        return
    failed = frozenset(name for name, passed in checks.items() if not passed)
    if failed != expected:
        V_RESPONSE_ATTEST_FALSE_POSITIVE_ERRORS.add(
            (file, digest, tuple(sorted(expected)), tuple(sorted(failed))))
        return
    V_RESPONSE_ATTEST_FALSE_POSITIVE_FIRED.add(key)
    for name in expected:
        checks[name] = True


def check_v_response_attest_false_positives(failures):
    for file, digest in sorted(
            set(V_RESPONSE_ATTEST_FALSE_POSITIVES) - V_RESPONSE_ATTEST_FALSE_POSITIVE_FIRED):
        failures.append((file, "attest", "V_RESPONSE_ATTEST_FALSE_POSITIVES",
                         f"reviewed response span {digest} was never matched exactly; re-read it"))
    for file, digest, expected, actual in sorted(V_RESPONSE_ATTEST_FALSE_POSITIVE_ERRORS):
        failures.append((file, "attest", "V_RESPONSE_ATTEST_FALSE_POSITIVES",
                         f"response span {digest} expected failures {list(expected)}, "
                         f"but independently recomputed {list(actual)}"))


# ---------------------------------------------------------------------------
# The validator's OWN reading of the VERDICT-EVIDENCE passive frame (DEFECTS
# R28 / audit round-2A N1). Same discipline as the attest above and for the
# same reason: until 2026-08-10 nothing in the repo held the verdict-evidence
# reader to anything, so when `RULED_PASSIVE_RE`'s adverb slot enumerated three
# words and its gap could not cross a decimal point, "No breach of Clause 15.4
# was thus ruled" created no evidence and no witness noticed. This is a token
# scan, not a re-typed regex.
#
# It is deliberately a SUBSET reader, and the check is one-directional: every
# (polarity, clause) it reads out of a case's panel_ruling prose must appear in
# that clause's published `prose_panel_*` receipt. The converse is not asserted
# -- the builder has four more frames (active, uphold, coordinated, anaphoric)
# and the R17 not-warranted rule, so it legitimately reads more than this does.
# What the direction buys is the thing that matters: narrowing the builder's
# pattern makes the recomputation state a reading the build no longer carries,
# and the build fails naming the case.
#
# Three refusals, each mirroring a builder rule by MECHANISM rather than by
# copied code, and each measured to be necessary (without them the check
# reports disagreements that are not defects):
#
#   * a sentence naming the Appeal Board is skipped -- the speaker is then the
#     Board or ambiguous, and either way it is not a panel receipt;
#   * a sentence citing a case number that is not this file's own is skipped --
#     that is another case's ruling recited as precedent;
#   * a positive statement whose span to the verb contains 'no breach' is
#     skipped -- the outer match has swallowed the real (negative) statement,
#     which is read separately from its own head.
V_RULED_ADVERB_STOP = ("not", "never")
V_VERDICT_WINDOW = 60


def v_clause_number(text, toks, j):
    """(clause, index_after) for one clause number at token j, or (None, j).

    '15.4' arrives as the tokens '15' and '4' with a '.' between them: the
    decimal point is the only separator that keeps two numbers one clause. A
    leading component of three or more digits is a year or a case serial.

    The second component is read as the LEADING DIGITS of its token, because
    the corpus sometimes drops the space after it: AUTH/3876/2/24 writes 'no
    breach of Clauses 28.1 and 31.1of the 2021 Code', which tokenises as
    '31', '1of'. A regex reading crosses that without noticing; a token
    reading has to be told.
    """
    n = len(toks)
    if j >= n or not toks[j][2].isdigit():
        return None, j
    num, end = toks[j][2], j
    if j + 1 < n and toks[j + 1][2][:1].isdigit() and text[toks[j][1]:toks[j + 1][0]] == ".":
        tail = toks[j + 1][2]
        digits = tail[:len(tail) - len(tail.lstrip("0123456789"))]
        num, end = f"{num}.{digits}", j + 1
    head = num.split(".")[0]
    if len(head) >= 3 or int(head) == 0:
        return None, j
    return num, end + 1


def v_clause_list(text, toks, k):
    """(clauses, index_after) for the clause list starting at token k.

    Items are numbers, each optionally preceded by 'Clause'/'Clauses'; they are
    joined by ',' 'and' 'or' '&', each optionally followed by one connective
    ('consequently', 'also', 'further').
    """
    out, i, n = [], k, len(toks)
    while i < n:
        j = i + 1 if toks[i][2] in ("clause", "clauses") else i
        num, nxt = v_clause_number(text, toks, j)
        if num is None:
            break
        if num not in out:
            out.append(num)
        i = nxt
        if i >= n:
            break
        joiner = text[toks[i - 1][1]:toks[i][0]]
        if toks[i][2] in ("and", "or"):
            i += 1
        elif joiner.strip() in (",", "&"):
            pass
        elif joiner.strip() == ".":
            # Reviewed publisher typo: "Clauses 1.11. 9.1 and 2".  A period
            # is a list joiner only when the following number itself continues
            # into another joined number.  Thus "Clause 6.1. 3. Hanging
            # comparison" cannot turn matter number 3 into a clause.
            _, after_period_item = v_clause_number(text, toks, i)
            if after_period_item >= n:
                break
            after_gap = text[toks[after_period_item - 1][1]:toks[after_period_item][0]]
            if toks[after_period_item][2] not in ("and", "or") \
                    and after_gap.strip() not in (",", "&"):
                break
        else:
            break
        if i < n and toks[i][2] in ("consequently", "also", "further"):
            i += 1
    return out, i


def v_passive_statements(sentence):
    """[(polarity, clause)] the passive verdict frame states in one sentence."""
    toks = v_tokens(sentence)
    n = len(toks)
    # 'was/were [one non-negating adverb] ruled' -- (start_of_was, end_of_ruled)
    ruleds = []
    for k, (s, e, w) in enumerate(toks):
        if w not in ("was", "were"):
            continue
        for j in (k + 1, k + 2):
            if j >= n or toks[j][2] != "ruled":
                continue
            if j == k + 2 and toks[k + 1][2] in V_RULED_ADVERB_STOP:
                continue
            if all(v_gap_is_space(sentence, toks[t][1], toks[t + 1][0]) for t in range(k, j)):
                ruleds.append((s, toks[j][1]))
                break
    if not ruleds:
        return []
    out = []
    for k, (s, e, w) in enumerate(toks):
        if w not in ("breach", "breaches"):
            continue
        if k + 2 >= n or toks[k + 1][2] != "of":
            continue
        if toks[k + 2][2] not in ("clause", "clauses"):
            continue
        clauses, after = v_clause_list(sentence, toks, k + 2)
        if not clauses:
            continue
        neg = bool(k and toks[k - 1][2] == "no")
        head_end = toks[after - 1][1]
        target = None
        for rs, re_ in ruleds:
            if 0 <= rs - head_end <= V_VERDICT_WINDOW:
                target = (rs, re_)
                break
        if target is None:
            continue
        if not neg:
            # the swallow rule: an inner 'no breach' between the head and the
            # verb is the real statement and is read from its own head.
            inner = [t for t in range(after, n) if toks[t][2] == "no"
                     and t + 1 < n and toks[t + 1][2] in ("breach", "breaches")
                     and toks[t][0] < target[0]]
            if inner:
                continue
            # 'not/never' within two words before the head detaches the ruling.
            before = [toks[t][2] for t in range(max(0, k - 3), k)]
            if {"no", "not", "never", "nor"} & set(before):
                continue
        polarity = "no_breach" if neg else "breach"
        for c in clauses:
            if (polarity, c) not in out:
                out.append((polarity, c))
    return out


def v_ruling_statement(quote, clause, polarity):
    """Does this sentence state `polarity` on `clause`? -- read INDEPENDENTLY.

    R28 stage 1's receipts check. `verdicts[].rulings` says 'this sentence, at
    these offsets, rules this way on this clause', and the F1 rule (DEFECTS)
    says an audit that borrows the builder's regex cannot detect the builder's
    regex being wrong. So this is a token walk over the whole sentence with no
    frame patterns in it at all: find every 'breach(es) of Clause(s) <list>' or
    'breach of that/this clause' head, take the polarity from the negator
    attached to THAT head, and accept if any head naming our clause carries our
    polarity.

    Two shapes need naming because they are not head-local:

    * ANAPHORA. 'no breach of that clause was ruled' names its clause earlier
      in the same sentence; the antecedent is the last clause number before the
      pronoun, which is the builder's rule stated the other way round.
    * THE CENSURE FRAME. 'did not consider that the circumstances warranted a
      ruling of a breach of Clause 2' has a POSITIVE head and is a no-breach
      ruling. Accepted only with 'did not' and a 'warrant' stem before the
      head -- R18's audit warns this frame is the one that reads backwards.

    A ruling verb ('ruled' or 'upheld') must be present somewhere in the
    sentence, so a bare allegation cannot pass as a ruling receipt.
    """
    toks = v_tokens(quote)
    n = len(toks)
    if not any(w.startswith("rul") or w.startswith("uph") for _, _, w in toks):
        return False

    def last_named_before(limit):
        last, t = [], 0
        while t < limit:
            if toks[t][2] not in ("clause", "clauses"):
                t += 1
                continue
            rows, nxt = v_clause_list(quote, toks, t)
            if rows:
                last = rows
                t = max(nxt, t + 1)
            else:
                t += 1
        return last

    for k, (s, e, w) in enumerate(toks):
        if w not in ("breach", "breaches"):
            continue
        if k + 2 >= n or toks[k + 1][2] != "of":
            continue
        j = k + 2
        if toks[j][2] in ("either", "both"):        # 'no breach of either Clause 7.3 or 7.4'
            j += 1
        if j >= n:
            continue
        if toks[j][2] in ("that", "this") and j + 1 < n and toks[j + 1][2] == "clause":
            # The antecedent: the last clause NAMED before the pronoun -- named,
            # so the number must be introduced by the word Clause. A bare number
            # is not a candidate ('disclosure prior to 31 March the following
            # calendar year ... ruled no breach of this clause' would otherwise
            # resolve to clause 31). Walked with the index advancing past a
            # two-token number, or 'Clause 18.3' would offer '3' after '18.3'.
            named, t = [], 0
            while t < k:
                if toks[t][2] not in ("clause", "clauses"):
                    t += 1
                    continue
                num, nxt = v_clause_number(quote, toks, t + 1)
                if num is None:
                    t += 1
                    continue
                named.append(num)
                t = nxt
            heads = [named[-1]] if named else []
        elif toks[j][2] in ("clause", "clauses"):
            heads, _ = v_clause_list(quote, toks, j)
        else:
            continue
        if clause not in heads:
            continue
        # 'no [further|additional|separate|other] breach of ...' -- the negator
        # is still attached to this head.
        neg = bool(k and (toks[k - 1][2] == "no"
                          or (k >= 2 and toks[k - 1][2] in ("further", "additional",
                                                            "separate", "other")
                              and toks[k - 2][2] == "no")))
        if not neg and polarity == "no_breach":
            # THE CENSURE FRAME, and nothing else, may invert a positive head.
            # Read as a shape rather than a window, because the corpus puts up
            # to 160 characters between the negator and the head: the head must
            # be the object of 'warrant(ed) a ruling of a breach', and a 'did
            # not' must sit before that verb in the same sentence.
            words = [w2 for _, _, w2 in toks]
            p = k - 1
            if p >= 0 and words[p] in ("a", "an", "the"):
                p -= 1
            if p >= 1 and words[p] == "of" and words[p - 1] == "ruling":
                p -= 2
                if p >= 0 and words[p] in ("a", "an"):
                    p -= 1
                if p >= 0 and words[p].startswith("warrant") \
                        and any(words[q] == "not" and q and words[q - 1] == "did"
                                for q in range(p)):
                    neg = True
        if ("no_breach" if neg else "breach") == polarity:
            return True

    # Independent reading of the assurance receipt-recall frames.  These are
    # token relations, not copies of the builder's regexes.
    for k, (_, _, word) in enumerate(toks):
        # "... Clause 9.3 and no breach was ruled": the disposition is
        # clause-less but its explicit antecedent is in this sentence.
        if word == "no" and k + 2 < n and toks[k + 1][2] in ("breach", "breaches") \
                and toks[k + 2][2] in ("was", "were"):
            ruled_at = k + 3
            if ruled_at < n and toks[ruled_at][2] != "ruled":
                ruled_at += 1              # at most one connective adverb
            connector = (k and toks[k - 1][2] == "and")
            if k:
                connector = connector or any(ch in quote[toks[k - 1][1]:toks[k][0]]
                                             for ch in ";,")
            if connector and ruled_at < n and toks[ruled_at][2] == "ruled" \
                    and polarity == "no_breach" and clause in last_named_before(k):
                return True

        # "requirements of Clause 15.5 ... ruled a breach accordingly" (and
        # its negative form).  The last explicit list before the verb is the
        # antecedent; a cross-sentence or bare-number guess is impossible.
        if word == "ruled":
            j, neg = k + 1, False
            if j < n and toks[j][2] == "no":
                neg, j = True, j + 1
            if j < n and toks[j][2] in ("a", "an", "the"):
                j += 1
            if j + 1 < n and toks[j][2] in ("breach", "breaches") \
                    and toks[j + 1][2] == "accordingly" \
                    and ("no_breach" if neg else "breach") == polarity \
                    and clause in last_named_before(k):
                return True

        # The single missing-preposition source form: "; no breach Clause
        # 19.1 ... was ruled".  Punctuation plus the explicit negative head
        # separates it from ordinary "did not breach Clause X" reasoning.
        if word in ("breach", "breaches") and k and toks[k - 1][2] == "no" \
                and k + 1 < n and toks[k + 1][2] in ("clause", "clauses"):
            punct = (k >= 2 and any(ch in quote[toks[k - 2][1]:toks[k - 1][0]]
                                   for ch in ";:"))
            heads, after = v_clause_list(quote, toks, k + 1)
            ruled = any(toks[t][2] in ("was", "were")
                         and any(toks[u][2] == "ruled"
                                 for u in range(t + 1, min(n, t + 3)))
                         for t in range(after, n))
            if punct and ruled and polarity == "no_breach" and clause in heads:
                return True
    return False


V_PANEL_CONTEXT_REFUSALS = {
    # Validator-local sentence keys.  `v_sentences` deliberately returns the
    # sentence body without terminal punctuation, so these are not the same
    # hashes as build.py's independently maintained context-refusal registry.
    # Both sentences occur in AUTH/2589's recap of AUTH/2442 and describe that
    # earlier case's Clause 25 rulings, not a ruling in AUTH/2589.
    ("AUTH/2589/3/13", "80eab20c8c11"):
        "recap of AUTH/2442/10/11, identified in the preceding sentence",
    ("AUTH/2589/3/13", "acd35567f3d1"):
        "second recap ruling from AUTH/2442/10/11",
}
V_PANEL_CONTEXT_REFUSALS_FIRED = set()


def v_panel_polarities(span, own_cases, case_number):
    """[(polarity, clause)] this reading attributes to the PANEL in one
    panel_ruling segment."""
    out = []
    for _, sentence in v_sentences(span):
        if "breach" not in sentence.lower():
            continue
        if re.search(r"\bappeal\s+board\b", sentence, re.I):
            continue
        cited = {v_case_number(m) for m in V_CASE_NUM_RE.finditer(sentence)}
        if cited - set(own_cases):
            continue
        sentence_key = hashlib.sha256(
            " ".join(sentence.split()).encode("utf-8")).hexdigest()[:12]
        refusal = (case_number, sentence_key)
        if refusal in V_PANEL_CONTEXT_REFUSALS:
            V_PANEL_CONTEXT_REFUSALS_FIRED.add(refusal)
            continue
        for row in v_passive_statements(sentence):
            if row not in out:
                out.append(row)
    return out


def check_v_panel_context_refusals(failures):
    dead = sorted(
        set(V_PANEL_CONTEXT_REFUSALS) - V_PANEL_CONTEXT_REFUSALS_FIRED)
    if dead:
        failures.append((
            "(corpus)", "verdict", "V_PANEL_CONTEXT_REFUSALS",
            "reviewed recap sentences were never matched exactly (dead entries): "
            f"{dead}"))


# R26's sixth check, also implemented rather than copied: the outcome-stating
# HEADLINE the publisher puts above the first body section. l1/derive's
# `banner_headings` rule is three literal strings (NO BREACH / BREACH OF THE
# CODE / BREACH OF CLAUSE) and misses the other word orders -- 'Breach of
# undertaking Clause 2', 'VPRIV press release breach Clause 2' -- which the
# report_abstract rendition then quotes as its opening line.
V_HEADLINE_BREACH_RE = re.compile(r"\bbreach(?:es|ed)?\b", re.I)
V_HEADLINE_CLAUSE_RE = re.compile(r"\bclauses?\s*\d", re.I)
V_HEADLINE_BODY = ("COMPLAINT", "RESPONSE", "PANEL_RULING")
# Wave C. The second outcome-bearing headline the same check refuses: the
# publisher's 'CASE AUTH/2353/8/10 VOLUNTARY ADMISSION BY NAPP' names a
# procedure SPEC §6b forbids showing and that is 92.9% of the label on the
# cases carrying it. The POLICY is re-typed here for the same reason
# ATTEST_CHECKS and the dropped sanction needles are: this validator has to
# know what the check refuses to recompute it at all. What it must not share is
# the READING, and it does not -- the word count is taken from L1's own
# `word_count` receipt independently, and the two bounds are re-derived from
# the measurement in the builder's comment rather than imported.
V_HEADLINE_PROCEDURE_RE = re.compile(r"\bvoluntary\s+admission\b", re.I)
V_HEADLINE_MAX_WORDS = 15   # above it, the 'headline' is a prose paragraph
V_HEADLINE_MIN_WORDS = 4    # below it, the needle is ordinary case vocabulary


def v_outcome_headings(rec, der):
    norm = {(s["pane"], s["index"]): s["heading_normalised"] for s in der["sections"]}
    found = set()
    for pane in ("report", "summary"):
        stop = None
        for sec in rec["sections"]:
            if sec["pane"] != pane:
                continue
            if stop is None and norm.get((pane, sec["index"])) in V_HEADLINE_BODY:
                stop = sec["index"]
            if stop is not None and sec["index"] >= stop:
                continue
            head = sec["heading_text"] or ""
            evidence = sec["heading_evidence"] or {}
            if evidence.get("has_terminal_punctuation", True):
                continue
            n_words = evidence.get("word_count")
            if n_words is None:
                n_words = len(head.split())
            hit = bool(V_HEADLINE_BREACH_RE.search(head)
                       and V_HEADLINE_CLAUSE_RE.search(head))
            if not hit and V_HEADLINE_PROCEDURE_RE.search(head):
                hit = V_HEADLINE_MIN_WORDS <= n_words <= V_HEADLINE_MAX_WORDS
            if hit:
                text = flat(head)
                if text:
                    found.add(text)
    return found
V_TAG_LABEL_RE = re.compile(r'<div[^>]*class="[^"]*tag-label[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
V_TAG_RE = re.compile(r"<[^>]+>")
V_MIN_TABLE_TEXT = 12
V_PDF_MARKERS = ("COMPLAINT", "RESPONSE", "PANEL RULING", "APPEAL")

ATTEST_CHECKS = (
    "no_ruling_language", "no_outcome_banner", "no_outcome_table",
    "outside_abstract", "no_sanctions_text", "no_outcome_heading",
)


def flat(s):
    """Whitespace-collapsed, case-folded -- the one form both sides of a
    containment test are reduced to."""
    return " ".join((s or "").split()).casefold()


# DEFECTS R31. The needles the builder's distinctiveness floor DROPS, re-typed
# here rather than imported. What must not be shared with the layer under audit
# is the READING -- the parse of the chips out of the HTML, which is re-done
# below -- not the POLICY, which this validator has to know to re-derive the
# same attest at all (`ATTEST_CHECKS` above is re-typed for the same reason).
# One entry today: 'advertisement' is the single generic word 253 pages carry as
# a chip and 407 of the 1,649 pages WITHOUT the chip use in ordinary prose.
V_SANCTION_NEEDLES_DROPPED = {"advertisement"}


def sanction_chip_labels(info_holder):
    """The rendered additional-sanction chips, parsed here rather than read out
    of the case object: reading sanctions.additional back would make the
    builder's own output the witness for a check on the builder's output."""
    out = []
    for item in info_holder:
        if item["label"] != "Additional sanctions":
            continue
        for m in V_TAG_LABEL_RE.finditer(item["value_html"] or ""):
            label = flat(html_mod.unescape(V_TAG_RE.sub(" ", m.group(1))))
            if label and label not in V_SANCTION_NEEDLES_DROPPED:
                out.append(label)
        break
    return out


def pdf_abstract_end(flow):
    """Where a substituted PDF's abstract stops: the first of the four literal
    markers, searched IN ORDER (so 'APPEAL' cannot be found before 'PANEL
    RULING'). No marker at all -- the modern abridged reports -- means the whole
    flow is abstract."""
    first, pos = None, 0
    for marker in V_PDF_MARKERS:
        m = re.compile(r"\b" + re.escape(marker) + r"\b").search(flow, pos)
        if m:
            if first is None:
                first = m.start()
            pos = m.end()
    return first if first is not None else len(flow)


def recompute_attest(span, pane, start, end, ctx, kind, file=None):
    hay = flat(span)
    abstract = ctx["abstract"].get(pane) if pane in ("report", "flow") else None
    if abstract is None or kind in ("abstract", "abstract_rendition"):
        outside = True
    else:
        a0, a1 = abstract
        outside = not (start < a1 and a0 < end)
    checks = {
        "no_ruling_language": not v_ruling_language(span, ctx["own_cases"], file),
        "no_outcome_banner": not any(b in hay for b in ctx["banners"]),
        "no_outcome_table": not any(t in hay for t in ctx["tables"]),
        "outside_abstract": outside,
        "no_sanctions_text": not any(c in hay for c in ctx["chips"]),
        "no_outcome_heading": not any(h in hay for h in ctx["outcome_headings"]),
    }
    if kind == "response":
        v_apply_response_attest_false_positive(span, file, checks)
    return checks


def check_segments(refs, panes, ctx, failures):
    """Re-slice and re-attest every segment that points at one file."""
    for name, idx, seg in refs:
        ref = seg["ref"]
        text = panes.get(ref["pane"])
        where = f"segments[{idx}] ({seg['kind']})"
        if text is None:
            failures.append((name, "segment", where, f"pane {ref['pane']!r} does not exist in the source"))
            continue
        if not (0 <= ref["char_start"] <= ref["char_end"] <= len(text)):
            failures.append((name, "segment", where,
                             f"span {ref['char_start']}-{ref['char_end']} is outside the "
                             f"{len(text)}-char {ref['pane']} pane"))
            continue
        span = text[ref["char_start"]:ref["char_end"]]
        if len(span) != ref["text_length"]:
            failures.append((name, "segment", where,
                             f"text_length {ref['text_length']} but the slice is {len(span)}"))
        got = hashlib.sha256(span.encode("utf-8")).hexdigest()
        if got != ref["text_sha256"]:
            failures.append((name, "segment", where,
                             f"text_sha256 {ref['text_sha256'][:12]}... but the slice hashes to {got[:12]}..."))
        mine = recompute_attest(span, ref["pane"], ref["char_start"], ref["char_end"], ctx,
                                seg["kind"], ref.get("file"))
        theirs = seg["leakage_attest"]["checks"]
        for check in ATTEST_CHECKS:
            if mine[check] != theirs.get(check):
                failures.append((name, "attest", where,
                                 f"{check}: build says {theirs.get(check)}, recomputation says {mine[check]}"))
        if seg["leakage_attest"]["clean"] != all(mine.values()):
            failures.append((name, "attest", where,
                             f"clean={seg['leakage_attest']['clean']} but the recomputed checks give "
                             f"{all(mine.values())}"))

# The canonical values, by path. Each must carry receipts.
CANONICALS = [
    ("case_number", lambda c: c["case_number"]),
    ("title", lambda c: c["title"]),
    ("subject", lambda c: c["subject"]),
    ("parties.respondent", lambda c: c["parties"]["respondent"]),
    ("parties.complainant", lambda c: c["parties"]["complainant"]),
    ("code_year", lambda c: c["code_year"]),
    ("dates.received", lambda c: c["dates"]["received"]),
    ("dates.completed", lambda c: c["dates"]["completed"]),
    ("appeal", lambda c: c["appeal"]),
    ("sanctions", lambda c: c["sanctions"]),
]

# A canonical is "filled" when it asserts something. The two group objects do
# not have a single `value`, so they say so their own way.
FILLED = {
    "parties.complainant": lambda o: o["category"] is not None,
    "appeal": lambda o: o["appealed"] is not None,
    "sanctions": lambda o: True,
}


def key_signature(obj, prefix=""):
    """Recursive key set for the fixed-shape parts of a case."""
    keys = set()
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        keys.add(path)
        # Objects only; arrays hold repeated items whose count legitimately
        # varies between cases.
        if isinstance(v, dict):
            keys |= key_signature(v, path)
    return keys


def load_rules():
    """The rule registry, from the builder that writes it. Importing it is the
    point: a basis is legal exactly when a rule of that name exists in code.
    This is the ONLY thing taken from build.py -- see the module docstring on
    why the attest is re-implemented instead."""
    sys.path.insert(0, str(ROOT / "l2"))
    import build

    return dict(build.RULES), tuple(build.RETIRED_VERDICT_RULES)


def check_panel_prose(refs, panes, ctx, by_name, failures):
    """DEFECTS R28 / N1. Re-read the passive verdict frame out of every
    `panel_ruling` segment and hold the published receipts to it.

    One direction only, for the reason `v_passive_statements` states: what the
    builder reads with its other frames is not this reading's business, but
    anything THIS reading finds must already be on the row. A clause with no
    verdict row is skipped -- SPEC §5 is that prose attributes rows and never
    creates them, so a ruling on a clause the outcome lists never name has
    nothing to attach to and is not a receipts defect.
    """
    for name, i, seg in refs:
        if seg["kind"] != "panel_ruling":
            continue
        ref = seg["ref"]
        span = panes[ref["pane"]][ref["char_start"]:ref["char_end"]]
        case = by_name.get(name)
        if case is None:
            continue
        rows = {v["clause"]: v for v in case["verdicts"]}
        for polarity, clause in v_panel_polarities(span, ctx["own_cases"], name):
            v = rows.get(clause)
            if v is None:
                continue
            flag = "prose_panel_breach" if polarity == "breach" else "prose_panel_no_breach"
            if not v["sources"][flag]:
                failures.append((name, "verdict", f"segments[{i}] -> verdicts[{clause}]",
                                 f"recomputation reads a PANEL {polarity} for clause {clause} "
                                 f"in this panel_ruling segment; the build's {flag} is false"))


def check_v_prose_only_verdicts(rec, keys, by_name, failures, seen):
    """Independently check the closed silent-clause audit against raw L1."""
    report = rec["panes"]["report"]["text"]
    got_sha = hashlib.sha256(report.encode("utf-8")).hexdigest()
    slot_keys = (
        "meta_clause_breach", "meta_clause_no_breach",
        "info_breach_clauses", "info_no_breach_clauses",
        "chip_breach", "chip_no_breach",
    )
    for key in keys:
        name, clause = key
        review = V_PROSE_ONLY_VERDICT_READ[key]
        seen.add(key)
        where = f"PROSE_ONLY_VERDICT_READ[{clause}]"
        if got_sha != review["source_sha256"]:
            failures.append((name, "prose-only verdict", where,
                             f"raw report sha is {got_sha}, reviewed sha is "
                             f"{review['source_sha256']}"))
        for quote in review["quotes"]:
            if quote not in report:
                failures.append((name, "prose-only verdict", where,
                                 f"review quote is not verbatim in raw report: {quote[:120]!r}"))
        case = by_name.get(name)
        if case is None:
            failures.append((name, "prose-only verdict", where,
                             "review names no published case"))
            continue
        rows = [v for v in case["verdicts"] if v["clause"] == clause]
        if review["decision"] == "refuse":
            if rows:
                failures.append((name, "prose-only verdict", where,
                                 "review refused this foreign/non-ruling prose, but a verdict "
                                 "row exists"))
            continue
        if len(rows) != 1:
            failures.append((name, "prose-only verdict", where,
                             f"accepted review requires exactly one verdict row, got {len(rows)}"))
            continue
        row = rows[0]
        if any(row["sources"][field] for field in slot_keys):
            failures.append((name, "prose-only verdict", where,
                             "reviewed prose-only row is now stated by an outcome slot/chip; "
                             "the exceptional entry is stale"))
        expected = {
            "panel": review["panel"],
            "appeal_board": review["appeal_board"],
            "final": review["final"],
            "code_year": review["code_year"],
            "code_year_basis": "year_prose_only_reviewed",
            "dual_ruling": review["dual_ruling"],
            "dual_ruling_appeal_board": review["dual_ruling_appeal_board"],
            "flipped_on_appeal": False,
            "basis": "verdict_prose_only_reviewed",
            "occurrence": 0,
        }
        got = {field: row[field] for field in expected}
        if got != expected:
            failures.append((name, "prose-only verdict", where,
                             f"expected {expected}, published {got}"))


def audit_segments(cases, failures, adj_ids=frozenset()):
    """SPEC §7.4. Re-slice every segment out of L1 and re-run the attest.

    Streams records.jsonl in lockstep with derived.jsonl and checks each file's
    segments as it passes, so the 202 MB never has to be resident. The 13
    substituted PDF flows are checked afterwards, using the attest context
    (banners, outcome table, sanction chips) gathered from their HTML page --
    the flow replaces the report, not the page.
    """
    by_name = {c["case_number"]["value"]: c for c in cases}
    pdf_flows, pdf_owner = {}, {}
    if L1_PDF.exists():
        with L1_PDF.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    p = json.loads(line)
                    pdf_flows[p["file"]] = p["flow_text"]
                    pdf_owner[p["file"]] = p["html_file"]

    by_file = {}
    ruling_cases = {}
    for case in cases:
        name = case["case_number"]["value"]
        for i, seg in enumerate(case["segments"]):
            by_file.setdefault(seg["ref"]["file"], []).append((name, i, seg))
        ruling_cases.setdefault(case["source_files"][0], []).append(name)

    review_by_file = {}
    review_seen = set()
    for key in V_PROSE_ONLY_VERDICT_READ:
        case = by_name.get(key[0])
        if case is None:
            failures.append((key[0], "prose-only verdict", "registry",
                             "review names no published case"))
            continue
        review_by_file.setdefault(case["source_files"][0], []).append(key)

    held_ctx = {}
    checked = 0
    with L1_RECORDS.open(encoding="utf-8") as rf, L1_DERIVED.open(encoding="utf-8") as df:
        for rline, dline in zip(rf, df):
            if not rline.strip():
                continue
            rec = json.loads(rline)
            der = json.loads(dline)
            wanted = by_file.get(rec["file"])
            owns_pdf = rec["file"] in pdf_owner.values()
            prose_reviews = review_by_file.get(rec["file"])
            if not wanted and not owns_pdf and not prose_reviews:
                continue
            tables = set()
            for row in rec["outcomes"]["report_table_rows"]:
                for cell in (row.get("verdict_text"), row.get("description_text")):
                    t = flat(cell)
                    if len(t) >= V_MIN_TABLE_TEXT:
                        tables.add(t)
            boundary = der["abstract_boundary"]
            ctx = {
                "banners": {b for b in (flat(x) for x in der["banner_headings"]) if b},
                "tables": tables,
                "chips": set(sanction_chip_labels(rec["info_holder"])),
                "outcome_headings": v_outcome_headings(rec, der),
                # From L1's identity, the same source the builder reads -- the
                # thing that must not be shared is the READING, not the bytes.
                "own_cases": {v_case_number(m) for name in
                              (rec["identity"]["filename_case_numbers"] or [])
                              for m in V_CASE_NUM_RE.finditer(name)},
                "abstract": {},
            }
            if boundary["is_measured"] and isinstance(boundary["offset"], int):
                ctx["abstract"]["report"] = (0, boundary["offset"])
            panes = {"summary": rec["panes"]["summary"]["text"],
                     "report": rec["panes"]["report"]["text"]}
            if prose_reviews:
                check_v_prose_only_verdicts(
                    rec, prose_reviews, by_name, failures, review_seen)
            if owns_pdf:
                held_ctx[rec["file"]] = ctx
            if wanted:
                # A substituted case keeps its HTML summary rendition, so its
                # segments legitimately span both the page and the PDF.
                html_segs = [s for s in wanted if s[2]["ref"]["pane"] != "flow"]
                check_segments(html_segs, panes, ctx, failures)
                check_panel_prose(html_segs, panes, ctx, by_name, failures)
                checked += len(html_segs)
            # R28 stage 1. The rulings receipts are re-sliced from the same
            # streamed pane, in the same pass, for the same reason: nothing has
            # to be resident. A substituted case's rulings sit in the PDF flow,
            # which is already held, so both panes are offered and the entry's
            # own `file`/`pane` decides which it is checked against.
            for case in ruling_cases.get(rec["file"], ()):
                avail = {rec["file"]: panes}
                for pf, owner in pdf_owner.items():
                    if owner == rec["file"]:
                        avail[pf] = {"flow": pdf_flows[pf]}
                audit_rulings(by_name[case], avail, failures, adj_ids)

    for pdf_file, segs in sorted(by_file.items()):
        if pdf_file not in pdf_flows:
            continue
        flow = pdf_flows[pdf_file]
        ctx = dict(held_ctx.get(pdf_owner[pdf_file],
                                {"banners": set(), "tables": set(), "chips": set(),
                                 "outcome_headings": set(), "own_cases": set()}))
        ctx["abstract"] = {"flow": (0, pdf_abstract_end(flow))}
        check_segments(segs, {"flow": flow}, ctx, failures)
        check_panel_prose(segs, {"flow": flow}, ctx, by_name, failures)
        checked += len(segs)

    unseen = sorted(f for f in by_file if f not in pdf_flows and f not in _seen_html(cases))
    for f in unseen:
        failures.append(("(corpus)", "segment", "ref.file", f"{f} is neither an L1 record nor a PDF record"))
    # Every segment in the corpus has now been re-attested, so a declared false
    # ruling-language match that never fired is a stale row, not a not-yet.
    check_v_ruling_false_matches(failures)
    check_v_response_attest_false_positives(failures)
    check_v_panel_context_refusals(failures)
    dead_reviews = sorted(set(V_PROSE_ONLY_VERDICT_READ) - review_seen)
    if dead_reviews:
        failures.append(("(corpus)", "prose-only verdict", "registry",
                         f"defined but never matched a raw L1 report (dead entries): "
                         f"{dead_reviews}"))
    return checked


def _seen_html(cases):
    return {c["source_files"][0] for c in cases}


def audit_verdicts(case, failures, retired, adj_ids):
    """SPEC §5 invariants, plus §6c's rule that `final` is never null.

    l2.2 (DEFECTS D3) replaces one invariant and adds three. The one removed:
    'an Appeal Board ruling with no Panel ruling under it' was only true while
    `panel` could be back-filled -- now that each body is read from its own
    prose, the Board demonstrably ruling on a clause whose Panel ruling the
    report does not spell out is an ordinary, honest state.

    The three added are the ones that keep the fix in place:
      * no row may carry a retired l2.1 basis (the back-fill rules, by name);
      * a `dual_ruling` row may never carry a Panel ruling -- the whole point is
        that no single one exists;
      * an attribution on an APPEALED case must be backed by prose receipts.
        This is the invariant that would have caught the original defect: every
        one of the 75+ mislabelled items had panel set with both prose flags
        false.
    """
    name = case["case_number"]["value"]
    appealed = case["appeal"]["appealed"]
    seen = set()
    for i, v in enumerate(case["verdicts"]):
        key = (v["clause"], v["code_year"], v["occurrence"])
        if key in seen:
            failures.append((name, "verdict", f"verdicts[{i}]",
                             f"duplicate key {key} -- (clause, code_year, occurrence) must be unique"))
        seen.add(key)
        if v["final"] not in ("breach", "no_breach"):
            failures.append((name, "verdict", f"verdicts[{i}]", "final is null or not a polarity"))
        expect = bool(appealed and v["panel"] is not None
                      and v["appeal_board"] is not None and v["panel"] != v["appeal_board"])
        if v["flipped_on_appeal"] != expect:
            failures.append((name, "verdict", f"verdicts[{i}]",
                             f"flipped_on_appeal={v['flipped_on_appeal']} but panel={v['panel']}, "
                             f"appeal_board={v['appeal_board']}, appealed={appealed}"))
        if appealed is False and v["appeal_board"] is not None:
            failures.append((name, "verdict", f"verdicts[{i}]",
                             "an unappealed case carries an Appeal Board ruling"))
        if v["basis"] in retired:
            failures.append((name, "verdict", f"verdicts[{i}]",
                             f"basis {v['basis']!r} was retired in l2.2: it attributed a ruling to a "
                             f"body from something other than that body's own prose (DEFECTS D3)"))
        if v["dual_ruling"] and v["panel"] is not None:
            failures.append((name, "verdict", f"verdicts[{i}]",
                             f"dual_ruling row carries panel={v['panel']!r}; a clause ruled both ways "
                             f"has no single Panel ruling"))
        src = v["sources"]
        # A reviewed hand decision, receipts audited above. `attribution_basis`
        # counts for the same reason `basis` does and is a separate field
        # because the two answer different questions -- which rule or reading
        # produced the row, and which reading corrected the body attribution on
        # it (Q3 / DEFECTS R6). The three prose-receipt checks below all ask
        # "does this body's own prose say so", and the whole point of an
        # attribution adjudication is that on ten rows it does not, in a form
        # any reader can attribute.
        if v["basis"] in adj_ids or v.get("attribution_basis") in adj_ids:
            continue
        if appealed and v["panel"] is not None and not (
                src["prose_panel_breach"] or src["prose_panel_no_breach"]):
            failures.append((name, "verdict", f"verdicts[{i}]",
                             "an appealed case attributes a Panel ruling with no panel-prose receipt"))
        if v["appeal_board"] is not None and not (
                src["prose_appeal_board_breach"] or src["prose_appeal_board_no_breach"]):
            failures.append((name, "verdict", f"verdicts[{i}]",
                             "an Appeal Board ruling with no appeal-prose receipt"))
        if v["panel"] is not None and appealed and (
                (v["panel"] == "breach" and not src["prose_panel_breach"])
                or (v["panel"] == "no_breach" and not src["prose_panel_no_breach"])):
            failures.append((name, "verdict", f"verdicts[{i}]",
                             f"panel={v['panel']!r} but the panel prose receipts state the other polarity"))


def audit_rulings(case, panes, failures, adj_ids=frozenset()):
    """R28 stage 1: every `rulings` entry re-slices, and re-reads, on its own.

    Four things, and the first two are the receipt:

      1. RE-SLICE. The quote must still cut out of the pane it names, at the
         offsets it names. This is what makes an entry evidence rather than a
         copy: if L1 moves underneath, the receipt fails loudly instead of
         quietly describing text that no longer exists. The regard is re-sliced
         the same way.
      2. RE-READ, independently (`v_ruling_statement`). The sentence must
         actually state that polarity on that clause under a reading that
         shares no code with the builder's frames.
      3. The reading is done against the ROW's clause, not against anything the
         entry carries, so an entry filed under the wrong row fails (2) rather
         than agreeing with itself.
      4. The two dual flags agree with the receipts they claim: an appeal-axis
         dual must show both polarities attributed to the Board, and must carry
         `appeal_board: null`. (The panel axis is only checked for the rows the
         PATTERN decided -- `verdict_dual_panel_prose` -- because the other two
         routes to `dual_ruling`, both outcome lists on an unappealed case and
         an adjudication, are not prose claims at all.)
    """
    name = case["case_number"]["value"]
    # A matter heading can open a party segment at exactly its first byte, but
    # a numbered heading strictly inside complaint/response prose is that
    # party's own organisation of its submission.  It cannot be a receipt for
    # what a later adjudicator ruling is "in regard to".  This independently
    # enforces the segment-containment repair in build.py and would have caught
    # all 37 all-regards-in-response cases found by the assurance pass.
    party_spans = {}
    for seg in case.get("segments") or []:
        if seg.get("kind") not in ("complaint", "response"):
            continue
        sr = seg["ref"]
        party_spans.setdefault((sr["file"], sr["pane"]), []).append(
            (sr["char_start"], sr["char_end"]))
    context_refusals = {
        ("AUTH/2589/3/13", "c9398635c0c2"),
        ("AUTH/2589/3/13", "3cdfc60f3ae0"),
        ("AUTH/2593/4/13", "a13ff44ceda5"),
        ("AUTH/2593/4/13", "41046e1bacd5"),
        ("AUTH/2960/6/17", "bb08f63974b7"),
        ("AUTH/3615/3/22", "6fe41f45a38b"),
        ("AUTH/2739/11/14", "46d6b238664c"),
    }
    for i, v in enumerate(case["verdicts"]):
        seen = {"panel": set(), "appeal_board": set()}
        for j, r in enumerate(v.get("rulings") or []):
            where = f"verdicts[{i}].rulings[{j}]"
            text = (panes.get(r["file"]) or {}).get(r["pane"])
            if text is None:
                failures.append((name, "ruling", where,
                                 f"no {r['pane']} pane for {r['file']}"))
                continue
            got = text[r["char_start"]:r["char_end"]]
            if got != r["quote"]:
                failures.append((name, "ruling", where,
                                 f"quote does not re-slice: {r['pane']}"
                                 f"[{r['char_start']}:{r['char_end']}] reads {got[:60]!r}, "
                                 f"the entry says {r['quote'][:60]!r}"))
                continue
            quote_key = hashlib.sha256(" ".join(r["quote"].split()).encode("utf-8")).hexdigest()[:12]
            if (name, quote_key) in context_refusals:
                failures.append((name, "ruling", where,
                                 "receipt survived an independently reviewed recap/submission "
                                 f"refusal ({quote_key})"))
                continue
            if not v_ruling_statement(r["quote"], v["clause"], r["polarity"]):
                failures.append((name, "ruling", where,
                                 f"the quote does not state {r['polarity']} on clause "
                                 f"{v['clause']} under an independent reading: {r['quote'][:120]!r}"))
            ref = r["regard_ref"]
            if (ref is None) != (r["regard"] is None):
                failures.append((name, "ruling", where, "regard and regard_ref disagree about null"))
            elif ref is not None:
                cut = text[ref["char_start"]:ref["char_end"]]
                if cut != r["regard"]:
                    failures.append((name, "ruling", where,
                                     f"regard does not re-slice: {cut[:60]!r} vs {r['regard'][:60]!r}"))
                if any(start < ref["char_start"] and ref["char_end"] <= end
                       for start, end in party_spans.get((r["file"], r["pane"]), ())):
                    failures.append((name, "ruling", where,
                                     "regard_ref points strictly inside a complaint/response "
                                     "segment; it is a party subheading, not a matter receipt"))
            seen[r["body"]].add(r["polarity"])
        if v["dual_ruling_appeal_board"]:
            if v["appeal_board"] is not None:
                failures.append((name, "ruling", f"verdicts[{i}]",
                                 f"dual_ruling_appeal_board with appeal_board="
                                 f"{v['appeal_board']!r}; a clause the Board ruled both ways has "
                                 f"no single Board ruling"))
            # A REVIEWED attribution (`verdicts[<clause>].attribution`) is the
            # one route to this flag that is not a prose claim, so it is the
            # one that cannot show prose receipts -- AUTH/1902+1903's Board
            # states neither half in a form any reader can attribute ('The
            # Appeal Board upheld the Panel's ruling of a breach of the Code';
            # 'there was no breach of the Code in relation to arrangements for
            # the TOPCAT service'). Demanding receipts here would forbid the
            # decision the entry exists to record. Same exemption the
            # attribution audit above already makes for `basis in adj_ids`.
            reviewed = v["basis"] in adj_ids or v.get("attribution_basis") in adj_ids
            if not reviewed and seen["appeal_board"] != {"breach", "no_breach"}:
                failures.append((name, "ruling", f"verdicts[{i}]",
                                 f"dual_ruling_appeal_board but the receipts show "
                                 f"{sorted(seen['appeal_board']) or 'no'} Appeal Board ruling(s)"))
        if v["basis"] == "verdict_dual_panel_prose" and seen["panel"] != {"breach", "no_breach"}:
            failures.append((name, "ruling", f"verdicts[{i}]",
                             f"basis says the Panel's prose states both polarities but the "
                             f"receipts show {sorted(seen['panel']) or 'none'}"))


def audit_renditions(case, failures):
    """A rendition is an INDEX into segments, and it must land on a segment of
    the kind that slot names."""
    name = case["case_number"]["value"]
    expect = {"summary": ("summary_rendition",), "report_abstract": ("abstract_rendition",),
              "pdf_flow": ("abstract",)}
    for slot, kinds in expect.items():
        idx = case["renditions"][slot]
        if idx is None:
            continue
        if not 0 <= idx < len(case["segments"]):
            failures.append((name, "rendition", slot,
                             f"index {idx} is outside segments[0:{len(case['segments'])}]"))
            continue
        seg = case["segments"][idx]
        if seg["kind"] not in kinds:
            failures.append((name, "rendition", slot,
                             f"index {idx} points at a {seg['kind']} segment, expected {kinds}"))
        if slot == "pdf_flow" and seg["source"] != "pdf":
            failures.append((name, "rendition", slot, "pdf_flow points at an html segment"))
    if case["renditions"]["pdf_flow"] is None and case["quality"]["pdf_substituted"] \
            and any(s["source"] == "pdf" for s in case["segments"]):
        failures.append((name, "rendition", "pdf_flow",
                         "the case has PDF segments but no pdf_flow rendition"))


def l1_files():
    names = set()
    with L1_RECORDS.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                names.add(json.loads(line)["file"])
    return names


def main():
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    rules, retired = load_rules()
    still_registered = [r for r in retired if r in rules]
    adjudications = json.loads(ADJUDICATIONS.read_text(encoding="utf-8")) if ADJUDICATIONS.exists() else []
    adj_ids = {a["id"] for a in adjudications}

    failures = []
    if still_registered:
        failures.append(("(corpus)", "receipts", "RULES",
                         f"retired l2.1 verdict rule(s) are registered again: {still_registered}"))
    signatures = {}
    cases = []
    n = 0

    with CASES.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            n += 1
            name = case.get("case_number", {}).get("value") or f"line {lineno}"
            cases.append(case)

            for err in validator.iter_errors(case):
                failures.append((name, "schema", "/".join(str(p) for p in err.absolute_path) or "(root)",
                                 err.message))

            signatures.setdefault(frozenset(key_signature(case)), []).append(name)

            # --- receipts audit ------------------------------------------
            for path, get in CANONICALS:
                obj = get(case)
                basis = obj["basis"]
                if basis not in rules and basis not in adj_ids:
                    failures.append((name, "receipts", path,
                                     f"basis '{basis}' is neither a registered rule nor an adjudication id"))
                filled = FILLED.get(path, lambda o: o.get("value") is not None)(obj)
                if filled and not obj["sources"]:
                    failures.append((name, "receipts", path, "filled canonical with empty sources"))
                if filled and all(v is None for v in obj["sources"].values()):
                    failures.append((name, "receipts", path,
                                     "filled canonical whose every source slot is null -- a value from nowhere"))

            # --- verdict receipts ----------------------------------------
            for i, v in enumerate(case["verdicts"]):
                if v["basis"] not in rules and v["basis"] not in adj_ids:
                    failures.append((name, "receipts", f"verdicts[{i}]",
                                     f"basis '{v['basis']}' is neither a registered rule nor an "
                                     f"adjudication id"))
                if not v["sources"]:
                    failures.append((name, "receipts", f"verdicts[{i}]", "verdict row with no receipts"))
                elif not any(v["sources"][k] for k in (
                        "meta_clause_breach", "meta_clause_no_breach",
                        "info_breach_clauses", "info_no_breach_clauses",
                        "chip_breach", "chip_no_breach")):
                    reviewed = V_PROSE_ONLY_VERDICT_READ.get((name, v["clause"]))
                    if reviewed is None or reviewed["decision"] != "accept" \
                            or v["basis"] != "verdict_prose_only_reviewed":
                        failures.append((name, "receipts", f"verdicts[{i}]",
                                         "no clause list states this clause and no independent "
                                         "accepted prose-only review licenses the row -- a verdict "
                                         "from nowhere"))

            audit_verdicts(case, failures, retired, adj_ids)
            audit_renditions(case, failures)

            # --- internal consistency ------------------------------------
            if case["case_number"]["value"] in case["sibling_cases"]:
                failures.append((name, "invariant", "sibling_cases", "a case lists itself as its own sibling"))
            if case["sibling_cases"] != sorted(set(case["sibling_cases"]), key=case_sort):
                failures.append((name, "invariant", "sibling_cases", "not in canonical order, or duplicated"))
            # The case-number year and the received year agree on 1,982 cases and
            # differ by exactly one on 22, in BOTH directions -- a complaint
            # received on 22 December 2017 is numbered AUTH/3008/1/18, and
            # AUTH/2478/2/11 was received 7 February 2012 (all three date slots
            # agree, so it is the numbering that lags, not the date). Anything
            # further apart means era and dates came from different cases.
            era, received = case["quality"]["era"], case["dates"]["received"]["value"]
            if era is not None and received is not None and abs(int(received[:4]) - era) > 1:
                failures.append((name, "invariant", "quality.era",
                                 f"era {era} is more than a year from the received year {received[:4]}"))
            for i, seg in enumerate(case["segments"]):
                r = seg["ref"]
                if r["char_end"] < r["char_start"]:
                    failures.append((name, "invariant", f"segments[{i}].ref", "char_end < char_start"))
                if (seg["source"] == "pdf") != (r["pane"] == "flow"):
                    failures.append((name, "invariant", f"segments[{i}]",
                                     f"source {seg['source']!r} does not match pane {r['pane']!r}"))
                if r["pane"] != "flow" and r["file"] != case["source_files"][0]:
                    failures.append((name, "invariant", f"segments[{i}].ref",
                                     f"html segment points at {r['file']}, not this case's source file"))

    print(f"cases validated : {n}")
    print(f"distinct key signatures across all cases : {len(signatures)}")
    if len(signatures) != 1:
        ref = max(signatures.items(), key=lambda kv: len(kv[1]))[0]
        for sig, names in signatures.items():
            if sig == ref:
                continue
            print(f"    {len(names)} case(s), e.g. {names[:3]}")
            print(f"      missing vs majority: {sorted(ref - sig)[:10]}")
            print(f"      extra   vs majority: {sorted(sig - ref)[:10]}")
        failures.append(("(corpus)", "invariant", "key_signature", "cases do not share one key set"))

    # --- reconciliation ---------------------------------------------------
    numbers = [c["case_number"]["value"] for c in cases]
    if len(set(numbers)) != len(numbers):
        dupes = sorted({x for x in numbers if numbers.count(x) > 1})[:5]
        failures.append(("(corpus)", "reconciliation", "case_number", f"duplicated: {dupes}"))
    if len(cases) < 1902:
        failures.append(("(corpus)", "reconciliation", "count",
                         f"{len(cases)} cases is fewer than the 1902 L1 files"))
    if numbers != sorted(numbers, key=case_sort):
        failures.append(("(corpus)", "reconciliation", "order", "cases are not in canonical case-number order"))

    by_number = {c["case_number"]["value"]: c for c in cases}
    for c in cases:
        me = c["case_number"]["value"]
        for sib in c["sibling_cases"]:
            other = by_number.get(sib)
            if other is None:
                failures.append((me, "reconciliation", "sibling_cases", f"{sib} has no case object"))
            elif me not in other["sibling_cases"]:
                failures.append((me, "reconciliation", "sibling_cases", f"{sib} does not list {me} back"))
            elif other["source_files"] != c["source_files"]:
                failures.append((me, "reconciliation", "sibling_cases",
                                 f"{sib} is a sibling but does not share the source file"))

    covered = {f for c in cases for f in c["source_files"]}
    if L1_RECORDS.exists():
        missing = sorted(l1_files() - covered)
        if missing:
            failures.append(("(corpus)", "reconciliation", "coverage",
                             f"{len(missing)} L1 file(s) yielded no case, e.g. {missing[:3]}"))
        print(f"L1 files accounted for : {len(covered)}")

    # --- segments re-sliced and the attest recomputed (SPEC §7.4) ---------
    if L1_RECORDS.exists() and L1_DERIVED.exists():
        n_seg = audit_segments(cases, failures, adj_ids)
        print(f"segments re-sliced and re-attested : {n_seg}")

    used = set()
    for c in cases:
        for _, get in CANONICALS:
            b = get(c)["basis"]
            if b in adj_ids:
                used.add(b)
        for v in c["verdicts"]:
            if v["basis"] in adj_ids:
                used.add(v["basis"])
            # Q3. Same dead-fix detection for the attribution decision, which
            # lands in its own field and would otherwise read as never used.
            if v.get("attribution_basis") in adj_ids:
                used.add(v["attribution_basis"])
            # R20 residue: a reviewed per-clause Code YEAR decision lands in
            # `code_year_basis`, not `basis`, so a dead-fix sweep that reads
            # only `basis` would call every one of them unused.
            if v.get("code_year_basis") in adj_ids:
                used.add(v["code_year_basis"])
    # N2. A clause-slot correction leaves NO basis anywhere on the row -- it
    # repairs the outcome slot before the row is built, so the row that comes
    # out is an ordinary `verdict_unappealed` one about a different clause.
    # Its trace is L2's own artefact, which is also where it is checked: the
    # corrected clause must have a row and the deleted one must not, so a
    # correction that silently failed to apply is a FAILURE here rather than a
    # dead fix.
    by_name = {c["case_number"]["value"]: c for c in cases}
    if SLOT_CORRECTIONS.exists():
        for line in SLOT_CORRECTIONS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            case = by_name.get(row["case_number"])
            if case is None:
                failures.append(("(corpus)", "receipts", "clause_slot_corrections",
                                 f"{row['adjudication']} names {row['case_number']}, which is "
                                 f"not a published case"))
                continue
            clauses = {v["clause"] for v in case["verdicts"]}
            if row["from_clause"] in clauses:
                failures.append((row["case_number"], "receipts", "clause_slot_corrections",
                                 f"{row['adjudication']} says clause {row['from_clause']} is a "
                                 f"slot error, but a verdict row still carries it"))
            if row["to_clause"] is not None and row["to_clause"] not in clauses:
                failures.append((row["case_number"], "receipts", "clause_slot_corrections",
                                 f"{row['adjudication']} corrects clause {row['from_clause']} to "
                                 f"{row['to_clause']}, which has no verdict row"))
            if row["adjudication"] in adj_ids:
                used.add(row["adjudication"])
    dead = sorted(adj_ids - used)
    if dead:
        failures.append(("(corpus)", "receipts", "adjudications",
                         f"defined but never applied (dead fix): {dead}"))
    unpinned = sorted(a["id"] for a in adjudications if not a.get("source_sha256"))
    if unpinned:
        failures.append(("(corpus)", "receipts", "adjudications",
                         f"not pinned to a source sha: {unpinned}"))
    print(f"adjudications : {len(adj_ids)} defined, {len(used)} applied")

    if failures:
        print(f"\nFAILURES: {len(failures)}")
        for f in failures[:MAX_REPORT]:
            print(f"  {f[0]}  [{f[1]}]  {f[2]}: {f[3]}")
        if len(failures) > MAX_REPORT:
            print(f"  ... and {len(failures) - MAX_REPORT} more")
        return 1

    print("\nOK: cases conform to the schema, share one key set, carry receipts, and reconcile.")
    print("\nDeterminism is checked OUTSIDE this validator -- re-running the builder here\n"
          "would share a witness with the layer being audited. Two builds, compared as bytes:\n"
          "\n"
          "    python3 l2/build.py && shasum -a 256 data/l2/cases.jsonl\n"
          "    python3 l2/build.py && shasum -a 256 data/l2/cases.jsonl\n"
          "\n"
          "The two digests must be identical.")
    return 0


def case_sort(num):
    """Same canonical order the builder emits (kept independent of it: this is
    an audit, so it re-derives rather than imports)."""
    parts = num.split("/")
    if len(parts) != 4:
        return ("￿", 0, 0, 0, num)
    prefix, serial, month, year = parts
    return (prefix, int(serial), int(month), int(year), num)


if __name__ == "__main__":
    sys.exit(main())
