"""
GRUS — Strands Agent Graph

Five agents. The first three run in parallel because they do not need
each other's output; Verifier waits for all three; Composer writes last.

    Retriever  ─┐
    Reconciler ─┼─→  Verifier  ─→  Composer
    Risk       ─┘

The important design point: the LLM decides WHICH tools to call, never
what is clinically true. Rules already decided severity, the retriever
already decided the values. An agent that picks 'search the notes for
anticoagulants' is choosing a query, not a diagnosis.

Requires: pip install strands-agents
"""

import os
import json
import re
import psycopg
from grus_config import DB, AWS

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent import GraphBuilder

from grus_tools import GrusTools
from grus_rules import evaluate



HOST = DB.HOST
PWD = DB.PASSWORD
REGION = AWS.REGION

# Qwen for the high-volume structured work, and for the Verifier too
# while Claude is unavailable — Anthropic models on Bedrock go through
# AWS Marketplace, which does not accept UPI AutoPay. Switch this back
# once a card is on the account; the Verifier is the one place where
# stronger reasoning genuinely helps.
FAST_MODEL = AWS.FAST_MODEL
REASONING_MODEL = AWS.REASONING_PREFERRED



# ---------------------------------------------------------------
# Session — the tools need a patient and a time cutoff, but Strands
# tools are plain functions. This holds the current context.
# ---------------------------------------------------------------
class Session:
    conn = None
    tools = None
    composer = None
    hadm_id = None
    as_of = None
    available_sources = []

    @classmethod
    def open(cls, conn, hadm_id, as_of_hours=None):
        from grus_composer import Composer
        cls.conn = conn
        cls.hadm_id = hadm_id
        cls.as_of = as_of_hours
        cls.tools = GrusTools(conn, hadm_id, as_of_hours)
        cls.composer = Composer(conn, hadm_id, as_of_hours)
        cls.available_sources = []
        return cls


def _result(r):
    """Tool results go to the model as JSON so nothing is lost in prose."""
    return json.dumps(r.to_dict(), default=str)


