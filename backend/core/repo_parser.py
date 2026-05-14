"""Vendored stub for metagpt.repo_parser.

The full upstream module parses repositories and produces UML class info
via tree-sitter and pydeps. The simulator path through `schema.py` only
needs `DotClassInfo` as a placeholder type for `UMLClassView.load_dot_class_info`,
which the StanfordTown experiment does not invoke. We therefore expose a
minimal pydantic model so imports resolve.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel


class DotClassAttribute(BaseModel):
    name: str = ""
    type_: str = ""
    default_: str = ""
    description: str = ""


class DotClassMethod(BaseModel):
    name: str = ""
    args: List[DotClassAttribute] = []
    return_args: DotClassAttribute = DotClassAttribute()
    description: str = ""


class DotClassRelationship(BaseModel):
    src: str = ""
    dest: str = ""
    relationship: str = ""
    label: str = ""


class DotClassInfo(BaseModel):
    name: str = ""
    package: str = ""
    attributes: dict[str, DotClassAttribute] = {}
    methods: dict[str, DotClassMethod] = {}
    compositions: List[str] = []
    aggregations: List[str] = []


__all__ = [
    "DotClassInfo",
    "DotClassAttribute",
    "DotClassMethod",
    "DotClassRelationship",
]
