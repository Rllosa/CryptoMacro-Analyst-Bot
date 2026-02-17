// Binance WebSocket message types and normalized output schema.
//
// Binance Futures kline stream events are parsed into BinanceKlineEvent,
// then converted to CandleMessage which matches schema/contracts/nats_candle_message.json.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Outer envelope for a Binance Futures kline stream event.
/// Received on subject: wss://fstream.binance.com/ws/{symbol}@kline_1m
#[derive(Debug, Deserialize)]
pub struct BinanceKlineEvent {
    /// Event type, expected to be "kline"
    #[serde(rename = "e")]
    pub event_type: String,
    /// Event time in milliseconds since epoch (UTC)
    #[serde(rename = "E")]
    pub event_time: i64,
    /// Symbol e.g. "BTCUSDT"
    #[serde(rename = "s")]
    pub symbol: String,
    /// Kline data
    #[serde(rename = "k")]
    pub kline: BinanceKline,
}

/// Kline (candlestick) data from Binance Futures stream.
/// All prices are strings — Binance sends them as decimal strings to preserve precision.
#[derive(Debug, Deserialize)]
pub struct BinanceKline {
    /// Kline start time in milliseconds since epoch (UTC)
    #[serde(rename = "t")]
    pub open_time: i64,
    /// Kline close time in milliseconds since epoch (UTC)
    #[serde(rename = "T")]
    pub close_time: i64,
    /// Interval, e.g. "1m"
    #[serde(rename = "i")]
    pub interval: String,
    /// Open price as decimal string
    #[serde(rename = "o")]
    pub open: String,
    /// Close price as decimal string
    #[serde(rename = "c")]
    pub close: String,
    /// High price as decimal string
    #[serde(rename = "h")]
    pub high: String,
    /// Low price as decimal string
    #[serde(rename = "l")]
    pub low: String,
    /// Base asset volume as decimal string
    #[serde(rename = "v")]
    pub volume: String,
    /// Number of trades in this kline
    #[serde(rename = "n")]
    pub trades: i64,
    /// Whether this kline is closed (final bar for the interval)
    #[serde(rename = "x")]
    pub is_closed: bool,
    /// Quote asset volume (USD equivalent) as decimal string
    #[serde(rename = "q")]
    pub quote_volume: String,
}

/// Normalized candle message published to NATS subject `market.candles.{symbol}`.
/// Shape matches schema/contracts/nats_candle_message.json exactly.
#[derive(Debug, Serialize)]
pub struct CandleMessage {
    pub symbol: String,
    pub exchange: String,
    pub timeframe: String,
    /// Candle open time in ISO 8601 UTC format, e.g. "2026-02-17T12:00:00Z"
    pub time: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    /// Base asset volume
    pub volume: f64,
    /// Quote asset volume (USD)
    pub quote_volume: f64,
    pub trades: i64,
}

impl CandleMessage {
    /// Build a CandleMessage from a Binance kline, converting string prices to f64.
    pub fn from_kline(symbol: &str, kline: &BinanceKline) -> anyhow::Result<Self> {
        let open_time_secs = kline.open_time / 1000;
        let open_time_nanos = ((kline.open_time % 1000) * 1_000_000) as u32;
        let dt = DateTime::<Utc>::from_timestamp(open_time_secs, open_time_nanos)
            .ok_or_else(|| anyhow::anyhow!("Invalid open_time timestamp: {}", kline.open_time))?;

        Ok(CandleMessage {
            symbol: symbol.to_uppercase(),
            exchange: "binance".to_string(),
            timeframe: kline.interval.clone(),
            time: dt.format("%Y-%m-%dT%H:%M:%SZ").to_string(),
            open: kline.open.parse::<f64>()?,
            high: kline.high.parse::<f64>()?,
            low: kline.low.parse::<f64>()?,
            close: kline.close.parse::<f64>()?,
            volume: kline.volume.parse::<f64>()?,
            quote_volume: kline.quote_volume.parse::<f64>()?,
            trades: kline.trades,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_kline() -> BinanceKline {
        BinanceKline {
            open_time: 1739786400000, // 2025-02-17T12:00:00Z in ms
            close_time: 1739786459999,
            interval: "1m".to_string(),
            open: "96000.00".to_string(),
            close: "96100.50".to_string(),
            high: "96200.00".to_string(),
            low: "95950.00".to_string(),
            volume: "12.345".to_string(),
            trades: 678,
            is_closed: true,
            quote_volume: "1185108.00".to_string(),
        }
    }

    #[test]
    fn test_from_kline_parses_prices_correctly() {
        let kline = sample_kline();
        let msg = CandleMessage::from_kline("btcusdt", &kline).expect("should parse");

        assert_eq!(msg.symbol, "BTCUSDT");
        assert_eq!(msg.exchange, "binance");
        assert_eq!(msg.timeframe, "1m");
        assert_eq!(msg.open, 96000.0);
        assert_eq!(msg.close, 96100.5);
        assert_eq!(msg.high, 96200.0);
        assert_eq!(msg.low, 95950.0);
        assert_eq!(msg.volume, 12.345);
        assert_eq!(msg.trades, 678);
    }

    #[test]
    fn test_from_kline_symbol_uppercased() {
        let kline = sample_kline();
        let msg = CandleMessage::from_kline("ethusdt", &kline).expect("should parse");
        assert_eq!(msg.symbol, "ETHUSDT");
    }

    #[test]
    fn test_from_kline_serializes_to_valid_json() {
        let kline = sample_kline();
        let msg = CandleMessage::from_kline("btcusdt", &kline).expect("should parse");
        let json = serde_json::to_string(&msg).expect("should serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("should be valid JSON");

        // Verify all required schema fields are present
        for field in &[
            "symbol",
            "exchange",
            "timeframe",
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        ] {
            assert!(parsed.get(field).is_some(), "missing field: {}", field);
        }
    }

    #[test]
    fn test_from_kline_rejects_invalid_price() {
        let mut kline = sample_kline();
        kline.open = "not_a_number".to_string();
        let result = CandleMessage::from_kline("btcusdt", &kline);
        assert!(result.is_err(), "should fail on invalid price");
    }

    #[test]
    fn test_binance_kline_event_deserializes() {
        let raw = r#"{"e":"kline","E":1739786460123,"s":"BTCUSDT","k":{"t":1739786400000,"T":1739786459999,"i":"1m","o":"96000.00","c":"96100.50","h":"96200.00","l":"95950.00","v":"12.345","n":678,"x":true,"q":"1185108.00"}}"#;
        let event: BinanceKlineEvent = serde_json::from_str(raw).expect("should deserialize");

        assert_eq!(event.event_type, "kline");
        assert_eq!(event.symbol, "BTCUSDT");
        assert!(event.kline.is_closed);
        assert_eq!(event.kline.trades, 678);
    }
}
