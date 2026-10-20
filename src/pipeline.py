"""Entrypoint: extract -> dbt build -> export."""
import sys

from src.extract.__main__ import main as extract_main
from src.log import log


def main() -> int:
    code = extract_main(["all"])
    log("not_implemented", step="dbt", message="dbt build is not implemented yet")
    log("not_implemented", step="export", message="export is not implemented yet")
    return code


if __name__ == "__main__":
    sys.exit(main())
