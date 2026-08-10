"""Build the bench fixture: four invented L2 case objects plus the L1 pane text
their segment offsets point into.

The fixture exists so `bench/` can be developed and tested before
`data/l2/cases.jsonl` is built. Every case conforms to `l2/SPEC.md` §2 exactly
-- no extra keys -- which means the segments carry offsets, not text. So this
script also emits `l1_panes.fixture.json`, a stand-in for the L1 pane text that
`segments[].ref` slices. The generator resolves text through the same code path
in both worlds; only the resolver differs.

Offsets are computed here rather than typed by hand, so they slice exactly.
Deterministic: same source, same bytes.

    python3 bench/fixtures/build_fixture.py

CONTENT IS INVENTED. Case numbers are TEST/xxxx/x/26, companies and products do
not exist, and no sentence is taken from a real PMCPA report.

TEST/0002/1/26's complaint carries one sentence that is here to be TRIPPED
OVER: "in Case TEST/0009/9/25 a breach of Clause 7.10 was ruled". It is a
decimal clause number in a ruling frame -- the exact shape DEFECTS R24 proved
three layers of leakage checking were blind to -- inside a citation of another
case, which is the one class that must stay quotable. So the fixture asserts
both halves at once: if the decimal fix regresses the sentence stops being
seen at all, and if the precedent exemption regresses the TEST/0002
item vanishes from a fixture build with a `tripwire` exclusion row. verify/ruling_battery.py is the same
argument stated as a table.
"""

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
CASES_OUT = HERE / "cases.fixture.jsonl"
PANES_OUT = HERE / "l1_panes.fixture.json"

SHARED = "TEST-0001-1-26__TEST-0002-1-26.html"
APPEAL = "TEST-0003-2-26.html"
APPEAL_PDF = "TEST-0003-2-26.pdf"
SCOPE = "TEST-0004-3-26.html"


# --- pane text -------------------------------------------------------------
# Each pane is a list of (block-name, text). Blocks are joined by a blank line;
# a segment ref names one block and gets that block's exact char span.

