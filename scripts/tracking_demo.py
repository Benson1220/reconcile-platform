"""Import one of three synthetic snapshots for the P9003 walkthrough."""
import argparse

from app.database import initialize_database, save_snapshot
from app.pipeline import prepare_sample, prepare_snapshot


def demo_snapshot(stage: str):
    sample = prepare_sample()
    if stage == "first":
        return sample

    files = dict(sample.files)
    config = dict(sample.config)
    if stage == "repeat":
        config["as_of"] = "2026-11-17T12:00:00+08:00"
        # Simulate a newly exported order snapshot while preserving the missing settlement.
        orders = files["orders"].decode("utf-8").splitlines(keepends=True)
        files["orders"] = "".join(
            line.replace("T09:00:00", "T11:45:00") if line.startswith("O1003,") else line
            for line in orders
        ).encode()
    else:
        config.update(as_of="2026-11-18T09:00:00+08:00", settlement_complete_through="2026-11-17")
        files["settlements"] = files["settlements"].rstrip(b"\r\n") + (
            b"\nS8003,P9003,800.00,16.00,784.00,TWD,2026-11-17\n"
        )
    return prepare_snapshot(files, config, coverage_source="tracking_demo")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["first", "repeat", "settled"])
    args = parser.parse_args()
    initialize_database()
    result = save_snapshot(demo_snapshot(args.stage))
    print(f"比對批次 #{result['run_id']} · 異常 {result['case_count']} 筆")


if __name__ == "__main__":
    main()
