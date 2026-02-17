// Integration tests for the Binance kline collector pipeline.
//
// These tests replay the DI-0 fixture files to verify the full parse→normalize→serialize
// pipeline produces valid output matching schema/contracts/nats_candle_message.json.
// No live network connections or running services required.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

// Re-export the crate modules for testing
// (requires `pub` visibility on the items under test)
use cryptomacro_collector::models::{BinanceKlineEvent, CandleMessage};

/// Read all lines from a JSONL fixture file and parse them as BinanceKlineEvents.
fn parse_fixture(relative_path: &str) -> Vec<BinanceKlineEvent> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let fixture_path = Path::new(manifest_dir)
        .parent()
        .expect("parent of collector/")
        .join(relative_path);

    let file = File::open(&fixture_path)
        .unwrap_or_else(|e| panic!("Failed to open fixture {:?}: {}", fixture_path, e));

    let reader = BufReader::new(file);
    let mut events = Vec::new();

    for (line_num, line) in reader.lines().enumerate() {
        let line = line.expect("Failed to read fixture line");
        if line.trim().is_empty() {
            continue;
        }
        let event: BinanceKlineEvent = serde_json::from_str(&line).unwrap_or_else(|e| {
            panic!(
                "Line {}: failed to parse kline event: {}\nLine: {}",
                line_num + 1,
                e,
                line
            )
        });
        events.push(event);
    }

    events
}

#[test]
fn test_btcusdt_fixture_parses_correctly() {
    let events = parse_fixture("tests/fixtures/binance/btcusdt_1m.jsonl");

    assert!(!events.is_empty(), "Fixture should contain events");

    // Every event should be a kline type
    for event in &events {
        assert_eq!(event.event_type, "kline", "Expected event_type=kline");
        assert_eq!(event.symbol, "BTCUSDT", "Expected symbol=BTCUSDT");
    }
}

#[test]
fn test_ethusdt_fixture_parses_correctly() {
    let events = parse_fixture("tests/fixtures/binance/ethusdt_1m.jsonl");
    assert!(!events.is_empty(), "Fixture should contain events");

    for event in &events {
        assert_eq!(event.event_type, "kline");
        assert_eq!(event.symbol, "ETHUSDT");
    }
}

#[test]
fn test_all_fixtures_produce_valid_candle_messages() {
    let fixtures = [
        ("tests/fixtures/binance/btcusdt_1m.jsonl", "BTCUSDT"),
        ("tests/fixtures/binance/ethusdt_1m.jsonl", "ETHUSDT"),
        ("tests/fixtures/binance/solusdt_1m.jsonl", "SOLUSDT"),
        ("tests/fixtures/binance/hypeusdt_1m.jsonl", "HYPEUSDT"),
    ];

    for (fixture_path, expected_symbol) in &fixtures {
        let events = parse_fixture(fixture_path);
        assert!(
            !events.is_empty(),
            "Fixture {fixture_path} should not be empty"
        );

        for event in &events {
            let candle = CandleMessage::from_kline(&event.symbol.to_lowercase(), &event.kline)
                .unwrap_or_else(|e| {
                    panic!("Failed to build CandleMessage from {fixture_path}: {e}")
                });

            assert_eq!(&candle.symbol, expected_symbol);
            assert_eq!(candle.exchange, "binance");
            assert_eq!(candle.timeframe, "1m");
            assert!(candle.open > 0.0, "open must be positive");
            assert!(candle.high >= candle.low, "high must be >= low");
            assert!(candle.volume >= 0.0, "volume must be non-negative");
            assert!(!candle.time.is_empty(), "time must be set");
            // Time format: YYYY-MM-DDTHH:MM:SSZ
            assert!(candle.time.ends_with('Z'), "time must be UTC ISO 8601");
        }
    }
}

#[test]
fn test_candle_message_json_has_all_required_schema_fields() {
    let events = parse_fixture("tests/fixtures/binance/btcusdt_1m.jsonl");
    let event = events
        .first()
        .expect("fixture must have at least one event");

    let candle = CandleMessage::from_kline("btcusdt", &event.kline)
        .expect("Should build CandleMessage from fixture");

    let json_str = serde_json::to_string(&candle).expect("Should serialize to JSON");
    let json: serde_json::Value =
        serde_json::from_str(&json_str).expect("Should parse back to JSON");

    // Verify all required fields from schema/contracts/nats_candle_message.json
    let required_fields = [
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
    ];

    for field in &required_fields {
        assert!(
            json.get(field).is_some(),
            "CandleMessage JSON missing required field: {}",
            field
        );
    }

    // Verify field values are sane
    assert_eq!(json["symbol"].as_str().unwrap(), "BTCUSDT");
    assert_eq!(json["exchange"].as_str().unwrap(), "binance");
    assert_eq!(json["timeframe"].as_str().unwrap(), "1m");
    assert!(json["open"].as_f64().unwrap() > 0.0);
    assert!(json["high"].as_f64().unwrap() > 0.0);
    assert!(json["low"].as_f64().unwrap() > 0.0);
    assert!(json["close"].as_f64().unwrap() > 0.0);
}

#[test]
fn test_fixture_candle_counts_are_reasonable() {
    // Verify the fixture contains a meaningful number of messages (not truncated/empty)
    let events = parse_fixture("tests/fixtures/binance/btcusdt_1m.jsonl");

    // DI-0 captured ~1h of data → expect at least 60 1m candle events
    assert!(
        events.len() >= 60,
        "Expected at least 60 events in 1m fixture, got {}",
        events.len()
    );
}
