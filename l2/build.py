"""L2 — one canonical case object per CASE, built from L1 with receipts.

L1 standardised LOCATION and SHAPE. L2 standardises VALUE. The boundary rule,
held everywhere:

    L1 never repairs. L2 repairs with receipts.

Every canonical value is `{value, basis, sources, note}`: what won, under which
registered rule, over which L1 slot values. Nothing that disagreed is thrown
away -- it lands in `sources`, so any repair can be re-argued from the record
alone without reopening L1 (let alone the HTML).

PHASE 1 (SPEC §9 step 2): case explosion, case_number (C1), title (C2/C3),
subject (C4), parties, code_year (C5 partial), procedure flags, dates (C10),
appeal (C6), sanctions, quality, renditions, entities.

PHASE 2 (SPEC §9 steps 3-4), added here: `segments` with the leakage attest
(§6) and `verdicts` (§5, C11/C12). `renditions` changes shape with them --
it now holds INDICES into `segments`, because a rendition is a segment
(carrying its own attest) rather than a bare span. `schema_version` moves from
"l2.1-phase1" to "l2.1" accordingly: an empty verdict list now means a case
with no stated outcome, not an unfinished phase.

Rules held throughout, inherited from L1:
  * every key on every record; absence is a value, never an omission
      null = genuinely absent / unresolved in the source
      ""   = present but empty
      []   = parsed, found nothing
  * one key signature corpus-wide. This is why a rendition that does not exist
    is an all-null ref rather than a null: the signature check recurses into
    objects, so a nullable OBJECT would split the corpus in two. Absence is
    carried by the null FIELDS.
  * every `basis` is a key of RULES below, or an adjudication id. A correction
    is a rule, not an edit.
  * deterministic: pure function of records.jsonl + derived.jsonl +
    pdf_records.jsonl + adjudications.json -- plus, since R20, the CLAUSE
    NUMBERING of data/code/{clauses,pdf_clauses}.jsonl. That last input is read
    for one question only ("does clause N exist in the YYYY Code?"), never for
    text, and both files are optional: an edition we have not extracted leaves
    existence unknown, which is not a refusal. Every fold table is built from
    the corpus in the same pass and every tie is broken by a total order.

Reads data/l1/records.jsonl (202 MB) as a stream, in lockstep with
derived.jsonl -- L1 asserts they list the same files in the same order, and
this build re-asserts it. Writes data/l2/cases.jsonl.

    python3 l2/build.py
    python3 l2/build.py --emit-adjudication-shas   # to (re-)pin adjudications
"""

import argparse
import hashlib
import html as html_mod
import json
import os
import pathlib
import re
import sys
import unicodedata
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORDS = ROOT / "data" / "l1" / "records.jsonl"
DERIVED = ROOT / "data" / "l1" / "derived.jsonl"
PDF_RECORDS = ROOT / "data" / "l1" / "pdf_records.jsonl"
ADJUDICATIONS = ROOT / "l2" / "adjudications.json"
# R20. The Code's own clause structure, per edition. Read ONLY to answer "does
# this clause exist in that year's Code?" -- never for text, which is bench's
# business. Both files are optional: an edition we have not extracted (2001,
# 2003) makes existence unknown, and unknown is not a refusal.
HTML_CLAUSES = ROOT / "data" / "code" / "clauses.jsonl"
PDF_CLAUSES = ROOT / "data" / "code" / "pdf_clauses.jsonl"
OUT = ROOT / "data" / "l2" / "cases.jsonl"
AUDIT = ROOT / "data" / "l2" / "audit_report.json"
# R20. One row per verdict whose year witnesses disagreed: what each witness
# said, which evidence level decided, and the quote it decided on.
YEAR_ARBITRATION = ROOT / "data" / "l2" / "code_year_arbitration.jsonl"
# N2. One row per outcome-slot clause number a reader corrected or deleted:
# the typed number, the number the report rules, the adjudication and its quote.
SLOT_CORRECTIONS = ROOT / "data" / "l2" / "clause_slot_corrections.jsonl"

SCHEMA_VERSION = "l2.3"

# l2.2 (bench/review/DEFECTS.md D1/D3/D4a) changes three things about VALUE and
# nothing about shape discipline:
#
#   D3  verdict attribution is prose-only on appealed cases. The info/meta
#       clause lists state the POST-APPEAL position, so orienting them by who
#       appealed back-filled the Appeal Board's outcome into `panel` -- 75+ T1
#       items carried the wrong body's ruling and T3's `overturned` class was an
#       artefact of dual listing. Lists now set `final` and nothing else;
#       `panel` comes only from this case's panel_ruling prose, `appeal_board`
#       only from appeal-side prose. Where a clause cannot be resolved to ONE
#       Panel ruling the row says so (`dual_ruling`) instead of picking.
#       UNAPPEALED cases are unchanged and deliberately so: with no Appeal
#       Board there is no attribution question, the list IS the Panel's ruling,
#       and that ~10k-item spine is the part both audits verified as sound.
#   D1  procedure.outwith_scope reads the meta status line rather than the bare
#       word 'outwith', which is ordinary Scottish English in these reports.
#   D4a quality.multi_case_undeclared marks pages that carry another case's
#       report banner without declaring it as a sibling.

# ---------------------------------------------------------------------------
# The rule registry. Every `basis` a record carries is a key here (or an
# adjudication id). The validator refuses an unregistered basis, which is what
# stops a repair from being smuggled in as a free-text label.
# ---------------------------------------------------------------------------
RULES = {
    # -- generic ------------------------------------------------------------
    # `unanimous` is a claim about the slots L2 COMPARES. `sources` can also
    # carry slots that are recorded as evidence but are not string-comparable:
    # the report masthead ('CASE AUTH/3303/1/20' against an h1 of
    # 'AUTH/3303/1/20 - Anonymous complainant v Vifor' -- same case, different
    # rendering).
    #
    # The date trailer lines USED to be in that category, with AUTH/3008/1/18
    # (meta and info say 22 December 2017, the trailer says 4 January 2018)
    # named as the reason: two witnesses cannot settle a disagreement, so
    # reading them was deferred rather than resolved wrongly. R12 supplies a
    # third witness for each date field -- the case number for `received`, the
    # receipt constraint for `completed` -- so the trailer is now compared and
    # the date bases below say which witness won. Where no third witness
    # decides, the basis says so; nothing is silently picked.
    "unanimous": "every source slot L2 compares states the same value",
    "sole_source": "exactly one slot states a value; nothing to reconcile",
    # -- C1 case number -----------------------------------------------------
    "case_number_unanimous": "filename, meta, info and h1 all parse to the same set",
    "case_number_info_meta_agree": "filename says which cases exist; info and meta agree on the display form",
    "case_number_h1_preferred": "info/meta do not parse to this number; the h1 states it",
    "case_number_filename_only": "no page slot parses to this number; the filename is the only witness",
    # -- C2/C3 title --------------------------------------------------------
    "title_h1_over_title_tag": "<title> disagrees with <h1>; h1 wins (title carries procedural suffixes and mojibake)",
    "title_h1_over_report_title_typo": "the report's own title line mis-states the case number; h1 wins",
    "title_h1_over_foreign_report_title": "the report pane belongs to another case, so its title line is not this case's; h1 wins",
    # -- C4 subject ---------------------------------------------------------
    "subject_h2_preferred": "hero h2 disagrees with cludo:description; h2 wins (page-visible)",
    "subject_description_fallback": "the hero block carries no h2; cludo:description is the only subject slot",
    "subject_absent": "neither the hero h2 nor cludo:description states a subject",
    # -- parties ------------------------------------------------------------
    "respondent_meta_folded": "cludo:respondent, folded to the corpus canonical form",
    "respondent_h1_fallback": "cludo:respondent names no company; the h1 tail after ' v ' does",
    "respondent_unresolved": "no slot names a respondent",
    "complainant_meta_vocabulary": "cludo:complainant mapped to the SPEC §4 controlled vocabulary; verbatim retained",
    # -- complainant, prose-first (l2.3, DEFECTS D5 / the T1 blocker) --------
    "complainant_prose_first": (
        "at least one of category/anonymous/contactable was read from the case report's own "
        "opening paragraph (or the reviewed explicit name-anonymity request in the complaint) "
        "rather than from the one-word meta slot; the per-field bases in "
        "`field_basis` say which, and every displaced meta value stays in `sources`"),
    "complainant_prose_self_description": (
        "the report's opening states a reflexive self-description ('who described themselves as "
        "a health professional') and the meta slot offered only `other` or `anonymous`, neither "
        "of which is a role; the self-description wins"),
    "complainant_prose_narrator_role": (
        "the report's opening states the complainant's role either as the SUBJECT of the "
        "complaint verb in its own voice ('An anonymous general practitioner complained about "
        "...') or as the direct source in the measured recent-site passive ('a complaint was "
        "received from a health professional'), and "
        "the meta slot offered only `other`; the report's statement wins. Read only where no "
        "self-description was found anywhere in the opening (wave C)"),
    "complainant_employment_outranks_role": (
        "the meta slot states an employment standing and the report's opening states a "
        "professional role, both true of one complainant ('an anonymous complainant writing as "
        "an ‘ExCephalon hospital specialist’'); the employment standing is a relation "
        "to a party and wins, and the role is kept in `note` and `sources.prose_role_verbatim`"),
    "complainant_prose_anonymity": (
        "the report's opening calls the complainant anonymous ('an anonymous complainant', "
        "'wished to remain anonymous'), or the complaint contains the reviewed first-person "
        "request to keep the complainant's name anonymous, and the meta slot does not say so; "
        "prose wins. Silence "
        "never runs the other way -- silence leaves the field null (l2.4)"),
    "complainant_prose_named": (
        "the report's opening states the complainant was NAMED ('a named, contactable "
        "complainant'), which is positive evidence against anonymity. Only a statement of "
        "naming sets this field False; the absence of the word 'anonymous' never does (l2.4)"),
    "complainant_meta_named_company": (
        "the complainant slot names one or more companies the corpus knows as respondents "
        "(an inter-company complaint), or states the complainant was named; a named complainant "
        "is not an anonymous one (l2.4)"),
    "unresolved_anonymity_not_stated": (
        "neither the report opening, the reviewed explicit anonymity-request frame, nor the "
        "meta slot states whether the complainant was "
        "anonymous or named. l2.4 keeps the field null rather than reading silence as 'not "
        "anonymous' -- a bare 'Complainant' meta token states nothing (DEFECTS D6)"),
    "complainant_prose_contactability": (
        "the report's opening states contactability ('an anonymous, non-contactable "
        "complainant', 'the complainant could not be contacted'). The meta slot is not read "
        "for this field, so prose is the only source "
        "and silence leaves it null"),
    "complainant_prose_contactability_final": (
        "the opening states contactability TWICE and the two disagree ('originally contactable "
        "but later became non-contactable', 'The complainant has now become non-contactable'). "
        "The FINAL state is recorded, not the state on receipt; both statements are quoted in "
        "`sources.prose_quotes` (l2.4, DEFECTS D7)"),
    "complainant_meta_structural_category": (
        "the meta slot states a structural category (company / voluntary_admission / "
        "director_initiated / media / organisation) which says how the case AROSE; a prose "
        "self-description does not contradict it, so the meta value stands and the described "
        "role is recorded in the note"),
    "unresolved_contactability_not_stated": (
        "neither the report opening nor the summary states whether the complainant was "
        "contactable; SPEC §6c keeps the field null rather than defaulting it"),
    # -- C5 code year -------------------------------------------------------
    "code_year_meta": "cludo:applicable_code_year",
    "code_year_info_fallback": "cludo:applicable_code_year empty; the info-holder 'Applicable Code year' states it",
    "unresolved_pending_code_dates": "no Code year stated; inference from the received date needs data/code/ (C5)",
    # -- C10 dates ----------------------------------------------------------
    "date_info_preferred": "meta and info dates disagree; the page-visible info-holder date wins",
    "date_meta_only": "only the meta slot holds a parsable date",
    "date_info_only": "only the info-holder slot holds a parsable date",
    "unresolved_no_parsable_date": "neither date slot holds a parsable date",
    # R12: the report trailer is compared, and a third witness breaks the tie.
    "date_slots_over_trailer_same_year":
        "the report trailer states a nearby date in the SAME year (modally one day "
        "later -- a convention difference, not an error); slots kept, trailer date "
        "recorded in sources",
    "date_trailer_over_slots_caseno_agrees":
        "meta/info disagree with the report's 'Complaint received' line on the YEAR; "
        "the case number encodes the same year as the report, so the report wins",
    "date_slots_over_trailer_caseno_agrees":
        "the report's 'Complaint received' line disagrees with meta/info on the YEAR; "
        "the case number encodes the same year as the slots, so the slots win",
    "date_trailer_over_slots_receipt_constraint":
        "meta/info state a completion date preceding receipt, which is impossible; "
        "the report's 'Case completed' line does not, so the report wins",
    "date_slots_over_trailer_receipt_constraint":
        "the report's 'Case completed' line precedes receipt, which is impossible; "
        "the slots do not, so the slots win",
    "date_slots_trailer_disagrees_unresolved":
        "the report trailer disagrees with meta/info and no third witness decides; "
        "the slot value is kept and the disagreement is stated in the note",
    # -- C6 appeal ----------------------------------------------------------
    "appeal_fold_table": "meta and info appeal slots fold to the same outcome",
    "appeal_info_preferred": "the two appeal slots fold differently; the page-visible info-holder wins",
    "appeal_panel_referral": "no party appealed; the Panel referred the case to the Appeal Board",
    "unresolved_appeal_empty": "both appeal slots empty; SPEC §4 requires report-text confirmation before folding to none",
    "unresolved_appeal_unmapped": "the appeal slot holds text that states nothing about who appealed",
    # R18. The report's own 'APPEAL BY <party>' section heading is a third
    # witness to WHO appealed, independent of the two info slots.
    "appeal_slots_and_headings_agree":
        "the appeal slots fold to a party and the report's own 'APPEAL BY <party>' "
        "heading(s) name the same side",
    "appeal_by_heading_conflicts_slots":
        "the appeal slots and the report's 'APPEAL BY <party>' heading(s) name DIFFERENT "
        "sides; `by` is refused (null) rather than picked, because the slot has been wrong "
        "at source before (R18) and the heading can belong to a foreign report",
    # -- sanctions ----------------------------------------------------------
    "sanctions_chips_over_meta_csv": "the rendered chips and the meta CSV disagree; the chips win (page-visible)",
    # -- C11/C12 verdicts (SPEC §5, rewritten in l2.2 for DEFECTS D3) --------
    # Row-CREATING sources are ordinarily the info-holder clause lists and the
    # meta clause CSVs only. The bounded, full-report-hash-pinned
    # PROSE_ONLY_VERDICT_READ registry is the sole reviewed exception. The
    # outcome table and banner headings remain CROSS-CHECKS (SPEC §5 closing
    # paragraph); unreviewed ruling prose only ATTRIBUTES a ruling to a BODY,
    # and the lists never attribute one.
    #
    # The removed l2.1 bases -- verdict_appeal_oriented_respondent /
    # _complainant, appeal_unoriented, verdict_upheld_appeal_prose(_inferred_
    # panel), verdict_final_and_panel_prose, final_only,
    # conflict_unappealed_info_final -- all inferred a Panel ruling from
    # something other than the Panel's own prose. l2/validate.py asserts that no
    # row carries one of those names again.
    "verdict_unappealed": (
        "no appeal was made, so no body other than the Panel ruled on this case and the outcome "
        "lists ARE the Panel's ruling: one polarity stated, final = panel = that polarity. This is "
        "not a prose attribution and does not claim to be; it is the absence of an attribution "
        "question (DEFECTS D3 leaves this class alone -- both audits verified it)"),
    "verdict_unappealed_dual_listed": (
        "no appeal, yet the clause is named in BOTH polarity lists. The corpus states this on 265 "
        "unappealed files and the '(xN)' multipliers show why -- the same clause was ruled on several "
        "materials with different outcomes, which one row cannot separate. panel is refused and the "
        "row is marked dual_ruling; final = breach records only that at least one breach was ruled"),
    "verdict_dual_panel_prose": (
        "the Panel's own ruling prose states BOTH polarities for this clause (different materials "
        "within one case), so there is no single Panel ruling to label. panel is refused and the row "
        "is marked dual_ruling"),
    "verdict_appealed_prose_attributed": (
        "appealed; panel is read ONLY from this case's panel_ruling prose and appeal_board ONLY from "
        "its appeal-side prose (appeal_ruling and the appellant's grounds), each requiring the ruling "
        "body to be the one named or implied by the segment. The lists state the post-appeal position "
        "and set final alone"),
    "verdict_appealed_unattributed": (
        "appealed, and no ruling prose in this case attributes this clause to either body, so the "
        "stated polarity is final and both attributions are refused"),
    "verdict_appeal_status_unresolved": (
        "the appeal status itself is unresolved (C6), so a stated polarity is final but cannot be "
        "attributed to the Panel or the Appeal Board"),
    "verdict_prose_only_reviewed": (
        "the outcome slots omit this clause, but the bounded prose-only assurance audit read the "
        "complete ruling context and accepted a verdict row through PROSE_ONLY_VERDICT_READ. The "
        "entry pins the report-pane sha, quotes every material disposing strand, fixes the final "
        "polarity, and declares the expected Panel/Appeal Board attribution; an unreviewed prose "
        "mention still cannot create a row"),
    # -- R20: which EDITION a verdict row is keyed to (`code_year_basis`) ----
    # These name the evidence LEVEL that decided a row's Code year, not a value.
    # They exist because the three structured witnesses -- the clause chip's
    # href year, the outcome table row's '(YYYY Code)' scope, and the case's own
    # Applicable Code year slot -- disagree on 125 of 7,696 rows, and neither
    # dominates: the chip leaps to editions that did not exist when the case
    # completed (AUTH/2983/10/17 tags Clause 2 to 2019 on a case completed
    # 2018-01-18) while the case slot is wrong in the other direction on
    # AUTH/3777/6/23 (ruled under 2021, chips 2019). See `arbitrate_year`.
    "year_uncontested": (
        "the year witnesses this row has -- clause chip, outcome-table '(YYYY Code)' scope, "
        "case Applicable Code year -- state at most one year between them"),
    "year_clause_prose": (
        "the witnesses disagreed and the report attaches exactly ONE Code year to THIS clause "
        "('Clause 9.1 of the 2016 Code', 'No Breach of Clause 12.2 (2016 Code)'); the most "
        "specific evidence in the document decides"),
    "year_case_prose": (
        "the witnesses disagreed, the report attaches no year to this clause, and it states "
        "exactly one edition for the case as a whole ('considered under the 2019 Code', "
        "'The outcome under the 2021 Code was', 'a breach of the 2016 Code was ruled')"),
    "year_case_slot": (
        "the witnesses disagreed and the report states no edition at all; the case's own "
        "Applicable Code year decides, and only where the clause exists in that edition"),
    "year_undecided_clause_prose_conflict": (
        "the report attaches TWO OR MORE Code years to this clause -- the same clause ruled "
        "under two editions in one case (AUTH/3722/1/23 rules Clause 9.1 under the 2016 Code "
        "for wave 1 and the 2019 Code for waves 2-3). One row cannot carry two editions, so "
        "the year is refused and bench excludes the item"),
    "year_undecided_multi_edition_case": (
        "no year is attached to this clause and the report names more than one edition for the "
        "case, so the case slot is one edition among several rather than the answer"),
    "year_undecided_no_witness": (
        "the witnesses disagreed, the report states no edition, and the case's own year is "
        "absent or does not contain this clause"),
    "year_prose_only_reviewed": (
        "a PROSE_ONLY_VERDICT_READ entry created this otherwise absent row and the same full-case "
        "reading fixed its Code edition (or refused it as null where the ruling spans editions); "
        "the decision is pinned to the report-pane sha"),
    # R20's RESIDUE has no rule id, deliberately (2026-08-10 reading round).
    # The four bases above fire only where the structured witnesses disagree;
    # where the chip and the case slot already AGREE, the per-clause prose can
    # displace them ONLY through a reviewed row in l2/adjudications.json
    # (`verdicts[<clause>].code_year`), and such a row carries the adjudication
    # id as `code_year_basis`, exactly as every other adjudicated value carries
    # its id as `basis`. So the vocabulary here stays the set of things a RULE
    # can decide, and a read decision is never mistakable for one.
    #
    # The consumer contract that goes with it, relied on by bench/generate.py:
    # a null `code_year` under any basis except `year_uncontested` is a REFUSAL.
    # `year_uncontested` with a null year is the other thing entirely -- 57
    # cases that simply never state a Code year.
}

# Bases that l2.1 used and l2.2 forbids. Kept as data so the validator can
# assert their absence by name rather than by memory (DEFECTS D3).
RETIRED_VERDICT_RULES = (
    "conflict_unappealed_info_final",
    "verdict_appeal_oriented_respondent",
    "verdict_appeal_oriented_complainant",
    "appeal_unoriented",
    "verdict_final_and_panel_prose",
    "verdict_upheld_appeal_prose",
    "verdict_upheld_appeal_prose_inferred_panel",
    "final_only",
)

# Warning classes written to data/l2/audit_report.json. Every warning a build
# raises must name one of these, for the same reason every basis must name a
# rule: a warning class is a claim about the corpus, not free text.
WARNING_CLASSES = {
    "prose_contradicts_unappealed_list":
        "an unappealed case's Panel prose states the OPPOSITE polarity to its own outcome lists; "
        "the list wins (it is the site's published outcome) and the case is listed for hand review "
        "-- the class is DECIDED: all 10 rows were hand-read on 2026-08-11 (adj-0109..0116 "
        "and adj-0002); this warning now marks a row whose adjudication has yet to be "
        "written, and a NEW member needs one",
    "prose_dual_ruling":
        "the ruling prose states both polarities for one clause, so no single ruling can be labelled",
    "prose_dual_appeal_ruling":
        "the APPEAL BOARD ruled one clause both ways, in different regards, so no single "
        "panel->board transition exists for it; the Panel's own ruling may still be single "
        "(R28 stage 1, read through DUAL_READ)",
    "conflict_unappealed_both_lists":
        "an unappealed case names the same clause in both polarity lists",
    "multi_case_undeclared":
        "the report pane carries a case-report banner for a case this page does not declare as a "
        "sibling, so its text and outcome lists mix cases (DEFECTS D4)",
    "banner_no_breach_without_no_breach_row":
        "a 'NO BREACH' outcome banner over a case whose verdicts hold no no_breach row",
    "banner_breach_without_breach_row":
        "a 'BREACH' outcome banner over a case whose verdicts hold no breach row",
    "table_clause_absent_from_verdicts":
        "the outcome table names a clause that no info/meta clause list states",
    "outcome_slots_empty_but_table_present":
        "no clause list at all, yet the page renders an outcome table",
    "attest_failed_quotable_segment":
        "a complaint or response segment failed the leakage attest, so bench may not quote it",
    "pdf_flow_without_markers":
        "a substituted PDF flow carries no COMPLAINT/RESPONSE/PANEL RULING/APPEAL marker",
    "rendition_refused_short_span":
        "the leading span before the first ruling language is under 200 chars, so no rendition exists",
    # -- R18 / R20, added 2026-08-09 ----------------------------------------
    "appeal_by_heading_conflicts_slots":
        "the appeal slots say one side appealed and the report's own 'APPEAL BY <party>' heading "
        "names the other; `by` is refused unless an adjudication decides it (R18)",
    "appeal_heading_on_unappealed_slot":
        "both appeal slots fold to 'no appeal' (or to a Panel referral) yet the report carries an "
        "'APPEAL BY <party>' section for a party of THIS case -- a contradiction about whether an "
        "appeal happened at all, which is a different question from who appealed and is NOT "
        "repaired here; recorded for its own review round",
    "code_year_arbitration_refused":
        "the row's year witnesses disagree and no evidence decides, so verdicts[].code_year is null "
        "and bench excludes the item rather than serve a clause from a guessed edition (R20)",
    "code_year_arbitration_displaced_chip":
        "the clause chip's year lost the arbitration; recorded because every item on this row gets a "
        "new item_id when its clause_key moves (R20)",
    # -- the finer slot-typo guard, added 2026-08-10 (pre-freeze repair pass) --
    "published_clause_unwitnessed_prose_sibling":
        "a published verdict row carries NO ruling witness of its own while the Panel's prose rules "
        "a SIBLING of it that the outcome lists never name -- the shape of a mistyped subclause "
        "(3.1 for 3.2, 20.4 for 20.2). A WARNING and never a repair: which side is the typo is a "
        "reading, and the corpus attests it going both ways (adj-0159 corrects the SLOT on "
        "AUTH/2505/5/12; AUTH/1992/4/07 is the same shape with the PROSE wrong and is left alone)",
}

# ---------------------------------------------------------------------------
# Company names
#
# 498 distinct cludo:respondent strings over 1902 pages, of which trailing
# whitespace alone accounts for ~165 ('AstraZeneca' 75 vs 'AstraZeneca ' 52 vs
# ' AstraZeneca' 2 vs 'AstraZeneca  ' 2 -- one company, four strings).
#
# The fold is deliberately conservative in one specific way, documented because
# it looks like an omission: a BARE region qualifier is never stripped.
# 'Otsuka UK and Otsuka Europe' and 'Astellas UK and Astellas Europe' are real
# corpus values, so the site names those as DISTINCT co-respondents within a
# single case; folding 'Otsuka UK' into 'Otsuka' would collapse two parties the
# source holds apart. A region followed by a LEGAL FORM is different -- it is
# the formal registered name of a national arm ('AstraZeneca UK Limited',
# 'Takeda UK Limited'), never a party shorthand, and the corpus never names one
# alongside its own parent. That asymmetry is the rule below.
# ---------------------------------------------------------------------------

# Stripped from the END of a name, repeatedly. Legal form only -- a suffix that
# says how the company is incorporated, never what it is.
LEGAL_SUFFIX = {
    "ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "gmbh",
    "ag", "nv", "bv", "sa", "srl", "spa", "aps", "ab", "oy", "pty",
    "co", "company", "corp", "corporation",
}
# What industry the company is in, which no more distinguishes two firms than
# the legal form does: 'Roche Products' is Roche, 'UCB Pharma' is UCB, 'Abbott
# Laboratories' is Abbott. Merges 23 groups. It cannot over-reach onto a
# qualifier that DOES distinguish, because those are not industry words:
# 'GlaxoSmithKline Consumer Healthcare' folds to 'glaxosmithkline consumer' and
# stays properly apart from GSK, as do 'Bayer Schering Pharma' and 'Sanofi
# Genzyme'. Never applied to a single-token name ('Small Pharma' keeps a key of
# 'small', which is ugly and harmless -- the DISPLAY name is a real corpus
# spelling, not the key).
INDUSTRY_SUFFIX = {
    "pharma", "pharmaceutical", "pharmaceuticals", "healthcare", "health",
    "laboratories", "laboratory", "labs", "therapeutics", "sciences",
    "products", "bioscience", "biosciences",
}
REGION_QUALIFIER = {"uk", "gb"}

# Same company, different spelling. Typos and abbreviations ONLY: each entry is
# two ways of writing one name. Corporate history is deliberately NOT folded --
# Sanofi-Aventis renamed to Sanofi in 2011 and both names are attested in their
# own eras, so merging them would be a claim about company law, not spelling.
# Counts are pages carrying the losing form.
RESPONDENT_ALIASES = {
    "gsk": "glaxosmithkline",                              # 45
    "glaxosmithkline gsk": "glaxosmithkline",              # 1, both names in one field
    "astrazeneca az": "astrazeneca",                       # 1, ditto
    "bristol myers squibb bms": "bristol myers squibb",     # 1, ditto
    "bms": "bristol myers squibb",                          # 1, in 'Pfizer and BMS'
    "alk abello alk abello": "alk abello",                  # 1, the name twice
    "boehringer ingleheim": "boehringer ingelheim",         # 1, typo
    "allergen": "allergan",                                 # 1, typo
    "guerbert": "guerbet",                                  # 3, typo (also in the AUTH-2091 h1)
    "reckit benckiser": "reckitt benckiser",                # 1, typo
    "menarini": "a menarini",                               # 1, the firm is A. Menarini
    "sobi": "swedish orphan biovitrum",                     # 6, the initialism
    "genzyme sanofi": "genzyme",                            # 1, owner in parentheses
    "astellas pharma europe": "astellas europe",            # 1, the industry word sits mid-name
}

# Joint respondents are written 'X and Y' or 'X/Y'. Two company names in this
# corpus contain the word 'and' themselves and would otherwise be torn in half.
PROTECTED_COMPANY_NAMES = (
    "Merck Sharp and Dohme",
    "Special Products and Chemical Developments",
)
COMPANY_JOIN_RE = re.compile(r"\s+and\s+|\s*/\s*", re.I)
VOLUNTARY_PREFIX_RE = re.compile(r"^\s*voluntary\s+admissions?\s*(?:by|[-–])?\s*", re.I)
V_SPLIT_RE = re.compile(r"\s+v\s+", re.I)

# ---------------------------------------------------------------------------
# complainant.category -- SPEC §4 controlled vocabulary.
#
# Tried in order; `company` is decided before this table, by looking the name
# up in the respondent fold (an inter-company complaint names a company the
# corpus knows as a respondent elsewhere). `other` is not a failure bucket: the
# 2024+ site simply writes 'Complainant' with no role at all on 483 pages, and
# claiming a category from that would be inventing evidence.
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    ("voluntary_admission", re.compile(r"voluntary\s+admission", re.I)),
    # 'director' as the PMCPA Director who took the case up.
    #
    # REVISED (wave C). The rule read `director\b(?!\s+of\b)` on the premise,
    # stated in this comment, that NHS job titles "always read 'director of
    # <something>'". Measured over the slot's whole closed vocabulary -- 425
    # distinct hand-typed values, 71 of them containing 'director' -- the
    # premise is false seven times over, and the seven were all published as
    # `director_initiated`, i.e. as cases the PMCPA Director took up:
    # 'Assistant Director Medicines Management', 'Associate Director Pharmacy
    # Policy & Prescribing' (x2 cases), 'Clinical Director', 'Primary Care
    # Medical Director', 'Primary Care Trust Assistant Director Medicines
    # Management', 'Trust Clinical Director'. Eight cases, and the value is a
    # SHOWN field, so eight prompts asserted a procedural fact the report
    # contradicts (AUTH/2180+2181's own opening: 'The Associate Director
    # Pharmacy Policy & Prescribing at a teaching primary care trust
    # complained').
    #
    # The discrimination is POSITIONAL, and it is the site's own convention:
    # the PMCPA Director is written bare ('Director', 65 cases), after a slash
    # ('Media/Director', 'Paragraph 5.1/Director', 'GP/Director' -- 40 values),
    # or with its institution ('PMCPA Director'). A job title puts a qualifier
    # in front ('Assistant', 'Associate', 'Clinical', 'Trust', 'Primary Care
    # Medical'). So the token must START the value, follow a slash, or carry
    # 'PMCPA'. The existing `of` lookahead stays: it is what keeps 'Director of
    # Pharmacy', 'Director of Research' and 'Media/Director of the PMCPA' where
    # they are, so this change moves the seven values above and nothing else.
    # All 71 values were read; the decided table below is the guard.
    ("director_initiated", re.compile(
        r"(?:^\s*|/\s*|\bpmcpa\s+)(?:pmcpa\s+)?director\b(?!\s+of\b)", re.I)),
    ("media", re.compile(r"\b(?:media|journalist|newspaper|press|the\s+sunday\s+times)\b", re.I)),
    # Wave C, three words in this alternation:
    #   `consultants?` -- the plural was unreadable ('Consultants in Child and
    #     Adolescent Psychiatry' fell through to `other`; the `\b` failed on
    #     the 's'). 1 case, AUTH/2241/6/09, 8 items.
    #   `professor` -- absent entirely; 2 cases state it (AUTH/2224/4/09
    #     'Professor of Cardiology', AUTH/2252/7/09 'Professor'), both clinical.
    #   the `regulatory affairs` negative -- 'consultant' is a clinical title in
    #     33 of the slot's 34 consultant-bearing values and a business one in
    #     the 34th ('Regulatory affairs consultant', AUTH/2240/6/09, 12 items,
    #     whose own opening reads 'A regulatory affairs consultant and
    #     scientist/writer'). R10's shape: a measured negative for the one
    #     attested non-clinical qualifier, not an invented class.
    ("health_professional", re.compile(
        r"\b(?:health\s*(?:care)?\s*professionals?|gp|gps|general\s+practitioner|doctor|"
        r"physician|pharmacist|pharmacists|nurse|nurses|"
        r"(?<!regulatory\saffairs\s)consultants?|professor|clinician|surgeon|"
        r"anaesthetist|oncologist|haematologist|dermatologist|gynaecologist|hepatologist|"
        r"rheumatologist|gastroenterolog\w*|paediatrician|psychiatrist|radiologist|"
        r"registrar|practitioner|prescribing\s+advis\w+|medicines\s+(?:management|"
        r"optimisation|information)|head\s+of\s+prescribing|public\s+health)\b", re.I)),
    ("employee_or_ex_employee", re.compile(
        r"\b(?:ex[-\s]?employees?|former\s+employees?|employees?|"
        r"ex[-\s]?contractors?|former\s+contractors?|contractors?|"
        r"ex[-\s]?representatives?|former\s+representatives?|representatives?)\b", re.I)),
    ("organisation", re.compile(
        r"\b(?:mhra|nhs\s+trust|primary\s+care\s+trust|pct|council|association|society|"
        r"charity|agency|group\s+of|on\s+behalf\s+of)\b", re.I)),
    ("member_of_public", re.compile(r"\b(?:member\s+of\s+the\s+public|patient|public)\b", re.I)),
    # l2.4 (audit fix 3d): `anonymous` was the last rule here, so 'Anonymous' in
    # the meta slot became a complainant CATEGORY. Anonymity is not a role -- it
    # is the boolean `complainant.anonymous`, which reads the same token -- and
    # publishing it in both places let a case whose only stated fact was
    # anonymity claim a category it never stated. The rule is removed: such a
    # slot now falls through to the meta-structural `other` and the anonymity
    # boolean carries the fact. `anonymous` is out of the category enum in
    # l2/schema.json too; the KEY set is untouched, so no contract breaks.
]
ANONYMOUS_RE = re.compile(r"\banonymous\b", re.I)

# Wave C. `director` and `consultant` are the two tokens in CATEGORY_RULES whose
# meaning depends on the words around them, and both were WRONG on this corpus
# until they were read exhaustively: 'director' was a PMCPA procedure on eight
# NHS job titles, 'consultant' a clinician on one regulatory-affairs contractor
# and unreadable in the plural. `cludo:complainant` is a hand-typed slot with
# 425 distinct values, so the rules over it are held to the decide-every-value
# standard (docs/WORKING_RULES.md; DEFECTS R3, R11): the 101 values below are the whole
# token-bearing vocabulary of the corpus as read, and `check_complainant_title_
# vocabulary` refuses the build naming any value that is not in it. A new
# spelling then surfaces as an error instead of silently taking whichever
# category the pattern happens to give it.
COMPLAINANT_TITLE_DECIDED = frozenset({
    'A Consultant In Public Health Medicine', 'ALK-Abello/Director',
    'ALK-Abello/PMCPA Director', 'Allergan/Director', 'Allergan/PMCPA Director',
    'Almirall/PMCPA Director', 'Anonymous Consultant Haematologist',
    'Anonymous Consultant Oncologist', 'Anonymous Gastroenterology Consultant',
    'Anonymous consultant', 'Anonymous consultant dermatologist',
    'Anonymous consultant gynaecologist', 'Anonymous consultant physician',
    'Anonymous health professional/PMCPA Director',
    'Anonymous, non-contactable Hospital Consultant', 'Anonymous/Director',
    'Assistant Director Medicines Management', 'Assistant director of public health',
    'Associate Director Pharmacy Policy & Prescribing',
    'Associate Director of Commissioning and ex-employee',
    'Bristol-Myers Squibb and Pfizer/PMCPA Director', 'Cephalon/Director',
    'Cephalon/PMCPA Director', 'Clinical Director', 'Complainant/Director',
    'Consultant', 'Consultant Dermatologist', 'Consultant Haematologist',
    'Consultant Neurologist', 'Consultant Physician',
    'Consultant Respiratory Physician', 'Consultant Rheumatologist',
    'Consultant Urological Surgeon', 'Consultant dermatologist',
    'Consultant in Palliative Medicine', 'Consultant in Public Health',
    'Consultant in Respiratory Medicine',
    'Consultant in anaesthesia & pain management', 'Consultant in palliative medicine',
    'Consultant oncologist and a pharmacist', 'Consultant oncologist and pharmacist',
    'Consultant physician', 'Consultant psychiatrist', 'Consultant radiologist',
    'Consultants in Child and Adolescent Psychiatry', 'Director',
    'Director of Pharmacy', 'Director of Research', 'Director/Media',
    'Director/Merz Pharma', 'Director/Paragraph 17 of the Con and Proc',
    'Director/Shire', 'Drug and Therapeutics Bulletin Media/Director',
    'Ex-employee/Director', 'Ferring/Director', 'Financial Times/Director',
    'GP/Director', 'GlaxoSmithKline/Director', 'Guardian Media/Director',
    'Health Professional/Director',
    'Health professional consultant to a pharmaceutical company',
    'Health professional consultant to a pharmaceutical company/PMCPA Director',
    'Hospital Chief Pharmacist/Director', 'Hospital Consultant', 'Hospital consultant',
    'Johnson & Johnson/Director', 'Johnson & Johnson/PMCPA Director', 'Lilly/Director',
    'Media FT/PMCPA Director', 'Media The Observer/Director',
    'Media criticism - Media/PMCPA Director',
    'Media criticism -The Daily Telegraph/PMCPA Director', 'Media/Director',
    'Media/Director and Anonymous', 'Media/Director of the PMCPA',
    'Media/PMCPA Director', 'Member of the public/Director', 'Merz/Director',
    'Novo Nordisk/PMCPA Director', 'PCT Director of Standards', 'PMCPA Director/Media',
    'Paragraph 17/Director', 'Paragraph 5.1/Director', 'Paragraph 5.1/PMCPA Director',
    'Pharmacosmos/PMCPA Director', 'Primary Care Medical Director',
    'Primary Care Trust Assistant Director Medicines Management',
    'Primary Care Trust Assistant Director of Clinical Services',
    'Primary Care Trust Chief Pharmacist/Associate Director of Public Health',
    'Principal Hospital Pharmacist/Director', 'ProStrakan/Director',
    'Regulatory affairs consultant', 'Roche/Director', 'Roche/PMCPA Director',
    'Scrutiny/Director', 'Senior Hospital Nurse/PMCPA Director',
    'The Sunday Times/Director And A General Practitioner',
    'The Sunday Times/Director and a General Practitioner', 'Trust Clinical Director',
    'ViiV/Director', 'Warner Chilcott/Director',
})
COMPLAINANT_TITLE_TOKEN_RE = re.compile(r"\b(?:director|consultants?)\b", re.I)

# ---------------------------------------------------------------------------
# PROSE-FIRST complainant metadata (l2.3, bench/review/DEFECTS.md D5 / T1
# blocker). The meta slot is a one- or two-word site field and from 2023 it is
# very often just 'Complainant' -- which the vocabulary above can only map to
# `other`, with anonymous False and contactable null. The REPORT SAYS SO,
# formulaically, in its opening paragraph:
#
#   'A complaint was received from an anonymous, non-contactable complainant,
#    who described themselves as a health professional about Novartis ...'
#   'An anonymous, contactable complainant who described him/herself as ...'
#   'An anonymous and non-contactable general practitioner complained about ...'
#
# so the prose is read FIRST and the meta token is the fallback. Measured over
# the corpus: 623 files state anonymity in the opening, 662 state contactability
# (334 contactable, 328 non-contactable) and 672 carry a reflexive
# self-description.
#
# Three precision rules, each measured rather than assumed:
#
# 1. ANCHORED. A token only counts inside a sentence that names the complainant
#    (`complainant`/`complained`/`complaint`), and within 90 characters of that
#    anchor. Without it, 'anonymised patient data' and 'an anonymous survey'
#    in the body would answer a question about the complainant.
# 2. REFLEXIVE. The self-description must carry a reflexive pronoun --
#    'described themselves as', 'described him/herself as'. The bare
#    'described as' form was measured: 672 reflexive matches are complainant
#    self-descriptions and 143 bare-only matches are almost entirely the report
#    describing MATERIALS ('the venue was described as a country house hotel',
#    'the material was described as a report from the 2008 UK Psychiatry
#    Forum'). Requiring the pronoun costs nothing and removes all of them.
# 3. NEVER GUESS. Silence is silence: where the prose says nothing, the meta
#    token stands, and where both are silent `contactable` stays null and
#    `anonymous` stays whatever the meta said. No default is invented.
# ---------------------------------------------------------------------------
COMPLAINANT_ANCHOR_RE = re.compile(r"\bcomplain(?:ant|ants|t|ts|ed|s|ing)\b", re.I)
PROSE_ANONYMOUS_RE = re.compile(r"\banonymous(?:ly)?\b", re.I)
PROSE_REMAIN_ANON_RE = re.compile(r"\bremain\s+anonymous\b", re.I)
# Batch 6.  An explicit first-person publication request occurs in the
# complaint text, beyond the opening-only metadata window: "I would like my
# name to be kept anonymous."  The exact sentence occurs in eight files, all
# copies of the same complaint (CASE/0748 and CASE/0832--0838), and every one
# has the complainant as the speaker.  This is deliberately not widened to
# "wish to remain anonymous": that wording also occurs in company responses
# about third-party health professionals (for example CASE/0238/07/24).
PROSE_EXPLICIT_ANONYMITY_REQUEST_RE = re.compile(
    r"\bI\s+would\s+like\s+my\s+name\s+to\s+be\s+kept\s+anonymous\b", re.I)
# DEFECTS R8. One quote in the corpus attaches `anonymous` to the EVIDENCE
# rather than to the person: AUTH/2956/5/17, 'in any patient specific data and
# the information sought was anonymous in nature'. The sentence names the
# complainant, so the anchor rule admits it, and the case published
# `anonymous: true` on the strength of anonymised data.
#
# The guard is deliberately narrow -- a data/information noun, then a copula,
# immediately before the token. The corpus sweep behind R8 established this is
# the only quote of its kind (of 342 cases where prose alone sets anonymity, 9
# others flagged by the same sweep were read by hand and are genuine), so a
# broad rule would be inventing coverage for a class of one.
ANON_ABOUT_EVIDENCE_RE = re.compile(
    r"\b(?:information|data|dataset|survey|responses?|results?|records?)\b"
    r"[^.;]{0,40}?\b(?:was|were|is|are|be|been)\s+$", re.I)
# Sentence-level anchoring is not SUBJECT-level anchoring, and two shapes prove
# it. AUTH/2327/6/10's complaint was taken up through a NAMED body -- 'The
# Medicines and Healthcare products Regulatory Agency (MHRA) advised the
# Authority ... The matter was taken up as a complaint under the Code' -- and
# the report then describes a DIFFERENT actor, the MHRA's own informant: 'The
# Panel noted that the complaint from an anonymous source to the MHRA was
# that ...'. That sentence carries the complainant anchor and the word, so the
# matcher published `anonymous: true` for a complainant the page names three
# times.
#
# Guarding 'anonymous source' alone does NOT fix it: the loop runs on to the
# PMCPA's standing procedural sentence, 'Anonymous complaints were accepted and
# like all complaints judged on the evidence provided by the parties', which is
# anchored on its own subject and sets the same wrong value. Both shapes are
# needed and both are measured over every anonymity firing in the corpus (686
# cases, each sentence recovered and read): 'anonymous source' occurs in 1
# window, the procedural sentence in 13, and together they move EXACTLY ONE
# published value -- AUTH/2327/6/10 `anonymous: true -> null` (the other 12
# have a genuine sentence firing first, or the meta slot saying so). The
# positive form of this rule was tried and refused: the token attaches to 75
# distinct head words, of which `complaint`/`complaints` is used both for the
# real complainant ('An anonymous complaint was received about inappropriate
# hospitality') and for the procedural statement, so no head-noun vocabulary
# can separate them -- only the verb frame can.
ANON_THIRD_PARTY_SOURCE_RE = re.compile(r"\banonymous\s+sources?\b", re.I)
ANON_GENERIC_PROCEDURE_RE = re.compile(
    r"\banonymous\s+complaints?\b[^.;]{0,60}?\b(?:were|was|are|is)\s+accepted\b", re.I)
# l2.4 (audit fix 1). The ONLY positive evidence that a complainant was not
# anonymous: the report (or the meta slot) says they were named. Measured: a
# bare `\bnamed\b` is a false friend 168 times over ('a named hospital', 'a
# named CCG', 'the CCGs named by the complainant'), so the word must sit in one
# of the two attested complainant-describing forms -- determiner + 'named' +
# (non-)contactable (19 files), or determiner + 'named complainant'. The
# leading `^` alternative is for the meta slot, whose 2 attested naming values
# open with the word ('Named, contactable complainant v CSL Vifor').
PROSE_NAMED_RE = re.compile(
    r"(?:\b(?:an?|the)\s+|^\s*)named[,\s]+(?:and\s+)?"
    r"(?:non[\s\-‐–—]?|un[\s\-]?)?contactable\b"
    r"|(?:\b(?:an?|the)\s+|^\s*)named\s+complainants?\b", re.I)
# The hyphen class covers the four dash characters the corpus actually uses in
# 'non-contactable' (ASCII hyphen, non-breaking hyphen, en and em dash).
# 'uncontactable' is the same statement in the corpus' older register ('An
# anonymous and uncontactable general practitioner', 23 files, 2008-2025) and
# in two of the fix-2 transition cases ('later became uncontactable'); reading
# only the 'non-' spelling left those cases' contactability null (l2.4).
PROSE_NONCONTACTABLE_RE = re.compile(r"\b(?:non[\s\-‐–—]?|un[\s\-]?)contactable\b", re.I)
PROSE_CONTACTABLE_RE = re.compile(r"\bcontactable\b", re.I)
# Assurance batches 1--6.  The corpus also states the same fact as an action,
# not an adjective: "the complainant could not be contacted".  This is kept
# subject-bound rather than admitting bare "could not be contacted": all 25
# firing sentences across 23 report openings were read, and all have the
# complainant as the grammatical subject.  Nineteen files already resolve
# False from an explicit non-/uncontactable statement elsewhere in their
# opening; those keep their existing receipt byte-for-byte.  The action phrase
# supplies the missing witness in AUTH/3293, AUTH/3502, AUTH/3751 and AUTH/3853
# only.
PROSE_COULD_NOT_CONTACT_RE = re.compile(
    r"\bcomplainants?\b[^.;]{0,160}?\bcould\s+not\s+be\s+contacted\b", re.I)
# l2.4 (audit fix 3a): the capture ran to 90 characters, which cut the role in
# half on the corpus' longer openings ('described him/herself a[s a cardiac
# specialist]'). It now runs to the end of the sentence clause or 200
# characters, whichever comes first -- the `[^.;]` class is what stops it at
# the sentence end, the {0,200} is the ceiling.
#
# Wave C, two SPELLINGS of the same frame the corpus writes and the pattern
# could not read. Neither widens the frame -- the reflexive guard and the
# describe-verb requirement both stand -- they close holes in the alternation:
#   * 'described HIMSELF/HERSELF as a concerned UK health professional' -- the
#     class had `him\s*/\s*her\s*self` and bare `himself`, so the full-word
#     slash form fell between them ('himself' matched, then `\s+as` met '/').
#     9 occurrences over 5 files (AUTH/3246, 3247, 3528, 3608, 3635), every one
#     a complainant self-description.
#   * 'REFERRED TO him/herself as a health professional managing ADHD' --
#     2 occurrences, both AUTH/2527/8/12. Bare 'referred to X as' without the
#     reflexive stays out; the pronoun is what makes it a self-description.
PROSE_SELF_DESCRIBE_RE = re.compile(
    r"\b(?:describ\w+|identif\w+|referred\s+to)\s+"
    r"(?:him\s*/\s*her\s*self|him/herself|himself\s*/\s*herself|"
    r"herself\s*/\s*himself|himself|herself|themselves|themself|"
    r"him\s+or\s+herself|itself)\s+(?:as|to\s+be)\s+(?P<role>[^.;]{0,200})", re.I)
# l2.4 (audit fix 3b). The attested NON-reflexive frame: 'an anonymous,
# non-contactable complainant, who stated he/she was a general practitioner'.
# The brief's form carries the relative pronoun, and the corpus also writes it
# without one ('The complainant stated that he/she was a consultant oncologist
# and haematologist working in the UK' -- AUTH/2880/10/16, whose role the audit
# lists as missed). Dropping `who` alone would be reckless: the bare form
# matches 77 times and most are narrative, not roles ('stated that she was
# extremely upset', 'stated that he/she was visited by the husband of a
# patient'). What separates them is grammar, not vocabulary -- a ROLE is a noun
# phrase, so the copula must be followed by a DETERMINER. With that required,
# the frame yields 18 captures over 16 files and every one is a stated role;
# every narrative predicate is refused because 'was extremely upset' and 'was
# visited by' have no determiner.
#
# The determiner alone still admits one sentence whose SUBJECT is not the
# complainant -- 'If visitors to the website stated that they were a health
# professional and clicked through to ferinject.co.uk ...' (AUTH/3303/1/20),
# where the complainant is describing the site's gate, not themselves. So the
# subject is required too: either the relative pronoun (which binds to the
# complainant noun the sentence-level anchor already demanded) or the
# complainant noun itself immediately before the verb.
PROSE_STATED_ROLE_RE = re.compile(
    r"(?:\bwho\s+|\b(?:complainants?|individuals?|persons?|group)\s*,?\s+)"
    r"stated\s+(?:that\s+)?(?:he\s*/\s*she|s?he|they)\s+(?:was|were)\s+(?:an?|the)\s+"
    r"(?P<role>[^.;]{0,200})", re.I)
# Wave C, third self-description frame: the role sits in a PREPOSITIONAL phrase
# before the copula rather than after it -- 'The complainant stated that AS A
# CURRENT MEMBER OF TEVA'S SALES FORCE (s)he was concerned about how
# representatives were encouraged ...' (AUTH/2017/7/07, an audit-confirmed
# miss). The corpus writes 'stated that as a/an ...' 9 times over 8 files and
# only 2 are the complainant describing themselves; the other 7 are companies
# ('Abbott stated that as a result of this incident ...', 'CSL Vifor stated
# that as an immediate action ...') or a document ('The policy stated that as
# a general rule ...'). TWO grammatical requirements separate them, the same
# two PROSE_STATED_ROLE_RE already uses plus one: the subject of 'stated' must
# be the complainant (the relative pronoun or the complainant noun), and the
# clause after the role phrase must resume with a COMPLAINANT PRONOUN --
# 'AstraZeneca should ...' is refused where '(s)he was concerned' is admitted,
# which is what drops AUTH/3013/1/18's 'as a UK company ... AstraZeneca should'.
# Yield: 2 files, 1 of which states a role this build's vocabulary maps.
PROSE_AS_ROLE_COPULA_RE = re.compile(
    r"(?:\bwho\s+|\b(?:complainants?|individuals?|persons?|group)\s*,?\s+)"
    r"stated\s+(?:that\s+)?as\s+(?:an?|the)\s+(?P<role>[^.;]{0,120}?)\s*,?\s+"
    r"(?:\(s\)he|he\s*/\s*she|s?he|they)\s+(?:was|were|had)\b", re.I)
# Wave C, fourth frame: the complainant's own PEN NAME. 'An anonymous
# complainant, WRITING AS "a very disappointed nurse", alleged ...'
# (AUTH/2364/10/10); 'writing as an "Unhappy Physician"' (AUTH/2264/9/09);
# 'writing as a concerned clinician' (AUTH/1961/2/07). The corpus writes it 14
# times over 10 files and every one is the complainant labelling themselves --
# including two where the label is a campaign name rather than a role
# ('Procter & Gamble ... writing as The Alliance for Better Bone Health'),
# which map to nothing and correctly leave the meta category standing.
#
# The capture stops at the first comma or quote, not at the sentence end: the
# label is a short phrase and the rest of the sentence is the ALLEGATION,
# whose vocabulary would otherwise be read as a role ('writing as The Alliance
# ... complained about slide kits ... to health professionals').
PROSE_WRITING_AS_RE = re.compile(r"\bwriting\s+as\s+(?P<role>[^.;,’'”\"]{0,120})", re.I)
# Assurance batches 1--6.  A deliberately narrow narrator appositive, distinct
# from the unsafe general bare "described as" form discussed above.  Requiring
# the complainant noun before the appositive and a complaint verb immediately
# after it makes the role's subject explicit.  It fires twice in one report
# opening (the case-summary copy and full-report opening), in one file only:
# AUTH/2605/5/13, "complainant, described as a neurologist, complained".
PROSE_APPOSITIVE_DESCRIBED_ROLE_RE = re.compile(
    r"\bcomplainants?\s*,\s*described\s+as\s+(?:an?|the)\s+"
    r"(?P<role>[^.;,]{1,80})\s*,\s*(?:complained|alleged|queried)\b", re.I)

# The recent-site passive opening.  This is intentionally the measured health-
# professional form, not a generic "received from <anything>" role guess.  It
# fires on nine direct-role openings: AUTH/3700, AUTH/3755 and AUTH/3824 already
# agree via their meta slots; AUTH/3735, AUTH/3804, CASE/0556, CASE/0557,
# CASE/0558 and CASE/0709 move other -> health_professional.  Openings with "complainant who
# described themselves" are outside this grammar and remain with the stronger
# self-description receipt above.
PROSE_PASSIVE_HEALTH_PROFESSIONAL_RE = re.compile(
    r"\bcomplaint\s+was\s+received\s+from\s+"
    r"(?P<role>(?:an?\s+)?(?:"
    r"(?:named,\s*)?(?:contactable\s+)?(?:verified\s+)?health\s+professionals?"
    r"|group\s+of\s+(?:verified\s+)?health\s+professionals?"
    r"(?:\s+from\s+an?\s+NHS\s+Health\s+Board)?))\s+about\b", re.I)
# Roles, in priority order, mapped to the SPEC §4 vocabulary. 'industry' is not
# in that vocabulary and is not invented as an eleventh value: an industry
# self-description maps to `other` and says so in the note.
# DEFECTS R10. `\bpharma\b` in the industry rule is a real self-description
# ('works in pharma') and also the tail of a company name ('UCB Pharma'), and a
# case-insensitive alternation cannot tell them apart. Python's `re` has no
# variable-width lookbehind, so the discrimination happens here instead: if the
# token is preceded by a capitalised word it is the second half of a name.
# Two cases were affected (AUTH/2972/8/17 'a complaint about the UCB Pharma
# website', AUTH/3030/4/18 'a senior key account manager at UCB Pharma'); the
# published category was already right in both -- `industry` is not a SPEC §4
# value so it falls through to the meta category -- and it was the `note` that
# claimed a self-description that was never made.
COMPANY_TAIL_RE = re.compile(r"(?:^|\s)([A-Z][A-Za-z&.\-]*)\s*$")


def role_hit_is_company_name(role, hit):
    """True when a role token is the tail of a proper name rather than a role."""
    if hit.group(0).lower() != "pharma":
        return False
    return bool(COMPANY_TAIL_RE.search(role[:hit.start()]))


# ---------------------------------------------------------------------------
# Wave C, fifth frame: NARRATOR VOICE. The referee's ruling on the wave-C
# cross-batch conflict (one triage batch called the gap a defect, another
# called it design).
#
# It is a defect. The design comment above this block introduces prose-first
# reading with three quoted formulaic openings, and the THIRD of them is
# 'An anonymous and non-contactable general practitioner complained about ...'
# -- the bare declarative. SPEC §4 says the category is 'mapped from the meta
# field (case-folded) + case text'. D8 is titled 'role extraction: window,
# frame, vocabulary' and its (b) ADDED a frame for exactly this reason: a role
# the report states in a construction the pattern could not read. Nowhere is
# narrator voice declared out of scope -- and where a scope decision WAS taken
# it is written down twice over (D8(d) 'anonymous is not a category', and
# complainant_prose_evidence's opening-only window, which SPEC §6b states and
# which STAYS: evidence deep in the body is still not read).
#
# So the frames are extended, and this is the one that carries the corpus:
# 411 of the 1,483 files whose category the two self-description frames read
# nothing from state the complainant's role as the SUBJECT of the complaint
# verb. 355 of those already have the same category from the meta slot (the
# agreement is the reassurance the rule is reading the right noun phrase); 12
# meet a structural meta category and become a note; 44 are cases whose
# category was `other` and whose own first line names a role -- every one hand
# read from the quote it is taken from.
#
# It is a WALK, not a regex, because the discriminations are positional:
#
#   VERB. complained / alleged / queried, and their inflections. NOT 'wrote',
#   'raised' or 'reported': measured, those three produce third-party subjects
#   ('the Specialist Pharmacy Service in October 2016 raised ...') and no
#   opening this build needs.
#   NOT 'AS ALLEGED'. 'representatives over called on health professionals as
#   alleged' is the report restating the allegation, not an act of complaining.
#   SUBJECT NOUN PHRASE. Runs back from the verb to its own determiner. A
#   SECOND determiner ends it -- a new noun phrase has begun -- unless it sits
#   immediately after 'of', which is how 'a member OF THE public' and 'a member
#   OF A primary care trust medicines management team' stay whole.
#   STOP WORDS end the search with nothing: a second verb, a relative pronoun
#   or an adversative means the phrase is not this verb's subject. 'and' is NOT
#   a stop word -- 'An anonymous AND non-contactable GP alleged' and 'Two
#   consultants in child AND adolescent psychiatry complained' are both single
#   noun phrases, and a conjoined CLAUSE still has a verb, which is.
#   PREPOSITIONAL OBJECT. A determiner preceded by a preposition or a verb is
#   not a subject: 'a complainant who worked in A SPECIALIST BURNS UNIT alleged'
#   and 'Someone who appeared to be A BAYER EMPLOYEE complained' are inference,
#   not a stated role, and are refused.
#   THIRD PARTY. A complainant anchor BEFORE the noun phrase means the sentence
#   has already named the complainant as somebody else, so this phrase is
#   about a third party: 'The complainant believed that THE GP complained ...'.
#   This is also what keeps the frame from becoming a back door around D8(b)'s
#   reflexive rule ('a complainant who was described as an ex ... employee').
# ---------------------------------------------------------------------------
NARRATOR_VERB_RE = re.compile(
    r"\b(?:complained|complains|complaining|alleged|alleges|alleging|"
    r"queried|queries|querying)\b", re.I)
NARRATOR_AS_RE = re.compile(r"\bas\s+$", re.I)
NARRATOR_WORD_RE = re.compile(r"[A-Za-z’'&/.\-]+")
NARRATOR_DETERMINERS = frozenset(
    "a an the two three four five six several this that these those "
    "its his her their our".split())
NARRATOR_PREPOSITIONS = frozenset(
    "in at for with from to on by of about as into over under against through "
    "during between within".split())
NARRATOR_STOPS = frozenset(
    "complained complains complaining alleged alleges alleging queried queries "
    "querying wrote writes raised reported was were had has have is are be been "
    "being said stated noted asked considered received submitted did does do "
    "would could should may might will but which who whom whose if when because "
    "however also further then therefore thus".split())
NARRATOR_ROLE_MAX_CHARS = 200


def narrator_roles(sentence):
    """Every subject noun phrase of a complaint verb in one sentence."""
    for vm in NARRATOR_VERB_RE.finditer(sentence):
        if NARRATOR_AS_RE.search(sentence[:vm.start()]):
            continue
        words = [(m.group(0), m.start(), m.end())
                 for m in NARRATOR_WORD_RE.finditer(sentence[:vm.start()])]
        i = len(words) - 1
        last = i
        while i >= 0:
            w = words[i][0].lower()
            if w in NARRATOR_STOPS:
                break
            if w in NARRATOR_DETERMINERS:
                if i > 0 and words[i - 1][0].lower() == "of":
                    i -= 1
                    continue
                if i == last:
                    break
                if i > 0 and words[i - 1][0].lower() in (
                        NARRATOR_PREPOSITIONS | NARRATOR_STOPS):
                    break
                if COMPLAINANT_ANCHOR_RE.search(sentence[:words[i][1]]):
                    break
                role = sentence[words[i][2]:vm.start()]
                if 0 < len(role) <= NARRATOR_ROLE_MAX_CHARS:
                    yield role, words[i][1], vm.end()
                break
            i -= 1


PROSE_ROLE_RULES = [
    # Wave C. FIRST, because a professional BODY is not its members' role: the
    # Royal College of General Practitioners (RCGP) Overdiagnosis Group
    # complained in AUTH/3425+3426/11/20, and 'Practitioners' inside its NAME
    # read as health_professional. Position priority does the work -- 'Royal
    # College' sits at offset 0 of that capture and the role word at 22 -- so
    # the rule only has to exist, not to be tried first, but the ordering says
    # what it is for. Deliberately NOT the meta rule's wider vocabulary: 'nhs
    # trust' / 'primary care trust' / 'pct' are WORKPLACES in this position ('A
    # primary care trust pharmacist complained', 4 files), and including them
    # moved four individuals to `organisation`. Measured against all 521
    # existing prose captures: 0 move.
    ("organisation", re.compile(
        r"\broyal\s+college\b|\bcollege\s+of\b|\bcommittee\b|\bassociation\b|"
        r"\bsociety\b", re.I)),
    # l2.4 (audit fix 3c) adds the roles the audit found unmapped, each one
    # attested in the corpus' own self-descriptions and each one measured before
    # admission: `heath` is the source's own typo ('a concerned UK heath
    # professional', 2 files) and is tolerated, not corrected; `practitioner`
    # bare catches 'healthcare practitioner' (the meta vocabulary above already
    # reads it bare); `specialist` appears in 6 self-descriptions corpus-wide
    # and every one is clinical (cardiac x3, fertility, nurse x2, pharmacist),
    # so it is admitted bare; `expert` appears ONCE, as 'cardiac expert', so it
    # is bound to that compound rather than admitted bare -- an unqualified
    # 'expert' is a role in no particular field.
    ("health_professional", re.compile(
        r"\b(?:he(?:al|a)th\s*(?:care)?\s*professional|healthcare\s+professional|clinician|"
        # Assurance batches 1--6. `prescriber` is a clinical role in all three
        # files whose opening-role captures contain it. It newly decides
        # AUTH/2433's narrator subject and AUTH/2728's self-description;
        # AUTH/3634 already
        # resolves from the earlier phrase `cardiac specialist` in its capture.
        r"doctor|physician|gp|gps|general\s+practitioner|practitioner|prescriber|"
        r"nurse|pharmacist|"
        # the same `regulatory affairs` negative CATEGORY_RULES carries, for the
        # same reason and the same case: AUTH/2240/6/09's opening reads 'A
        # regulatory affairs consultant and scientist/writer, complained', which
        # the wave-C narrator frame reads, and a fix in one vocabulary that the
        # other undoes is not a fix.
        r"(?<!regulatory\saffairs\s)consultant|surgeon|registrar|oncologist|"
        r"psychiatrist|paediatrician|radiologist|"
        r"anaesthetist|gynaecologist|rheumatologist|dermatologist|haematologist|neurologist|"
        r"cardiologist|optometrist|specialist|cardiac\s+expert|"
        # Wave C. `professor` is deliberately NOT here, though it is in
        # CATEGORY_RULES above. The meta slot states it twice and both are
        # clinical ('Professor of Cardiology', 'Professor' on a case whose
        # opening reads 'A hospital professor complained'). The PROSE attests a
        # third use that is not: AUTH/3763/4/23's complainants 'described
        # themselves as a university professor and a university senior
        # lecturer' about transfers of value -- academics, not clinicians. A
        # token that is clinical in one slot and not in the other is admitted
        # only in the slot where it was measured; both AUTH/2224 and AUTH/2252
        # still resolve health_professional through their meta value, and
        # AUTH/3763 keeps the honest `other`.
        #
        # The four terms below ARE parity with CATEGORY_RULES: the same words
        # already mean health_professional when the site's meta slot says them,
        # and the prose list was assembled for self-descriptions only, so it
        # never got them. They are what reads 'a member of a primary care trust
        # (PCT) MEDICINES MANAGEMENT team' and 'a MEDICINES MANAGEMENT team
        # leader'.
        r"medicines\s+(?:management|optimisation|information)|"
        r"prescribing\s+advis\w+|head\s+of\s+prescribing|public\s+health|"
        r"medical\s+professional|practice\s+manager)\w*\b", re.I)),
    ("member_of_public", re.compile(
        r"\bmember\s+of\s+the\s+(?:general\s+)?public\b|\bpatient\b", re.I)),
    ("employee_or_ex_employee", re.compile(
        r"\bex[\s\-]?employee|former\s+employee|\bemployee\b|ex[\s\-]?representative|"
        # Wave C: 'a current member of Teva's SALES FORCE' (AUTH/2017/7/07).
        # The phrase appears 88 times corpus-wide and 87 are company materials
        # ('Slides for hospital sales force'), which is why it is admitted only
        # HERE -- inside a capture that a self-description frame has already
        # decided is about the complainant -- and not in the meta vocabulary.
        r"\bsales\s+force\b|"
        r"former\s+representative|ex[\s\-]?contractor|former\s+contractor|\bcontractor\b", re.I)),
    ("industry", re.compile(
        r"pharmaceutical\s+industry|industry\s+professional|\bpharma\b|"
        r"work\w*\s+for\s+a\s+pharmaceutical", re.I)),
]
# Categories the meta slot states as a STRUCTURAL fact about how the case
# arose, not as a role the complainant claimed. A self-description does not
# contradict them (a case can be taken up by the PMCPA Director AND have been
# raised by a health professional -- 'Complainant/Director' is an attested meta
# value), so prose does not displace them; it is recorded in the note instead.
STRUCTURAL_CATEGORIES = ("company", "voluntary_admission", "director_initiated",
                         "media", "organisation")
# An EMPLOYMENT STANDING outranks a professional role when one person attests
# both (2026-08-11, on AUTH/2361/10/10 -- a RULE, not an exception for that
# case). The category answers "what is this complainant's standing", and the two
# readings here are not in conflict: 'An anonymous complainant writing as an
# "ExCephalon hospital specialist"' is an ex-employee of Cephalon AND a hospital
# specialist, both true of one person. The employment is a RELATION TO A PARTY
# and the clinical role is an attribute of the person, and it is the relation
# that shapes the evidence -- an insider's account of internal conduct, with the
# contactability and burden-of-proof consequences the Code's own process
# attaches to it. The role is never lost: it stays in the note and in
# `sources.prose_role_verbatim`.
#
# Decided over the whole slot, not over the case that raised it. Sweeping every
# case where the two readings differ, the pairs that put employment against a
# role are 5 -- AUTH/2361/10/10 ('ExCephalon hospital specialist'),
# AUTH/3203/6/19 ('An anonymous employee who described him/herself as a
# concerned health professional'), AUTH/3204/6/19 ('a concerned health
# professional employed by Otsuka'), AUTH/3790/7/23 ('a health professional and
# ex-employee') and AUTH/3454/1/21 ('described themselves as a Sanofi
# representative', which was publishing `member_of_public` against a Sanofi
# respondent) -- and 0 run the other way (prose employment against a meta role),
# where the prose-first default already gives employment the value, so the rule
# is symmetric by construction and only ever had one side to write.
#
# ONE MEMBER FOR TIM'S EYE: on AUTH/3204/6/19 the employer named in the prose is
# Otsuka while the respondent is GlaxoSmithKline -- the complainant is an
# industry employee complaining about a competitor 'in his/her private
# capacity'. The meta slot still states the employment standing ('Anonymous
# pharmaceutical employee'), and standing in the industry is what the slot
# records, so the rule takes it; if the rule should be narrowed to employment
# TO THE RESPONDENT, this is the one case that leaves and it needs its own
# reading.
EMPLOYMENT_CATEGORY = "employee_or_ex_employee"
ROLES_OUTRANKED_BY_EMPLOYMENT = ("health_professional", "member_of_public")
# How far into the report pane the opening runs when there is no body heading
# to stop at. The complainant sentence sits at char 79-1153 across the corpus.
PROSE_OPENING_FALLBACK_CHARS = 3000
# l2.4 (audit fix 3a, second 90-character constant). The anchor window decides
# whether a token is ABOUT the complainant. At 90 characters it split the
# corpus' own standard opening sentence: 'An anonymous, contactable health
# professional who described themselves as a general practitioner complained
# about ...' puts `contactable` 84 characters from `complained` and `anonymous`
# 95, so the SAME sentence had its contactability read and its anonymity
# refused (AUTH/3170/3/19, AUTH/3202/6/19 published 'anonymity not recorded'
# while their first line said 'An anonymous ... complainant'). 200 characters,
# the same ceiling as the role capture, keeps the sentence-level requirement --
# the sentence must still NAME the complainant -- while letting one clause of
# self-description sit between the token and the anchor.
PROSE_ANCHOR_WINDOW = 200

# ---------------------------------------------------------------------------
# C6 appeal fold. 72 distinct meta forms and 65 info forms over the corpus; the
# two slots agree everywhere (after whitespace folding only 2 pages differ, and
# only by a doubled space), so `unanimous` is the overwhelming basis and
# `appeal_info_preferred` exists for completeness rather than volume.
#
# A leading 'No appeal' settles the case before appellants are looked for: 12
# of the forms read 'No appeal, <what happened next>' and naming the Appeal
# Board there does not make it an appeal.
# ---------------------------------------------------------------------------
NO_APPEAL_RE = re.compile(r"^\s*no\b(?:\s+appeal\b)?", re.I)
RESPONDENT_APPELLANT_RE = re.compile(r"\brespondents?\b", re.I)
# 'complainent', 'complainents' and 'complaint' are attested misspellings of
# complainant in this slot (3 pages between them).
COMPLAINANT_APPELLANT_RE = re.compile(r"\bcomplain(?:a|e)nts?\b|\bcomplaints?\b", re.I)
BOTH_PARTIES_RE = re.compile(r"\bboth\s+parties\b", re.I)
# The Panel can send a case up itself. No party appealed, so `by` is none -- but
# the case was still heard by the Appeal Board, which the note records.
PANEL_REFERRAL_RE = re.compile(r"\breport(?:ed|s)?\b.{0,40}\bappeal\s+board\b", re.I | re.S)

# ---------------------------------------------------------------------------
# R18. The report's own appeal heading, as a THIRD witness to `by`.
#
# The two info slots share the PMCPA's record-keeping, so they are not two
# independent witnesses -- and on AUTH/3028/3/18 and AUTH/3535/7/21 they are
# both simply false: the slot reads 'Appeal by respondent' over a report headed
# `APPEAL BY THE COMPLAINANT` whose body says 'Sobi accepted all of the rulings
# from the Panel in this case'. The heading is a cheap independent witness.
#
# The vocabulary is CLOSED and exhaustively checkable, which is the standard a
# pattern over a hand-typed slot has to meet (R3/R11): l1/derive.py normalises
# 352 report headings over 273 files to APPEAL_GROUNDS, in 103 distinct
# spellings, and EVERY ONE of them begins 'APPEAL BY ' or 'APPEAL FROM '. So
# the party string is always extractable; what varies is whether it can be tied
# to a party of THIS case. `check_appeal_heading_coverage` refuses a build that
# meets an APPEAL_GROUNDS heading this pattern cannot parse.
# ---------------------------------------------------------------------------
APPEAL_HEADING_PARTY_RE = re.compile(r"^\s*APPEAL\s+(?:BY|FROM)\s+(.+?)\s*$", re.I)
# 'APPEAL FROM THE COMPLAINANT (first email, 23 April 2021)' and 'APPEAL FROM
# THE COMPLAINANT – REDACTED CONTRACT': the party ends at the first bracket or
# dash. 2 headings corpus-wide, both complainant-side.
APPEAL_HEADING_GLOSS_RE = re.compile(r"\s*[\(\[–—].*$")
COMPLAINANT_HEADING_WORDS = frozenset({"complainant", "complainants"})

# ---------------------------------------------------------------------------
# R19/R20. Which EDITION of the Code a report says it ruled under.
#
# Two grains, read separately because they are different evidence:
#
#   per CLAUSE   'Clause 9.1 of the 2016 Code', 'Clauses 24.1, 24.4 and 27.7 of
#                the 2019 Code', 'No Breach of Clause 12.2 (2016 Code)'
#   per CASE     'considered under the 2019 Code', 'thus the Panel used the
#                2014 Code', 'The outcome under the 2021 Code was',
#                'a breach of the 2016 Code was ruled'
#
# The case-grain family is split into DECISIVE and WEAK because the corpus
# contains a case that states both and means only one: AUTH/2220/3/09 reads
# 'the requirements of the 2006 Code applied. However the clauses cited ... were
# the same in the 2006 Code as in the 2008 Code. The case was thus considered
# under the 2008 Code' -- and its 2008 tag is CORRECT. A rule that pooled the
# two frames would have called that case a conflict; a rule that read only the
# weak frame would have called its tag wrong. Only DECISIVE statements are used
# to decide a year; WEAK ones are recorded as evidence and nothing more.
# ---------------------------------------------------------------------------
_YEAR = r"(?:19|20)\d{2}"
EDITION_DECISIVE_RES = (
    re.compile(r"(?:was|were|would\s+be|is|are|be)\s+considered\s+(?:under|in\s+relation\s+to)\s+"
               r"(?:the\s+)?(?:requirements\s+of\s+the\s+)?(" + _YEAR + r")\s+"
               r"(?:edition\s+of\s+the\s+)?Code", re.I),
    re.compile(r"considered\s+(?:the\s+)?(?:this\s+)?(?:case|matter|complaint)s?\s+"
               r"(?:under|in\s+relation\s+to)\s+(?:the\s+)?(?:requirements\s+of\s+the\s+)?"
               r"(" + _YEAR + r")\s+(?:edition\s+of\s+the\s+)?Code", re.I),
    re.compile(r"Panel\s+used\s+(?:the\s+)?(" + _YEAR + r")\s+Code", re.I),
    re.compile(r"rulings?\s+(?:below\s+)?(?:were|are|was|is)\s+made\s+under\s+the\s+"
               r"(" + _YEAR + r")\s+Code", re.I),
    re.compile(r"outcome\s+under\s+the\s+(" + _YEAR + r")\s+Code", re.I),
    re.compile(r"considered\s+under\s+the\s+(" + _YEAR + r")\s+Code", re.I),
    re.compile(r"used\s+the\s+(" + _YEAR + r")\s+(?:edition|Code)", re.I),
    # Clause-less ruling frames. AUTH/3143/1/19 states its edition exactly once,
    # this way: 'high standards had not been maintained and a breach of the 2016
    # Code was ruled' -- on a case the slot tags 2019.
    re.compile(r"(?:no\s+)?breach(?:es)?\s+of\s+the\s+(" + _YEAR + r")\s+Code\s+"
               r"(?:was|were)\s+ruled", re.I),
    re.compile(r"ruled\s+(?:a\s+|no\s+)?breach(?:es)?\s+of\s+the\s+(" + _YEAR + r")\s+Code", re.I),
)
EDITION_WEAK_RES = (
    re.compile(r"(?:requirements|provisions)\s+of\s+the\s+(" + _YEAR + r")\s+"
               r"(?:edition\s+of\s+the\s+)?Code[^.]{0,40}?applied", re.I),
    re.compile(r"the\s+(" + _YEAR + r")\s+Code\s+applied", re.I),
    re.compile(r"relevant\s+Code\s+was\s+the\s+(" + _YEAR + r")", re.I),
    re.compile(r"Code\s+in\s+force\s+then\s+was\s+the\s+(" + _YEAR + r")\s+Code", re.I),
)
# 'Clauses 24.1, 24.4 and 27.7 of the 2019 Code' distributes the year over every
# clause in the list; '(2016 Code)' is the outcome list's own per-row scope.
CLAUSE_YEAR_RE = re.compile(
    r"Clauses?\s+((?:\d{1,2}(?:\.\d{1,2})?)(?:\s*(?:,|and|&)\s*\d{1,2}(?:\.\d{1,2})?)*)"
    r"\s*(?:\(\s*(" + _YEAR + r")\s+Code\s*\)"
    r"|(?:of|in|under)\s+the\s+(" + _YEAR + r")\s+(?:edition\s+of\s+the\s+)?Code)", re.I)
CLAUSE_TOKEN_RE = re.compile(r"\d{1,2}(?:\.\d{1,2})?")
# How much context a year receipt keeps. Long enough to read the frame back
# without keeping the pane.
YEAR_QUOTE_BEFORE, YEAR_QUOTE_AFTER = 90, 60


def edition_evidence(text):
    """What one report says about which Code edition governed it.

    Returns (decisive, weak, per_clause), each keyed by year, each value a
    QUOTE -- the receipts a reader needs to re-argue the decision without the
    pane. `per_clause` is {clause: {year: quote}}.
    """
    flat = collapse(text or "")
    decisive, weak = {}, {}
    for pats, bucket in ((EDITION_DECISIVE_RES, decisive), (EDITION_WEAK_RES, weak)):
        for pat in pats:
            for m in pat.finditer(flat):
                bucket.setdefault(int(m.group(1)), flat[max(0, m.start() - YEAR_QUOTE_BEFORE):
                                                       m.end() + YEAR_QUOTE_AFTER])
    per_clause = {}
    for m in CLAUSE_YEAR_RE.finditer(flat):
        year = int(m.group(2) or m.group(3))
        quote = flat[max(0, m.start() - YEAR_QUOTE_BEFORE):m.end() + YEAR_QUOTE_AFTER]
        for clause in CLAUSE_TOKEN_RE.findall(m.group(1)):
            per_clause.setdefault(clause, {}).setdefault(year, quote)
    return decisive, weak, per_clause

# ---------------------------------------------------------------------------
# Sanctions chips.
#
# The chip is a DIV carrying class 'tag-label', and its content comes in two
# shapes -- <span>Advertisement</span> and <a href="/cases/advertised-sanctions/
# ...">Advertisement</a>. A span-only extractor silently drops every linked
# chip (Advertisement reads 194 instead of 261). The div nests nothing, so the
# non-greedy match is safe.
# ---------------------------------------------------------------------------
TAG_LABEL_RE = re.compile(r'<div[^>]*class="[^"]*tag-label[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
# The info-holder's flattened `value` space-joins the chips
# ('Advertisement Public reprimand'), which is unparseable; the meta slot
# comma-joins the same list. Only the chips and the meta CSV are usable.
#
# ---------------------------------------------------------------------------
# DEFECTS R31 -- the distinctiveness floor on the `no_sanctions_text` needles.
#
# `leakage_attest`'s docstring already reasons this way for `no_ruling_language`
# ("a check that fired on allegations would refuse every complaint in the corpus
# and the refusal would look like rigour") and stopped short of applying it to
# the chip needles. It should have: a chip is a page-visible LABEL for the
# sanction, and one of the nine labels the corpus uses is the single generic
# word 'Advertisement', which is also what a quarter of all case reports are
# ABOUT. AUTH/2008/6/07's matter 2 is headed "2 Quick Guide 'Advertisement
# Feature'" -- no segment of that matter could ever survive its own case's chip,
# so both of its segments were refused, bench/generate.py fell back to matter 1,
# and four items asked about Clause 3.2 while showing text that never mentions
# it.
#
# The floor is BASE-RATE CONTAMINATION, measured per needle over the whole
# corpus: of the 1,902 pages that do NOT carry the chip, how many contain the
# needle anyway? A needle ordinary case vocabulary supplies carries no
# information about THIS case's sanction, so refusing on it is not rigour.
#
#   needle                                  w  chip pages  other pages  base rate
#   advertisement                           1         253    407/1,649     24.68%
#   re-audit                                1          22     25/1,880      1.33%
#   corrective statement                    2          11     22/1,891      1.16%
#   public reprimand                        2          44      3/1,858      0.16%
#   suspended from membership of the abpi   6           8      2/1,894      0.11%
#   audit of company's procedures           4          43      0/1,859      0.00%
#   recovery of items                       3           8      0/1,894      0.00%
#   reports to abpi board                   4           6      0/1,896      0.00%
#   reports to appeal board                 4           2      0/1,900      0.00%
#
# The distribution is not a gradient: one needle at 24.68% and eight at or under
# 1.33%, a factor of 19 between them. The floor is 5% -- 4.9x below the value it
# drops and 3.7x above the highest it keeps -- and it DECIDES all nine. The
# decisions are declared here rather than recomputed at build time on purpose:
# `digest` keeps no pane text (its own first rule), a corpus-wide rate would
# need a second pass over 1,902 panes, and a threshold recomputed as the corpus
# grows is exactly how `abridged` drifted to 40% false positives (R11). What
# grows instead is the refusal below.
#
# Effect, measured on the pre-fix build: 146 segments corpus-wide failed
# `no_sanctions_text` and nothing else (33 complaint, 33 response, 40
# summary_rendition, 40 abstract_rendition over 56 cases); 142 of them were
# refused by 'advertisement' alone. The other 4 stay refused and are a DIFFERENT
# mechanism, recorded rather than repaired: AUTH/2780/7/15 x3 and
# AUTH/3123/11/18 x1, where the needle is distinctive but the sentence is not a
# sanction ("the complainant urged the PMCPA to consider more serious sanctions
# including an audit of the company's procedures, a public reprimand and
# possible suspension"; "Otsuka global regulatory affairs was recently
# re-audited by the same auditor"). Deciding those needs the sentence, not the
# needle, and a needle floor must not pretend otherwise.
SANCTION_NEEDLE_BASE_RATE_FLOOR = 0.05
SANCTION_NEEDLE_USED = {
    "advertisement": False,                        # 24.68% -- ordinary vocabulary
    "audit of company’s procedures": True,         # 0.00%
    "corrective statement": True,                  # 1.16%
    "public reprimand": True,                      # 0.16%
    "re-audit": True,                              # 1.33%
    "recovery of items": True,                     # 0.00%
    "reports to abpi board": True,                 # 0.00%
    "reports to appeal board": True,               # 0.00%
    "suspended from membership of the abpi": True,  # 0.11%
}

# ---------------------------------------------------------------------------
# Procedure flags.
#
# `abridged`: the bare word matches 20 pages, but 10 of them describe abridged
# promotional MATERIAL ('a smaller, abridged leavepiece', 'an abridged version
# of the instructions for use'), not the abridged complaints procedure. The
# two-word phrase matches 10 pages, all of them the procedure, stated in the
# case title line -- consistent with DESIGN.md's '6+'.
#
# REVISED (R11). That last claim was measured and true when written, and the
# corpus then grew: with cases through 2025 the two-word phrase still matches
# 10 pages but only SIX are the title line. The other four are prose -- a
# company arguing for the reform before it existed ('once the new abridged case
# management process is introduced'), one citing it in a Clause 2 argument, and
# two cases the case preparation manager STARTED under the abridged procedure
# and then moved to the full one ('initially asked ... however ... determined
# that the complaint should be progressed through' the full procedure). Those
# last two are the sharpest: they mention the procedure precisely because it
# did NOT apply.
#
# The fix is what the original comment already named: read the title line, not
# the body. All six genuine matches sit at offset 29 of the summary pane,
# immediately after 'Case Summary <case number> '; the four false positives do
# not appear in the summary pane at all. The window below is ~10x that offset
# and still nowhere near the narrative.
#
# External check: the PMCPA's 2025 Annual Report states 'only five complaints
# progressing through the abridged procedure in 2025'. The six survivors are
# one received in 2024 and five in 2025.
ABRIDGED_TITLE_WINDOW = 300
#
# `outwith_scope`: REPLACED in l2.2 (DEFECTS D1). The bare keyword matched 116
# summary panes and was wrong on every T4 item it produced -- 'outwith' is
# Scottish English for 'outside' and these reports use it generally ('outwith
# the licence', 'outwith the SPC', 'outwith the SOP'), sometimes on cases that
# carry BREACHES. The flag now reads the site's own status line, which states
# the disposal literally.
#
# WIDENED (R3). The rule matched ONE exact string, 'no breach, outwith the
# scope of the code' (72 files), and the status slot is hand-typed with 193
# distinct values across the corpus. Twenty-four more files state the same
# disposal in seven other spellings -- 'No breach not within the scope of the
# Code' (16), 'outwith the scope of the Code' (2), 'No breach ruled as not
# within the scope of the Code' (2), and one each of four more including the
# typo 'out with'. 'Outwith' is Scottish English for 'outside'; every variant
# means the same thing, and the two groups are the same kind of document
# (median report length 12 characters for the exact-match group, 45 for the
# variants -- stubs, which is what this disposal produces).
#
# This does NOT reopen D1. D1's false friends were in NARRATIVE PROSE ('outwith
# the licence', 'outwith the SPC'); the fix was to read the status slot instead.
# Widening the pattern INSIDE that slot does not reintroduce body text, and the
# guard below measures it rather than assuming: any status naming the Code's
# scope that this rule does not match is a REFUSAL, so a new hand-typed
# spelling surfaces as an error instead of quietly shrinking the class.
# 72 -> 96 files. No item changes: all 24 variant cases are stubs that produce
# no quotable segment.
# ---------------------------------------------------------------------------
ABRIDGED_RE = re.compile(r"abridged\s+(?:procedure|complaint|case)", re.I)
OUTWITH_RE = re.compile(r"outwith", re.I)
OUTWITH_STATUS_RE = re.compile(
    r"\b(?:outwith|out\s+with|not\s+within|outside)\s+the\s+scope\b", re.I)
# Anything mentioning the Code's scope must be decided by the rule above, not
# fall through it. Checked corpus-wide at the end of the build.
OUTWITH_SCOPE_MENTION_RE = re.compile(r"scope\s+of\s+the\s+code", re.I)
PARAGRAPH_17_RE = re.compile(r"paragraph\s*17", re.I)

# D4a. 'CASE AUTH/2296/1/10 - COMPLAINANT v X' as a HEADING: the line opens with
# the number (optionally 'CASE'/'CASES'), which is what a report banner looks
# like and what a citation inside a sentence does not.
CASE_BANNER_RE = re.compile(
    r"^\s*(?:CASES?\s+)?[A-Z]{3,}\s*/?\s*\d{2,5}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b", re.I)

# ---------------------------------------------------------------------------
# Segments (SPEC §6).
#
# Boundaries come from the DERIVED heading verdicts, never from a regex over
# the pane: l1/derive.py already carries the confidence model and its measured
# error rates, and re-detecting headings here would give the corpus two
# disagreeing opinions about where a section starts.
#
# The four boundary tokens are the only ones that open a canonical narrative
# section. They legitimately REPEAT -- a multi-case report runs
# COMPLAINT/RESPONSE/PANEL RULING once per case, and one file runs them 316
# times -- so every occurrence is emitted, in char order, rather than the
# first winning.
# ---------------------------------------------------------------------------
# APPEAL_GROUNDS (l1d.2) is here for one reason above all: it TERMINATES the
# panel_ruling segment. 'APPEAL BY <COMPANY>' opens the appellant's grounds, and
# without that boundary the Panel's ruling ran on into them -- 33 of 35 sampled
# T3 extracts quoted the appellant's argument as if it were the ruling under
# appeal (DEFECTS D3). It is appeal-side material, so it also feeds the
# appeal_board attribution, under the speaker rules in `ruling_polarities`.
BOUNDARY_KIND = {
    "COMPLAINT": "complaint",
    "RESPONSE": "response",
    "PANEL_RULING": "panel_ruling",
    "APPEAL_GROUNDS": "appeal_comments",
    "APPEAL_BOARD_RULING": "appeal_ruling",
}
# l2.3 (DEFECTS residual R2). These two l1d.3 tokens open a section only where
# the document's stage structure says they do; `html_boundaries` holds the test
# and is the ONLY place that may promote them. Kept out of BOUNDARY_KIND so no
# other code path can treat them as unconditional boundaries.
#
# 'APPEAL BOARD CONSIDERATION' is the Board's own section -- its prose defaults
# to the Board as speaker, which is the same default `appeal_ruling` already
# carries. 'COMMENTS FROM <party>' is a party speaking at the appeal, so it
# gets `appeal_comments`, whose default speaker is nobody.
POSITIONAL_BOUNDARY_KIND = {
    "APPEAL_BOARD_CONSIDERATION": "appeal_ruling",
    "APPEAL_COMMENTS_HEADING": "appeal_comments",
}
# R28 stage 1, `matter_headings`. Page furniture: these l1d tokens name the
# document, not a matter within it, and all four sit immediately before a
# COMPLAINT/RESPONSE boundary on some page ('CASE AUTH/2099/2/08', 'FULL CASE
# REPORT', a banner, the summary head) -- 548 of the 2,309 headings the other
# filters keep.
HEADING_PAGE_FURNITURE = frozenset(
    {"CASE_TITLE", "FULL_CASE_REPORT", "OUTCOME_BANNER", "CASE_SUMMARY"})
MATTER_HEADING_MAX_WORDS = 40
MATTER_ENUMERATOR_RE = re.compile(r"^\s*\d{1,2}\s*[.):]?\s+\S")

# Assurance repair pass, segmentation class G.  These are section boundaries
# the frozen L1-derived vocabulary did not name.  They are repaired in L2 so
# L1 remains a source-faithful, byte-stable extraction layer.
#
# The possessive form has three measured holes in l1d.4's otherwise-correct
# RESPONSE reader: a plural name whose possessive is written ``S’`` rather than
# ``’S``; an optional ``JOINT``; and a trailing ``(Case AUTH/...)`` scope.  The
# pattern below is still a headline grammar, not a body-text search: complete
# string, title-cased/all-capital name tokens, at most eight tokens, and the
# literal final word RESPONSE.  Corpus-wide it matches 269 headings; 259 are
# already RESPONSE tokens and the ten listed below are the complete missed set.
POSSESSIVE_RESPONSE_BOUNDARY_RE = re.compile(
    r"^[A-Z][A-Za-z0-9&.\-]*(?: [A-Z&][A-Za-z0-9&.\-]*){0,7}"
    r"(?:['’][Ss]|(?<=[Ss])['’]) (?:JOINT )?RESPONSE"
    r"(?: \(Case (?:AUTH|CASE)/\d{2,5}/\d{1,2}/\d{2,4}\))?$")
EXPECTED_POSSESSIVE_RESPONSE_BOUNDARIES = frozenset({
    ("AUTH-3779-6-23__AUTH-3780-6-23.html", 2773),
    ("AUTH-3779-6-23__AUTH-3780-6-23.html", 14951),
    ("AUTH-3824-09-2023.html", 3791),
    ("AUTH-3860-12-23.html", 3110),
    ("AUTH-3861-12-23.html", 6989),
    ("AUTH-3868-12-23.html", 2218),
    ("AUTH-3882-2-24.html", 13629),
    ("CASE-0215-06-24.html", 8603),
    ("CASE-0579-05-25__CASE-0580-05-25.html", 12352),
    ("CASE-0834-12-25.html", 8440),
})

# The older HTML export sometimes joins a bare all-capital RESPONSE marker to
# a complaint carrier with ``<br>``.  L1 records that line break, so L2 can use
# the source receipt instead of guessing from ordinary prose: the marker must
# be within one collapsed-text character of a recorded line break.  Excluding
# the explicit ``RESPONSE FROM [THE] COMPLAINANT`` form leaves exactly nineteen
# markers in eighteen files / nineteen cases; each was read as the transition
# from allegation to respondent submission.
INLINE_RESPONSE_RE = re.compile(r"\bRESPONSE\b")
RESPONSE_FROM_COMPLAINANT_RE = re.compile(
    r"RESPONSE\s+FROM\s+(?:THE\s+)?COMPLAINANT\b", re.I)
EXPECTED_INLINE_RESPONSE_BOUNDARIES = frozenset({
    ("AUTH-2126-5-08.html", 21821),
    ("AUTH-2275-11-09.html", 4418),
    ("AUTH-3218-6-19.html", 34834),
    ("AUTH-3219-6-19__AUTH-3220-6-19.html", 25276),
    ("AUTH-3263-11-19.html", 8008),
    ("AUTH-3365-7-20.html", 16598),
    ("AUTH-3378-9-20.html", 5807),
    ("AUTH-3418-11-20.html", 60704),
    ("AUTH-3488-3-21.html", 21127),
    ("AUTH-3498-3-21.html", 14552),
    ("AUTH-3528-6-21.html", 29417),
    ("AUTH-3544-7-21.html", 5269),
    ("AUTH-3592-12-21.html", 38918),
    ("AUTH-3592-12-21.html", 72433),
    ("AUTH-3624-3-22.html", 14124),
    ("AUTH-3672-6-22.html", 2832),
    ("AUTH-3717-12-22.html", 4493),
    ("AUTH-3743-2-23.html", 1833),
    ("AUTH-3812-8-23.html", 2082),
})

# One section has the inverse fused form: matter heading + reference sentence
# + ``<br>COMPLAINT``.  Starting at the marker would discard the report's own
# name and reference for matter 2, so this reviewed receipt promotes the matter
# heading itself.  Both offsets and the literal marker are checked on every
# build.  It restores AUTH/3522's stated 16764--17553 complaint span without
# changing L1.
REVIEWED_INLINE_COMPLAINT_BOUNDARIES = {
    "AUTH-3522-6-21.html": {
        "start": 16764, "marker": 17073, "marker_text": "COMPLAINT",
    },
}

# A title-case ``Complaint`` occurring after the report's RESPONSE boundary is
# an internal label in the respondent's reproduced letter, not a new PMCPA
# matter.  There are eight title-case headings corpus-wide: two occur before a
# response and remain real complaint boundaries; these six occur inside an
# active response and are the complete suppressed set.  Upper-case COMPLAINT
# remains untouched, preserving the older multi-matter cycles.
EXPECTED_NESTED_COMPLAINT_HEADINGS = frozenset({
    ("AUTH-3873-01-24.html", 2771),
    ("AUTH-3891-4-24.html", 1727),
    ("CASE-0224-07-24.html", 12072),
    ("CASE-0236-07-24.html", 13887),
    ("CASE-0246-07-24.html", 3051),
    ("CASE-0309-10-24.html", 21260),
})
POSSESSIVE_RESPONSE_BOUNDARY_FIRED = set()
INLINE_RESPONSE_BOUNDARY_FIRED = set()
INLINE_COMPLAINT_BOUNDARY_FIRED = set()
NESTED_COMPLAINT_HEADING_FIRED = set()

# Was VERBATIM from l1/derive.py. It DIVERGED on 2026-08-10 (DEFECTS R24) and
# the divergence is deliberate: derive's copy sets the abstract boundary and
# moving it would re-cut every abstract in the corpus, which is a separate
# round. Still copied rather than imported -- L2 must not start depending on
# l1's module surface -- and l2/validate.py now holds a genuinely INDEPENDENT
# reading (a token/window scan, not a re-typed regex; see its comment). The
# byte-identical copy it used to hold carried the same three holes, so the
# "two independent readings" guarantee was nominal for this pattern.
#
# The three holes, all in the second (breach-statement) branch, all proven by
# direct execution before the fix:
#
#   DECIMAL POINT. `[^.]{0,70}` cannot cross the '.' in a clause number, so
#   "No breach of Clause 2 was ruled" matched and "No breach of Clause 9.2 was
#   ruled" did NOT -- the corpus's commonest ruling form. _GAP allows a '.'
#   only where a digit follows it, which is exactly a decimal point and never
#   a sentence end (the reason `[^.]` was there at all).
#
#   PLURAL. `(?:Clause|the Code)\b` cannot match "Clauses": the \b fails
#   against the 's'. "No breach of Clauses 7.2 and 7.4 was ruled" was
#   unmatchable in every form.
#
#   SPELLING. Reports write "No breach of Code 7.2 was ruled" as well as
#   "Clause 7.2" (DEFECTS R5, AUTH/1816/3/06), and `the Code` needs the "the".
#
# Blast radius, measured over all 19,214 segments in data/l2/cases.jsonl
# before enabling: the fix flips exactly one complaint/response segment from
# clean to dirty (AUTH/1797/2/06's end-of-pane complaint, R24's own case) and
# moves no rendition cut at all (0 renditions contain a newly-visible match
# inside their existing span).
_GAP = r"(?:[^.]|\.(?=\d))"
_RULING_F1 = (r"\b(?:The\s+)?(?:Panel|Appeal Board)\b" + _GAP + r"{0,90}?"
              r"\b(?:rul\w*|consider\w*|noted|accept\w*|uph\w*|decid\w*)\b")
# `w(as|ere)\s+ruled` also wanted the two words adjacent, and 197 ruling
# sentences corpus-wide put an adverb between them -- thus 70, also 63,
# therefore 21, again 12, not 11, accordingly 5, and ten more with 1-2 each
# against 13,196 adjacent. One optional word, generic rather than a list, so
# the eleventh adverb is not a new hole; measured to change no segment's
# no_ruling_language either way.
_RULING_F2 = (r"\b(?:no\s+)?breach(?:es)?\s+of\s+(?:Clauses?|(?:the\s+)?Code)\b" + _GAP + r"{0,70}?"
              r"\bw(?:as|ere)\s+(?:\w+\s+)?ruled\b")
RULING_RE = re.compile(_RULING_F1 + "|" + _RULING_F2, re.I)
RULING_F2_RE = re.compile(_RULING_F2, re.I)

# A ruling sentence that names ANOTHER case is that case's ruling, quoted as
# precedent -- the legitimate class bench/generate.py's TRIPWIRE comment
# documents ("complaints cite OTHER cases' appeal decisions as precedent, 41
# items, verified 2026-08-02 to be citations, not outcomes"). Two shapes in
# the corpus, both hand-read:
#     "In case AUTH/3676/7/22, a breach of clause 25.3 was ruled"  (before)
#     "'... No breach of Clause 9.5 was ruled.' (ref AUTH/2052/10/07)"  (after)
# so the window is symmetric and 120 chars wide -- enough for the trailing
# "(ref ...)" attribution, short enough that it is still the same citation.
#
# The exemption is scoped to the F2 branch ON PURPOSE. F2 names no ruling
# body, so the neighbouring case number is the only thing that says WHOSE
# ruling it is. F1 names the Panel or the Appeal Board, and a party restating
# another case's Panel ruling inside a response is precisely the D3 hazard the
# attest exists to refuse. Measured: exempting F1 as well would flip 72
# complaint/response segments from dirty to clean -- 72 spans of text no audit
# has read, admitted by a leakage fix. F2-only flips 3, all hand-verified as
# precedent citations (AUTH/1854/6/06 "a breach of the Code was ruled (Case
# AUTH/1756/9/05)", AUTH/2831/4/16 "... Case AUTH/2454/11/11]",
# AUTH/3382/9/20 "... similar to those in Case AUTH/2918/12/16 in which no
# breach of the Code was ruled"), and it keeps AUTH/1797/2/06's real leak,
# which has no case number anywhere near it.
PRECEDENT_WINDOW = 120


def ruling_language(span, own_cases, file=None):
    """The first ruling-language match that is not another case's precedent.

    `own_cases` is the set of case numbers the SOURCE FILE is named for (L1's
    identity.filename_case_numbers), normalised; a citation of one of those is
    this case talking about itself and is never exempt.

    `file` admits the reviewed rows of RULING_LANGUAGE_FALSE_MATCHES, which
    name a match VERBATIM in a named document: a string this table declares is
    not an adjudicator speaking cannot be a leak of one. The exemption is by
    exact matched text, so it is position-independent and holds however the
    span was sliced.
    """
    known_false = RULING_LANGUAGE_FALSE_MATCHES.get(file, ())
    for m in RULING_RE.finditer(span):
        if m.group(0) in known_false:
            RULING_FALSE_MATCH_FIRED.add((file, m.group(0)))
            continue
        if RULING_F2_RE.fullmatch(m.group(0)):
            lo = max(0, m.start() - PRECEDENT_WINDOW)
            hi = min(len(span), m.end() + PRECEDENT_WINDOW)
            cited = {normalise_case_number(c) for c in CASE_NUM_IN_TEXT_RE.finditer(span[lo:hi])}
            if cited - set(own_cases):
                continue
        return m
    return None


def normalise_case_number(m):
    """CASE_NUM_IN_TEXT_RE match -> 'AUTH/1797/2/06'. Two-digit year, ints for
    the middle components: the corpus writes 'AUTH/2052/10/07', 'AUTH 2052/10/07'
    and 'CASE/0233/07/24' for the same shape."""
    prefix, serial, month, year = m.groups()
    return f"{prefix.upper()}/{int(serial)}/{int(month)}/{year[-2:]}"


# DEFECTS R26. The publisher's own headline statement of the outcome, in the
# headline block above the first body section -- the same region l1/derive
# scans for `banner_headings`, and these are the ones its literal rule misses.
#
# Three conditions, and the closed set they had to DECIDE is the 389 pre-body
# headings corpus-wide whose text contains a breach word (5,161 more are
# already banner_headings):
#
#   HEADLINE, NOT SENTENCE. `has_terminal_punctuation` is L1's own receipt.
#   It is what separates 'VPRIV press release breach Clause 2' from
#   'Bristol-Myers Squibb considered that it had breached Clause 14.1.'
#   (a voluntary-admission narrative) and 'As Chiesi was referred to within
#   the email, a breach of Clause 2 was alleged.' -- both party statements
#   inside the abstract's prose, neither an outcome.
#
#   NAMES A CLAUSE. ~55 of the 389 are the SUBJECT of a breach-of-undertaking
#   case ('Breach of undertaking', 'Alleged breach of undertaking',
#   'Promotion of Meriofert/breach of undertaking'): what the case is ABOUT,
#   not how it ended. Requiring a clause number leaves the four that state a
#   ruling.
#
# Measured before enabling: over all 19,214 segments the check fires on 1,027,
# of which 1,024 were already dirty for another reason. The three it newly
# catches are all `abstract_rendition`: AUTH/1888/9/06 and AUTH/2335/7/10
# ('Breach of undertaking Clause 2', 'Breach of undertaking Clause 2 breach')
# and AUTH/2528/8/12 ('VPRIV press release breach Clause 2'). AUTH/1866/7/06
# carries the same headline and was already refused on sanctions text.
OUTCOME_HEADLINE_BREACH_RE = re.compile(r"\bbreach(?:es|ed)?\b", re.I)
OUTCOME_HEADLINE_CLAUSE_RE = re.compile(r"\bclauses?\s*\d", re.I)
HEADLINE_STOPS_AT = ("COMPLAINT", "RESPONSE", "PANEL_RULING")

# Wave C, the SECOND outcome-bearing headline. R26's rule requires a clause
# number, so the publisher's other outcome headline slipped past it: 'CASE
# AUTH/2353/8/10 VOLUNTARY ADMISSION BY NAPP', which the abstract rendition
# then serves as its opening line under the P1/P3 rendition axis.
#
# `procedure.voluntary_admission` is named in SPEC §6b as a field a benchmark
# may never show, because it IS most of the label: over the 137 voluntary
# admission cases the bank's items run 277 breach to 21 no_breach (92.9%)
# against a corpus base rate of 38.7%. A headline that states it is the outcome
# in the headline, which is exactly what `no_outcome_heading` refuses.
#
# Two conditions decide the closed set -- all 208 pre-body headlines corpus-wide
# whose text contains the phrase:
#
#   HEADLINE, NOT SENTENCE, by L1's own `word_count` receipt rather than by the
#   `has_terminal_punctuation` one alone. Five of the 208 are prose paragraphs
#   the heading detector caught mid-sentence and so carry no full stop -- 'The
#   Panel noted Tesaro's admission that the seven items were only certifed ...'
#   (137 words), 'Britannia Pharmaceuticals Ltd made a voluntary admission
#   about incorrect prescribing information ...' (74), and three of 22-25. The
#   publisher's headline forms run 2 to 13 words. The bound is 15, inside a gap
#   with nothing in it, and refusing the five is also what keeps R26's decided
#   borderline: a party's ADMISSION SENTENCE is evidence, not a leak (the
#   AUTH/3286/12/19 line the fix wave deliberately did not swallow).
#   FOUR WORDS OR MORE. 75 of the 208 are the bare section heading 'VOLUNTARY
#   ADMISSION'. As a needle -- these are matched by CONTAINMENT against a
#   collapsed span -- a two-word phrase of ordinary case vocabulary would refuse
#   every span in the corpus that says 'X made a voluntary admission', i.e. the
#   admission sentences again. The distinctive forms are the ones that name the
#   company: 'VOLUNTARY ADMISSION BY NAPP', 'VOLUNTARY ADMISSION FROM COLONIS',
#   "CSL VIFOR'S VOLUNTARY ADMISSION", 'Voluntary admission regarding provision
#   of Olympics tickets to patients'.
OUTCOME_HEADLINE_PROCEDURE_RE = re.compile(r"\bvoluntary\s+admission\b", re.I)
OUTCOME_HEADLINE_MAX_WORDS = 15
OUTCOME_HEADLINE_MIN_WORDS = 4


def outcome_headline_needles(rec, der):
    """Collapsed needles for every outcome-stating headline in this record."""
    norm = {(s["pane"], s["index"]): s["heading_normalised"] for s in der["sections"]}
    out = set()
    for pane in ("report", "summary"):
        secs = [s for s in rec["sections"] if s["pane"] == pane]
        first_body = next((s["index"] for s in secs
                           if norm.get((pane, s["index"])) in HEADLINE_STOPS_AT), None)
        for sec in secs:
            if first_body is not None and sec["index"] >= first_body:
                break
            head = sec["heading_text"] or ""
            ev = sec["heading_evidence"] or {}
            if ev.get("has_terminal_punctuation", True):
                continue
            words = ev.get("word_count") or len(head.split())
            leaks = (OUTCOME_HEADLINE_BREACH_RE.search(head)
                     and OUTCOME_HEADLINE_CLAUSE_RE.search(head))
            if not leaks and OUTCOME_HEADLINE_PROCEDURE_RE.search(head) \
                    and OUTCOME_HEADLINE_MIN_WORDS <= words <= OUTCOME_HEADLINE_MAX_WORDS:
                leaks = True
            if leaks:
                n = needle(head)
                if n:
                    out.add(n)
    return sorted(out)
# From l1/build.py -- the sentence that introduces the outcome list.
OUTCOME_INTRO_RE = re.compile(r"The outcome under the .{0,40}?Code(?: of Practice)? was", re.I)

# A rendition shorter than this is not worth a benchmark item, and a very short
# leading span usually means the pane opens ON the outcome (a banner heading at
# char 0). Refused rather than shipped short.
MIN_RENDITION_CHARS = 200
# Outcome-table cell texts shorter than this are matched as substrings against
# whole narrative sections, where a short cell ('Breach of Clause 2',
# 'Misleading') would fire on ordinary prose. The check is meant to catch the
# TABLE leaking into a section, not the words the table happens to use.
MIN_TABLE_TEXT_CHARS = 12

# The 13 PDF substitutions are typeset, not marked up: there are no heading
# verdicts to read, so the flow is cut on the four literal upper-case markers,
# searched IN ORDER so that a later marker can never be found before an earlier
# one. Case-sensitive by design -- lower-case 'complaint' is the commonest word
# in the corpus.
PDF_MARKERS = (
    ("COMPLAINT", "complaint"),
    ("RESPONSE", "response"),
    ("PANEL RULING", "panel_ruling"),
    ("APPEAL", "appeal_ruling"),
)

# ---------------------------------------------------------------------------
# Verdict evidence (SPEC §5).
#
# A clause token is one or two dotted components ('2', '9.1'); the Code has
# never had a three-level number. Everything numeric that is NOT a clause must
# be removed BEFORE tokenising, because the flat lists really do carry case
# numbers and Code years inline:
#     '2 and 18.1, Audits. AUTH/1903/10/06 further Audit in January 2008'
#     '2, 9.1 and 18.1 (2003 Code)'
#     '7.2 (x7), 7.3 (x3), 7.4 (x4), 7.10, 9.1 (x2), 9.10 and 12.1'
# so case numbers, '(YYYY Code)' scopes and '(xN)' multipliers are stripped
# first and any surviving token whose leading component is 3+ digits is
# rejected. Without that, AUTH/1903/10/06 contributes clauses '10' and '06'.
# ---------------------------------------------------------------------------
CLAUSE_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
CASE_NUM_IN_TEXT_RE = re.compile(r"\b([A-Z]{3,})\s*/?\s*(\d{2,5})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b")
CODE_YEAR_SCOPE_RE = re.compile(r"\(\s*((?:19|20)\d{2})\s*Code\s*\)", re.I)
MULTIPLIER_RE = re.compile(r"\(\s*x\s*(\d+)\s*\)?|\bx\s*(\d+)\b", re.I)
# The 2016+ pages render each clause as a chip linking into the interactive
# Code, which is the ONLY place the corpus states which YEAR's clause was
# ruled on and the only place a clause slug exists at all.
INDEX_LABEL_RE = re.compile(r'<div[^>]*class="[^"]*index-label[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
CHIP_HREF_RE = re.compile(r'href="([^"]*)"', re.I)
# Four href shapes carry the year: '2021-interactive-...', '2024-interactive-...',
# 'interactive-2015-abpi-...', 'interactive-2014-code'. One '(19|20)\d{2}' scan
# reads all four; nothing else in these paths is a four-digit number.
CHIP_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
CHIP_SLUG_RE = re.compile(r"/(clause-[^/#?]+)")
TABLE_POLARITY_RE = re.compile(r"^\s*no\s+breach\b", re.I)
TABLE_BREACH_RE = re.compile(r"^\s*(?:no\s+)?breach\b", re.I)

# ---------------------------------------------------------------------------
# SPEC §5(e): ruling prose, read INSIDE the ruling segments only -- rewritten in
# l2.2 (DEFECTS D3).
#
# l2.1 read `(?<!no )breach(es) of Clauses? <list>` over whole segments. Two
# measured failures:
#
#   NEGATION. 'The Panel did not consider that there had been breaches of
#   Clauses 9.1 and 2 and ruled accordingly' is a NO-breach ruling written in
#   the negative, and the old pattern read it as a breach. So did 'no evidence
#   that ... had breached the Code' (294 sentences corpus-wide).
#
#   SPEAKER. The appellant's grounds and the Appeal Board's ruling are FULL of
#   restatements of the other body's ruling -- 'Novo Nordisk noted the Panel
#   also ruled a breach of Clause 7.8', 'The Appeal Board upheld the Panel's
#   ruling of a breach of Clause 8.1', 'In Case AUTH/2471/1/12, the Appeal Board
#   ... ruled no breach of Clause 2'. Attributing by SEGMENT alone therefore
#   credits the wrong body, which is the D3 defect in miniature.
#
# So a polarity statement is read sentence by sentence, in one of three frames,
# each carrying its own explicit negation slot -- there is no lookbehind and no
# guessing which 'breach' a stray 'no' belongs to:
#
#   F1 passive : '[no] breach(es) of Clause(s) X ... was/were ruled'
#   F2 active  : 'ruled [that there was] [a|no] breach(es) of Clause(s) X'
#   F3 uphold  : 'upheld the Panel's ruling(s) of [a|no] breach(es) of Clause(s) X'
#                -- the Appeal Board's commonest form by far (≈350 of 978
#                clause mentions in appeal_ruling segments) and the only way the
#                'upheld' class is readable from prose at all
#
# and the sentence's RULING BODY decides which set the statement joins (see
# `ruling_polarities`). A statement that names no body inherits the segment's.
#
# 'ruling of a breach' (a NOUN) is deliberately not a frame: the corpus uses it
# for rulings that were NOT made ('did not consider that the circumstances
# warranted a ruling of a breach of Clause 2', 8+ occurrences in appeal
# segments alone) as often as for ones that were.
#
# R17 refines that, and does not overturn it. The bare noun stays out. But the
# NEGATED form is not ambiguous at all -- 'did not consider that the
# circumstances warranted a ruling of a breach of Clause 2' IS a no-breach
# ruling on Clause 2, and reading it as silence lost real rulings. Measured
# over PANEL RULING spans: 177 (case, clause) pairs use the frame, 171 of them
# on Clause 2 (it is the standard formula for declining the censure clause).
# 110 are already attributed by another frame, so this adds nothing there;
# 20 have a verdict row with `panel: null` and are recovered; 47 have NO
# verdict row and stay unrecovered, because SPEC §5 is that prose ATTRIBUTES a
# row and never creates one -- an unruled Clause 2 is absent from the outcome
# lists, so there is nothing to attribute to. That limit is deliberate and is
# recorded in DEFECTS R17 rather than worked around here.
RULED_NOT_WARRANTED_RE = re.compile(
    r"did\s+not\s+(?:consider\s+that\s+)?[^.]{0,160}?"
    r"warrant(?:ed)?\s+a\s+ruling\s+of\s+a\s+breach\s+of\s+"
    r"Clauses?\s+(?P<list>\d{1,2}(?:\.\d{1,2})?)", re.I)
NO_PREFIX_RE = r"no\s+(?:further\s+|additional\s+|separate\s+|other\s+)?"
CLAUSE_ITEM_RE = r"(?:Clauses?\s+)?\d{1,2}(?:\.\d{1,2})?"
CLAUSE_LIST_RE = (
    rf"{CLAUSE_ITEM_RE}"
    # A full stop is a list separator in three reviewed ruling occurrences:
    # "Clauses 1.11. 9.1 and 2" (AUTH/3078 and AUTH/3079).  Ordinary sentence
    # splitting still stops at every other full stop; `ruling_sentence_spans`
    # below rejoins only that exact, guarded typo shape, so this does not turn
    # the next numbered matter into another clause.
    rf"(?:\s*(?:,|\.|and|or|&|,\s*and)\s*"
    rf"(?:consequently\s+|subsequently\s+|also\s+|further\s+)?{CLAUSE_ITEM_RE})*"
)
# DEFECTS R28 / audit round-2A finding N1. The 2026-08-10 fix wave gave
# RULING_RE -- the LEAKAGE pattern -- a generic adverb slot and the
# decimal-tolerant `_GAP`, and left the VERDICT-EVIDENCE patterns below on the
# old three-word enumeration and the old `[^.]`. So "No breach of Clause 15.4
# was thus ruled" created no evidence at all, and three cases published a Panel
# value their own ruling prose contradicts (AUTH/2026/7/07 7.2, AUTH/2220/3/09
# 15.4, AUTH/3476/2/21 14.1 -- the last with six sibling clauses already dual).
# The two character classes are brought to parity here.
#
# THE ADVERB SLOT. Generic, like RULING_RE's, rather than a longer list -- the
# eleventh adverb must not be a new hole. The closed set it has to decide is
# every word the corpus actually puts between 'was/were' and 'ruled' inside
# ruling prose, measured over all 2,004 cases' panel_ruling + appeal_ruling +
# appeal_comments segments: also 39, thus 32, therefore 13, not 6,
# accordingly 3, again 2, nonetheless 1, consequently 1, previously 1 -- nine
# words. Eight are connectives the statement survives unchanged. The ninth,
# `not`, INVERTS it ("However, a breach of Clause 2 was not ruled" --
# AUTH/3364/6/20, reciting AUTH/3287/12/19), so a generic slot would have read
# a no-breach sentence as a breach; `not` and `never` are therefore excluded
# from the slot rather than silently swallowed. Measured: exactly one sentence
# corpus-wide matches the frame with a negating adverb, the AUTH/3364 one, and
# it is in an `appeal_comments` segment naming the Panel, so it never reached a
# published row -- the guard is against the latent form, not a live defect.
_RULED_ADVERB = r"(?:(?!not\b|never\b)\w+\s+)?"

# An enumerated list is ONE statement about EVERY clause in it: 'no breach of
# Clauses 3.2, 9.1 and consequently Clause 2 were ruled' rules on three clauses.
RULED_PASSIVE_RE = re.compile(
    rf"(?P<neg>\b{NO_PREFIX_RE})?\bbreach(?:es)?\s+of\s+Clauses?\s+(?P<list>{CLAUSE_LIST_RE})"
    rf"{_GAP}{{0,60}}?\bw(?:as|ere)\s+{_RULED_ADVERB}ruled\b", re.I)
RULED_ACTIVE_RE = re.compile(
    rf"\bruled\s+(?:that\s+there\s+w(?:as|ere)\s+)?(?P<neg>{NO_PREFIX_RE})?"
    rf"(?:(?:a|an|any|the)\s+)?(?:(?:further|additional|separate|consequent)\s+)?"
    rf"breach(?:es)?\s+of\s+Clauses?\s+(?P<list>{CLAUSE_LIST_RE})", re.I)
# Wave C, the `of` after 'ruling(s)' is OPTIONAL. The report writes 'The Appeal
# Board upheld the Panel's ruling A BREACH of Clause 23.8' -- the publisher's
# own dropped preposition -- and the pattern required it, so AUTH/2308/4/10's
# Clause 23.8 had a Panel value and no Appeal Board one and its T3 candidate was
# excluded as unattributed. Measured over every panel_ruling / appeal_ruling /
# appeal_comments segment in the corpus, making it optional matches exactly ONE
# sentence the old pattern did not: that one.
RULED_UPHELD_RE = re.compile(
    rf"\bupheld\s+(?:the\s+)?(?:[A-Za-z’'\- ]{{0,24}}?\s)?rulings?\s+(?:of\s+)?(?P<neg>{NO_PREFIX_RE})?"
    rf"(?:(?:a|an|the)\s+)?breach(?:es)?\s+of\s+Clauses?\s+(?P<list>{CLAUSE_LIST_RE})", re.I)
# Wave C, the PASSIVE word order of the same statement: 'The Panel's ruling of a
# breach of Clause 9.9 was upheld. The appeal was thus unsuccessful.'
# (AUTH/2089/1/08). The active pattern above needs 'upheld' FIRST and cannot
# reach it. 12 sentences over 9 cases match, 10 of them in `appeal_ruling` and
# all 10 the Appeal Board disposing of an appeal.
#
# The other two are a party's CONDITIONAL in `appeal_comments` -- 'Takeda
# submitted that IF the Panel's ruling of a breach of Clause 7.2 WAS UPHELD it
# would have a significant impact ...' (AUTH/2367/10/10) and 'Accordingly, IF
# the Panel's rulings of breaches of Clause 7.2 and 7.3 WERE UPHELD (which were
# contested by Shire ...)' (AUTH/2528/8/12) -- which is R1's irrealis hazard in
# a new frame: a party arguing about a ruling is not a body making one. R1's
# guard catches the first (submitted-that ... would) and not the second, so the
# frame carries its own: a subordinating 'if' anywhere before the match refuses
# it. Measured over the same corpus sweep, that refuses exactly those two and
# no sentence in any `appeal_ruling` segment.
RULED_UPHELD_PASSIVE_RE = re.compile(
    rf"\brulings?\s+of\s+(?P<neg>{NO_PREFIX_RE})?(?:(?:a|an|the)\s+)?"
    rf"breach(?:es)?\s+of\s+Clauses?\s+(?P<list>{CLAUSE_LIST_RE})"
    rf"{_GAP}{{0,60}}?\bw(?:as|ere)\s+{_RULED_ADVERB}upheld\b", re.I)
UPHELD_CONDITIONAL_RE = re.compile(r"\bif\b", re.I)


def uphold_passive_match(sentence):
    """The first passive-uphold match that is not inside a conditional, or None.

    One implementation, used by both the statement reader and the body reader,
    so the guard cannot be applied in one and forgotten in the other."""
    for m in RULED_UPHELD_PASSIVE_RE.finditer(sentence):
        if UPHELD_CONDITIONAL_RE.search(sentence[:m.start()]):
            continue
        return m
    return None
RULED_FRAMES = (("passive", RULED_PASSIVE_RE), ("active", RULED_ACTIVE_RE),
                ("uphold", RULED_UPHELD_RE))

# -- l2.3 additions (DEFECTS residual work; the two T3 mislabels) ------------
#
# F4 COORDINATED. 'No breach of Clause 22 was ruled together with no breach of
# Clause 2' (AUTH/2008/6/07) states TWO rulings and the three frames above read
# only the first: the second conjunct carries its own polarity and clause but
# no verb of its own. Anchored on 'was ruled ... together with' so it can only
# fire as the tail of a ruling that has already been stated.
RULED_COORDINATED_RE = re.compile(
    rf"\bw(?:as|ere)\s+{_RULED_ADVERB}ruled\b\s*,?\s*"
    rf"(?:together\s+with|along\s+with|as\s+well\s+as)\s+"
    rf"(?P<neg>{NO_PREFIX_RE})?(?:(?:a|an|the)\s+)?"
    rf"breach(?:es)?\s+of\s+Clauses?\s+(?P<list>{CLAUSE_LIST_RE})", re.I)

# Assurance repair, receipt recall.  The active ruling can be followed by a
# second, separately polarised head with no second verb:
#
#   "ruled no breach of Clauses 13.1 and 9.1 and consequently no breach of
#    Clause 2"                                           (AUTH/3324/3/20)
#   "ruled no breach of Clause 6.1 and subsequently no breach of Clause 6.2"
#                                                         (AUTH/3696/10/22)
#
# It is read only when an existing frame has already read a ruling earlier in
# the same sentence.  That covers active and passive heads, including a small
# amount of matter/scope text between the first list and the tail, without
# treating a free-standing party assertion as a ruling.
RULED_CONNECTED_TAIL_RE = re.compile(
    rf"\b(?:and|or)\b\s+"
    rf"(?:(?:consequently|subsequently|also|further|then)\s+)?"
    rf"(?P<neg>{NO_PREFIX_RE})?(?:(?:a|an|the)\s+)?"
    rf"breach(?:es)?\s+of\s+Clauses?\s+(?P<list>{CLAUSE_LIST_RE})", re.I)

# The ruling clause can precede a terminal, clause-less disposition.  The
# reader accepts this only where the same sentence contains an explicit
# Clause(s) list before the connected "and/; no breach was ruled" tail.  A
# sentence-start "No breach was ruled" remains deliberately unresolved.
RULED_BARE_TERMINAL_RE = re.compile(
    rf"(?:\band\b|[;,])\s*(?P<neg>{NO_PREFIX_RE})"
    rf"breach(?:es)?\s+w(?:as|ere)\s+{_RULED_ADVERB}ruled\b", re.I)

# A second source idiom puts both the polarity and verb at the end and leaves
# the clause in the immediately preceding reasoning: "the requirements of
# Clause 15.5 had not been met and ruled a breach accordingly".  The last
# explicit Clause(s) list before the phrase is the antecedent.  The measured
# class has 17 ruling-prose occurrences; 16 carry such an antecedent and one
# says only "that clause", which stays refused.
RULED_ACCORDINGLY_RE = re.compile(
    rf"\bruled\s+(?P<neg>{NO_PREFIX_RE})?(?:(?:a|an|the)\s+)?"
    rf"breach(?:es)?\s+accordingly\b", re.I)

# One publisher typo drops "of": "; no breach Clause 19.1 ... was ruled"
# (AUTH/2779/7/15).  Requiring the semicolon/colon and the explicit negative
# head excludes ordinary "did not breach Clause X" reasoning.
RULED_MISSING_OF_PASSIVE_RE = re.compile(
    rf"(?:^|[;:])\s*(?P<neg>{NO_PREFIX_RE})breach(?:es)?\s+Clauses?\s+"
    rf"(?P<list>{CLAUSE_LIST_RE}){_GAP}{{0,80}}?"
    rf"\bw(?:as|ere)\s+{_RULED_ADVERB}ruled\b", re.I)

# F5 ANAPHORIC. '... contrary to the requirements of Clause 15.4 as alleged and
# the Panel ruled no breach of THAT CLAUSE' (AUTH/2823/2/16) and 'a ruling of
# Clause 2 ... was not warranted in this instance and no breach of THAT CLAUSE
# was ruled'. The clause is named earlier in the same sentence, so the frames
# carry no `list` group; `resolve_that_clause` supplies the antecedent, and
# refuses when the sentence names no clause before the pronoun. Never guessed
# from a neighbouring sentence: an anaphor whose antecedent is not in the
# sentence is not resolvable by this build, so it states nothing.
THAT_CLAUSE = r"th(?:at|is)\s+clause"
RULED_THAT_PASSIVE_RE = re.compile(
    rf"(?P<neg>\b{NO_PREFIX_RE})?\bbreach(?:es)?\s+of\s+{THAT_CLAUSE}"
    rf"{_GAP}{{0,60}}?\bw(?:as|ere)\s+{_RULED_ADVERB}ruled\b", re.I)
RULED_THAT_ACTIVE_RE = re.compile(
    rf"\bruled\s+(?:that\s+there\s+w(?:as|ere)\s+)?(?P<neg>{NO_PREFIX_RE})?"
    rf"(?:(?:a|an|any|the)\s+)?(?:(?:further|additional|separate|consequent)\s+)?"
    rf"breach(?:es)?\s+of\s+{THAT_CLAUSE}\b", re.I)
ANAPHORIC_FRAMES = (("passive_that_clause", RULED_THAT_PASSIVE_RE),
                    ("active_that_clause", RULED_THAT_ACTIVE_RE))
# The antecedent: the last 'Clause N' named before the pronoun in this sentence.
CLAUSE_NAMED_RE = re.compile(r"\bClauses?\s+(\d{1,2}(?:\.\d{1,2})?)", re.I)

# R1 IRREALIS. The appellant's grounds are full of sentences that name the
# Appeal Board and a ruling in the same breath while asking for one rather than
# reporting one -- 'The complainant sincerely HOPED THAT the Appeal Board
# considered the evidence ..., rejected the appeal and ruled breaches of
# Clauses 18.1 and 2' (AUTH/1902+1903, which put a phantom breach ruling on
# clause 18.1). A wish is not a ruling, so these never credit the Board.
IRREALIS_RE = re.compile(
    r"\b(?:hope[ds]?|hoping|request(?:ed|s|ing)?|ask(?:ed|s|ing)?|urge[ds]?|urging|"
    r"invite[ds]?|inviting)\s+that\b"
    r"|\bsubmitted\s+that\b[^.]{0,240}\bwould\b"
    r"|\brequested\s+(?:that\s+)?the\s+appeal\s+board\b",
    re.I)

# R2/F4 IMPERSONAL APPEAL RULING. The Appeal Board's decision is sometimes
# written with no subject at all -- 'no breach of Clause 2 was ruled. The
# appeal on this point was successful.' (AUTH/3483/3/21). Inside an appeal-side
# segment that sentence pair is the Board ruling and nothing else could be: the
# appeal outcome is stated in the same breath. Used only to supply a SPEAKER
# for a statement that names no body; it never overrides a named one.
APPEAL_DISPOSED_RE = re.compile(
    r"\bthe\s+appeal\s+(?:on\s+(?:this|that)\s+point\s+)?w(?:as|ere)\s+"
    r"(?:therefore\s+|thus\s+|accordingly\s+)?(?:un)?successful\b"
    r"|\bthe\s+appeal\s+(?:on\s+(?:this|that)\s+point\s+)?w(?:as|ere)\s+"
    r"(?:therefore\s+|thus\s+)?(?:not\s+)?(?:upheld|allowed)\b",
    re.I)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
PANEL_BODY_RE = re.compile(r"\bpanel\b", re.I)
APPEAL_BODY_RE = re.compile(r"\bappeal\s+board\b", re.I)
# A negator only counts when it ATTACHES to the statement -- 'no further breach
# of Clause 9.1 was ruled', 'as it did not a breach ... was ruled'. A flat
# 60-character window was tried first and measured: it rejected 420 of 2,728
# genuine breach statements (15%), because the corpus writes the FINDING in the
# negative and the RULING in the positive in one sentence --
#
#     'High standards had not been maintained; a breach of Clause 9.1 was ruled'
#     'As no prescribing information was included a breach of Clause 4.1 was ruled'
#
# -- and both are breaches. So the negator must sit within two words of the
# breach noun, and the sentences the audit named ('did not consider that there
# had been breaches of Clauses 9.1 and 2 and ruled accordingly', 'no evidence
# that ... had breached the Code') are already excluded a step earlier: neither
# is one of the three frames, because neither says the ruling was MADE.
NEGATOR_ATTACHED_RE = re.compile(r"\b(?:no|not|never|nor)\b(?:\s+\w+){0,2}\s*$", re.I)
NO_BREACH_INSIDE_RE = re.compile(r"\bno\s+breach", re.I)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
# Measured: all 1902 meta dates are DD/MM/YYYY -- the first field exceeds 12 on
# 1132 pages, the second never does, and the day agrees with the info-holder's
# spelled-out date on every page. The format is not ambiguous in this corpus.
META_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
INFO_DATE_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*$")

CASE_NUM_SPLIT_RE = re.compile(r"^([A-Z]+)/(\d+)/(\d+)/(\d+)$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def collapse(s):
    """Whitespace-collapsed, stripped. Never used where a value is stored
    verbatim -- only for comparing and for building fold keys."""
    return " ".join((s or "").split())


def strip_tags(s):
    """cludo:description is HTML-wrapped ('<p>Promotion of Arimidex</p>') on
    every page that has one, so a raw comparison against the hero h2 disagrees
    1894 times. Stripped, the real disagreement is 853."""
    return collapse(html_mod.unescape(TAG_RE.sub(" ", s or "")))


def canon(value, basis, sources, note=None):
    """A canonical value and its receipts.

    SPEC §2 sketches some of these as `{value, basis}`; §1.2 is the normative
    statement and asks for `sources` too. One shape for all of them is also what
    keeps the corpus to one key signature.
    """
    return {"value": value, "basis": basis, "sources": sources, "note": note}


def receipts_sha(sources):
    """The sha an adjudication is pinned to: the L1 slot values it was reviewed
    against. If any of them changes, the build fails rather than silently
    applying a decision made about different evidence (SPEC §1.1)."""
    payload = json.dumps(sources, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def case_sort_key(num):
    """Canonical case-number order. Within a prefix the serial is monotonic in
    time, so this is also chronological; AUTH (2003-2024) sorts before the CASE
    scheme (2024+), which is the order the corpus reads in."""
    m = CASE_NUM_SPLIT_RE.match(num)
    if not m:
        return ("￿", 0, 0, 0, num)
    prefix, serial, month, year = m.groups()
    return (prefix, int(serial), int(month), int(year), num)


def era_from_case_number(num):
    """The year the case was opened, from the case number's own suffix. All
    2004 case numbers carry a two-digit year, spanning 03-26."""
    m = CASE_NUM_SPLIT_RE.match(num)
    if not m:
        return None
    y = m.group(4)
    return int(y) if len(y) == 4 else 2000 + int(y)


def sha_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def needle(s):
    """A string reduced to the one form both sides of a containment test use:
    whitespace-collapsed and case-folded. The panes carry NBSP and newlines
    where the outcome table and the banner headings carry single spaces, so a
    raw substring test misses the leak it is looking for."""
    return " ".join((s or "").split()).casefold()


def loose_pattern(s):
    """A phrase as it might be re-wrapped inside a pane: every whitespace run
    matches any whitespace run. Used to LOCATE a banner heading (a cut needs an
    offset, which a collapsed-string containment test cannot give)."""
    words = collapse(s).split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(w) for w in words), re.I)


def parse_iso_date(meta_value, info_value):
    """(iso_from_meta, iso_from_info). None where the slot holds no date --
    including the 2 pages whose Completed slot reads 'Interim case report'."""
    out = []
    m = META_DATE_RE.match(meta_value or "")
    if m:
        d, mo, y = (int(x) for x in m.groups())
        out.append(f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None)
    else:
        out.append(None)
    m = INFO_DATE_RE.match(info_value or "")
    if m:
        d, name, y = m.group(1), m.group(2).lower(), m.group(3)
        mo = MONTHS.get(name)
        out.append(f"{int(y):04d}-{mo:02d}-{int(d):02d}" if mo and 1 <= int(d) <= 31 else None)
    else:
        out.append(None)
    return out[0], out[1]


# ---------------------------------------------------------------------------
# company names
# ---------------------------------------------------------------------------

def split_companies(value):
    """A respondent/complainant field into its named companies, in stated
    order. 'Roche and Chugai' -> two; 'Merck Sharp & Dohme' -> one."""
    s = value or ""
    holds = []
    for name in PROTECTED_COMPANY_NAMES:
        pat = re.compile(re.escape(name), re.I)
        m = pat.search(s)
        while m:
            holds.append(m.group(0))
            s = f"{s[:m.start()]}\x00{len(holds) - 1}\x00{s[m.end():]}"
            m = pat.search(s)
    parts = []
    for p in COMPANY_JOIN_RE.split(s):
        for i, held in enumerate(holds):
            p = p.replace(f"\x00{i}\x00", held)
        p = collapse(p)
        if p:
            parts.append(p)
    return parts


def fold_key(name):
    """The key that decides whether two strings are the same company.

    Accents folded ('ALK-Abello' / 'ALK-Abelló'), case folded, every
    non-alphanumeric run to a space (so '&', '.', '-' and '/' all vanish:
    'Daiichi-Sankyo' meets 'Daiichi Sankyo', 'Merck Sharp & Dohme' meets 'Merck
    Sharp and Dohme'), the standalone word 'and' dropped, then trailing
    legal-form, region-after-legal-form and industry words stripped.
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.casefold())
    toks = [t for t in s.split() if t != "and"]
    had_legal_form = False
    while toks and toks[-1] in LEGAL_SUFFIX:
        toks.pop()
        had_legal_form = True
    # Only a region that a legal form stood behind: see the note above on
    # 'Otsuka UK and Otsuka Europe'.
    if had_legal_form and len(toks) > 1 and toks[-1] in REGION_QUALIFIER:
        toks.pop()
    while len(toks) > 1 and toks[-1] in INDUSTRY_SUFFIX:
        toks.pop()
    key = " ".join(toks)
    return RESPONDENT_ALIASES.get(key, key)


def respondent_candidates(meta_respondent, h1_text):
    """(names, basis). The meta field first; the h1 tail where it names nobody.

    11 pages state no company in cludo:respondent -- 7 empty, 1 '.', 3 some form
    of bare 'voluntary admission'. One more reads 'Director v Novartis', which
    is a complainant-v-respondent string in the respondent slot.
    """
    raw = VOLUNTARY_PREFIX_RE.sub("", collapse(meta_respondent))
    if V_SPLIT_RE.search(raw):
        raw = V_SPLIT_RE.split(raw)[-1]
    names = [n for n in split_companies(raw) if fold_key(n)]
    if names:
        return names, "respondent_meta_folded"
    tail = collapse(h1_text)
    tail = tail.split(" - ", 1)[1] if " - " in tail else tail
    tail = VOLUNTARY_PREFIX_RE.sub("", tail)
    tail = V_SPLIT_RE.split(tail)[-1]
    names = [n for n in split_companies(tail) if fold_key(n)]
    if names:
        return names, "respondent_h1_fallback"
    return [], "respondent_unresolved"


# ---------------------------------------------------------------------------
# prose-first complainant metadata (l2.3)
# ---------------------------------------------------------------------------

def _anchored(sentence, match):
    """Is this token close enough to a mention of the complainant to be ABOUT
    the complainant? 90 characters, measured against the corpus' phrasing."""
    return any(abs(a.start() - match.start()) <= PROSE_ANCHOR_WINDOW
               for a in COMPLAINANT_ANCHOR_RE.finditer(sentence))


def role_category(role):
    """SPEC §4 category for a captured role phrase, or None.

    The role is the HEAD of the described phrase, so the rule that matches
    EARLIEST in the capture wins; the list order only breaks ties. l2.4 widened
    the capture from 90 to 200 characters (fix 3a) and list-order alone then
    let a word from the rest of the sentence outrank the stated role --
    'described themselves as an ex-employee of Cephalon UK [who] complained
    that a medical affairs manager ... was not a qualified doctor' read as
    health_professional. Six cases were wrong that way; position fixes all six
    and changes nothing that the 90-character capture got right.

    Lifted out of complainant_prose in wave C so that all five frames -- the
    two self-description ones, the two added there, and the narrator walk --
    resolve the capture by exactly one rule rather than three copies of it.
    """
    best = None
    for name, pat in PROSE_ROLE_RULES:
        hit = pat.search(role)
        if hit is not None and role_hit_is_company_name(role, hit):
            continue
        if hit is not None and (best is None or hit.start() < best[1]):
            best = (name, hit.start())
    return best[0] if best is not None else None


def complainant_prose(text):
    """What the case's own opening says about who complained.

    Returns anonymity, naming, contactability and a self-described role, each
    either stated or None -- never a default. `quotes` records the sentence
    fragment each verdict was read from, so the receipt can show its evidence.

    l2.4 changes two behaviours (bench/review/DEFECTS.md D6/D7):

      * `named` is collected as evidence in its own right. Anonymity used to be
        a two-valued field where False meant 'nothing said so', which is an
        inference from silence; False now needs a statement of its own.
      * contactability takes the FINAL state, not the state on receipt. 48
        files state it twice ('originally contactable but later became
        non-contactable', 'The complainant has now become non-contactable') and
        every one of them describes the same transition, contactable ->
        non-contactable; the corpus attests no transition the other way. Both
        statements are kept.

    Wave C adds three frames and a walk for `category`, in a SECOND PASS: the
    two self-description frames are tried over every sentence first, and the
    narrator-voice reading only runs where they read nothing anywhere in the
    opening. That ordering is what makes the change auditable -- all 521 values
    the old code produced are produced unchanged, byte for byte, and the delta
    is exactly the files that had no prose category at all. It is also the
    right precedence: what the complainant said about themselves outranks the
    publisher's shorthand for them.
    """
    # `quotes` carries all five keys always, null where nothing was read: SPEC
    # §7.1 requires ONE key signature across every case object, so a receipt
    # whose shape depends on what it found would split the corpus into dozens.
    out = {"anonymous": None, "named": None, "contactable": None, "category": None,
           "role_verbatim": None, "contactable_conflict": False,
           # Which frame read the role. Always present, null where none did --
           # the SPEC §7.1 one-key-signature rule applies to this receipt too.
           "category_frame": None,
           "quotes": {"anonymous": None, "named": None, "contactable": None,
                      "contactable_superseded": None, "category": None}}
    # Every contactability statement in the opening, in document order.
    statements = []
    # Preserve an already-sufficient adjectival receipt. The action frame below
    # was added to close nulls, not to re-source 19 correct False values.
    has_adjectival_noncontactability = bool(
        PROSE_NONCONTACTABLE_RE.search(text or ""))
    for sentence in SENTENCE_SPLIT_RE.split(text or ""):
        if not COMPLAINANT_ANCHOR_RE.search(sentence):
            continue

        if out["anonymous"] is None:
            # The two subject-scoping guards (see ANON_THIRD_PARTY_SOURCE_RE):
            # a `continue`-shaped skip like ANON_ABOUT_EVIDENCE_RE's, so the
            # loop keeps looking rather than latching on the wrong subject.
            hit = next((m for m in PROSE_ANONYMOUS_RE.finditer(sentence)
                        if _anchored(sentence, m)
                        and not ANON_ABOUT_EVIDENCE_RE.search(sentence[:m.start()])
                        and not ANON_THIRD_PARTY_SOURCE_RE.match(sentence, m.start())
                        and not ANON_GENERIC_PROCEDURE_RE.match(sentence, m.start())), None)
            if hit is None:
                hit = next((m for m in PROSE_REMAIN_ANON_RE.finditer(sentence)
                            if _anchored(sentence, m)), None)
            if hit is not None:
                out["anonymous"] = True
                out["quotes"]["anonymous"] = collapse(
                    sentence[max(0, hit.start() - 60):hit.end() + 40])

        if out["named"] is None:
            hit = next((m for m in PROSE_NAMED_RE.finditer(sentence)
                        if _anchored(sentence, m)), None)
            if hit is not None:
                out["named"] = True
                out["quotes"]["named"] = collapse(
                    sentence[max(0, hit.start() - 40):hit.end() + 60])

        # 'non-contactable' / 'uncontactable' are masked out before looking for
        # the bare word, so the two never read the same characters.
        masked = PROSE_NONCONTACTABLE_RE.sub(lambda m: "@" * len(m.group(0)), sentence)
        found = [(m, False) for m in PROSE_NONCONTACTABLE_RE.finditer(sentence)]
        if not has_adjectival_noncontactability:
            found += [(m, False) for m in PROSE_COULD_NOT_CONTACT_RE.finditer(sentence)]
        found += [(m, True) for m in PROSE_CONTACTABLE_RE.finditer(masked)]
        for m, value in sorted(found, key=lambda mv: mv[0].start()):
            if not _anchored(sentence, m):
                continue
            statements.append((value, collapse(
                sentence[max(0, m.start() - 90):m.end() + 60])))

        if out["category"] is None:
            for frame, category_frame in (
                    (PROSE_SELF_DESCRIBE_RE, "self_description"),
                    (PROSE_STATED_ROLE_RE, "self_description"),
                    (PROSE_AS_ROLE_COPULA_RE, "self_description"),
                    (PROSE_WRITING_AS_RE, "self_description"),
                    (PROSE_APPOSITIVE_DESCRIBED_ROLE_RE, "narrator_subject")):
                for m in frame.finditer(sentence):
                    best = role_category(m.group("role"))
                    if best is not None:
                        out["category"] = best
                        out["role_verbatim"] = collapse(m.group("role"))[:80]
                        out["quotes"]["category"] = collapse(
                            sentence[max(0, m.start() - 40):m.end()])
                        out["category_frame"] = category_frame
                        break
                if out["category"]:
                    break

    # -- second pass: narrator voice, only where nothing was self-described --
    #
    # TWO ROUNDS over the same sentences, differing only in which ones they
    # are allowed to look at (pre-freeze repair pass, 2026-08-10).
    #
    # Round 1 keeps the original gate, COMPLAINANT_ANCHOR_RE. Round 2 widens it
    # to the pass's OWN verb set and runs only where round 1 read nothing.
    #
    # The bug the second round fixes: this pass reads the SUBJECT OF A COMPLAINT
    # VERB, and its verb set has always included `alleged` and `queried`
    # alongside `complained` -- but the gate it inherited from the first pass
    # required the word `complain*` somewhere in the sentence. The first pass
    # needs that word (anonymity, naming and contactability are claims ABOUT
    # the complainant, so a sentence that never mentions one is not evidence);
    # this pass does not, because its own verb is already the evidence. So an
    # opening written as 'An anonymous consultant neurologist alleged ...' was
    # thrown away by a filter that exists for a different question.
    #
    # Two rounds rather than one widened gate, and the reason is measured. A
    # single widened gate ALSO re-sources 44 categories that were already
    # read: the widened set admits the report's title line -- which carries no
    # full stop, so the sentence splitter runs it into the first sentence of
    # the abstract -- and that sentence, being earlier, wins the break. The
    # value never changed, but the receipt did, from 'A principal hospital
    # pharmacist complained' to 'v SERVIER Alleged breach of undertaking A
    # principal hospital pharmacist alleged'. Trading 44 clean quotes for
    # heading furniture to gain 19 is not a fix. Ordering the rounds is the
    # same discipline wave C used when it added this pass in the first place:
    # everything the old code produced is produced unchanged, byte for byte,
    # and the delta is exactly the files that read nothing before.
    #
    # Measured delta, 19 cases, all of which had NO prose category at all:
    #   * 3 move the published value, all from `other`, because the meta slot
    #     had no vocabulary for what the report says in words --
    #     AUTH/2500/4/12 'an anonymous consultant neurologist alleged' and
    #     AUTH/2510/6/12 'an anonymous physician alleged' to
    #     health_professional, AUTH/2879/10/16 'an anonymous non-contactable
    #     member of the public alleged' to member_of_public.
    #   * 16 publish the same value they already did and gain a prose receipt
    #     for it, with `field_basis.category` moving from
    #     complainant_meta_vocabulary to complainant_prose_narrator_role --
    #     the precedence this function already declares, that what the report
    #     says outranks the publisher's shorthand.
    # No item is renamed: `category` is shown metadata, not part of item
    # identity.
    if out["category"] is None:
        for gate in (COMPLAINANT_ANCHOR_RE, NARRATOR_VERB_RE):
            for sentence in SENTENCE_SPLIT_RE.split(text or ""):
                if not gate.search(sentence):
                    continue
                for role, np_start, verb_end in narrator_roles(sentence):
                    best = role_category(role)
                    if best is not None:
                        out["category"] = best
                        out["role_verbatim"] = collapse(role)[:80]
                        out["quotes"]["category"] = collapse(
                            sentence[max(0, np_start - 40):verb_end])
                        out["category_frame"] = "narrator_subject"
                        break
                if out["category"]:
                    break
            if out["category"]:
                break

    # -- third pass: the measured recent-site passive ---------------------
    # This stays after both stronger forms above so adding it cannot re-source
    # any existing self-description or active narrator receipt.  Its exact
    # grammar and complete firing set are documented at the compiled pattern.
    if out["category"] is None:
        for sentence in SENTENCE_SPLIT_RE.split(text or ""):
            m = PROSE_PASSIVE_HEALTH_PROFESSIONAL_RE.search(sentence)
            if m is None:
                continue
            best = role_category(m.group("role"))
            if best is not None:
                out["category"] = best
                out["role_verbatim"] = collapse(m.group("role"))[:80]
                out["quotes"]["category"] = collapse(m.group(0))
                out["category_frame"] = "narrator_subject"
                break

    if statements:
        values = {v for v, _ in statements}
        if len(values) == 1:
            out["contactable"] = statements[0][0]
            out["quotes"]["contactable"] = statements[0][1]
        else:
            # The final state. Which statement is written LAST varies (the
            # summary line and the report opening are not always in the same
            # order -- AUTH/2816/12/15 states non-contactable first and
            # 'initially contactable but later could not be contacted'
            # second), so the transition itself decides rather than position:
            # all 48 files that state both describe a complainant who stopped
            # being contactable. The last statement of the losing value is
            # kept as the superseded one.
            out["contactable"] = False
            out["contactable_conflict"] = True
            out["quotes"]["contactable"] = next(
                q for v, q in reversed(statements) if v is False)
            out["quotes"]["contactable_superseded"] = next(
                q for v, q in reversed(statements) if v is True)
    return out


def complainant_prose_evidence(report_text, summary_text, body_starts_at):
    """The report's opening, then the summary pane as fallback (SPEC §6b task).

    The opening is everything before the first canonical body heading -- the
    title line, the banner, the case-summary block and the 'FULL CASE REPORT'
    paragraph that introduces the complainant. Where no body heading was
    measured the first 3,000 characters stand in. The sole full-report read is
    the exact, reviewed first-person request to keep the complainant's name
    anonymous; no generic anonymity phrase is admitted beyond the opening.
    """
    end = body_starts_at if isinstance(body_starts_at, int) and body_starts_at > 0 \
        else PROSE_OPENING_FALLBACK_CHARS
    stated = ("anonymous", "named", "contactable", "category")
    found = complainant_prose((report_text or "")[:end])
    found["source"] = "report_opening"
    if all(found[k] is None for k in stated):
        alt = complainant_prose(summary_text or "")
        if any(alt[k] is not None for k in stated):
            alt["source"] = "summary_pane"
            found = alt
    # The one reviewed exception to the opening-only window is an explicit
    # first-person request governing publication of the complainant's name.
    # Its exact measured grammar is documented at the compiled pattern above.
    if found["anonymous"] is None:
        request = PROSE_EXPLICIT_ANONYMITY_REQUEST_RE.search(report_text or "")
        if request is not None:
            found["anonymous"] = True
            found["quotes"]["anonymous"] = collapse(request.group(0))
            found["source"] += "+explicit_anonymity_request"
    return found


# ---------------------------------------------------------------------------
# segments and the leakage attest (SPEC §6)
# ---------------------------------------------------------------------------

# Response passages can legitimately repeat outcome-looking words while
# describing the RESPONDENT'S position: "did not breach", quoted table labels,
# or a party's account of an earlier ruling.  The assurance re-audit read each
# passage below end to end.  This is deliberately not a broader regex escape:
# every decision is pinned to the source file, the complete sliced-text hash,
# and the exact attest checks that the reviewed passage is allowed to clear.
# If either the segment boundary or any detector changes, the build refuses
# until the passage is read again.
RESPONSE_ATTEST_FALSE_POSITIVES = {
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
RESPONSE_ATTEST_FALSE_POSITIVE_FIRED = set()


def apply_response_attest_false_positive(span, file, checks):
    key = (file, sha_text(span))
    expected = RESPONSE_ATTEST_FALSE_POSITIVES.get(key)
    if expected is None:
        return
    failed = frozenset(name for name, passed in checks.items() if not passed)
    if failed != expected:
        raise SystemExit(
            "REFUSING: reviewed response-attest decision has drifted for "
            f"{file} {key[1]}: expected {sorted(expected)}, now {sorted(failed)}; "
            "re-read the response before trusting the row.")
    RESPONSE_ATTEST_FALSE_POSITIVE_FIRED.add(key)
    for name in expected:
        checks[name] = True


def check_response_attest_false_positive_registry():
    dead = sorted(set(RESPONSE_ATTEST_FALSE_POSITIVES) - RESPONSE_ATTEST_FALSE_POSITIVE_FIRED)
    if dead:
        raise SystemExit(
            "REFUSING: RESPONSE_ATTEST_FALSE_POSITIVES contains stale rows:\n  "
            + "\n  ".join(f"{file} {digest}" for file, digest in dead)
            + "\nRe-read the response before trusting the row.")

def leakage_attest(span, pane, start, end, ctx, kind, file=None):
    """The six checks of SPEC §6, computed on the SLICED text.

    The precision that matters: a complaint legitimately contains allegation
    vocabulary ('alleged a breach of Clause 9.1'), so `no_ruling_language` uses
    RULING_RE -- which requires a ruling VERB attached to the Panel or the
    Appeal Board, or an explicit 'was ruled' -- and never a bare 'breach of
    Clause'. A check that fired on allegations would refuse every complaint in
    the corpus and the refusal would look like rigour.

    `outside_abstract` is measured against the abstract of the SAME pane, and
    is vacuously true where no abstract was measured (SPEC §6c: the 102 pages
    with no measured boundary are refused, not guessed).

    `no_outcome_heading` is the sixth check, added 2026-08-10 for DEFECTS R26.
    `no_outcome_banner` matches the headings l1/derive RECORDED as banners, and
    derive's rule is a literal one -- NO BREACH / BREACH OF THE CODE / BREACH OF
    CLAUSE -- so the publisher's other headline word order slips through:
    AUTH/1888/9/06 heads its report 'Breach of undertaking Clause 2' and
    AUTH/2528/8/12 heads its 'VPRIV press release breach Clause 2', both of
    which the abstract_rendition then quotes as its opening line. See
    `outcome_headline` for the rule and the counts.
    """
    hay = needle(span)
    abstract = ctx["abstract"].get(pane)
    if kind in ("abstract", "abstract_rendition") or abstract is None \
            or pane not in ("report", "flow"):
        # A span cut FROM the abstract cannot be outside it; the check exists
        # to keep BODY segments out of the abstract, so for the two abstract
        # kinds it is vacuously true and `clean` stays meaningful (previously
        # 1,246/1,287 abstract_renditions failed this check and nothing else,
        # forcing consumers to read `checks` instead of `clean`).
        outside = True
    else:
        a_start, a_end = abstract
        outside = not (start < a_end and a_start < end)
    checks = {
        "no_ruling_language": ruling_language(span, ctx["own_case_numbers"], file) is None,
        "no_outcome_banner": not any(b in hay for b in ctx["banners"]),
        "no_outcome_table": not any(t in hay for t in ctx["table_texts"]),
        "outside_abstract": outside,
        "no_sanctions_text": not any(c in hay for c in ctx["chips"]),
        "no_outcome_heading": not any(h in hay for h in ctx["outcome_headings"]),
    }
    if kind == "response":
        apply_response_attest_false_positive(span, file, checks)
    return {"clean": all(checks.values()), "checks": checks, "checked_at_build": True}


def segment(kind, file, pane, start, end, pane_text, source, ctx):
    span = pane_text[start:end]
    return {
        "kind": kind,
        "ref": {
            "file": file, "pane": pane, "char_start": start, "char_end": end,
            "text_length": len(span), "text_sha256": sha_text(span),
        },
        "source": source,
        "leakage_attest": leakage_attest(span, pane, start, end, ctx, kind, file),
    }


# A RULING_RE match that is not ruling language, named verbatim, per file.
# Quote-pinned in the MATTER_SCOPE_REFUSALS idiom: the registry states the
# exact characters it was written against, and if the text there moves the
# build stops rather than silently let the cut run somewhere nobody read.
#
# BOTH consumers of RULING_RE honour it -- `rendition_cut`, which decides where
# a retelling stops, and `leakage_attest`'s `no_ruling_language`, which decides
# whether a span may be quoted at all. They have to agree: a cut that steps
# over a match the attest still counts produces a longer rendition that is then
# marked dirty and dropped, which is a worse outcome than the truncation it was
# meant to fix. One table, one reading, two readers.
#
# AUTH/2465/12/11 is the read case. Its F1 branch fires at summary 378 and
# report 454 on the ADVERTISEMENT'S OWN TYPOGRAPHY, quoted by the retelling:
#
#   "Beneath the heading 'Recommendations of the Consensus Panel' was a
#    diagram headed 'Qutenza may be considered for the treatment of ...'"
#
# `Panel` is the last word of a quoted advertisement heading and `considered`
# is the fourth word of the next quoted heading, 44 characters later, well
# inside F1's 90-character window. No adjudicator is speaking. The cost was
# the whole item: both renditions stopped at 378/454 characters, in the middle
# of the description of the advert, BEFORE the claim at issue -- the NICE
# treatment algorithm and the 'A suggested Drug Treatment Algorithm adapted
# from NICE Guidelines' tag that the complaint is entirely about. Dropping
# these two matches lets each cut resume at the next match, which is the
# genuine one and the same sentence in both panes: 'The Panel noted that the
# prominent title of the advertisement was ...' (summary 1820, report 1896).
# Every later match in both spans is a real Panel sentence, so one row per
# pane closes the case; there is no third.
#
# A REGISTRY and not a loosened pattern, for R24's reason restated: F1 names
# the Panel or the Appeal Board on purpose, and the corpus quotes those words
# in non-adjudicating positions often enough that any general exemption --
# 'skip a match inside quotation marks', 'require a sentence-initial Panel' --
# admits spans no audit has read. R24 measured the neighbouring version of
# that trade (exempting F1 from the precedent window flips 72 complaint and
# response segments from dirty to clean) and refused it. This registry moves
# TWO spans, both of which were read end to end.
#
# Blast radius on the attest, measured over the whole file: the registered
# string occurs three times in AUTH-2465-12-11.html -- summary 378, report 454
# and report 4305. The first two are inside the two renditions this exists for.
# The third falls in the gap between the abstract (ends 3828) and the complaint
# (starts 5000) and is inside NO segment at all, so no complaint, response or
# ruling segment's attest moves. The abstract and panel_ruling segments stay
# dirty on their real matches at 1896 and 8494. Exactly two attests change,
# both back to clean, and both spans were read end to end before the row was
# written -- so this is not R24's 72 unread spans admitted by a leakage fix.
RULING_LANGUAGE_FALSE_MATCHES = {
    "AUTH-2465-12-11.html": (
        # The publisher sets the summary pane with straight quotes and the
        # report pane with curly ones, so the same sentence is two strings.
        "Panel' was a diagram headed 'Qutenza may be considered",
        "Panel’ was a diagram headed ‘Qutenza may be considered",
    ),
}

# Which registry strings actually fired, filled during the build and checked by
# `check_ruling_false_match_registry` once every file has been read. A row that
# stops matching is a row written against text that has moved.
RULING_FALSE_MATCH_FIRED = set()


def check_ruling_false_match_registry():
    """Every RULING_LANGUAGE_FALSE_MATCHES string must have been found.

    The MATTER_SCOPE_REFUSALS discipline: a quote-pinned row may not survive
    the text moving under it. A row that no longer matches is not a harmless
    no-op -- it means the span it was written about has changed, and the match
    it was suppressing is now landing somewhere nobody has read.
    """
    dead = sorted((f, q) for f, quotes in RULING_LANGUAGE_FALSE_MATCHES.items()
                  for q in quotes if (f, q) not in RULING_FALSE_MATCH_FIRED)
    if dead:
        raise SystemExit(
            "REFUSING: RULING_LANGUAGE_FALSE_MATCHES names a ruling-language match that the "
            "build no longer finds where it was read:\n  "
            + "\n  ".join(f"{f}: {q!r}" for f, q in dead)
            + "\nRe-read the case before trusting the row.")


def rendition_cut(text, banner_patterns, file=None):
    """Where a leading rendition span has to stop.

    The summary pane and the report abstract are independently written accounts
    of the case that STATE the outcome, usually at the end. The quotable part is
    therefore the leading allegation span, cut before the first ruling language,
    the first 'The outcome under the ... Code was', or the first occurrence of
    one of this case's own outcome banner headings -- whichever comes first.

    `file` lets RULING_LANGUAGE_FALSE_MATCHES skip the reviewed false positives
    of one named document; every other file passes through untouched.
    """
    cuts = []
    known_false = RULING_LANGUAGE_FALSE_MATCHES.get(file, ())
    for m in RULING_RE.finditer(text):
        matched = m.group(0)
        if matched in known_false:
            RULING_FALSE_MATCH_FIRED.add((file, matched))
            continue
        cuts.append(m.start())
        break
    m = OUTCOME_INTRO_RE.search(text)
    if m:
        cuts.append(m.start())
    for pat in banner_patterns:
        m = pat.search(text)
        if m:
            cuts.append(m.start())
    return min(cuts) if cuts else len(text)


# ---------------------------------------------------------------------------
# DEFECTS R30, the half of the missing-segment class that is NOT a vocabulary
# problem. l1d.4 fixed the spellings (PANEL MINUTE / PANEL DECISION / PANEL'S
# RULING); this fixes the files where the words PANEL RULING are there, in
# capitals, and are simply not an element the heading finder could see. Two
# shapes, both from the same cause -- the publisher's <p> carrier swallowing
# the paragraph before the heading:
#
#   CARRIER TAIL   the heading candidate ENDS with the marker
#                  'Sanofi denied breaches of Clauses 15.1, 9.1 or 2 of the
#                   2019 Code. PANEL RULING'          (AUTH/3500/4/21)
#   INLINE         the marker is inside a section's body text, with no line
#                  break and no element of its own
#                  '... available at the conference. PANEL RULING The Panel
#                   noted that the Insights 2008 meeting ...' (AUTH/2174/10/08)
#
# THE CLOSED SET. 'PANEL RULING' occurs 2,493 times in report pane text.
# 2,468 of those are the first characters of a section heading and are already
# boundaries. The other 25 are this class, and all 25 were read. 23 open the
# Panel's ruling. TWO do not, and both fail in the same way: the marker is the
# OBJECT OF A PREPOSITION inside a longer all-capitals heading --
#
#   'RELEVANT EXTRACTS FROM THE PANEL RULING IN CASE AUTH/2168/9/08'
#                                                        (AUTH/2246/7/09)
#   'FURTHER INFORMATION FROM CHIESI FOLLOWING NOTIFICATION OF THE PANEL
#    RULING'                                             (AUTH/2618/7/13)
#
# -- the first a recital of ANOTHER case's ruling, the second an appeal-stage
# information section. So the rule is the one thing that separates them: the
# marker opens a section when it INTERRUPTS PROSE, and does not when it
# continues an unbroken run of capitals. Measured over the 25: 23 have a
# lowercase letter in the 25 characters before the marker, 2 do not, and the
# split is exactly the read one.
PANEL_RULING_MARKER = "PANEL RULING"
PANEL_MARKER_LOOKBACK = 25


def panel_ruling_text_markers(rec, der, sections_by_start):
    """[(char_start, 'panel_ruling')] for markers no heading boundary covers."""
    text = rec["panes"]["report"]["text"]
    out = []
    for m in re.finditer(re.escape(PANEL_RULING_MARKER), text):
        pos = m.start()
        sec = None
        for s in sections_by_start:
            if s["char_start"] <= pos < s["char_end"]:
                sec = s
                break
        if sec is None:
            continue
        if pos == sec["char_start"]:
            continue                      # the heading starts here: already a boundary
        if der.get(sec["index"]) == "PANEL_RULING":
            continue                      # l1d.4's vocabulary already opened this one
        before = text[max(0, pos - PANEL_MARKER_LOOKBACK):pos]
        if not any(c.islower() for c in before):
            continue                      # inside a longer all-capitals heading
        out.append((pos, "panel_ruling"))
    return out


def supplemental_html_boundaries(rec, known_starts):
    """Reviewed RESPONSE/COMPLAINT boundaries absent from l1d.4.

    Every returned offset is pinned either to a complete heading grammar plus
    its measured firing set, to L1's ``line_breaks`` receipt, or to the one
    explicitly reviewed complaint row above.  ``known_starts`` prevents a
    heading already named by L1 from being counted twice.
    """
    out = []
    for sec in rec["sections"]:
        if sec["pane"] != "report":
            continue
        head = (sec.get("heading_text") or "").strip()
        pos = sec["char_start"]
        if pos not in known_starts and POSSESSIVE_RESPONSE_BOUNDARY_RE.fullmatch(head):
            key = (rec["file"], pos)
            POSSESSIVE_RESPONSE_BOUNDARY_FIRED.add(key)
            out.append((pos, "RESPONSE"))

        text = sec.get("text") or ""
        breaks = sec.get("line_breaks") or []
        for match in INLINE_RESPONSE_RE.finditer(text):
            marker = sec["char_start"] + match.start()
            if marker in known_starts:
                continue
            if not any(abs(br - match.start()) <= 1 or abs(br - match.end()) <= 1
                       for br in breaks):
                continue
            if RESPONSE_FROM_COMPLAINANT_RE.match(text, match.start()):
                continue
            key = (rec["file"], marker)
            INLINE_RESPONSE_BOUNDARY_FIRED.add(key)
            out.append((marker, "RESPONSE"))

    reviewed = REVIEWED_INLINE_COMPLAINT_BOUNDARIES.get(rec["file"])
    if reviewed is not None:
        pane = rec["panes"]["report"]["text"]
        marker = reviewed["marker"]
        marker_text = reviewed["marker_text"]
        if pane[marker:marker + len(marker_text)] != marker_text:
            raise SystemExit(
                "REFUSING: reviewed inline COMPLAINT marker moved in "
                f"{rec['file']}: expected {marker_text!r} at {marker}, got "
                f"{pane[marker:marker + len(marker_text)]!r}; re-read the boundary.")
        start = reviewed["start"]
        INLINE_COMPLAINT_BOUNDARY_FIRED.add((rec["file"], start))
        if start not in known_starts:
            out.append((start, "COMPLAINT"))
    return out


def check_supplemental_boundary_coverage():
    """Refuse a changed firing set instead of silently changing segmentation."""
    checks = (
        ("possessive RESPONSE", EXPECTED_POSSESSIVE_RESPONSE_BOUNDARIES,
         POSSESSIVE_RESPONSE_BOUNDARY_FIRED),
        ("inline RESPONSE", EXPECTED_INLINE_RESPONSE_BOUNDARIES,
         INLINE_RESPONSE_BOUNDARY_FIRED),
        ("reviewed inline COMPLAINT",
         frozenset((f, row["start"])
                   for f, row in REVIEWED_INLINE_COMPLAINT_BOUNDARIES.items()),
         INLINE_COMPLAINT_BOUNDARY_FIRED),
        ("nested title-case Complaint", EXPECTED_NESTED_COMPLAINT_HEADINGS,
         NESTED_COMPLAINT_HEADING_FIRED),
    )
    failures = []
    for name, expected, fired in checks:
        if fired != expected:
            failures.append(
                f"{name}: missing={sorted(expected - fired)} extra={sorted(fired - expected)}")
    if failures:
        raise SystemExit(
            "REFUSING: supplemental section-boundary firing set changed; read every "
            "new or missing member before changing segmentation:\n  "
            + "\n  ".join(failures))


def matter_headings(rec, der, segments):
    """[(char_start, char_end, text)] -- the report's own MATTER headings.

    R28 stage 1. A multi-matter report names each matter before running the
    COMPLAINT / RESPONSE / PANEL RULING cycle again for it, and those names are
    the only descriptor of a `rulings` regard the corpus itself writes. Nothing
    here is composed: the value is a heading verbatim, with its offsets, and a
    consumer can re-slice it.

    The rule is six source-backed constraints conjoined, not a new classifier,
    and each filter was measured on the whole corpus:

      (a) the NEXT report section is a structural boundary (l1d's own
          normalised token) -- this is what makes it a MATTER heading rather
          than a subheading inside one. Alone it yields 3,632 headings over
          1,439 files, and its residue is body prose: the last paragraph of the
          abstract sits immediately before COMPLAINT on hundreds of pages.
      (b) L1's `has_terminal_punctuation` is false -- a headline, not a
          sentence (R26's rule uses the same receipt for the same reason);
          `matches_date_trailer` false, which drops CASE/0689's '07 April 2026'.
      (c) 1-40 words. The prose residue is long: 1,291 of the 3,632 run past 20
          words and every one sampled was a paragraph. 40 rather than 20
          because real matter headings quote the claim at issue and run long --
          AUTH/1941's matters 1 and 3 are 34 and 29 words.
      (d) an enumerator ('1', '2.', '10)'). This is the decisive one: it cuts
          2,309 to 642 and what it removes is page furniture and stray prose
          ('Teva', 'FINAL COMMENTS FROM COMPLAINANT', 'FULL CASE REPORT').
      (e) not a structural or page-furniture heading itself.
      (f) not strictly INSIDE a complaint/response segment.  A heading at the
          exact segment start may name the matter that the segment opens; a
          respondent's numbered subheading after that start cannot name what a
          later Panel ruling is "in regard to".  This segment-containment test
          removes the 37-case all-regards-in-response class (and 14 analogous
          all-in-complaint cases), without a case-number table.

    Before (f), the measured inventory was 642 headings over 216 files.  On the
    frozen pre-repair segment inventory, (f) removes 66 party subheadings across
    59 files (44 inside responses and 22 inside complaints); the repaired
    segment inventory is then authoritative at build time.  A ruling with no
    matter heading before it -- every single-matter report, and all 13 PDF
    substitutions, whose flow text carries no section structure at all -- gets
    `regard: null`. That is the honest value: the report states no name for the
    regard, and the quote is the receipt either way.
    """
    norm = {s["index"]: s["heading_normalised"] for s in der["sections"] if s["pane"] == "report"}
    secs = sorted([s for s in rec["sections"] if s["pane"] == "report"],
                  key=lambda s: s["char_start"])
    party_spans = [(s["ref"]["char_start"], s["ref"]["char_end"])
                   for s in segments
                   if s["ref"]["pane"] == "report"
                   and s["kind"] in ("complaint", "response")]
    party_starts = {start for start, _end in party_spans}
    out = []
    for i, s in enumerate(secs):
        token = norm.get(s["index"])
        if token in BOUNDARY_KIND or token in POSITIONAL_BOUNDARY_KIND \
                or token in HEADING_PAGE_FURNITURE:
            continue
        ev = s.get("heading_evidence") or {}
        if ev.get("has_terminal_punctuation") or ev.get("matches_date_trailer"):
            continue
        if not 1 <= (ev.get("word_count") or 0) <= MATTER_HEADING_MAX_WORDS:
            continue
        head = (s["heading_text"] or "").strip()
        if not MATTER_ENUMERATOR_RE.match(head):
            continue
        nxt = norm.get(secs[i + 1]["index"]) if i + 1 < len(secs) else None
        if nxt not in BOUNDARY_KIND and nxt not in POSITIONAL_BOUNDARY_KIND \
                and s["char_start"] not in party_starts:
            continue
        end = s["char_start"] + len(head)
        if any(start < s["char_start"] and end <= stop for start, stop in party_spans):
            continue
        out.append((s["char_start"], end, head))
    return out


def html_boundaries(rec, der):
    """(char_start, kind) for every canonical section boundary in the report
    pane, in char order. Kinds repeat; every occurrence is kept.

    l2.3 adds the two positional tokens (DEFECTS residual R2). l1d.3 NAMES
    'COMMENTS FROM <party>' and a standalone 'APPEAL BOARD CONSIDERATION'; this
    is where it is decided whether they open a section, because the answer
    depends on the document's stage structure and only L2 assembles that.

    APPEAL_BOARD_CONSIDERATION always opens the Board's own section: it names
    the ruling body, and there is no Panel-stage form of it.

    APPEAL_COMMENTS_HEADING is genuinely ambiguous and gets the measured test:
    it terminates the Panel's ruling only if it lies AFTER an 'APPEAL BY ...'
    heading, or between a Panel ruling and a later Appeal Board heading (the
    AUTH/1984/4/07 shape, where the appeal stage opens straight into the
    parties' comments with no grounds heading of its own). 237 of the 256
    occurrences qualify; the other 19 are Paragraph-17 / interim-case comments
    gathered at Panel stage ('COMMENTS FROM OTSUKA EUROPE ON THE REPORT FROM
    THE PANEL') and open nothing -- the heading stays inside the Panel's ruling
    where it belongs, exactly as before l2.3.
    """
    norm = {s["index"]: s["heading_normalised"] for s in der["sections"] if s["pane"] == "report"}
    tokens = []
    for s in rec["sections"]:
        if s["pane"] != "report":
            continue
        token = norm.get(s["index"])
        if token in BOUNDARY_KIND or token in POSITIONAL_BOUNDARY_KIND:
            tokens.append((s["char_start"], token))
    known_starts = {start for start, _token in tokens}
    tokens.extend(supplemental_html_boundaries(rec, known_starts))
    tokens.sort(key=lambda kv: kv[0])
    # R30. Markers the heading finder could not see, because the carrier
    # swallowed them. Added as PANEL_RULING tokens so they go through the same
    # ordering and the same APPEAL_COMMENTS_HEADING positional test below.
    report_sections = [s for s in rec["sections"] if s["pane"] == "report"]
    marker_starts = {p for p, _ in panel_ruling_text_markers(rec, norm, report_sections)}
    if marker_starts:
        known = {p for p, _ in tokens}
        tokens = sorted(tokens + [(p, "PANEL_RULING") for p in marker_starts - known],
                        key=lambda kv: kv[0])

    # Recent response letters contain their own title-case ``Complaint``
    # subheading.  l1d.4 (case-insensitively) names it COMPLAINT, but once a
    # real RESPONSE boundary has opened, that nested label cannot start a new
    # PMCPA complaint.  The all-capitals form used by genuine older
    # complaint/response cycles is deliberately unaffected.
    heading_at = {s["char_start"]: (s.get("heading_text") or "").strip()
                  for s in rec["sections"] if s["pane"] == "report"}
    filtered = []
    active = None
    for start, token in tokens:
        if token == "COMPLAINT" and active == "RESPONSE" \
                and heading_at.get(start) == "Complaint":
            NESTED_COMPLAINT_HEADING_FIRED.add((rec["file"], start))
            continue
        filtered.append((start, token))
        if token in BOUNDARY_KIND or token in POSITIONAL_BOUNDARY_KIND:
            active = token
    tokens = filtered

    grounds = [p for p, t in tokens if t == "APPEAL_GROUNDS"]
    panel = [p for p, t in tokens if t == "PANEL_RULING"]
    board = [p for p, t in tokens
             if t in ("APPEAL_BOARD_RULING", "APPEAL_BOARD_CONSIDERATION")]

    out = []
    for start, token in tokens:
        if token == "APPEAL_COMMENTS_HEADING":
            after_grounds = any(g < start for g in grounds)
            between_panel_and_board = (any(p < start for p in panel)
                                       and any(b > start for b in board))
            if not (after_grounds or between_panel_and_board):
                continue
        kind = BOUNDARY_KIND.get(token) or POSITIONAL_BOUNDARY_KIND.get(token)
        if kind:
            out.append((start, kind))
    return out


def pdf_boundaries(flow):
    """(char_start, kind) for the four literal markers, searched in order so
    that 'APPEAL' can never be found before 'PANEL RULING'."""
    out, pos = [], 0
    for marker, kind in PDF_MARKERS:
        m = re.compile(r"\b" + re.escape(marker) + r"\b").search(flow, pos)
        if m:
            out.append((m.start(), kind))
            pos = m.end()
    return out


def whole_sentence_ruling(body):
    """Is this L1 section, in its entirety, one ruling sentence?

    Not "does it contain ruling language" -- that is the attest's question and
    the answer there is to REFUSE the segment, never to trim it. This asks the
    segmentation question: is this section a stray fragment of the ruling
    column rather than part of the narrative the segment is cutting? A section
    whose whole text is 'No breach of Clause 9.2 was ruled.' is; a response
    section that happens to quote a ruling mid-paragraph is not, and stays
    inside the segment where the attest can refuse it.
    """
    s = (body or "").strip()
    if not s:
        return False
    m = RULING_RE.search(s)
    if not m or m.start() != 0:
        return False
    return s[m.end():].strip() in ("", ".", ".’", ".”", '."', ".'")


def quotable_tail_cut(rec, pane, pane_text, start, end):
    """Where a LAST-in-pane complaint/response segment really stops (R24).

    Every other segment ends at a boundary the document stated -- the next
    section heading. The last one ends at `len(pane_text)`, which is an
    assumption, and on a column-scrambled page it is wrong: AUTH/1797/2/06 is a
    two-column *Code of Practice Review* page flattened out of order (its PANEL
    RULING heading sits at 3534, its COMPLAINT at 4409), so the complaint
    segment ran to the end of the pane and swallowed the ruling fragment
    'No breach of Clause 9.2 was ruled.' (report 5219-5253) plus the case
    trailer -- and shipped it to four items, two of them with the verbatim
    answer to their own clause, all four in the TEST split.

    The cut is a BACKWARD scan over the L1 sections the segment contains, so it
    can only shorten the tail and can never truncate mid-narrative: it drops a
    contiguous trailing run of sections that are the document's date trailer
    (L1's own `matches_date_trailer` receipt) or whose whole text is one ruling
    sentence, and stops at the first section that is neither.

    Scoped to complaint/response because those are the kinds a model may be
    shown. Applying it to panel_ruling/appeal_ruling would move the offsets
    every T3 item is named by, and would edit the prose the verdict resolver
    reads, for no gain -- a trailer inside a ruling segment reveals nothing.

    Measured over the whole corpus: 18 segments move. One is AUTH/1797/2/06's
    complaint (97 chars: the ruling fragment plus both trailer lines) and it is
    the only one that was attest-clean. The other 17 lose 58-67 chars of date
    trailer from a response that stays dirty on its own ruling language.
    """
    secs = sorted((s for s in rec["sections"]
                   if s["pane"] == pane and s.get("char_start") is not None
                   and start <= s["char_start"] < end),
                  key=lambda s: s["char_start"])
    cut = end
    for sec in reversed(secs):
        body = pane_text[sec["char_start"]:min(end, sec.get("char_end") or end)]
        if (sec["heading_evidence"] or {}).get("matches_date_trailer") \
                or whole_sentence_ruling(body):
            cut = sec["char_start"]
            continue
        break
    return cut


def build_segments(rec, der, pdf, ctx):
    """Every segment for one FILE, plus the rendition indices and the ruling
    prose the verdict resolver needs.

    Multi-case files share these: the report is one document and its sections
    belong to every case reported in it. Emission order is fixed -- abstract,
    then body sections in char order, then the two renditions -- so the indices
    `renditions` carries are stable across builds.
    """
    file = rec["file"]
    summary_text = rec["panes"]["summary"]["text"]
    report_text = rec["panes"]["report"]["text"]
    banner_patterns = [p for p in (loose_pattern(b) for b in der["banner_headings"]) if p]

    segments = []
    renditions = {"summary": None, "report_abstract": None, "pdf_flow": None}
    # Kept apart by SEGMENT KIND, not merged into 'panel' and 'appeal': the kind
    # is what supplies the default speaker when a sentence names no body, and
    # the appellant's grounds get a different default from the Board's ruling.
    # R28 stage 1. Each entry is {file, pane, start, text}, not a bare string:
    # `rulings` records every attributed statement at its PANE offsets, and a
    # slice that has forgotten where it came from cannot supply them.
    prose = {"panel_ruling": [], "appeal_ruling": [], "appeal_comments": []}
    notes = []

    if pdf is not None:
        # C8/C9. The HTML report pane is empty, or belongs to another case
        # entirely; either way it is not this case's report, so it contributes
        # NOTHING here. The PDF flow is the report.
        flow = pdf["flow_text"]
        pdf_file = pdf["file"]
        bounds = pdf_boundaries(flow)
        if not bounds:
            notes.append("pdf_flow_without_markers")
        first = bounds[0][0] if bounds else len(flow)
        ctx["abstract"]["flow"] = (0, first)
        if first > 0:
            renditions["pdf_flow"] = len(segments)
            segments.append(segment("abstract", pdf_file, "flow", 0, first, flow, "pdf", ctx))
        for i, (start, kind) in enumerate(bounds):
            end = bounds[i + 1][0] if i + 1 < len(bounds) else len(flow)
            seg = segment(kind, pdf_file, "flow", start, end, flow, "pdf", ctx)
            segments.append(seg)
            if kind in prose:
                prose[kind].append({"file": pdf_file, "pane": "flow", "start": start,
                                    "text": flow[start:end]})
    else:
        boundary = der["abstract_boundary"]
        abstract_end = None
        if boundary["is_measured"] and isinstance(boundary["offset"], int):
            abstract_end = boundary["offset"]
            ctx["abstract"]["report"] = (0, abstract_end)
            segments.append(segment("abstract", file, "report", 0, abstract_end,
                                    report_text, "html", ctx))
        bounds = html_boundaries(rec, der)
        for i, (start, kind) in enumerate(bounds):
            if i + 1 < len(bounds):
                end = bounds[i + 1][0]
            else:
                # The last segment has no stated boundary; see quotable_tail_cut.
                end = len(report_text)
                if kind in ("complaint", "response"):
                    end = quotable_tail_cut(rec, "report", report_text, start, end)
            seg = segment(kind, file, "report", start, end, report_text, "html", ctx)
            segments.append(seg)
            if kind in prose:
                prose[kind].append({"file": file, "pane": "report", "start": start,
                                    "text": report_text[start:end]})

    # -- the two renditions ------------------------------------------------
    if summary_text:
        cut = rendition_cut(summary_text, banner_patterns, file)
        if cut >= MIN_RENDITION_CHARS:
            renditions["summary"] = len(segments)
            segments.append(segment("summary_rendition", file, "summary", 0, cut,
                                    summary_text, "html", ctx))
        else:
            notes.append("rendition_refused_short_span")
    if pdf is None and ctx["abstract"].get("report"):
        region_end = ctx["abstract"]["report"][1]
        cut = rendition_cut(report_text[:region_end], banner_patterns, file)
        if cut >= MIN_RENDITION_CHARS:
            renditions["report_abstract"] = len(segments)
            segments.append(segment("abstract_rendition", file, "report", 0, cut,
                                    report_text, "html", ctx))
        else:
            notes.append("rendition_refused_short_span")

    return segments, renditions, prose, notes


# ---------------------------------------------------------------------------
# verdict evidence, read off one file (SPEC §5 a-e)
# ---------------------------------------------------------------------------

def clause_tokens(text):
    """Clause numbers stated in a free-text list, in stated order, deduplicated.

    Everything numeric that is not a clause is removed FIRST -- see the note on
    CLAUSE_RE. A token whose leading component runs to three digits is a year or
    a case serial that survived, and is dropped rather than shipped as clause
    '1903'."""
    s = CASE_NUM_IN_TEXT_RE.sub(" ", text or "")
    s = CODE_YEAR_SCOPE_RE.sub(" ", s)
    s = MULTIPLIER_RE.sub(" ", s)
    out = []
    for tok in CLAUSE_RE.findall(s):
        head = tok.split(".")[0]
        if len(head) >= 3 or int(head) == 0:
            continue
        if tok not in out:
            out.append(tok)
    return out


# N2. A clause number as it appears in prose: not preceded by a digit or a dot
# (so '4.1' does not match inside '14.10'), and not followed by one (so '14'
# does not match the head of '14.1'). Distinct from CLAUSE_NAMED_RE, which
# carries the word 'Clause' and is used to resolve an anaphor inside one
# sentence; this one matches the bare number and the anchor is checked
# separately, because a list names its second and later clauses without
# repeating the word.
CLAUSE_IN_PROSE_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,2})?(?![\d]|\.\d)")
CLAUSE_ANCHOR_BEFORE_RE = re.compile(r"\b(?:Clause|Clauses|Code)\b", re.I)


def clause_names_in_text(text):
    """The clause numbers a report NAMES, as a set.

    N2 (round-2A). The outcome slots are hand-typed and mistype clause numbers
    -- '7.10' for the 10.1 that was ruled, '4.1' for 4.10, 'Paragraph 17 of the
    Constitution' read as Code Clause 17 -- and nothing checked the slot
    against the report. This is the check's witness: a number is NAMED when it
    is preceded, within 60 characters, by Clause / Clauses / Code. 60 because
    the corpus lists run long ('Clauses 2, 4.1, 4.3, 4.6, 4.8, 4.9, 4.10, 9.1,
    22.4, 26.1, 27.2, 27.9 and 28.1'), and the anchor because a bare '15.2' in
    a report is as often a page, a dose or a percentage.

    Deliberately NOT family-aware: '14.1' does not witness '14' and '12' does
    not witness '12.1'. Both directions occur in the corpus and both are worth
    seeing -- AUTH/2469/12/11's slot says 14 where the Panel ruled 14.1, and
    AUTH/2845/5/16's says 12.1 where the report argues Clause 12 throughout --
    so the guard reports them and a reader decides, which is the point.
    """
    out = set()
    text = text or ""
    for m in CLAUSE_IN_PROSE_RE.finditer(text):
        tok = m.group(0)
        head = tok.split(".")[0]
        if len(head) >= 3 or int(head) == 0:
            continue
        if CLAUSE_ANCHOR_BEFORE_RE.search(text[max(0, m.start() - 60):m.start()]):
            out.add(tok)
    return out


def parse_clause_chips(value_html):
    """[(clause, code_year|None, slug|None)] from the rendered chips.

    Authoritative where present: the chip is a LINK into a specific year's
    interactive Code, so it states the (year, clause) pair that the flat text
    can only imply. 5 chips corpus-wide link into the Constitution and
    Procedure instead of a clause page and carry no slug; the year is still
    read from the path.
    """
    out = []
    for m in INDEX_LABEL_RE.finditer(value_html or ""):
        block = m.group(1)
        label = collapse(html_mod.unescape(TAG_RE.sub(" ", block)))
        toks = clause_tokens(label)
        if not toks:
            continue
        href = CHIP_HREF_RE.search(block)
        year, slug = None, None
        if href:
            ym = CHIP_YEAR_RE.search(href.group(1))
            year = int(ym.group(0)) if ym else None
            sm = CHIP_SLUG_RE.search(href.group(1))
            slug = sm.group(1) if sm else None
        for tok in toks:
            out.append((tok, year, slug))
    return out


def parse_table_rows(rows):
    """The outcome table, reduced to one statement per (clause, polarity).

    The rows are rendered in BOTH panes (2,227 each, the same table twice), so
    they are deduplicated here; where the two copies state different '(xN)'
    multipliers the larger is kept, because a multiplier is a count of rulings
    and the table cannot state fewer than it shows.
    """
    seen = {}
    for row in rows:
        text = collapse((row.get("verdict_text") or "").replace("\xa0", " "))
        if not TABLE_BREACH_RE.match(text):
            continue
        polarity = "no_breach" if TABLE_POLARITY_RE.match(text) else "breach"
        ym = CODE_YEAR_SCOPE_RE.search(text)
        year = int(ym.group(1)) if ym else None
        mm = MULTIPLIER_RE.search(text)
        mult = int(mm.group(1) or mm.group(2)) if mm else None
        toks = clause_tokens(text)
        if not toks:
            continue
        clause = toks[0]
        key = (clause, year, polarity)
        prev = seen.get(key)
        if prev is None:
            seen[key] = {"clause": clause, "code_year": year, "polarity": polarity,
                         "multiplicity": mult, "verdict_text": text}
        elif mult is not None and (prev["multiplicity"] is None or mult > prev["multiplicity"]):
            prev["multiplicity"] = mult
    return sorted(seen.values(), key=lambda r: (r["clause"], str(r["code_year"]), r["polarity"]))


def governing_negator(sentence, match):
    """Is this positive-looking statement negated after all?

    Two ways, both measured against the corpus rather than imagined: a negator
    attached to the front of the statement, or a 'no breach' hiding inside the
    span it matched ('Nor had the complainant established a breach of Clause 2;
    no breach of that clause was ruled').
    """
    if NO_BREACH_INSIDE_RE.search(match.group(0)):
        return True
    return bool(NEGATOR_ATTACHED_RE.search(sentence[max(0, match.start() - 30):match.start()]))


def frame_statements(pat, sentence):
    """(match, polarity) for one frame, re-reading rather than discarding.

    The l2.2 loop used `finditer` and DROPPED any match that turned out to hold
    a 'no breach' inside its own span. That was right about the polarity and
    wrong about what to do next, because the inner 'no breach' is usually the
    real statement and the outer match had merely swallowed it:

        'The Appeal Board considered ... that a ruling of a breach of Clause 2
         was not warranted and no breach of Clause 2 was ruled.'

    matches from the FIRST 'breach of Clause 2' (the one that was refused) all
    the way to 'was ruled', so the whole sentence was discarded and the Board's
    actual ruling -- AUTH/3483/3/21, the audit's worked example -- was lost.
    Re-reading from the inner 'no breach' recovers it and cannot loop: the
    scan position strictly increases on every branch.

    R28/N1 adds the mirror of that rule for a statement that was NOT discarded.
    After a yield the scan resumes at the end of the clause LIST, not at the end
    of the match, because in the passive frame the verb sits behind a 60-char
    gap and everything in that gap was being consumed with it. Two sentences in
    the corpus put a whole second ruling there:

        'ruled no breach of Clause 7.2 and subsequently no breach of
         Clause 9.1 was ruled'                             (AUTH/3039/5/18)
        'No breach of Clauses 9.1, 15.2, 18.1 and consequently no breach of
         Clause 2 were ruled'                              (AUTH/2230/5/09)

    Both were half-read before (9.1 lost in the first once `_GAP` let the match
    reach across the decimal point; clause 2 lost in the second all along), and
    both are fully read now. It affects the passive frame ALONE: the active,
    uphold and coordinated frames end ON their clause list, so end-of-list and
    end-of-match are the same offset, and the anaphoric frames carry no list at
    all and keep the old step. Termination is unchanged -- the list is
    non-empty, so its end is strictly past the match start, which is at least
    `pos`.
    """
    pos, n = 0, len(sentence)
    has_list = "list" in pat.groupindex
    while pos <= n:
        m = pat.search(sentence, pos)
        if m is None:
            return
        step = max(m.end(), m.start() + 1)
        after_list = m.end("list") if (has_list and m.group("list") is not None) else step
        resume = max(after_list, m.start() + 1)
        if m.group("neg"):
            yield m, "no_breach"
            pos = resume
            continue
        inner = NO_BREACH_INSIDE_RE.search(m.group(0))
        if inner is not None:
            restart = m.start() + inner.start()
            pos = restart if restart > pos else m.start() + 1
            continue
        if NEGATOR_ATTACHED_RE.search(sentence[max(0, m.start() - 30):m.start()]):
            pos = step
            continue
        yield m, "breach"
        pos = resume


def resolve_that_clause(sentence, match):
    """The antecedent of 'that clause': the last clause NAMED before it here.

    Returns None when the sentence names no clause before the pronoun -- an
    unresolvable anaphor states nothing, and reaching into the previous
    sentence for one would be a guess.
    """
    last = None
    for m in CLAUSE_NAMED_RE.finditer(sentence, 0, match.start()):
        last = m.group(1)
    return last


def explicit_clause_list_before(sentence, end):
    """The last explicit Clause(s) list ending before ``end``.

    This is the bounded antecedent used by the two clause-less terminal ruling
    idioms below.  It never reaches into another sentence and never treats a
    bare number as a clause.
    """
    last = []
    pat = re.compile(rf"\bClauses?\s+(?P<list>{CLAUSE_LIST_RE})", re.I)
    for m in pat.finditer(sentence, 0, end):
        last = clause_tokens(m.group("list"))
    return last


def sentence_statements(sentence):
    """[(polarity, clause, frame)] stated by one sentence, in frame order.

    Five frames: the three l2.2 ones over an explicit clause list, plus the
    l2.3 coordinated tail ('... was ruled together with no breach of Clause 2')
    and the anaphoric pair ('... and no breach of that clause was ruled'),
    whose clause comes from the antecedent rather than from a list.

    Every clause in an enumerated list gets the statement's polarity. Returns []
    for a sentence that states nothing -- including every 'ruling of a breach'
    that was considered and refused.

    R28 stage 1 splits this out of `sentence_polarities` so `rulings` can record
    WHICH frame read each statement. The dedup key is still (polarity, clause) --
    two frames reading the same statement is one ruling, credited to the first
    frame that read it, and the frame order below is fixed so that is stable.
    """
    out = []

    def add(polarity, clause, frame):
        if clause and not any(p == polarity and c == clause for p, c, _ in out):
            out.append((polarity, clause, frame))

    ruled_anchors = []
    for name, pat in RULED_FRAMES + (("coordinated", RULED_COORDINATED_RE),):
        for m, polarity in frame_statements(pat, sentence):
            ruled_anchors.append((m.end(), polarity))
            for clause in clause_tokens(m.group("list")):
                add(polarity, clause, name)
    # A separately polarised tail belongs to a ruling only when a core frame
    # has already established one earlier in this sentence.  The source puts
    # these tails after both active and passive heads, sometimes with scope
    # words between the first clause list and the conjunction.
    for tail in RULED_CONNECTED_TAIL_RE.finditer(sentence):
        if not any(end <= tail.start() for end, _ in ruled_anchors):
            continue
        polarity = "no_breach" if tail.group("neg") else "breach"
        for clause in clause_tokens(tail.group("list")):
            add(polarity, clause, "connected_repeated_tail")
    # Wave C. The passive uphold frame is applied here rather than in
    # RULED_FRAMES because it carries a guard the others do not: a conditional
    # 'if' before the match makes the sentence a party's argument about a
    # ruling, not a body's ruling (see RULED_UPHELD_PASSIVE_RE).
    if uphold_passive_match(sentence) is not None:
        for m, polarity in frame_statements(RULED_UPHELD_PASSIVE_RE, sentence):
            for clause in clause_tokens(m.group("list")):
                add(polarity, clause, "uphold_passive")
    for name, pat in ANAPHORIC_FRAMES:
        for m, polarity in frame_statements(pat, sentence):
            add(polarity, resolve_that_clause(sentence, m), name)
    # R17. Unconditionally negative, so it does not go through frame_statements'
    # polarity machinery: 'did not ... warrant a ruling of a breach of Clause N'
    # can only be a no-breach ruling on N. Applied after the frames above so a
    # sentence that also states a positive ruling keeps that too.
    for m in RULED_NOT_WARRANTED_RE.finditer(sentence):
        for clause in clause_tokens(m.group("list")):
            add("no_breach", clause, "not_warranted")
    # Clause-less terminal dispositions.  Both are accepted only with an
    # explicit same-sentence antecedent; otherwise they add nothing.
    for m in RULED_BARE_TERMINAL_RE.finditer(sentence):
        for clause in explicit_clause_list_before(sentence, m.start()):
            add("no_breach", clause, "bare_terminal")
    for m in RULED_ACCORDINGLY_RE.finditer(sentence):
        polarity = "no_breach" if m.group("neg") else "breach"
        for clause in explicit_clause_list_before(sentence, m.start()):
            add(polarity, clause, "accordingly_antecedent")
    for m in RULED_MISSING_OF_PASSIVE_RE.finditer(sentence):
        for clause in clause_tokens(m.group("list")):
            add("no_breach", clause, "missing_of_passive")
    return out


def sentence_polarities(sentence):
    """[(polarity, clause)] stated by one sentence -- `sentence_statements`
    without the frame name, which is what every caller but `rulings` wants."""
    return [(polarity, clause) for polarity, clause, _ in sentence_statements(sentence)]


def ruling_body(sentence, default):
    """Which body this sentence says did the ruling.

    `default` is the body the SEGMENT implies -- a bare 'A breach of Clause 7.2
    was ruled' inside PANEL RULING is the Panel speaking. A sentence naming both
    bodies is refused unless it is the uphold frame, where the Appeal Board is
    unambiguously the one ruling on the Panel's ruling. `None` = refuse.
    """
    panel = bool(PANEL_BODY_RE.search(sentence))
    board = bool(APPEAL_BODY_RE.search(sentence))
    if board and RULED_UPHELD_RE.search(sentence):
        return "appeal_board"
    # Wave C. The passive uphold names only the PANEL -- "The Panel's ruling of
    # a breach of Clause 9.9 was upheld." -- and the body that upheld it is the
    # one this sentence credits. Without this the polarity went to `panel`,
    # which the case already had from its own ruling prose, and the Appeal
    # Board side stayed null (AUTH/2089/1/08, a T3 candidate excluded as
    # unattributed). The conditional guard is applied where the statement is
    # read, in sentence_statements, so a sentence refused there never reaches
    # this function with an uphold statement to credit.
    if panel and not board and uphold_passive_match(sentence) is not None:
        return "appeal_board"
    if panel and board:
        return None
    if board:
        return "appeal_board"
    if panel:
        return "panel"
    return default


# ---------------------------------------------------------------------------
# DEFECTS R28 / audit round-2A lead 4. THE UNDERTAKING-SUBJECT EXEMPTION.
#
# `ruling_polarities` skipped every sentence naming a case number that is not
# this page's own, because such a sentence is normally another case's ruling
# recited as precedent. On the breach-of-undertaking clause that rule is
# exactly backwards. An undertaking is given IN an earlier case and breached in
# THIS one, so this case's own ruling cannot be stated without naming the other
# case:
#
#     'In the Panel's view this represented a breach of the undertaking given
#      in Case AUTH/2168/9/08 and thus a breach of Clause 25 was ruled.'
#
# That is AUTH/2246/7/09's own Panel ruling. Discarding it left the row
# published as `panel: no_breach` -- against the case's own outcome list, which
# records 25 under BREACH -- and shipped two items with the wrong label.
#
# THE CLOSED SET, and how the rule decides it. Sweeping every ruling-prose
# sentence in the corpus that (a) names a foreign case number and (b) states a
# polarity, there are 81 such sentences over 69 cases -- the whole population
# the guard acts on. The rule reads each foreign case number and asks one
# question: is it the object of an UNDERTAKING phrase ('the undertaking given
# in Case X', 'undertakings given in Cases X and Y', 'its undertaking in
# Case X', 'the undertaking signed in Case X', 'undertaking provided in
# Case X', 'undertaking given by Bayer in Case X', 'undertaking given in
# September 2021 for Case X')?
#
#     37 sentences  ALL foreign numbers undertaking-anchored
#     42 sentences  NONE anchored                             -> SKIP (as before)
#      2 sentences  SOME anchored, some not                   -> REFUSED, named
#
# THE SENTENCE-LOCAL RULE IS NOT SUFFICIENT ON ITS OWN, and the corpus proves
# it. AUTH/2833/4/16 writes
#
#     'In Case AUTH/2817/12/15 ALK-Abello complained on 23 December 2015 that
#      Emerade continued to be described as "new" on the product website. The
#      Panel considered that Bausch & Lomb had failed to comply with its
#      undertaking given in Case AUTH/2802/11/15 and breaches of Clauses 2, 9.1
#      and 29 were ruled. Turning to the case now before it, Case
#      AUTH/2833/4/16, ...'
#
# -- a sentence in AUTH/2817/12/15's shape, word for word, that is a RECITAL of
# AUTH/2817's ruling inside AUTH/2833's preamble. It is indistinguishable from
# AUTH/2246's genuine one without reading the narrative around it. So all 37
# were read IN CONTEXT (900 characters of preceding segment each) and the
# reading is recorded below: 36 are the case's own ruling on the
# breach-of-undertaking clause (25 in the 2006/2008 Codes, 29 in 2011-2019, 3.3
# in 2021) plus the consequential 9.1/2 rulings that travel with it; 1 is the
# recital above. The registry is what makes the rule DECIDE the slot rather
# than merely match most of it: an anchored sentence that is not in it stops
# the build, because the only way to tell the two apart is to read.
#
# The unanchored sentences were left as a READ LIST by that wave -- 'a measured
# residue of 8 that ARE this case's own ruling with an incidental foreign
# reference the rule cannot prove inert' -- and skipped, on the ground that a
# missing prose receipt asserts nothing.
#
# READ 2026-08-11 (DEFECTS D3/N2 reading round, decision 4), and the read
# list is now a decided registry, `PRECEDENT_CITATION_READ` below. Two deltas
# from the wave's note, per the docs rule:
#
#   * the population is 30 sentences, not 42. 42 counted every citation
#     sentence that states a polarity; the guard only COSTS something on a
#     sentence a body would also have owned, so `ruling_polarities` now applies
#     the precedent skip below the body and polarity tests and the registry
#     covers exactly what survives them.
#   * AUTH/3641/4/22 is NOT one of this case's own rulings, and the wave's list
#     of 8 was wrong about it. Its sentence ends '... and the Panel therefore
#     ruled a breach of Clause 4.6 of the 2019 Code IN THAT CASE (Case
#     AUTH/3446/12/20)', and the next sentence opens 'Turning to the current
#     case, Case AUTH/3641/4/22'. It is a recital, and Clause 4.6 is not in
#     AUTH/3641's outcome slots either. 7 of the 8, not 8.
_CASE_NUM_BARE = r"\b[A-Z]{3,}\s*/?\s*\d{2,5}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b"
UNDERTAKING_CASE_RE = re.compile(
    rf"\bundertakings?\b" + _GAP + rf"{{0,90}}?\b(?:in|for|under)\s+(?:the\s+)?"
    rf"(?:Cases?\s+)?(?P<list>{_CASE_NUM_BARE}"
    rf"(?:\s*(?:,|and|&)\s*(?:Cases?\s+)?{_CASE_NUM_BARE})*)", re.I)

# The sentences whose foreign case numbers are PART anchored and part not.
# Same key and same value vocabulary as the other two registries since
# 2026-08-11: (case, sha12 of the collapsed sentence) -> None to READ, or a
# string saying why it is refused. It used to key on the foreign numbers in
# stated order, which is weaker -- a case can cite the same numbers in two
# sentences -- and it used to hold reason strings only, because a mixed
# sentence was always skipped.
#
# Both entries are now READ. The wave that wrote them said so in terms ('the
# ruling ... is this case's, so the skip costs a true reading -- accepted') and
# then kept the skip because the disposal had no read setting; it does now.
# Neither reading moves a value: both cases are unappealed and the sentence
# states the polarity its own outcome slot already publishes (AUTH/3664 cl 3.3
# no_breach, AUTH/3769 cl 3.3 breach). What they add is the prose receipt --
# and, on AUTH/3769, the receipt for a breach-of-undertaking ruling that is the
# whole subject of the case.
PRECEDENT_MIXED_DECIDED = {
    ("AUTH/3664/6/22", "951222ebdf24"): None,
    # 'the steps taken by AstraZeneca to comply with its undertaking given in Case
    # AUTH/1800/2/06' is anchored; the same number recurs as 'the actions in Case
    # AUTH/1800/2/06 related to those of a representative', a fact about the earlier
    # case. Read in context: the sentence is this Panel DISTINGUISHING the earlier
    # case and ruling on the clause before it -- '... considered that the cases were
    # sufficiently different such that AstraZeneca was not in breach of the
    # undertaking given in Case AUTH/1800/2/06, and thus no breach of Clause 3.3 was
    # ruled' -- and the next sentence continues 'The Panel consequently ruled no
    # breach of Clause 5.1 and Clause 2 in this regard.'
    ("AUTH/3769/5/23", "9ee0996bb141"): None,
    # 'breached the undertaking given in September 2021 for Case AUTH/3488/3/21' is
    # anchored; '(Case AUTH/3585/11/21)' is a parenthetical dating the material.
    # '... the Panel therefore ruled a breach of Clause 3.3' is this case's ruling;
    # the next sentence is its consequence ('by failing to comply with it,
    # AstraZeneca had failed to maintain high standards').
}

# THE CITATION REGISTRY (2026-08-11). Every sentence the precedent guard skips
# as a citation that a body would otherwise have owned -- the whole population
# the skip can cost anything on. Same key and value vocabulary as
# UNDERTAKING_SUBJECT_READ: None to READ it as this case's own ruling, a string
# to refuse it with the reason.
#
# 30 members, 23 refused and 7 read. The refusals are the ordinary shapes: a
# recital of another case's ruling inside this Panel's reasoning, a party
# citing a precedent in its submission or grounds, and a conditional ('if the
# Appeal Board upheld ...'). The 7 reads are sentences whose foreign case
# number is the SUBJECT of what was done -- the response that was inadequate,
# the earlier complaint whose allegations these rulings are said to cover, the
# prior rulings being weighed for Clause 2 -- and whose ruling verb belongs to
# this case. Four of the seven name THIS case in the same sentence.
#
# Verdict-row consequences, measured before and after: 6 of the 7 change no
# published value (AUTH/1950's clause 2 has no verdict row at all; AUTH/3123's
# and AUTH/3355's 9.1 rows already carry the same polarity from another
# sentence; AUTH/3647, AUTH/3699 and AUTH/3738 are unappealed rows whose
# list-derived polarity the sentence agrees with). The seventh, AUTH/3763/4/23
# clause 28.5, is an APPEALED case whose row had no Panel attribution at all
# (`verdict_appealed_unattributed`), so reading it attributes the Panel ruling.
PRECEDENT_CITATION_READ = {
    # -- read: this case's own ruling ------------------------------------
    ("AUTH/1950/1/07", "4c653310cd77"): None,
    # 'Taking all the circumstances into account and bearing in mind its rulings in
    # the previous case, Case AUTH/1899/10/06, the Panel did not accept that the
    # cumulative effect of the Panel's rulings at points 1, 2 and 3 above and the
    # previous case were ... sufficient to warrant a ruling of a breach of Clause 2'
    # -- the censure idiom, about THIS case; the foreign number is one of the things
    # weighed. Next sentence: 'APPEAL BY COMPLAINANT The complainant was surprised
    # that Clause 2 was not ruled.' No clause-2 verdict row exists (the slots name
    # only 7.2 and 15.9), so the reading adds a receipt to nothing.
    ("AUTH/3123/11/18", "7f6907f5f8d2"): None,
    # '... Otsuka Europe had failed to maintain high standards by not supplying all
    # the relevant information in its response to Case AUTH/3041/6/18 and a breach of
    # Clause 9.1 was ruled.' The foreign case is the one whose RESPONSE was
    # inadequate; the breach is this case's, and its own slots list 9.1 under breach.
    ("AUTH/3355/5/20", "bb4fd232a630"): None,
    # 'The Panel considered that Britannia's failure to refer to the contract with
    # the second health professional in Case AUTH/3302/1/20 meant that it had failed
    # to maintain high standards and a breach of Clause 9.1 was ruled.' Same shape.
    ("AUTH/3647/5/22", "74f5cfa07962"): None,
    # '... the Panel considered that the matter in relation to uncertified
    # promotional material in this voluntary admission (Case AUTH/3647/5/22) was
    # adequately covered by its rulings of breaches of Clauses 14.1 and 9.1 above,
    # and thus the Panel ruled no breach of Clause 2.' Names THIS case.
    ("AUTH/3699/10/22", "0875aac7ac81"): None,
    # 'The Panel therefore considered the current case (Case AUTH/3699/10/22)
    # differed in nature and was ... sufficiently different to Case AUTH/3229/7/19;
    # no breach of Clause 3.3 was ruled.' Names THIS case.
    ("AUTH/3738/2/23", "53f0159aba86"): None,
    # '... the Panel considered that the matter in relation to this voluntary
    # admission (uncertified versions of the hub) was adequately covered by its
    # rulings of breaches of Clauses 5.1 and 8.3 above and therefore ... ruled no
    # breach of Clause 2.'
    ("AUTH/3763/4/23", "431c75200625"): None,
    # 'Noting its comments above that Novo Nordisk had not commented on whether it
    # could identify the health professionals at issue in Case AUTH/3525/6/21, that
    # the complainants bore the burden of proof, and the Panel's rulings of no breach
    # of Clauses 24.8 (2019 Code) and 28.4 (2021 Code) above, the Panel did not
    # consider that Clause 28.5 was applicable and thus ruled no breach of Clause
    # 28.5 (2021 Code) and Clause 24.9 (2019 Code).' The foreign case is where the
    # health professionals at issue were identified; the ruling is this case's, and
    # its own slots list 28.5 and 24.9 under no breach.

    # -- refused: a recital of another case's ruling ----------------------
    ("AUTH/1857/6/06", "f2070dc4446a"):
        "recital: 'this had been considered in Case AUTH/1819/4/06 wherein no breach of "
        "Clauses 20.1 and 20.2 was ruled' -- AUTH/1819's ruling, not this Board's.",
    ("AUTH/2261/9/09", "cc3e4b2733f4"):
        "recital inside a correction of the respondent: 'Merck Sharp & Dohme was wrong in "
        "its submission that in Case AUTH/2192/12/08 no breach of Clause 25 was ruled'.",
    ("AUTH/2325/6/10", "04223e44afd8"):
        "recital of the complainant's precedent: 'referred to Cases AUTH/2287/12/09 and "
        "AUTH/2288/12/09, in which the Appeal Board had previously ruled a breach of "
        "Clause 12.1'.",
    ("AUTH/2402/4/11", "d00b685d3aa9"):
        "precedent distinguished: 'different to Case AUTH/2355/9/10 cited by Bayer wherein "
        "no breach of Clause 2 was ruled'. Already named as a precedent citation by the "
        "R28 stage-1 panel read list.",
    ("AUTH/2445/10/11", "3b1100c656f2"):
        "recital: 'in Case AUTH/2424/8/11 it had considered ... and in that wider sense it "
        "had already ruled a breach of Clause 3.1' -- the ruling was made in AUTH/2424.",
    ("AUTH/2804/11/15", "aa2e760287c4"):
        "recital: 'in Case AUTH/2756/5/15 a breach of Clause 15.9 was ruled with regard to "
        "uncertified representative's briefing material'.",
    ("AUTH/3028/3/18", "e8009a06fb03"):
        "recital: 'In Case AUTH/2997/12/17, the Panel ruled a breach of Clause 14.1 ...'.",
    ("AUTH/3219/6/19", "22ac23cb86ba"):
        "recital: 'in Case AUTH/3042/6/18 (Otsuka UK) the Panel ruled a breach of Clause "
        "4.1 in relation to Jinarc prescribing information ...'.",
    ("AUTH/3219/6/19", "f85f50983106"):
        "recital: 'in Case AUTH/3041/6/18 (Otsuka Europe) the Panel ruled breaches of "
        "Clause 4.1 in relation to 7 Jinarc promotional materials ...'.",
    ("AUTH/3430/11/20", "c19a71365274"):
        "recital: 'in Case AUTH/3248/9/19 the Panel considered that the re-tweet ...'.",
    ("AUTH/3452/1/21", "7a6721adb1fb"):
        "precedents distinguished: 'the cases cited by Lundbeck (Case AUTH/3213/6/19, "
        "3321/3/20, 3112/11/18 and 3438/12/20), in which no breach of Clause 12.1 was "
        "ruled, were [not] similar to this case'.",
    ("AUTH/3545/7/21", "41ded7c50cc0"):
        "recital: 'in Case AUTH/3281/11/19 a breach of Clause 9.1 of the 2019 Code was "
        "ruled by the Panel, and upheld on appeal ...'.",
    ("AUTH/3641/4/22", "52ee7b3ef52a"):
        "recital, and the correction to the fix wave's list of 8: '... the Panel therefore "
        "ruled a breach of Clause 4.6 of the 2019 Code IN THAT CASE (Case "
        "AUTH/3446/12/20)', followed by 'Turning to the current case, Case AUTH/3641/4/22'. "
        "Clause 4.6 is not in AUTH/3641's outcome slots either.",
    ("CASE/0272/08/24", "f1fae69fd610"):
        "recital of the complainant's precedent: 'referred to Case AUTH/3760/4/23 in which "
        "the Panel ruled a breach of Clause 6.1 ...'.",

    # -- refused: a party citing or supposing, in submissions or grounds ---
    ("AUTH/2099/2/08", "8926728a409b"):
        "the appellant's CONDITIONAL: 'Roche considered that if the Appeal Board upheld "
        "the Panel's rulings of breaches of Clauses 2 and 9.1 ... there would be "
        "significant implications'. Not a ruling at all.",
    ("AUTH/2100/2/08", "8926728a409b"):
        "same sentence, sibling case; same reason.",
    ("AUTH/2325/6/10", "375db8ea0220"):
        "the complainant's grounds citing a precedent: 'in Cases AUTH/2287/12/09 and "
        "AUTH/2288/12/09, the Appeal Board ruled a breach of Clause 12.1'.",
    ("AUTH/2528/8/12", "1f334e1c732d"):
        "the respondent's CONDITIONAL: 'even if Genzyme's appeal on Clause 22.1 was "
        "successful, Shire reiterated that this case ... did not warrant a ruling of a "
        "breach of Clause 2'.",
    ("AUTH/2617/7/13", "44685e7aa714"):
        "a precedent quoted in appeal comments: 'In Case AUTH/2471/1/12, the Appeal Board "
        "noted the educational content and ruled no breach of Clause 2'.",
    ("AUTH/2628/8/13", "44685e7aa714"): "same sentence, co-reported case; same reason.",
    ("AUTH/2629/8/13", "44685e7aa714"): "same sentence, co-reported case; same reason.",
    ("AUTH/2631/8/13", "44685e7aa714"): "same sentence, co-reported case; same reason.",
    ("AUTH/3535/7/21", "71efd51cc71b"):
        "the respondent citing a precedent: 'the complainant had also referred to Case "
        "AUTH3151/1/19 as a precedent where the Appeal Board ruled a breach of Clause 2'.",
}

# THE REGISTRY. Key: (case number, sha256 of the whitespace-collapsed sentence,
# first 12 hex). Value: None to READ the sentence as this case's own ruling, or
# a string saying why it is refused. A sha rather than a quote because the key
# must not survive an edit to the text it was read against -- the discipline
# every adjudication in l2/adjudications.json already carries.
UNDERTAKING_SUBJECT_READ = {
    ("AUTH/2246/7/09", "33423dc22b7d"): None,   # cl 25 breach, Panel (R28's own case)
    ("AUTH/2246/7/09", "bd3565495a4a"): None,   # cl 25 breach, Appeal Board
    ("AUTH/2298/2/10", "d2ae3eff81a5"): None,   # cl 25 breach
    ("AUTH/2335/7/10", "087daa3f66ae"): None,   # cl 25 no_breach
    ("AUTH/2346/8/10", "087daa3f66ae"): None,   # cl 25 no_breach
    ("AUTH/2388/2/11", "77b2ac4c0855"): None,   # cl 25 no_breach
    ("AUTH/2398/4/11", "00b5142564ed"): None,   # cl 25 no_breach
    ("AUTH/2487/3/12", "83b1540daa6a"): None,   # cl 25 no_breach (2nd undertaking)
    ("AUTH/2489/3/12", "83b1540daa6a"): None,   # cl 25 no_breach (2nd undertaking)
    ("AUTH/2620/7/13", "b0b115a7d640"): None,   # cl 25 breach, Appeal Board
    ("AUTH/2823/2/16", "21411b21afcb"): None,   # cl 29 breach ('Turning to the present case')
    ("AUTH/2833/4/16", "c293c0394c0f"):
        "RECITAL, not this case's ruling: the sentence sits in AUTH/2833's preamble, "
        "immediately after 'In Case AUTH/2817/12/15 ALK-Abello complained on 23 December "
        "2015 that Emerade continued to be described as \"new\" on the product website.' "
        "and before 'Turning to the case now before it, Case AUTH/2833/4/16'. The "
        "breaches of Clauses 2, 9.1 and 29 it reports are AUTH/2817/12/15's. AUTH/2833's "
        "own rulings come later and unanchored ('The Panel therefore ruled a breach of "
        "Clause 29 ... a breach of Clause 9.1 was also ruled ... did not warrant a ruling "
        "of a breach of Clause 2 and thus no breach of that clause was ruled'), so "
        "reading this one would have made clause 2 a false dual and excluded its items.",
    ("AUTH/2928/1/17", "67a898aff877"): None,   # cl 29 breach
    ("AUTH/2928/1/17", "0df84c6355bc"): None,   # cl 29 no_breach
    ("AUTH/2929/1/17", "67a898aff877"): None,   # cl 29 breach
    ("AUTH/2929/1/17", "0df84c6355bc"): None,   # cl 29 no_breach
    ("AUTH/3069/9/18", "a2b34b6a54b8"): None,   # cl 29, 9.1, 2 no_breach
    ("AUTH/3071/9/18", "eb837fad99d1"): None,   # cl 29, 9.1, 2 no_breach
    ("AUTH/3078/9/18", "39d9211b785d"): None,   # cl 29, 9.1, 2 no_breach
    ("AUTH/3080/9/18", "142e6f836223"): None,   # cl 29, 9.1, 2 no_breach
    ("AUTH/3090/9/18", "dbdaccd1f42f"): None,   # cl 29, 9.1, 2 no_breach
    ("AUTH/3167/2/19", "266ac740ba94"): None,   # cl 29, 2 no_breach
    ("AUTH/3184/4/19", "d522cc3e2556"): None,   # cl 29 no_breach
    ("AUTH/3199/5/19", "c0bea1ea298c"): None,   # cl 29 breach
    ("AUTH/3250/10/19", "3d89d8819bae"): None,  # cl 29 breach
    ("AUTH/3282/11/19", "00de71c0aa53"): None,  # cl 29 breach
    ("AUTH/3308/2/20", "be7ef3eb08c1"): None,   # cl 29 breach
    ("AUTH/3375/8/20", "f7534c80a353"): None,   # cl 29 breach
    ("AUTH/3430/11/20", "e1b0f3215cb4"): None,  # cl 29 no_breach
    # Reached only once R30 gave AUTH/3499/4/21 a panel_ruling segment at all.
    # 'Turning to the present case, Case AUTH/3499/4/21, the Panel ruled breaches
    # of the Code ... The Panel considered that there had thus been a failure to
    # comply with the undertaking given in Case AUTH/3107/10/18 and a breach of
    # Clause 29 was ruled.' -- the pivot is explicit and precedes it.
    ("AUTH/3499/4/21", "2bfde083795f"): None,   # cl 29 breach
    ("AUTH/3502/4/21", "6d674d2ca94f"): None,   # cl 29 breach
    ("AUTH/3565/10/21", "fb159810e72f"): None,  # cl 29 breach
    ("AUTH/3566/10/21", "fb159810e72f"): None,  # cl 29 breach (same report)
    ("AUTH/3587/12/21", "fe4ec1b9526c"): None,  # cl 3.3 no_breach
    ("AUTH/3719/12/22", "801136b49d82"): None,  # cl 3.3 no_breach, Appeal Board
    ("AUTH/3889/4/24", "f264153a0bd4"): None,   # cl 3.3 no_breach
    ("AUTH/3893/4/24", "9383aca1d78c"): None,   # cl 3.3 no_breach
    ("CASE/0583/05/25", "6d9351359667"): None,  # cl 3.3 no_breach
}


# ---------------------------------------------------------------------------
# DEFECTS R28 stage 1. THE DUAL-RULING READ LISTS, both axes.
#
# Two things were missing and this table is the second of them. The APPEAL axis
# had no dual detection at all: `_single_polarity` refused a both-ways appeal
# clause into `appeal_board: null` and nothing recorded that a DUAL was why, so
# bench's exclusion row said "the appeal-side prose does not state an Appeal
# Board ruling on this clause" -- the opposite of true. And on BOTH axes the
# reader's frames and guards can hide a dual, which is how R18's pair and
# R28's seven were found by hand rather than by the build.
#
# So the candidates are gathered two ways -- what the READER attributes both
# ways, and what the LOOSE screen (`dual_screen`) sees both ways -- and every
# candidate must appear here, read against the report. 82 rows: 27 on the panel
# axis, 55 on the appeal axis. `check_dual_read_coverage` refuses the build on
# a candidate that is not here AND on a row here that is no longer a candidate,
# so the set cannot drift silently in either direction.
#
# THE THREE VALUES:
#
#   None      READ, and it IS a dual, with both halves already attributed in
#             `rulings`. On the appeal axis this sets `dual_ruling_appeal_board`
#             and nulls `appeal_board` (which `_single_polarity` had already
#             refused into null on all 38 rows it can see both ways).
#   {...}     READ, and it IS a dual whose SECOND HALF the reader cannot see.
#             The dict carries that half verbatim with its pane offsets; the
#             build re-slices the pane and refuses if the text has moved, which
#             is the same stale-fix protection an adjudication's sha gives, and
#             the sentence joins `rulings` as a receipt.
#   "reason"  READ, and it is NOT a dual. The reason is the receipt. The row
#             keeps exactly the state it had; nothing here repairs a
#             mis-attribution, because a repair would CREATE items and every
#             one of these needs its own reading first.
#
# WHAT THE NOT-DUALS TURNED OUT TO BE, since the classes recur: a party's
# CONDITIONAL submission in the appellant's grounds ('in the event that the
# Appeal Board ruled a breach of Clause 18.5 then ...' -- a hypothesis, and the
# IRREALIS guard's vocabulary does not reach it); the Appeal Board RECITING the
# Panel's ruling inside its own section; a precedent citation whose case number
# is in a NEIGHBOURING sentence ('These cases concerned ... breaches of Clauses
# 7.2, 7.4 and 7.9 were ruled'), so the sentence-local precedent guard cannot
# see it; the Clause 2 censure idiom, which R18's audit already warned reads as
# a breach and is a no-breach ruling; and, on a shared report, the SIBLING
# case's ruling (R5's family -- AUTH/1822 and AUTH/1823 rule Clause 4.1
# opposite ways in one document).
RULE_DECIDED_PANEL_DUAL = ("verdict_dual_panel_prose", "verdict_unappealed_dual_listed")


def dual_read_is_dual(case, clause, axis):
    """Did the reading say this row IS a dual? (None or an evidence dict.)

    A missing key is not False -- it is unread, which `check_dual_read_coverage`
    refuses on. This helper is only ever asked about registered rows.
    """
    decided = DUAL_READ.get((case, clause, axis), _DUAL_UNREAD)
    return decided is None or isinstance(decided, dict)

DUAL_READ = {
    # -- PANEL axis (screen-only candidates; the reader's own both-polarity
    # rows are all already `verdict_dual_panel_prose`) --------------------
    ("AUTH/1822/4/06", "4.1", "panel"):
        "shared report: the breach ruling at 10982 and the no-breach at 19396 are the two "
        "SIBLING cases' rulings, not two regards of one case (R5's class; 1822/1823)",
    ("AUTH/1823/4/06", "4.1", "panel"): "the sibling of AUTH/1822/4/06 above, same document",
    # READ AS A GENUINE DUAL -> adj-0107 / adj-0108. The Panel ruled breaches of
    # 15.4 and 15.9 on the call-rate and briefing-material regards, then 'It was
    # thus not possible to determine on the balance of probabilities whether
    # what had been said at the meeting amounted to a breach of Clauses 15.4 or
    # 15.9; no breach of these clauses was accordingly ruled.' Unreadable twice
    # over: the anaphor is 'these clauses' (the reader resolves 'that clause')
    # and the verb is 'accordingly ruled'.
    ("AUTH/1899/10/06", "15.4", "panel"): None,
    ("AUTH/1899/10/06", "15.9", "panel"): None,
    # Already a genuine dual by adj-0008 (R18's confirmed member); the screen
    # sees it through 'ruled no breach of Clause 20.1 in this regard' against
    # 'a breach of that clause was thus ruled'. Registered so the reading and
    # the adjudication stay together.
    ("AUTH/2414/6/11", "20.1", "panel"): None,
    ("AUTH/2371/11/10", "2", "panel"):
        "censure idiom: 'did not consider the circumstances warranted a breach of Clause 2 and "
        "ruled accordingly' is a NO-breach ruling; the other hit says the same",
    ("AUTH/2402/4/11", "2", "panel"):
        "precedent citation: the no-breach half is Case AUTH/2355/9/10's ruling, quoted",
    ("AUTH/2445/10/11", "3.1", "panel"):
        "precedent citation: the breach half is this Panel's ruling in Case AUTH/2424/8/11",
    ("AUTH/2589/3/13", "25", "panel"):
        "the two breach hits recap Case AUTH/2442/10/11 and are independently refused by "
        "RULING_CONTEXT_REFUSALS; this case's own Panel ruling is no breach",
    ("AUTH/2804/11/15", "15.9", "panel"):
        "precedent citation: the breach half is Case AUTH/2756/5/15's ruling",
    ("AUTH/2833/4/16", "2", "panel"):
        "the breach half is the AUTH/2802/11/15 RECITAL already read and refused in "
        "UNDERTAKING_SUBJECT_READ; the case's own ruling is no breach",
    ("AUTH/3043/6/18", "9.1", "panel"):
        "the no-breach half names the APPEAL BOARD ('The Appeal Board ruled no breaches of "
        "Clauses 20 and 9.1'), which the reader credits to the Board; the Panel ruled breach",
    ("AUTH/3043/6/18", "20", "panel"):
        "same sentence as 9.1 above: an Appeal Board ruling, not a second Panel ruling",
    ("AUTH/3044/6/18", "9.1", "panel"):
        "the two no-breach halves recite the APPEAL BOARD's rulings in the related case "
        "('Novartis appealed and the Appeal Board subsequently ruled no breach'); this case "
        "was not appealed and its Panel ruled breach",
    ("AUTH/3044/6/18", "20", "panel"): "same two sentences as 9.1 above",
    ("AUTH/3067/9/18", "2", "panel"):
        "censure idiom, both halves ('did not consider that the circumstances warranted a "
        "ruling of a breach of Clause 2 ... and ruled accordingly')",
    ("AUTH/3079/9/18", "2", "panel"): "censure idiom ('a breach of Clause 2 was warranted' "
                                      "under 'did not consider'); the Panel ruled no breach",
    ("AUTH/3084/9/18", "2", "panel"): "censure idiom, as AUTH/3079",
    ("AUTH/3087/9/18", "2", "panel"): "censure idiom, as AUTH/3079",
    ("AUTH/3091/9/18", "2", "panel"): "censure idiom, as AUTH/3079",
    ("AUTH/3097/9/18", "2", "panel"): "censure idiom, as AUTH/3079",
    ("AUTH/3099/9/18", "2", "panel"): "censure idiom, as AUTH/3079",
    ("AUTH/3430/11/20", "26.1", "panel"):
        "precedent citation: the breach half is Case AUTH/3248/9/19's rulings",
    ("AUTH/3809/8/23", "3.1", "panel"):
        "the no-breach half is the APPEAL BOARD's ruling, sitting inside a panel_ruling "
        "segment because this report has no APPEAL BOARD RULING heading (R30's class)",
    ("AUTH/3809/8/23", "3.6", "panel"): "same sentence as 3.1 above",
    ("AUTH/3809/8/23", "24.2", "panel"): "same class as 3.1 above, its own sentence",
    ("AUTH/3684/8/22", "5.1", "panel"):
        "screen artefact, the AUTH/3869 shape: 'a breach of Clause 5.1 adequately covered the "
        "matter and no breach of Clause 2 was ruled accordingly' states breach of 5.1 and no "
        "breach of 2",
    ("AUTH/3869/12/23", "5.1", "panel"):
        "screen artefact: 'the ruling of a breach of Clause 5.1 was a proportionate sanction "
        "and no breach of Clause 2 was ruled accordingly' states breach of 5.1 and no breach "
        "of 2; the reader's swallow rule reads it correctly and the screen does not",

    # -- APPEAL axis ------------------------------------------------------
    # Read as GENUINE duals: the Board ruled the clause both ways in different
    # regards, each disposed with its own 'the appeal on this point was
    # successful/unsuccessful'.
    ("AUTH/1841/5/06", "7.2", "appeal_board"): None,
    ("AUTH/1841/5/06", "7.3", "appeal_board"): None,
    ("AUTH/1857/6/06", "20.1", "appeal_board"): None,
    ("AUTH/1862/7/06", "2", "appeal_board"): None,
    ("AUTH/1862/7/06", "9.1", "appeal_board"): None,
    ("AUTH/1862/7/06", "22", "appeal_board"): None,
    ("AUTH/2141/7/08", "7.2", "appeal_board"): None,
    ("AUTH/2141/7/08", "9.1", "appeal_board"): None,
    ("AUTH/2246/7/09", "9.1", "appeal_board"): None,
    ("AUTH/2273/10/09", "3.2", "appeal_board"): None,
    ("AUTH/2273/10/09", "7.2", "appeal_board"): None,
    ("AUTH/2273/10/09", "7.3", "appeal_board"): None,
    ("AUTH/2273/10/09", "7.4", "appeal_board"): None,
    ("AUTH/2273/10/09", "7.10", "appeal_board"): None,
    ("AUTH/2289/12/09", "7.2", "appeal_board"): None,
    ("AUTH/2289/12/09", "7.10", "appeal_board"): None,
    ("AUTH/2334/7/10", "3.2", "appeal_board"): None,
    ("AUTH/2334/7/10", "7.2", "appeal_board"): None,
    ("AUTH/2334/7/10", "7.10", "appeal_board"): None,
    ("AUTH/2417/6/11", "3.2", "appeal_board"): None,
    ("AUTH/2721/7/14", "7.4", "appeal_board"): None,
    ("AUTH/2723/7/14", "7.2", "appeal_board"): None,
    ("AUTH/2739/11/14", "3.1", "appeal_board"): None,
    ("AUTH/2849/6/16", "2", "appeal_board"): None,
    ("AUTH/2984/10/17", "2", "appeal_board"): None,
    ("AUTH/2984/10/17", "9.1", "appeal_board"): None,
    ("AUTH/2984/10/17", "18.5", "appeal_board"): None,
    ("AUTH/2987/10/17", "9.1", "appeal_board"): None,
    ("AUTH/3010/1/18", "2", "appeal_board"): None,
    ("AUTH/3010/1/18", "9.1", "appeal_board"): None,
    ("AUTH/3320/3/20", "9.1", "appeal_board"): None,
    ("AUTH/3431/11/20", "26.1", "appeal_board"): None,
    ("AUTH/3503/4/21", "3.2", "appeal_board"): None,
    ("AUTH/3503/4/21", "7.2", "appeal_board"): None,
    ("AUTH/3597/1/22", "5.1", "appeal_board"): None,
    ("AUTH/3717/12/22", "6.1", "appeal_board"): None,
    ("AUTH/3763/4/23", "5.1", "appeal_board"): None,
    ("AUTH/3815/8/23", "5.1", "appeal_board"): None,
    ("CASE/0409/12/24", "11.2", "appeal_board"): None,
    # The two the reader cannot see both halves of. R28's own finding: the
    # Board ruled no breach of 7.3 and 7.4 on matter 1 ('the appeal on these
    # points was successful') and upheld breaches of 7.2, 7.3 and 7.4 on matter
    # 3. Only the second half is in a frame the reader has, so it published
    # `appeal_board: breach` and two T3 items labelled `upheld` at half the
    # truth -- both in the published 297-item board, for both models.
    ("AUTH/1941/1/07", "7.3", "appeal_board"): {
        "polarity": "no_breach", "pane": "report", "char_start": 34670, "char_end": 34793,
        "quote": "In that regard the Appeal Board thus considered that there was no breach of "
                 "either Clause 7.3 or 7.4 and ruled accordingly.",
        "note": "matter 1 (Endoscopic healing rates): the appeal on 7.3/7.4 was successful. "
                "Matter 3 upheld breaches of 7.2, 7.3 and 7.4. Unreadable by the frames: "
                "'ruled accordingly' carries the verb and 'either Clause 7.3 or 7.4' the list.",
    },
    ("AUTH/1941/1/07", "7.4", "appeal_board"): {
        "polarity": "no_breach", "pane": "report", "char_start": 34670, "char_end": 34793,
        "quote": "In that regard the Appeal Board thus considered that there was no breach of "
                 "either Clause 7.3 or 7.4 and ruled accordingly.",
        "note": "the same sentence rules on both clauses; see the 7.3 row",
    },
    # Read as NOT duals.
    ("AUTH/2272/10/09", "18.5", "appeal_board"):
        "both non-ruling halves are the appellant's CONDITIONAL submissions in its grounds "
        "('In the event that the Appeal Board ruled a breach of Clause 18.5 then Allergan "
        "submitted ...'); the Board's own ruling is 'thus ruled no breach of Clause 18.5'",
    ("AUTH/2325/6/10", "12.1", "appeal_board"):
        "the breach half is the complainant citing Cases AUTH/2287/12/09 and AUTH/2288/12/09; "
        "the Board upheld the Panel's ruling of no breach",
    ("AUTH/2488/3/12", "7.2", "appeal_board"):
        "the breach half is the Appeal Board RECITING the Panel ('The Panel had thus considered "
        "that ... Breaches of Clauses 7.2 and 7.4 were ruled'); its own ruling is no breach",
    ("AUTH/2488/3/12", "7.4", "appeal_board"): "the same recital sentence as 7.2 above",
    ("AUTH/2538/10/12", "25", "appeal_board"):
        "all three breach halves are AstraZeneca's conditional grounds ('even if the Appeal "
        "Board disagreed ... and upheld the ruling of a breach of Clause 25'); the Board ruled "
        "no breach of Clause 25",
    ("AUTH/2572/1/13", "7.2", "appeal_board"):
        "the breach half is a parenthetical about OTHER cases ('(These cases concerned a "
        "Seroquel journal advertisement ...; breaches of Clauses 7.2, 7.4 and 7.9 were ruled)') "
        "whose case numbers are in the preceding sentence, so the sentence-local precedent "
        "guard cannot see them",
    ("AUTH/3079/9/18", "9.1", "appeal_board"):
        "the breach half is the Board reciting the Panel ('The Appeal Board noted that the "
        "Panel had ruled breaches of Clauses 9.1 for Pfizer's failure to disclose ...'); its "
        "own ruling is no breach in relation to each trial",
    ("AUTH/3084/9/18", "9.1", "appeal_board"): "the same recital shape as AUTH/3079",
    ("AUTH/3087/9/18", "9.1", "appeal_board"): "the same recital shape as AUTH/3079",
    ("AUTH/3097/9/18", "9.1", "appeal_board"): "the same recital shape as AUTH/3079",
    ("AUTH/3102/9/18", "9.1", "appeal_board"):
        "the breach half is the Board reciting the Panel ('The Appeal Board noted that the "
        "Panel had ruled breaches of Clauses 9.1 ...'); its own ruling is no breach",
    ("AUTH/3118/11/18", "9.1", "appeal_board"): "the same recital shape as AUTH/3102",
    ("AUTH/3452/1/21", "12.1", "appeal_board"):
        "the no-breach half cites four other cases (AUTH/3213/6/19, 3321/3/20, 3112/11/18, "
        "3438/12/20) in which no breach of 12.1 was ruled; the Board upheld a breach here",
    ("AUTH/3624/3/22", "6.1", "appeal_board"):
        "the segment is kinded appeal_ruling but its prose is the PANEL's ('The Panel therefore "
        "ruled no breach of Clauses 6.1 and 14.4') -- a boundary defect, R30's class, not a "
        "second Board ruling",
    ("AUTH/3624/3/22", "14.4", "appeal_board"): "the same mis-kinded segment as 6.1 above",
}
_DUAL_UNREAD = "unread"


# The screen's own frames. Three of them restate the reader's (passive,
# active, uphold) at wider tolerance -- no precedent guard, no body
# attribution, 'either/both Clause N or M' allowed -- and the fourth is the one
# the READER does not have: '... and ruled accordingly', the frame R17/R18/R27
# all name as the carrier of half a dual ('the arrangements were unacceptable
# in relation to Clause 18.1 and ruled accordingly'). Giving it to the screen
# rather than to the reader is deliberate: read as evidence it would create
# attributions on ~22 rows that no one has read, which is a separate round.
_SCR_LIST = (rf"{CLAUSE_ITEM_RE}"
             rf"(?:\s*(?:,|and|or|&)\s*(?:consequently\s+|also\s+|further\s+)?{CLAUSE_ITEM_RE})*")
_SCR_MENTION = (rf"(?P<neg>{NO_PREFIX_RE})?\bbreach(?:es)?\s+of\s+"
                rf"(?:either\s+|both\s+)?Clauses?\s+(?P<list>{_SCR_LIST})")
DUAL_SCREEN_FRAMES = (
    re.compile(rf"{_SCR_MENTION}{_GAP}{{0,60}}?\bw(?:as|ere)\s+{_RULED_ADVERB}ruled\b", re.I),
    re.compile(rf"\bruled\s+(?:that\s+there\s+w(?:as|ere)\s+)?(?:(?:a|an|any|the)\s+)?"
               rf"(?:(?:further|additional|separate|consequent)\s+)?{_SCR_MENTION}", re.I),
    re.compile(rf"\bupheld\s+(?:the\s+)?(?:[A-Za-z’'\- ]{{0,24}}?\s)?rulings?\s+of\s+"
               rf"(?:(?:a|an|the)\s+)?{_SCR_MENTION}", re.I),
    re.compile(rf"{_SCR_MENTION}{_GAP}{{0,80}}?\band\s+(?:thus\s+|therefore\s+)?"
               rf"ruled\s+accordingly\b", re.I),
)


def dual_screen(chunks):
    """{clause: {polarity}} -- a LOOSE both-polarity screen over ruling prose.

    This is not a reader and never attributes anything. Its only power is to
    make the build REFUSE until a (case, clause) it flags has been read, which
    is the discipline UNDERTAKING_SUBJECT_READ established: a rule that fires
    on a small closed set must decide every member of it, and the members here
    are exactly the rows where a dual ruling could be hiding behind a frame or
    a guard the reader applies.

    It is deliberately looser than `ruling_polarities` in three ways, each of
    which is a way the reader has hidden a real dual before: no precedent guard
    (R28's AUTH/2246), no body attribution (a sentence naming the wrong body
    still counts), and the 'ruled accordingly' frame (R17/R18/R27's family,
    which is how AUTH/1941's appeal-side no-breach half is written).
    """
    found = {}
    for chunk in chunks:
        for sentence in SENTENCE_SPLIT_RE.split(chunk["text"]):
            if "breach" not in sentence.lower():
                continue
            for pat in DUAL_SCREEN_FRAMES:
                for m in pat.finditer(sentence):
                    if m.group("neg") or NO_BREACH_INSIDE_RE.search(m.group(0)):
                        polarity = "no_breach"
                    elif NEGATOR_ATTACHED_RE.search(sentence[max(0, m.start() - 30):m.start()]):
                        continue
                    else:
                        polarity = "breach"
                    for clause in clause_tokens(m.group("list")):
                        found.setdefault(clause, set()).add(polarity)
    return sorted(c for c, p in found.items() if len(p) == 2)


# Assurance repair (2026-08-12): the outcome slots silently omit these
# clause-level disposals. This is deliberately a CLOSED read list, not a new
# prose-mining rule. Each entry was read against the complete report pane; the
# complete-pane digest and every material quote are checked while that pane is
# resident in `digest`, and `build_cases` refuses a dead entry. An accepted
# entry may create exactly one otherwise-absent verdict row. A refused entry is
# just as durable: it must remain absent, so a later generic widening cannot
# quietly admit it.
#
# `panel` and `appeal_board` are expected results, not overrides. The ordinary
# body-attribution reader still has to produce them (with the unappealed rule as
# its documented exception), and the exact row is checked after construction.
# The reviewed Code edition is necessarily part of this decision: the
# structured clause-year witnesses do not exist for a clause the slots omit.
PROSE_ONLY_VERDICT_READ = {
    ("AUTH/2337/7/10", "2"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "be983dbd1d631257a61b8afb150511ba45d05785238e889dc1915fc594cc6d8d",
        "quotes": (
            "Given that the item was not in its final form and had not been used as described "
            "above the Panel ruled no breach of Clauses 2, 7.2, 9.10 and 22.1 of the Code.",
        ),
        "reason": "the Panel expressly disposed of omitted Clause 2 in the same no-breach ruling as listed Clause 7.2",
    },
    ("AUTH/2337/7/10", "9.10"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "be983dbd1d631257a61b8afb150511ba45d05785238e889dc1915fc594cc6d8d",
        "quotes": (
            "Given that the item was not in its final form and had not been used as described "
            "above the Panel ruled no breach of Clauses 2, 7.2, 9.10 and 22.1 of the Code.",
        ),
        "reason": "the Panel expressly disposed of omitted Clause 9.10 in the same no-breach ruling as listed Clause 7.2",
    },
    ("AUTH/2337/7/10", "22.1"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "be983dbd1d631257a61b8afb150511ba45d05785238e889dc1915fc594cc6d8d",
        "quotes": (
            "Given that the item was not in its final form and had not been used as described "
            "above the Panel ruled no breach of Clauses 2, 7.2, 9.10 and 22.1 of the Code.",
        ),
        "reason": "the Panel expressly disposed of omitted Clause 22.1 in the same no-breach ruling as listed Clause 7.2",
    },
    ("AUTH/2220/3/09", "18.1"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "03cf6259f006761f4ab78bce44e57d50a6043bdd013310c38369da99c6c36d09",
        "quotes": (
            "The Panel ruled no breach of Clauses 18.1 and 18.4.",
            "The Panel ruled no breach of Clauses 15.2 and 18.1 of the Code on this point.",
            "No breach of Clauses 18.1 and 19.1 were ruled.",
        ),
        "reason": "three separately disposed matters all rule no breach of omitted Clause 18.1",
    },
    ("AUTH/2316/5/10", "7.4"): {
        "decision": "accept", "final": "no_breach", "code_year": 2008,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "cb1087e2d7840b80c9b7032db2761194c213bf2fa736b702dd1c774ab2b11f02",
        "quotes": (
            "Although noting that extreme dissatisfaction was usually required before an individual "
            "was moved to complain, on the basis of the information before it the Panel ruled no "
            "breach of Clauses 7.2 and 7.4 of the Code.",
        ),
        "reason": "the Panel expressly included omitted Clause 7.4 in its no-breach disposal",
    },
    ("AUTH/1855/6/06", "7.9"): {
        "decision": "accept", "final": "no_breach", "code_year": 2006,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "54dc428187e1761b5339053c2195a0754f5fe9153d1b96059e2a39f3f4753836",
        "quotes": (
            "Thus the Panel ruled no breach of Clauses 7.2, 7.8, 7.9 and 7.10 of the Code.",
        ),
        "reason": "the unappealed 7.9 disposal is explicit although other clauses in this appealed case were challenged",
    },
    ("AUTH/1855/6/06", "9.1"): {
        "decision": "accept", "final": "no_breach", "code_year": 2006,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "54dc428187e1761b5339053c2195a0754f5fe9153d1b96059e2a39f3f4753836",
        "quotes": (
            "The Panel did not consider that the page failed to maintain a high standard and thus "
            "no breach of Clause 9.1 of the Code was ruled.",
            "The Panel did not consider that the pages were misleading and thus ruled no breach "
            "of Clauses 7.2, 7.4 and 9.1 of the Code.",
        ),
        "reason": "two separately disposed matters both rule no breach of omitted Clause 9.1; neither was appealed",
    },
    ("AUTH/1884/8/06", "15.2"): {
        "decision": "accept", "final": "no_breach", "code_year": 2006,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "c6c4b3b86a3d43df69e2c9ea56b514ab61cee04dd7814cfd9b1df03837e479d4",
        "quotes": (
            "The Panel considered that the evidence before it was such that it was not possible to "
            "determine whether on the balance of probabilities the representative’s conduct amounted "
            "to a breach of Clauses 15.2 and 15.4 of the Code and thus no breach of these clauses was ruled.",
            "The Panel thus ruled no breach of Clauses 15.2 and 15.4 of the Code.",
            "The Panel did not know where the truth lay and thus ruled no breach of Clauses 15.2 "
            "and 15.4 of the Code.",
            "The Panel ruled no breach of Clauses 15.2 and 15.4 of the Code.",
        ),
        "reason": "every disposing strand for omitted Clause 15.2 is no breach",
    },
    ("AUTH/2634/8/13", "15.9"): {
        "decision": "accept", "final": "no_breach", "code_year": 2012,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "97470a66bd1e6025ca39c4019741b678949f75e7a046e752180c0fea205b1e6e",
        "quotes": (
            "The Panel ruled no breach of Clauses 7.2, 7.4, 7.9, 15.2 and 15.9 of the Code.",
        ),
        "reason": "the Panel expressly included omitted Clause 15.9 in its no-breach disposal",
    },
    ("AUTH/3587/12/21", "12.6"): {
        "decision": "accept", "final": "no_breach", "code_year": 2021,
        "panel": "no_breach", "appeal_board": "no_breach",
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "2bd43b24d74f45ca02a3a1cc2d6c149539dbb87abf75fe17123640fa9637a7d0",
        "quotes": (
            "It therefore ruled no breach of Clauses 12.1, 12.3, 12.4 and 12.6 of the 2021 Code.",
            "The Appeal Board agreed with the Panel’s comments above and upheld its rulings of no "
            "breach of Clauses 2, 3.3, 5.1, 12.1, 12.3, 12.4 and 12.6 of the 2021 Code.",
        ),
        "reason": "the Panel's omitted 12.6 no-breach ruling was expressly upheld by the Appeal Board",
    },
    ("AUTH/2667/11/13", "2"): {
        "decision": "accept", "final": "no_breach", "code_year": None,
        "panel": "no_breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "3cc2fef8522111bd70ceee74d4f353053687d87c2662ad48bab93b08cb81babc",
        "quotes": (
            "Thus the Panel ruled no breach of Clauses 9.1 and 2 of the 2006 Code.",
            "The Panel ruled no breach of Clauses 21.3 and consequently no breach of Clauses 9.1 "
            "and 2 of the 2011 Code in relation to NCT00472290.",
            "The results had been disclosed and the Panel considered that there was no breach of "
            "Clause 2 and ruled accordingly.",
        ),
        "reason": "Clause 2 is no breach, but its disposals span the 2006 and 2011 Codes and the unqualified consequence cannot choose one edition",
    },
    ("AUTH/3258/10/19", "7.9"): {
        "decision": "accept", "final": "breach", "code_year": 2019,
        "panel": "breach", "appeal_board": None,
        "dual_ruling": False, "dual_ruling_appeal_board": False,
        "source_sha256": "b0f3f0c2ba2929d9ab8fac9dd560488cd5ffb700ed115465940f068385ed3d26",
        "quotes": (
            "The available evidence was not reflected in the formulary decision guide and the Panel "
            "therefore ruled a breach of the Code.",
            "The Panel did not consider that the complainant had made an allegation with regard to "
            "Clause 7.9 in this regard and therefore made no ruling.",
        ),
        "reason": "matter 3 rules breach of 7.9; the later made-no-ruling sentence belongs to a different allegation and is not a contrary polarity",
    },
    ("AUTH/3615/3/22", "9.1"): {
        "decision": "refuse",
        "source_sha256": "b02a3bd51cd6b114559c4e75b46b442af2f460c41723ab3061422bb3d7c61cbb",
        "quotes": (
            "The Panel consequently ruled no breach of Clauses 9.1 and 2 of the 2019 Code.",
            "Turning to the case now before it, Case AUTH/3615/3/22, the Panel considered that "
            "there was a difference to the previous case (Case AUTH/3504/4/21).",
        ),
        "reason": "the 9.1 sentence recaps AUTH/3504/4/21 before the explicit pivot to this case; AUTH/3615's corresponding current-Code allegation is Clause 5.1",
    },
}


def sentence_key(sentence):
    return hashlib.sha256(collapse(sentence).encode("utf-8")).hexdigest()[:12]


# Assurance repair: the source typo "Clauses 1.11. 9.1 and 2" is split into
# two sentences by the ordinary punctuation rule, leaving the latter clauses
# without a ruling receipt.  The complete corpus population of the *ruling*
# shape is three occurrences: one in AUTH/3078 and two in AUTH/3079, all the
# same sentence after whitespace collapse.  Rejoin only those exact reviewed
# file/hash pairs and require the expected multiplicity at end of build.
STRAY_PERIOD_RULING_READ = {
    ("AUTH-3078-9-18.html", "beeaabcacae9"): 1,
    ("AUTH-3079-9-18.html", "beeaabcacae9"): 2,
}
STRAY_PERIOD_RULING_FIRED = Counter()
STRAY_PERIOD_LEFT_RE = re.compile(
    r"\b(?:ruled|upheld)\b[^\n]{0,300}\bClauses?\s+"
    r"\d{1,2}(?:\.\d{1,2})?\.$", re.I)
STRAY_PERIOD_RIGHT_RE = re.compile(
    r"\d{1,2}(?:\.\d{1,2})?\s+(?:and|or|&)\b", re.I)


def ruling_sentence_spans(text, file):
    """[(sentence, start)] with only reviewed stray-period lists rejoined."""
    raw = []
    at = 0
    for gap in SENTENCE_SPLIT_RE.finditer(text):
        raw.append((text[at:gap.start()], at, text[gap.start():gap.end()]))
        at = gap.end()
    raw.append((text[at:], at, ""))

    out, i = [], 0
    while i < len(raw):
        sentence, start, gap = raw[i]
        if i + 1 < len(raw) and STRAY_PERIOD_LEFT_RE.search(sentence) \
                and STRAY_PERIOD_RIGHT_RE.match(raw[i + 1][0]):
            merged = sentence + gap + raw[i + 1][0]
            key = (file, sentence_key(merged))
            if key not in STRAY_PERIOD_RULING_READ:
                raise SystemExit(
                    f"REFUSING: unreviewed stray-period ruling list in {file}: "
                    f"{collapse(merged)!r}")
            STRAY_PERIOD_RULING_FIRED[key] += 1
            out.append((merged, start))
            i += 2
            continue
        out.append((sentence, start))
        i += 1
    return out


# Assurance repair: sentence-local precedent detection cannot see a recap
# whose foreign case number lives in the preceding sentence or heading.  Nor
# can a segment kind prove that a hypothetical in an appellant's submissions
# is an adjudicator speaking.  Each row below was read in its surrounding
# section and is pinned to the complete collapsed-sentence hash.  Suppressing
# only these sentences leaves the later, same-case ruling receipts intact.
RULING_CONTEXT_REFUSALS = {
    ("AUTH/2589/3/13", "c9398635c0c2"):
        "recap of AUTH/2442/10/11, identified in the preceding sentence; not this case's ruling",
    ("AUTH/2589/3/13", "3cdfc60f3ae0"):
        "second recap ruling from AUTH/2442/10/11; not this case's ruling",
    ("AUTH/2593/4/13", "a13ff44ceda5"):
        "inside the headed PANEL RULING IN CASE AUTH/2590/3/13 recap block",
    ("AUTH/2593/4/13", "41046e1bacd5"):
        "inside the headed PANEL RULING IN CASE AUTH/2590/3/13 recap block",
    ("AUTH/2960/6/17", "bb08f63974b7"):
        "the preceding sentence identifies Case AUTH/2949/3/17; this is that case's ruling",
    ("AUTH/3615/3/22", "6fe41f45a38b"):
        "precedent recap immediately before 'Turning to the case now before it, Case AUTH/3615/3/22'",
    ("AUTH/2739/11/14", "46d6b238664c"):
        "Daiichi-Sankyo's hypothetical submission ('Even if the Appeal Board ruled'), not a Board ruling",
}
RULING_CONTEXT_REFUSALS_FIRED = set()


def precedent_disposal(sentence, own_serials):
    """'own' | 'citation' | 'undertaking_subject' | 'mixed' for one sentence.

    'own' means no foreign case number at all, which is the ordinary case and
    never reaches the guard.
    """
    spans = [(m.start(), m.end(), m.group(0))
             for m in CASE_NUM_IN_TEXT_RE.finditer(sentence)
             if (m.group(1), m.group(2)) not in own_serials]
    if not spans:
        return "own", []
    anchored = [(m.start("list"), m.end("list"))
                for m in UNDERTAKING_CASE_RE.finditer(sentence)]
    marks = [any(a <= s and e <= b for a, b in anchored) for s, e, _ in spans]
    texts = [t for _, _, t in spans]
    if all(marks):
        return "undertaking_subject", texts
    if any(marks):
        return "mixed", texts
    return "citation", texts


def ruling_polarities(texts, default_body, own_cases, appeal_side=False, mixed=None,
                      records=None, segment_kind=None):
    """{body: {polarity: {clause}}} for a set of segments of ONE kind.

    `own_cases` guards the other half of the speaker problem: these segments
    cite OTHER cases' rulings as precedent ('In Case AUTH/2471/1/12, the Appeal
    Board ... ruled no breach of Clause 2'), and a sentence naming a case that
    is not this page's is about that case, not this one -- with the one
    exemption `precedent_disposal` decides, where the foreign case is the
    source of an undertaking rather than the author of a ruling.

    `appeal_side` turns on the two l2.3 rules that only make sense past the
    appeal boundary: the impersonal Board ruling (a statement naming no body at
    all, disposed of by 'The appeal on this point was successful') is credited
    to the Board, and an irrealis sentence -- a party HOPING or REQUESTING that
    the Board rule something -- never is.

    `mixed` collects the sentences the precedent rule cannot decide, for the
    build-time coverage refusal.

    R28 stage 1: `records` collects one entry per ATTRIBUTED statement -- the
    same statements that move the sets, with the sentence they were read from
    and its pane offsets, so `verdicts[].rulings` can carry the receipt. A
    statement this function skips (precedent citation, refused body, irrealis,
    unregistered undertaking sentence) contributes nothing here either: the
    list is what the build READ, not what the text contains.
    """
    found = {"panel": {"breach": set(), "no_breach": set()},
             "appeal_board": {"breach": set(), "no_breach": set()}}
    own_serials = {tuple(c.split("/")[:2]) for c in own_cases}
    for chunk in texts:
        text, base = chunk["text"], chunk["start"]
        sentence_rows = ruling_sentence_spans(text, chunk["file"])
        sentences = [s for s, _ in sentence_rows]
        # Offsets come from the splitter rather than a text search: a sentence
        # repeated verbatim in one segment (AUTH/2334 does this) must retain
        # the position of each occurrence.  The reviewed stray-period joiner
        # above likewise retains the first half's exact start.
        starts = [at for _, at in sentence_rows]
        for i, sentence in enumerate(sentences):
            if "breach" not in sentence.lower():
                continue
            key = sentence_key(sentence)
            refused_for = [(c, key) for c in own_cases
                           if (c, key) in RULING_CONTEXT_REFUSALS]
            if refused_for:
                RULING_CONTEXT_REFUSALS_FIRED.update(refused_for)
                continue
            disposal, foreign = precedent_disposal(sentence, own_serials)
            if disposal == "undertaking_subject":
                if not sentence_polarities(sentence):
                    continue
                key = sentence_key(sentence)
                read = [UNDERTAKING_SUBJECT_READ[(c, key)] for c in own_cases
                        if (c, key) in UNDERTAKING_SUBJECT_READ]
                if not read:
                    if mixed is not None:
                        mixed.append(("unregistered", key, collapse(sentence)))
                    continue
                if read[0] is not None:       # read, and refused with a reason
                    continue
            stated = sentence_statements(sentence)
            if not stated:
                continue
            body = ruling_body(sentence, default_body)
            if body is None and appeal_side \
                    and not PANEL_BODY_RE.search(sentence) \
                    and not APPEAL_BODY_RE.search(sentence):
                nxt = sentences[i + 1] if i + 1 < len(sentences) else ""
                if APPEAL_DISPOSED_RE.search(sentence) or APPEAL_DISPOSED_RE.search(nxt):
                    body = "appeal_board"
            if body is None:
                continue
            if appeal_side and body == "appeal_board" and IRREALIS_RE.search(sentence):
                continue
            if disposal in ("citation", "mixed"):
                # The precedent guard's two skip classes, moved BELOW the body
                # and polarity tests so the registry population is exactly the
                # sentences the skip COSTS something on: a citation that states
                # no polarity, or that no body would own, was never going to
                # move a set and needs no reader. Measured, panel_ruling +
                # appeal_ruling + appeal_comments over the whole corpus: 30
                # citation sentences and 3 mixed ones survive both tests, and
                # both registries below decide every one of them.
                key = sentence_key(sentence)
                registry = PRECEDENT_CITATION_READ if disposal == "citation" \
                    else PRECEDENT_MIXED_DECIDED
                read = [registry[(c, key)] for c in own_cases if (c, key) in registry]
                if not read:
                    # The caller keeps only the body this segment kind may speak
                    # for -- a panel_ruling never speaks for the Board and an
                    # appeal segment never speaks for the Panel -- so a statement
                    # the kind cannot own is discarded whatever the guard does,
                    # and demanding a reading of it would pad the registry with
                    # rows that decide nothing. 4 sentences, all the appellant
                    # quoting the Panel inside their grounds (AUTH/2528/8/12 x3,
                    # AUTH/3805/7/23).
                    if mixed is not None and body == ("appeal_board" if appeal_side else "panel"):
                        mixed.append((f"{disposal}_unregistered", key, collapse(sentence)))
                    continue
                if read[0] is not None:       # read, and refused with a reason
                    continue
            for polarity, clause, frame in stated:
                found[body][polarity].add(clause)
                if records is not None:
                    records.append({
                        "body": body, "polarity": polarity, "clause": clause,
                        "quote": sentence,
                        "char_start": base + starts[i],
                        "char_end": base + starts[i] + len(sentence),
                        "file": chunk["file"], "pane": chunk["pane"],
                        "segment_kind": segment_kind, "source_frame": frame,
                    })
    return found


# ---------------------------------------------------------------------------
# pass 1 -- one compact digest per L1 record
# ---------------------------------------------------------------------------

def digest(rec, der, pdf):
    """Everything L2 needs from one page, without keeping its pane text.

    The panes are ~100 KB each and there are 1902 of them; every question this
    build asks of the text (does the summary say 'outwith'? how long is the
    report? does this span leak the outcome? which clauses does the ruling
    prose name?) is answered here and only the answer is kept. Phase 2 extends
    the same discipline rather than breaking it: the segments carry offsets and
    a sha of the slice, never the slice.
    """
    ident = rec["identity"]
    summary_text = rec["panes"]["summary"]["text"]
    report_text = rec["panes"]["report"]["text"]
    report_text_sha256 = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
    # The prose-only registry is pinned to the COMPLETE report pane, not a
    # reduced receipts dict. Check its human-readable quotes too: a correct
    # digest beside a mistyped quote would be an unauditable decision even
    # though it could not drift onto a different source.
    for case in ident["filename_case_numbers"]:
        for (review_case, clause), review in PROSE_ONLY_VERDICT_READ.items():
            if review_case != case:
                continue
            if review["decision"] not in ("accept", "refuse"):
                raise SystemExit(
                    f"REFUSING: PROSE_ONLY_VERDICT_READ[{review_case}, {clause}] has invalid "
                    f"decision {review['decision']!r}")
            if report_text_sha256 != review["source_sha256"]:
                raise SystemExit(
                    f"REFUSING: PROSE_ONLY_VERDICT_READ[{review_case}, {clause}] was reviewed "
                    f"against report sha {review['source_sha256']}, but {rec['file']} now hashes "
                    f"to {report_text_sha256}. Re-read the complete report before re-pinning it.")
            for quote in review["quotes"]:
                if quote not in report_text:
                    raise SystemExit(
                        f"REFUSING: PROSE_ONLY_VERDICT_READ[{review_case}, {clause}] quote is "
                        f"not verbatim in {rec['file']}: {quote!r}")
    dates = rec["dates"]
    if pdf is not None:
        # C8/C9 source substitution applies to receipts as well as narrative.
        # In particular AUTH/2602's HTML pane is AUTH/2603's report and says
        # completed 15 July; its own PDF says 17 June, agreeing with both
        # canonical slots.  Keeping the foreign HTML trailer manufactured a
        # same-year discrepancy that did not exist in the case being built.
        dates = dict(rec["dates"])
        dates["report_trailer_lines"] = pdf_trailer_lines(
            pdf["flow_text"], pdf["file"])

    # Key info-holder items by LABEL: `order` is not a stable slot index (order
    # 3 is 'Appeal hearing' on 1838 pages, 'Review' on 26, 'Applicable Code
    # year' on 21, 'Additional sanctions' on 17). First occurrence wins, which
    # is the rule L1 already applies to its named slots.
    by_label = {}
    for item in rec["info_holder"]:
        by_label.setdefault(item["label"], item)

    chips = []
    addl = by_label.get("Additional sanctions")
    if addl:
        for m in TAG_LABEL_RE.finditer(addl["value_html"]):
            label = collapse(html_mod.unescape(TAG_RE.sub(" ", m.group(1))))
            if label:
                chips.append(label)

    # The report's own title line, for the C2 receipts. Taken by NAME from the
    # derived heading verdicts (CASE_TITLE), never by position.
    norm_by_index = {s["index"]: s["heading_normalised"]
                     for s in der["sections"] if s["pane"] == "report"}
    report_title_line = None
    for sec in rec["sections"]:
        if sec["pane"] == "report" and norm_by_index.get(sec["index"]) == "CASE_TITLE":
            report_title_line = sec["heading_text"]
            break

    h1 = ident["h1_text"] or ""
    title = ident["title_text"] or ""
    complainant = rec["meta"]["cludo:complainant"] or ""

    # -- segments and the attest, computed while the panes are in hand -------
    table_rows = parse_table_rows(rec["outcomes"]["report_table_rows"])
    ctx = {
        "banners": sorted({n for n in (needle(b) for b in der["banner_headings"]) if n}),
        "table_texts": sorted({
            n for row in rec["outcomes"]["report_table_rows"]
            for n in (needle(row.get("verdict_text")), needle(row.get("description_text")))
            if len(n) >= MIN_TABLE_TEXT_CHARS}),
        # R31. Only the needles the distinctiveness floor keeps (see
        # SANCTION_NEEDLE_USED). An undeclared label is NOT silently dropped
        # here -- it stays in the set, so it keeps refusing, and
        # `check_sanction_needle_coverage` stops the build naming it.
        "chips": sorted({n for n in (needle(c) for c in chips)
                         if n and SANCTION_NEEDLE_USED.get(n, True)}),
        # R26. Needles for `no_outcome_heading`, matched the same way banners
        # are: whitespace-collapsed containment against the collapsed span.
        "outcome_headings": outcome_headline_needles(rec, der),
        # R24. The file's OWN case numbers, so the precedent exemption can tell
        # "another case's ruling, quoted" from "this case's ruling, leaked".
        # From L1's identity, not from anything L2 derived.
        "own_case_numbers": frozenset(
            normalise_case_number(m)
            for n in (ident["filename_case_numbers"] or [])
            for m in CASE_NUM_IN_TEXT_RE.finditer(n)),
        "abstract": {},
    }
    segments, renditions, prose, seg_notes = build_segments(rec, der, pdf, ctx)

    # -- prose-first complainant metadata (l2.3) ---------------------------
    # Read from the pane the segments were cut from: on the 13 PDF
    # substitutions the HTML report pane is empty or belongs to another case,
    # so its opening would describe the wrong complainant.
    opening_pane = pdf["flow_text"] if pdf is not None else report_text
    body_at = next((s["ref"]["char_start"] for s in segments
                    if s["kind"] in set(BOUNDARY_KIND.values())), None)
    complainant_evidence = complainant_prose_evidence(opening_pane, summary_text, body_at)
    # DEFECTS D3. Each segment kind supplies the DEFAULT speaker for a sentence
    # that names no body; a sentence that names one overrides it, and a sentence
    # naming both is refused. The appellant's grounds default to nobody: their
    # prose is overwhelmingly the appellant restating the PANEL's ruling, so
    # only an explicit 'the Appeal Board ruled/upheld ...' counts there.
    own = list(ident["filename_case_numbers"])
    attributed = {"panel": {"breach": set(), "no_breach": set()},
                  "appeal_board": {"breach": set(), "no_breach": set()}}
    grounds_only = {"breach": set(), "no_breach": set()}
    precedent_mixed = []
    ruling_records = []
    for kind, default_body in (("panel_ruling", "panel"),
                               ("appeal_ruling", "appeal_board"),
                               ("appeal_comments", None)):
        kind_records = []
        got = ruling_polarities(prose[kind], default_body, own,
                                appeal_side=kind != "panel_ruling",
                                mixed=precedent_mixed,
                                records=kind_records, segment_kind=kind)
        for body in attributed:
            for polarity in ("breach", "no_breach"):
                if kind == "panel_ruling" and body != "panel":
                    continue        # panel segments never speak for the Board
                if kind != "panel_ruling" and body != "appeal_board":
                    continue        # appeal segments never speak for the Panel
                attributed[body][polarity] |= got[body][polarity]
                if kind == "appeal_comments":
                    grounds_only[polarity] |= got[body][polarity]
        # The same two rules, applied to the receipts: a record whose body the
        # kind is not allowed to speak for did not move a set, so it is not a
        # ruling this build attributed and it does not become a receipt either.
        ruling_records += [r for r in kind_records
                           if r["body"] == ("panel" if kind == "panel_ruling" else "appeal_board")]
    # A dual half the frames cannot read joins the receipts by READING, and the
    # reading is verified: the quote must still cut out of the pane at the
    # offsets it was reviewed against, or the build refuses. That is the same
    # protection an adjudication's source sha gives, against the same failure
    # (a decision applied to evidence that has since moved).
    panes_by_name = {"report": report_text, "summary": summary_text}
    if pdf is not None:
        panes_by_name["flow"] = pdf["flow_text"]
    for case in ident["filename_case_numbers"]:
        for (num, clause, axis), decided in sorted(DUAL_READ.items()):
            if num != case or not isinstance(decided, dict):
                continue
            pane_text = panes_by_name.get(decided["pane"], "")
            got = pane_text[decided["char_start"]:decided["char_end"]]
            if got != decided["quote"]:
                raise SystemExit(
                    f"REFUSING: DUAL_READ[{num}, {clause}, {axis}] was reviewed against "
                    f"{decided['pane']}[{decided['char_start']}:{decided['char_end']}] = "
                    f"{decided['quote']!r}, but that slice now reads {got!r}. Re-read the "
                    f"report and re-pin the offsets.")
            ruling_records.append({
                "body": axis, "polarity": decided["polarity"], "clause": clause,
                "quote": decided["quote"],
                "char_start": decided["char_start"], "char_end": decided["char_end"],
                "file": pdf["file"] if decided["pane"] == "flow" else rec["file"],
                "pane": decided["pane"],
                "segment_kind": None, "source_frame": "dual_read_registry",
            })

    # The regard: the last matter heading at or before the ruling sentence, in
    # the same pane. `null` where the report names no matter (single-matter
    # reports; the 13 PDF substitutions, whose flow carries no sections).
    matters = matter_headings(rec, der, segments)
    for r in ruling_records:
        prior = [h for h in matters if h[0] <= r["char_start"]] if r["pane"] == "report" else []
        head = prior[-1] if prior else None
        r["regard"] = head[2] if head else None
        r["regard_ref"] = ({"basis": "matter_heading", "char_start": head[0], "char_end": head[1]}
                           if head else None)
    ruling_records.sort(key=lambda r: (r["file"], r["pane"], r["char_start"], r["char_end"],
                                       r["clause"], r["body"], r["polarity"], r["source_frame"]))
    panel_breach = attributed["panel"]["breach"]
    panel_no_breach = attributed["panel"]["no_breach"]
    appeal_breach = attributed["appeal_board"]["breach"]
    appeal_no_breach = attributed["appeal_board"]["no_breach"]

    # D4a. A case-report banner for a case this page does not declare. Read from
    # the SECTION HEADINGS only, and only where the heading IS the banner --
    # 'CASE AUTH/2296/1/10', not a sentence that happens to cite a case number.
    # The loose form flags 136 pages, of which the ~95 extra are precedent
    # citations L1 recorded as low-confidence heading candidates.
    own_serials = {tuple(c.split("/")[:2]) for c in own}
    banner_cases, foreign_cases = [], []
    for sec in rec["sections"]:
        if sec["pane"] != "report":
            continue
        head = sec["heading_text"] or ""
        if not CASE_BANNER_RE.match(head):
            continue
        for m in CASE_NUM_IN_TEXT_RE.finditer(head):
            num = "/".join(m.group(1, 2, 3, 4))
            if num not in banner_cases:
                banner_cases.append(num)
            # Same prefix+serial, different month/year, is a title-line typo
            # (l1/derive.py source_integrity holds the same rule), not another
            # case.
            if (m.group(1), m.group(2)) not in own_serials and num not in foreign_cases:
                foreign_cases.append(num)

    # -- R18: the report's own APPEAL BY/FROM headings -----------------------
    # Taken by NAME from the derived heading verdicts, exactly as the CASE_TITLE
    # line above is, so a sentence that happens to start 'appeal by' inside the
    # body cannot become a witness.
    appeal_headings = [collapse(sec["heading_text"] or "")
                       for sec in rec["sections"]
                       if sec["pane"] == "report"
                       and norm_by_index.get(sec["index"]) == "APPEAL_GROUNDS"]

    # -- R19/R20: which edition the report says it ruled under ---------------
    # Read from the pane the case's own narrative lives in -- the PDF flow where
    # one was substituted, since those reports' HTML panes are empty or belong
    # to another case (AUTH/2602/5/13's pane is AUTH/2603/5/13's report).
    dec_years, weak_years, clause_years = edition_evidence(opening_pane)

    o = rec["outcomes"]
    breach_chips = parse_clause_chips((by_label.get("Breach Clause(s)") or {}).get("value_html"))
    no_breach_chips = parse_clause_chips((by_label.get("No breach Clause(s)") or {}).get("value_html"))

    return {
        "file": rec["file"],
        "report_text_sha256": report_text_sha256,
        "cases": list(ident["filename_case_numbers"]),
        "identity": ident,
        "meta_respondent": rec["meta"]["cludo:respondent"],
        "meta_complainant": rec["meta"]["cludo:complainant"],
        "complainant_prose": complainant_evidence,
        "meta_description": rec["meta"]["cludo:description"],
        "meta_code_year": rec["meta"]["cludo:applicable_code_year"],
        "info_code_year": (by_label.get("Applicable Code year") or {}).get("value"),
        "report_title_line": report_title_line,
        "dates": dates,
        "appeal": rec["appeal"],
        "appeal_headings": appeal_headings,
        "edition_prose_decisive": {str(y): q for y, q in sorted(dec_years.items())},
        "edition_prose_weak": {str(y): q for y, q in sorted(weak_years.items())},
        "clause_year_prose": {c: {str(y): q for y, q in sorted(ys.items())}
                              for c, ys in sorted(clause_years.items())},
        "outcomes": {k: rec["outcomes"][k] for k in (
            "meta_clause_breach", "meta_clause_no_breach",
            "info_breach_clauses", "info_no_breach_clauses",
            "meta_sanctions_applied", "info_sanctions_applied",
            "meta_additional_sanctions", "info_additional_sanctions")},
        "sanction_chips": chips,
        # -- phase 2 -------------------------------------------------------
        "segments": segments,
        "rendition_index": renditions,
        "segment_notes": seg_notes,
        "banner_headings": list(der["banner_headings"]),
        "table_rows": table_rows,
        "breach_chips": breach_chips,
        "no_breach_chips": no_breach_chips,
        "flat_breach": clause_tokens(f"{o['meta_clause_breach'] or ''} {o['info_breach_clauses'] or ''}"),
        "flat_no_breach": clause_tokens(
            f"{o['meta_clause_no_breach'] or ''} {o['info_no_breach_clauses'] or ''}"),
        "meta_breach_tokens": clause_tokens(o["meta_clause_breach"]),
        "meta_no_breach_tokens": clause_tokens(o["meta_clause_no_breach"]),
        "info_breach_tokens": clause_tokens(o["info_breach_clauses"]),
        "info_no_breach_tokens": clause_tokens(o["info_no_breach_clauses"]),
        # N2 (round-2A). Which clause numbers the case's OWN TEXT names, so a
        # slot-derived clause with no witness in the report can be refused
        # rather than published. Read from every pane -- for the 13 PDF
        # substitutions the flow, whose HTML pane is empty or another case's --
        # and NOT from the slots, which are the thing being checked.
        "clause_names_in_text": sorted(
            clause_names_in_text("\n".join(
                [pdf["flow_text"]] if pdf is not None else [report_text, summary_text])),
            key=clause_sort_key),
        "prose_panel_breach": sorted(panel_breach),
        "prose_panel_no_breach": sorted(panel_no_breach),
        "prose_appeal_breach": sorted(appeal_breach),
        "prose_appeal_no_breach": sorted(appeal_no_breach),
        "prose_appeal_grounds_breach": sorted(grounds_only["breach"]),
        "prose_appeal_grounds_no_breach": sorted(grounds_only["no_breach"]),
        # R28 stage 1. One entry per ATTRIBUTED ruling statement -- the sets
        # above say WHICH polarities exist, these say where each was read and
        # in what words. `resolve_verdicts` distributes them over the rows.
        "ruling_records": ruling_records,
        # R28 stage 1. Clauses the LOOSE screen sees both ways, per axis. Not
        # evidence -- a demand that the row be read (see `dual_screen`).
        "dual_screen_panel": dual_screen(prose["panel_ruling"]),
        "dual_screen_appeal": dual_screen(prose["appeal_ruling"]),
        # R28. Ruling sentences whose foreign case numbers are PART
        # undertaking-anchored: the precedent rule's two signals conflict, so
        # `check_precedent_guard_coverage` refuses unless the row is declared.
        "precedent_mixed": precedent_mixed,
        "banner_cases": banner_cases,
        "foreign_banner_cases": foreign_cases,
        "multi_case_undeclared": len(banner_cases) >= 2 and bool(foreign_cases),
        "report_len": rec["panes"]["report"]["text_length"],
        "source_integrity": der["source_integrity"],
        # -- procedure evidence, reduced to booleans while the text is in hand
        # R11: title line only. Searching report_text too matched company prose
        # ABOUT the procedure on four cases that did not use it.
        "has_abridged": bool(ABRIDGED_RE.search(summary_text[:ABRIDGED_TITLE_WINDOW])),
        # Kept as evidence: the phrase appearing in the body is a real signal
        # (usually a company discussing the procedure), just not this flag.
        "abridged_in_body_only": bool(
            not ABRIDGED_RE.search(summary_text[:ABRIDGED_TITLE_WINDOW])
            and (ABRIDGED_RE.search(summary_text) or ABRIDGED_RE.search(report_text))),
        # DEFECTS D1. The keyword is kept only to report what it used to claim:
        # 'outwith' is ordinary Scottish English here ('outwith the licence',
        # 'outwith the SOP') and all 19 T4 positives it produced were false.
        "has_outwith_keyword": bool(OUTWITH_RE.search(summary_text)),
        "has_outwith_status": bool(OUTWITH_STATUS_RE.search(
            collapse(rec["meta"]["cludo:status"] or ""))),
        # Kept so the guard can find a status that names the Code's scope but
        # that the rule above did not match -- i.e. a spelling we have not seen.
        "status_mentions_scope": bool(OUTWITH_SCOPE_MENTION_RE.search(
            collapse(rec["meta"]["cludo:status"] or ""))),
        "status_line": collapse(rec["meta"]["cludo:status"] or ""),
        # N2, second half. The rule read the h1 and the title only, and
        # AUTH/1921/11/06 states it in the OUTCOME SLOT instead -- '... The
        # Panel decided to take its concerns up as a separate complaint in
        # accordance with Paragraph 17 of the Constitution and Procedure (Case
        # AUTH/1936/12/06)' -- so the case published `paragraph_17: false`
        # while the same sentence was being mis-tokenised into a Code Clause 17
        # verdict row (adj-0138). Both halves of that defect are fixed here.
        # The four outcome slots are added as a witness, and the widening is
        # measured rather than assumed: 'paragraph 17' appears in the slots of
        # exactly ONE file corpus-wide, this one, so the class goes 3 -> 4 and
        # nothing else can move.
        "has_paragraph_17": bool(
            PARAGRAPH_17_RE.search(h1) or PARAGRAPH_17_RE.search(title)
            or any(PARAGRAPH_17_RE.search(rec["outcomes"][k] or "") for k in (
                "meta_clause_breach", "meta_clause_no_breach",
                "info_breach_clauses", "info_no_breach_clauses"))
            or der["source_integrity"]["status"] == "ok_arises_from_other_case"),
        "has_voluntary_admission": bool(
            VOLUNTARY_PREFIX_RE.match(collapse(complainant))
            or re.search(r"voluntary\s+admission", h1, re.I)
            or re.search(r"voluntary\s+admission", complainant, re.I)),
    }


def read_corpus(pdf_by_html):
    """records.jsonl and derived.jsonl in lockstep, one line at a time."""
    digests = []
    with RECORDS.open(encoding="utf-8") as rf, DERIVED.open(encoding="utf-8") as df:
        for rline, dline in zip(rf, df):
            if not rline.strip():
                continue
            rec = json.loads(rline)
            der = json.loads(dline)
            if rec["file"] != der["file"]:
                raise SystemExit(
                    f"records.jsonl and derived.jsonl are out of step at {rec['file']} / {der['file']}"
                )
            digests.append(digest(rec, der, pdf_by_html.get(rec["file"])))
    return digests


def read_clause_inventory():
    """year -> the set of clause and subclause numbers that edition contains.

    R20 level 2 only lets the case's own year decide a row whose clause EXISTS
    in that edition -- 2008's Clause 20 is 'The Use of Consultants' with no
    subclauses at all, so a (2008, 20.2) row is proof the year is wrong. The
    `by_edition` keys are included: a clause the 2012 editions render
    differently is parked there rather than in `subclauses`, and it exists just
    the same.

    A year absent from both files (2001, 2003, and any edition the PMCPA never
    published in a form we parse) yields no entry, and `clause_exists` returns
    None for it -- unknown, not absent. Asserting from silence is what the
    absence rule forbids.
    """
    inventory = {}
    for path in (HTML_CLAUSES, PDF_CLAUSES):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                year = int(row["code_year"])
                seen = inventory.setdefault(year, set())
                seen.add(str(row["clause_number"]))
                for sub in row.get("subclauses") or []:
                    if sub.get("number") is not None:
                        seen.add(str(sub["number"]))
                for number in row.get("subclause_numbers") or []:
                    seen.add(str(number))
                for number in row.get("by_edition") or {}:
                    seen.add(str(number))
    return inventory


def clause_exists(inventory, year, clause):
    """True / False / None (that edition is not on disk, so we cannot say)."""
    if year is None or year not in inventory:
        return None
    return str(clause) in inventory[year] or str(clause).split(".")[0] in inventory[year]


def read_pdf_records():
    """html_file -> the PDF rendition's file and flow text (the 13, C8/C9).

    The flow is kept, not summarised: it is the REPORT for these 13 cases, so
    the segment pass has to slice it exactly as it slices a pane. 13 flows come
    to ~230 KB, which is a rounding error against the 202 MB of records.
    """
    out = {}
    if not PDF_RECORDS.exists():
        return out
    with PDF_RECORDS.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            p = json.loads(line)
            out[p["html_file"]] = {"file": p["file"], "flow_text": p["flow_text"]}
    return out


# ---------------------------------------------------------------------------
# corpus fold tables (built from the digests, so the build stays one pass)
# ---------------------------------------------------------------------------

def build_respondent_keys(digests):
    """The fold keys that appear as a RESPONDENT somewhere in the corpus.

    This is also what makes a complainant a company: naming a firm the PMCPA has
    adjudicated against elsewhere. The complainant field cannot be used to
    DEFINE the company vocabulary, because most of its values are roles rather
    than firms -- 'Ex-employee', 'Anonymous, non-contactable', 'Hospital doctor'
    all fold perfectly well and none of them is a company.
    """
    keys = set()
    for d in digests:
        names, _ = respondent_candidates(d["meta_respondent"], d["identity"]["h1_text"])
        for name in names:
            k = fold_key(name)
            if k:
                keys.add(k)
    return keys


def build_company_fold(digests, respondent_keys):
    """fold key -> canonical display name, over the company vocabulary only.

    The display name is the most frequent verbatim spelling in the group, so it
    is always a form the source actually used. Ties break by shortest then
    lexicographic -- a total order, so the table is deterministic.
    """
    seen = {}
    for d in digests:
        names, _ = respondent_candidates(d["meta_respondent"], d["identity"]["h1_text"])
        for name in names:
            seen.setdefault(fold_key(name), Counter())[name] += 1
        # Complainant-side spellings vote on the DISPLAY name of a company the
        # respondent side already established -- an inter-company complaint may
        # spell a firm better than its own case ever did.
        for name in split_companies(collapse(d["meta_complainant"])):
            key = fold_key(name)
            if key in respondent_keys:
                seen.setdefault(key, Counter())[name] += 1
    return {
        key: sorted(counts.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        for key, counts in seen.items()
    }


# ---------------------------------------------------------------------------
# field resolution
# ---------------------------------------------------------------------------

def resolve_case_number(num, ident):
    """C1. The filename is authoritative for WHICH cases exist -- it is the only
    slot that is per-case rather than per-page, and it disagrees with the h1
    exactly once (AUTH-2030-7-07's h1 names AUTH/2029/7/07 as well, which has
    its own page). The display form prefers info and meta where they agree, the
    h1 otherwise."""
    sources = {
        "filename_case_numbers": list(ident["filename_case_numbers"]),
        "meta_case_number": ident["meta_case_number"],
        "meta_case_numbers_parsed": list(ident["meta_case_numbers_parsed"]),
        "info_case_number": ident["info_case_number"],
        "info_case_numbers_parsed": list(ident["info_case_numbers_parsed"]),
        "h1_text": ident["h1_text"],
        "h1_case_numbers_parsed": list(ident["h1_case_numbers_parsed"]),
        "title_case_numbers_parsed": list(ident["title_case_numbers_parsed"]),
        "index_case_number": ident["index_case_number"],
    }
    fn = set(ident["filename_case_numbers"])
    meta = set(ident["meta_case_numbers_parsed"])
    info = set(ident["info_case_numbers_parsed"])
    h1 = set(ident["h1_case_numbers_parsed"])
    title = set(ident["title_case_numbers_parsed"])

    stated = [s for s in (meta, info, h1, title) if s]
    if stated and all(s == fn for s in stated):
        basis = "case_number_unanimous"
    elif num in meta and num in info:
        basis = "case_number_info_meta_agree"
    elif num in h1:
        basis = "case_number_h1_preferred"
    else:
        basis = "case_number_filename_only"
    return canon(num, basis, sources)


def resolve_title(d):
    """C2/C3. The h1 wins: <title> carries procedural suffixes and mojibake, and
    the report's own title line mis-states the case number on 32 pages."""
    ident = d["identity"]
    sources = {
        "h1_text": ident["h1_text"],
        "title_text": ident["title_text"],
        "report_title_line": d["report_title_line"],
    }
    value = ident["h1_text"] if ident["h1_text"] else ident["title_text"]
    status = d["source_integrity"]["status"]
    if status == "ok_with_title_typo":
        return canon(value, "title_h1_over_report_title_typo", sources,
                     d["source_integrity"]["note"])
    if status == "report_pane_mismatch":
        return canon(value, "title_h1_over_foreign_report_title", sources,
                     d["source_integrity"]["note"])
    if collapse(ident["title_text"]) != collapse(ident["h1_text"]):
        return canon(value, "title_h1_over_title_tag", sources)
    return canon(value, "unanimous", sources)


def resolve_subject(d):
    """C4. The hero h2 is what a reader sees, so it wins over cludo:description
    on the 853 pages they differ.

    Some of those differences are SEMANTIC, not cosmetic -- AUTH-1877-8-06's
    description reads 'No breach of undertaking' against an h2 of 'Alleged
    breach of undertaking'. Semantics cannot be detected mechanically, so this
    phase records both and states which won; SPEC §3 C4 routes the genuine
    flips to adjudication once they are identified by reading.
    """
    h2 = d["identity"]["h2_text"]
    desc_plain = strip_tags(d["meta_description"])
    sources = {
        "h2_text": h2,
        "meta_description": d["meta_description"],
        "meta_description_plain": desc_plain,
    }
    if h2 is None or not collapse(h2):
        if desc_plain:
            return canon(desc_plain, "subject_description_fallback", sources)
        return canon(None, "subject_absent", sources)
    if not desc_plain:
        # The h2 is the only slot stating a subject -- nothing was beaten, so
        # this is not a preference.
        return canon(collapse(h2), "sole_source", sources)
    if collapse(h2) == desc_plain:
        return canon(collapse(h2), "unanimous", sources)
    return canon(collapse(h2), "subject_h2_preferred", sources)


def resolve_respondent(d, company_fold):
    names, basis = respondent_candidates(d["meta_respondent"], d["identity"]["h1_text"])
    sources = {"meta_respondent": d["meta_respondent"], "h1_text": d["identity"]["h1_text"]}
    canonical = [company_fold.get(fold_key(n), n) for n in names]
    if not canonical:
        return canon(None, basis, sources), []
    # Joint respondents keep one canonical string (SPEC §2 gives respondent a
    # single value); the individual firms go to entities.companies, which is
    # what redaction and the memorisation probes read.
    return canon(" and ".join(canonical), basis, sources), canonical


def resolve_complainant(d, company_fold, respondent_keys):
    """C7, rewritten PROSE-FIRST in l2.3 (bench/review/DEFECTS.md D5).

    The meta slot is one or two words and from 2023 it is usually the bare
    'Complainant', which maps to category `other`, anonymous False and
    contactable null -- while the report's own opening paragraph says, in so
    many words, 'A complaint was received from an anonymous, non-contactable
    complainant, who described themselves as a health professional'. The old
    rule therefore published a metadata FALSEHOOD on the majority of the recent
    era, which is what blocked T1.

    So each of the three fields is resolved separately, prose first:

      anonymous    l2.4, tri-state (DEFECTS D6). A STATEMENT of anonymity
                   ('an anonymous complainant', 'wished to remain anonymous',
                   the meta/h1 token 'Anonymous') -> True. A STATEMENT of
                   naming ('a named, contactable complainant', or a complainant
                   slot naming companies the corpus knows as respondents)
                   -> False. Nothing stated -> null. The old rule read silence
                   as False and so published 'Complainant: not anonymous' on
                   every case whose site metadata is the bare word
                   'Complainant', which states nothing at all
      contactable  prose only, and the FINAL state where the opening states two
                   (DEFECTS D7). The meta slot never states it in a form this
                   build reads, so silence leaves it null (SPEC §6c)
      category     a prose SELF-DESCRIPTION beats `other` and beats
                   `anonymous` (which is not a role -- it is the boolean above,
                   and keeping it here duplicated one fact into two fields).
                   It does not displace a structural meta category, and where
                   the two agree the basis is `unanimous`

    Every field's losing value stays in `sources`, and the sentence fragment
    each prose verdict was read from is quoted there too.
    """
    verbatim = d["meta_complainant"]
    flat = collapse(verbatim)
    p = d["complainant_prose"]
    sources = {
        "meta_complainant": verbatim,
        "h1_text": d["identity"]["h1_text"],
        "prose_source": p["source"],
        "prose_anonymous": p["anonymous"],
        "prose_named": p["named"],
        "prose_contactable": p["contactable"],
        "prose_category": p["category"],
        "prose_category_frame": p["category_frame"],
        "prose_role_verbatim": p["role_verbatim"],
        "prose_quotes": dict(p["quotes"]),
    }

    # Wave C. The WHOLE value is tried against the respondent fold before it is
    # split, because `split_companies` splits on the literal word ' and ' and
    # some companies have one in their name. 'Johnson and Johnson' became two
    # 'Johnson' fragments, neither of which folds to a respondent, so an
    # inter-company complaint published `category: other, anonymous: null`
    # (AUTH/2067/11/07, whose own opening reads 'Johnson & Johnson Consumer
    # Services Eame alleged that Pfizer's ...'). PROTECTED_COMPANY_NAMES exists
    # for this and holds two hand-typed names; the fold already knows the
    # answer, so asking it first is the rule rather than a third name in a
    # list. Measured over all 2,004 cases: this fires on exactly one value --
    # AUTH/2067's -- and no other whole value folds to a respondent that its
    # split parts do not.
    whole_key = fold_key(flat)
    if whole_key and whole_key in respondent_keys:
        companies = [company_fold.get(whole_key, flat)]
    else:
        companies = []
        for part in split_companies(flat):
            key = fold_key(part)
            if key and key in respondent_keys:
                companies.append(company_fold.get(key, part))

    if companies:
        meta_category = "company"
    else:
        meta_category = "other"
        for name, pat in CATEGORY_RULES:
            if pat.search(flat):
                meta_category = name
                break
    sources["meta_category"] = meta_category

    meta_anonymous = bool(
        ANONYMOUS_RE.search(flat) or ANONYMOUS_RE.search(d["identity"]["h1_text"] or ""))
    sources["meta_anonymous"] = meta_anonymous
    # l2.4. The meta slot's positive evidence AGAINST anonymity: it names
    # companies the corpus knows as respondents (an inter-company complaint), or
    # it says the complainant was named ('Named, contactable complainant v CSL
    # Vifor'). A role token -- 'Pharmacist', 'Ex-employee' -- names nobody and
    # is not evidence either way.
    meta_named = bool(companies) or bool(PROSE_NAMED_RE.search(flat))
    sources["meta_named"] = meta_named

    notes = []

    # -- category ----------------------------------------------------------
    prose_category = p["category"]
    if prose_category == "industry":
        # SPEC §4 has no `industry` value and l2 does not invent an eleventh.
        notes.append(f"prose self-description is an industry role "
                     f"({p['role_verbatim']!r}); SPEC §4 has no such category, so `other` stands")
        prose_category = None
    if prose_category is None:
        category, cat_basis = meta_category, "complainant_meta_vocabulary"
    elif prose_category == meta_category:
        category, cat_basis = meta_category, "unanimous"
    elif meta_category == EMPLOYMENT_CATEGORY and prose_category in ROLES_OUTRANKED_BY_EMPLOYMENT:
        # See ROLES_OUTRANKED_BY_EMPLOYMENT: both readings are true of one
        # person and the standing wins over the attribute. 5 cases corpus-wide.
        category, cat_basis = meta_category, "complainant_employment_outranks_role"
        notes.append(f"the report describes the complainant as {p['role_verbatim']!r} and the "
                     f"meta slot states an employment standing ({meta_category!r}); both are "
                     f"true of one person and the employment standing is published, because it "
                     f"is a relation to a party where the role is an attribute of the person")
    elif meta_category in STRUCTURAL_CATEGORIES:
        category, cat_basis = meta_category, "complainant_meta_structural_category"
        notes.append(f"the report describes the complainant as {p['role_verbatim']!r}; "
                     f"the meta slot states the structural category {meta_category!r} and keeps it")
    else:
        # Wave C: the basis names WHICH reading produced the value. A role the
        # report's own opening sentence states about the complainant is not a
        # self-description, and calling it one would be a false receipt.
        category = prose_category
        cat_basis = ("complainant_prose_narrator_role"
                     if p["category_frame"] == "narrator_subject"
                     else "complainant_prose_self_description")

    # -- anonymity (l2.4, tri-state; see the docstring) ---------------------
    if p["anonymous"]:
        anonymous = True
        anon_basis = "unanimous" if meta_anonymous else "complainant_prose_anonymity"
        if p["named"] or meta_named:
            # One file states both: 'A named contactable complainant, who
            # described him/herself as a healthcare practitioner and wished to
            # remain anonymous' (AUTH/3464/1/21). The complainant identified
            # themselves to the Authority and asked not to be identified
            # further; the anonymity statement is the one about how the case was
            # published, so it wins and the naming statement is recorded.
            notes.append("the opening states BOTH that the complainant was named and that they "
                         "were/wished to remain anonymous; the anonymity statement wins and the "
                         "naming statement is quoted in sources.prose_quotes.named")
    elif meta_anonymous:
        anonymous, anon_basis = True, "complainant_meta_vocabulary"
    elif p["named"]:
        anonymous, anon_basis = False, "complainant_prose_named"
    elif meta_named:
        anonymous, anon_basis = False, "complainant_meta_named_company"
    else:
        anonymous, anon_basis = None, "unresolved_anonymity_not_stated"

    # -- contactability ----------------------------------------------------
    contactable = p["contactable"]
    if contactable is None:
        cont_basis = "unresolved_contactability_not_stated"
    elif p["contactable_conflict"]:
        cont_basis = "complainant_prose_contactability_final"
        notes.append("the opening states contactability both ways (the corpus writes "
                     "'originally contactable but later became non-contactable'); the FINAL "
                     "state is recorded and the superseded statement is quoted in "
                     "sources.prose_quotes.contactable_superseded")
    else:
        cont_basis = "complainant_prose_contactability"

    # The record's single `basis` names the strongest claim made about this
    # complainant; the per-field bases are carried alongside so no field's
    # provenance is lost to the collapse.
    prose_bases = ("complainant_prose_self_description", "complainant_prose_narrator_role",
                   "complainant_prose_anonymity",
                   "complainant_prose_named", "complainant_prose_contactability",
                   "complainant_prose_contactability_final")
    if cat_basis in prose_bases or anon_basis in prose_bases or cont_basis in prose_bases:
        basis = "complainant_prose_first"
    elif cat_basis == "unanimous" or anon_basis == "unanimous":
        basis = "unanimous"
    else:
        basis = "complainant_meta_vocabulary"

    return {
        "verbatim": verbatim,
        "category": category,
        "anonymous": anonymous,
        "contactable": contactable,
        "basis": basis,
        "field_basis": {"category": cat_basis, "anonymous": anon_basis,
                        "contactable": cont_basis},
        "sources": sources,
        "note": "; ".join(notes) or None,
    }, companies


def resolve_code_year(d):
    """C5. 1848 pages state a Code year; 54 do not. Inference from the received
    date needs the Code commencement table (data/code/, not yet acquired), so
    those 54 stay null with a basis that names what is missing -- never a
    silent guess, never a quiet zero."""
    meta = d["meta_code_year"]
    info = d["info_code_year"]
    sources = {"meta_applicable_code_year": meta, "info_applicable_code_year": info}
    if re.fullmatch(r"\d{4}", collapse(meta)):
        note = None
        if collapse(info) and collapse(info) != collapse(meta):
            note = f"info-holder states {collapse(info)}"
        return canon(int(collapse(meta)), "code_year_meta", sources, note)
    if re.fullmatch(r"\d{4}", collapse(info)):
        return canon(int(collapse(info)), "code_year_info_fallback", sources)
    return canon(None, "unresolved_pending_code_dates", sources)


# Wave C, the PLURAL. A report that covers more than one case writes 'Cases
# completed 15 May 2006', and the singular-only pattern read nothing there, so
# R12's third witness -- the report's own trailer, COMPARED rather than merely
# recorded -- was silently absent on every multi-case page. Measured: 41
# completed dates the corpus states and this build was not reading; 35 agree
# with the published value and 6 differ by 1-4 days inside the same year, which
# is R12's own `date_slots_over_trailer_same_year` class ('not an error', 233
# cases). No value moves; 41 receipts stop being null and 6 disagreements stop
# being invisible. `Complaints received` is given the same plural for symmetry
# (the corpus writes the received half singular on every page measured, so it
# adds nothing today and cannot go stale later).
TRAILER_DATE_RE = {
    "received": re.compile(r"Complaints?\s+received\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.I),
    "completed": re.compile(r"Cases?\s+completed\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.I),
}
# A PDF substitution is the report for that case; the HTML report pane is
# empty or belongs to another case.  Date receipts must therefore come from
# the same PDF flow as the segments and ruling receipts.  This exact line
# grammar finds received+completed in 12 of the 13 flows and completed-only in
# AUTH/3015's voluntary-admission report.  Every flow has a completion witness.
PDF_TRAILER_DATE_LINE_RE = re.compile(
    r"\b(?:Complaints?|Cases?)\s+(?:received|completed)\s+"
    r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\b", re.I)


def pdf_trailer_lines(flow, file):
    lines = [m.group(0) for m in PDF_TRAILER_DATE_LINE_RE.finditer(flow or "")]
    if not any(TRAILER_DATE_RE["completed"].search(line) for line in lines):
        raise SystemExit(
            f"REFUSING: PDF substitute {file} has no parsed completion trailer; "
            "do not fall back to its absent/foreign HTML report pane.")
    return lines
# ... and the COMPOSITE form, where the shared trailer gives each case its own
# date: 'Cases completed AUTH/2154/8/08 7 October 2008 AUTH/2155/8/08 9 October
# 2008'. The date does not follow the word 'completed' there, it follows a case
# NUMBER, so the patterns above read nothing and AUTH/2154/8/08 published its
# sibling's completion date on a `unanimous` basis with the trailer recorded as
# unparsed -- a false receipt, since the trailer is a third source that
# disagrees. Keyed on the case's OWN number: a sibling's pair is never read for
# it, and a page whose stored trailer line holds only one of the two pairs
# leaves the other case's trailer null, which is the honest answer.
TRAILER_PER_CASE_RE = re.compile(
    r"([A-Z]{3,}\s*/?\s*\d{2,5}\s*/\s*\d{1,2}\s*/\s*\d{2,4})\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})")
# Case numbers encode the month and year the complaint was received
# (AUTH/3891/4/24 -> April 2024). This comes from the listing, not the report
# body, so it is independent of both the meta/info slots and the trailer -- the
# third witness that makes a slot-vs-trailer disagreement decidable.
CASENO_MONTH_YEAR_RE = re.compile(r"/(\d{1,2})/(\d{2})\s*$")


def _spelled_date(text):
    d = INFO_DATE_RE.match(collapse(text))
    if not d:
        return None
    mo = MONTHS.get(d.group(2).lower())
    if not mo or not 1 <= int(d.group(1)) <= 31:
        return None
    return f"{int(d.group(3)):04d}-{mo:02d}-{int(d.group(1)):02d}"


def trailer_date(trailer, field):
    """The date the case report itself states, or None if it states none.

    Parsed with the same INFO_DATE_RE/MONTHS the info-holder slot uses, so the
    trailer is read by exactly the rules the other spelled-out slot is read by.
    """
    m = TRAILER_DATE_RE[field].search(" ".join(trailer or []))
    return _spelled_date(m.group(1)) if m else None


def trailer_per_case_dates(trailer):
    """{case number -> date} where the trailer gives cases their own dates.

    'Cases completed AUTH/2154/8/08 7 October 2008 AUTH/2155/8/08 9 October
    2008'. This is NOT resolved into `completed` here, and the reason is
    structural: dates are resolved once per PAGE and shared by every case the
    page reports, so attaching one of these pairs would give both siblings
    whichever date happened to be read. What it does is make the omission
    VISIBLE -- resolve_date records the pairs in a note, so AUTH/2154/8/08's
    receipt stops saying `unanimous` with an unparsed trailer when the trailer
    is in fact a third source that disagrees with both slots (the audit's own
    finding: 2154 publishes 9 October, its own trailer says 7 October, and 9
    October is its sibling's date). Resolving it needs per-case date
    resolution, which is a change to the build's shape, not a pattern fix.
    """
    out = {}
    for pair in TRAILER_PER_CASE_RE.finditer(" ".join(trailer or [])):
        m = next(CASE_NUM_IN_TEXT_RE.finditer(pair.group(1)), None)
        iso = _spelled_date(pair.group(2))
        if m is not None and iso:
            out.setdefault(normalise_case_number(m), iso)
    return out


def caseno_year(case_numbers):
    """The receipt year the case numbers encode, or None if they do not agree.

    A multi-case report resolves one date for the page, so the witness has to
    be a consensus of the numbers on that page; siblings that disagree leave
    the disagreement unresolved rather than letting the first number decide.
    """
    years = set()
    for num in case_numbers or []:
        m = CASENO_MONTH_YEAR_RE.search((num or "").strip())
        if not m:
            return None
        years.add(2000 + int(m.group(2)))
    return years.pop() if len(years) == 1 else None


def resolve_date(meta_value, info_value, trailer, field, case_numbers=None,
                 received_iso=None):
    """C10. Both source slots kept, and since R12 the report's own trailer line
    is COMPARED rather than merely recorded.

    The meta slot is DD/MM/YYYY, the info-holder spells the month; they agree on
    the day everywhere, so `unanimous` is the normal basis. On 19 received and
    13 completed dates the trailer disagrees with them, which the original build
    deferred as phase 2 because two witnesses cannot settle a disagreement.

    A third witness settles it. For `received`, the case number encodes the
    month and year of receipt and is independent of the report body. For
    `completed`, a case cannot complete before it was received, which rules out
    whichever candidate precedes receipt. Where neither decides, the slot value
    is kept and the basis SAYS it is unresolved -- never a silent pick.
    """
    # Set on every case, including the early return below: the corpus invariant
    # is that all cases share one key set, and the two 'Interim case report'
    # pages would otherwise be missing this slot.
    t_iso = trailer_date(trailer, field)
    sources = {"meta": meta_value, "info": info_value,
               "report_trailer_lines": list(trailer),
               "report_trailer_parsed": t_iso}
    # Wave C. The per-case pairs go in the NOTE, not in `sources`, and that is
    # deliberate: every date adjudication is pinned to a sha of this receipts
    # dict (apply_adjudication), so adding a key to it would make three
    # reviewed decisions read as stale against evidence that has not changed.
    # The note is where a statement about the receipts belongs anyway.
    per_case = (trailer_per_case_dates(trailer)
                if field == "completed" and t_iso is None else {})
    m_iso, i_iso = parse_iso_date(meta_value, info_value)
    slot, slot_basis, note = None, None, None
    if m_iso and i_iso:
        slot = m_iso if m_iso == i_iso else i_iso
        slot_basis = "unanimous" if m_iso == i_iso else "date_info_preferred"
        note = None if m_iso == i_iso else f"meta states {m_iso}"
    elif m_iso:
        slot, slot_basis = m_iso, "date_meta_only"
    elif i_iso:
        slot, slot_basis = i_iso, "date_info_only"
    else:
        return canon(None, "unresolved_no_parsable_date", sources)

    if t_iso is None or t_iso == slot:
        if per_case:
            # Stated, not resolved: the note is what stops `unanimous` from
            # reading as "and the report agrees" when the report gives each
            # case on the page its own date and one of them differs.
            note = ((note + "; ") if note else "") + (
                "the trailer states a date per case on this page ("
                + ", ".join(f"{k} {v}" for k, v in sorted(per_case.items()))
                + "); dates are resolved per page, so it is recorded rather "
                  "than applied")
        return canon(slot, slot_basis, sources, note)

    # --- the slots and the report disagree ----------------------------------
    # Two different questions, and only the first one matters downstream (era
    # splits, the post-cutoff holdout and every per-year count key on the year).
    #
    # Sub-year differences are the common case and are NOT errors: across 233
    # received and 138 completed disagreements the modal gap is the report
    # stating ONE DAY LATER than the slots (39% and 62% respectively), which is
    # a systematic convention difference, not a typo. Calling the trailer wrong
    # there would overclaim, so the slots are kept and the trailer date is
    # recorded in `sources` for anyone who needs day-level precision.
    if int(t_iso[:4]) == int(slot[:4]):
        return canon(slot, "date_slots_over_trailer_same_year", sources,
                     f"report trailer states {t_iso}, {_day_gap(t_iso, slot)}; "
                     "same year, so no downstream field is affected")

    # A year-level disagreement is a real conflict. The third witness decides in
    # ONE direction only, and round 4 is why.
    #
    # The case number and the meta/info slots are NOT independent: both come from
    # the PMCPA's own record-keeping, so both carry the same error when a case is
    # logged late. AUTH/3543/7/21 says it outright -- "Whilst the case was
    # received in August 2020, in error it was not processed until July 2021" --
    # and the number (/7/21) plus both slots (21/07/2021) encode the PROCESSING
    # date while the trailer states the receipt. CASE/0251/07/25 is the same
    # shape with the number itself mistyped (the company's own letter quotes
    # AUTH/0251/07/24).
    #
    # So:
    #   trailer + case number AGAINST the slots  -> decisive. Two witnesses with
    #       different provenance agree; this is the 13 corrections R12 made.
    #   slots + case number AGAINST the trailer  -> NOT decisive, because those
    #       two share a cause. Previously this branch published the slot value
    #       and asserted "the trailer is the typo", which was false twice.
    #       It is now recorded as unresolved and the disagreement is stated.
    if field == "received":
        cy = caseno_year(case_numbers)
        if cy is not None and cy == int(t_iso[:4]):
            return canon(t_iso, "date_trailer_over_slots_caseno_agrees", sources,
                         f"slots state {slot}; the case number and the report "
                         f"both put receipt in {cy}")
        if cy is not None and cy == int(slot[:4]):
            return canon(slot, "date_slots_trailer_disagrees_unresolved", sources,
                         f"report trailer states {t_iso}; the slots and the case "
                         f"number say {cy}, but those share the PMCPA's own "
                         "record-keeping and both carry a late-logging error "
                         "together (see AUTH/3543/7/21). Slot value kept, "
                         "conflict NOT resolved")
    elif field == "completed" and received_iso:
        slot_impossible = slot < received_iso
        trailer_impossible = t_iso < received_iso
        if slot_impossible and not trailer_impossible:
            return canon(t_iso, "date_trailer_over_slots_receipt_constraint", sources,
                         f"slots state {slot}, which precedes receipt on {received_iso}")
        if trailer_impossible and not slot_impossible:
            return canon(slot, "date_slots_over_trailer_receipt_constraint", sources,
                         f"report trailer states {t_iso}, which precedes receipt "
                         f"on {received_iso}")

    return canon(slot, "date_slots_trailer_disagrees_unresolved", sources,
                 f"report trailer states {t_iso}, a different YEAR, and no third "
                 "witness decides")


def _day_gap(a, b):
    """'+1 day' / '-3 days' between two ISO dates, for a human-readable note."""
    ta, tb = [tuple(int(x) for x in s.split("-")) for s in (a, b)]
    days = (ta[0] - tb[0]) * 365 + (ta[1] - tb[1]) * 30 + (ta[2] - tb[2])
    return f"{days:+d} day" + ("" if abs(days) == 1 else "s") + " (approx)"


def fold_appeal(value):
    """One appeal slot -> 'none' | 'respondent' | 'complainant' | 'both' | None,
    plus whether the text describes a Panel referral. None = the slot says
    nothing about who appealed."""
    t = collapse(value)
    if not t:
        return None, False
    referral = bool(PANEL_REFERRAL_RE.search(t))
    if NO_APPEAL_RE.match(t):
        return "none", referral
    resp = bool(RESPONDENT_APPELLANT_RE.search(t))
    comp = bool(COMPLAINANT_APPELLANT_RE.search(t))
    if resp and comp:
        return "both", referral
    if resp:
        return "respondent", referral
    if comp:
        return "complainant", referral
    if BOTH_PARTIES_RE.search(t):
        return "both", referral
    if referral:
        return "none", True
    return None, False


def heading_appellant(heading, respondent_keys, complainant_keys):
    """One 'APPEAL BY <party>' heading -> the SIDE it names, or None.

    None means the heading names nobody this case has a party record for, which
    happens for three honest reasons and is an ABSENCE of evidence, never a
    contradiction:

      * the heading belongs to another case's report spliced into this page --
        'APPEAL BY PFIZER' on AUTH/2091/1/08, a GE Healthcare v Guerbet case
        whose pane carries AUTH/2093/1/08 in full; 'APPEAL FROM CHIESI' on the
        three sibling files of the Chiesi case in that co-reported series;
      * the party is named by a nickname the party fields do not carry --
        'APPEAL BY THE ALLIANCE' for Pfizer and Bristol-Myers Squibb;
      * the complainant is a company the meta slot spells as a role.

    Company names are matched on l2's own fold key, then on token containment
    in EITHER direction, which is what makes 'APPEAL BY LILLY' meet respondent
    'Eli Lilly' and 'APPEAL BY ASTELLAS EUROPE' meet respondent 'Astellas'.
    The RESPONDENT side wins a name that matches both, because the complainant
    slot carries the respondent's own name on the voluntary admissions
    ('Voluntary Admission by AstraZeneca') and on the 'X v Y' strings
    ('NHS Muslim affairs specialist v ProStrakan') -- 2 pages, both of them
    respondent appeals.
    """
    party = APPEAL_HEADING_PARTY_RE.match(heading or "")
    if not party:
        return None
    party = collapse(APPEAL_HEADING_GLOSS_RE.sub("", party.group(1)))
    words = {w for w in re.sub(r"[^a-z ]", " ", party.casefold()).split() if w != "the"}
    if words and words <= COMPLAINANT_HEADING_WORDS:
        return "complainant"

    def side_of(key):
        tokens = set(key.split())
        for side, keys in (("respondent", respondent_keys), ("complainant", complainant_keys)):
            for other in keys:
                other_tokens = set(other.split())
                if tokens == other_tokens or tokens < other_tokens or other_tokens < tokens:
                    return side
        return None

    sides = set()
    for name in split_companies(party):
        key = fold_key(name)
        if not key:
            continue
        side = side_of(key)
        if side is None:
            return None                 # one unmatched name and the heading is mute
        sides.add(side)
    if not sides:
        return None
    return sides.pop() if len(sides) == 1 else "both"


def resolve_appeal(d, respondent_companies, complainant_companies, warn):
    """C6. 72 distinct meta forms fold to four outcomes.

    The 64 pages with both slots empty stay UNRESOLVED. SPEC §4 allows empty ->
    none only where the report text confirms it, and reading the report text is
    phase 3; folding them to `none` now would put 64 unverified 'no appeal'
    claims into the appeal counts that T3 is built on.

    R18 adds a third witness to `by`: the report's own 'APPEAL BY <party>'
    heading. It speaks ONLY to who appealed, never to whether an appeal
    happened -- a heading over a slot that folds to 'no appeal' is a
    contradiction about a different question (2 files: AUTH/2296/1/10 and the
    AUTH/2825+2826/3/16 pair, which the slots record as a Panel referral), and
    turning those cases' `appealed` to null would delete every T1 item they
    carry on evidence nobody has adjudicated. Those are warned, not repaired.

    Where slot and heading name different SIDES, `by` is refused rather than
    picked -- and an adjudication may still decide it. The slot has been wrong
    at source twice (AUTH/3028/3/18, AUTH/3535/7/21, both adjudicated) and the
    heading can belong to a foreign report, so neither witness dominates.
    """
    meta_raw = d["appeal"]["meta_appeal"]
    info_raw = d["appeal"]["info_appeal_hearing"]
    headings = list(d["appeal_headings"])
    sources = {"meta_appeal": meta_raw, "info_appeal_hearing": info_raw,
               "report_appeal_headings": headings}
    meta_by, meta_ref = fold_appeal(meta_raw)
    info_by, info_ref = fold_appeal(info_raw)

    respondent_keys = {k for k in (fold_key(n) for n in respondent_companies) if k}
    complainant_keys = {k for k in (fold_key(n) for n in complainant_companies) if k}
    heading_sides = {s for s in (heading_appellant(h, respondent_keys, complainant_keys)
                                 for h in headings) if s}
    heading_by = None if not heading_sides else (
        heading_sides.copy().pop() if len(heading_sides) == 1 else "both")

    if meta_by is None and info_by is None:
        basis = "unresolved_appeal_empty" if not (collapse(meta_raw) or collapse(info_raw)) \
            else "unresolved_appeal_unmapped"
        return {"appealed": None, "by": None, "basis": basis, "sources": sources, "note": None}

    if meta_by is not None and info_by is not None and meta_by != info_by:
        by, referral, basis = info_by, info_ref, "appeal_info_preferred"
        note = f"meta folds to {meta_by}"
    else:
        by = meta_by if meta_by is not None else info_by
        referral = meta_ref or info_ref
        basis = "appeal_fold_table" if (meta_by is not None and info_by is not None) else "sole_source"
        note = None
    if by == "none" and referral:
        basis = "appeal_panel_referral"
        note = "no party appealed; the Panel referred the case to the Appeal Board"

    appealed = by != "none"
    if heading_by is not None:
        if by == "none":
            warn("appeal_heading_on_unappealed_slot",
                 f"slots fold to none ({basis}); report heading(s) {headings} name {heading_by}")
        elif heading_by == by:
            basis = "appeal_slots_and_headings_agree"
        else:
            warn("appeal_by_heading_conflicts_slots",
                 f"slots say {by}; report heading(s) {headings} say {heading_by}")
            note = (f"the appeal slots fold to {by}; the report's own heading(s) "
                    f"{headings} name the {heading_by}. Two witnesses, no third: "
                    f"`by` is refused, not picked")
            by, basis = None, "appeal_by_heading_conflicts_slots"
    return {"appealed": appealed, "by": by, "basis": basis, "sources": sources, "note": note}


def resolve_sanctions(d, verdicts):
    """Undertaking, additional sanctions, and the Clause 2 censure.

    'Sanctions applied' has exactly one value corpus-wide ('Undertaking
    received', 1202 pages), so it carries no information beyond presence.

    The additional sanctions are read from the rendered chips and cross-checked
    against the meta CSV. Where they disagree the chips win: they are what the
    page shows. Duplicate chips are real in the source (one page renders
    Advertisement as both a link and a plain chip) but say nothing twice, so the
    canonical list is deduplicated in stated order and the raw chip list stays
    in the receipts.
    """
    o = d["outcomes"]
    chips_raw = list(d["sanction_chips"])
    chips = []
    for c in chips_raw:
        if c not in chips:
            chips.append(c)
    csv = []
    for c in (o["meta_additional_sanctions"] or "").split(","):
        c = collapse(c)
        if c and c not in csv:
            csv.append(c)

    sources = {
        "info_sanctions_applied": o["info_sanctions_applied"],
        "meta_sanctions_applied": o["meta_sanctions_applied"],
        "info_additional_sanctions_chips": chips_raw,
        "meta_additional_sanctions": o["meta_additional_sanctions"],
        "meta_clause_breach": o["meta_clause_breach"],
        "meta_clause_no_breach": o["meta_clause_no_breach"],
        "info_breach_clauses": o["info_breach_clauses"],
        "info_no_breach_clauses": o["info_no_breach_clauses"],
    }
    basis, note = "unanimous", None
    if sorted(chips) != sorted(csv):
        basis = "sanctions_chips_over_meta_csv"
        note = f"meta CSV lists {csv or '[]'}"

    # Phase 2 refinement. Phase 1 looked for a bare '2' anywhere in a breach
    # clause list, which counted a clause that was ruled NO breach and, on the
    # 265 both-ways files, counted the same clause twice over. It now reads the
    # resolved verdicts: a censure exists where some row for clause 2 (bare --
    # never 2.1, never a year) ends in breach.
    censure = any(v["clause"] == "2" and v["final"] == "breach" for v in verdicts)
    return {
        "undertaking": bool(collapse(o["info_sanctions_applied"])),
        "additional": chips,
        "clause_2_censure": censure,
        "basis": basis,
        "sources": sources,
        "note": note,
    }


def arbitrate_year(clause, witnesses, d, case_year, inventory, reviewed=None):
    """R20. Which Code EDITION one verdict row is keyed to.

    Evidence order, not preference order. The three structured witnesses -- the
    clause chip's href year, the outcome table row's '(YYYY Code)' scope, and
    the case's Applicable Code year -- disagree on 125 of 7,696 rows and NEITHER
    dominates, which is the whole finding: the chip serves the 2019 Clause 6.1
    (journal page counts) to AUTH/3777/6/23, whose report says 'The outcome
    under the 2021 Code was ... No Breach of Clause 6.1 ... must not be
    misleading'; and the case slot is the wrong one on AUTH/3722/1/23, whose
    outcome list scopes Clause 9.1 to the 2016 and 2019 Codes on a 2021-tagged
    case. So the report's own words decide, at the finest grain they are
    offered at:

      1  the year the report attaches to THIS CLAUSE, in prose or in the
         outcome list's own '(YYYY Code)' scope. Two or more different years on
         one clause is a REFUSAL, not a tie to break: it means the clause was
         genuinely ruled under two editions in one case, and one row cannot
         carry two.
      2  no year on this clause, but exactly one edition stated for the case
         ('considered under the 2019 Code'). More than one, and the case is
         multi-edition, so the case slot is one edition among several rather
         than the answer -- refused for the same reason.
      3  no edition stated at all: the case's own year, and only where the
         clause EXISTS in that edition. This is the eight 2016-tagged cases
         whose reports name no edition anywhere and whose chips point at 2019,
         an edition that did not exist when they completed.
      4  otherwise the year is refused (null) and bench excludes the item.

    Levels 1-4 run only where the structured witnesses DISAGREE. Where they
    agree, the per-clause prose is consulted only through LEVEL 0: a reviewed
    row in l2/adjudications.json, one per (case, clause), carrying the verbatim
    quote and the reader's classification of it.

    Level 0 is deliberately not a widened pattern (2026-08-10 reading round,
    R20's residue). The population -- per-clause prose naming a year the agreed
    chip and case slot do not -- measured 134 rows over 72 cases (132 items),
    and reading every one of them MOVED 34 and refused 12: 88 stay where they
    were, because the mention is a party's submission (13), a precedent recital
    (9), a renumbering gloss (6), the case preparation manager's letter that
    the Panel then overrides (22), or the adjudicator naming several editions
    of which the agreed year is one (38). A regex cannot tell those apart --
    AUTH/2220/3/09 ('the clauses cited ... were the same ... thus considered
    under the 2008 Code') is the corpus's own proof -- so the promotion is only
    ever as good as a human reading, and this level refuses to act without one.
    A row whose receipts have changed since it was read fails the build
    (`source_sha256`), the same protection every other adjudication carries.

    Level 0 outranks agreement AND disagreement: a read row is the best
    evidence available, so it is not sensible to let a chip overrule it.
    """
    if reviewed is not None:
        return (reviewed["value"], reviewed["id"], reviewed["justification"],
                [reviewed["quote"]])

    stated = {y for y in witnesses if y is not None}
    if len(stated) <= 1:
        return (next(iter(stated), None), "year_uncontested", None, [])

    clause_prose = d["clause_year_prose"].get(clause) or {}
    per_clause = {int(y) for y in clause_prose}
    # The outcome table's scope is per-clause evidence as well as a witness --
    # 'No Breach of Clause 12.2 (2016 Code)' is the report stating the edition
    # for that clause, in the same breath as the ruling.
    per_clause |= {y for y, names in witnesses.items()
                   if y is not None and "outcome_table_scope" in names}
    quotes = [f"clause {clause}: {q}" for _, q in sorted(clause_prose.items())]
    if len(per_clause) == 1:
        return (next(iter(per_clause)), "year_clause_prose", None, quotes)
    if len(per_clause) > 1:
        return (None, "year_undecided_clause_prose_conflict",
                f"the report attaches {sorted(per_clause)} to clause {clause}", quotes)

    decisive = {int(y): q for y, q in d["edition_prose_decisive"].items()}
    if len(decisive) == 1:
        year, quote = next(iter(decisive.items()))
        return (year, "year_case_prose", None, [quote])
    if len(decisive) > 1:
        return (None, "year_undecided_multi_edition_case",
                f"the report states editions {sorted(decisive)} for the case and none for "
                f"clause {clause}",
                [q for _, q in sorted(decisive.items())])

    if case_year is not None and clause_exists(inventory, case_year, clause) is not False:
        return (case_year, "year_case_slot", None, [])
    return (None, "year_undecided_no_witness",
            f"witnesses {sorted(stated)} disagree; the report states no edition and the case's "
            f"own year is {case_year!r}"
            + ("" if case_year is None else f", whose Code has no clause {clause}"), [])


def resolve_verdicts(d, appeal, code_year, inventory, shared, warn,
                     arbitration_log=None, case_number=None,
                     year_reviews=None, used_adjudications=None, emit_shas=None,
                     used_prose_reviews=None):
    """C11/C12, SPEC §5. The one hard algorithm -- rewritten in l2.2 (DEFECTS D3).

    Which sources may CREATE a row is the first decision, and it is not
    symmetric with which sources are read. The info-holder clause lists and the
    meta clause CSVs create rows: they are the case's own statement of its
    outcome. PROSE_ONLY_VERDICT_READ is the sole, finite reviewed exception,
    pinned to complete-report hashes. The outcome table and banner headings are
    CROSS-CHECKS (SPEC §5 closing paragraph) -- a table row naming a clause no
    list states still raises a warning rather than inventing a verdict.

    What changed. The lists state the case's FINAL position, and l2.1 read that
    position BACKWARDS onto the Panel: a clause in both lists on a case appealed
    by the respondent was recorded as panel=breach, appeal_board=no_breach, and
    a clause confirmed by appeal-side prose had panel inferred from it. Both
    audits found the result wrong wherever they checked it -- T3's `overturned`
    class was dual listing rather than flips (10/10 sampled), and 75+ T1 items
    labelled the Appeal Board's outcome as the Panel's (17/17 sampled).

    So attribution is now one-directional and prose-only:

        panel        <- this case's panel_ruling prose, and nothing else
        appeal_board <- this case's appeal-side prose, and nothing else
        final        <- the outcome lists, and nothing else

    with ONE deliberate exception, which is not an inference: on a case that was
    never appealed no other body ruled, so the lists ARE the Panel's ruling and
    final = panel. That is the ~10k-item spine both audits verified.

    Where a clause cannot be reduced to one Panel ruling -- both polarities in
    the Panel's own prose, or both lists on an unappealed case -- the row is
    marked `dual_ruling` and carries panel = null. `final` stays non-null (SPEC
    §6c), so the row still records that the clause was ruled on; it is the
    BENCH that refuses to make a binary item out of it (bench/generate.py).
    """
    case_year = code_year["value"]
    prose_reviews = {
        clause: review
        for (review_case, clause), review in PROSE_ONLY_VERDICT_READ.items()
        if review_case == case_number
    }

    chip_year, chip_slug = {}, {}
    for clause, year, slug in d["breach_chips"] + d["no_breach_chips"]:
        if year is not None:
            chip_year.setdefault(clause, year)
        if slug is not None:
            chip_slug.setdefault(clause, slug)
    # Where a clause has no chip, a '(YYYY Code)' scope on its outcome-table row
    # is the only other year evidence the corpus offers; it is used only when
    # every table row naming the clause agrees on one year.
    table_year = {}
    for row in d["table_rows"]:
        if row["code_year"] is not None:
            table_year.setdefault(row["clause"], set()).add(row["code_year"])

    def year_of(clause, row_sources):
        """(year, basis, note, quotes) -- see `arbitrate_year`.

        R20 replaced a preference order (chip, then table scope, then the case
        slot) with a witness set. The old rule never noticed a disagreement; it
        simply took the first witness that had an opinion, which is how 137
        items came to serve clause text from an edition their own case report
        contradicts.

        `row_sources` is the row's own receipts, passed in rather than built
        after the fact so a reviewed year decision can be pinned to the same sha
        `apply_verdict_adjudication` pins a polarity decision to. Nothing in the
        receipts depends on the year, so hoisting them changes no sha.
        """
        reviewed = None
        prose_review = prose_reviews.get(clause)
        adj = (year_reviews or {}).get((case_number, f"verdicts[{clause}].code_year"))
        if prose_review is not None and adj is not None:
            raise SystemExit(
                f"REFUSING: {case_number} clause {clause} is decided by both "
                "PROSE_ONLY_VERDICT_READ and a code-year adjudication")
        if adj is not None:
            got = receipts_sha(row_sources)
            if emit_shas is not None:
                emit_shas[adj["id"]] = got
            elif adj.get("source_sha256") != got:
                raise SystemExit(
                    f"adjudication {adj['id']} ({case_number}/verdicts[{clause}].code_year) is "
                    f"stale: it was reviewed against receipts hashing to "
                    f"{adj.get('source_sha256')}, the build computed {got}. Re-read the row, "
                    f"then re-pin with --emit-adjudication-shas.")
            if used_adjudications is not None:
                used_adjudications.add(adj["id"])
            reviewed = adj
        witnesses = {}
        if clause in chip_year:
            witnesses.setdefault(chip_year[clause], []).append("chip")
        years = table_year.get(clause)
        if years and len(years) == 1:
            witnesses.setdefault(next(iter(years)), []).append("outcome_table_scope")
        elif years and len(years) > 1:
            for y in sorted(years):
                witnesses.setdefault(y, []).append("outcome_table_scope")
        if case_year is not None:
            witnesses.setdefault(case_year, []).append("case_code_year")
        if prose_review is not None:
            year = prose_review["code_year"]
            note = prose_review["reason"]
            basis = "year_prose_only_reviewed"
            if year is None:
                warn("code_year_arbitration_refused", f"{clause}: {note}")
            elif clause in chip_year and chip_year[clause] != year:
                warn("code_year_arbitration_displaced_chip",
                     f"{clause}: chip {chip_year[clause]} -> {year} ({basis})")
            if arbitration_log is not None:
                arbitration_log.append({
                    "case_number": case_number,
                    "clause": clause,
                    "resolved_code_year": year,
                    "basis": basis,
                    "witnesses": {str(y): sorted(names)
                                  for y, names in sorted(witnesses.items())},
                    "note": note,
                    "quotes": list(prose_review["quotes"]),
                })
            return year, basis, note
        year, basis, note, quotes = arbitrate_year(clause, witnesses, d, case_year, inventory,
                                                   reviewed)
        if basis != "year_uncontested":
            # A null year under any basis but `year_uncontested` is a refusal --
            # the rule bases spell it (`year_undecided_*`) and a reviewed
            # refusal carries an adjudication id instead, so the NULL is what
            # both have in common.
            if year is None:
                warn("code_year_arbitration_refused", f"{clause}: {note}")
            elif clause in chip_year and chip_year[clause] != year:
                warn("code_year_arbitration_displaced_chip",
                     f"{clause}: chip {chip_year[clause]} -> {year} ({basis})")
            if arbitration_log is not None:
                arbitration_log.append({
                    "case_number": case_number,
                    "clause": clause,
                    "resolved_code_year": year,
                    "basis": basis,
                    "witnesses": {str(y): sorted(names) for y, names in sorted(witnesses.items())},
                    "note": note,
                    "quotes": quotes,
                })
        return year, basis, note

    breach_chip_clauses = {c for c, _, _ in d["breach_chips"]}
    no_breach_chip_clauses = {c for c, _, _ in d["no_breach_chips"]}
    slot_stated_breach = set(d["flat_breach"]) | breach_chip_clauses
    slot_stated_no_breach = set(d["flat_no_breach"]) | no_breach_chip_clauses
    stated_breach = set(slot_stated_breach)
    stated_no_breach = set(slot_stated_no_breach)
    for clause, review in prose_reviews.items():
        if clause in slot_stated_breach or clause in slot_stated_no_breach:
            raise SystemExit(
                f"REFUSING: PROSE_ONLY_VERDICT_READ[{case_number}, {clause}] is no longer "
                "prose-only: an outcome slot or chip now states the clause. Re-read and retire "
                "or replace the registry entry.")
        if used_prose_reviews is not None:
            used_prose_reviews.add((case_number, clause))
        if review["decision"] == "accept":
            target = stated_breach if review["final"] == "breach" else stated_no_breach
            target.add(clause)

    by_clause_table = {}
    for row in d["table_rows"]:
        by_clause_table.setdefault(row["clause"], []).append(row)

    appealed, by = appeal["appealed"], appeal["by"]
    verdicts = []
    for clause in sorted(stated_breach | stated_no_breach, key=clause_sort_key):
        in_b, in_nb = clause in stated_breach, clause in stated_no_breach
        panel_b = clause in d["prose_panel_breach"]
        panel_nb = clause in d["prose_panel_no_breach"]
        appeal_b = clause in d["prose_appeal_breach"]
        appeal_nb = clause in d["prose_appeal_no_breach"]

        # What each body's own prose says, reduced to one polarity or refused.
        panel_prose = _single_polarity(panel_b, panel_nb)
        appeal_prose = _single_polarity(appeal_b, appeal_nb)

        # R28 stage 1. The APPEAL axis, which had no dual detection at all: the
        # Board ruling both ways on one clause simply produced `appeal_board:
        # null` above and no record that a dual is WHY. It is a separate flag
        # from `dual_ruling`, not a second writer of it, because the two axes
        # answer different questions -- AUTH/1841's Panel ruled one way on 7.2
        # and its T1 label is sound; it is the Board that ruled twice, so it is
        # T3 that has no single transition to test. Folding both into one flag
        # would delete ~40 correctly-labelled T1/T1-triage items.
        #
        # Every candidate is READ, not pattern-decided: 43 (case, clause) rows
        # in the corpus have both polarities on the appeal side and 7 of them
        # are not duals at all (a recital of the Panel's ruling inside the
        # Board's section, a mis-kinded segment). APPEAL_DUAL_READ carries the
        # reading; `check_appeal_dual_coverage` refuses the build on a row that
        # is not in it.
        panel = appeal_board = None
        dual = dual_board = False
        note = None
        if appeal_b and appeal_nb or clause in d["dual_screen_appeal"]:
            dual_board = dual_read_is_dual(case_number, clause, "appeal_board")
        # `final` always comes from the lists. A clause in BOTH lists records
        # that at least one breach of it was ruled, which is what 'breach'
        # asserts; the no-breach entry concerns a different allegation.
        final = "breach" if in_b else "no_breach"

        # R28 stage 1. A clause the loose screen sees both ways on the PANEL
        # axis, where the reader does not, is not decided here -- it is a
        # DEMAND that the row be read (`check_dual_read_coverage` refuses until
        # it is). 24 of the 27 current members read as not-dual (precedent
        # citation, the Clause 2 censure idiom, an Appeal Board sentence
        # sitting inside a panel_ruling segment, a sibling case's ruling on a
        # shared report, a screen artefact). The two that ARE duals go through
        # l2/adjudications.json, the reviewed sha-pinned route adj-0008/0009
        # already used for exactly this: a code table may record a reading, but
        # overwriting a published Panel ruling is an adjudication's job, and
        # the entry then carries a reviewer and a stale-fix pin.
        if not dual and clause in d["dual_screen_panel"] \
                and dual_read_is_dual(case_number, clause, "panel") \
                and (year_reviews or {}).get((case_number, f"verdicts[{clause}]"),
                                             {}).get("value") != "dual":
            raise SystemExit(
                f"REFUSING: {case_number} clause {clause} is registered in DUAL_READ as a genuine "
                f"PANEL dual, but no adjudication says so and stage 1 does not let a code table "
                f"overwrite a Panel ruling. File one (value 'dual', the adj-0008 shape) with the "
                f"quote, or change the registry row to a reason.")

        if panel_b and panel_nb:
            # The Panel itself ruled this clause both ways -- different
            # materials in one case. There is no single Panel ruling.
            dual, basis = True, "verdict_dual_panel_prose"
            note = "the Panel's own prose states both polarities for this clause"
            warn("prose_dual_ruling", clause)
            appeal_board = appeal_prose if appealed else None
        elif in_b and in_nb and appealed is False:
            dual, basis = True, "verdict_unappealed_dual_listed"
            note = "both polarity lists name this clause on a case with no appeal"
            warn("conflict_unappealed_both_lists", clause)
        elif appealed is False:
            panel, basis = final, "verdict_unappealed"
            if panel_prose is not None and panel_prose != final:
                note = (f"the Panel's ruling prose states {panel_prose}; the outcome lists say "
                        f"{final} and win, pending hand review (DEFECTS D3)")
                warn("prose_contradicts_unappealed_list", f"{clause} prose={panel_prose} list={final}")
        elif appealed is None:
            basis = "verdict_appeal_status_unresolved"
        else:
            panel, appeal_board = panel_prose, appeal_prose
            basis = ("verdict_appealed_prose_attributed"
                     if (panel or appeal_board) else "verdict_appealed_unattributed")

        review = prose_reviews.get(clause)
        if review is not None:
            if review["decision"] != "accept":
                raise SystemExit(
                    f"REFUSING: refused PROSE_ONLY_VERDICT_READ[{case_number}, {clause}] "
                    "nevertheless created a verdict row")
            basis = "verdict_prose_only_reviewed"
            note = f"{note}; {review['reason']}" if note else review["reason"]

        if dual_board:
            # Both polarities on the appeal axis, READ and confirmed: there is
            # no single Appeal Board ruling to publish. On 38 of the 40 the
            # reader itself saw both ways, so this is already the value
            # `_single_polarity` refused into; on AUTH/1941/1/07's two it is a
            # repair, because the reader saw only the 'upheld ... breaches of
            # Clauses 7.2, 7.3 and 7.4' half and missed 'no breach of either
            # Clause 7.3 or 7.4 and ruled accordingly'. Both halves are in
            # `rulings` either way.
            appeal_board = None
            reason = "the Appeal Board's own prose states both polarities for this clause"
            note = f"{note}; {reason}" if note else reason
            warn("prose_dual_appeal_ruling", clause)

        table_rows = by_clause_table.get(clause, [])
        mult = max((r["multiplicity"] for r in table_rows if r["multiplicity"]), default=None)
        if shared:
            note = f"{note}; shared_report_evidence" if note else "shared_report_evidence"
        # Built BEFORE the year is arbitrated, because a reviewed year decision
        # is pinned to this dict's sha (the same receipts a reviewed polarity
        # decision is pinned to). No key here depends on the year, so the shas
        # every existing adjudication carries are unmoved by the hoist.
        row_sources = {
            "meta_clause_breach": clause in d["meta_breach_tokens"],
            "meta_clause_no_breach": clause in d["meta_no_breach_tokens"],
            "info_breach_clauses": clause in d["info_breach_tokens"],
            "info_no_breach_clauses": clause in d["info_no_breach_tokens"],
            "chip_breach": clause in breach_chip_clauses,
            "chip_no_breach": clause in no_breach_chip_clauses,
            "chip_code_year": chip_year.get(clause),
            # R20 year receipts. The two report-side witnesses the arbitration
            # reads, per row, so `code_year_basis` can be re-argued from the
            # record alone.
            "clause_code_year_prose": dict(d["clause_year_prose"].get(clause) or {}),
            "case_code_year_prose_decisive": dict(d["edition_prose_decisive"]),
            "case_code_year_prose_weak": dict(d["edition_prose_weak"]),
            "table_verdict_texts": [r["verdict_text"] for r in table_rows],
            "banner_headings": [b for b in d["banner_headings"] if clause in clause_tokens(b)],
            "prose_panel_breach": panel_b,
            "prose_panel_no_breach": panel_nb,
            "prose_appeal_board_breach": appeal_b,
            "prose_appeal_board_no_breach": appeal_nb,
            # The appeal-side statement came from the appellant's GROUNDS rather
            # than the Appeal Board's ruling section. Recorded because those
            # segments are the riskiest attribution source in the corpus (they
            # restate the Panel constantly), so a consumer can see which rows
            # depend on them.
            "prose_appeal_from_grounds": (
                clause in d["prose_appeal_grounds_breach"]
                or clause in d["prose_appeal_grounds_no_breach"]),
            "stated_multiplicity": mult,
            "shared_report_evidence": shared,
        }
        row_year, row_year_basis, row_year_note = year_of(clause, row_sources)
        if row_year_basis.startswith("adj-"):
            # The reviewed decision's own words, kept on the row the way
            # `apply_verdict_adjudication` keeps a reviewed polarity's.
            note = f"{note}; {row_year_note}" if note else row_year_note
        row = {
            "clause": clause,
            "code_year": row_year,
            # R20. WHICH evidence decided the year, kept beside the year itself
            # so a null can be told apart from the 57 cases that simply state no
            # Code year (`year_uncontested` with nothing to contest): a null
            # under ANY OTHER basis -- a `year_undecided_*` rule or a reviewed
            # adjudication id -- means bench must refuse the item.
            "code_year_basis": row_year_basis,
            # Q3/R6. Which reviewed decision, if any, attributed a body's
            # ruling on this row -- kept apart from `basis` so a row whose dual
            # state or polarity came from a RULE still says which rule
            # (`check_dual_read_coverage` reads `basis` to exempt a
            # rule-decided dual, and lost three rows when this shared it).
            "attribution_basis": None,
            # Always 0. The corpus DOES state multiplicity -- 584 outcome-table
            # rows carry '(xN)' -- but a multiplier is a count of rulings on
            # different materials that the outcome slots never separate, so
            # splitting it into N rows would emit N indistinguishable verdicts
            # and break the meta-CSV cardinality reconciliation SPEC §5 asks
            # for. The count is kept in sources instead.
            "occurrence": 0,
            "clause_slug": chip_slug.get(clause),
            "panel": panel,
            "appeal_board": appeal_board,
            "final": final,
            # l2.2: no single ruling can be attributed to the Panel for this
            # clause. The row exists (the clause WAS ruled on) but it is not a
            # binary label, and bench/generate.py excludes it from T1/T3.
            "dual_ruling": dual,
            # R28 stage 1. The same statement about the APPEAL BOARD, kept
            # apart from `dual_ruling` (see the reading above): the Board ruled
            # this clause both ways, so `appeal_board` is null and no single
            # panel->board transition exists for T3, but the Panel's own ruling
            # -- and so the T1 label -- may be perfectly single.
            "dual_ruling_appeal_board": dual_board,
            "flipped_on_appeal": bool(
                appealed and panel is not None and appeal_board is not None and panel != appeal_board),
            "basis": basis,
            "sources": row_sources,
            # R28 stage 1. One entry per prose ruling this build ATTRIBUTED to
            # a body for this clause, in pane order, each with the sentence
            # verbatim and its offsets. A single ruling gets a one-entry list:
            # uniform beats special-cased, and `len(rulings) > 1` is then the
            # per-regard fact the scalars above cannot carry.
            #
            # What is ordinarily NOT here, and is R27's measured limit rather
            # than an oversight: rulings on clauses the outcome lists never
            # name. Only the finite PROSE_ONLY_VERDICT_READ entries create an
            # exception; the other 400+ unlisted prose mentions still have no
            # row to hang from.
            "rulings": [
                {k: r[k] for k in ("body", "polarity", "regard", "regard_ref", "quote",
                                   "char_start", "char_end", "file", "pane",
                                   "segment_kind", "source_frame")}
                for r in d["ruling_records"] if r["clause"] == clause
            ],
            "note": note,
        }
        if review is not None:
            expected = {
                "panel": review["panel"],
                "appeal_board": review["appeal_board"],
                "final": review["final"],
                "code_year": review["code_year"],
                "code_year_basis": "year_prose_only_reviewed",
                "dual_ruling": review["dual_ruling"],
                "dual_ruling_appeal_board": review["dual_ruling_appeal_board"],
                "basis": "verdict_prose_only_reviewed",
            }
            got = {field: row[field] for field in expected}
            if got != expected:
                raise SystemExit(
                    f"REFUSING: PROSE_ONLY_VERDICT_READ[{case_number}, {clause}] expected "
                    f"{expected}, but ordinary attribution plus the reviewed row produced {got}")
        verdicts.append(row)

    for clause, review in prose_reviews.items():
        present = [v for v in verdicts if v["clause"] == clause]
        expected_count = 1 if review["decision"] == "accept" else 0
        if len(present) != expected_count:
            raise SystemExit(
                f"REFUSING: PROSE_ONLY_VERDICT_READ[{case_number}, {clause}] decision "
                f"{review['decision']!r} requires {expected_count} row(s), got {len(present)}")

    # -- cross-checks: warnings, never failures, never edits ---------------
    finals = Counter(v["final"] for v in verdicts)
    banners = " ".join(d["banner_headings"]).upper()
    if "NO BREACH" in banners and not finals["no_breach"]:
        warn("banner_no_breach_without_no_breach_row", None)
    if re.search(r"(?<!NO )BREACH", banners) and not finals["breach"]:
        warn("banner_breach_without_breach_row", None)
    known = {v["clause"] for v in verdicts}
    for clause in sorted({r["clause"] for r in d["table_rows"]} - known, key=clause_sort_key):
        warn("table_clause_absent_from_verdicts", clause)

    # The finer slot-typo guard (pre-freeze repair pass, 2026-08-10).
    # `check_clause_witness_coverage` catches a slot clause the report never
    # names ANYWHERE. It is silent on the harder shape, which AUTH/2505/5/12
    # is: the numeral 3.1 IS in the text -- the Authority's scoping sentence
    # types it -- but the Panel's one disposing sentence rules 3.2, so four of
    # the five rows carry that sentence as their witness and the 3.1 row
    # carries none. Two conjoined receipts name that shape without naming
    # anything else:
    #
    #   (a) the row has no ruling witness at all (`rulings == []`), and
    #   (b) the Panel's prose rules a clause in the SAME numeric family that
    #       no outcome list states -- a sibling, not R27's general class of
    #       417 unlisted-clause rulings.
    #
    # Measured over the whole corpus: (a) alone is 458 rows, (a)+(b) is 13
    # rows in 11 cases (14 in 12 before adj-0159 took AUTH/2505/5/12 out of
    # the class by correcting it -- a repaired row acquires the witness and
    # stops firing, which is the guard behaving). The 13 split into two
    # sub-shapes and only the first was read here:
    #
    #   SAME-RANK SIBLINGS, 7 rows. AUTH/1992/4/07 (3.2 | 3.1),
    #   AUTH/1857/6/06 (20.4 | 20.2), AUTH/1916/11/06 (18.1 | 18.3),
    #   AUTH/2075/12/07 (4.3 | 4.1), AUTH/2260/9/09 (7.9 | 7.3),
    #   AUTH/2336/7/10 (9.5 | 9.1), AUTH/3024/3/18 (15.2 | 15.9). The first
    #   two are read and recorded in CLAUSE_PROSE_SIBLING_READ; the other
    #   five are NEW and unread, registered by this warning and by DEFECTS.
    #
    #   PARENT FOR CHILD, 6 rows. The slot names the parent and the Panel
    #   rules the subclause -- AUTH/1937/1/07 (21 | 21.1), AUTH/1970/3/07
    #   (10 | 10.1), AUTH/2823/2/16 (20 | 20.1), AUTH/3131/12/18 (14 | 14.2,
    #   22 | 22.1+22.4, 9 | 9.1). This is the shape CLAUSE_WITNESS_READ's
    #   AUTH/2845/5/16 entry already names from the other direction, and it
    #   is usually not a defect at all: the parent IS what the outcome list
    #   published. Kept in the class rather than filtered out, because
    #   deciding it needs the same reading and a filter would hide it.
    #
    # A WARNING and deliberately not a refusal or an auto-rename, because the
    # class does not point one way. On AUTH/2505 the SLOT is wrong (the
    # respondent answers 3.2 and the case is off-label promotion of a licensed
    # medicine, which is 2011 Clause 3.2); on AUTH/1992 the same shape has the
    # PROSE wrong (both slots, the Authority's scoping sentence and the
    # response all say 3.2, and the subject is again off-licence promotion).
    # A rule that renamed the row would have corrected one and corrupted the
    # other. It surfaces the pair; a reader decides; adj-0159 is the decision.
    unlisted_panel_prose = {r["clause"] for r in d["ruling_records"]
                            if r["body"] == "panel" and r["clause"] not in known}
    for v in verdicts:
        if v["rulings"]:
            continue
        sibs = sorted((u for u in unlisted_panel_prose
                       if u != v["clause"]
                       and u.split(".")[0] == v["clause"].split(".")[0]),
                      key=clause_sort_key)
        if sibs:
            warn("published_clause_unwitnessed_prose_sibling",
                 f"{v['clause']} unwitnessed; panel prose rules unlisted {', '.join(sibs)}")
    if d["table_rows"] and not (stated_breach or stated_no_breach):
        warn("outcome_slots_empty_but_table_present", None)
    return verdicts


def _single_polarity(said_breach, said_no_breach):
    """One polarity, or None where the prose said both or neither."""
    if said_breach and not said_no_breach:
        return "breach"
    if said_no_breach and not said_breach:
        return "no_breach"
    return None


def clause_sort_key(clause):
    """Numeric, not lexicographic: clause 10.1 comes after 9.1."""
    parts = clause.split(".")
    return (int(parts[0]), int(parts[1]) if len(parts) > 1 else -1)


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def load_adjudications():
    if not ADJUDICATIONS.exists():
        return {}
    entries = json.loads(ADJUDICATIONS.read_text(encoding="utf-8"))
    out = {}
    for e in entries:
        key = (e["case"], e["field"])
        if key in out:
            raise SystemExit(f"two adjudications target {key}; append-only means one decision per slot")
        out[key] = e
    return out


def apply_adjudication(case, field, value_obj, adjudications, used, emit_shas):
    """Irreducible judgment, applied to a canonical value.

    The entry is pinned to a sha of the L1 slot values it was reviewed against.
    A build that finds a different sha fails: the decision was made about
    evidence that no longer exists, and applying it anyway is exactly the
    stale-fix class this file is meant to prevent (SPEC §1.1).
    """
    adj = adjudications.get((case, field))
    if not adj:
        return value_obj
    got = receipts_sha(value_obj["sources"])
    if emit_shas is not None:
        emit_shas[adj["id"]] = got
    elif adj.get("source_sha256") != got:
        raise SystemExit(
            f"adjudication {adj['id']} ({case}/{field}) is stale: it was reviewed against source "
            f"slots hashing to {adj.get('source_sha256')}, the build computed {got}. "
            f"Re-review the decision, then re-pin with --emit-adjudication-shas."
        )
    used.add(adj["id"])
    return canon(adj["value"], adj["id"], value_obj["sources"], adj["justification"])


def slot_receipts(d):
    """The evidence a clause-slot correction is reviewed against (N2).

    Both halves of the disagreement: the four hand-typed outcome slots and the
    two chip lists that CREATE verdict rows, and the set of clause numbers the
    report's own text names, which is what shows the slot to be wrong. If
    either half moves, the reading has to be redone.
    """
    o = d["outcomes"]
    return {
        "meta_clause_breach": o["meta_clause_breach"],
        "meta_clause_no_breach": o["meta_clause_no_breach"],
        "info_breach_clauses": o["info_breach_clauses"],
        "info_no_breach_clauses": o["info_no_breach_clauses"],
        "breach_chips": [list(c) for c in d["breach_chips"]],
        "no_breach_chips": [list(c) for c in d["no_breach_chips"]],
        "clause_names_in_text": list(d["clause_names_in_text"]),
    }


def apply_clause_slot_corrections(d, case_numbers, adjudications, used, emit_shas, log):
    """N2. A reviewed hand decision that an outcome SLOT mistyped a clause.

    Field `verdicts[<slot clause>].clause`; value the corrected clause number,
    or null to delete the row because the token is not a Code clause of this
    case at all (AUTH/1921/11/06's '17' is Paragraph 17 of the Constitution and
    Procedure; AUTH/2790/8/15's '5.2' says so in the slot in words).

    It is applied HERE, to the slot-derived token sets, and not to the finished
    row -- which is the whole point. The slots are what CREATE rows (SPEC §5);
    the prose attribution, the year arbitration, the receipts and the ruling
    records are all computed per clause afterwards. Correcting the token lets
    every one of them run for the clause the report actually ruled, exactly as
    if the slot had been typed right. Correcting the finished row instead would
    leave `sources.prose_panel_*` describing a different clause -- receipts that
    lie -- and would leave AUTH/1871/7/06's row unattributed while the Panel's
    'A breach of Clause 3.2 of the Code was ruled' sat unread.

    Prose-derived structures are NOT touched: `prose_*`, `dual_screen_*`,
    `ruling_records` and `clause_year_prose` already name the right clause. The
    only things rewritten are the six slot-derived ones plus the outcome table
    rows, which are a cross-check keyed on the same typed number.
    """
    fixes = {}
    for (case, field), adj in adjudications.items():
        if case not in case_numbers or not field.endswith("].clause"):
            continue
        old = field[len("verdicts["):-len("].clause")]
        got = receipts_sha(slot_receipts(d))
        if emit_shas is not None:
            emit_shas[adj["id"]] = got
        elif adj.get("source_sha256") != got:
            raise SystemExit(
                f"adjudication {adj['id']} ({case}/{field}) is stale: it was reviewed against "
                f"outcome slots hashing to {adj.get('source_sha256')}, the build computed {got}. "
                f"Re-read the case, then re-pin with --emit-adjudication-shas.")
        used.add(adj["id"])
        # A page's slots are one string, so two siblings must not correct the
        # same token differently -- that would publish two clause numbers for
        # one typed value (the failure R19's AUTH/2424+2425 pairing exists to
        # prevent, on the other page-level slot).
        if old in fixes and fixes[old]["value"] != adj["value"]:
            raise SystemExit(
                f"REFUSING: {sorted(case_numbers)} share one report and its outcome slots, but "
                f"{fixes[old]['id']} and {adj['id']} correct clause {old} to "
                f"{fixes[old]['value']!r} and {adj['value']!r}. One document, one reading.")
        fixes[old] = adj
    if not fixes:
        return d
    # A rename onto a clause the slots already state would MERGE two rows and
    # lose one silently. Where the reading says the token is a duplicate of a
    # clause already published (AUTH/2824/2/16's '15.10' beside its '15.1'), the
    # honest form is a deletion, and the entry has to say so.
    stated = set(d["flat_breach"]) | set(d["flat_no_breach"]) \
        | {c for c, _, _ in d["breach_chips"]} | {c for c, _, _ in d["no_breach_chips"]}
    for old, adj in sorted(fixes.items()):
        if adj["value"] is not None and adj["value"] in stated - {old}:
            raise SystemExit(
                f"REFUSING: {adj['id']} renames {sorted(case_numbers)} clause {old} to "
                f"{adj['value']}, which the outcome slots already state. That would merge two "
                f"verdict rows into one. If the token is a duplicate, delete it (value null).")
    d = dict(d)
    for key in ("flat_breach", "flat_no_breach", "meta_breach_tokens", "meta_no_breach_tokens",
                "info_breach_tokens", "info_no_breach_tokens"):
        out = []
        for tok in d[key]:
            adj = fixes.get(tok)
            new = tok if adj is None else adj["value"]
            if new is not None and new not in out:
                out.append(new)
        d[key] = out
    for key in ("breach_chips", "no_breach_chips"):
        out = []
        for clause, year, slug in d[key]:
            adj = fixes.get(clause)
            new = clause if adj is None else adj["value"]
            if new is not None:
                # A clause hyperlink slug is evidence about the OLD chip, not
                # about an adjudicated replacement.  Parent/child corrections
                # can safely retain their shared top-level slug; a correction
                # across top-level clauses cannot.  Keeping it made
                # CASE/0261/08/24 publish Clause 6.1 with a Clause 26 slug (and
                # did the same for two older N2 repairs).  Null is the honest
                # value where the source provides no link for the corrected
                # top-level clause.
                if (adj is not None
                        and str(clause).split(".", 1)[0] != str(new).split(".", 1)[0]):
                    slug = None
                out.append((new, year, slug))
        d[key] = out
    rows = []
    for row in d["table_rows"]:
        adj = fixes.get(row["clause"])
        if adj is None:
            rows.append(row)
        elif adj["value"] is not None:
            rows.append({**row, "clause": adj["value"]})
    d["table_rows"] = rows
    for old in sorted(fixes, key=clause_sort_key):
        for case in sorted(case_numbers):
            # The case's OWN entry, not whichever sibling's won the merge: on a
            # shared page both siblings carry their own adjudication (they must
            # agree, checked above) and logging one of them twice would make
            # the other read as a dead fix to any counter that recomputes usage
            # from the artefact.
            adj = adjudications.get((case, f"verdicts[{old}].clause"))
            if adj is None:
                continue
            log.append({
                "case_number": case,
                "from_clause": old,
                "to_clause": adj["value"],
                "adjudication": adj["id"],
                "quote": adj.get("quote"),
                "clause_names_in_text": list(d["clause_names_in_text"]),
                "justification": adj["justification"],
            })
    return d


def check_clause_witness_coverage(rows):
    """N2. A published verdict row whose clause the report never names.

    The slots are hand-typed and they mistype: 28 of 7,696 rows name a clause that
    appears nowhere in their own case's text. Every one has now been read --
    20 corrected to the clause the report rules, 7 deleted as tokens that are
    not Code clauses of the case at all, 1 kept -- and the ones kept are
    declared here with the reading. An undeclared new member stops the build,
    because a slot number nobody has checked against the report is exactly how
    AUTH/1921/11/06 came to ship two items testing the 2006 Code's samples
    clause on a case about a Mirena website.
    """
    missed = [r for r in rows if (r[0], r[1]) not in CLAUSE_WITNESS_READ]
    if missed:
        raise SystemExit(
            "REFUSING: verdict row(s) whose clause is named nowhere in the case's own "
            "text, and which are not declared in CLAUSE_WITNESS_READ:\n  "
            + "\n  ".join(f'("{c}", "{cl}"),  # panel={p} final={f}; report names {names}'
                          for c, cl, p, f, names in missed)
            + "\nRead each against the report. If the report rules a different clause, file "
              "an adjudication `verdicts[<slot clause>].clause` with the corrected number "
              "(or null to delete the row); if the clause under test is right despite the "
              "numeral's absence, declare it here with the reason.")


# N2. Rows KEPT although the numeral never appears in the case's own text.
# Key (case, clause), value the reading. One member.
CLAUSE_WITNESS_READ = {
    ("AUTH/2845/5/16", "12.1"):
        "The mirror of the parent-for-child slot defects, and the one that does not need "
        "correcting: the PMCPA's slot names the operative subclause 12.1 while the report "
        "argues the PARENT throughout -- 'the impression was one of disguised promotion in "
        "breach of Clause 12' (complaint), 'Clause 12 concerned materials and activities "
        "that were disguised' (response), 'The Panel noted that CSL Behring had cited "
        "Clause 12 although not included it in its list of alleged breaches' (ruling) -- "
        "and rules it anonymously: 'In that regard, the event could not be disguised "
        "promotion and no breach of the Code was ruled.' In the 2016 Code Clause 12 has "
        "exactly two subclauses, 12.1 (promotional material and activities must not be "
        "disguised) and 12.2 (market research), and the allegation is 12.1's in terms. The "
        "item's clause_ref is therefore honest and its served clause_text is the clause the "
        "case is about; there is nothing to correct.",
}


# The READ of the `published_clause_unwitnessed_prose_sibling` class (see the
# warning's own comment for the rule and the counts). All three members were
# read in one sitting on 2026-08-10; one is corrected, two stand. Kept as data
# next to CLAUSE_WITNESS_READ because it answers the same question about the
# same slots -- is the published number the ruled number -- and because the
# reason a row was LEFT is evidence, not an absence of work.
#
# Nothing consumes this table. That is deliberate: making it consumed would
# make it a rule, and the whole finding is that this class has no rule.
CLAUSE_PROSE_SIBLING_READ = {
    ("AUTH/2505/5/12", "3.1"):
        "CORRECTED, adj-0159 (slot -> 3.2). The Panel's one disposing sentence rules 3.2 "
        "and is the witness on all four sibling rows: 'No breach of Clauses 3.2, 9.1, 15.2, "
        "15.9 and 2 was ruled' (report 24612). The respondent answers the same number -- "
        "'ProStrakan did not believe that Clauses 3.2 or 15.2 had been breached' (13273) -- "
        "and the subject decides: Abstral held a marketing authorisation and the allegation "
        "is promotion for burns patients, which the Panel treats as such ('companies had to "
        "be extremely careful in ensuring that their medicines were not promoted for "
        "unlicensed indications', 19907). 2011 Clause 3.2 is promotion outside the terms of "
        "the authorisation; 3.1 is promotion BEFORE one is granted, which this case never "
        "raises. Only the Authority's scoping sentence and the slot derived from it say 3.1.",
    ("AUTH/1992/4/07", "3.2"):
        "LEFT ALONE. The exact mirror, and the reason the class gets a warning instead of a "
        "rename: here the LIST is right and the PROSE is the typo. Both outcome slots read "
        "'3.2, 7.2 and 15.2'; the Authority scoped it that way ('the Authority asked it to "
        "respond in relation to Clauses 3.2, 7.2 and 15.2 of the Code', report 11778) and "
        "Sanofi-Aventis answered that way ('breaches of Clauses 3.2, 7.2 and 15.2 had not "
        "occurred', 18647). Only the Panel's two closing sentences type '3.1' ('ruled no "
        "breach of Clauses 3.1, 7.2 and 15.2', 29735; 'No breach of Clauses 15.2 and 3.1 was "
        "ruled', 31988). The subject is off-licence promotion of a licensed medicine by a "
        "representative -- the complaint's own words are 'the Code which explicitly forbade "
        "off-licence promotion' -- which is 2006 Clause 3.2, the number the slots carry. The "
        "two items (T1-e5e5163b5fa5795f, T1-triage-3b43c8a29adcd81d) are served the right "
        "clause text and keep their ids; nothing is corrected.",
    ("AUTH/1857/6/06", "20.4"):
        "LEFT ALONE, and it costs nothing. The third member, the same shape again: the slots "
        "list '2, 9.1, and 20.4' while the Panel rules 20.2 beside 20.1 ('No breaches of "
        "Clauses 20.1 and 20.2 were ruled', report 40671) and the Appeal Board upholds the "
        "pair ('upheld the Panel's rulings of no breaches of Clauses 20.1 and 20.2', 84884); "
        "'20.4' appears in no ruling sentence in either pane. Which is the typo was NOT "
        "settled, and does not have to be: the 20.4 row is `verdict_appealed_unattributed` "
        "with panel null, so bench builds NO item from it (the case's items are 2, 9.1 and "
        "20.1 only). Correcting it would move no served text and no label, so it is recorded "
        "rather than adjudicated -- a repair with no consequence is churn.",
}


def apply_appeal_adjudication(case, appeal, adjudications, used, emit_shas):
    """R18. A reviewed hand decision about WHO appealed (`appeal.by`).

    The appeal object is not a `canon`, so it cannot go through
    `apply_adjudication`, but the discipline is identical: pinned to a sha of
    the slots the decision was reviewed against, refusing loudly if they move.
    Only `by` moves. `appealed` is left exactly as the slots folded it -- both
    of the two cases this exists for ARE appealed cases and only the appellant
    is wrong; an adjudication that could also flip `appealed` would be able to
    delete a case's whole verdict attribution as a side effect.
    """
    adj = adjudications.get((case, "appeal.by"))
    if not adj:
        return appeal
    got = receipts_sha(appeal["sources"])
    if emit_shas is not None:
        emit_shas[adj["id"]] = got
    elif adj.get("source_sha256") != got:
        raise SystemExit(
            f"adjudication {adj['id']} ({case}/appeal.by) is stale: it was reviewed against "
            f"source slots hashing to {adj.get('source_sha256')}, the build computed {got}. "
            f"Re-review the decision, then re-pin with --emit-adjudication-shas.")
    used.add(adj["id"])
    out = dict(appeal)
    out["by"] = adj["value"]
    out["basis"] = adj["id"]
    out["note"] = adj["justification"]
    return out


def apply_complainant_adjudication(case, complainant, adjudications, used, emit_shas):
    """A reviewed hand decision about `parties.complainant.category` (SPEC §8).

    A third sibling of `apply_appeal_adjudication`: the complainant record is
    not a `canon` either, so the same pinned-sha discipline is written out
    rather than borrowed, and ONLY `category` moves. `anonymous`, `contactable`
    and their bases are left exactly as the readings folded them -- an
    adjudication that could reach them would be able to rewrite the whole
    complainant from one reviewed sentence about their role.

    It exists because some values in this slot are reachable by no safe rule,
    and each was measured before the plumbing was written rather than after:

      * AUTH/2108/3/08 + AUTH/2109/3/08 -- 'Orphan Europe complained about the
        promotion of N-carbamyl-L-glutamic acid powder ... by Special Products
        and Chemical Developments', and 'Orphan Europe SARL was the marketing
        authorization holder'. A marketing authorization holder is a
        pharmaceutical company, but the `company` category is reached only by
        folding the complainant against the RESPONDENT vocabulary, and Orphan
        Europe is a complainant in this corpus and never a respondent (its fold
        key is absent from all 217 respondent keys). The alternative is a
        company-name gazetteer, and the residue it would have to decide
        contains its own counter-example: `ESPRIT` is not a company but a
        professional group ('ESPRIT (Efficacy and Safety of Prescribing in
        Transplantation) alleged ...'), so the table could not be derived from
        spelling and would be this same reading, written 55 times.
      * AUTH/2355/9/10 -- the complainant's role is stated ONLY in the report's
        own title line, 'CASE AUTH/2355/9/10 MEMBER OF THE PUBLIC v TAKEDA'
        (report pane char 0). The meta slot is the bare 'Complainant', the h1
        reads 'Complainant v Takeda', and the body opening states no role.
        `report_title_line` is not read by `resolve_complainant` at all, and
        making it a general source was measured and REFUSED: applying
        CATEGORY_RULES to the title side disagrees with the published category
        on 379 of the 1,036 cases that parse, 177 of them company->other.
        Restricted to the last-resort position it would decide 171 cases over
        57 values and move 7 -- a hand-decided table, i.e. this same reading,
        written 57 times, and two of its rows would need owner sign-off
        anyway. The other 6 members are registered as an open residual; this
        one is fixed because it was read.
      * AUTH/3067/9/18 -- the captured phrase says the complainant was a FRIEND
        OF a current Chiesi employee, not an employee. A lexical negative for
        `employee` would also suppress genuine roles, while treating every
        "friend of" phrase as `other` would be an unmeasured semantic rule. The
        reviewed relation is therefore recorded as a one-case decision.
    """
    adj = adjudications.get((case, "parties.complainant.category"))
    if not adj:
        return complainant
    got = receipts_sha(complainant["sources"])
    if emit_shas is not None:
        emit_shas[adj["id"]] = got
    elif adj.get("source_sha256") != got:
        raise SystemExit(
            f"adjudication {adj['id']} ({case}/parties.complainant.category) is stale: it was "
            f"reviewed against source slots hashing to {adj.get('source_sha256')}, the build "
            f"computed {got}. Re-review the decision, then re-pin with "
            f"--emit-adjudication-shas.")
    used.add(adj["id"])
    out = dict(complainant)
    out["category"] = adj["value"]
    out["field_basis"] = dict(complainant["field_basis"], category=adj["id"])
    out["basis"] = adj["id"]
    out["note"] = adj["justification"]
    return out


def apply_verdict_adjudication(case, verdict, adjudications, used, emit_shas):
    """A reviewed hand decision about ONE verdict row (SPEC §8).

    Field name is `verdicts[<clause>]` and the value is the polarity the reviewer
    read in the ruling prose. It sets `final` -- the site's outcome LISTS are
    what an adjudication of this kind displaces -- and, on an unappealed case,
    the Panel ruling with it, because there the two are the same statement.

    Same stale-fix protection as every other adjudication: the entry is pinned
    to a sha of the row's receipts, so a build whose evidence has moved refuses
    to apply a decision that was made about different evidence.
    """
    adj = adjudications.get((case, f"verdicts[{verdict['clause']}]"))
    if not adj:
        return verdict
    got = receipts_sha(verdict["sources"])
    if emit_shas is not None:
        emit_shas[adj["id"]] = got
    elif adj.get("source_sha256") != got:
        raise SystemExit(
            f"adjudication {adj['id']} ({case}/verdicts[{verdict['clause']}]) is stale: it was "
            f"reviewed against receipts hashing to {adj.get('source_sha256')}, the build computed "
            f"{got}. Re-review the decision, then re-pin with --emit-adjudication-shas.")
    used.add(adj["id"])
    out = dict(verdict)
    if adj["value"] == "dual":
        # R18. The reviewer read BOTH polarities in the Panel's own prose for
        # this clause -- a dual ruling in different regards that L2's own
        # detector missed because the breach half is carried by a frame it does
        # not recognise ('a breach of that clause was thus ruled',
        # '... were unacceptable in relation to Clause 18.1 and ruled
        # accordingly'). This is the same state `verdict_dual_panel_prose`
        # produces, reached by reading instead of by pattern, so it lands in the
        # same place: no single Panel ruling, and bench makes no item.
        #
        # `final` is NOT touched. The outcome lists still state the case's
        # published position and SPEC §6c keeps that non-null; what the
        # adjudication denies is that one Panel ruling can be attributed.
        out["panel"] = None
        out["dual_ruling"] = True
    else:
        out["final"] = adj["value"]
        # Only an UNAPPEALED row's panel moves with final. On an appealed case
        # the Panel ruling is a separate question that only panel prose may
        # answer, so an adjudication about the outcome lists must not fill it in
        # -- that is the back-fill D3 exists to stop.
        if verdict["basis"] in ("verdict_unappealed", "verdict_unappealed_dual_listed"):
            out["panel"] = adj["value"]
            out["dual_ruling"] = False
    out["flipped_on_appeal"] = bool(
        out["appeal_board"] is not None and out["panel"] is not None
        and out["panel"] != out["appeal_board"])
    out["basis"] = adj["id"]
    out["note"] = adj["justification"]
    return out


def apply_attribution_adjudication(case, verdict, adjudications, used, emit_shas):
    """A reviewed hand decision about WHICH BODY ruled what (SPEC §8).

    Field `verdicts[<clause>].attribution`; value a dict with either or both of
    `panel` and `appeal_board`, each 'breach', 'no_breach' or 'dual'. It exists
    because the prose reader is one-directional and prose-only by design (D3),
    and three shapes in the corpus defeat it while a reader has no trouble:

      * the Board RECITING the Panel inside its own ruling section, which makes
        the reader see both polarities and refuse (AUTH/2488/3/12: 'The Panel
        had thus considered ... Breaches of Clauses 7.2 and 7.4 were ruled' set
        against 'No breach of Clause 7.2 was ruled ... The appeal on both
        points was successful');
      * an APPEAL BOARD RULING heading L1 never recorded, so the whole appeal
        sits inside one panel_ruling segment and there is no appeal-side prose
        to read at all (AUTH/3809/8/23 -- DEFECTS R6, whose own note says the
        remedy is an L1 change or an adjudication);
      * a clause-anonymised Board ruling whose clause is named in the PREVIOUS
        sentence (AUTH/1902+1903: '... the arrangements failed to comply with
        the requirements of Clause 18.1. The Appeal Board upheld the Panel's
        ruling of a breach of the Code.').

    'dual' is admitted on BOTH axes for the same reason `dual_ruling_appeal_board`
    exists: a Board that rules one clause both ways in different regards has no
    single transition for T3 to test, and on AUTH/1902+1903 neither half is
    readable by any frame (the upheld half is clause-anonymised, the overturned
    half -- 'there was no breach of the Code in relation to arrangements for the
    TOPCAT service' -- names neither clause nor body).

    No `rulings` receipt is added. That list is what the build READ, by its own
    definition, and two of the three shapes above cannot be re-read
    independently (`v_ruling_statement` would reject a quote that names no
    clause), so a receipt filed from here would be a claim the validator cannot
    check. The evidence lives where every other adjudication's does: the
    entry's `quote` and `justification`, and `basis` names it.
    """
    adj = adjudications.get((case, f"verdicts[{verdict['clause']}].attribution"))
    if not adj:
        return verdict
    got = receipts_sha(verdict["sources"])
    if emit_shas is not None:
        emit_shas[adj["id"]] = got
    elif adj.get("source_sha256") != got:
        raise SystemExit(
            f"adjudication {adj['id']} ({case}/verdicts[{verdict['clause']}].attribution) is "
            f"stale: it was reviewed against receipts hashing to {adj.get('source_sha256')}, the "
            f"build computed {got}. Re-read the row, then re-pin with "
            f"--emit-adjudication-shas.")
    used.add(adj["id"])
    out = dict(verdict)
    value = adj["value"]
    unknown = set(value) - {"panel", "appeal_board"}
    if unknown:
        raise SystemExit(f"{adj['id']}: attribution names {sorted(unknown)}; only panel and "
                         f"appeal_board are attributable bodies")
    for body in ("panel", "appeal_board"):
        if body not in value:
            continue
        if value[body] == "dual":
            out[body] = None
            out["dual_ruling" if body == "panel" else "dual_ruling_appeal_board"] = True
        elif value[body] in ("breach", "no_breach"):
            out[body] = value[body]
        else:
            raise SystemExit(f"{adj['id']}: attribution {body}={value[body]!r} is not a polarity")
    # `final` is untouched on purpose: the outcome lists state the case's
    # published position and only a `verdicts[<clause>]` adjudication displaces
    # them. This entry says who ruled what, not what the case's outcome was.
    out["flipped_on_appeal"] = bool(
        out["panel"] is not None and out["appeal_board"] is not None
        and out["panel"] != out["appeal_board"])
    # `basis` is NOT overwritten, and the reason is a guard firing: three of
    # these rows are panel-axis duals the RULE decided, and
    # `check_dual_read_coverage` exempts a rule-decided dual by reading
    # `basis`. Replacing it made the build refuse three rows that were already
    # read -- correctly, because the row would no longer have said which rule
    # decided its dual. So the attribution gets its own basis field, the way
    # R20's year decision does (`code_year_basis`), and the row keeps both
    # provenances.
    out["attribution_basis"] = adj["id"]
    out["note"] = f'{out["note"]}; {adj["justification"]}' if out.get("note") else adj["justification"]
    return out


def build_cases(digests, pdf_by_html, adjudications, emit_shas):
    respondent_keys = build_respondent_keys(digests)
    company_fold = build_company_fold(digests, respondent_keys)
    clause_inventory = read_clause_inventory()
    used_adjudications = set()
    used_prose_reviews = set()
    warnings = {k: [] for k in WARNING_CLASSES}
    cases = []
    # R20. One row per verdict whose year witnesses disagreed, written to
    # data/l2/code_year_arbitration.jsonl. The decision has to be readable
    # without re-running the build -- the exclusions-are-durable rule, applied
    # to a repair rather than to a drop.
    year_arbitration = []
    # N2. Same shape, same reason: one row per corrected or deleted clause slot,
    # written to data/l2/clause_slot_corrections.jsonl. bench/generate.py reads
    # it to write a durable exclusion row for a clause whose verdict row was
    # deleted -- without it those item-candidates vanish with no trace, which is
    # the failure the exclusions file exists to prevent.
    slot_corrections = []
    # N2. (case, clause, panel, final, clause numbers the report names) for
    # every published row the report never names, checked once at the end.
    unwitnessed_rows = []

    for d in digests:
        pdf = pdf_by_html.get(d["file"])
        respondent, respondent_companies = resolve_respondent(d, company_fold)
        complainant, complainant_companies = resolve_complainant(d, company_fold, respondent_keys)
        title = resolve_title(d)
        subject = resolve_subject(d)
        code_year = resolve_code_year(d)
        # N2. The outcome slots are page-level, so a mistyped clause number is
        # corrected once for the whole document and every sibling inherits it --
        # the reason R19 adjudicates AUTH/2424+2425's year as a pair, applied to
        # the other page-level slot. It runs before anything reads the tokens.
        d = apply_clause_slot_corrections(
            d, set(d["cases"]), adjudications, used_adjudications, emit_shas, slot_corrections)

        # A page-level warning is booked against the page's FIRST case number,
        # in canonical order, with the file named -- the appeal fold and the
        # edition prose are properties of the document, not of one sibling.
        page_case = sorted(d["cases"], key=case_sort_key)[0] if d["cases"] else d["file"]

        def page_warn(cls, detail, _num=page_case, _file=d["file"]):
            warnings[cls].append({"case": _num, "file": _file, "detail": detail})

        appeal = resolve_appeal(d, respondent_companies, complainant_companies, page_warn)
        # Received first: its value is the receipt constraint that adjudicates a
        # completed-date disagreement (a case cannot complete before it arrives).
        received = resolve_date(d["dates"]["meta_received"], d["dates"]["info_received"],
                                d["dates"]["report_trailer_lines"], "received",
                                case_numbers=d["cases"])
        completed = resolve_date(d["dates"]["meta_completed"], d["dates"]["info_completed"],
                                 d["dates"]["report_trailer_lines"], "completed",
                                 received_iso=received.get("value"))

        # Companies named in the case, respondent first, deduplicated in stated
        # order. Complainant-side firms are included deliberately: redacting only
        # the respondent leaves 'Merck Sharp & Dohme and Roche v GlaxoSmithKline'
        # half-redacted, which defeats the memorisation probe.
        companies = []
        for name in respondent_companies + complainant_companies:
            if name not in companies:
                companies.append(name)

        procedure = {
            "voluntary_admission": d["has_voluntary_admission"],
            "abridged": d["has_abridged"],
            "paragraph_17": d["has_paragraph_17"],
            # DEFECTS D1: the status line, not the keyword.
            "outwith_scope": d["has_outwith_status"],
            # An inter-company complaint: the complainant is a firm the corpus
            # knows as a respondent elsewhere.
            "inter_company": complainant["category"] == "company",
            # The 35 stubs: an empty report pane with no PDF standing in for it.
            # AUTH-3015-1-18 is the 36th empty pane and IS substituted, so it is
            # not a stub.
            "no_report": d["report_len"] == 0 and pdf is None,
        }

        for num in d["cases"]:
            siblings = sorted((c for c in d["cases"] if c != num), key=case_sort_key)
            case_number = apply_adjudication(
                num, "case_number", resolve_case_number(num, d["identity"]),
                adjudications, used_adjudications, emit_shas)

            # A respondent slot can contain a publisher typo even when the H1,
            # report title, scoping sentence, response and ruling all agree on
            # the other company (AUTH/2679/11/13).  Keep this sha-pinned and
            # per-case, like the other semantic field adjudications.  Rebuild
            # the respondent portion of entities.companies from the corrected
            # value so the two model-facing fields cannot contradict each
            # other.
            case_respondent = apply_adjudication(
                num, "parties.respondent", respondent,
                adjudications, used_adjudications, emit_shas)
            case_respondent_companies = respondent_companies
            if case_respondent["basis"] != respondent["basis"]:
                case_respondent_companies = [
                    company_fold.get(fold_key(name), name)
                    for name in split_companies(collapse(case_respondent["value"]))
                    if fold_key(name)
                ]

            def warn(cls, detail, _num=num, _file=d["file"]):
                warnings[cls].append({"case": _num, "file": _file, "detail": detail})

            # R19. The Applicable Code year slot is adjudicable, and it is
            # adjudicated PER CASE rather than per page, because that is the
            # grain the value is published at -- a multi-case page whose year is
            # corrected has to be corrected for every sibling or the two cases
            # publish contradictory years for one document (AUTH/2424/8/11 and
            # AUTH/2425/8/11, adjudicated together for exactly that reason).
            case_code_year = apply_adjudication(
                num, "code_year", code_year, adjudications, used_adjudications, emit_shas)
            # R18. `appeal.by` likewise: the PMCPA's own slot is false on two
            # cases and the report's heading says so in terms.
            case_appeal = apply_appeal_adjudication(
                num, appeal, adjudications, used_adjudications, emit_shas)
            # The complainant is resolved PER PAGE (siblings share the slots and
            # the opening), so the adjudication is applied per case and a
            # co-reported pair must carry one row EACH or the two cases publish
            # contradictory categories for one document.
            case_complainant = apply_complainant_adjudication(
                num, complainant, adjudications, used_adjudications, emit_shas)
            case_companies = []
            for name in case_respondent_companies + complainant_companies:
                if name not in case_companies:
                    case_companies.append(name)
            # `inter_company` is derived from the category, so it has to be
            # derived AFTER the adjudication -- the page-level `procedure` above
            # was computed from the unadjudicated reading.
            case_procedure = dict(procedure,
                                  inter_company=case_complainant["category"] == "company")

            # A multi-case report is ONE document: the info-holder, the meta
            # CSVs, the outcome table and the report sections belong to the
            # page, not to a case within it, and nothing in the source splits
            # them per sibling. So the siblings share the rows and every row
            # says so, rather than L2 guessing which case a clause belongs to.
            verdicts = resolve_verdicts(d, case_appeal, case_code_year, clause_inventory,
                                        len(d["cases"]) > 1, warn, year_arbitration, num,
                                        adjudications, used_adjudications, emit_shas,
                                        used_prose_reviews)
            verdicts = [
                apply_verdict_adjudication(num, v, adjudications, used_adjudications, emit_shas)
                for v in verdicts
            ]
            # Q3/R6/R28-residue. Applied AFTER the polarity adjudication so the
            # two cannot fight over `basis`: they target different questions
            # (what the case's outcome was, versus which body ruled what) and
            # no row in the corpus carries both.
            verdicts = [
                apply_attribution_adjudication(num, v, adjudications, used_adjudications, emit_shas)
                for v in verdicts
            ]
            named = set(d["clause_names_in_text"])
            unwitnessed_rows += [
                (num, v["clause"], v["panel"], v["final"], d["clause_names_in_text"])
                for v in verdicts if v["clause"] not in named]
            sanctions = resolve_sanctions(d, verdicts)
            if d["multi_case_undeclared"]:
                warn("multi_case_undeclared", "banners: " + ", ".join(d["foreign_banner_cases"]))
            for seg_note in d["segment_notes"]:
                warn(seg_note, None)
            for seg in d["segments"]:
                if seg["kind"] in ("complaint", "response") and not seg["leakage_attest"]["clean"]:
                    warn("attest_failed_quotable_segment",
                         f"{seg['kind']} {seg['ref']['char_start']}-{seg['ref']['char_end']}: "
                         + ",".join(k for k, v in sorted(seg["leakage_attest"]["checks"].items()) if not v))
            cases.append({
                "schema_version": SCHEMA_VERSION,
                "case_number": case_number,
                # Never more than one: multi-case reports SHARE a file, they do
                # not have several.
                "source_files": [d["file"]],
                "sibling_cases": siblings,
                "title": title,
                "subject": subject,
                "parties": {"respondent": case_respondent, "complainant": case_complainant},
                "code_year": case_code_year,
                "procedure": case_procedure,
                # R14/round 4. `dates.received` is adjudicable: three cases carry
                # a date that no rule can settle because the PMCPA's own record
                # slots disagree with the report's statement of receipt, and
                # picking by rule is what produced two wrong values. The
                # adjudication is pinned to the source slots it was reviewed
                # against, so it goes stale loudly if the page changes.
                "dates": {
                    "received": apply_adjudication(
                        num, "dates.received", received,
                        adjudications, used_adjudications, emit_shas),
                    "completed": apply_adjudication(
                        num, "dates.completed", completed,
                        adjudications, used_adjudications, emit_shas),
                },
                "verdicts": verdicts,
                "appeal": case_appeal,
                "sanctions": sanctions,
                # Siblings share the report, so they share its segments and the
                # indices that point into them.
                "segments": d["segments"],
                "renditions": dict(d["rendition_index"]),
                "entities": {"companies": case_companies, "products": [], "people_roles": []},
                "quality": {
                    "source_integrity": d["source_integrity"]["status"],
                    "source_integrity_note": d["source_integrity"]["note"],
                    "pdf_substituted": pdf is not None,
                    # DEFECTS D4a. The page reports a case it does not declare
                    # as a sibling, so its segments mix parties and its
                    # page-level outcome lists cover several cases. Recorded,
                    # never repaired: separating the rulings per case inside a
                    # shared pane is out of scope, and bench excludes the items.
                    "multi_case_undeclared": d["multi_case_undeclared"],
                    "multi_case_banners": list(d["foreign_banner_cases"]),
                    "known_text_defects": [],
                    "era": era_from_case_number(num),
                    "report_chars": d["report_len"],
                },
            })

    cases.sort(key=lambda c: case_sort_key(c["case_number"]["value"]))
    check_clause_witness_coverage(unwitnessed_rows)
    dead_prose_reviews = sorted(set(PROSE_ONLY_VERDICT_READ) - used_prose_reviews)
    if dead_prose_reviews:
        raise SystemExit(
            "REFUSING: PROSE_ONLY_VERDICT_READ has dead entries whose case/row was not "
            f"encountered: {dead_prose_reviews}")
    return (cases, company_fold, respondent_keys, used_adjudications, warnings,
            year_arbitration, slot_corrections)


def write_audit(warnings, cases):
    """SPEC §5/§7: the cross-checks that are WARNINGS, not failures.

    A warning is a place where the corpus contradicts itself and L2 resolved it
    by rule rather than by reading. Written as data, not printed, so the classes
    can be diffed between builds and worked through by hand.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "cases": len(cases),
        "classes": {
            cls: {
                "description": WARNING_CLASSES[cls],
                "count": len(items),
                "cases": sorted({i["case"] for i in items}, key=case_sort_key),
                "items": sorted(
                    (i for i in items),
                    key=lambda i: (case_sort_key(i["case"]), str(i["detail"]))),
            }
            for cls, items in sorted(warnings.items())
        },
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    # Temp file + os.replace, the same discipline cases.jsonl has had since the
    # R24 wave and for the same reason: a reader that catches a truncated
    # artefact reports defects that are not in the data. Small files are not
    # exempt -- the window is shorter, not absent.
    tmp = AUDIT.with_name(AUDIT.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, AUDIT)
    return payload


def write_year_arbitration(rows):
    """R20's build artefact: every row whose year witnesses disagreed.

    Sorted by (case, clause) in the corpus' own orders, so two builds of the
    same corpus produce byte-identical files. A row that is never written is a
    row where the witnesses agreed and there was nothing to decide.
    """
    rows = sorted(rows, key=lambda r: (case_sort_key(r["case_number"]),
                                       clause_sort_key(r["clause"])))
    YEAR_ARBITRATION.parent.mkdir(parents=True, exist_ok=True)
    tmp = YEAR_ARBITRATION.with_name(YEAR_ARBITRATION.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, YEAR_ARBITRATION)
    return rows


def write_slot_corrections(rows):
    """N2's build artefact: every clause slot a reader corrected or deleted.

    Same discipline as `write_year_arbitration`: sorted by (case, from-clause)
    so two builds of the same corpus produce byte-identical files, and readable
    without re-running the build. bench/generate.py consumes it -- a DELETED
    row's item-candidates would otherwise disappear leaving neither an item nor
    a reasoned exclusion, and bench/id_migrations.py needs the old->new clause
    map to see a renamed item as renamed rather than as one item vanishing and
    an unrelated one appearing.
    """
    rows = sorted(rows, key=lambda r: (case_sort_key(r["case_number"]),
                                       clause_sort_key(r["from_clause"])))
    SLOT_CORRECTIONS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SLOT_CORRECTIONS.with_name(SLOT_CORRECTIONS.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, SLOT_CORRECTIONS)
    return rows


def check_appeal_heading_coverage(digests):
    """R18, and the R3/R11 standard applied to a new pattern.

    `APPEAL_HEADING_PARTY_RE` reads a party out of a heading l1/derive.py has
    already normalised to APPEAL_GROUNDS. That vocabulary is closed -- 352
    headings, 103 distinct spellings -- and every one of them currently begins
    'APPEAL BY ' or 'APPEAL FROM '. If the corpus grows a spelling the pattern
    cannot parse, the witness would go quiet on that case with no trace, which
    is exactly how `outwith_scope` lost a quarter of its class. So the build
    refuses and names the strings.

    Note what this does NOT claim: that every parsed party can be tied to a
    party of its case. It cannot, on 5 files, and that is an honest absence --
    see `heading_appellant`.
    """
    unparsed = sorted({h for d in digests for h in d["appeal_headings"]
                       if not APPEAL_HEADING_PARTY_RE.match(h)})
    if unparsed:
        raise SystemExit(
            "REFUSING: %d APPEAL_GROUNDS heading(s) do not begin 'APPEAL BY/FROM', so the "
            "appellant witness cannot read them and would go silent without saying so:\n  %s"
            % (len(unparsed), "\n  ".join(unparsed[:10])))


def check_sanction_needle_coverage(digests):
    """R31, and the R3/R11 decide-every-value standard applied to the chips.

    `Additional sanctions` is a hand-typed closed vocabulary: 9 distinct labels
    over the 264 pages that carry any, and `SANCTION_NEEDLE_USED` decides every
    one of them. A label the table does not name would take the default (keep
    refusing) and, if it were another generic word, would silently refuse a
    whole matter's segments the way 'Advertisement' did on AUTH/2008/6/07 --
    with no trace, since a dropped segment leaves no row in this layer.

    So the build refuses and names the label rather than defaulting quietly.
    The default itself is deliberate: an unknown label keeps refusing until it
    is measured, which errs towards a missing item rather than a leaked one.
    """
    undecided = sorted({n for d in digests for n in (needle(c) for c in d["sanction_chips"])
                        if n and n not in SANCTION_NEEDLE_USED})
    if undecided:
        raise SystemExit(
            "REFUSING: %d 'Additional sanctions' chip label(s) have no decision in "
            "SANCTION_NEEDLE_USED, so `no_sanctions_text` would refuse on an unmeasured "
            "needle:\n  %s\nMeasure each one's base rate over the pages that do NOT carry "
            "the chip and declare it against the %.0f%% floor."
            % (len(undecided), "\n  ".join(repr(u) for u in undecided[:10]),
               SANCTION_NEEDLE_BASE_RATE_FLOOR * 100))


def check_dual_read_coverage(digests, cases):
    """R28 stage 1: every dual-ruling candidate, both axes, is in DUAL_READ.

    The decide-every-value rule (R3/R11) applied to a rule that fires on a
    small closed set. A candidate is a (case, clause, axis) where EITHER the
    reader attributes both polarities to that body -- read from the row's own
    receipts, so this cannot disagree with what `resolve_verdicts` acted on --
    OR the loose screen sees both. Either way it is a dual ruling or a mis-read
    and only a reading tells which; 73 candidates today.

    A panel-axis row the RULE already calls dual is not a candidate: it is
    decided and there is nothing to read. A row an ADJUDICATION calls dual
    stays a candidate, because the registry row is where the reading that
    justified the adjudication lives, and losing it would lose the reading.

    Both directions refuse. An unread candidate stops the build. A registry row
    that no candidate matches is a dead fix -- the same dead-entry detection
    every adjudication gets, and the reason this table cannot quietly outlive
    the evidence it was written about.
    """
    screen = {}
    for d in digests:
        for num in d["cases"]:
            for clause in d["dual_screen_panel"]:
                screen[(num, clause, "panel")] = True
            for clause in d["dual_screen_appeal"]:
                screen[(num, clause, "appeal_board")] = True
    seen, unread = set(), []
    for c in cases:
        num = c["case_number"]["value"]
        for v in c["verdicts"]:
            s = v["sources"]
            reader = {
                "panel": s["prose_panel_breach"] and s["prose_panel_no_breach"],
                "appeal_board": (s["prose_appeal_board_breach"]
                                 and s["prose_appeal_board_no_breach"]),
            }
            for axis in ("panel", "appeal_board"):
                key = (num, v["clause"], axis)
                if axis == "panel" and v["basis"] in RULE_DECIDED_PANEL_DUAL:
                    continue          # the RULE decided it; nothing left to read

                if not (reader[axis] or screen.get(key)):
                    continue
                seen.add(key)
                if key not in DUAL_READ:
                    unread.append(key)
    if unread:
        raise SystemExit(
            "REFUSING: %d dual-ruling candidate row(s) are not in DUAL_READ, so the build cannot "
            "say whether the body ruled twice or the second polarity belongs to someone else. "
            "Read each against the report and register it:\n  %s"
            % (len(unread), "\n  ".join(f"{a} clause {b} ({c})" for a, b, c in sorted(unread))))
    dead = sorted(set(DUAL_READ) - seen)
    if dead:
        raise SystemExit(
            "REFUSING: %d DUAL_READ row(s) match no candidate any more -- the evidence moved "
            "under a reviewed decision:\n  %s"
            % (len(dead), "\n  ".join(f"{a} clause {b} ({c})" for a, b, c in dead)))


def check_date_coherence(cases):
    """A case cannot be received after it completed. Round 4 found this catches
    more than the tie-break heuristic that replaced it: AUTH/3293/1/20 published
    a receipt of 2020-12-30 against a completion of 2020-12-18 and no rule
    noticed, because the check was 'do the witnesses agree' rather than 'is the
    result possible'. A structural impossibility needs no third witness.
    """
    bad = []
    for c in cases:
        r = (c["dates"].get("received") or {}).get("value")
        d = (c["dates"].get("completed") or {}).get("value")
        if r and d and r > d:
            bad.append((c["case_number"]["value"], r, d,
                        (c["dates"]["received"] or {}).get("basis")))
    if bad:
        raise SystemExit(
            "REFUSING: received date AFTER completed date -- impossible, so one "
            "of them is wrong:\n  "
            + "\n  ".join(f"{n}: received {r} > completed {d} (basis {b})"
                          for n, r, d, b in bad)
            + "\nResolve with an adjudication in l2/adjudications.json; do not "
              "widen a rule to make an impossible value acceptable.")


def check_outwith_coverage(digests):
    """R3. Every status naming the Code's scope must be DECIDED by the rule.

    The status slot is hand-typed -- 193 distinct values over 1,902 files -- so
    a pattern that matches most of them looks correct while quietly missing a
    quarter of the class, which is what it did. This turns 'does the regex catch
    everything?' (unanswerable) into 'is every value that mentions the scope
    decided?' (checkable), and names the exact strings when the answer is no.
    """
    missed = sorted({d["status_line"] for d in digests
                     if d.get("status_mentions_scope") and not d["has_outwith_status"]})
    if missed:
        raise SystemExit(
            "REFUSING: status line(s) name the Code's scope but OUTWITH_STATUS_RE "
            "does not decide them, so `outwith_scope` would under-report:\n  "
            + "\n  ".join(repr(m) for m in missed)
            + "\nAdd the spelling to OUTWITH_STATUS_RE, or record why it is not an "
              "outwith disposal. Do not leave the class short.")


def check_complainant_title_vocabulary(digests):
    """Wave C. Every `director`/`consultant` complainant slot must be a value
    that was READ, not one the pattern happened to land on.

    Same shape as check_outwith_coverage, for the same reason: both tokens sit
    in a hand-typed slot, and both were wrong here in ways a
    matched-most-of-them rule cannot show ('Trust Clinical Director' published
    as a PMCPA-initiated case; 'Consultants in ...' unreadable in the plural).
    """
    seen = {collapse(d["meta_complainant"]) for d in digests}
    missed = sorted(v for v in seen
                    if v and COMPLAINANT_TITLE_TOKEN_RE.search(v)
                    and v not in COMPLAINANT_TITLE_DECIDED)
    if missed:
        raise SystemExit(
            "REFUSING: complainant slot(s) name a director or a consultant in a "
            "spelling COMPLAINANT_TITLE_DECIDED has not been read against:\n  "
            + "\n  ".join(repr(m) for m in missed)
            + "\nRead each one, decide whether the token is the PMCPA Director / a "
              "clinician or a job title, adjust CATEGORY_RULES if it is a new shape, "
              "and add it to the table. Do not let the pattern decide unread values.")


def check_precedent_guard_coverage(digests):
    """R28. Every foreign-case ruling sentence must be DECIDED by the guard.

    Two ways it can fail to be, and both stop the build:

      MIXED -- some of the sentence's foreign case numbers are the subject of
        an undertaking and some are not, so the rule's two signals conflict.
        There are two such sentences in the corpus; both were read and both are
        declared in `PRECEDENT_MIXED_DECIDED`.

      UNREGISTERED -- an undertaking-anchored ruling sentence that nobody has
        read. AUTH/2833/4/16 is why this is not optional: a recital of another
        case's ruling can carry the anchor word for word, so the pattern alone
        cannot tell a ruling from a report of one, and the registry is the
        reading. A new member means new text, and new text needs a reader.
    """
    stray_mismatch = []
    for key, expected in sorted(STRAY_PERIOD_RULING_READ.items()):
        actual = STRAY_PERIOD_RULING_FIRED[key]
        if actual != expected:
            stray_mismatch.append((key, expected, actual))
    unknown_stray = sorted(set(STRAY_PERIOD_RULING_FIRED) - set(STRAY_PERIOD_RULING_READ))
    if stray_mismatch or unknown_stray:
        rows = [f"{file} {key}: expected {want}, saw {got}"
                for (file, key), want, got in stray_mismatch]
        rows += [f"{file} {key}: unreviewed" for file, key in unknown_stray]
        raise SystemExit(
            "REFUSING: reviewed stray-period ruling-list population changed:\n  "
            + "\n  ".join(rows)
            + "\nRe-read the complete sentence before changing the registry.")

    dead_context = sorted(set(RULING_CONTEXT_REFUSALS) - RULING_CONTEXT_REFUSALS_FIRED)
    if dead_context:
        raise SystemExit(
            "REFUSING: RULING_CONTEXT_REFUSALS contains sentence hashes that no longer fire:\n  "
            + "\n  ".join(f"{case} {key}" for case, key in dead_context)
            + "\nRe-read the recap/submission before trusting the refusal.")
    buckets = {"mixed_unregistered": [], "unregistered": [], "citation_unregistered": []}
    for d in digests:
        for why, key, sentence in d.get("precedent_mixed") or []:
            buckets[why].append((d["cases"], key, sentence))
    def refuse(rows, headline, tail):
        if not rows:
            return
        raise SystemExit(
            f"REFUSING: {headline}:\n  "
            + "\n  ".join(f'("{cases[0]}", "{key}")\n    {sentence[:300]}'
                          for cases, key, sentence in rows)
            + "\n" + tail)
    refuse(buckets["mixed_unregistered"],
           "ruling sentence(s) whose foreign case numbers are PART "
           "undertaking-anchored, which `precedent_disposal` cannot decide, and which "
           "are not in PRECEDENT_MIXED_DECIDED",
           "Read each one and add the key with None (read) or a reason string (refused), "
           "or narrow UNDERTAKING_CASE_RE so the sentence falls cleanly on one side.")
    refuse(buckets["unregistered"],
           "undertaking-anchored ruling sentence(s) not in UNDERTAKING_SUBJECT_READ, "
           "so nobody has read whether they are this case's ruling or a recital of the "
           "case the undertaking came from",
           "Read each one in its segment context and add the key with None (read) or "
           "a reason string (refused). AUTH/2833/4/16 is the worked example of why the "
           "sentence alone does not decide it.")
    refuse(buckets["citation_unregistered"],
           "ruling sentence(s) carrying a foreign case number that a body would have "
           "owned, skipped as precedent citations, and not in PRECEDENT_CITATION_READ -- "
           "so nobody has read whether the foreign number is the case being CITED or the "
           "case being ruled ABOUT",
           "Read each one in its segment context and add the key with None (read) or a "
           "reason string (refused). AUTH/3123/11/18 ('failed to supply all the relevant "
           "information in its response to Case AUTH/3041/6/18 and a breach of Clause 9.1 "
           "was ruled') and AUTH/3641/4/22 ('ruled a breach of Clause 4.6 ... in that "
           "case (Case AUTH/3446/12/20)') are the two worked examples.")


def report_phase2(cases, warnings):
    """Segment, attest and verdict measurements. The complaint/response attest
    pass rate is the number bench T1/T2 lives on: a segment that fails is an
    item that is never generated."""
    kinds = Counter()
    clean = Counter()
    per_check = {}
    for c in cases:
        for s in c["segments"]:
            kinds[s["kind"]] += 1
            clean[s["kind"]] += bool(s["leakage_attest"]["clean"])
            for k, v in s["leakage_attest"]["checks"].items():
                per_check.setdefault(s["kind"], Counter())[k] += bool(v)
    print(f"segments                : {sum(kinds.values())} over {len(cases)} cases")
    for kind in sorted(kinds):
        n = kinds[kind]
        pct = 100.0 * clean[kind] / n
        detail = " ".join(f"{k.replace('no_', '').replace('outside_', 'outside-')}"
                          f" {100.0 * per_check[kind][k] / n:.0f}%"
                          for k in sorted(per_check[kind]))
        print(f"  {kind:<19}: {n:5d}  clean {clean[kind]:5d} ({pct:5.1f}%)   {detail}")
    src = Counter(s["source"] for c in cases for s in c["segments"])
    print(f"  by source          : {dict(sorted(src.items()))}")
    for slot in ("summary", "report_abstract", "pdf_flow"):
        n = sum(1 for c in cases if c["renditions"][slot] is not None)
        print(f"  rendition {slot:<15}: {n:5d} / {len(cases)} cases")

    rows = [v for c in cases for v in c["verdicts"]]
    print(f"verdict rows            : {len(rows)}  over "
          f"{sum(1 for c in cases if c['verdicts'])} cases with an outcome")
    for basis, n in sorted(Counter(v["basis"] for v in rows).items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {basis:<38}: {n}")
    print(f"  final       : {dict(sorted(Counter(v['final'] for v in rows).items()))}")
    print(f"  panel       : {dict(sorted(Counter(str(v['panel']) for v in rows).items()))}")
    print(f"  appeal_board: {dict(sorted(Counter(str(v['appeal_board']) for v in rows).items()))}")
    print(f"  dual_ruling rows: {sum(1 for v in rows if v['dual_ruling'])}"
          f" | flipped_on_appeal rows: {sum(1 for v in rows if v['flipped_on_appeal'])}"
          f" | clause_2_censure cases: {sum(1 for c in cases if c['sanctions']['clause_2_censure'])}")
    appealed_rows = [v for c in cases if c["appeal"]["appealed"] for v in c["verdicts"]]
    t3_ready = [v for v in appealed_rows if v["panel"] and v["appeal_board"] and not v["dual_ruling"]]
    print(f"  appealed-case rows: {len(appealed_rows)}  panel attributed "
          f"{sum(1 for v in appealed_rows if v['panel'])}  board attributed "
          f"{sum(1 for v in appealed_rows if v['appeal_board'])}  BOTH (T3-eligible) {len(t3_ready)}"
          f"  of which upheld {sum(1 for v in t3_ready if not v['flipped_on_appeal'])}"
          f" / overturned {sum(1 for v in t3_ready if v['flipped_on_appeal'])}")
    print(f"  board attribution resting on the appellant's grounds: "
          f"{sum(1 for v in rows if v['appeal_board'] and v['sources']['prose_appeal_from_grounds'])}")
    print(f"  outwith_scope: status line {sum(1 for c in cases if c['procedure']['outwith_scope'])} cases"
          f" | multi_case_undeclared {sum(1 for c in cases if c['quality']['multi_case_undeclared'])} cases")
    print("audit warnings          :")
    for cls, items in sorted(warnings.items()):
        print(f"  {cls:<38}: {len(items):5d} in {len({i['case'] for i in items})} cases")


def report(cases, company_fold, respondent_keys, digests, adjudications, used):
    """What the build measured. Printed, not asserted -- the validator asserts."""
    multi = sum(1 for d in digests if len(d["cases"]) > 1)
    from_multi = sum(len(d["cases"]) for d in digests if len(d["cases"]) > 1)
    print(f"case objects            : {len(cases)}  (from {len(digests)} L1 files)")
    print(f"multi-case files        : {multi}  contributing {from_multi} cases")
    print(f"company fold groups     : {len(company_fold)} canonical companies "
          f"({len(respondent_keys)} of them appear as a respondent)")
    for field, get in (
        ("case_number", lambda c: c["case_number"]["basis"]),
        ("title", lambda c: c["title"]["basis"]),
        ("subject", lambda c: c["subject"]["basis"]),
        ("respondent", lambda c: c["parties"]["respondent"]["basis"]),
        ("code_year", lambda c: c["code_year"]["basis"]),
        ("received", lambda c: c["dates"]["received"]["basis"]),
        ("completed", lambda c: c["dates"]["completed"]["basis"]),
        ("appeal", lambda c: c["appeal"]["basis"]),
        ("sanctions", lambda c: c["sanctions"]["basis"]),
    ):
        counts = Counter(get(c) for c in cases)
        parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        print(f"  {field:<12}: {parts}")
    cat = Counter(c["parties"]["complainant"]["category"] for c in cases)
    print("  complainant : " + ", ".join(f"{k} {v}" for k, v in sorted(cat.items(), key=lambda kv: (-kv[1], kv[0]))))
    ap = Counter(str(c["appeal"]["by"]) for c in cases)
    print("  appeal.by   : " + ", ".join(f"{k} {v}" for k, v in sorted(ap.items(), key=lambda kv: (-kv[1], kv[0]))))
    proc = {k: sum(1 for c in cases if c["procedure"][k]) for k in cases[0]["procedure"]}
    print("  procedure   : " + ", ".join(f"{k} {v}" for k, v in sorted(proc.items())))
    # DEFECTS D1, old rule vs new, in files and in cases.
    kw_files = sum(1 for d in digests if d["has_outwith_keyword"])
    st_files = sum(1 for d in digests if d["has_outwith_status"])
    kw_cases = sum(len(d["cases"]) for d in digests if d["has_outwith_keyword"])
    print(f"  outwith_scope: keyword rule {kw_files} files / {kw_cases} cases (l2.1, false) "
          f"-> status rule {st_files} files / {proc['outwith_scope']} cases (l2.2)")
    print(f"  pdf_substituted {sum(1 for c in cases if c['quality']['pdf_substituted'])}")
    dead = sorted(set(a["id"] for a in adjudications.values()) - used)
    print(f"adjudications           : {len(adjudications)} defined, {len(used)} used"
          + (f", DEAD: {dead}" if dead else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-adjudication-shas", action="store_true",
                    help="print the source-slot sha for every adjudication instead of checking it")
    args = ap.parse_args()

    adjudications = load_adjudications()
    emit_shas = {} if args.emit_adjudication_shas else None

    pdf_by_html = read_pdf_records()
    digests = read_corpus(pdf_by_html)
    (cases, company_fold, respondent_keys, used, warnings, year_arbitration,
     slot_corrections) = build_cases(digests, pdf_by_html, adjudications, emit_shas)

    if emit_shas is not None:
        for adj_id in sorted(emit_shas):
            print(f"{adj_id}  {emit_shas[adj_id]}")
        return 0

    # Before anything is written: a status naming the Code's scope that the
    # outwith rule does not decide would silently shrink the class, which is
    # exactly how it came to under-report by 25% (DEFECTS R3). Refusing after
    # the write would leave a bad artefact on disk.
    check_outwith_coverage(digests)
    check_supplemental_boundary_coverage()
    check_ruling_false_match_registry()
    check_response_attest_false_positive_registry()
    check_appeal_heading_coverage(digests)
    check_sanction_needle_coverage(digests)
    check_precedent_guard_coverage(digests)
    check_complainant_title_vocabulary(digests)
    check_dual_read_coverage(digests, cases)
    check_date_coherence(cases)

    # Written to a sibling temp file and renamed into place. cases.jsonl is
    # 23 MB and takes seconds; a truncate-and-rewrite leaves a window in which
    # every reader -- both validators, bench/generate.py, the site exporter, an
    # audit reading the layer while a build runs -- sees a half-file and
    # reports defects that are not there. os.replace is atomic on one
    # filesystem: a reader gets the old build or the new one, never a prefix.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, OUT)
    write_audit(warnings, cases)
    write_year_arbitration(year_arbitration)
    write_slot_corrections(slot_corrections)
    report(cases, company_fold, respondent_keys, digests, adjudications, used)
    report_phase2(cases, warnings)
    print(f"wrote {len(cases)} cases -> {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
    print(f"wrote audit report      -> {AUDIT}  ({AUDIT.stat().st_size / 1e3:.0f} KB)")
    print(f"wrote year arbitration  -> {YEAR_ARBITRATION}  ({len(year_arbitration)} row(s))")
    print(f"wrote slot corrections  -> {SLOT_CORRECTIONS}  ({len(slot_corrections)} row(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
