from pydantic import BaseModel

class SetRoleBody(BaseModel):
    id: str
    role: str
