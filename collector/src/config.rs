// Collector configuration loaded from environment variables.
//
// Connection parameters (reconnect delays, heartbeat timeout) mirror the values
// in configs/providers.yaml so that changing them only requires a config/env update,
// not a code change.

use std::env;

#[derive(Debug, Clone)]
pub struct Config {
    /// NATS server URL — injected by docker-compose via NATS_URL env var
    pub nats_url: String,
    /// Binance Futures WebSocket base URL
    pub binance_ws_base: String,
    /// Symbols to stream, lowercased (e.g. ["btcusdt", "ethusdt", ...])
    pub symbols: Vec<String>,
    /// Base delay in seconds for exponential backoff on reconnect (providers.yaml: reconnect_delay_base)
    pub reconnect_base_delay_secs: u64,
    /// Maximum reconnect backoff delay in seconds (providers.yaml: max_reconnect_delay)
    pub max_reconnect_delay_secs: u64,
    /// Seconds of silence before logging a heartbeat warning (providers.yaml: heartbeat_timeout)
    pub heartbeat_timeout_secs: u64,
}

impl Config {
    pub fn from_env() -> Self {
        Config {
            nats_url: env::var("NATS_URL").unwrap_or_else(|_| "nats://nats:4222".to_string()),
            binance_ws_base: env::var("BINANCE_WS_BASE")
                .unwrap_or_else(|_| "wss://fstream.binance.com".to_string()),
            symbols: vec![
                "btcusdt".to_string(),
                "ethusdt".to_string(),
                "solusdt".to_string(),
                "hypeusdt".to_string(),
            ],
            reconnect_base_delay_secs: env::var("RECONNECT_BASE_DELAY_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(1),
            max_reconnect_delay_secs: env::var("MAX_RECONNECT_DELAY_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(60),
            heartbeat_timeout_secs: env::var("HEARTBEAT_TIMEOUT_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(30),
        }
    }

    /// Build the Binance Futures kline stream URL for a given symbol.
    /// e.g. wss://fstream.binance.com/ws/btcusdt@kline_1m
    pub fn kline_stream_url(&self, symbol: &str) -> String {
        format!("{}/ws/{}@kline_1m", self.binance_ws_base, symbol)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_kline_stream_url_format() {
        let config = Config {
            nats_url: "nats://localhost:4222".to_string(),
            binance_ws_base: "wss://fstream.binance.com".to_string(),
            symbols: vec!["btcusdt".to_string()],
            reconnect_base_delay_secs: 1,
            max_reconnect_delay_secs: 60,
            heartbeat_timeout_secs: 30,
        };

        let url = config.kline_stream_url("btcusdt");
        assert_eq!(url, "wss://fstream.binance.com/ws/btcusdt@kline_1m");
    }

    #[test]
    fn test_default_symbols_are_four_assets() {
        // Simulate no env vars set — expect BTC, ETH, SOL, HYPE
        let config = Config::from_env();
        assert!(config.symbols.contains(&"btcusdt".to_string()));
        assert!(config.symbols.contains(&"ethusdt".to_string()));
        assert!(config.symbols.contains(&"solusdt".to_string()));
        assert!(config.symbols.contains(&"hypeusdt".to_string()));
        assert_eq!(config.symbols.len(), 4);
    }
}
