"""Child-process entry points shared by source and bundled desktop builds."""

import sys
import time

from src.services.signal_scanner.core import Settings


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"scan", "research", "market"}:
        raise SystemExit("Expected scan, research, or market")
    settings = Settings.load()
    if args[0] == "scan":
        from src.services.signal_scanner.worker.jobs import Worker

        Worker(settings).run()
    elif args[0] == "research":
        from src.services.signal_scanner.worker.rule_validation import main as research_main

        from src.services.signal_scanner.services.rule_validation_web.store import Store

        store = Store.configured(settings.runtime)
        sys.argv = [sys.argv[0], "--root", str(store.root)]
        for path in store.input_roots:
            sys.argv += ["--input-root", str(path)]
        sys.argv += ["worker"]
        research_main()
    else:
        from src.services.signal_scanner.services.daily_market import DailyMarket

        collector = DailyMarket(settings)
        while True:
            collector.step()
            time.sleep(3)


if __name__ == "__main__":
    main()
