from app.models.application_queue import (
    ApplicationQueue,
    QueueItem
)

queue = ApplicationQueue()

queue.add(

    QueueItem(

        company="Bamboo",

        job_title="Financial Data Analyst",

        score=87,

        decision="GENERATE_AND_QUEUE",

        priority="HIGH",

        status="PENDING",

        job_url="https://example.com",

        application_context={}

    )

)

print()

print("Pending:", len(queue.pending()))

print("Approved:", len(queue.approved()))

print("Rejected:", len(queue.rejected()))