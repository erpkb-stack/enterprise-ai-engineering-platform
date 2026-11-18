from typing import Annotated

from pydantic import AfterValidator, StringConstraints

# Pragmatic email shape check (RFC 5322 is not regex-validatable). Allows internal domains like .local.
EmailStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    AfterValidator(str.lower),
]
