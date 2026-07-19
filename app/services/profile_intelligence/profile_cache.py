import hashlib
import json
from pathlib import Path

from app.services.profile_intelligence.profile_builder import (
    ProfileBuilder,
)


class ProfileCache:
    """
    Manages the intelligent profile cache.

    Rebuilds only when the master profile changes.
    """

    def __init__(self):

        self.master = Path(
            "app/data/master_candidate_profile.json"
        )

        self.cache = Path(
            "app/data/candidate_profile_intelligence.json"
        )

        self.hash_file = Path(
            "app/data/profile.hash"
        )

    # ==========================================================
    # Calculate File Hash
    # ==========================================================

    def calculate_hash(self):

        with open(

            self.master,

            "rb"

        ) as f:

            return hashlib.md5(

                f.read()

            ).hexdigest()

    # ==========================================================
    # Cache Exists
    # ==========================================================

    def exists(self):

        return (

            self.cache.exists()

            and

            self.hash_file.exists()

        )

    # ==========================================================
    # Needs Rebuild
    # ==========================================================

    def needs_rebuild(self):

        if not self.exists():

            return True

        current_hash = self.calculate_hash()

        stored_hash = self.hash_file.read_text().strip()

        return current_hash != stored_hash

    # ==========================================================
    # Build / Refresh
    # ==========================================================

    def build_if_needed(self):

        if self.needs_rebuild():

            print()

            print("=" * 70)
            print("Building Profile Intelligence...")
            print("=" * 70)

            ProfileBuilder().build()

            self.hash_file.write_text(

                self.calculate_hash()

            )

        else:

            print()

            print("=" * 70)
            print("Using Cached Profile Intelligence")
            print("=" * 70)

    # ==========================================================
    # Load
    # ==========================================================

    def load(self):

        self.build_if_needed()

        with open(

            self.cache,

            "r",

            encoding="utf-8"

        ) as f:

            return json.load(f)