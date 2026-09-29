"""Stable semantic target and exact human instructions for annotation provenance."""

JUDGEMENT_TARGET_ID = "standard_non_generative_v1"

HUMAN_INSTRUCTIONS = """Évalue cette photo seule pour un album familial, uniquement d’après le
contenu visible, sans connaissances personnelles ni contexte extérieur. Juge son potentiel
après corrections ordinaires, non génératives et raisonnablement automatisables.
Ne suppose pas que le flou, les détails perdus ou le contenu absent puissent être récupérés.
Reste prudent en cas d’incertitude."""

JUDGEMENT_CONVENTION = {
    "id": JUDGEMENT_TARGET_ID,
    "human_instructions": HUMAN_INSTRUCTIONS,
}


def judgement_target_id(convention: dict | None) -> str | None:
    """Read only an explicit target ID, including from existing convention snapshots."""
    if not isinstance(convention, dict):
        return None
    target = convention.get("id")
    return target if isinstance(target, str) and target.strip() else None


def matching_targets(first: dict | None, second: dict | None) -> bool:
    target = judgement_target_id(first)
    return target is not None and target == judgement_target_id(second)
