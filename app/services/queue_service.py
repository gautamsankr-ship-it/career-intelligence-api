from app.models.application_queue import (
    ApplicationQueue,
    QueueItem
)
from app.config import (
    SCREENING_AUTO_APPLY,
    SCREENING_REVIEW,
    SCREENING_SKIP,
)


class QueueService:

    def __init__(self):

        self.queue = ApplicationQueue()

    def add_application(self, opportunity):

        decision = "DISCOVERED"
        priority = "LOW"
        score = 0

        if opportunity.decision:

            decision = opportunity.decision.decision
            priority = opportunity.decision.priority
            score = opportunity.decision.overall_score

        if decision in {SCREENING_SKIP, SCREENING_REVIEW, SCREENING_AUTO_APPLY}:
            status = decision

        else:

            status = "DISCOVERED"

        item = QueueItem(

            company=opportunity.company,

            job_title=opportunity.job_title,

            score=score,

            decision=decision,

            priority=priority,

            status=status,

            job_url=opportunity.job_url,

            application_context=opportunity

        )

        self.queue.add(item)

        return item
