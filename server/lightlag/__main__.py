"""python -m lightlag  ->  start the universe server."""
import logging

import uvicorn

from .api.app import create_app, lan_urls
from .config import CONFIG


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = create_app(CONFIG)
    print("\n  LIGHT-LAG universe server")
    print(f"  delay scale {CONFIG.delay_scale} (universe runs {CONFIG.time_rate:.0f}x)  judge mode: {CONFIG.judge_mode}")
    for u in lan_urls(CONFIG.port):
        print(f"  join:  {u}/   control: {u}/control/   ship: {u}/ship/   spectator: {u}/spectator/")
    print()
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level="warning")


if __name__ == "__main__":
    main()
