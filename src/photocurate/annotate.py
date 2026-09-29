"""Terminal labels with images opened in Preview without activating the app."""

import subprocess
import sys

from .convention import HUMAN_INSTRUCTIONS, JUDGEMENT_CONVENTION
from .media import ensure_proxy
from .store import Store, dumps

SCALE = """0 = à jeter
1 = bof / seulement si utile au contexte
2 = bonne photo d'album
3 = forte / quasi certain de garder
? = impossible à juger seule"""


def annotate(
    store: Store,
    name: str,
    annotator: str,
    review: bool = False,
    viewer: bool = True,
    ask=input,
    output=print,
) -> dict:
    if not annotator.strip():
        raise ValueError("An annotator name is required")
    if viewer and sys.platform != "darwin":
        raise ValueError("Preview annotation requires macOS; use --no-viewer elsewhere")
    evaluation = store.get_set(name)
    members = store.members(evaluation["id"])
    labels = {row["position"]: row["label"] for row in members}
    current_convention = dumps(JUDGEMENT_CONVENTION)
    matching = {row["position"]: row["convention_json"] == current_convention for row in members}
    outdated = sum(row["label"] is not None and not matching[row["position"]] for row in members)
    if outdated:
        output(f"Convention mise à jour : {outdated} annotations à revoir.")
    queue = (
        list(range(len(members)))
        if review
        else [
            i
            for i, row in enumerate(members)
            if row["label"] is None or not matching[row["position"]]
        ]
    )
    done = []
    corrected = None
    count = 0
    while True:
        if not queue and corrected is None:
            if not done:
                break
            output("\nToutes les images sont annotées. b = corriger la dernière ; q = terminer")
            try:
                command = ask("Commande : ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if command == "b":
                corrected = done[-1]
            elif command == "q":
                break
            else:
                continue
        index = corrected if corrected is not None else queue[0]
        row = members[index]
        proxy = ensure_proxy(store, row["version_id"], 512)
        if viewer:
            subprocess.run(["open", "-g", "-a", "Preview", str(proxy)], check=True)
        output(f"\n{name}: {row['position']}/{len(members)} — {row['source_path']}")
        if labels[row["position"]] is not None:
            output(f"Label actuel : {labels[row['position']]}")
        output(HUMAN_INSTRUCTIONS)
        output(SCALE)
        output("b = corriger la précédente ; q = quitter et reprendre plus tard")
        try:
            label = ask("Label : ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            output("\nAnnotations enregistrées.")
            break
        if label == "q":
            break
        if label == "b":
            if corrected is None and done:
                corrected = done[-1]
            else:
                output("Aucune annotation précédente à corriger.")
            continue
        if label not in ("0", "1", "2", "3", "?"):
            output("Saisir 0, 1, 2, 3, ?, b ou q.")
            continue
        store.set_label(evaluation["id"], row["position"], label, annotator)
        labels[row["position"]] = label
        matching[row["position"]] = True
        count += 1
        if corrected is not None:
            corrected = None
        else:
            done.append(queue.pop(0))
    return {
        "name": name,
        "writes": count,
        "labelled": sum(
            label is not None and matching[position] for position, label in labels.items()
        ),
        "size": len(members),
    }
