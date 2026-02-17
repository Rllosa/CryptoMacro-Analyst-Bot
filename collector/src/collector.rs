// Single-symbol Binance WebSocket collector.
//
// Maintains a persistent kline stream for one symbol. On any error or disconnect,
// reconnects with exponential backoff. Logs a warning if no message arrives within
// the configured heartbeat timeout.
//
// Published to NATS subject: market.candles.{symbol} (lowercased)

use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use tokio::sync::watch;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, error, info, warn};

use crate::config::Config;
use crate::models::{BinanceKlineEvent, CandleMessage};
use crate::nats_client::NatsClient;

/// Compute exponential backoff delay, capped at max_delay_secs.
/// Delay = base * 2^(attempt-1), e.g. 1s, 2s, 4s, 8s, ... up to 60s.
pub fn calculate_backoff(attempt: u32, base_secs: u64, max_secs: u64) -> u64 {
    let exp = 2u64.saturating_pow(attempt.saturating_sub(1));
    (base_secs.saturating_mul(exp)).min(max_secs)
}

/// Run the collector for a single symbol until a shutdown signal is received.
/// Reconnects automatically on disconnect or error using exponential backoff.
pub async fn run_collector(
    symbol: String,
    config: Arc<Config>,
    nats: Arc<NatsClient>,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut attempt: u32 = 0;

    loop {
        // Check shutdown before attempting connection
        if *shutdown.borrow() {
            info!(symbol = %symbol, "Shutdown received, stopping collector");
            return;
        }

        attempt += 1;
        info!(symbol = %symbol, attempt = attempt, "Starting WebSocket connection");

        tokio::select! {
            result = stream_klines(&symbol, &config, &nats) => {
                match result {
                    Ok(_) => {
                        // Stream exited cleanly — only happens during shutdown (caught by select)
                        info!(symbol = %symbol, "Stream closed cleanly");
                    }
                    Err(e) => {
                        error!(symbol = %symbol, error = %e, "Collector stream error");
                    }
                }
            }
            // Abort the stream and exit if shutdown is signalled
            _ = shutdown.changed() => {
                info!(symbol = %symbol, "Shutdown signal received during stream");
                return;
            }
        }

        if *shutdown.borrow() {
            return;
        }

        let delay = calculate_backoff(
            attempt,
            config.reconnect_base_delay_secs,
            config.max_reconnect_delay_secs,
        );
        warn!(
            symbol = %symbol,
            attempt = attempt,
            delay_secs = delay,
            "Reconnecting after backoff delay"
        );

        // Wait for backoff delay, but abort immediately on shutdown
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(delay)) => {}
            _ = shutdown.changed() => {
                info!(symbol = %symbol, "Shutdown during backoff, stopping collector");
                return;
            }
        }
    }
}

/// Connect to a Binance kline stream and process messages until an error or disconnect.
/// Returns Ok(()) when the stream ends cleanly, Err on connection/parse errors.
async fn stream_klines(symbol: &str, config: &Config, nats: &NatsClient) -> anyhow::Result<()> {
    let url = config.kline_stream_url(symbol);
    let (ws_stream, _) = connect_async(&url)
        .await
        .map_err(|e| anyhow::anyhow!("WS connect failed for {symbol} at {url}: {e}"))?;

    info!(symbol = %symbol, url = %url, "WebSocket connected");

    let (_, mut read) = ws_stream.split();

    let mut last_message = Instant::now();
    let heartbeat_timeout = Duration::from_secs(config.heartbeat_timeout_secs);
    let nats_subject = format!("market.candles.{}", symbol);

    // Process incoming messages until the stream closes or an error occurs
    loop {
        // Use a timeout on the next message to detect silent streams
        let msg_result = tokio::time::timeout(heartbeat_timeout, read.next()).await;

        match msg_result {
            // Timeout: no message within heartbeat window
            Err(_elapsed) => {
                let silence_secs = last_message.elapsed().as_secs();
                warn!(
                    symbol = %symbol,
                    silence_secs = silence_secs,
                    "Heartbeat timeout: no message received — stream may be stale"
                );
                // Continue looping — next iteration will retry the timeout
                continue;
            }

            // Stream ended
            Ok(None) => {
                info!(symbol = %symbol, "WebSocket stream closed by server");
                return Ok(());
            }

            // Message received
            Ok(Some(msg_result)) => {
                let msg = msg_result
                    .map_err(|e| anyhow::anyhow!("WebSocket read error for {symbol}: {e}"))?;

                last_message = Instant::now();

                match msg {
                    Message::Text(text) => {
                        handle_text_message(symbol, &text, nats, &nats_subject).await?;
                    }
                    Message::Ping(payload) => {
                        // tokio-tungstenite auto-responds to pings with a Pong
                        debug!(symbol = %symbol, "Received Ping ({} bytes)", payload.len());
                    }
                    Message::Close(frame) => {
                        info!(symbol = %symbol, close_frame = ?frame, "WebSocket close frame received");
                        return Ok(());
                    }
                    // Binary and Pong messages are not expected from Binance Futures streams
                    _ => {}
                }
            }
        }
    }
}

