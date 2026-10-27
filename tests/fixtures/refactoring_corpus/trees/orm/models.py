# Archetype: declarative ORM model file.
#
# Every class here is a run of near-identical column declarations. A clone
# detector sees the repetition; a helper function cannot express it, because a
# declarative attribute is evaluated in the class body and bound to the class.
# The correct composed answer for this file is therefore no clone *step* - at
# most supporting evidence - which is what the composition corpus pins.

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="eu")
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="eu")
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="eu")
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
