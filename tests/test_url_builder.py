from app.services.linkedin_url_builder import LinkedInURLBuilder

builder = LinkedInURLBuilder()

urls = builder.build_urls()

print()

print("=" * 80)

for item in urls:

    print(item["name"])

    print(item["url"])

    print("-" * 80)