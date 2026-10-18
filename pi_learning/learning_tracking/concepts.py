import re


COURSE_PROFILES = {
    "database concepts": (
        "database", "sql", "schema", "table", "key", "constraint", "join",
        "trigger", "audit", "provenance",
    ),
    "analysis of categorical data": (
        "categorical", "classification", "outcome", "logistic", "odds", "count",
        "model", "aic", "diagnostic", "regression",
    ),
    "applied bayesian statistics": (
        "bayesian", "prior", "posterior", "mcmc", "jags", "uncertainty",
        "calibration", "regression",
    ),
}
GENERIC_TITLE_TERMS = {"assessment", "assignment", "case", "studies"}


def concept_terms(course: str, title: str, fallback: tuple[str, ...] = ()) -> list[str]:
    profile = COURSE_PROFILES.get(re.sub(r"\s*\([^)]*\)\s*$", "", course).lower())
    terms = list(profile or fallback)
    for term in re.findall(r"[a-z0-9]+", title.lower()):
        if term not in GENERIC_TITLE_TERMS and term not in terms:
            terms.append(term)
    return terms
