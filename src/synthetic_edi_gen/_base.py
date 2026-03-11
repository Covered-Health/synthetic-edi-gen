from typing import ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class EDIBaseModel(BaseModel):
    model_config: ClassVar[ConfigDict] = {
        "extra": "allow",
        "alias_generator": to_camel,
        "validate_by_name": True,
        "validate_by_alias": True,
        "serialize_by_alias": True,
        "validate_assignment": True,
    }
