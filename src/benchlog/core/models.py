"""Circuit schema. Placeholder: the team defines this together at kickoff.

Everything else (CLI, server, checks, vision, frontend types) is built on these models.
"""

from pydantic import BaseModel


class Circuit(BaseModel):
    schema_version: int = 1