PANE_BLOCKS = {
    SHARED: {
        "summary": [
            ("summary", """Case Summary

TEST/0001/1/26 and TEST/0002/1/26
Health professional and anonymous complainant v Brackenmoor Pharma Ltd
Zentaril leavepiece and webinar slides

The Panel ruled a breach of the Code in Case TEST/0001/1/26 because the leavepiece made a comparative claim which the company could not substantiate, and because the presentation of the accompanying graph did not maintain a high standard. No breach of the Code was ruled in Case TEST/0002/1/26 because the complainant, who was anonymous and non-contactable, did not establish the allegation on the balance of probabilities.

Completed 12 March 2026"""),
        ],
        "report": [
            ("heading", """CASE TEST/0001/1/26 AND CASE TEST/0002/1/26

HEALTH PROFESSIONAL AND ANONYMOUS COMPLAINANT v BRACKENMOOR PHARMA LTD

Zentaril leavepiece and webinar slides"""),
            ("abstract", """A consultant cardiologist complained about a printed leavepiece for Zentaril (rilvacaftor), and a separate anonymous complainant raised the content of a webinar slide about the same medicine. Both matters were taken up with Brackenmoor Pharma Ltd. The Panel ruled a breach of the Code in relation to the leavepiece and ruled no breach in relation to the webinar."""),
            ("h_complaint_1", "1 COMPLAINT (Case TEST/0001/1/26)"),
            ("complaint_1", """I am a consultant cardiologist working in an NHS trust in the north of England. At a meeting last month a representative from Brackenmoor Pharma left me a printed leavepiece for Zentaril (rilvacaftor) which I believe misrepresents the evidence.

The leavepiece is headed 'The only once-daily therapy proven to cut hospitalisation' and repeats that wording twice more inside. No comparator is named anywhere on the piece. The only reference given is to a single open-label study in 84 patients which, so far as I can establish, did not report hospitalisation as a pre-specified endpoint. The figure '42% fewer admissions' appears beside a bar chart whose vertical axis begins at 30%, which greatly exaggerates the visual difference between the two bars.

I raised this with the representative at the time and was told that the claim had been 'signed off centrally'. I do not consider that the piece can be substantiated on the evidence cited, and the presentation of the chart is in my view designed to leave a stronger impression than the data support. I have not been able to find the study referenced in the piece on any trials register."""),
            ("h_response_1", "RESPONSE (Case TEST/0001/1/26)"),
            ("response_1", """Brackenmoor Pharma Ltd submitted that the leavepiece had been certified in accordance with its standard operating procedures and that the claim was supported by the study cited, a copy of which was provided.

The company submitted that the phrase 'the only once-daily therapy' was a statement of fact about the licensed dosing schedules available in the therapy area at the date of certification, and that no comparison with any named product was intended or made. The company accepted that the study was open-label and that hospitalisation was a secondary endpoint, but submitted that the endpoint had been pre-specified in the protocol and that the difference reported was statistically significant.

As to the bar chart, the company submitted that the axis had been truncated for legibility, that both bars carried their absolute values as data labels, and that the underlying figures were stated in the reference. The company did not accept that the piece was misleading. It confirmed that the leavepiece had been withdrawn from use and that it would not be recertified in its current form."""),
            ("h_ruling_1", "PANEL RULING (Case TEST/0001/1/26)"),
            ("ruling_1", """The Panel noted that a claim to be 'the only' therapy proven to achieve an outcome was a comparative claim, whether or not a competitor was named, and that such a claim had to be capable of substantiation by direct comparative evidence. The single open-label study cited did not provide that evidence. The Panel therefore ruled a breach of Clause 6.1 of the 2021 Code.

The Panel further considered that truncating the axis of the bar chart at 30% exaggerated the difference between the two columns and that the piece as a whole did not maintain a high standard. The Panel ruled a breach of Clause 9.1 of the 2021 Code."""),
            ("h_complaint_2", "2 COMPLAINT (Case TEST/0002/1/26)"),
            ("complaint_2", """I wish to remain anonymous and I do not wish to be contacted about this complaint.

Brackenmoor Pharma ran a webinar in January for prescribers in respiratory medicine. During the session one of the slides about Zentaril showed a patient case in which the dose was doubled after four weeks. That is not what the summary of product characteristics says, and I believe the audience were left with the impression that dose escalation of this kind is routine when it is not.

I did not keep a copy of the slides and I am not able to say which speaker presented them or how many people attended. I am raising it because I think prescribers were misled.

I would add that in Case TEST/0009/9/25 a breach of Clause 7.10 was ruled on a slide deck which described dose escalation in almost identical terms, so this is not a novel point."""),
            ("h_response_2", "RESPONSE (Case TEST/0002/1/26)"),
            ("response_2", """Brackenmoor Pharma Ltd submitted that it had reviewed the recording of the webinar and the certified slide deck and that no slide showed a doubling of the dose of Zentaril at four weeks. The certified deck contained one patient case in which the dose was adjusted within the licensed range at week eight, in line with the summary of product characteristics.

The company noted that in Case TEST/0001/1/26 the Panel ruled a breach of Clause 6.1 in relation to a separate item, and submitted that the two matters were entirely unconnected. The company submitted that in the absence of a copy of the slide complained of, or any detail identifying the session, it was not in a position to investigate further, and that the material actually used complied with the Code."""),
            ("h_ruling_2", "PANEL RULING (Case TEST/0002/1/26)"),
            ("ruling_2", """The Panel noted that the complainant bore the burden of proving the matter complained of on the balance of probabilities. The complainant was anonymous and had stated that they did not wish to be contacted, and no copy of the slide was provided. The company had produced the certified deck, which did not contain the material described.

The Panel considered that the complainant had not established that the audience had been misled and ruled no breach of Clause 7.2 of the 2021 Code."""),
        ],
    },
    APPEAL: {
        "summary": [
            ("summary", """Case Summary

TEST/0003/2/26
Nordwyck Laboratories Ltd v Halveston Biosciences Ltd
Olvexa journal advertisement

An inter-company complaint about a journal advertisement. The Panel ruled breaches of the Code in relation to both the superiority claim and the presentation of the advertisement. Halveston Biosciences Ltd appealed both rulings. The Appeal Board overturned the ruling on the claim and upheld the ruling on presentation. A public reprimand was imposed.

Completed 28 April 2026"""),
        ],
        "report": [
            ("heading", """CASE TEST/0003/2/26

NORDWYCK LABORATORIES LTD v HALVESTON BIOSCIENCES LTD

Olvexa journal advertisement"""),
            ("abstract", """Nordwyck Laboratories Ltd complained about a journal advertisement for Olvexa (bemtizumab) placed by Halveston Biosciences Ltd. The Panel ruled breaches of Clause 6.1 and Clause 9.1 of the 2016 Code. The respondent appealed. The Appeal Board ruled no breach of Clause 6.1 and upheld the Panel's ruling of a breach of Clause 9.1."""),
            ("h_complaint", "COMPLAINT"),
            ("complaint", """Nordwyck Laboratories Limited complained about a journal advertisement for Olvexa (bemtizumab) placed by Halveston Biosciences Limited in the February issue of a UK respiratory journal.

The advertisement carried the claim 'Olvexa: superior symptom control from week 2' in display type across the top of the page. The only reference cited was a pooled analysis of two trials, neither of which was a head-to-head comparison against our product Prantiva or against any other named therapy. A claim of superiority must rest on direct comparative evidence, and a pooled analysis of separate placebo-controlled studies cannot support it.

We further complain that the prescribing information was set in a type size which was not clearly legible, and that the obligatory adverse event reporting statement was printed in pale grey on a white background at the foot of the page, where it was very difficult to read. We consider that the overall impression created by the advertisement was one which failed to maintain a high standard."""),
            ("h_response", "RESPONSE"),
            ("response", """Halveston Biosciences Limited submitted that the claim 'superior symptom control from week 2' was not a comparison with any other product but a description of the change from baseline observed in the pooled analysis, and that the words 'vs baseline' appeared immediately beneath the claim in the same colour, albeit in smaller type.

The company submitted that the pooled analysis had been pre-specified, was published in a peer-reviewed journal, and was cited in full in the reference list. It did not accept that a reader would take the claim to be a comparison with a named competitor product, and noted that no competitor product was mentioned anywhere in the advertisement.

As to presentation, the company submitted that the prescribing information was set at the type size specified in its own artwork standards and that the adverse event statement was set in the same size as the surrounding text. The company accepted that the contrast of the adverse event statement was lower than it should have been and undertook to correct it in future artwork."""),
            ("h_panel_ruling", "PANEL RULING"),
            ("panel_ruling", """The Panel considered that the word 'superior' at the head of an advertisement would be read as a comparison with other therapies, and that the qualification 'vs baseline' was neither sufficiently prominent nor sufficiently proximate to correct that impression. The Panel ruled a breach of Clause 6.1 of the 2016 Code.

The Panel further considered that an adverse event reporting statement printed in pale grey on white was not clearly legible, and ruled a breach of Clause 9.1 of the 2016 Code."""),
            ("h_appeal_comments", "APPEAL BY HALVESTON BIOSCIENCES LTD"),
            ("appeal_comments", """The company appealed both rulings. In relation to Clause 6.1 it submitted that the Panel had considered the claim in isolation and that the qualification appeared within the same visual unit, in the same typeface and colour, directly beneath the claim. It provided a reader survey of 40 respiratory prescribers in which the majority reported understanding the claim as a within-arm change.

In relation to Clause 9.1 the company submitted that it had already accepted the contrast point and had changed the artwork, and that a ruling of a breach was disproportionate to a single presentational defect."""),
            ("h_appeal_ruling", "APPEAL BOARD RULING"),
            ("appeal_ruling", """The Appeal Board considered that the qualification 'vs baseline' was legible, immediately adjacent to the claim and in the same colour, and that on the balance of probabilities a health professional reading the advertisement would understand the claim as a within-arm comparison. The Appeal Board ruled no breach of Clause 6.1 of the 2016 Code. The appeal on this point was successful.

The Appeal Board considered that the legibility of an adverse event reporting statement was not a minor matter and that the standard had not been maintained. The Appeal Board upheld the Panel's ruling of a breach of Clause 9.1 of the 2016 Code. The appeal on this point was unsuccessful. The Appeal Board decided that a public reprimand should be issued."""),
        ],
    },
    APPEAL_PDF: {
        "flow": [
            ("heading", """Case TEST/0003/2/26 - Nordwyck Laboratories Ltd v Halveston Biosciences Ltd - Olvexa journal advertisement"""),
            ("complaint", """The complainant company objected to a journal advertisement for Olvexa (bemtizumab) which appeared in the February issue of a UK respiratory journal.

At the head of the page the advertisement claimed 'Olvexa: superior symptom control from week 2'. The single reference given was a pooled analysis of two placebo-controlled trials; neither trial compared Olvexa directly with Prantiva or with any other named therapy. In the complainant's submission a superiority claim required direct comparative evidence, which the cited analysis did not supply.

The complainant also objected to the presentation of the page: the prescribing information was, in its view, too small to read clearly, and the adverse event reporting statement was printed in pale grey type on white at the foot of the advertisement. Taken as a whole, the complainant submitted, the advertisement fell below the standard required."""),
            ("response", """The respondent company replied that 'superior symptom control from week 2' described the change from baseline recorded in the pooled analysis and was not a comparison with another product. Immediately below the claim, in the same colour and typeface but at a smaller size, the advertisement carried the words 'vs baseline'.

The respondent stated that the pooled analysis had been pre-specified, had been published after peer review, and was referenced in full. It observed that no competitor medicine was named anywhere on the page.

On presentation, the respondent said the prescribing information followed its internal artwork standard and that the adverse event statement matched the surrounding text in size. It acknowledged that the contrast of that statement was too low and undertook to correct the artwork."""),
            ("ruling", """The Panel ruled a breach of Clause 6.1 and a breach of Clause 9.1 of the 2016 Code. On appeal by the respondent the Appeal Board ruled no breach of Clause 6.1 and upheld the ruling of a breach of Clause 9.1."""),
        ],
    },
    SCOPE: {
        "summary": [
            ("summary", """Case Summary

TEST/0004/3/26
Member of the public v Nordwyck Laboratories Ltd
Corporate social media post

A member of the public complained about a corporate social media post concerning sponsorship of a community sporting event. The Panel ruled that the matter fell outwith the scope of the Code and made no ruling on the substance of the complaint.

Completed 5 June 2026"""),
        ],
        "report": [
            ("heading", """CASE TEST/0004/3/26

MEMBER OF THE PUBLIC v NORDWYCK LABORATORIES LTD

Corporate social media post"""),
            ("h_complaint", "COMPLAINT"),
            ("complaint", """I saw a post on the Nordwyck Laboratories corporate LinkedIn account about the company's sponsorship of a regional half marathon. The post included a photograph of a staff team in branded running shirts and the sentence 'we are proud to support people living with respiratory disease across the region'.

No medicine is named in the post and it does not describe any product. Even so I do not think a company which sells prescription medicines should be posting material of this kind where members of the public can see it, and I would like the Authority to look at whether it is appropriate."""),
            ("h_response", "RESPONSE"),
            ("response", """Nordwyck Laboratories Limited submitted that the post appeared on its corporate LinkedIn page, that it related solely to sponsorship of a community sporting event, and that it named no medicine, described no product and made no reference to any therapy area beyond the general description quoted by the complainant.

The company submitted that the post was corporate communication and not promotion of a medicine, and that nothing in it encouraged a member of the public to ask a health professional to prescribe a specific prescription only medicine. The company added that the material had been reviewed by its corporate communications function and had not been certified as promotional material because it was not promotional."""),
            ("h_ruling", "PANEL RULING"),
            ("ruling", """The Panel noted that the post named no medicine, made no product claim and did not encourage a member of the public to seek a prescription for a specific medicine. The Panel considered that the material was corporate communication which did not relate to the promotion of medicines to health professionals or to the provision of information to the public about prescription only medicines.

The Panel therefore ruled that the complaint fell outwith the scope of the Code and made no ruling on the substance of the allegation."""),
        ],
    },
}


