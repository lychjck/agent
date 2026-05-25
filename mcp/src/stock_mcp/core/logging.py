import logging
import sys

def setup_logging(level_name: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("stock_mcp")
    logger.setLevel(getattr(logging, level_name.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        ))
        logger.addHandler(handler)
    return logger

logger = setup_logging()
