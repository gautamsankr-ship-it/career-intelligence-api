"""Task 21.32: one-time, idempotent reconciliation of every existing
`application_history` row into the new CRM `crm_stage` lifecycle. Backs up
the database file before making any schema/data change. Safe to re-run --
already-migrated rows are skipped (see OpportunityCRMService.migrate_legacy_records).
"""
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import APPLICATION_HISTORY_DB
from app.services.opportunity_crm_service import OpportunityCRMService


def main():
    db_path = Path(APPLICATION_HISTORY_DB)
    backup_path = db_path.with_name(f"{db_path.stem}.bak-pre-crm-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    print(f"Backed up {db_path} -> {backup_path}")

    with OpportunityCRMService() as service:
        summary = service.migrate_legacy_records()
        print(f"Migration summary: {summary}")

        print()
        print("Confirmed applications (Trackers 61, 103, 81):")
        for tracker_id in (61, 103, 81):
            record = service.get_opportunity(tracker_id)
            if not record:
                print(f"  Tracker {tracker_id}: NOT FOUND")
                continue
            print(
                f"  Tracker {tracker_id}: {record['company']} / {record['job_title']} "
                f"-> crm_stage={record['crm_stage']} applied_at={record['applied_at']} "
                f"submission_confirmation_source={record.get('submission_confirmation_source')}"
            )

        print()
        print("Funnel counts:", service.funnel_counts())


if __name__ == "__main__":
    main()