def assemble(blocks):
    """Join blocks with a blank line; return (text, {name: (start, end)})."""
    parts = []
    spans = {}
    pos = 0
    for i, (name, body) in enumerate(blocks):
        if i:
            parts.append("\n\n")
            pos += 2
        spans[name] = (pos, pos + len(body))
        parts.append(body)
        pos += len(body)
    return "".join(parts), spans


PANES = {}
SPANS = {}
for _file, _panes in PANE_BLOCKS.items():
    PANES[_file] = {}
    SPANS[_file] = {}
    for _pane, _blocks in _panes.items():
        _text, _spans = assemble(_blocks)
        PANES[_file][_pane] = _text
        SPANS[_file][_pane] = _spans


def ref(file, pane, block):
    start, end = SPANS[file][pane][block]
    return {"file": file, "pane": pane, "char_start": start, "char_end": end}


def pane_ref(file, pane):
    """Whole-pane ref -- what a rendition pointer looks like."""
    return {"file": file, "pane": pane, "char_start": 0, "char_end": len(PANES[file][pane])}


def attest(clean, **failed):
    """leakage_attest per l2/SPEC.md §6. `failed` names checks that did not pass."""
    checks = {
        "no_ruling_language": True,
        "no_outcome_banner": True,
        "no_outcome_table": True,
        "outside_abstract": True,
        "no_sanctions_text": True,
        # SPEC §6's sixth check (DEFECTS R26). None of the invented reports
        # carries an outcome-stating headline, so it passes everywhere here --
        # but it has to be PRESENT, or bench/generate.py's attest_ok refuses
        # the whole fixture as malformed, which is what it should do to a case
        # object built before the check existed.
        "no_outcome_heading": True,
    }
    checks.update({k: False for k in failed})
    assert clean == all(checks.values()), "clean must equal all(checks)"
    return {"clean": clean, "checks": checks, "checked_at_build": True}


