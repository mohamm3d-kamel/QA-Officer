"""Settings model - key-value store for app configuration."""
from sqlalchemy import Column, Integer, String, Text
from database import Base


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value,
        }

