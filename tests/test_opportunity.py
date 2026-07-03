from app.models.career_opportunity import CareerOpportunity

job = CareerOpportunity(

    company="EY",

    job_title="Senior Accountant",

    location="Remote",

    source="LinkedIn"

)

job.add_event(

    "DISCOVERED"

)

job.update_scores(

    raw=81.5,

    optimized=92.4,

    confidence=95

)

job.add_event(

    "PACKAGE_GENERATED"

)

print()

print(job)

print()

print("Timeline")

print("-"*50)

for event in job.timeline:

    print(

        event.stage,

        event.timestamp

    )