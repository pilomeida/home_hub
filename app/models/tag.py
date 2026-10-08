"""Tags on category nodes: cross-cutting labels for spending analysis."""

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Tag(SQLModel, table=True):
    """A tag that can be assigned to category nodes for cross-cutting analysis."""
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)  # lowercase slug like "insurance"
    label: str                                   # display text like "Insurance"


class NodeTag(SQLModel, table=True):
    """Assignment of a tag to a category node."""
    __tablename__ = "node_tags"
    __table_args__ = (UniqueConstraint("node_id", "tag_id", name="uq_node_tags_node_id_tag_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: int = Field(foreign_key="category_nodes.id", index=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
