from db import create_job, init_db


def main() -> None:
    init_db()
    create_job(
        "sample-job",
        "sample-invoice.pdf",
        "sample-hash",
        "data/uploads/sample-invoice.pdf",
        0,
    )
    print("Inserted sample queued job: sample-job")


if __name__ == "__main__":
    main()
