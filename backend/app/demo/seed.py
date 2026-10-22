"""Reset the local demo environment to a known state.

Safety: only removes the configured SQLite file and storage directory. For a
non-SQLite database it drops and recreates DataSentinel's own tables only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings
from app.database.base import Base
from app.database.session import build_engine, build_session_factory, init_db
from app.demo.sample_data import generate_all
from app.services import baseline_service, dataset_service, lineage_service, quality_service, storage

logger = logging.getLogger(__name__)

SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"

DEMO_RULES = [
    {
        "name": "Revenue formula",
        "rule_type": "business_rule",
        "severity": "high",
        "params": {
            "expression": "abs(revenue - quantity * unit_price * (1 - discount_pct / 100)) <= 0.01",
            "description": "revenue = quantity x unit_price x (1 - discount_pct/100), within 0.01",
        },
    },
    {
        "name": "Customer exists in Customer Master",
        "rule_type": "referential_integrity",
        "column": "customer_id",
        "severity": "high",
        "params": {"reference_dataset": "Customer Master", "reference_column": "customer_id"},
    },
]


def reset_demo(settings: Settings, *, with_v2: bool = False, sample_dir: Path = SAMPLE_DIR) -> dict:
    """Regenerate samples, wipe metadata + storage, and register the demo datasets.

    Registers *Retail Transactions* (v1) and *Customer Master*. With ``with_v2`` the
    modified version 2 is also uploaded; by default it is left for the live demo.
    """
    import app.models  # noqa: F401

    generate_all(sample_dir)

    engine = build_engine(settings.database_url)
    Base.metadata.drop_all(engine)
    init_db(engine)
    storage.clear_storage(settings)

    session = build_session_factory(engine)()
    registered = []
    try:
        retail, _ = dataset_service.register_dataset(
            session,
            settings,
            name="Retail Transactions",
            description="SYNTHETIC online retail transactions used for the DataSentinel demo.",
            owner="demo",
            project="Retail Analytics",
            is_synthetic=True,
            content=(sample_dir / "retail_transactions_v1.csv").read_bytes(),
            filename="retail_transactions_v1.csv",
            notes="Clean baseline candidate (Q1 2025).",
        )
        registered.append(retail.name)
        customers, _ = dataset_service.register_dataset(
            session,
            settings,
            name="Customer Master",
            description="SYNTHETIC clean customer master data.",
            owner="demo",
            project="Retail Analytics",
            is_synthetic=True,
            content=(sample_dir / "customers_clean.csv").read_bytes(),
            filename="customers_clean.csv",
        )
        registered.append(customers.name)

        # Starter rules learned from the clean v1 profile + two hand-written demo rules.
        _, _, saved = quality_service.suggest_rules(session, retail.id, 1, save=True)
        for spec in DEMO_RULES:
            quality_service.create_rule(session, {"dataset_id": retail.id, **spec})
        rule_count = len(saved) + len(DEMO_RULES)

        # Approve the clean v1 as the baseline so drift / root-cause work out of the box.
        baseline_service.set_baseline(
            session, retail.id, 1, reason="Clean Q1 2025 synthetic baseline (demo seed)",
            set_by="demo", confirm=False,
        )
        # Seed the demo lineage graph (idempotent).
        dataset_name_to_id = {retail.name: retail.id, customers.name: customers.id}
        lineage_service.seed_demo_lineage(session, dataset_name_to_id)
        if with_v2:
            dataset_service.add_version(
                session,
                settings,
                retail.id,
                content=(sample_dir / "retail_transactions_v2.csv").read_bytes(),
                filename="retail_transactions_v2.csv",
                notes="Modified Q2 2025 file with injected issues.",
            )
    finally:
        session.close()
        engine.dispose()
    return {"registered": registered, "with_v2": with_v2, "sample_dir": str(sample_dir),
            "rules": rule_count, "baseline_version": 1}
