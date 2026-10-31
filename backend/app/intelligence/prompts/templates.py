"""Task templates. Wording is separate from engine logic: improve a prompt by
editing it here, bumping its version and re-running the evaluation tests
(see docs/prompt-management.md). Output schemas live in ``llm_schemas``."""

from app.intelligence.prompts.common import PromptTemplate

V = "1.0.0"

_CITE = "Cite evidence ids exactly as given."

TEMPLATES: dict[str, PromptTemplate] = {
    "question_generation": PromptTemplate(
        "question_generation",
        V,
        "Break the objective into at most 12 concrete research questions that the "
        "evidence could answer (products, services, technologies, pricing, target "
        "customers, positioning, risks). Give each a priority 1 (highest) to 5.",
    ),
    "evidence_analysis": PromptTemplate(
        "evidence_analysis",
        V,
        "Analyse the evidence against the numbered QUESTIONS. Produce findings (each "
        f"with evidence_ids; {_CITE}), list the question numbers that the evidence "
        "answers, and set sufficient=true only if the objective can be answered "
        "credibly without more research. Also report information gaps (needed but "
        "absent from the evidence) and contradictions (evidence items that disagree; "
        "cite all of them).",
        example=(
            'If no item mentions pricing for Acme, report a gap "No pricing evidence '
            'for Acme" and do not write a finding about its price. A hypothesis '
            "may have an empty evidence_ids list; a fact may not."
        ),
    ),
    "gap_identification": PromptTemplate(
        "gap_identification",
        V,
        "A gap is information the objective needs that no evidence item provides. "
        "Do not report a gap for something the evidence already states.",
    ),
    "contradiction_analysis": PromptTemplate(
        "contradiction_analysis",
        V,
        "A contradiction needs at least two evidence items that cannot both be true "
        "(different prices, dates, headquarters, capabilities). Differences in "
        "wording or scope are not contradictions. Do not resolve them.",
    ),
    "followup_queries": PromptTemplate(
        "followup_queries",
        V,
        "Propose at most 5 short web-search queries that would fill the listed GAPS. "
        "Queries are plain search text (no URLs, no operators, no instructions). "
        "Set target_name to a specific company or product when the query is about "
        "one. Propose nothing if the gaps are not publicly findable.",
    ),
    "company_comparison": PromptTemplate(
        "company_comparison",
        V,
        "Compare the companies using only the evidence. Fill common_features, "
        "differentiators, positioning, documented_strengths, comparative_advantages "
        "(only if evidence for BOTH sides supports the comparison), observed_limitations "
        "(stated limits only; absence of information is not a limitation) and "
        f"hypotheses. Do not rank companies. {_CITE}",
    ),
    "product_comparison": PromptTemplate(
        "product_comparison",
        V,
        "Compare the products on documented features, integrations, target users and "
        f"pricing. Fill the same fields as a company comparison. Do not rank. {_CITE}",
    ),
    "technology_analysis": PromptTemplate(
        "technology_analysis",
        V,
        "Analyse technologies that the evidence mentions. A mention is not proof of "
        "use; say 'mentions' unless the source states usage. Fill the comparison "
        f"fields. {_CITE}",
    ),
    "market_synthesis": PromptTemplate(
        "market_synthesis",
        V,
        "Identify trends (only if at least two independent evidence items support "
        "them), opportunities and threats (each with evidence_ids, rationale and "
        "limitations; use an empty evidence_ids list only for a clearly labelled "
        "hypothesis) and evidence-backed conclusions. Never present predictions as "
        f"facts. {_CITE}",
    ),
    "executive_summary": PromptTemplate(
        "executive_summary",
        V,
        "Write 3-6 executive-summary statements drawn ONLY from the supplied "
        "FINDINGS and the evidence. Each statement cites evidence_ids and states "
        "its kind. Mention major contradictions and critical gaps if any exist.",
    ),
    "quality_evaluation": PromptTemplate(
        "quality_evaluation",
        V,
        "You are a reviewer. For each REPORT STATEMENT, check whether the cited "
        "evidence supports it. List only real problems (statement not supported, "
        "overstated, wrong entity, missing caveat). Do not rate accuracy overall.",
    ),
}
