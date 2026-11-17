"""Privacy levels for to-dos (decision D16): a to-do with no source and no area has no level of its own, so Ask may read
what was typed; a Health to-do is always High. This pins the behaviour so a change to it is deliberate."""

from app.models.category import Category
from app.models.todo import Todo
from app.privacy.reach import reach_for
from app.privacy.tiers import Tier


def test_a_general_to_do_has_no_level_of_its_own_and_a_health_to_do_is_high(session):
    reach = reach_for(session)
    general = Todo(title="Re-register the Ergon domain", category=None)
    assert reach.tier_of_todo(session, general) is None
    assert reach.includes_todo(session, general) is True
    assert reach.tier_of_todo(session, Todo(title="Book a blood test", category=Category.HEALTH)) == Tier.HIGH