/// Parse a text frame from the Binance kline stream and publish to NATS.
/// Logs a warning and continues on parse failures — never panics on bad data.
async fn handle_text_message(
    symbol: &str,
    text: &str,
    nats: &NatsClient,
    nats_subject: &str,
) -> anyhow::Result<()> {
    let event: BinanceKlineEvent = match serde_json::from_str(text) {
        Ok(e) => e,
        Err(e) => {
            warn!(symbol = %symbol, error = %e, "Failed to parse kline event, skipping");
            return Ok(());
        }
    };

    if event.event_type != "kline" {
        // Binance may send other event types (e.g. "depthUpdate") on the stream
        debug!(symbol = %symbol, event_type = %event.event_type, "Ignoring non-kline event");
        return Ok(());
    }

    let candle = match CandleMessage::from_kline(symbol, &event.kline) {
        Ok(c) => c,
        Err(e) => {
            warn!(symbol = %symbol, error = %e, "Failed to build CandleMessage, skipping");
            return Ok(());
        }
    };

    let payload = serde_json::to_vec(&candle)
        .map_err(|e| anyhow::anyhow!("Failed to serialize CandleMessage for {symbol}: {e}"))?;

    nats.publish(nats_subject, payload)
        .await
        .map_err(|e| anyhow::anyhow!("NATS publish failed for {symbol}: {e}"))?;

    debug!(
        symbol = %symbol,
        timeframe = %candle.timeframe,
        is_closed = event.kline.is_closed,
        close = candle.close,
        "Published candle"
    );

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_calculate_backoff_exponential() {
        assert_eq!(calculate_backoff(1, 1, 60), 1);
        assert_eq!(calculate_backoff(2, 1, 60), 2);
        assert_eq!(calculate_backoff(3, 1, 60), 4);
        assert_eq!(calculate_backoff(4, 1, 60), 8);
        assert_eq!(calculate_backoff(5, 1, 60), 16);
        assert_eq!(calculate_backoff(6, 1, 60), 32);
        assert_eq!(calculate_backoff(7, 1, 60), 60); // capped
        assert_eq!(calculate_backoff(10, 1, 60), 60); // stays capped
    }

    #[test]
    fn test_calculate_backoff_with_base_delay() {
        // Base 2s: 2, 4, 8, 16, ... capped at 60
        assert_eq!(calculate_backoff(1, 2, 60), 2);
        assert_eq!(calculate_backoff(2, 2, 60), 4);
        assert_eq!(calculate_backoff(3, 2, 60), 8);
    }

    #[test]
    fn test_calculate_backoff_zero_attempt() {
        // Attempt 0 should be treated as 0 exponent → 1 * 2^0 = 1 but saturating_sub gives 0
        // 2^0 = 1, but attempt 0 => saturating_sub(1) = 0 => 2^0 = 1 via pow(0) = 1
        let delay = calculate_backoff(0, 1, 60);
        assert!(delay <= 60);
    }
}
