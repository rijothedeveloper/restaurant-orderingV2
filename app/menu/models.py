from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(min_length=1, max_length=80)]
OptionKind = Literal["choice", "add", "remove"]
MAX_PRICE_CENTS = 1_000_000


class ModifierOption(BaseModel):
    id: UUID
    name: str
    kind: OptionKind
    price_delta_cents: int
    is_available: bool


class ModifierGroup(BaseModel):
    id: UUID
    name: str
    min_selections: int
    max_selections: int
    options: list[ModifierOption] = Field(default_factory=list)


class MenuItem(BaseModel):
    id: UUID
    name: str
    description: str | None
    price_cents: int
    prep_minutes: int
    is_available: bool
    aliases: list[str] = Field(default_factory=list)
    modifier_groups: list[ModifierGroup] = Field(default_factory=list)


class MenuCategory(BaseModel):
    id: UUID
    name: str
    items: list[MenuItem] = Field(default_factory=list)


class Menu(BaseModel):
    restaurant_id: UUID
    restaurant_name: str
    categories: list[MenuCategory] = Field(default_factory=list)

    def find_item(self, item_id: UUID) -> MenuItem | None:
        for category in self.categories:
            for item in category.items:
                if item.id == item_id:
                    return item
        return None


# The menu as a manager edits it, and the changes they send.


class EditableItem(BaseModel):
    id: UUID
    name: str
    description: str | None
    price_cents: int
    prep_minutes: int
    is_available: bool
    aliases: list[str]
    # The item's modifier groups, in the order they are asked.
    group_ids: list[UUID]


class EditableCategory(BaseModel):
    id: UUID
    name: str
    items: list[EditableItem]


class EditableMenu(BaseModel):
    """Categories and items in menu order, and every modifier group once."""

    categories: list[EditableCategory]
    groups: list[ModifierGroup]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CategoryInput(_Input):
    name: Name


class ItemInput(_Input):
    category_id: UUID
    name: Name
    description: str | None = Field(default=None, max_length=500)
    price_cents: int = Field(ge=0, le=MAX_PRICE_CENTS)
    prep_minutes: int = Field(ge=0, le=240)
    # How people say the item ("chick sando").
    aliases: list[Name] = Field(default_factory=list, max_length=20)
    group_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_lists(self) -> Self:
        if len({alias.lower() for alias in self.aliases}) != len(self.aliases):
            raise ValueError("an alias is listed twice")
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError("a modifier group is listed twice")
        self.description = self.description or None
        return self


class OptionInput(_Input):
    # An option the group already has; leave out to add a new one.
    id: UUID | None = None
    name: Name
    kind: OptionKind
    price_delta_cents: int = Field(default=0, ge=0, le=MAX_PRICE_CENTS)

    @model_validator(mode="after")
    def removing_is_free(self) -> Self:
        if self.kind == "remove" and self.price_delta_cents != 0:
            raise ValueError("removing an ingredient cannot have a price")
        return self


class GroupInput(_Input):
    """A modifier group with all of its options, in menu order.

    Options of the group that are not listed are taken off the menu.
    """

    name: Name
    # 1 or more makes the group required.
    min_selections: int = Field(ge=0, le=20)
    max_selections: int = Field(ge=1, le=20)
    options: list[OptionInput] = Field(max_length=50)

    @model_validator(mode="after")
    def check_options(self) -> Self:
        if self.max_selections < self.min_selections:
            raise ValueError("max_selections must be at least min_selections")
        if self.min_selections > len(self.options):
            raise ValueError("the group needs more options than it has")
        names = [option.name for option in self.options]
        if len(set(names)) != len(names):
            raise ValueError("an option name is listed twice")
        ids = [option.id for option in self.options if option.id is not None]
        if len(set(ids)) != len(ids):
            raise ValueError("an option is listed twice")
        return self


class Reorder(_Input):
    """Every id of the list being reordered, in the new order."""

    ids: list[UUID]