def _repair(raw):
    """
    Recover arguments from malformed tool-call JSON.

    Qwen sometimes emits the object without its opening brace:

        question": "why anticoagulated", "keywords": ["warfarin"]}

    Strands catches the parse error, defaults to an empty dict, and the
    tool runs with no arguments at all — search_notes searching for
    nothing, silently. The call appears to succeed and returns nothing
    useful, which is worse than failing.

    Returns a dict, or None when nothing can be salvaged.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None

    s = raw.strip()
    for candidate in (s, "{" + s, s + "}", "{" + s + "}"):
        try:
            v = json.loads(candidate)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue

    # Last resort: pull "key": value pairs out with a regex.
    out = {}
    for k, v in re.findall(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\]|[\d.]+|true|false)', s):
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v.strip('"')
    return out or None


# ---------------------------------------------------------------
# Tools — the same functions the plain-Python version used, exposed
# so an agent can choose among them.
# ---------------------------------------------------------------

@tool
def get_patient_summary() -> str:
    """Age, sex, admission type, and how many prior admissions exist.
    Cheap orienting call — use first."""
    return _result(Session.tools.get_patient_summary())


@tool
def get_active_alerts() -> str:
    """Alerts the deterministic rule engine produced, with the values each
    rule saw and the rows it read them from. Prefer this over working out
    risk yourself — these are already decided."""
    return _result(Session.tools.get_active_alerts())


@tool
def list_available_labs() -> str:
    """Which labs this patient has, with exact MIMIC labels. Call before
    get_lab when unsure of a name: MIMIC uses 'INR(PT)' not 'INR', and
    'Urea Nitrogen' not 'BUN'."""
    return _result(Session.tools.list_available_labs())


@tool
def get_lab(label: str = None, mode: str = "trend") -> str:
    """Any lab result by exact label.

    Args:
        label: exact MIMIC label, e.g. 'INR(PT)'
        mode: trend | latest | first | nadir | peak.
              'first' is the arrival value; 'nadir' the lowest, which is
              what matters for bleeding risk.
    """
    if not label:
        # A tool called with no arguments must say so. Returning an empty
        # result would look like 'no such lab exists', which is a
        # different and misleading claim.
        return json.dumps({
            "found": False,
            "summary": "get_lab needs a label.",
            "note": "Call list_available_labs to see the exact names. "
                    "MIMIC uses 'INR(PT)' not 'INR'."})
    return _result(Session.tools.get_lab(label, mode))


@tool
def get_arrival_labs() -> str:
    """What the patient arrived with, before treatment. Coagulation and
    haematology first."""
    return _result(Session.tools.get_arrival_labs())


@tool
def get_vital(code: str = None, mode: str = "latest") -> str:
    """A vital sign by short code.

    Args:
        code: hr, sbp, dbp, map, spo2, rr, temp_c, gcs_eye, gcs_verbal,
              gcs_motor, weight_kg
        mode: latest | trend | nadir | peak
    """
    if not code:
        return json.dumps({
            "found": False,
            "summary": "get_vital needs a code.",
            "note": "One of: hr, sbp, dbp, map, spo2, rr, temp_c, "
                    "gcs_eye, gcs_verbal, gcs_motor, weight_kg."})
    return _result(Session.tools.get_vital(code, mode))


@tool
def get_current_vitals() -> str:
    """Latest reading of every vital, flagged when stale."""
    return _result(Session.tools.get_current_vitals())


@tool
def get_medications(drug_class: str = None, drug_name: str = None,
                    status: str = None) -> str:
    """Medications this admission.

    Args:
        drug_class: anticoagulant, antiplatelet, nsaid, opioid,
                    reversal_agent, beta_blocker, antibiotic,
                    vasopressor, insulin, diuretic
        drug_name: partial name match
        status: active | stopped | unknown
    """
    return _result(Session.tools.get_medications(drug_class, drug_name, status))


@tool
def get_home_medications() -> str:
    """What the patient took before arrival, from the reconciled list in
    the notes and from prior admissions. Home medications are frequently
    absent from the structured record — this is how an anticoagulant gets
    missed."""
    return _result(Session.tools.get_home_medications())


@tool
def get_diagnoses() -> str:
    """Coded diagnoses, principal first."""
    return _result(Session.tools.get_diagnoses())


@tool
def get_recurring_diagnoses(min_visits: int = 2) -> str:
    """Conditions appearing across several prior admissions. 'Fifth
    presentation with heart failure' is a pattern nobody looks up by
    hand."""
    return _result(Session.tools.get_recurring_diagnoses(min_visits))


@tool
def get_procedures() -> str:
    """Coded procedures for this admission."""
    return _result(Session.tools.get_procedures())


@tool
def get_outputs(label: str = None) -> str:
    """Drain and urine volumes. Drain output is a direct measure of
    bleeding — often the most concrete number in a haemorrhage case."""
    return _result(Session.tools.get_outputs(label))


@tool
def search_notes(question: str = None, keywords: list = None,
                 k: int = 5) -> str:
    """Search the clinical notes semantically, plus exact matching when
    keywords are given.

    Use keywords for drug names. General-purpose embeddings map those
    poorly: a passage naming vitamin K scored 0.185 on semantic search
    while a keyword match found it immediately.
    """
    if not question and not keywords:
        # Searching for nothing returns nothing, which reads as 'the
        # notes contain nothing relevant' — a claim the tool has not
        # actually checked.
        return json.dumps({
            "found": False,
            "summary": "search_notes needs a question or keywords.",
            "note": "e.g. question='why is the patient anticoagulated', "
                    "keywords=['warfarin','coumadin']"})
    return _result(Session.tools.search_notes(
        question or " ".join(keywords), keywords, k))


@tool
def get_admission_history() -> str:
    """Previous admissions with their principal diagnoses."""
    return _result(Session.tools.get_admission_history())


@tool
def get_source_data() -> str:
    """The complete structured record for this patient, every value tagged
    with the database row it came from.

    Call this before writing anything. The other agents summarise in prose,
    which loses the source ids — a summary saying 'the alerts showed INR
    6.3' cannot be cited, while this returns 'INR(PT): 6.3 [labs#301694]'.

    Copy values and ids from here character for character.
    """
    block, sources = Session.composer.build_data_block()
    Session.available_sources = sources
    return block


@tool
def get_risk_scores() -> str:
    """Predicted risk from the trained models: transfusion likely within
    12h, acute kidney injury within 24h, electrolyte crisis within 12h.

    Returns a probability per model, whether it crosses that model's
    threshold, and how much of the feature vector was available. A score
    built from a quarter of the features is not the same claim as one
    built from most of them, and the tool says so.

    Returns available=false when there is too little data to score. That
    is not a low-risk result — say so rather than treating it as
    reassurance.
    """
    from grus_risk_score import score
    return json.dumps(score(Session.conn, Session.hadm_id, Session.as_of),
                      default=str)
@tool
def calculate_score(score_name: str = None, provided: dict = None) -> str:
    """Compute a validated clinical score for this patient.

    Fifteen are encoded: PERC, Wells (PE), HEART, qSOFA, SIRS, HAS-BLED,
    CHA2DS2-VASc, CURB-65, NEWS2, MEWS, KDIGO AKI, Shock Index,
    Glasgow-Blatchford, SOFA respiratory, anion gap.

    The record fills what it can; the rest needs examination or history.
    A score with missing criteria returns no total, with the question to
    ask for each gap. Report that as it comes — a partial PERC read as a
    real one turns 'not assessed' into 'rules out PE'.

    Args:
        score_name: e.g. 'PERC', 'HEART', 'shock index'
        provided: answers the clinician gave, e.g. {"leg_swelling": "no"}
    """
    from grus_scores import list_scores
    if not score_name:
        return json.dumps({"available": list_scores()})

    from grus_score_engine import ScoreEngine
    eng = ScoreEngine(Session.conn, Session.hadm_id, Session.as_of)
    r = eng.compute(score_name, provided or {})
    if r is None:
        return json.dumps({
            "found": False,
            "summary": f"No score called '{score_name}'.",
            "available": [s["key"] for s in list_scores()]})
    return json.dumps(r.to_dict(), default=str)


@tool
def suggest_scores(presentation: str = None) -> str:
    """Which clinical scores are worth running for this patient.

    Args:
        presentation: optional, e.g. 'chest pain', 'bleeding', 'infection'
    """
    from grus_score_engine import ScoreEngine
    from grus_scores import SCORES
    eng = ScoreEngine(Session.conn, Session.hadm_id, Session.as_of)
    return json.dumps({"suggested": [
        {"key": k, "name": SCORES[k]["name"], "purpose": SCORES[k]["purpose"]}
        for k in eng.suggest(presentation)]})

RETRIEVAL_TOOLS = [
    get_patient_summary, get_active_alerts,
    list_available_labs, get_lab, get_arrival_labs,
    get_vital, get_current_vitals,
    get_medications, get_home_medications,
    get_diagnoses, get_recurring_diagnoses, get_procedures,
    get_outputs, search_notes, get_admission_history,
    get_risk_scores,
    calculate_score, suggest_scores,
]


# ---------------------------------------------------------------
# Agents
# ---------------------------------------------------------------

def fast_model():
    return BedrockModel(model_id=FAST_MODEL, region_name=REGION,
                        temperature=0.2, max_tokens=2500)


def reasoning_model():
    return BedrockModel(model_id=REASONING_MODEL, region_name=REGION,
                        temperature=0.2, max_tokens=2000)


RETRIEVER_PROMPT = """You gather the facts for an emergency brief.

Call the tools you need. Start with get_patient_summary and
get_active_alerts — the alerts tell you what the rule engine already
found, and that tells you where to look next.

Then follow the clinical thread:
  anticoagulation flagged -> get_home_medications, search_notes for the
                             drug name, get_lab('INR(PT)', 'trend')
  bleeding flagged        -> get_lab('Hemoglobin','nadir'), get_outputs
  prior admissions exist  -> get_recurring_diagnoses, get_admission_history
  nothing flagged         -> get_arrival_labs, get_current_vitals

A tool returning found=false is an ANSWER, not a failure. 'No
anticoagulant on file' with an INR of 6.3 is the most important thing
you can report.

Output: a plain list of what you found, each line ending with its source
ids exactly as the tools gave them. Do not interpret. Do not diagnose.
Copy values character for character."""


RECONCILER_PROMPT = """You build the timeline and find contradictions.

Use the retrieved facts. Look for:
  - events out of order (a stop time before its start time)
  - a value in the notes that disagrees with the structured record
  - a drug in the home list that appears nowhere this admission
  - gaps: hours with no data at all

Report contradictions plainly. Do not resolve them — a doctor decides
which source to trust. Say what disagrees and cite both sides.

If nothing conflicts, say so in one line."""


RISK_PROMPT = """You report risk from two independent sources.

First call get_active_alerts — deterministic rules with published
thresholds. Then call get_risk_scores — models trained on 20,000
admissions.

Report both, separately. They answer different questions:

  A rule fires when a threshold is crossed. Shock index 1.32, alert.
  A model weighs everything together and can flag a patient whose
  individual numbers are all still normal.

When they disagree, say so and show both. Do not pick a winner — a
clinician decides which signal to trust. 'Shock index above threshold,
model risk 0.31 with other markers stable' is a more useful sentence
than either half alone.

Do not invent a risk score, estimate a probability, or apply a threshold
the tools did not return.

Where either source says unknown — a shock index that cannot be
calculated, a model that could not score for lack of data — that is your
most important output. An unscored patient is not a low-risk patient.
Say what could not be assessed and why."""


VERIFIER_PROMPT = """You check that every claim traces to a source.

Call get_source_data first. That is the ground truth — every valid source
id and every real value is in there.

For each statement the other agents produced:
  - does it carry a source id shaped table#number?
  - is that id present in get_source_data?
  - does the value appear in get_source_data, copied not invented?

Reject anything citing a tool name rather than a row: [get_active_alerts]
is not a source. Reject any number absent from the data — a fabricated
age or vital sign beside a real id looks verified and is not.

Output two lists: VERIFIED and REJECTED, with a reason for each
rejection. Do not rewrite the claims; only judge them."""


COMPOSER_PROMPT = """You write the final brief for a clinician.

FIRST, ALWAYS: call get_source_data.

That returns every value with the database row it came from:
    INR(PT): 6.3 (pre-arrival) ABNORMAL [labs#301694]
    Heart Rate: 89 @36.47h [vitals#492675]

The other agents wrote prose summaries. Prose loses source ids — "the
alerts showed INR 6.3" cannot be cited. Use their work to know what
matters; take every VALUE and every ID from get_source_data.

Copy character for character. Age, sex, every number. If a value is not
in get_source_data it does not exist, and writing a plausible one beside
a real source id is worse than omitting it.

STRUCTURE

SUMMARY        one line: age, sex, presentation, most important finding
RED FLAGS      each critical and warning alert, with its action
CURRENT STATE  vitals and key labs with timing
HISTORY        prior admissions and recurring conditions, if any
CRITICAL UNKNOWNS
               only what the data explicitly marks absent or unreliable

CITATIONS

End every clinical claim with its source exactly as given: [labs#301694].
Several: [labs#301694, vitals#492675].

Only table#number is a source. A tool name is not, and neither is a
phrase. When there is nothing to cite, write NOTHING — no brackets:

  WRONG:  Vitals: none recorded in the first hour [no source]
  RIGHT:  Vitals: none recorded in the first hour

  WRONG:  No prior admissions at this facility [no source]
  RIGHT:  No prior admissions at this facility

  WRONG:  INR 6.3 [get_active_alerts]
  RIGHT:  INR 6.3 [labs#301694]

An empty bracket, [no source], [unknown] or a tool name in brackets is a
false claim about provenance. Omit the brackets instead.

STYLE

Terse. Clinical register. Times as hours since arrival, never dates. A
registrar reads this in twenty seconds while walking.
Do not recommend a diagnosis or a drug dose."""


def build_agents():
    retriever = Agent(name="retriever", model=fast_model(),
                      tools=RETRIEVAL_TOOLS, system_prompt=RETRIEVER_PROMPT)

    reconciler = Agent(name="reconciler", model=fast_model(),
                       tools=[get_medications, get_home_medications,
                              search_notes, get_lab],
                       system_prompt=RECONCILER_PROMPT)

    risk = Agent(name="risk", model=fast_model(),
                 tools=[get_active_alerts, get_current_vitals,
                        get_risk_scores],
                 system_prompt=RISK_PROMPT)

    # Both of these need the source data itself, not summaries of it.
    verifier = Agent(name="verifier", model=reasoning_model(),
                     tools=[get_source_data],
                     system_prompt=VERIFIER_PROMPT)

    composer = Agent(name="composer", model=fast_model(),
                     tools=[get_source_data],
                     system_prompt=COMPOSER_PROMPT)

    return retriever, reconciler, risk, verifier, composer


def build_graph():
    """
    Three parallel gatherers, then verification, then writing.

    Parallel matters: retrieval, reconciliation and risk do not depend on
    each other, so running them in sequence means waiting three times
    instead of once.

    The limits are not decoration. An agent that keeps calling tools
    without converging would otherwise run until the request times out,
    and in a clinical setting a brief that never arrives is worse than a
    short one.
    """
    retriever, reconciler, risk, verifier, composer = build_agents()

    b = GraphBuilder()
    b.add_node(retriever, "retriever")
    b.add_node(reconciler, "reconciler")
    b.add_node(risk, "risk")
    b.add_node(verifier, "verifier")
    b.add_node(composer, "composer")

    b.add_edge("retriever", "verifier")
    b.add_edge("reconciler", "verifier")
    b.add_edge("risk", "verifier")
    b.add_edge("verifier", "composer")

    b.set_entry_point("retriever")
    b.set_entry_point("reconciler")
    b.set_entry_point("risk")

    # The graph is a DAG, so a cycle is impossible by construction — but
    # Strands warns without an explicit bound, and a node limit also
    # catches a misconfiguration that adds an edge back.
    try:
        b.set_max_node_executions(12)
        b.set_execution_timeout(180)
        b.set_node_timeout(60)
    except AttributeError:
        # Older Strands versions do not expose these.
        pass

    return b.build()


def check_citations(text):
    """
    A deterministic pass over the finished brief.

    The Verifier agent is an LLM judging other LLMs, which is useful but
    not a guarantee. This is arithmetic: pull every bracketed tag out of
    the text and check it against the ids the tools actually returned.

    Returns (valid, invalid, coverage_pct).
    """
    import re
    tags = re.findall(r"\[([^\]]+)\]", text)
    valid, invalid = [], []
    allowed = set(Session.available_sources)

    for block in tags:
        block = block.strip()

        # A bracket with no '#' is not an attempt at a citation at all —
        # "[no source]", "[unknown]". Count it once, not once per word.
        if "#" not in block:
            invalid.append(block)
            continue

        for part in re.split(r"[,\s]+", block):
            if not part:
                continue
            if re.fullmatch(r"[a-z_]+#\d+", part):
                (valid if part in allowed else invalid).append(part)
            else:
                invalid.append(part)

    total = len(valid) + len(invalid)
    pct = round(100 * len(valid) / total) if total else 0
    return valid, invalid, pct


def generate_brief(conn, hadm_id, as_of_hours=None, verbose=True):
    """Run the graph for one patient."""
    Session.open(conn, hadm_id, as_of_hours)

    # Populate the allowed-source set before the graph runs, so the
    # citation check has something to compare against even if an agent
    # never calls get_source_data.
    _, Session.available_sources = Session.composer.build_data_block()

    window = ("the full record" if as_of_hours is None
              else f"only the first {as_of_hours} hours after arrival")
    task = (f"Produce the emergency brief for admission {hadm_id}, using "
            f"{window}. Nothing recorded after that point exists for this "
            f"purpose.")

    graph = build_graph()
    result = graph(task)

    if verbose:
        print(f"\nnodes executed: {len(result.results)}")
        for name in result.results:
            print(f"  {name}")

    return result


if __name__ == "__main__":
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require", keepalives=1, keepalives_idle=30)
    conn.execute("SET search_path TO grus, public")

    print("=" * 70)
    print("GRUS — Strands agent graph")
    print("=" * 70)

    result = generate_brief(conn, 28173870, as_of_hours=1)

    print("\n" + "=" * 70)
    print("BRIEF")
    print("=" * 70)

    composer_out = result.results.get("composer")
    if composer_out:
        text = str(composer_out.result)
        print(text)

        valid, invalid, pct = check_citations(text)
        print("\n" + "-" * 70)
        print(f"citations: {len(valid)} valid, {len(invalid)} invalid "
              f"({pct}% traceable)")
        if invalid:
            print(f"  rejected: {sorted(set(invalid))[:8]}")
        else:
            print("  every citation resolves to a real database row")
    else:
        print("(composer produced no output)")
        print(result)

    conn.close()