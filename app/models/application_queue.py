from dataclasses import dataclass, field
from typing import List, Any
from app.config import SCREENING_AUTO_APPLY, SCREENING_REVIEW, SCREENING_SKIP


@dataclass
class QueueItem:

    company: str

    job_title: str

    score: float

    decision: str

    priority: str

    status: str

    job_url: str

    application_context: Any


@dataclass
class ApplicationQueue:

    items: List[QueueItem] = field(default_factory=list)

    def add(self, item: QueueItem):

        self.items.append(item)

    def pending(self):

        return [

            item

            for item in self.items

            if item.status == SCREENING_REVIEW

        ]

    def ready(self):

        return [

            item

            for item in self.items

            if item.status == SCREENING_AUTO_APPLY

        ]

    def rejected(self):

        return [

            item

            for item in self.items

            if item.status == SCREENING_SKIP

        ]

    def all(self):

        return self.items
