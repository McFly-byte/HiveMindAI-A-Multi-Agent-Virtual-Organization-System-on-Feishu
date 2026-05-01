from schemas.base import RunRequest


def main() -> None:
    """TODO: wire this script to the real Gateway or Agent runtime."""
    request = RunRequest(project_id="demo-rag", trigger_type="手动")
    print(request.model_dump())


if __name__ == "__main__":
    main()
