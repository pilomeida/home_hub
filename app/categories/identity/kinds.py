"""Identity & Legal paper kinds (PLAN step 8a) and the renewal lead time.

Every paper names its holder (a person, a subject of the registry) and, for an identity paper, its number and expiry.
A will has no expiry. The number is kept on the paper's details only: never in a To-Do, a notice or the Overview."""

from enum import Enum


class IdentityKind(str, Enum):
    ID_CARD = "id_card"
    PASSPORT = "passport"
    DRIVING_LICENCE = "driving_licence"
    RESIDENCE = "residence"
    WILL = "will"


ALL_KINDS = frozenset(k.value for k in IdentityKind)
EXPIRING = frozenset(ALL_KINDS - {IdentityKind.WILL.value})
LABELS = {
    IdentityKind.ID_CARD.value: "ID card",
    IdentityKind.PASSPORT.value: "Passport",
    IdentityKind.DRIVING_LICENCE.value: "Driving licence",
    IdentityKind.RESIDENCE.value: "Residence papers",
    IdentityKind.WILL.value: "Will",
}

# A passport or ID card takes weeks to renew (an appointment first): the To-Do comes this long before it expires.
RENEW_BEFORE_DAYS = 90
