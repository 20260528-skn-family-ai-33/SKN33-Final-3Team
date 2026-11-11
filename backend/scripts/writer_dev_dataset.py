"""Load frozen synthetic Writer states and score observable checks separately from human review."""
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.agents.compose import cited_article_numbers, numbered_articles
from backend.app.agents.digest import korean_usd
from backend.app.agents.graph import compose_node
from backend.app.agents.state import AgentState, AnswerStatus

DATA = Path(__file__).resolve().parents[2] / 'docs' / 'evaluations'


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Case(Record):
    case_id: str
    group: str
    state: AgentState
    country_names: dict[str, str] = Field(default_factory=dict)
    product_names: dict[str, str] = Field(default_factory=dict)


class Criteria(Record):
    expected_status: AnswerStatus
    required_facts: list[str] = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    amounts_usd: list[int]
    percentages: list[float]
    hs_codes: list[str]
    required_citations: list[int]


class Gold(Record):
    case_id: str
    route: Literal['compose', 'compose_text']
    mode: Literal['generated', 'fixed']
    tasks: dict[str, Criteria]


def read_rows(path, model):
    rows = {}
    for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        try:
            row = model.model_validate_json(line)
            if row.case_id in rows:
                raise ValueError(f'duplicate case_id {row.case_id}')
        except ValueError as exc:
            raise ValueError(f'{path.name}:{line_no}: {exc}') from exc
        rows[row.case_id] = row
    return rows


def load_dataset(cases_path=DATA / 'writer_dev_cases.jsonl', gold_path=DATA / 'writer_dev_gold.jsonl'):
    cases, gold = read_rows(cases_path, Case), read_rows(gold_path, Gold)
    if len(cases) != 100 or cases.keys() != gold.keys():
        raise ValueError('Dev requires 100 unique cases with matching gold IDs')
    dataset = []
    for key, case in cases.items():
        rule, state = gold[key], case.state
        if not state.plan or state.draft is not None or not state.steps_finished():
            raise ValueError(f'{key}: expected a finished plan before compose')
        if list(rule.tasks) != [t.task_id for t in state.plan.tasks] or rule.route != compose_node(state):
            raise ValueError(f'{key}: gold task IDs or route do not match the plan')
        cards = numbered_articles(state)
        for tid, criteria in rule.tasks.items():
            valid = {c['n'] for c in cards if c['task_id'] == tid and c.get('summary') and c.get('evidence_sufficient')}
            if set(criteria.required_citations) - valid:
                raise ValueError(f'{key}/{tid}: required citation unavailable in this task')
        dataset.append((case, rule))
    return dataset


# Compare whole displayed amounts. For example, "3만 달러" is not a match inside "1억 3만 달러".
_AMOUNT = re.compile(r'(?<![\d.,])[-+]?(?:\d[\d,]*(?:\.\d+)?\s*억(?:\s*\d[\d,]*(?:\.\d+)?\s*만)?|\d[\d,]*(?:\.\d+)?\s*만?)\s*(?:달러|USD)', re.I)
_PERCENT = re.compile(r'(?<![\d.])[-+]?(\d+(?:\.\d+)?)\s*%(?!\s*[pP])')


def amount_key(value):
    return re.sub(r'[\s,]', '', value.upper().replace('USD', '달러'))


def score(state, rule, *, fixed):
    """Presence checks do not certify polarity, attribution, causal claims or completeness."""
    by_id = {t.task_id: t.answer_status for t in state.plan.tasks}
    checks = {}
    for item in state.draft.conclusion:
        tid = item.task_id
        expected = rule.tasks[tid]
        # In multi-task answers the shared explanation cannot establish attribution to a single task.
        sentences = list(item.sentences)
        if len(rule.tasks) == 1:
            sentences.extend(state.draft.explanation)
        text = ' '.join(sentences)
        amounts = {amount_key(m.group()) for m in _AMOUNT.finditer(text)}
        percentages = {float(m.group(1)) for m in _PERCENT.finditer(text)}
        citations = {int(n) for n in cited_article_numbers(text)}
        checks[tid] = {
            'status_matches': by_id[tid] == expected.expected_status,
            'missing_display_amounts': [korean_usd(v) for v in expected.amounts_usd if amount_key(korean_usd(v)) not in amounts],
            'missing_percentage_magnitudes': [v for v in expected.percentages if abs(v) not in percentages],
            'missing_hs_codes': [c for c in expected.hs_codes if not re.search(r'(?<!\d)' + re.escape(c) + r'(?!\d)', text)],
            'missing_required_citations': sorted(set(expected.required_citations) - citations),
        }
    return {'mode_matches': fixed == (rule.mode == 'fixed'), 'task_checks': checks,
            'semantic_review': 'pending'}
