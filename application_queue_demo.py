"""Manual ApplicationQueue demo — NOT a pytest test.

In-memory model manipulation (no production/network access), but has no
assertions and is not a real test, so it must never be collected by pytest.
Run it deliberately from the command line only.
"""

from app.models.application_queue import ApplicationQueue, QueueItem


def main() -> None:
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
            application_context={},
        )
    )

    print()
    print("Pending:", len(queue.pending()))
    print("Approved:", len(queue.approved()))
    print("Rejected:", len(queue.rejected()))


if __name__ == "__main__":
    main()