def receipt(value, basis, sources=None):
    r = {"value": value, "basis": basis}
    if sources is not None:
        r["sources"] = sources
    return r


def summary_rendition(file):
    """The whole summary pane, as l2/build.py emits it when nothing cuts it
    short. Every invented summary states the outcome, so every one is dirty."""
    return {"kind": "summary_rendition", "ref": pane_ref(file, "summary"), "source": "html",
            "leakage_attest": attest(False, no_ruling_language=False)}


def abstract_rendition(file):
    """The report abstract block. Dirty for the same reason."""
    return {"kind": "abstract_rendition", "ref": ref(file, "report", "abstract"), "source": "html",
            "leakage_attest": attest(False, no_ruling_language=False)}


def segment(kind, file, pane, block, source, clean, **failed):
    return {
        "kind": kind,
        "ref": ref(file, pane, block),
        "source": source,
        "leakage_attest": attest(clean, **failed),
    }


# --- the four cases --------------------------------------------------------

CASES = [
    # (a) clean breach case; co-reported with TEST/0002 (sibling rule exercise)
    {
        "schema_version": "l2.1",
        "case_number": receipt("TEST/0001/1/26", "unanimous", {"meta": "TEST/0001/1/26", "h1": "TEST/0001/1/26"}),
        "source_files": [SHARED],
        "sibling_cases": ["TEST/0002/1/26"],
        "title": receipt("Health professional v Brackenmoor Pharma Ltd", "h1_wins",
                         {"h1": "Health professional v Brackenmoor Pharma Ltd",
                          "title_tag": "Health professional v Brackenmoor Pharma Ltd - PMCPA"}),
        "subject": receipt("Zentaril leavepiece", "h2_wins",
                           {"hero_h2": "Zentaril leavepiece", "cludo_description": "Breach of the Code - Zentaril leavepiece"}),
        "parties": {
            "respondent": receipt("Brackenmoor Pharma Ltd", "canonical_entity"),
            "complainant": {
                "verbatim": "Health professional",
                "category": "health_professional",
                "anonymous": False,
                "contactable": True,
            },
        },
        "code_year": receipt(2021, "unanimous"),
        "procedure": {
            "voluntary_admission": False,
            "abridged": False,
            "paragraph_17": False,
            "outwith_scope": False,
            "inter_company": False,
            "no_report": False,
        },
        "dates": {
            "received": receipt("2026-01-19", "meta_date"),
            "completed": receipt("2026-03-12", "meta_date"),
        },
        "verdicts": [
            {
                "clause": "6.1", "code_year": 2021,
                "clause_slug": "clause-6-substantiation",
                "panel": "breach", "appeal_board": None, "final": "breach",
                "flipped_on_appeal": False,
                "basis": "info_holder_and_prose_agree",
                "sources": {"info_holder": "breach", "meta_csv": "breach", "prose": "breach"},
            },
            {
                "clause": "9.1", "code_year": 2021,
                "clause_slug": "clause-9-high-standards",
                "panel": "breach", "appeal_board": None, "final": "breach",
                "flipped_on_appeal": False,
                "basis": "info_holder_and_prose_agree",
                "sources": {"info_holder": "breach", "meta_csv": "breach", "prose": "breach"},
            },
        ],
        "appeal": {"appealed": False, "by": "none", "basis": "no_appeal_section"},
        "sanctions": {"undertaking": True, "additional": [], "clause_2_censure": False, "basis": "standard_sanctions"},
        "segments": [
            segment("abstract", SHARED, "report", "abstract", "html", False, outside_abstract=False),
            segment("complaint", SHARED, "report", "complaint_1", "html", True),
            segment("response", SHARED, "report", "response_1", "html", True),
            segment("panel_ruling", SHARED, "report", "ruling_1", "html", False, no_ruling_language=False),
            # l2.1: `renditions` are INDICES into this list, not bare refs. The
            # fixture carried refs until 2026-08-10, which made
            # bench/generate.py --use-fixture crash in rendition_variants
            # (`0 <= idx < len(segs)` against a dict). Both invented renditions
            # state the outcome in full, so both are dirty and no item takes
            # one -- the same shape as the 3 real report_abstract renditions
            # R26 refused.
            summary_rendition(SHARED),
            abstract_rendition(SHARED),
        ],
        "renditions": {"summary": 4, "report_abstract": 5, "pdf_flow": None},
        "entities": {
            "companies": ["Brackenmoor Pharma Ltd"],
            "products": ["Zentaril", "rilvacaftor"],
            "people_roles": ["consultant cardiologist", "representative"],
        },
        "quality": {
            "source_integrity": "ok",
            "pdf_substituted": False,
            "known_text_defects": [],
            "era": 2021,
            "report_chars": len(PANES[SHARED]["report"]),
        },
    },
    # (b) no-breach on burden of proof; anonymous, non-contactable complainant.
    #     Its response segment fails the attest (it quotes the sibling ruling),
    #     so no T1 item can be built -- only T1-triage.
    {
        "schema_version": "l2.1",
        "case_number": receipt("TEST/0002/1/26", "unanimous", {"meta": "TEST/0002/1/26", "h1": "TEST/0002/1/26"}),
        "source_files": [SHARED],
        "sibling_cases": ["TEST/0001/1/26"],
        "title": receipt("Anonymous v Brackenmoor Pharma Ltd", "h1_wins",
                         {"h1": "Anonymous v Brackenmoor Pharma Ltd",
                          "title_tag": "Anonymous v Brackenmoor Pharma Ltd - PMCPA"}),
        "subject": receipt("Zentaril webinar slides", "h2_wins",
                           {"hero_h2": "Zentaril webinar slides", "cludo_description": "No breach of the Code - webinar slides"}),
        "parties": {
            "respondent": receipt("Brackenmoor Pharma Ltd", "canonical_entity"),
            "complainant": {
                "verbatim": "Anonymous",
                "category": "anonymous",
                "anonymous": True,
                "contactable": False,
            },
        },
        "code_year": receipt(2021, "unanimous"),
        "procedure": {
            "voluntary_admission": False,
            "abridged": False,
            "paragraph_17": False,
            "outwith_scope": False,
            "inter_company": False,
            "no_report": False,
        },
        "dates": {
            "received": receipt("2026-01-22", "meta_date"),
            "completed": receipt("2026-03-12", "meta_date"),
        },
        "verdicts": [
            {
                "clause": "7.2", "code_year": 2021,
                "clause_slug": "clause-7-information-claims-and-comparisons",
                "panel": "no_breach", "appeal_board": None, "final": "no_breach",
                "flipped_on_appeal": False,
                "basis": "info_holder_and_prose_agree",
                "sources": {"info_holder": "no_breach", "meta_csv": "no_breach", "prose": "no_breach"},
            },
        ],
        "appeal": {"appealed": False, "by": "none", "basis": "no_appeal_section"},
        "sanctions": {"undertaking": False, "additional": [], "clause_2_censure": False, "basis": "no_breach_no_undertaking"},
        "segments": [
            segment("complaint", SHARED, "report", "complaint_2", "html", True),
            segment("response", SHARED, "report", "response_2", "html", False, no_ruling_language=False),
            segment("panel_ruling", SHARED, "report", "ruling_2", "html", False, no_ruling_language=False),
            summary_rendition(SHARED),
            abstract_rendition(SHARED),
        ],
        "renditions": {"summary": 3, "report_abstract": 4, "pdf_flow": None},
        "entities": {
            "companies": ["Brackenmoor Pharma Ltd"],
            "products": ["Zentaril"],
            "people_roles": ["prescriber", "speaker"],
        },
        "quality": {
            "source_integrity": "ok",
            "pdf_substituted": False,
            "known_text_defects": [],
            "era": 2021,
            "report_chars": len(PANES[SHARED]["report"]),
        },
    },
    # (c) appeal flip: one clause overturned, one upheld. Carries a usable
    #     pdf_flow rendition (the only rendition kind that can contain clean
    #     complaint/response segments).
    {
        "schema_version": "l2.1",
        "case_number": receipt("TEST/0003/2/26", "unanimous", {"meta": "TEST/0003/2/26", "h1": "TEST/0003/2/26"}),
        "source_files": [APPEAL],
        "sibling_cases": [],
        "title": receipt("Nordwyck Laboratories Ltd v Halveston Biosciences Ltd", "h1_wins",
                         {"h1": "Nordwyck Laboratories Ltd v Halveston Biosciences Ltd",
                          "title_tag": "Nordwyck v Halveston - appeal - PMCPA"}),
        "subject": receipt("Olvexa journal advertisement", "h2_wins",
                           {"hero_h2": "Olvexa journal advertisement",
                            "cludo_description": "Appeal - breach of the Code overturned in part"}),
        "parties": {
            "respondent": receipt("Halveston Biosciences Ltd", "canonical_entity"),
            "complainant": {
                "verbatim": "Nordwyck Laboratories Ltd",
                "category": "company",
                "anonymous": False,
                "contactable": True,
            },
        },
        "code_year": receipt(2016, "unanimous"),
        "procedure": {
            "voluntary_admission": False,
            "abridged": False,
            "paragraph_17": False,
            "outwith_scope": False,
            "inter_company": True,
            "no_report": False,
        },
        "dates": {
            "received": receipt("2026-02-09", "meta_date"),
            "completed": receipt("2026-04-28", "meta_date"),
        },
        "verdicts": [
            {
                "clause": "6.1", "code_year": 2016,
                "clause_slug": "clause-6-substantiation",
                "panel": "breach", "appeal_board": "no_breach", "final": "no_breach",
                "flipped_on_appeal": True,
                "basis": "panel_prose_vs_appeal_prose",
                "sources": {"info_holder": "no_breach", "meta_csv": "breach,no_breach",
                            "panel_prose": "breach", "appeal_prose": "no_breach"},
            },
            {
                "clause": "9.1", "code_year": 2016,
                "clause_slug": "clause-9-high-standards",
                "panel": "breach", "appeal_board": "breach", "final": "breach",
                "flipped_on_appeal": False,
                "basis": "panel_prose_vs_appeal_prose",
                "sources": {"info_holder": "breach", "meta_csv": "breach",
                            "panel_prose": "breach", "appeal_prose": "breach"},
            },
        ],
        "appeal": {"appealed": True, "by": "respondent", "basis": "appeal_heading_fold_table"},
        "sanctions": {"undertaking": True, "additional": ["Public reprimand"], "clause_2_censure": False,
                      "basis": "appeal_board_sanction_chip"},
        "segments": [
            segment("abstract", APPEAL, "report", "abstract", "html", False, outside_abstract=False),
            segment("complaint", APPEAL, "report", "complaint", "html", True),
            segment("response", APPEAL, "report", "response", "html", True),
            segment("panel_ruling", APPEAL, "report", "panel_ruling", "html", False, no_ruling_language=False),
            segment("appeal_comments", APPEAL, "report", "appeal_comments", "html", True),
            segment("appeal_ruling", APPEAL, "report", "appeal_ruling", "html", False,
                    no_ruling_language=False, no_sanctions_text=False),
            segment("complaint", APPEAL_PDF, "flow", "complaint", "pdf", True),
            segment("response", APPEAL_PDF, "flow", "response", "pdf", True),
            summary_rendition(APPEAL),
            abstract_rendition(APPEAL),
        ],
        # No pdf_flow rendition: the invented PDF flow opens straight on the
        # complaint, so there is no leading abstract span to quote -- the same
        # reason 1,991 of the 2,004 real cases carry `pdf_flow: null`.
        "renditions": {"summary": 8, "report_abstract": 9, "pdf_flow": None},
        "entities": {
            "companies": ["Halveston Biosciences Ltd", "Nordwyck Laboratories Ltd"],
            "products": ["Olvexa", "bemtizumab", "Prantiva"],
            "people_roles": ["respiratory prescriber"],
        },
        "quality": {
            "source_integrity": "ok",
            "pdf_substituted": False,
            "known_text_defects": ["pdf ligature loss in flow pane"],
            "era": 2016,
            "report_chars": len(PANES[APPEAL]["report"]),
        },
    },
    # (d) outwith scope: no verdicts at all.
    {
        "schema_version": "l2.1",
        "case_number": receipt("TEST/0004/3/26", "unanimous", {"meta": "TEST/0004/3/26", "h1": "TEST/0004/3/26"}),
        "source_files": [SCOPE],
        "sibling_cases": [],
        "title": receipt("Member of the public v Nordwyck Laboratories Ltd", "h1_wins",
                         {"h1": "Member of the public v Nordwyck Laboratories Ltd",
                          "title_tag": "Member of the public v Nordwyck Laboratories Ltd - PMCPA"}),
        "subject": receipt("Corporate social media post", "h2_wins",
                           {"hero_h2": "Corporate social media post",
                            "cludo_description": "Outwith scope of the Code"}),
        "parties": {
            "respondent": receipt("Nordwyck Laboratories Ltd", "canonical_entity"),
            "complainant": {
                "verbatim": "Member of the public",
                "category": "member_of_public",
                "anonymous": False,
                "contactable": True,
            },
        },
        "code_year": receipt(2021, "unanimous"),
        "procedure": {
            "voluntary_admission": False,
            "abridged": False,
            "paragraph_17": False,
            "outwith_scope": True,
            "inter_company": False,
            "no_report": False,
        },
        "dates": {
            "received": receipt("2026-04-30", "meta_date"),
            "completed": receipt("2026-06-05", "meta_date"),
        },
        "verdicts": [],
        "appeal": {"appealed": False, "by": "none", "basis": "no_appeal_section"},
        "sanctions": {"undertaking": False, "additional": [], "clause_2_censure": False, "basis": "outwith_scope"},
        "segments": [
            segment("complaint", SCOPE, "report", "complaint", "html", True),
            segment("response", SCOPE, "report", "response", "html", True),
            segment("panel_ruling", SCOPE, "report", "ruling", "html", False, no_ruling_language=False),
            summary_rendition(SCOPE),
        ],
        "renditions": {"summary": 3, "report_abstract": None, "pdf_flow": None},
        "entities": {
            "companies": ["Nordwyck Laboratories Ltd"],
            "products": [],
            "people_roles": [],
        },
        "quality": {
            "source_integrity": "ok",
            "pdf_substituted": False,
            "known_text_defects": [],
            "era": 2021,
            "report_chars": len(PANES[SCOPE]["report"]),
        },
    },
]


def main():
    # Every segment ref must slice its pane; catch a typo here, not in bench.
    for case in CASES:
        for seg in case["segments"]:
            r = seg["ref"]
            text = PANES[r["file"]][r["pane"]]
            assert 0 <= r["char_start"] <= r["char_end"] <= len(text), (case["case_number"]["value"], r)
            assert text[r["char_start"]:r["char_end"]].strip(), "empty segment"

    with CASES_OUT.open("w", encoding="utf-8") as fh:
        for case in CASES:
            fh.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    PANES_OUT.write_text(
        json.dumps(PANES, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {CASES_OUT.name}  : {len(CASES)} cases")
    print(f"wrote {PANES_OUT.name} : {sum(len(p) for p in PANES.values())} panes across {len(PANES)} files")
    for case in CASES:
        n_clean = sum(1 for s in case["segments"] if s["leakage_attest"]["clean"])
        print(f"  {case['case_number']['value']}  verdicts={len(case['verdicts'])}  "
              f"segments={len(case['segments'])} (clean={n_clean})")


if __name__ == "__main__":
    main()
