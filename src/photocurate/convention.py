"""The shared, versioned judgement convention for human labels and VLM experiments."""

MODEL_INSTRUCTIONS = """Evaluate the photo's album-worthiness after reasonable, standard,
non-generative corrections that could be automated: cropping, straightening, moderate
exposure and colour adjustments, red-eye correction, mild denoising and sharpening.
Do not penalize minor flaws when these corrections could reasonably address them.
A crop must preserve a useful composition and sufficient visible detail for an album.
Assess only potential supported by visible image content. Do not imagine content outside
the frame, reconstruct missing content, or recover severely degraded detail.
Still penalize cut-off subjects, poor expressions, substantial blur, and detail lost in
severely underexposed or overexposed areas when these corrections cannot address them.
Be conservative when recoverability is unclear; a reduced proxy limits technical judgement.
You are estimating correction potential, not verifying an actual correction pipeline."""

HUMAN_INSTRUCTIONS = """Juger le potentiel après corrections standard non génératives
raisonnablement automatisables : recadrage, redressement, exposition/couleurs modérées,
yeux rouges, léger débruitage/netteté.
Ne pas pénaliser un défaut mineur raisonnablement corrigeable. Le recadrage doit garder
une composition exploitable et assez de détails visibles pour un album.
Ne pas imaginer de contenu hors cadre, reconstruire du contenu absent ni récupérer des
détails fortement dégradés. Sujet coupé, expression ratée, flou important et détails perdus
dans les zones très sous-exposées ou surexposées restent pénalisants si non corrigeables.
Rester prudent si la récupération est incertaine : le proxy limite le jugement technique.
On estime un potentiel ; on ne vérifie pas ici un traitement photo réel."""

JUDGEMENT_CONVENTION = {
    "id": "standard_non_generative_v1",
    "model_instructions": MODEL_INSTRUCTIONS,
    "human_instructions": HUMAN_INSTRUCTIONS,
}
