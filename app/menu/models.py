from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ModifierOption(BaseModel):
    id: UUID
    name: str
    kind: Literal["choice", "add", "remove"]
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
