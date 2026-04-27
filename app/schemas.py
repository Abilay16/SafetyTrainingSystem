from pydantic import BaseModel
from typing import Optional

class RecordIn(BaseModel):
    idnum: str
    fio: str
    type: str
    incident: str
    signature: Optional[str] = None
    instrSignature: Optional[str] = None
    birthday: Optional[str] = None
    profession: Optional[str] = None
    cex: Optional[str] = None
    instructorName: Optional[str] = None
