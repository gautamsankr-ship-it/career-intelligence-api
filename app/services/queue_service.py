from app.models.application_queue import (
    ApplicationQueue,
    QueueItem
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

        if decision == "REJECT":

            status = "REJECTED"

        elif decision == "GENERATE_AND_QUEUE":

            status = "PENDING"

        elif decision == "APPROVE_AND_SEND":

            status = "READY"

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