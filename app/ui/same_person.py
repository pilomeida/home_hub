"""The "Same person?" section on the Inbox page: two names the Hub thinks are one person, for the family to join
or keep apart (answered at /subjects/matches/{id})."""

from sqlmodel import Session

from app.services import subjects as svc
from app.templating import templates


def section_html(session: Session) -> str:
    return templates.get_template("inbox/_same_person.html").render(proposals=svc.pending(session))
