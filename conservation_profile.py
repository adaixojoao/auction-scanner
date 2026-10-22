"""Conservation benefits, constraints and unknowns, kept apart (Gamma G3).

Protected habitat is a possible benefit and an operational constraint, not
automatically a disadvantage. A map hit is not confirmed legal protection or
habitat quality; the caller supplies documented, current site findings.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ConservationAssessment:
    benefits: tuple[str, ...]
    constraints: tuple[str, ...]
    unresolved: tuple[str, ...]
    acquisition_ready: bool = False


def assess_conservation(evidence):
    benefits, constraints, unresolved = [], [], []
    fields = ("native_habitat", "connectivity", "restoration_potential")
    for field in fields:
        state = evidence.get(field)
        if state is True:
            benefits.append(field)
        elif state is not False:
            unresolved.append(field)
    protection = evidence.get("protected_status")
    if protection == "confirmed":
        benefits.append("documented_protection_context")
        constraints.append("verify_permitted_management")
    elif protection != "confirmed_absent":
        unresolved.append("protected_status")
    for field in ("legal_access", "water_rights", "management_costs", "wildfire_plan"):
        if evidence.get(field) != "reviewed":
            unresolved.append(field)
    return ConservationAssessment(tuple(benefits), tuple(constraints), tuple(unresolved))
