from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    app_title: str = "V4 Crypto Monitor"
    operational_model: str = "V4 Core"
    shadow_model: str = "V4-O"
    opportunity_weight: float = 0.10
    currency: str = "USDT"

    project_dir: Path = Path(__file__).parent
    data_dir: Path = project_dir / "data"
    cache_dir: Path = data_dir / "cache"
    crypto_cache_dir: Path = cache_dir / "crypto"
    live_state_file: Path = cache_dir / "live_state.json"
    live_snapshot_file: Path = cache_dir / "live_snapshot.json"
    live_history_file: Path = cache_dir / "live_history.jsonl"
    sp500_cache_file: Path = cache_dir / "sp500.csv"
    sp500_seed_file: Path = data_dir / "bootstrap" / "sp500_seed.csv"

    binance_base_url: str = "https://data-api.binance.vision"
    binance_fallback_url: str = "https://api.binance.com"
    fred_csv_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"
    stooq_csv_url: str = "https://stooq.com/q/d/l/?s=%5Espx&i=d"

    # Bootstrap do universo vivo. O histórico diário cobre sinais e 12 meses de
    # liquidez; a data de listagem é consultada separadamente.
    live_universe_size: int = 180
    live_history_days: int = 430
    top_liquidity_n: int = 30
    structural_age_months: int = 48
    structural_presence_months: int = 6
    structural_sequence_months: int = 3
    structural_score_min: float = 0.55


CONFIG = AppConfig()
