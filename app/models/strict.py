"""SQLModel table models drop a keyword they do not know without a word: `Document(category="bill")` makes a paper
with no kind. A model that mixes this in refuses it instead (work-list B1b: a field renamed, a caller missed)."""


class RefusesUnknownFields:
    def __init__(self, **data):
        unknown = set(data) - set(type(self).model_fields)
        if unknown:
            raise TypeError(f"{type(self).__name__} has no field {', '.join(sorted(unknown))}")
        super().__init__(**data)
