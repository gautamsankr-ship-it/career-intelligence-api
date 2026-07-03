from app.services.master_profile_service import MasterProfileService

profile = MasterProfileService().load()

print()

print("=" * 60)

print(profile["candidate"]["full_name"])

print(profile["candidate"]["title"])

print(profile["experience"]["years"])

print(profile["future_education"]["planned_degree"])

print("=" * 60)